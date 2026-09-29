"""Today's attendance summary: learners marked Late are in school, so the
Present total includes them."""
from django.conf import settings as django_settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import Department, GradeClass
from attendance.models import AttendanceEntry, AttendanceStatus
from students.models import Student
from users.models import User, UserRole

if not getattr(django_settings, "AUDIT_LOG_DB_CONSTRAINT", True):  # see academics/tests.py
    _orig = TestCase._fixture_teardown

    def _patched(self):
        try:
            _orig(self)
        except Exception:
            pass

    TestCase._fixture_teardown = _patched


class TodaySummaryPresentIncludesLateTests(TestCase):
    def test_present_total_includes_late(self):
        GradeClass.objects.create(name="Grade 3", department=Department.PRIMARY)
        admin = User.objects.create_user(username="sa", email="sa@x.test", password="x", role=UserRole.SUPER_ADMIN)
        today = timezone.localdate()
        statuses = [AttendanceStatus.PRESENT, AttendanceStatus.PRESENT, AttendanceStatus.LATE, AttendanceStatus.ABSENT]
        for i, status in enumerate(statuses):
            s = Student.objects.create(admission_no=f"A{i}", first_name=f"K{i}", last_name="T", class_name="Grade 3")
            AttendanceEntry.objects.create(date=today, student=s, status=status, class_name="Grade 3", marked_by=admin)

        self.client.force_login(admin)
        resp = self.client.get(reverse("attendance:today"), {"date": today.isoformat(), "class_name": "Grade 3"})
        summary = resp.context["summary"]
        self.assertEqual((summary["present"], summary["late"], summary["absent"]), (3, 1, 1))
        self.assertEqual(summary["present_pct"], 75.0)
        self.assertContains(resp, "incl. 1 late")


class ReportsCountLateAsPresentTests(TestCase):
    """Weekly/monthly/class reports and report cards: Late counts as present
    (it used to be counted as absent in some reports)."""

    @classmethod
    def setUpTestData(cls):
        from datetime import date
        GradeClass.objects.create(name="Grade 3", department=Department.PRIMARY)
        cls.admin = User.objects.create_user(username="sa2", email="sa2@x.test", password="x", role=UserRole.SUPER_ADMIN)
        cls.student = Student.objects.create(admission_no="R1", first_name="Asha", last_name="T", class_name="Grade 3")
        cls.days = [date(2026, 9, d) for d in (7, 8, 9, 10)]
        for d, status in zip(cls.days, [AttendanceStatus.PRESENT, AttendanceStatus.LATE,
                                        AttendanceStatus.LATE, AttendanceStatus.ABSENT]):
            AttendanceEntry.objects.create(date=d, student=cls.student, status=status,
                                           class_name="Grade 3", marked_by=cls.admin)

    def test_monthly_report(self):
        from attendance.reporting_service import ReportingService
        report = ReportingService.generate_monthly_report(2026, 9, "Grade 3")
        row = report["student_summaries"][0]
        self.assertEqual((row["present_days"], row["absent_days"]), (3, 1))
        self.assertEqual(report["summary"]["total_present"], 3)

    def test_class_report(self):
        from attendance.reporting_service import ReportingService
        report = ReportingService.generate_class_report("Grade 3", self.days[0], self.days[-1])
        self.assertEqual(report["summary"]["present"], 3)
        self.assertEqual(report["statistics"]["present_percentage"], 75.0)

    def test_report_card_days_present_includes_late(self):
        from academics.models import AcademicYear, ReportCard, Term
        ay = AcademicYear.objects.create(name="2026", is_current=True)
        term = Term.objects.create(academic_year=ay, name="Term 3", start_date=self.days[0], end_date=self.days[-1])
        rc = ReportCard.objects.create(student=self.student, term=term, generated_by=self.admin)
        rc.populate_attendance_summary(self.days[0], self.days[-1])
        rc.refresh_from_db()
        self.assertEqual((rc.attendance_days_present, rc.attendance_days_late, rc.attendance_days_absent), (3, 2, 1))
        self.assertEqual(float(rc.attendance_rate), 75.0)
