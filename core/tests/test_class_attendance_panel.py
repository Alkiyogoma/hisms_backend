"""HOD dashboards' "Class attendance today" panels: present students over
enrolled (active) students, each student counted once, never above 100%.

They used to add Late on top of PRESENT_STATUSES (which already includes Late)
and count entries of students no longer active in the class, giving 106%-178%."""
from django.conf import settings as django_settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import Department, GradeClass
from attendance.models import AttendanceEntry, AttendanceStatus
from core.views import _class_attendance_today
from students.models import Student, StudentStatus
from users.models import User, UserRole

if not getattr(django_settings, "AUDIT_LOG_DB_CONSTRAINT", True):  # see academics/tests.py
    _orig = TestCase._fixture_teardown

    def _patched(self):
        try:
            _orig(self)
        except Exception:
            pass

    TestCase._fixture_teardown = _patched


class ClassAttendancePanelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        GradeClass.objects.create(name="Grade 1", department=Department.PRIMARY)
        GradeClass.objects.create(name="Pre-Kindergarten", department=Department.ECD)
        cls.admin = User.objects.create_user(username="sa", email="sa@x.test", password="x", role=UserRole.SUPER_ADMIN)
        n = 0

        def add(class_name, status, student_status=StudentStatus.ACTIVE, entry_class=None):
            nonlocal n
            n += 1
            s = Student.objects.create(admission_no=f"A{n}", first_name=f"K{n}", last_name="T",
                                       class_name=class_name, status=student_status)
            if status:
                AttendanceEntry.objects.create(date=cls.today, student=s, status=status,
                                               class_name=entry_class or class_name, marked_by=cls.admin)

        # Every enrolled student present, all of them late: must be 100%, not 200%.
        for _ in range(4):
            add("Grade 1", AttendanceStatus.LATE)
        # A student who left but was still marked present must not count.
        add("Grade 1", AttendanceStatus.PRESENT, student_status=StudentStatus.WITHDRAWN)

        # Pre-K: 4 enrolled; 2 present, 1 late, 1 absent -> 3/4 = 75%.
        add("Pre-Kindergarten", AttendanceStatus.PRESENT)
        add("Pre-Kindergarten", AttendanceStatus.PRESENT)
        add("Pre-Kindergarten", AttendanceStatus.LATE)
        add("Pre-Kindergarten", AttendanceStatus.ABSENT)

    def test_helper_counts_each_enrolled_student_once(self):
        result = _class_attendance_today(self.today, ["Grade 1", "Pre-Kindergarten"])
        self.assertEqual(result["Grade 1"], (4, 4, 100))
        self.assertEqual(result["Pre-Kindergarten"], (3, 4, 75))

    def _panel(self, role, username):
        user = User.objects.create_user(username=username, email=f"{username}@x.test", password="x", role=role)
        self.client.force_login(user)
        resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 200)
        return {row["name"]: row["pct"] for row in resp.context["class_attendance"]}

    def test_primary_hod_panel(self):
        self.assertEqual(self._panel(UserRole.PRIMARY_HOD, "phod"), {"Grade 1": 100})

    def test_ecd_hod_panel(self):
        self.assertEqual(self._panel(UserRole.ECD_HOD, "ehod"), {"Pre-Kindergarten": 75})
