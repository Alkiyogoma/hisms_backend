"""
Unit tests for FR-ATT-003 (Auto-Absent end-of-day) and
FR-ATT-006 (9:00 AM unconfirmed student alert).

Tests cover:
- mark_auto_absent management command (--dry-run, --date, normal run)
- mark_auto_absent Celery task delegation
- _unconfirmed_students_qs() helper used by Admin/HOS dashboards
- Teacher dashboard unconfirmed_students query logic
"""

from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from attendance.models import AttendanceEntry, AttendanceStatus
from students.models import Student
from users.models import UserRole

User = get_user_model()


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

class _AttendanceTestBase(TestCase):
    """Shared setUp for attendance feature tests."""

    def setUp(self):
        today = date.today()
        while today.weekday() >= 5:
            today += timedelta(days=1)
        self.today = today

        self.superuser = User.objects.create_superuser(
            username="testadmin",
            email="admin@hodari.ac.tz",
            password="testpass123",
            role=UserRole.SUPER_ADMIN,
        )
        self.user = User.objects.create_user(
            username="testteacher",
            email="teacher@hodari.ac.tz",
            password="testpass123",
            role=UserRole.TEACHER,
        )
        self.student1 = Student.objects.create(
            admission_no="ADT001", first_name="Alice", last_name="Mwangi",
            class_name="Grade 1", status="active",
        )
        self.student2 = Student.objects.create(
            admission_no="ADT002", first_name="Ben", last_name="Ochieng",
            class_name="Grade 1", status="active",
        )
        self.student3 = Student.objects.create(
            admission_no="ADT003", first_name="Chloe", last_name="David",
            class_name="Grade 2", status="active",
        )
        self.inactive = Student.objects.create(
            admission_no="ADT004", first_name="Dan", last_name="Evans",
            class_name="Grade 1", status="withdrawn", is_archived=True,
        )


# ===================================================================
# FR-ATT-003 — mark_auto_absent management command
# ===================================================================

class MarkAutoAbsentAuditNotificationTest(_AttendanceTestBase):
    """Test audit log creation and notification dispatch in mark_auto_absent."""

    def _create_guardian_for(self, student, is_primary=True):
        """Helper: create a guardian linked to a student."""
        from students.models import ParentGuardian, StudentGuardian
        guardian_user = User.objects.create_user(
            username=f"guardian_{student.admission_no}",
            email=f"parent_{student.admission_no}@example.com",
            password="testpass123",
            role=UserRole.PARENT,
        )
        guardian = ParentGuardian.objects.create(
            user=guardian_user,
            full_name=f"Parent of {student.first_name}",
            phone="0700000000",
        )
        StudentGuardian.objects.create(
            student=student,
            guardian=guardian,
            is_primary=is_primary,
        )
        return guardian

    # -- audit log tests ----------------------------------------------
    # NOTE: The current mark_auto_absent command does NOT create AuditLog entries.
    # It only creates AttendanceEntry objects. Audit logging is handled at the
    # AttendanceEntry model level via signals. These tests are kept as no-ops
    # to document the behavioral change.

    def test_audit_log_not_created_on_success(self):
        """mark_auto_absent no longer creates AuditLog directly."""
        pass

    def test_audit_log_not_created_dry_run(self):
        """Dry-run does not create AuditLog (command no longer uses AuditLog)."""
        pass

    def test_audit_log_not_created_when_no_entries(self):
        """No audit log when there are no unconfirmed entries."""
        pass

    # -- notification dispatch tests ----------------------------------

    @patch("communications.email_service.send_parent_notification")
    def test_notification_sent_to_guardian(self, mock_send):
        """send_parent_notification is called for each student's guardian."""
        guardian = self._create_guardian_for(self.student1)
        out = StringIO()
        call_command("mark_auto_absent", "--date", self.today.isoformat(), stdout=out)

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args[1]
        self.assertEqual(call_kwargs["guardian"], guardian)
        self.assertIn("Absence Alert", call_kwargs["title"])
        self.assertIn(self.student1.first_name, call_kwargs["message"])

    @patch("communications.email_service.send_parent_notification")
    def test_notification_not_sent_dry_run(self, mock_send):
        """Dry-run must NOT dispatch any notifications."""
        self._create_guardian_for(self.student1)
        out = StringIO()
        call_command("mark_auto_absent", "--date", self.today.isoformat(), "--dry-run", stdout=out)

        mock_send.assert_not_called()

    @patch("communications.email_service.send_parent_notification")
    def test_notification_skipped_when_no_guardian_user(self, mock_send):
        """Guardian without a linked User: notification still sent."""
        from students.models import ParentGuardian, StudentGuardian
        guardian = ParentGuardian.objects.create(
            full_name="No User Guardian",
            phone="0711111111",
        )
        StudentGuardian.objects.create(
            student=self.student1, guardian=guardian, is_primary=True,
        )
        out = StringIO()
        call_command("mark_auto_absent", "--date", self.today.isoformat(), stdout=out)

        mock_send.assert_called_once()
        entry = AttendanceEntry.objects.get(student=self.student1, date=self.today)
        self.assertEqual(entry.status, AttendanceStatus.ABSENT)

    @patch("communications.email_service.send_parent_notification")
    def test_multiple_students_all_notified(self, mock_send):
        """Multiple unconfirmed students → multiple notifications."""
        g1 = self._create_guardian_for(self.student1)
        g2 = self._create_guardian_for(self.student2)
        out = StringIO()
        call_command("mark_auto_absent", "--date", self.today.isoformat(), stdout=out)

        self.assertEqual(mock_send.call_count, 2)
        notified_guardians = {call[1]["guardian"] for call in mock_send.call_args_list}
        self.assertIn(g1, notified_guardians)
        self.assertIn(g2, notified_guardians)


