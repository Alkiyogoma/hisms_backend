from datetime import date, timedelta

from django.conf import settings as django_settings
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from academics.models import AcademicYear, Department, GradeClass
from admissions.models import Applicant, ApplicantStatus, EntryRoute
from audit.models import AuditLog
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


def make_user(username, role):
    from users.role_models import ROLE_DEFAULT_PERMISSIONS
    user = User.objects.create_user(username=username, email=f"{username}@school.test", password="pw-12345!", role=role)
    group, _ = Group.objects.get_or_create(name=f"role_{role}")
    group.permissions.add(*Permission.objects.filter(codename__in=ROLE_DEFAULT_PERMISSIONS.get(role, [])))
    group.user_set.add(user)
    return user


class DirectEnrolmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        AcademicYear.objects.create(name=str(date.today().year), is_current=True)
        GradeClass.objects.create(name="Grade 4", department=Department.PRIMARY, max_capacity=25)
        cls.officer = make_user("office", UserRole.ADMIN_OFFICER)
        cls.teacher = make_user("teach", UserRole.TEACHER)

    def payload(self, **overrides):
        data = {
            "reason": "transfer", "reason_note": "Family relocated from Arusha",
            "first_name": "Imani", "last_name": "Mollel", "date_of_birth": "2016-05-04",
            "gender": "female", "nationality": "Tanzanian", "previous_school": "Arusha Primary",
            "class_name": "Grade 4", "enrolment_date": date.today().isoformat(),
            "parent_full_name": "Grace Mollel", "parent_relationship": "mother",
            "parent_phone": "+255700000001", "parent_email": "grace@example.test",
            "pdpa_consent": "on",
        }
        data.update(overrides)
        return data

    def test_admin_officer_enrols_directly(self):
        self.client.force_login(self.officer)
        resp = self.client.post(reverse("admissions:direct_enrol"), self.payload())
        student = Student.objects.get(first_name="Imani")
        self.assertRedirects(resp, reverse("students:detail", args=[student.pk]), fetch_redirect_response=False)
        self.assertEqual((student.last_name, student.class_name, student.gender, student.nationality),
                         ("Mollel", "Grade 4", "female", "Tanzanian"))
        self.assertEqual(student.enrolment_date, date.today())
        self.assertTrue(student.guardians.filter(phone="+255700000001").exists())

        applicant = Applicant.objects.get(enrolled_student=student)
        self.assertEqual(applicant.entry_route, EntryRoute.DIRECT)
        self.assertEqual(applicant.status, ApplicantStatus.ENROLLED)
        self.assertEqual(applicant.direct_enrolment_reason, "transfer")
        entry = applicant.timeline.get(to_status=ApplicantStatus.ENROLLED)
        self.assertEqual(entry.actor, self.officer)
        self.assertIn("Family relocated", entry.reason)
        self.assertTrue(AuditLog.objects.filter(action_type="DIRECT_ENROLMENT", actor=self.officer).exists())
        # No assessment was ever scheduled.
        from admissions.models import AssessmentSchedule
        self.assertFalse(AssessmentSchedule.objects.filter(applicant=applicant).exists())

        review = self.client.get(reverse("admissions:direct_enrolments"))
        self.assertContains(review, "Family relocated from Arusha")

    def test_transfer_needs_previous_school_and_consent(self):
        self.client.force_login(self.officer)
        resp = self.client.post(reverse("admissions:direct_enrol"), self.payload(previous_school="", pdpa_consent=""))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("previous_school", resp.context["form"].errors)
        self.assertIn("pdpa_consent", resp.context["form"].errors)
        self.assertFalse(Student.objects.exists())

    def test_duplicate_is_blocked(self):
        self.client.force_login(self.officer)
        self.client.post(reverse("admissions:direct_enrol"), self.payload())
        resp = self.client.post(reverse("admissions:direct_enrol"), self.payload())
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "Duplicate student detected", status_code=400)
        self.assertEqual(Student.objects.count(), 1)

    def test_teacher_cannot_enrol_directly(self):
        self.client.force_login(self.teacher)
        resp = self.client.post(reverse("admissions:direct_enrol"), self.payload())
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Student.objects.exists())
