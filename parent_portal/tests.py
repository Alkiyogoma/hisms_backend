"""
Parent portal v1: ownership, report publication, converted/weighted marks,
attendance rules and the school calendar.
"""
from datetime import date, time, timedelta
from decimal import Decimal

from django.test import TestCase
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
        # Marks never appear on Home.
        self.assertNotIn("16.6", page)
        self.assertNotIn("83", page)
        self.assertNotIn("average", page.lower())
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

    def test_today_shows_not_checked_out(self):
        AttendanceEntry.objects.create(student=self.child, date=self.today, status=AttendanceStatus.PRESENT,
                                       check_in_time=time(7, 42), class_name="Grade 5", marked_by=self.teacher)
        self.client.force_login(self.parent)
        for url in (reverse("parent_portal:dashboard"), reverse("parent_portal:progress", args=[self.child.pk])):
            page = self.client.get(url).content.decode()
            self.assertIn("07:42", page)
            self.assertIn("Not checked out", page)

    def test_checked_out_time_shown(self):
        AttendanceEntry.objects.create(student=self.child, date=self.today, status=AttendanceStatus.LATE,
                                       check_in_time=time(8, 21), check_out_time=time(15, 30),
                                       class_name="Grade 5", marked_by=self.teacher)
        info = services.today_status(self.child, self.today)
        self.assertEqual((info["label"], info["not_checked_out"]), ("Late", False))
        self.assertEqual(info["check_out"], time(15, 30))


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
        self.assertIn("/accounts/login/", resp["Location"])
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
        # No report section, navigation or "awaiting" placeholder before publication.
        self.assertNotIn("Report cards", page)
        self.assertNotIn("report-cards", page)
        self.assertNotIn("Not yet published", page)
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
        self.assertIn("Report cards", page)
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

    def test_charts_one_bar_per_assessed_term(self):
        self._score("Mathematics", "quiz", 75, term=self.t1)           # 15 /20, Term 1 final
        self._score("Mathematics", "quiz", 90)                         # 18 /20, Term 2 running
        self._score("English", "quiz", 80)                             # 16 /20
        self._score("Mathematics", "mid_term", 50, term=self.t1)       # 15 /30
        self.client.force_login(self.parent)
        charts = {c["type"].code: c for c in self._progress().context["academic"]["charts"]}
        quiz = [(b["term"].name, b["state"], b["value"]) for b in charts["quiz"]["bars"]]
        self.assertEqual(quiz, [("Term 1", "final", 15.0), ("Term 2", "running", 17.0), ("Term 3", "upcoming", None)])
        mid = [(b["state"], b["value"]) for b in charts["mid_term"]["bars"]]
        self.assertEqual(mid, [("final", 15.0), ("running", None), ("upcoming", None)])
        self.assertEqual((charts["quiz"]["max"], charts["mid_term"]["max"]), (20, 30))

        page = self._progress().content.decode()
        self.assertIn("Upcoming", page)
        self.assertIn("Running", page)
        # Upcoming terms get no bar at all.
        self.assertEqual(page.count('class="pp-bar pp-bar-running"') + page.count('class="pp-bar"'), 3)


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
        self.assertIn("No learners linked yet", self.client.get(reverse("parent_portal:dashboard")).content.decode())
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
        # No approved marks at all this year: a short message, no empty bars.
        self.assertIn("No approved Quiz marks yet this year.", page)
        self.assertNotIn('class="pp-bar', page)


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
        login = self.client.get(reverse("users:login")).content.decode()
        self.assertIn("getElementById('parentSignin')", login)
        self.assertNotIn("/static/parent_portal/", login)

    def test_term_year_is_not_repeated(self):
        Term.objects.filter(pk=self.t2.pk).update(name="Term 2 - 2026/2027")
        self.client.force_login(self.parent)
        home = self.client.get(reverse("parent_portal:dashboard")).content.decode()
        self.assertIn("Term 2 &middot; 2026/2027", home)
        self.assertNotIn("2026/2027 &middot; 2026/2027", home)
        resp = self._progress()
        self.assertEqual([b["term"].short_name for b in resp.context["academic"]["charts"][0]["bars"]],
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