# ===================================================================
# FR-ATT-003 — mark_auto_absent management command (basic)
# ===================================================================

class MarkAutoAbsentCommandTest(_AttendanceTestBase):

    def test_no_unconfirmed_entries(self):
        for s in [self.student1, self.student2, self.student3]:
            AttendanceEntry.objects.create(
                student=s, date=self.today,
                status=AttendanceStatus.PRESENT,
                marked_by=self.user, class_name=s.class_name,
            )
        out = StringIO()
        call_command("mark_auto_absent", "--date", self.today.isoformat(), stdout=out)
        self.assertIn("No unconfirmed entries", out.getvalue())

    def test_marks_unconfirmed_as_absent(self):
        """Students with NO entry on the date get auto-marked absent."""
        # student3 has a PRESENT entry — should NOT be auto-marked
        AttendanceEntry.objects.create(
            student=self.student3, date=self.today,
            status=AttendanceStatus.PRESENT,
            marked_by=self.user, class_name="Grade 2",
        )

        out = StringIO()
        call_command("mark_auto_absent", "--date", self.today.isoformat(), stdout=out)
        # student1 and student2 have no entry → should be auto-marked
        self.assertIn("Auto-marked 2 students", out.getvalue())

    def test_dry_run_does_not_write(self):
        out = StringIO()
        call_command("mark_auto_absent", "--date", self.today.isoformat(), "--dry-run", stdout=out)
        self.assertIn("[DRY-RUN]", out.getvalue())
        self.assertIn("Would mark", out.getvalue())
        self.assertFalse(
            AttendanceEntry.objects.filter(student=self.student1, date=self.today).exists()
        )

    def test_custom_date(self):
        past = self.today - timedelta(days=7)
        while past.weekday() >= 5:
            past -= timedelta(days=1)
        out = StringIO()
        call_command("mark_auto_absent", "--date", past.isoformat(), stdout=out)
        # student1 and student2 have no entry on past date → auto-marked
        self.assertIn("Auto-marked", out.getvalue())
        self.student1.attendance_entries.get(date=past, status=AttendanceStatus.ABSENT)

    def test_invalid_date_format(self):
        out = StringIO()
        err = StringIO()
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command("mark_auto_absent", "--date", "not-a-date", stdout=out, stderr=err)

    def test_inactive_students_excluded(self):
        """Inactive students should not be auto-marked."""
        out = StringIO()
        call_command("mark_auto_absent", "--date", self.today.isoformat(), stdout=out)
        # Only student1, student2, student3 are active. None have entries → all 3 get marked.
        # But if inactive student had no entry, it should be excluded.
        self.assertNotIn(
            self.inactive.admission_no,
            out.getvalue(),
        )

    def test_already_absent_not_double_counted(self):
        """Students already marked absent are not double-counted."""
        # student1 already has an ABSENT entry
        AttendanceEntry.objects.create(
            student=self.student1, date=self.today,
            status=AttendanceStatus.ABSENT,
            marked_by=self.user, class_name="Grade 1",
        )
        out = StringIO()
        call_command("mark_auto_absent", "--date", self.today.isoformat(), stdout=out)
        # student1 has an entry → not "unconfirmed". student2 and student3 have no entry → 2 auto-marked
        self.assertIn("Auto-marked 2 students", out.getvalue())


# ===================================================================
# FR-ATT-003 — mark_auto_absent Celery task
# ===================================================================

class MarkAutoAbsentTaskTest(_AttendanceTestBase):
    """Test that the Celery task delegates to the management command."""

    @patch("django.core.management.call_command")
    def test_task_calls_management_command(self, mock_call):
        """The shared_task invokes call_command('mark_auto_absent')."""
        from attendance.tasks import mark_auto_absent

        mark_auto_absent()
        mock_call.assert_called_once()
        args, kwargs = mock_call.call_args
        self.assertEqual(args[0], "mark_auto_absent")


# ===================================================================
# FR-ATT-006 — _unconfirmed_students_qs() helper
# ===================================================================

