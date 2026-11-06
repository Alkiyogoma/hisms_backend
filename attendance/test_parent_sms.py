"""
Parent SMS on attendance events: parents are texted when their child is
checked out (picked up), never when the child is checked in.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from attendance import tasks
from attendance.models import AttendanceEntry, Message, NotificationLog
from attendance.notification_service import NotificationService
from attendance.services import AttendanceService
from students.models import (
    LaravelParent, ParentGuardian, Student, StudentGuardian, StudentLaravelParent,
)
from users.models import UserRole

User = get_user_model()

_BACKGROUND_TASKS = (
    "broadcast_checkin_event_task", "broadcast_checkout_event_task",
    "broadcast_statistics_update_task", "audit_log_task",
)


def _run_checkout_task_inline(student_id, entry_id, timestamp_iso, parent_name=""):
    tasks.send_checkout_notification_task.apply(args=(student_id, entry_id, timestamp_iso, parent_name))


class ParentSmsTests(TestCase):
    def setUp(self):
        # Keep the scan path off the Celery broker.
        for name in _BACKGROUND_TASKS:
            p = patch.object(getattr(tasks, name), "delay")
            p.start()
            self.addCleanup(p.stop)
        checkout_delay = patch.object(tasks.send_checkout_notification_task, "delay",
                                      side_effect=_run_checkout_task_inline)
        checkout_delay.start()
        self.addCleanup(checkout_delay.stop)

        self.user = User.objects.create_user(
            username="gate", email="gate@hodari.ac.tz", password="x", role=UserRole.SUPER_ADMIN,
        )
        self.student = Student.objects.create(
            admission_no="SMS001", first_name="Amani", last_name="Juma",
            class_name="Grade 3", status="active",
        )
        self.mother = self._guardian("Mama Amani", "0712000001", primary=True)
        self.father = self._guardian("Baba Amani", "0712000002")

    def _guardian(self, name, phone, primary=False, archived=False, student=None):
        g = ParentGuardian.objects.create(full_name=name, phone=phone, is_archived=archived)
        StudentGuardian.objects.create(student=student or self.student, guardian=g, is_primary=primary)
        return g

    def _laravel_parent(self, phone, guardian=None, laravel_id=1):
        parent = LaravelParent.objects.create(
            first_name="Legacy", last_name="Parent", phone=phone,
            guardian=guardian, laravel_parent_id=laravel_id,
        )
        StudentLaravelParent.objects.create(student=self.student, parent=parent)
        return parent

    def _check_in(self, minutes_ago=2):
        return AttendanceService.checkin_student(
            self.student.admission_no, self.user,
            timestamp=timezone.now() - timedelta(minutes=minutes_ago),
            student=self.student, is_school_day_result=True,
        )

    def _check_out(self):
        return AttendanceService.checkout_student(
            self.student.admission_no, self.user, timestamp=timezone.now() - timedelta(minutes=1),
            parent_name="Mama Amani", student=self.student, is_school_day_result=True,
        )

    def _sent_to(self):
        return sorted(Message.objects.values_list("phone", flat=True))

    # --- check-in ---------------------------------------------------------

    def test_check_in_sends_no_sms(self):
        with patch.object(tasks.send_checkin_notification_task, "delay") as checkin_delay:
            result = self._check_in()
        self.assertTrue(result["success"], result)
        checkin_delay.assert_not_called()
        self.assertEqual(Message.objects.count(), 0)
        self.assertFalse(NotificationLog.objects.filter(notification_type="checkin").exists())

    def test_queued_check_in_task_from_before_the_change_sends_nothing(self):
        entry = AttendanceEntry.objects.create(student=self.student, date=timezone.localdate(),
                                               marked_by=self.user, status="present")
        tasks.send_checkin_notification_task.apply(
            args=(self.student.id, entry.id, timezone.now().isoformat()))
        self.assertEqual(Message.objects.count(), 0)

    def test_notification_service_check_in_sends_no_sms(self):
        AttendanceEntry.objects.create(student=self.student, date=timezone.now().date(),
                                       marked_by=self.user, status="present")
        result = NotificationService.send_checkin_notification(self.student, timezone.now())
        self.assertEqual(result["sms"], 0)
        self.assertEqual(Message.objects.count(), 0)
        self.assertFalse(hasattr(NotificationService, "CHECKIN_SMS_TEMPLATE"))

    # --- check-out --------------------------------------------------------

    def test_check_out_texts_each_guardian_once(self):
        self._check_in()
        result = self._check_out()
        self.assertTrue(result["success"], result)
        self.assertEqual(self._sent_to(), ["0712000001", "0712000002"])
        for msg in Message.objects.all():
            self.assertIn("picked up by Mama Amani", msg.message)
        self.assertEqual(NotificationLog.objects.filter(notification_type="checkout").count(), 2)

    def test_check_out_without_check_in_still_texts(self):
        self.assertTrue(self._check_out()["success"])
        self.assertEqual(len(self._sent_to()), 2)

    def test_second_check_out_scan_sends_nothing_more(self):
        self._check_out()
        result = self._check_out()
        self.assertEqual(result["error_code"], "ALREADY_CHECKED_OUT")
        self.assertEqual(Message.objects.count(), 2)

    def test_rerun_of_checkout_task_does_not_resend(self):
        self._check_out()
        entry = AttendanceEntry.objects.get(student=self.student)
        _run_checkout_task_inline(self.student.id, entry.id, timezone.now().isoformat())
        self.assertEqual(Message.objects.count(), 2)

    def test_archived_guardian_gets_no_sms(self):
        self._guardian("Old Guardian", "0712000003", archived=True)
        self._check_out()
        self.assertEqual(self._sent_to(), ["0712000001", "0712000002"])

    def test_guardian_with_no_phone_is_skipped(self):
        g = ParentGuardian.objects.create(full_name="No Phone", phone="0712999999")
        ParentGuardian.objects.filter(pk=g.pk).update(phone="  ")
        StudentGuardian.objects.create(student=self.student, guardian=g)
        self._check_out()
        self.assertEqual(self._sent_to(), ["0712000001", "0712000002"])

    def test_same_number_in_two_formats_is_texted_once(self):
        self._laravel_parent("+255 712 000 001")
        self._check_out()
        self.assertEqual(self._sent_to(), ["0712000001", "0712000002"])

    def test_laravel_parent_of_removed_guardian_gets_no_sms(self):
        removed = ParentGuardian.objects.create(full_name="Former Guardian", phone="0712000004")
        self._laravel_parent("0712000004", guardian=removed)
        self._check_out()
        self.assertEqual(self._sent_to(), ["0712000001", "0712000002"])

    def test_laravel_parent_of_archived_guardian_gets_no_sms(self):
        archived = self._guardian("Archived", "0712000005", archived=True)
        self._laravel_parent("0712000006", guardian=archived)
        self._check_out()
        self.assertEqual(self._sent_to(), ["0712000001", "0712000002"])

    def test_unlinked_laravel_parent_still_gets_sms(self):
        self._laravel_parent("0712000007")
        self._check_out()
        self.assertEqual(self._sent_to(), ["0712000001", "0712000002", "0712000007"])

    def test_other_students_guardian_gets_no_sms(self):
        sibling = Student.objects.create(admission_no="SMS002", first_name="Neema", last_name="Juma",
                                         class_name="Grade 1", status="active")
        self._guardian("Other Parent", "0712000008", student=sibling)
        self._check_out()
        self.assertEqual(self._sent_to(), ["0712000001", "0712000002"])
