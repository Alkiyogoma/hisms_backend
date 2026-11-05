"""Every parent on record can use the parent portal: there is no "Create
Portal Access" step. Parents sign in at /parent/login/ with their phone or
email and a one-time code; the login is created on first sign-in."""
from datetime import date
from unittest import mock

from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from academics.models import AcademicYear, Department, GradeClass
from students.models import ParentGuardian, Student, StudentGuardian, StudentStatus
from users.models import User, UserRole


def _sign_in_with_code(test, client, who):
    """Run the parent code sign-in; returns the signed-in user id or None."""
    sent = []
    with mock.patch("parent_portal.auth_views._deliver",
                    side_effect=lambda chan, addr, code: sent.append(code) or True):
        client.post(reverse("parent_portal:login"), {"action": "lookup", "who": who})
    test.assertTrue(sent, f"no code was sent for {who}")
    client.post(reverse("parent_portal:login"), {"action": "verify", "code": sent[-1]})
    uid = client.session.get("_auth_user_id")
    return int(uid) if uid else None


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", DEBUG=False)
class ParentPortalNeedsNoActivationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser(
            username="registrar", email="registrar@hodari.edu", password="Pass123!",
            role=UserRole.SUPER_ADMIN,
        )
        self.student = Student.objects.create(admission_no="PA-001", first_name="Neema", last_name="Kajeguka",
                                              class_name="Grade 7", status=StudentStatus.ACTIVE)
        self.guardian = ParentGuardian.objects.create(
            full_name="Mary Kajeguka", phone="+255767000001", email="mary@example.com",
        )
        StudentGuardian.objects.create(student=self.student, guardian=self.guardian, relationship="parent")

    def test_parent_page_has_no_account_details_or_portal_buttons(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("students:guardian_detail", args=[self.guardian.pk])).content.decode()
        for text in ("Account Details", "Create Portal Access", "Resend Login Details", "Portal Login"):
            self.assertNotIn(text, page)
        with self.assertRaises(NoReverseMatch):
            reverse("students:guardian_portal_access", args=[self.guardian.pk])

    def test_add_parent_form_has_no_portal_access_option(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("students:guardian_create")).content.decode()
        self.assertNotIn('name="portal_access"', page)

    def test_existing_parent_never_activated_can_sign_in(self):
        self.assertIsNone(self.guardian.user)
        uid = _sign_in_with_code(self, self.client, "0767 000 001")
        self.guardian.refresh_from_db()
        self.assertEqual(uid, self.guardian.user_id)
        self.assertEqual(self.guardian.user.role, UserRole.PARENT)
        self.assertTrue(self.guardian.user.groups.filter(name="role_parent").exists())

    def test_existing_parent_with_old_password_login_can_sign_in_by_email(self):
        old = User.objects.create_user(username="parent_255767000001", email="mary@example.com",
                                       password="Temp-Pass-1", role=UserRole.PARENT)
        old.must_change_password = True
        old.save()
        self.guardian.user = old
        self.guardian.save()
        self.assertEqual(_sign_in_with_code(self, self.client, "mary@example.com"), old.pk)
        old.refresh_from_db()
        self.assertFalse(old.must_change_password)
        self.assertEqual(User.objects.filter(role=UserRole.PARENT).count(), 1)

    def test_parent_whose_login_was_switched_off_can_sign_in(self):
        off = User.objects.create_user(username="mary", email="mary@example.com", password="x-Pass-123",
                                       role=UserRole.PARENT, is_active=False)
        self.guardian.user = off
        self.guardian.save()
        self.assertEqual(_sign_in_with_code(self, self.client, "+255767000001"), off.pk)

    def test_newly_added_parent_can_sign_in_straight_away(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("students:guardian_link", args=[self.student.pk]), {
            "mode": "new", "full_name": "John Kajeguka", "phone": "0767 000 002",
            "relationship": "parent", "preferred_language": "en",
        })
        john = ParentGuardian.objects.get(full_name="John Kajeguka")
        self.assertTrue(StudentGuardian.objects.filter(guardian=john, student=self.student).exists())
        self.client.logout()
        john_client = self.client_class()
        self.assertIsNotNone(_sign_in_with_code(self, john_client, "0767000002"))
        john.refresh_from_db()
        self.assertIsNotNone(john.user)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EnrolmentSendsSignInInstructionsTests(TestCase):
    def test_enrolled_parent_gets_code_sign_in_instructions_not_a_password(self):
        from users.role_models import ROLE_DEFAULT_PERMISSIONS
        AcademicYear.objects.create(name=str(date.today().year), is_current=True)
        GradeClass.objects.create(name="Grade 4", department=Department.PRIMARY, max_capacity=25)
        officer = User.objects.create_user(username="office", email="office@school.test", password="pw-12345!",
                                           role=UserRole.ADMIN_OFFICER)
        group, _ = Group.objects.get_or_create(name=f"role_{UserRole.ADMIN_OFFICER}")
        group.permissions.add(*Permission.objects.filter(
            codename__in=ROLE_DEFAULT_PERMISSIONS.get(UserRole.ADMIN_OFFICER, [])))
        group.user_set.add(officer)
        self.client.force_login(officer)
        self.client.post(reverse("admissions:direct_enrol"), {
            "reason": "transfer", "reason_note": "Family relocated from Arusha",
            "first_name": "Imani", "last_name": "Mollel", "date_of_birth": "2016-05-04",
            "gender": "female", "nationality": "Tanzanian", "previous_school": "Arusha Primary",
            "class_name": "Grade 4", "enrolment_date": date.today().isoformat(),
            "parent_full_name": "Grace Mollel", "parent_relationship": "mother",
            "parent_phone": "+255700000001", "parent_email": "grace@example.test",
            "pdpa_consent": "on",
        })
        grace = ParentGuardian.objects.get(full_name="Grace Mollel")
        self.assertIsNotNone(grace.user)
        self.assertFalse(grace.user.has_usable_password())
        self.assertFalse(grace.user.must_change_password)
        bodies = [m.body for m in mail.outbox if "grace@example.test" in m.to]
        self.assertTrue(any("/parent/login/" in b and "4-digit code" in b for b in bodies), bodies)
        self.assertFalse(any("Password:" in b or "/accounts/login/" in b for b in bodies), bodies)
