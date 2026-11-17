"""
Parent portal v1: ownership, report publication, converted/weighted marks,
attendance rules and the school calendar.
"""
import tempfile
from datetime import date, time, timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import (
    AcademicYear, AssessmentMode, Department, ExamScore, ExamTypeConfiguration, GradeClass,
    ReportCard, ReportCardStatus, ScoreStatus, Subject, Term,
)
from academics.score_progress import exam_summary
from academics.tests import assign_role_group
from attendance.models import AttendanceEntry, AttendanceStatus
from events.models import CalendarEvent, EventCategory
from parent_portal import services
from students.models import ParentGuardian, Student, StudentGuardian, StudentStatus
from users.models import User, UserRole

TYPES = (("quiz", "Quiz", 20, 1), ("mid_term", "Mid-Term", 30, 2), ("end_of_term", "End-Term", 50, 3))


def _weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class PortalTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        # Term 1 has ended, Term 2 is running, Term 3 is still ahead.
        cls.t1 = Term.objects.create(academic_year=cls.year, name="Term 1",
                                     start_date=cls.today - timedelta(days=220),
                                     end_date=cls.today - timedelta(days=121))
        cls.t2 = Term.objects.create(academic_year=cls.year, name="Term 2",
                                     start_date=cls.today - timedelta(days=100),
                                     end_date=cls.today + timedelta(days=30))
        cls.t3 = Term.objects.create(academic_year=cls.year, name="Term 3",
                                     start_date=cls.today + timedelta(days=60),
                                     end_date=cls.today + timedelta(days=150))
        ExamTypeConfiguration.objects.exclude(code__in=[t[0] for t in TYPES]).update(is_active=False)
        for code, name, weight, order in TYPES:
            ExamTypeConfiguration.objects.update_or_create(
                code=code, defaults={"name": name, "weight_percentage": Decimal(weight), "display_order": order,
                                     "max_score": Decimal("100"), "is_active": True},
            )
        gc = GradeClass.objects.create(name="Grade 5", department=Department.PRIMARY)
        for code, name in (("MAT", "Mathematics"), ("ENG", "English")):
            Subject.objects.create(name=name, code=code, department=Department.PRIMARY,
                                   departments=[Department.PRIMARY]).classes.add(gc)
        Subject.objects.create(name="Choir", code="CHO", department=Department.PRIMARY,
                               departments=[Department.PRIMARY],
                               assessment_mode=AssessmentMode.REMARK).classes.add(gc)

        cls.teacher = User.objects.create_user(username="t", email="t@x.test", password="x", role=UserRole.TEACHER)
        assign_role_group(cls.teacher)
        cls.child = Student.objects.create(admission_no="G5-001", first_name="Sherissa", last_name="Kimaro",
                                           class_name="Grade 5", academic_year=cls.year,
                                           status=StudentStatus.ACTIVE,
                                           enrolment_date=cls.today - timedelta(days=400))
        cls.other_child = Student.objects.create(admission_no="G5-002", first_name="Baraka", last_name="Otieno",
                                                 class_name="Grade 5", academic_year=cls.year,
                                                 status=StudentStatus.ACTIVE)
        cls.parent = cls._parent("p", "+255700000001", cls.child)
        cls.other_parent = cls._parent("q", "+255700000002", cls.other_child)

    @classmethod
    def _parent(cls, username, phone, child):
        user = User.objects.create_user(username=username, email=f"{username}@x.test", first_name="Esther",
                                        role=UserRole.PARENT)
        assign_role_group(user)
        guardian = ParentGuardian.objects.create(
            full_name=f"Parent {username}", phone=phone, user=user,
            pdpa_consent_given=True, pdpa_consent_method="in_person",
            pdpa_consent_version="v1", pdpa_consented_at=timezone.now(),
        )
        StudentGuardian.objects.create(student=child, guardian=guardian, relationship="mother", is_primary=True)
        return user

    def _score(self, subject, code, value, term=None, status=ScoreStatus.APPROVED, student=None):
        ExamScore.objects.create(student=student or self.child, term=term or self.t2, subject_name=subject,
                                 exam_type=code, score=Decimal(value), entered_by=self.teacher, status=status,
                                 is_locked=status != ScoreStatus.DRAFT)

    def _report(self, student, term, status):
        return ReportCard.objects.create(student=student, term=term, generated_by=self.teacher, status=status,
                                         published_at=timezone.now() if status == ReportCardStatus.PUBLISHED else None)

    @staticmethod
    def _today_panel(page):
        """The Today panel's markup: the only place check-in must never show a time."""
        panel = page[page.index('<dl class="pp-today'):]
        return panel[:panel.index("</dl>")]

    @staticmethod
    def _check_log(page):
        log = page[page.index('id="check-log-heading"'):]
        return log[:log.index("</table>")]

    def _progress(self, student=None, **params):
        return self.client.get(reverse("parent_portal:progress", args=[(student or self.child).pk]), params)


