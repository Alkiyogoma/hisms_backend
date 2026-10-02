"""Attendance marking window: today only, 00:00-18:00, then super-admin only."""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceCorrectionRequest, AttendanceEntry
from attendance.policy import AttendanceWindowError, check_can_modify
from attendance.services import correct_attendance, create_excused_absence, mark_attendance
from audit.models import AuditLog
from students.models import Student
from users.models import UserRole

User = get_user_model()


def at(hour, minute=0, day_offset=0):
    tz = timezone.get_current_timezone()
    base = timezone.localtime().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (base + timedelta(days=day_offset)).astimezone(tz)


class MarkingWindowTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user("t1", "t1@x.edu", "pw", role=UserRole.TEACHER)
        self.admin = User.objects.create_user("sa", "sa@x.edu", "pw", role=UserRole.SUPER_ADMIN)
        self.student = Student.objects.create(
            admission_no="W1", first_name="A", last_name="B", class_name="Grade 1", status="active")
        self.today = timezone.localdate()

    def _mark_at(self, when, actor, status="present", date=None):
        with patch("django.utils.timezone.now", return_value=when):
            return mark_attendance(actor=actor, student=self.student, date=date or when.date(), status=status)

    def test_teacher_can_mark_today_before_18(self):
        e = self._mark_at(at(17, 59), self.teacher)
        self.assertEqual(e.status, "present")

    def test_future_date_rejected_for_everyone(self):
        for actor in (self.teacher, self.admin):
            with self.assertRaises(AttendanceWindowError):
                self._mark_at(at(9), actor, date=self.today + timedelta(days=1))

    def test_teacher_locked_out_at_18(self):
        self._mark_at(at(10), self.teacher)
        with self.assertRaises(AttendanceWindowError) as cm:
            self._mark_at(at(18), self.teacher, status="absent")
        self.assertEqual(cm.exception.code, "locked")
        self.assertEqual(AttendanceEntry.objects.get().status, "present")

    def test_teacher_cannot_edit_past_day(self):
        with self.assertRaises(AttendanceWindowError):
            self._mark_at(at(9), self.teacher, date=self.today - timedelta(days=1))

    def test_super_admin_override_is_audited_with_before_value(self):
        self._mark_at(at(10), self.teacher, status="present")
        self._mark_at(at(19), self.admin, status="absent")
        e = AttendanceEntry.objects.get()
        self.assertEqual((e.status, e.original_status, e.corrected_by), ("absent", "present", self.admin))
        log = AuditLog.objects.filter(action_type="ATTENDANCE_OVERRIDE", actor=self.admin).get()
        self.assertEqual(log.before_snapshot["status"], "present")
        self.assertEqual(log.after_snapshot["status"], "absent")

    def test_correct_attendance_locked_for_non_super_admin(self):
        e = self._mark_at(at(10), self.teacher)
        ao = User.objects.create_user("ao", "ao@x.edu", "pw", role=UserRole.ADMIN_OFFICER)
        with patch("django.utils.timezone.now", return_value=at(19)):
            denied = correct_attendance(ao, e, "absent", "oops")
            ok = correct_attendance(self.admin, e, "absent", "oops")
        self.assertFalse(denied["success"])
        self.assertTrue(ok["success"])

    def test_excused_respects_window(self):
        with patch("django.utils.timezone.now", return_value=at(19)):
            r = create_excused_absence(self.teacher, self.student, self.today, "sick")
        self.assertFalse(r["success"])

    def test_check_can_modify_boundaries(self):
        self.assertFalse(check_can_modify(self.teacher, self.today, now=at(0, 0)))
        with self.assertRaises(AttendanceWindowError):
            check_can_modify(self.teacher, self.today, now=at(18, 0))


class CorrectionRequestFlowTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user("t2", "t2@x.edu", "pw", role=UserRole.TEACHER)
        self.admin = User.objects.create_user("sa2", "sa2@x.edu", "pw", role=UserRole.SUPER_ADMIN)
        self.student = Student.objects.create(
            admission_no="W2", first_name="C", last_name="D", class_name="Grade 1", status="active")
        self.yesterday = timezone.localdate() - timedelta(days=1)
        AttendanceEntry.objects.create(date=self.yesterday, student=self.student, status="absent",
                                       marked_by=self.teacher, class_name="Grade 1")

    def test_teacher_requests_and_super_admin_approves(self):
        from django.contrib.auth.models import Permission
        self.teacher.user_permissions.add(Permission.objects.get(codename="view_attendanceentry"))
        self.client.force_login(self.teacher)
        r = self.client.post(reverse("attendance:request_correction"), {
            "student_id": self.student.pk, "date": self.yesterday, "status": "present", "reason": "Was in school"})
        self.assertEqual(r.status_code, 200)
        req = AttendanceCorrectionRequest.objects.get()
        self.assertEqual(req.status, "pending")

        # a second request for the same learner/day replaces the first
        self.client.post(reverse("attendance:request_correction"), {
            "student_id": self.student.pk, "date": self.yesterday, "status": "late", "reason": "Came at 9"})
        self.assertEqual(AttendanceCorrectionRequest.objects.count(), 1)
        req.refresh_from_db()
        self.assertEqual(req.requested_status, "late")

        # teacher can't open the inbox
        self.assertEqual(self.client.get(reverse("attendance:correction_requests")).status_code, 403)

        self.client.force_login(self.admin)
        # plain visit lands on the register with the drawer flag; htmx gets the drawer
        r = self.client.get(reverse("attendance:correction_requests"))
        self.assertRedirects(r, reverse("attendance:today") + "?corrections=1", fetch_redirect_response=False)
        r = self.client.get(reverse("attendance:correction_requests"), HTTP_HX_REQUEST="true")
        self.assertContains(r, "Came at 9")
        r = self.client.post(reverse("attendance:correction_requests"),
                             {"request_id": req.pk, "decision": "approve"}, HTTP_HX_REQUEST="true")
        self.assertContains(r, "Request approved")
        self.assertEqual(r["HX-Trigger"], "att-corrections-changed")
        req.refresh_from_db()
        entry = AttendanceEntry.objects.get()
        self.assertEqual(req.status, "approved")
        self.assertEqual((entry.status, entry.original_status, entry.corrected_by), ("late", "absent", self.admin))

    def test_register_page_renders_for_teacher_and_admin(self):
        from django.contrib.auth.models import Permission
        self.teacher.user_permissions.add(Permission.objects.get(codename="view_attendanceentry"))
        for user in (self.teacher, self.admin):
            self.client.force_login(user)
            r = self.client.get(reverse("attendance:today") + f"?date={self.yesterday}")
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, 'id="reqCorrectionModal"')
            self.assertContains(r, "This register is locked")
        self.assertContains(r, "Correction requests")
