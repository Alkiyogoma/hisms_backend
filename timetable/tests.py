import json
from datetime import date, time, timedelta

from django.conf import settings as django_settings
from django.test import TestCase, Client
from django.urls import reverse

from academics.models import AcademicYear, GradeClass, Term
from hr.models import StaffProfile, TeacherClassAssignment
from timetable.models import TimetableSlot, Weekday
from users.models import User, UserRole

# SQLite FK-check workaround (same as academics/tests.py): the audit FK constraint
# cannot be reflected on SQLite, so suppress the false positive IntegrityError
# thrown by TestCase._fixture_teardown.
if not getattr(django_settings, "AUDIT_LOG_DB_CONSTRAINT", True):
    _orig_fixture_teardown = TestCase._fixture_teardown

    def _patched_fixture_teardown(self):
        try:
            _orig_fixture_teardown(self)
        except Exception:
            pass

    TestCase._fixture_teardown = _patched_fixture_teardown


class TeacherTimetableVisibilityTests(TestCase):
    """Teachers must be able to see their own timetable as well as other
    teachers' timetables (filter by teacher works for TEACHER role)."""

    def setUp(self):
        from django.core.management import call_command
        call_command("seed_roles", "--reset", verbosity=0)

        today = date.today()
        self.year = AcademicYear.objects.create(
            name="2026",
            is_current=True,
            start_date=today - timedelta(days=180),
            end_date=today + timedelta(days=180),
        )
        self.term = Term.objects.create(
            academic_year=self.year,
            name="Term 1",
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=60),
            is_locked=False,
        )

        self.grade_one = GradeClass.objects.create(name="Grade 1", department="PRIMARY")
        self.grade_two = GradeClass.objects.create(name="Grade 2", department="PRIMARY")

        self.teacher = self._make_teacher("teacher.one", "Alice", "Teacher", self.grade_one)
        self.other_teacher = self._make_teacher("teacher.two", "Bob", "Teacher", self.grade_two)
        self.admin = User.objects.create_superuser(
            username="admin.timetable",
            email="admin@example.com",
            password="TestPass123!",
            role=UserRole.SUPER_ADMIN,
        )

        self.teacher_slot = TimetableSlot.objects.create(
            term=self.term,
            class_name="Grade 1",
            subject_name="Mathematics",
            teacher=self.teacher,
            day_of_week=Weekday.MON,
            start_time=time(8, 0),
            end_time=time(8, 40),
            is_published=True,
        )
        self.other_slot = TimetableSlot.objects.create(
            term=self.term,
            class_name="Grade 2",
            subject_name="English",
            teacher=self.other_teacher,
            day_of_week=Weekday.TUE,
            start_time=time(9, 0),
            end_time=time(9, 40),
            is_published=True,
        )

    def _make_teacher(self, username, first, last, grade_class):
        from django.contrib.auth.models import Group
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="TestPass123!",
            role=UserRole.TEACHER,
            first_name=first,
            last_name=last,
        )
        user.groups.add(Group.objects.get(name="role_teacher"))

        staff = StaffProfile.objects.create(
            user=user,
            full_name=f"{first} {last}",
            job_title="Teacher",
            department="PRIMARY",
            departments=["PRIMARY"],
            employment_start_date=date(2024, 1, 1),
        )
        TeacherClassAssignment.objects.create(
            teacher=staff,
            term=self.term,
            grade_class=grade_class,
            subjects_taught=["Mathematics"] if grade_class == self.grade_one else ["English"],
        )
        return user

    def _client(self, user):
        client = Client()
        client.force_login(user)
        return client

    def _slot_pks(self, response):
        return {s.pk for s in response.context["slots"]}

    def test_teacher_defaults_to_own_schedule(self):
        resp = self._client(self.teacher).get(reverse("timetable:index"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._slot_pks(resp), {self.teacher_slot.pk})

    def test_teacher_can_view_own_schedule_via_filter(self):
        resp = self._client(self.teacher).get(
            reverse("timetable:index"), {"teacher_id": str(self.teacher.pk)}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._slot_pks(resp), {self.teacher_slot.pk})

    def test_teacher_can_view_other_teachers_schedule(self):
        resp = self._client(self.teacher).get(
            reverse("timetable:index"), {"teacher_id": str(self.other_teacher.pk)}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._slot_pks(resp), {self.other_slot.pk})

    def test_teacher_can_view_all_teachers_schedule(self):
        resp = self._client(self.teacher).get(
            reverse("timetable:index"), {"teacher_id": ""}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._slot_pks(resp), {self.teacher_slot.pk, self.other_slot.pk})

    def test_admin_sees_all_teachers_by_default(self):
        resp = self._client(self.admin).get(reverse("timetable:index"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._slot_pks(resp), {self.teacher_slot.pk, self.other_slot.pk})

    def test_teacher_dropdown_lists_all_teachers(self):
        resp = self._client(self.teacher).get(reverse("timetable:index"))
        self.assertEqual(resp.status_code, 200)
        names = [t.get_full_name() for t in resp.context["teachers"]]
        self.assertIn(self.teacher.get_full_name(), names)
        self.assertIn(self.other_teacher.get_full_name(), names)

    def test_teacher_dropdown_renders_all_teachers_option(self):
        resp = self._client(self.teacher).get(reverse("timetable:index"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertIn('name="teacher_id"', html)
        self.assertIn(">All Teachers<", html)

    def test_unpublished_slots_hidden_from_teachers(self):
        from timetable.models import TimetableSlot as TSlot
        unpublished = TSlot.objects.create(
            term=self.term,
            class_name="Grade 1",
            subject_name="Science",
            teacher=self.teacher,
            day_of_week=Weekday.WED,
            start_time=time(11, 0),
            end_time=time(11, 40),
            is_published=False,
        )
        resp = self._client(self.teacher).get(
            reverse("timetable:index"), {"teacher_id": ""}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(unpublished.pk, self._slot_pks(resp))