class HomeTests(PortalTestBase):
    def test_home_greets_parent_with_term_and_no_marks(self):
        self._score("Mathematics", "quiz", 83)
        self.client.force_login(self.parent)
        resp = self.client.get(reverse("parent_portal:dashboard"))
        self.assertEqual(resp.status_code, 200)
        page = resp.content.decode()
        self.assertIn("Esther", page)
        self.assertIn("Term 2", page)
        self.assertIn("2026/2027", page)
        # Running-term marks never appear on Home; with no completed term
        # marked yet, the card says so.
        self.assertNotIn("16.6", page)
        self.assertNotIn("83", page)
        self.assertIn("No completed term yet", page)
        # One explicit button per learner; the card itself is not a link.
        progress = reverse("parent_portal:progress", args=[self.child.pk])
        self.assertEqual(page.count(f'href="{progress}"'), 1)
        self.assertIn("View Sherissa&rsquo;s progress", page)
        self.assertNotIn("Baraka", page)
        # The calendar is reached from Home.
        self.assertIn(reverse("parent_portal:calendar"), page)

    def test_no_staff_navigation_viewing_as_or_excluded_modules(self):
        self.client.force_login(self.parent)
        for url in (reverse("parent_portal:dashboard"), reverse("parent_portal:progress", args=[self.child.pk]),
                    reverse("parent_portal:calendar")):
            page = self.client.get(url).content.decode()
            for text in ("Viewing as", "Welfare", "Discipline", "Finance", "Invoice", "/attendance/",
                         "Fix markers", "Mockup"):
                self.assertNotIn(text, page, f"{text!r} on {url}")

    def test_today_checked_in_shows_in_school_without_time(self):
        AttendanceEntry.objects.create(student=self.child, date=self.today, status=AttendanceStatus.PRESENT,
                                       check_in_time=time(7, 42), class_name="Grade 5", marked_by=self.teacher)
        self.client.force_login(self.parent)
        for url in (reverse("parent_portal:dashboard"), reverse("parent_portal:progress", args=[self.child.pk])):
            page = self.client.get(url).content.decode()
            today_panel = self._today_panel(page)
            self.assertNotIn("7:42", today_panel)
            self.assertIn("In-School", today_panel)
            self.assertIn("Checked in", today_panel)
            self.assertNotIn("Not checked in", today_panel)

    def test_register_present_without_card_scan_counts_as_checked_in(self):
        # Marked present on the class register, never scanned: Status and
        # Checked in must agree.
        AttendanceEntry.objects.create(student=self.child, date=self.today, status=AttendanceStatus.PRESENT,
                                       class_name="Grade 5", marked_by=self.teacher)
        info = services.today_status(self.child, self.today)
        self.assertEqual((info["label"], info["checked_in"], info["in_school"]), ("Present", True, True))
        self.client.force_login(self.parent)
        page = self.client.get(reverse("parent_portal:dashboard")).content.decode()
        self.assertNotIn("Not checked in", page)

    def test_absent_is_not_checked_in(self):
        AttendanceEntry.objects.create(student=self.child, date=self.today, status=AttendanceStatus.ABSENT,
                                       class_name="Grade 5", marked_by=self.teacher)
        info = services.today_status(self.child, self.today)
        self.assertEqual((info["checked_in"], info["in_school"]), (False, False))

    def test_checked_out_time_shown(self):
        AttendanceEntry.objects.create(student=self.child, date=self.today, status=AttendanceStatus.LATE,
                                       check_in_time=time(8, 21), check_out_time=time(15, 42),
                                       class_name="Grade 5", marked_by=self.teacher)
        info = services.today_status(self.child, self.today)
        self.assertEqual((info["label"], info["checked_in"], info["in_school"]), ("Late", True, False))
        self.assertEqual(info["check_out"], time(15, 42))
        self.client.force_login(self.parent)
        page = self.client.get(reverse("parent_portal:progress", args=[self.child.pk])).content.decode()
        self.assertIn("Checked out <time>3:42 PM</time>", self._today_panel(page))
        self.assertNotIn("8:21", self._today_panel(page))

    def test_daily_check_log_lists_each_school_day(self):
        day = self.today.replace(day=1)
        while day.weekday() > 4:
            day += timedelta(days=1)
        if day >= self.today:
            self.skipTest("No earlier weekday in this month.")
        AttendanceEntry.objects.create(student=self.child, date=day, status=AttendanceStatus.PRESENT,
                                       check_in_time=time(7, 30), check_out_time=time(16, 5),
                                       class_name="Grade 5", marked_by=self.teacher)
        self.client.force_login(self.parent)
        resp = self.client.get(reverse("parent_portal:progress", args=[self.child.pk]))
        rows = {r["date"]: r for r in resp.context["check_log"]}
        self.assertEqual((rows[day]["label"], rows[day]["checked_in"], rows[day]["check_out"]),
                         ("Present", True, time(16, 5)))
        page = resp.content.decode()
        self.assertIn("Daily check-in and check-out", page)
        self.assertIn("4:05 PM", self._check_log(page))
        self.assertNotIn("7:30", self._check_log(page))


