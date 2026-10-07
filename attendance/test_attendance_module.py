"""
Attendance module: Today, Attendance Reports and Printable Register.

Unmarked learners are excluded from every calculation; late is its own figure
(and in school); excused is not in school, shown separately, and its reason is
visible to authorised staff only and never printed; weekends, holidays and
days before a learner's admission are omitted.
"""
from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import AcademicYear, Department, GradeClass, Term
from academics.tests import assign_role_group
from attendance import analytics
from attendance.models import AttendanceEntry, AttendanceStatus
from events.models import CalendarEvent, EventCategory
from students.models import Student, StudentStatus
from users.models import User, UserRole

P, L, A, E = (AttendanceStatus.PRESENT, AttendanceStatus.LATE, AttendanceStatus.ABSENT, AttendanceStatus.EXCUSED)


class AttendanceModuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        # A full past school week: Monday..Friday two weeks ago.
        cls.mon = cls.today - timedelta(days=cls.today.weekday() + 14)
        cls.week = [cls.mon + timedelta(days=i) for i in range(5)]
        year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term = Term.objects.create(academic_year=year, name="Term 1",
                                       start_date=cls.mon - timedelta(days=21), end_date=cls.today + timedelta(days=40))
        GradeClass.objects.create(name="Grade 4", department=Department.PRIMARY, sort_order=4)
        GradeClass.objects.create(name="Grade 5", department=Department.PRIMARY, sort_order=5)
        # Wednesday of that week is a public holiday.
        CalendarEvent.objects.create(title="Heroes Day", category=EventCategory.HOLIDAY, start_date=cls.week[2])

        cls.admin = cls._user("sa", UserRole.SUPER_ADMIN)
        cls.hos = cls._user("hos", UserRole.HEAD_OF_SCHOOL)
        cls.teacher = cls._user("teacher", UserRole.TEACHER)
        cls.parent = cls._user("parent", UserRole.PARENT)

        def learner(adm, first, cls_name, enrolled=None):
            return Student.objects.create(admission_no=adm, first_name=first, last_name="Learner", class_name=cls_name,
                                          status=StudentStatus.ACTIVE, enrolment_date=enrolled or cls.term.start_date)
        cls.asha = learner("A1", "Asha", "Grade 4")
        cls.bora = learner("B1", "Bora", "Grade 4")
        cls.chiku = learner("C1", "Chiku", "Grade 4", enrolled=cls.week[3])  # admitted Thursday
        cls.dani = learner("D1", "Dani", "Grade 5")

        def mark(st, d, status, reason="", by=None):
            AttendanceEntry.objects.create(student=st, date=d, status=status, class_name=st.class_name,
                                           marked_by=by or cls.admin, reason=reason)
        # Asha: P L (holiday) A P  -> 3 in school of 4 marked = 75%
        for d, s in zip((cls.week[0], cls.week[1], cls.week[3], cls.week[4]), (P, L, A, P)):
            mark(cls.asha, d, s)
        # Bora: P, unmarked, (holiday), E with a reason, P -> 2 of 3 = 66.7%; 1 unmarked
        mark(cls.bora, cls.week[0], P)
        mark(cls.bora, cls.week[3], E, reason="Hospital appointment")
        mark(cls.bora, cls.week[4], P)
        # Chiku (admitted Thursday): P P -> 100%, never counted before admission.
        mark(cls.chiku, cls.week[3], P)
        mark(cls.chiku, cls.week[4], P)
        # Grade 5 register never confirmed that week.

    @classmethod
    def _user(cls, name, role):
        u = User.objects.create_user(username=name, email=f"{name}@example.test", password="x", role=role)
        assign_role_group(u)
        return u

    def _range(self):
        return {"start": self.week[0].isoformat(), "end": self.week[4].isoformat()}

    # -- shared calculation ---------------------------------------------------

    def test_period_omits_weekends_and_holidays(self):
        period = analytics.build_period(self.week[0] - timedelta(days=2), self.week[4] + timedelta(days=2), self.today)
        self.assertEqual(period.days, [self.week[0], self.week[1], self.week[3], self.week[4]])
        self.assertEqual(period.holidays, [(self.week[2], "Heroes Day")])

    def test_rates_exclude_unmarked_and_count_from_admission(self):
        period = analytics.build_period(self.week[0], self.week[4], self.today)
        rows = {r["name"].split()[0]: r for r in analytics.build_learners(period, "Grade 4")}
        asha, bora, chiku = rows["Asha"], rows["Bora"], rows["Chiku"]
        self.assertEqual((asha["in_school"], asha["marked"], asha["late"], asha["rate"]), (3, 4, 1, 75.0))
        self.assertEqual((bora["unmarked"], bora["excused"], bora["rate"]), (1, 1, 66.7))
        self.assertEqual(chiku["codes"], ["", "", "P", "P"])
        self.assertEqual((chiku["on_roll_days"], chiku["rate"], chiku["unmarked"]), (2, 100.0, 0))
        s = analytics.summarise(list(rows.values()))
        self.assertEqual((s["in_school"], s["marked"], s["unmarked"]), (7, 9, 1))
        self.assertEqual(s["average"], 77.8)

    # -- Today ------------------------------------------------------------------

    def test_today_headline_is_calculated_on_marked_learners_only(self):
        for st, status in ((self.asha, P), (self.bora, L), (self.dani, A)):
            AttendanceEntry.objects.create(student=st, date=self.today, status=status, class_name=st.class_name, marked_by=self.admin)
        self.client.force_login(self.hos)
        resp = self.client.get(reverse("attendance:today"), {"date": self.today.isoformat(), "class_name": ""})
        s = resp.context["summary"]
        self.assertEqual((s["total"], s["marked"], s["present"], s["late"], s["absent"], s["unconfirmed"]), (4, 3, 2, 1, 1, 1))
        self.assertEqual(s["present_pct"], 66.7)  # 2 of 3 marked, not 2 of 4 enrolled
        self.assertContains(resp, "66.7% of those marked")
        self.assertContains(resp, "Registers not yet marked")

    def test_everyone_sees_every_class(self):
        self.client.force_login(self.teacher)
        resp = self.client.get(reverse("attendance:today"), {"class_name": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual({r["student"].class_name for r in resp.context["rows"]}, {"Grade 4", "Grade 5"})
        resp = self.client.get(reverse("attendance:reports"), self._range())
        self.assertEqual(resp.context["summary"]["learners"], 4)

    def test_parent_is_sent_to_their_own_page(self):
        self.client.force_login(self.parent)
        for name in ("attendance:today", "attendance:reports", "attendance:register"):
            self.assertRedirects(self.client.get(reverse(name)), reverse("attendance:parent"), fetch_redirect_response=False)

    def test_marking_excused_needs_a_reason_and_other_marks_clear_it(self):
        self.client.force_login(self.admin)
        url = reverse("attendance:mark")
        data = {"student_id": self.dani.pk, "date": self.today.isoformat(), "status": "excused"}
        self.assertEqual(self.client.post(url, data).status_code, 400)
        resp = self.client.post(url, {**data, "reason": "Fever"})
        self.assertContains(resp, 'data-status="E"')
        self.assertContains(resp, "Reason: Fever")
        entry = AttendanceEntry.objects.get(student=self.dani, date=self.today)
        self.assertEqual(entry.reason, "Fever")
        self.client.post(url, {**data, "status": "present"})
        entry.refresh_from_db()
        self.assertEqual((entry.status, entry.reason), ("present", ""))

    def test_excusal_reason_is_restricted(self):
        AttendanceEntry.objects.create(student=self.dani, date=self.today, status=E, class_name="Grade 5",
                                       marked_by=self.admin, reason="Hospital appointment")
        self.client.force_login(self.teacher)
        resp = self.client.get(reverse("attendance:today"), {"class_name": "Grade 5"})
        self.assertNotContains(resp, "Hospital appointment")
        self.assertContains(resp, "Reason restricted")
        self.client.force_login(self.hos)
        resp = self.client.get(reverse("attendance:today"), {"class_name": "Grade 5"})
        self.assertContains(resp, "Hospital appointment")

    # -- Reports ----------------------------------------------------------------

    def test_reports_figures_and_drilldowns(self):
        self.client.force_login(self.hos)
        url = reverse("attendance:reports")
        resp = self.client.get(url, {**self._range(), "class_name": "Grade 4"})
        s = resp.context["summary"]
        self.assertEqual((s["average"], s["persistent"], s["flagged"], s["unmarked"]), (77.8, 2, 0, 1))
        self.assertEqual([r["name"] for r in resp.context["rows"]], ["Bora Learner", "Asha Learner", "Chiku Learner"])
        resp = self.client.get(url, {**self._range(), "show": "pers"})
        self.assertEqual({r["name"] for r in resp.context["rows"]}, {"Asha Learner", "Bora Learner"})
        resp = self.client.get(url, {**self._range(), "show": "unm"})
        issues = {(i["class_name"], i["date"]): i for i in resp.context["issues"]}
        self.assertEqual(issues[("Grade 5", self.week[0])]["label"], "Never confirmed")
        self.assertEqual(issues[("Grade 4", self.week[1])]["unmarked"], 1)
        resp = self.client.get(url, {**self._range(), "show": "cls"})
        self.assertEqual([c["name"] for c in resp.context["classes_summary"]], ["Grade 4", "Grade 5"])
        self.assertIsNone(resp.context["classes_summary"][1]["average"])  # never marked: no rate, not 0%
        resp = self.client.get(url, {**self._range(), "student": self.asha.pk})
        self.assertEqual(resp.context["summary"]["learners"], 1)
        self.assertEqual(resp.context["pattern"]["longest_absent_run"], 1)

    def test_chart_points_per_day_for_short_ranges(self):
        self.client.force_login(self.hos)
        resp = self.client.get(reverse("attendance:reports"), {**self._range(), "class_name": "Grade 4"})
        chart = resp.context["chart"]
        self.assertEqual(chart["unit"], "day")
        self.assertEqual(len(chart["points"]), 4)  # holiday omitted
        first = chart["points"][0]
        self.assertEqual((first["present"], first["late"], first["unmarked"]), (2, 0, 0))

    def test_learner_pattern_shows_reason_only_to_authorised_staff(self):
        url = reverse("attendance:learner", args=[self.bora.pk])
        self.client.force_login(self.hos)
        resp = self.client.get(url, self._range())
        self.assertContains(resp, "Hospital appointment")
        self.client.force_login(self.teacher)
        resp = self.client.get(url, self._range())
        self.assertNotContains(resp, "Hospital appointment")
        self.assertEqual(resp.context["row"]["excused"], 1)

    # -- Printable register and print documents ---------------------------------

    def test_register_grid_totals_and_present_per_day(self):
        self.client.force_login(self.teacher)
        resp = self.client.get(reverse("attendance:register"), {**self._range(), "class_name": "Grade 4"})
        chunk = resp.context["chunks"][0]
        self.assertEqual(chunk["days"], [self.week[0], self.week[1], self.week[3], self.week[4]])
        rows = {r["learner"]["name"].split()[0]: r for r in chunk["rows"]}
        self.assertEqual(rows["Asha"]["codes"], ["P", "L", "A", "P"])
        self.assertEqual((rows["Asha"]["in_school"], rows["Asha"]["absent"], rows["Asha"]["late"]), (3, 1, 1))
        self.assertEqual(rows["Chiku"]["codes"], ["", "", "P", "P"])
        # Tuesday: Asha late (in school), Bora unmarked, Chiku not yet admitted.
        self.assertEqual(chunk["daily"][1], {"present": 1, "marked": 1, "on_roll": 2, "future": False})
        self.assertContains(resp, "Quality assurance officer")

    def test_print_documents_never_show_excusal_reasons(self):
        self.client.force_login(self.hos)
        url = reverse("attendance:print")
        for params in ({"kind": "register", "class_name": "Grade 4"}, {"kind": "report"},
                       {"kind": "student", "student": self.bora.pk}, {"kind": "today", "class_name": ""}):
            resp = self.client.get(url, {**self._range(), **params})
            self.assertEqual(resp.status_code, 200, params)
            self.assertNotContains(resp, "Hospital appointment")
            self.assertContains(resp, "Head of School")

    def test_pdf_download_falls_back_to_print_page_without_pdf_engine(self):
        import sys
        from unittest import mock
        self.client.force_login(self.hos)
        with mock.patch.dict(sys.modules, {"weasyprint": None}):
            resp = self.client.get(reverse("attendance:print"), {**self._range(), "kind": "register",
                                                                 "class_name": "Grade 4", "format": "pdf"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"].split(";")[0], "text/html")
        self.assertTrue(resp.context["print_now"])
