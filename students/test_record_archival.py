"""Visible, reversible archives retain history and enforce directory access."""
from datetime import timedelta

from django.contrib.auth.models import Permission
from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import AcademicYear, Department, GradeClass, ReportCard, Term
from academics.tests import assign_role_group
from audit.models import AuditLog
from hr.models import StaffProfile
from students.models import ParentGuardian, Student, StudentGuardian
from users.models import User, UserRole


class RecordArchivalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def account(name, role):
            user = User.objects.create_user(name, email=f"{name}@example.test", password="x", role=role)
            assign_role_group(user)
            return user

        cls.admin = account("archive-admin", UserRole.SUPER_ADMIN)
        cls.staff_user = account("archive-staff", UserRole.TEACHER)
        cls.parent_user = account("archive-parent", UserRole.PARENT)
        cls.hod = account("archive-hod", UserRole.PRIMARY_HOD)
        cls.staff = StaffProfile.objects.create(
            user=cls.staff_user, full_name="Archive Staff", department=Department.LOWER_SECONDARY,
            employment_start_date=timezone.localdate() - timedelta(days=365),
        )
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term = Term.objects.create(academic_year=cls.year, name="Term 1")
        cls.grade = GradeClass.objects.create(name="Grade 7", department=Department.LOWER_SECONDARY)
        cls.student = Student.objects.create(
            admission_no="ARCHIVE-001", first_name="Archive", last_name="Learner",
            class_name=cls.grade.name, academic_year=cls.year,
        )
        cls.guardian = ParentGuardian.objects.create(
            user=cls.parent_user, full_name="Archive Parent", phone="0712345678", email=cls.parent_user.email,
        )
        StudentGuardian.objects.create(student=cls.student, guardian=cls.guardian, relationship="parent", is_primary=True)
        cls.report = ReportCard.objects.create(student=cls.student, term=cls.term, generated_by=cls.admin)

    def setUp(self):
        self.client.force_login(self.admin)

    def test_student_archive_directory_and_restore_keep_report_history(self):
        response = self.client.get(reverse("students:list"))
        self.assertContains(response, reverse("students:archive", args=[self.student.pk]))
        self.assertEqual(self.client.post(reverse("students:archive", args=[self.student.pk])).status_code, 302)
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_archived)
        self.assertNotIn(self.student, self.client.get(reverse("students:list")).context["students"])
        archived = self.client.get(reverse("students:list"), {"archive_status": "archived"})
        self.assertIn(self.student, archived.context["students"])
        self.assertContains(archived, reverse("students:restore", args=[self.student.pk]))
        self.client.post(reverse("students:restore", args=[self.student.pk]))
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_archived)
        self.assertEqual(self.student.status, "active")
        self.assertTrue(ReportCard.objects.filter(pk=self.report.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action_type="RESTORE", object_id=str(self.student.pk)).exists())

    def test_parent_archive_and_restore_keep_links_and_control_portal_access(self):
        parent_client = Client()
        parent_client.force_login(self.parent_user)
        session_key = parent_client.session.session_key
        self.client.post(reverse("students:guardian_archive", args=[self.guardian.pk]))
        self.guardian.refresh_from_db()
        self.parent_user.refresh_from_db()
        self.assertTrue(self.guardian.is_archived)
        self.assertFalse(self.parent_user.is_active)
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
        self.assertTrue(StudentGuardian.objects.filter(guardian=self.guardian, student=self.student).exists())
        self.assertNotIn(self.guardian, self.client.get(reverse("students:guardian_list")).context["guardians"])
        archived = self.client.get(reverse("students:guardian_list"), {"archive_status": "archived"})
        self.assertIn(self.guardian, archived.context["guardians"])
        self.assertContains(archived, reverse("students:guardian_restore", args=[self.guardian.pk]))
        self.assertEqual(self.client.post(reverse("students:guardian_restore", args=[self.guardian.pk])).status_code, 302)
        self.guardian.refresh_from_db()
        self.parent_user.refresh_from_db()
        self.assertFalse(self.guardian.is_archived)
        self.assertIsNone(self.guardian.archived_at)
        self.assertTrue(self.parent_user.is_active)

    def test_parent_without_portal_account_can_be_archived_and_restored(self):
        self.guardian.user = None
        self.guardian.save(update_fields=["user"])
        self.client.post(reverse("students:guardian_archive", args=[self.guardian.pk]))
        self.client.post(reverse("students:guardian_restore", args=[self.guardian.pk]))
        self.guardian.refresh_from_db()
        self.assertFalse(self.guardian.is_archived)

    def test_archived_parent_can_be_found_by_email(self):
        self.client.post(reverse("students:guardian_archive", args=[self.guardian.pk]))
        response = self.client.get(reverse("students:guardian_list"), {
            "archive_status": "archived", "q": self.guardian.email,
        })
        self.assertIn(self.guardian, response.context["guardians"])

    def test_archived_student_live_search_preserves_filter_and_restore_action(self):
        self.client.post(reverse("students:archive", args=[self.student.pk]))
        active = Student.objects.create(
            admission_no="ARCHIVE-ACTIVE", first_name="Archive", last_name="Active", class_name=self.grade.name,
        )
        response = self.client.get(reverse("students:api_search"), {"q": "Archive", "archive_status": "archived"})
        results = response.json()["students"]
        self.assertEqual([row["id"] for row in results], [self.student.pk])
        self.assertEqual(results[0]["archive_url"], reverse("students:restore", args=[self.student.pk]))
        self.assertNotIn(active.pk, [row["id"] for row in results])

    def test_student_restoration_still_enforces_class_capacity(self):
        self.client.post(reverse("students:archive", args=[self.student.pk]))
        self.grade.max_capacity = 1
        self.grade.save()
        Student.objects.create(admission_no="ARCHIVE-FULL", first_name="Current", last_name="Learner", class_name=self.grade.name)
        self.client.post(reverse("students:restore", args=[self.student.pk]))
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_archived)

    def test_archived_parent_pagination_keeps_archive_filter(self):
        for number in range(31):
            ParentGuardian.objects.create(full_name=f"Archived Parent {number}", phone=f"071234{number:04d}", is_archived=True)
        response = self.client.get(reverse("students:guardian_list"), {"archive_status": "archived"})
        self.assertTrue(response.context["page_obj"].has_next())
        self.assertContains(response, 'href="?archive_status=archived&amp;page=2"')

    def test_staff_archive_and_restore_synchronize_profile_and_account(self):
        staff_client = Client()
        staff_client.force_login(self.staff_user)
        session_key = staff_client.session.session_key
        response = self.client.post(reverse("users:archive_user", args=[self.staff_user.pk]), {"reason": "Left school"})
        self.assertEqual(response.status_code, 302)
        self.staff_user.refresh_from_db()
        self.staff.refresh_from_db()
        self.assertFalse(self.staff_user.is_active)
        self.assertFalse(self.staff.is_active)
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
        active = self.client.get(reverse("users:user_list"))
        self.assertNotIn(self.staff_user, active.context["users"])
        archived = self.client.get(reverse("users:user_list"), {"status": "inactive"})
        self.assertIn(self.staff_user, archived.context["users"])
        self.assertContains(archived, "Restore Staff")
        self.client.post(reverse("users:reactivate_user", args=[self.staff_user.pk]), {"reason": "Returned to school"})
        self.staff_user.refresh_from_db()
        self.staff.refresh_from_db()
        self.assertTrue(self.staff_user.is_active)
        self.assertTrue(self.staff.is_active)
        self.assertIsNone(self.staff_user.departure_date)
        self.assertIsNone(self.staff.departure_date)
        self.assertEqual(self.staff.departure_reason, "")
        self.assertTrue(AuditLog.objects.filter(action_type="REACTIVATION", object_id=str(self.staff_user.pk)).exists())

    def test_restore_repairs_an_active_account_with_an_inactive_profile(self):
        StaffProfile.objects.filter(pk=self.staff.pk).update(is_active=False, departure_date=timezone.localdate())
        archived = self.client.get(reverse("users:user_list"), {"status": "inactive"})
        self.assertIn(self.staff_user, archived.context["users"])
        self.client.post(reverse("users:reactivate_user", args=[self.staff_user.pk]), {"reason": "Restore profile"})
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active)
        self.assertIsNone(self.staff.departure_date)

    def test_staff_accounts_without_profiles_can_be_archived_and_restored(self):
        user = User.objects.create_user("no-profile", email="no-profile@example.test", password="x")
        self.client.post(reverse("users:archive_user", args=[user.pk]), {"reason": "No longer needed"})
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.client.post(reverse("users:reactivate_user", args=[user.pk]), {"reason": "Needed again"})
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_staff_changes_require_a_reason_and_cannot_archive_self(self):
        self.client.post(reverse("users:archive_user", args=[self.staff_user.pk]))
        self.staff_user.refresh_from_db()
        self.assertTrue(self.staff_user.is_active)
        self.client.post(reverse("users:archive_user", args=[self.admin.pk]), {"reason": "Self"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        User.objects.filter(pk=self.staff_user.pk).update(is_active=False)
        self.client.post(reverse("users:reactivate_user", args=[self.staff_user.pk]))
        self.staff_user.refresh_from_db()
        self.assertFalse(self.staff_user.is_active)

    def test_permission_checks_block_archive_and_restore(self):
        self.client.force_login(self.parent_user)
        for url in (
            reverse("students:archive", args=[self.student.pk]),
            reverse("students:restore", args=[self.student.pk]),
            reverse("students:guardian_archive", args=[self.guardian.pk]),
            reverse("students:guardian_restore", args=[self.guardian.pk]),
            reverse("users:archive_user", args=[self.staff_user.pk]),
            reverse("users:reactivate_user", args=[self.staff_user.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {"reason": "Denied"}).status_code, 403)

    def test_section_head_cannot_archive_another_departments_records(self):
        self.hod.user_permissions.add(*Permission.objects.filter(codename__in=[
            "change_student", "change_parentguardian", "change_user",
        ]))
        self.client.force_login(self.hod)
        self.assertEqual(self.client.post(reverse("students:archive", args=[self.student.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("students:guardian_archive", args=[self.guardian.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("users:archive_user", args=[self.staff_user.pk]), {"reason": "Denied"}).status_code, 403)

    def test_staff_routes_cannot_change_parent_account_state(self):
        for route in ("users:archive_user", "users:reactivate_user"):
            self.assertEqual(self.client.post(reverse(route, args=[self.parent_user.pk]), {"reason": "Wrong directory"}).status_code, 403)

    def test_archive_endpoints_require_post(self):
        for url in (
            reverse("students:archive", args=[self.student.pk]),
            reverse("students:restore", args=[self.student.pk]),
            reverse("students:guardian_archive", args=[self.guardian.pk]),
            reverse("students:guardian_restore", args=[self.guardian.pk]),
            reverse("users:archive_user", args=[self.staff_user.pk]),
            reverse("users:reactivate_user", args=[self.staff_user.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 405)