class OwnershipTests(PortalTestBase):
    def test_other_learner_is_not_found_everywhere(self):
        report = self._report(self.other_child, self.t1, ReportCardStatus.PUBLISHED)
        own = self._report(self.child, self.t1, ReportCardStatus.PUBLISHED)
        self.client.force_login(self.parent)
        urls = [
            reverse("parent_portal:progress", args=[self.other_child.pk]),
            reverse("parent_portal:report_card", args=[self.other_child.pk, report.pk]),
            reverse("parent_portal:report_download", args=[self.other_child.pk, report.pk]),
            # Own learner id with someone else's report id, and the reverse.
            reverse("parent_portal:report_card", args=[self.child.pk, report.pk]),
            reverse("parent_portal:report_download", args=[self.child.pk, report.pk]),
            reverse("parent_portal:report_card", args=[self.other_child.pk, own.pk]),
            reverse("parent_portal:progress", args=[999999]),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404, url)
        # Old URLs redirect into the same ownership check.
        resp = self.client.get(reverse("parent_portal:child_detail", args=[self.other_child.pk]), follow=True)
        self.assertEqual(resp.status_code, 404)
        resp = self.client.get(reverse("parent_portal:grades"), {"student": self.other_child.pk}, follow=True)
        self.assertEqual(resp.status_code, 404)
        # The shared academics download is closed too.
        self.assertEqual(self.client.get(reverse("academics:report_pdf_download", args=[report.pk])).status_code,
                         403)

    def test_inactive_learner_is_hidden(self):
        self.child.status = StudentStatus.WITHDRAWN
        self.child.save(update_fields=["status"])
        self.client.force_login(self.parent)
        self.assertEqual(self._progress().status_code, 404)

    def test_staff_and_anonymous_are_kept_out(self):
        url = reverse("parent_portal:progress", args=[self.child.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/parent/login/", resp["Location"])
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(reverse("parent_portal:dashboard")).status_code, 403)

    def test_sign_out_needs_post_with_csrf(self):
        self.client.force_login(self.parent)
        page = self.client.get(reverse("parent_portal:dashboard")).content.decode()
        self.assertIn('action="/accounts/logout/"', page)
        self.assertIn("csrfmiddlewaretoken", page)


class ReportPublicationTests(PortalTestBase):
    def test_only_published_reports_are_listed_viewable_and_downloadable(self):
        draft = self._report(self.child, self.t1, ReportCardStatus.DRAFT)
        self.client.force_login(self.parent)
        page = self._progress().content.decode()
        # Before publication: "Not yet published" for the current term only, no
        # link to the draft, and nothing from the draft itself.
        self.assertIn("Not yet published", page)
        self.assertNotIn(reverse("parent_portal:report_card", args=[self.child.pk, draft.pk]), page)
        self.assertNotIn(reverse("parent_portal:report_download", args=[self.child.pk, draft.pk]), page)
        for name in ("report_card", "report_download"):
            self.assertEqual(self.client.get(reverse(f"parent_portal:{name}", args=[self.child.pk, draft.pk]))
                             .status_code, 404)

        draft.status = ReportCardStatus.PENDING_SIGN_OFF
        draft.save(update_fields=["status"])
        self.assertEqual(self.client.get(reverse("parent_portal:report_card", args=[self.child.pk, draft.pk]))
                         .status_code, 404)

        draft.status = ReportCardStatus.PUBLISHED
        draft.published_at = timezone.now()
        draft.save(update_fields=["status", "published_at"])
        page = self._progress().content.decode()
        self.assertIn("Preview report", page)
        self.assertIn(reverse("parent_portal:report_download", args=[self.child.pk, draft.pk]), page)
        view_url = reverse("parent_portal:report_card", args=[self.child.pk, draft.pk])
        self.assertIn(view_url, page)
        resp = self.client.get(view_url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("Approve &amp; Sign Off", resp.content.decode())
        self.assertNotIn("Approve & Sign Off", resp.content.decode())
        resp = self.client.get(reverse("parent_portal:report_download", args=[self.child.pk, draft.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_academics_download_refuses_unpublished_for_parent_without_guardian_record(self):
        loose = User.objects.create_user(username="loose", email="l@x.test", role=UserRole.PARENT)
        assign_role_group(loose)
        rc = self._report(self.child, self.t1, ReportCardStatus.PENDING_SIGN_OFF)
        self.client.force_login(loose)
        self.assertEqual(self.client.get(reverse("academics:report_pdf_download", args=[rc.pk])).status_code, 403)


class MarksTests(PortalTestBase):
    def test_running_term_shows_converted_marks_and_running_average(self):
        self._score("Mathematics", "quiz", 80)                         # 16 /20
        self._score("Mathematics", "mid_term", 60)                     # 18 /30
        self._score("English", "quiz", 50)                             # 10 /20
        self._score("English", "mid_term", 97, status=ScoreStatus.DRAFT)   # not approved: ignored
        self._score("Choir", "quiz", 19)                               # remark-only: ignored
        self.client.force_login(self.parent)
        resp = self._progress()
        self.assertEqual(resp.status_code, 200)
        academic = resp.context["academic"]
        rows = {r["subject"]: r for r in academic["rows"]}
        self.assertEqual(set(rows), {"English", "Mathematics"})
        self.assertEqual([c for c, _ in rows["Mathematics"]["cells"]], [16.0, 18.0, None])
        self.assertEqual([c for c, _ in rows["English"]["cells"]], [10.0, None, None])
        # Missing assessments are left out and their weight redistributed:
        # Maths 80 x 40% + 60 x 60% = 68.0; English 50.0; mean 59.0.
        self.assertEqual(rows["Mathematics"]["running"], 68.0)
        self.assertEqual(rows["English"]["running"], 50.0)
        self.assertEqual(academic["average"], 59.0)
        self.assertFalse(academic["complete"])
        self.assertEqual(resp.context["average_ring"]["value"], 59.0)

        page = resp.content.decode()
        self.assertIn("Quiz /20", page)
        self.assertIn("Mid-Term /30", page)
        self.assertIn("End-Term /50", page)
        self.assertIn("Running average", page)
        self.assertEqual(page.count("Running average"), 1)
        self.assertIn("Not yet", page)
        # Raw /100 marks and unapproved marks never appear.
        for raw in (">80<", ">60<", ">50<", "97", "Choir"):
            self.assertNotIn(raw, page)

    def test_complete_term_uses_report_card_figure(self):
        for code, value in (("quiz", 90), ("mid_term", 90), ("end_of_term", 90)):
            self._score("Mathematics", code, value)
        for code, value in (("quiz", 70), ("mid_term", 80), ("end_of_term", 80)):
            self._score("English", code, value)
        self.client.force_login(self.parent)
        resp = self._progress()
        report = exam_summary(self.child, self.t2)
        self.assertTrue(report["complete"])
        self.assertEqual(resp.context["academic"]["average"], report["average"])  # 84.0
        self.assertEqual(resp.context["academic"]["grade"], report["grade"])
        page = resp.content.decode()
        self.assertIn("Term average", page)
        self.assertNotIn("Running average", page)
        self.assertIn("84%", page)

    def test_charts_one_card_per_term_with_assessments_as_bars(self):
        self._score("Mathematics", "quiz", 75, term=self.t1)           # 15 /20, Term 1 final
        self._score("Mathematics", "quiz", 90)                         # 18 /20, Term 2 running
        self._score("English", "quiz", 80)                             # 16 /20
        self._score("Mathematics", "mid_term", 50, term=self.t1)       # 15 /30
        self.client.force_login(self.parent)
        resp = self._progress()
        cards = resp.context["academic"]["term_cards"]
        self.assertEqual([(c["term"].name, c["state"], c["label"]) for c in cards],
                         [("Term 1", "final", "Final"), ("Term 2", "running", "Running"),
                          ("Term 3", "upcoming", "Upcoming")])
        bars = {c["term"].name: [(b["type"].code, b["max"], b["value"]) for b in c["bars"]] for c in cards}
        self.assertEqual(bars["Term 1"][:2], [("quiz", 20, 15.0), ("mid_term", 30, 15.0)])
        self.assertEqual(bars["Term 2"][:2], [("quiz", 20, 17.0), ("mid_term", 30, None)])
        self.assertEqual([c["has_marks"] for c in cards], [True, True, False])
        # Bars are scaled to their own maximum, so heights compare fairly.
        self.assertEqual(cards[0]["bars"][0]["pct"], 75.0)
        self.assertEqual(cards[0]["bars"][1]["pct"], 50.0)

        page = resp.content.decode()
        self.assertIn(">Upcoming<", page)
        self.assertIn(">Running<", page)
        # Term 3 has not started: "Upcoming", a line and no bars at all.
        self.assertIn("Term 3 has not started.", page)
        self.assertIn("Quiz /20<em>20%", page)
        self.assertIn("Mid-Term /30<em>30%", page)
        # Filled bars only where there are marks: Term 1 (quiz, mid) and Term 2 (quiz).
        self.assertEqual(page.count('class="pp-bar pp-bar-running"') + page.count('class="pp-bar"'), 3)
        # Started terms show all three assessment columns; Term 3 shows none.
        self.assertEqual(page.count('class="pp-bar-col"'), 2 * len(cards[0]["bars"]))

    def test_started_term_without_marks_gets_its_own_empty_message(self):
        self._score("Mathematics", "quiz", 90)                         # Term 2 only
        self.client.force_login(self.parent)
        page = self._progress().content.decode()
        self.assertIn("No approved marks yet for Term 1", page)
        self.assertNotIn("No approved marks yet for Term 2", page)
        self.assertIn("Term 3 has not started.", page)


class AttendanceTests(PortalTestBase):
    def test_rate_excludes_unmarked_and_hides_excuse_reasons(self):
        month = (self.today.replace(day=1) - timedelta(days=1)).replace(day=1)  # last month: fully past
        days = _weekdays(month, 6)
        statuses = [AttendanceStatus.PRESENT, AttendanceStatus.LATE, AttendanceStatus.ABSENT,
                    AttendanceStatus.EXCUSED, AttendanceStatus.UNCONFIRMED]
        for d, status in zip(days, statuses):
            AttendanceEntry.objects.create(student=self.child, date=d, status=status, class_name="Grade 5",
                                           marked_by=self.teacher,
                                           reason="Hospital visit" if status == AttendanceStatus.EXCUSED else "")
        # days[5] has no entry at all.
        self.client.force_login(self.parent)
        resp = self._progress(month=month.strftime("%Y-%m"))
        m = resp.context["month"]
        self.assertEqual((m["present"], m["late"], m["absent"], m["excused"]), (1, 1, 1, 1))
        self.assertEqual(m["marked"], 4)
        self.assertEqual(m["rate"], 50.0)            # (present + late) / marked
        self.assertGreaterEqual(m["unmarked"], 2)    # unconfirmed + no entry, excluded
        cells = {c["date"]: c for w in m["weeks"] for c in w if c}
        self.assertEqual(cells[days[3]]["label"], "Excused")
        self.assertEqual(cells[days[4]]["label"], "Not yet marked")
        self.assertEqual(cells[days[5]]["label"], "Not yet marked")
        self.assertEqual(resp.context["attendance_ring"]["value"], 50.0)
        page = resp.content.decode()
        self.assertNotIn("Hospital visit", page)
        self.assertIn("Not yet marked", page)
        self.assertIn("Excused", page)

    def test_holiday_shows_no_school_and_is_not_counted(self):
        month = (self.today.replace(day=1) - timedelta(days=1)).replace(day=1)
        d1, d2 = _weekdays(month, 2)
        CalendarEvent.objects.create(title="Public holiday", category=EventCategory.HOLIDAY,
                                     start_date=d1, is_public_holiday=True)
        AttendanceEntry.objects.create(student=self.child, date=d2, status=AttendanceStatus.PRESENT,
                                       class_name="Grade 5", marked_by=self.teacher)
        m = services.month_attendance(self.child, month, self.today)
        cells = {c["date"]: c for w in m["weeks"] for c in w if c}
        self.assertEqual(cells[d1]["label"], "No school")
        self.assertEqual((m["marked"], m["rate"]), (1, 100.0))

    def test_future_month_is_clamped(self):
        self.client.force_login(self.parent)
        resp = self._progress(month="2099-01")
        self.assertEqual(resp.context["month"]["month"], self.today.replace(day=1))
        self.assertIsNone(resp.context["month"]["next"])


class CalendarTests(PortalTestBase):
    def test_school_wide_published_events_only(self):
        CalendarEvent.objects.create(title="Sports Day", category=EventCategory.SOCIAL,
                                     start_date=self.today + timedelta(days=3))
        CalendarEvent.objects.create(title="Draft staff retreat", start_date=self.today + timedelta(days=4),
                                     is_published=False)
        CalendarEvent.objects.create(title="Staff only training", start_date=self.today + timedelta(days=5),
                                     notify_parents=False)
        self.client.force_login(self.parent)
        page = self.client.get(reverse("parent_portal:calendar")).content.decode()
        self.assertIn("Sports Day", page)
        self.assertNotIn("Draft staff retreat", page)
        self.assertNotIn("Staff only training", page)
        self.assertNotIn("Sherissa", page)
        self.assertNotIn("G5-001", page)

    def test_calendar_is_linked_from_home_only(self):
        self.client.force_login(self.parent)
        cal = reverse("parent_portal:calendar")
        self.assertIn(cal, self.client.get(reverse("parent_portal:dashboard")).content.decode())
        self.assertNotIn(cal, self._progress().content.decode())

    def test_empty_states(self):
        lonely = User.objects.create_user(username="lonely", email="lonely@x.test", role=UserRole.PARENT)
        assign_role_group(lonely)
        self.client.force_login(lonely)
        CalendarEvent.objects.all().delete()  # terms add their own dates to the calendar
        self.assertIn("No students linked yet", self.client.get(reverse("parent_portal:dashboard")).content.decode())
        self.assertIn("No upcoming events", self.client.get(reverse("parent_portal:calendar")).content.decode())


class QueryBudgetTests(PortalTestBase):
    def _count(self, url):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(ctx.captured_queries)

    def test_home_queries_do_not_grow_with_children(self):
        self.client.force_login(self.parent)
        url = reverse("parent_portal:dashboard")
        self._count(url)  # warm up
        one = self._count(url)
        guardian = ParentGuardian.objects.get(user=self.parent)
        for n in range(2):
            kid = Student.objects.create(admission_no=f"G5-1{n}", first_name=f"Kid{n}", last_name="K",
                                         class_name="Grade 5", academic_year=self.year, status=StudentStatus.ACTIVE)
            StudentGuardian.objects.create(student=kid, guardian=guardian, relationship="mother")
        self.assertEqual(self._count(url), one)


class NavigationTests(PortalTestBase):
    def test_switch_between_own_children_only_and_empty_chart_message(self):
        sibling = Student.objects.create(admission_no="G5-003", first_name="Neema", last_name="Kimaro",
                                         class_name="Grade 5", academic_year=self.year, status=StudentStatus.ACTIVE)
        StudentGuardian.objects.create(student=sibling, guardian=ParentGuardian.objects.get(user=self.parent),
                                       relationship="mother")
        self.client.force_login(self.parent)
        page = self._progress().content.decode()
        self.assertIn(reverse("parent_portal:progress", args=[sibling.pk]), page)
        self.assertNotIn(reverse("parent_portal:progress", args=[self.other_child.pk]), page)
        # No approved marks at all this year: one card per term. Started terms
        # show their three assessments as empty placeholders; Term 3 has none.
        self.assertIn("Not yet", page)
        for name in ("Term 1", "Term 2"):
            self.assertIn(f"No approved marks yet for {name}", page)
        self.assertIn("Term 3 has not started.", page)
        self.assertNotIn('class="pp-bar-val"', page)
        self.assertEqual(page.count('class="pp-bar pp-bar-empty"'), 6)


class HomeLastTermTests(PortalTestBase):
    def test_home_shows_last_completed_term_not_running_term(self):
        for subject in ("Mathematics", "English"):
            self._score(subject, "quiz", 80, term=self.t1)
            self._score(subject, "mid_term", 70, term=self.t1)
            self._score(subject, "end_of_term", 90, term=self.t1)
        self._score("Mathematics", "quiz", 41)  # running Term 2: never on Home
        expected = exam_summary(self.child, self.t1)["running_average"]
        self.client.force_login(self.parent)
        page = self.client.get(reverse("parent_portal:dashboard")).content.decode()
        self.assertIn("Term 1 2026/2027 average", page)
        self.assertIn(f"{expected:g}%", page)
        # The running Term 2 figure is never on Home (checked by label, since a
        # bare number can appear by chance in tokens on the page).
        self.assertNotIn("Term 2 2026/2027 average", page)
        self.assertIn("Performance, report cards and attendance", page)


class TodayTimesTests(PortalTestBase):
    def test_check_out_time_shown_check_in_time_never(self):
        AttendanceEntry.objects.create(student=self.child, date=self.today, status=AttendanceStatus.PRESENT,
                                       check_in_time=time(7, 42), check_out_time=time(15, 30),
                                       class_name="Grade 5", marked_by=self.teacher)
        self.client.force_login(self.parent)
        for url in (reverse("parent_portal:dashboard"), reverse("parent_portal:progress", args=[self.child.pk])):
            page = self.client.get(url).content.decode()
            # Today: check-out time always shown, check-in time never.
            panel = self._today_panel(page)
            self.assertNotIn("7:42", panel)
            self.assertIn("3:30 PM", panel)
            self.assertNotIn("In-School", panel)


class TermAttendanceExportTests(PortalTestBase):
    def test_month_picker_and_download_link_on_progress_page(self):
        self.client.force_login(self.parent)
        page = self._progress().content.decode()
        self.assertIn('name="month"', page)
        self.assertIn(reverse("parent_portal:term_attendance", args=[self.child.pk]), page)
        # Term 3 has not started, so it is not offered for download.
        self.assertIn(f'value="{self.t2.pk}"', page)
        self.assertNotIn(f'value="{self.t3.pk}"', page)

    def test_term_record_for_own_child_without_excuse_reasons(self):
        AttendanceEntry.objects.create(student=self.child, date=self.today - timedelta(days=1),
                                       status=AttendanceStatus.EXCUSED, reason="Private family matter",
                                       class_name="Grade 5", marked_by=self.teacher)
        self.client.force_login(self.parent)
        url = reverse("parent_portal:term_attendance", args=[self.child.pk])
        resp = self.client.get(url, {"term": self.t2.pk})
        self.assertEqual(resp.status_code, 200)
        page = resp.content.decode()
        self.assertIn("Student Attendance Record", page)
        self.assertIn("Sherissa", page)
        self.assertIn("Requested by", page)
        self.assertNotIn("Private family matter", page)
        self.assertNotIn(reverse("attendance:reports"), page)
        self.assertIn(reverse("parent_portal:progress", args=[self.child.pk]), page)

    def test_other_childs_record_is_404_and_staff_print_stays_closed(self):
        self.client.force_login(self.parent)
        url = reverse("parent_portal:term_attendance", args=[self.other_child.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        resp = self.client.get(reverse("attendance:print"), {"kind": "student", "student": self.child.pk})
        self.assertNotEqual(resp.status_code, 200)


class V11LayoutTests(PortalTestBase):
    def _complete_term2(self):
        for subject in ("Mathematics", "English"):
            self._score(subject, "quiz", 80)
            self._score(subject, "mid_term", 70)
            self._score(subject, "end_of_term", 90)

    def test_complete_term_is_labelled_complete_not_running(self):
        self._complete_term2()
        self.client.force_login(self.parent)
        page = self._progress().content.decode()
        self.assertIn(">Complete<", page)
        self.assertIn("Term average", page)
        self.assertNotIn("is in progress", page)

    def test_report_cards_sit_above_performance(self):
        self._report(self.child, self.t1, ReportCardStatus.PUBLISHED)
        self.client.force_login(self.parent)
        page = self._progress().content.decode()
        self.assertLess(page.index('id="report-cards"'), page.index('id="performance"'))
        self.assertLess(page.index('id="today"'), page.index('id="report-cards"'))

    def test_late_pickups_follow_the_school_setting_and_show_fee_due(self):
        from core.models import SchoolSettings
        settings_obj = SchoolSettings.get_settings()
        settings_obj.late_pickup_after = time(17, 0)
        settings_obj.late_pickup_fee = 10000
        settings_obj.save()
        day = self.today.replace(day=1)
        while day.weekday() > 3:  # a Mon-Thu inside this month, on or before today
            day += timedelta(days=1)
        if day > self.today:
            self.skipTest("No earlier weekday in this month.")
        AttendanceEntry.objects.create(student=self.child, date=day, status=AttendanceStatus.PRESENT,
                                       check_in_time=time(7, 30), check_out_time=time(17, 35),
                                       class_name="Grade 5", marked_by=self.teacher)
        self.client.force_login(self.parent)
        page = self._progress().content.decode()
        self.assertIn('class="pp-tbar pp-tbar-late"', page)
        self.assertIn("35 min", page)
        # Each late pick-up shows the fee as due; the tile totals them.
        self.assertIn('TZS 10,000 <span class="pp-chip pp-chip-L">Due</span>', page)
        self.assertIn("Late fees due", page)
        self.assertIn("1 this month", page)
        # Moving the setting later clears it.
        settings_obj.late_pickup_after = time(18, 0)
        settings_obj.save()
        page = self._progress().content.decode()
        self.assertNotIn('class="pp-tbar pp-tbar-late"', page)

    def test_v11_section_and_jump_link_order(self):
        self.client.force_login(self.parent)
        page = self._progress().content.decode()
        ids = ['id="today"', 'id="report-cards"', 'id="performance"', 'id="checkins"', 'id="attendance"']
        positions = [page.index(i) for i in ids]
        self.assertEqual(positions, sorted(positions))
        jump = page[page.index('class="pp-jump"'):]
        jump = jump[:jump.index("</nav>")]
        links = ["#today", "#report-cards", "#performance", "#checkins", "#attendance"]
        self.assertEqual([jump.index(l) for l in links], sorted(jump.index(l) for l in links))

    def test_late_fees_due_total_and_check_in_trend(self):
        from core.models import SchoolSettings
        settings_obj = SchoolSettings.get_settings()
        settings_obj.late_pickup_after = time(17, 0)
        settings_obj.late_pickup_fee = 10000
        settings_obj.save()
        days, d = [], self.today.replace(day=1)
        while d < self.today and len(days) < 3:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        if len(days) < 3:
            self.skipTest("Fewer than three earlier weekdays this month.")
        for day, out in zip(days, (time(17, 20), time(18, 5), time(16, 0))):
            AttendanceEntry.objects.create(student=self.child, date=day, status=AttendanceStatus.PRESENT,
                                           check_in_time=time(7, 40), check_out_time=out,
                                           class_name="Grade 5", marked_by=self.teacher)
        self.client.force_login(self.parent)
        resp = self._progress()
        times = resp.context["card_times"]
        self.assertEqual(times["avg_in"], "7:40 AM")
        self.assertEqual(len(times["in_days"]), 3)
        self.assertEqual([l["minutes_after"] for l in times["late"]], [20, 65])
        self.assertEqual(times["fees_due"], "TZS 20,000")
        page = resp.content.decode()
        self.assertIn("Average check-in", page)
        self.assertIn("Check-in times", page)
        self.assertIn("TZS 20,000", page)

    def test_check_in_section_has_its_own_month_navigation(self):
        self.client.force_login(self.parent)
        prev_month = (self.today.replace(day=1) - timedelta(days=1)).replace(day=1)
        page = self._progress().content.decode()
        section = page[page.index('id="checkins"'):page.index('id="attendance"')]
        self.assertIn(f'href="?month={prev_month:%Y-%m}#checkins"', section)
        page = self._progress(month=f"{prev_month:%Y-%m}").content.decode()
        section = page[page.index('id="checkins"'):page.index('id="attendance"')]
        self.assertIn(f'<span class="pp-card-title">{prev_month:%B %Y}</span>', section)

    def test_no_fee_setting_hides_fees_and_shows_minutes_past(self):
        from core.models import SchoolSettings
        settings_obj = SchoolSettings.get_settings()
        settings_obj.late_pickup_fee = 0
        settings_obj.save()
        self.client.force_login(self.parent)
        page = self._progress().content.decode()
        self.assertNotIn("Late fees due", page)
        self.assertNotIn("Late fee", page)
        self.assertIn("Minutes past", page)

    def test_top_grade_is_a_plus_not_a_star(self):
        for subject in ("Mathematics", "English"):
            self._score(subject, "quiz", 95)
            self._score(subject, "mid_term", 95)
            self._score(subject, "end_of_term", 95)
        self.client.force_login(self.parent)
        resp = self._progress()
        self.assertEqual(resp.context["academic"]["grade"], "A+")
        page = resp.content.decode()
        self.assertIn(">A+<", page)
        self.assertNotIn("A*", page)

    def test_ecd_student_sees_published_progress_report_not_marks(self):
        from academics.models import ECDItem, ECDSection, ECDEvaluation
        kid = Student.objects.create(admission_no="PS-001", first_name="Zawadi", last_name="K",
                                     class_name="Pre-School", academic_year=self.year, status=StudentStatus.ACTIVE)
        StudentGuardian.objects.create(student=kid, guardian=ParentGuardian.objects.get(user=self.parent),
                                       relationship="mother")
        self.client.force_login(self.parent)
        url = reverse("parent_portal:progress", args=[kid.pk])
        page = self.client.get(url).content.decode()
        self.assertIn("Academic progress", page)
        self.assertIn("No progress report yet", page)
        self.assertNotIn("Quiz", page)
        # A draft report's ratings are never shown.
        section = ECDSection.objects.create(class_type="pre_school", title="Numeracy",
                                            part=ECDSection.Part.ACADEMIC)
        item = ECDItem.objects.create(section=section, label="Counting")
        draft = self._report(kid, self.t1, ReportCardStatus.DRAFT)
        draft.is_ecd_report = True
        draft.save()
        ECDEvaluation.objects.create(report_card=draft, item=item, domain="Counting", rating="3")
        self.assertIn("No progress report yet", self.client.get(url).content.decode())
        draft.status = ReportCardStatus.PUBLISHED
        draft.published_at = timezone.now()
        draft.save()
        page = self.client.get(url).content.decode()
        self.assertIn("3.0", page)
        self.assertIn(reverse("parent_portal:report_card", args=[kid.pk, draft.pk]), page)
        home = self.client.get(reverse("parent_portal:dashboard")).content.decode()
        self.assertIn("average point", home)


class DeploymentAndStaffViewTests(PortalTestBase):
    def test_pages_do_not_depend_on_collected_static_files(self):
        """Styles and the sign-in script are inline, so a deploy without
        collectstatic can't leave the portal unstyled or sign-in broken."""
        self.client.force_login(self.parent)
        for url in (reverse("parent_portal:dashboard"), reverse("parent_portal:progress", args=[self.child.pk]),
                    reverse("parent_portal:calendar")):
            page = self.client.get(url).content.decode()
            self.assertIn("<style>", page)
            self.assertIn(".pp-btn-primary", page)
            self.assertNotIn("/static/parent_portal/", page)
        self.client.logout()
        login = self.client.get(reverse("parent_portal:login")).content.decode()
        self.assertIn("<style>", login)
        self.assertIn("<script>", login)
        self.assertNotIn("/static/parent_portal/", login)

    def test_term_year_is_not_repeated(self):
        Term.objects.filter(pk=self.t2.pk).update(name="Term 2 - 2026/2027")
        self.client.force_login(self.parent)
        home = self.client.get(reverse("parent_portal:dashboard")).content.decode()
        self.assertIn("Term 2 &middot; 2026/2027", home)
        self.assertNotIn("2026/2027 &middot; 2026/2027", home)
        resp = self._progress()
        self.assertEqual([c["term"].short_name for c in resp.context["academic"]["term_cards"]],
                         ["Term 1", "Term 2", "Term 3"])
        self.assertIn("Term 2 average", resp.content.decode())

    def test_staff_viewing_as_parent_get_exit_not_banner(self):
        admin = User.objects.create_user(username="sa", email="sa@x.test", password="x", role=UserRole.SUPER_ADMIN)
        self.client.force_login(admin)
        session = self.client.session
        session["impersonate_user_id"] = self.parent.pk
        session.save()
        page = self.client.get(reverse("parent_portal:dashboard")).content.decode()
        self.assertIn("Sherissa", page)
        self.assertIn("Exit parent view", page)
        self.assertIn(reverse("users:impersonate_stop"), page)
        self.assertNotIn(">Sign out<", page)
        self.assertNotIn("Viewing as", page)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="pp-media-"))
class LearnerPhotoTests(PortalTestBase):
    JPEG = b"\xff\xd8\xff\xe0fakejpeg"

    def test_photo_shown_when_available_and_only_to_own_parent(self):
        self.child.photo.save("sherissa.jpg", ContentFile(self.JPEG), save=True)
        self.other_child.photo.save("baraka.jpg", ContentFile(self.JPEG), save=True)
        photo_url = reverse("parent_portal:learner_photo", args=[self.child.pk])
        self.client.force_login(self.parent)
        for url in (reverse("parent_portal:dashboard"), reverse("parent_portal:progress", args=[self.child.pk])):
            page = self.client.get(url).content.decode()
            self.assertIn(f'src="{photo_url}?v=', page)
            self.assertNotIn("/media/", page)       # served through the ownership check only
        resp = self.client.get(photo_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/jpeg")
        self.assertEqual(b"".join(resp.streaming_content), self.JPEG)
        self.assertIn("private", resp["Cache-Control"])
        # Someone else's child: not found.
        self.assertEqual(self.client.get(reverse("parent_portal:learner_photo",
                                                 args=[self.other_child.pk])).status_code, 404)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(photo_url).status_code, 403)

    def test_admission_photo_is_used_when_profile_has_none(self):
        from admissions.models import Applicant
        applicant = Applicant.objects.create(child_full_name="Sherissa Kimaro", parent_full_name="Parent",
                                             parent_phone="0767016940", grade_applying_for="Grade 5",
                                             enrolled_student=self.child)
        applicant.photo.save("passport.png", ContentFile(b"\x89PNG\r\n\x1a\nfake"), save=True)
        self.client.force_login(self.parent)
        resp = self.client.get(reverse("parent_portal:learner_photo", args=[self.child.pk]))
        self.assertEqual((resp.status_code, resp["Content-Type"]), (200, "image/png"))

    def test_initials_when_no_usable_photo(self):
        self.client.force_login(self.parent)
        photo_url = reverse("parent_portal:learner_photo", args=[self.child.pk])
        # No photo at all.
        page = self.client.get(reverse("parent_portal:dashboard")).content.decode()
        self.assertNotIn(photo_url, page)
        self.assertIn(">SK<", page)
        self.assertEqual(self.client.get(photo_url).status_code, 404)
        # Record points at a file that is gone, or at something that isn't an image.
        for name in ("students/photos/gone.jpg", None):
            if name:
                self.child.photo.name = name
                self.child.save(update_fields=["photo"])
            else:
                self.child.photo.save("scan.pdf", ContentFile(b"%PDF-1.4"), save=True)
            page = self.client.get(reverse("parent_portal:progress", args=[self.child.pk])).content.decode()
            self.assertNotIn(photo_url, page)
            self.assertIn(">SK<", page)
            self.assertEqual(self.client.get(photo_url).status_code, 404)
