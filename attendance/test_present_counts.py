"""Gate screen counts: 'Present Today' must not just mirror the check-outs."""
from datetime import time

from django.utils import timezone
from rest_framework.test import APITestCase

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.tests import assign_role_group
from attendance.models import AttendanceEntry, AttendanceStatus
from students.models import Student
from users.models import User, UserRole


class PresentAndCheckedOutCountsTests(APITestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.admin = User.objects.create_user(username="ao", email="ao@x.test", password="x",
                                              role=UserRole.ADMIN_OFFICER)
        assign_role_group(self.admin)

        def student(n, cls):
            return Student.objects.create(admission_no=f"S{n}", first_name=f"Kid{n}", last_name="T",
                                          gender="male", status="active", class_name=cls)
        self.scanned_in, self.register, self.left, self.only_out, self.absent = (
            student(1, "Grade 1"), student(2, "Grade 1"), student(3, "Grade 2"),
            student(4, "Grade 2"), student(5, "Grade 3"))

        def entry(st, **kw):
            return AttendanceEntry.objects.create(date=self.today, student=st, class_name=st.class_name,
                                                  marked_by=self.admin, **kw)
        entry(self.scanned_in, status=AttendanceStatus.PRESENT, check_in_time=time(7, 30))
        entry(self.register, status=AttendanceStatus.LATE)                       # class register, no scan
        entry(self.left, status=AttendanceStatus.PRESENT, check_in_time=time(7, 40),
              check_out_time=time(13, 0), checkout_by=self.admin)
        entry(self.absent, status=AttendanceStatus.ABSENT)

    def _checkout_only(self):
        """Check out a learner who was never checked in, through the real service."""
        from attendance.services import AttendanceService
        result = AttendanceService.checkout_student(
            student_id=self.only_out.admission_no, user=self.admin, timestamp=timezone.now(),
            parent_name="Mum", is_school_day_result=True)
        self.assertTrue(result["success"], result)

    def _get(self, user=None):
        self.client.force_authenticate(user or self.admin)
        resp = self.client.get("/attendance/api/scans/today/")
        self.assertEqual(resp.status_code, 200)
        return resp.data

    def test_checkout_does_not_count_as_check_in_or_present(self):
        self._checkout_only()
        entry = AttendanceEntry.objects.get(student=self.only_out, date=self.today)
        self.assertIsNone(entry.check_in_time)

        data = self._get()
        self.assertEqual(data["summary"], {"present": 3, "checked_in": 2, "checked_out": 2,
                                           "still_in_school": 2})
        names = {p["student_name"] for p in data["present"]}
        self.assertEqual(names, {"Kid1 T", "Kid2 T", "Kid3 T"})
        self.assertNotIn("Kid4 T", {s["student_name"] for s in data["data"] if s["scan_type"] == "IN"})

    def test_old_copied_check_in_is_ignored(self):
        # How check-out used to save a learner it never saw come in.
        AttendanceEntry.objects.create(date=self.today, student=self.only_out, class_name="Grade 2",
                                       marked_by=self.admin, status=AttendanceStatus.PRESENT,
                                       check_in_time=timezone.localtime().time().replace(microsecond=0),
                                       check_out_time=timezone.localtime().time().replace(microsecond=0),
                                       checkout_by=self.admin)
        summary = self._get()["summary"]
        self.assertEqual(summary["checked_in"], 2)
        self.assertEqual(summary["present"], 3)
        self.assertEqual(summary["checked_out"], 2)

    def test_register_present_shows_without_check_in_time(self):
        row = next(p for p in self._get()["present"] if p["student_name"] == "Kid2 T")
        self.assertIsNone(row["check_in_time"])
        self.assertIsNone(row["check_out_time"])

    def test_teacher_sees_whole_school(self):
        teacher = User.objects.create_user(username="tt", email="tt@x.test", password="x",
                                           role=UserRole.TEACHER)
        assign_role_group(teacher)
        self.assertEqual(self._get(teacher)["summary"]["present"], 3)