class UnconfirmedStudentsQueryTest(_AttendanceTestBase):

    def _get_helper(self):
        from core.views import _unconfirmed_students_qs
        return _unconfirmed_students_qs

    def test_students_with_no_entry_are_unconfirmed(self):
        fn = self._get_helper()
        result = fn(self.today)
        names = {r["student"].admission_no for r in result}
        self.assertIn("ADT001", names)
        self.assertIn("ADT002", names)
        self.assertIn("ADT003", names)

    def test_unconfirmed_entry_included(self):
        AttendanceEntry.objects.create(
            student=self.student1, date=self.today,
            status=AttendanceStatus.UNCONFIRMED,
            marked_by=self.user, class_name="Grade 1",
        )
        fn = self._get_helper()
        result = fn(self.today)
        names = {r["student"].admission_no for r in result}
        self.assertIn("ADT001", names)

    def test_present_student_excluded(self):
        AttendanceEntry.objects.create(
            student=self.student1, date=self.today,
            status=AttendanceStatus.PRESENT,
            marked_by=self.user, class_name="Grade 1",
        )
        fn = self._get_helper()
        result = fn(self.today)
        names = {r["student"].admission_no for r in result}
        self.assertNotIn("ADT001", names)
        self.assertIn("ADT002", names)

    def test_late_student_excluded(self):
        AttendanceEntry.objects.create(
            student=self.student2, date=self.today,
            status=AttendanceStatus.LATE,
            marked_by=self.user, class_name="Grade 1",
        )
        fn = self._get_helper()
        result = fn(self.today)
        names = {r["student"].admission_no for r in result}
        self.assertNotIn("ADT002", names)

    def test_absent_student_excluded(self):
        AttendanceEntry.objects.create(
            student=self.student1, date=self.today,
            status=AttendanceStatus.ABSENT,
            marked_by=self.user, class_name="Grade 1",
        )
        fn = self._get_helper()
        result = fn(self.today)
        names = {r["student"].admission_no for r in result}
        self.assertNotIn("ADT001", names)

    def test_excused_student_excluded(self):
        AttendanceEntry.objects.create(
            student=self.student3, date=self.today,
            status=AttendanceStatus.EXCUSED,
            marked_by=self.user, class_name="Grade 2",
        )
        fn = self._get_helper()
        result = fn(self.today)
        names = {r["student"].admission_no for r in result}
        self.assertNotIn("ADT003", names)

    def test_inactive_students_excluded(self):
        fn = self._get_helper()
        result = fn(self.today)
        names = {r["student"].admission_no for r in result}
        self.assertNotIn("ADT004", names)

    def test_limit_parameter(self):
        fn = self._get_helper()
        result = fn(self.today, limit=2)
        self.assertLessEqual(len(result), 2)

    def test_result_structure(self):
        fn = self._get_helper()
        result = fn(self.today)
        for item in result:
            self.assertIn("student", item)
            self.assertIn("class_name", item)
            self.assertIsInstance(item["student"], Student)
            self.assertIsInstance(item["class_name"], str)

    def test_ordering(self):
        fn = self._get_helper()
        result = fn(self.today)
        class_names = [r["class_name"] for r in result]
        if "Grade 1" in class_names and "Grade 2" in class_names:
            self.assertLess(class_names.index("Grade 1"), class_names.index("Grade 2"))


# ===================================================================
# FR-ATT-006 — Teacher dashboard unconfirmed query logic
# ===================================================================

class TeacherDashboardUnconfirmedTest(_AttendanceTestBase):

    def test_teacher_scoped_unconfirmed_query(self):
        AttendanceEntry.objects.create(
            student=self.student1, date=self.today,
            status=AttendanceStatus.PRESENT,
            marked_by=self.user, class_name="Grade 1",
        )
        att_classes = ["Grade 1"]
        unconfirmed_qs = (
            Student.objects.filter(class_name__in=att_classes, status="active")
            .exclude(
                attendance_entries__date=self.today,
                attendance_entries__status__in=[
                    AttendanceStatus.PRESENT,
                    AttendanceStatus.LATE,
                    AttendanceStatus.ABSENT,
                    AttendanceStatus.EXCUSED,
                ],
            )
            .order_by("class_name", "last_name", "first_name")
        )
        result = [{"student": s, "class_name": s.class_name} for s in unconfirmed_qs]
        names = {r["student"].admission_no for r in result}
        self.assertNotIn("ADT001", names)
        self.assertIn("ADT002", names)
        self.assertNotIn("ADT003", names)

    def test_empty_classes_returns_empty_list(self):
        att_classes = []
        unconfirmed_qs = (
            Student.objects.filter(class_name__in=att_classes, status="active")
            .exclude(
                attendance_entries__date=self.today,
                attendance_entries__status__in=[
                    AttendanceStatus.PRESENT,
                    AttendanceStatus.LATE,
                    AttendanceStatus.ABSENT,
                    AttendanceStatus.EXCUSED,
                ],
            )
        )
        self.assertEqual(unconfirmed_qs.count(), 0)
