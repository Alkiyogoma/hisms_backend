"""
RBAC tests for student views — permission-based, not role-hardcoded.
Access is determined by Django permissions configured via the Role Management UI.
"""
from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from django.contrib.auth.models import Permission

from users.models import User, UserRole
from students.models import Student
from students.views import StudentListView, StudentDetailView, StudentEditView


def _create_user(username, role=UserRole.TEACHER, password="Pass123!"):
    return User.objects.create_user(
        username=username, email=f"{username}@hodari.edu",
        password=password, role=role,
    )


def _grant_perm(user, perm_codename):
    """Grant a specific permission to a user."""
    perm = Permission.objects.get(codename=perm_codename)
    user.user_permissions.add(perm)


def _remove_perm(user, perm_codename):
    """Remove a specific permission from a user."""
    perm = Permission.objects.get(codename=perm_codename)
    user.user_permissions.remove(perm)


class StudentListViewPermissionTests(TestCase):
    """Access to student list is controlled by students.view_student permission."""

    def setUp(self):
        self.factory = RequestFactory()
        self.teacher = _create_user("teacher1", UserRole.TEACHER)
        self.finance = _create_user("fin1", UserRole.FINANCE_OFFICER)
        self.admin = _create_user("admin1", UserRole.ADMIN_OFFICER)
        self.hos = _create_user("hos1", UserRole.HEAD_OF_SCHOOL)
        self.super_admin = _create_user("super1", UserRole.SUPER_ADMIN)

    def _get_response(self, user):
        request = self.factory.get("/students/")
        request.user = user
        view = StudentListView.as_view()
        try:
            response = view(request)
            return response
        except PermissionDenied:
            return None

    def test_super_admin_always_passes(self):
        result = self._get_response(self.super_admin)
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 200)

    def test_user_with_view_student_perm_passes(self):
        _grant_perm(self.teacher, "view_student")
        result = self._get_response(self.teacher)
        self.assertIsNotNone(result, "User with view_student permission should access list")
        self.assertEqual(result.status_code, 200)

    def test_user_without_view_student_perm_denied(self):
        _remove_perm(self.finance, "view_student")
        result = self._get_response(self.finance)
        self.assertIsNone(result, "User without view_student permission should be denied")

    def test_permission_toggle_revokes_access(self):
        _grant_perm(self.admin, "view_student")
        result = self._get_response(self.admin)
        self.assertIsNotNone(result)

        _remove_perm(self.admin, "view_student")
        result = self._get_response(self.admin)
        self.assertIsNone(result, "Removing permission should revoke access")

    def test_admin_officer_has_default_perm(self):
        """Admin Officer should have view_student by default from ROLE_DEFAULT_PERMISSIONS."""
        result = self._get_response(self.admin)
        self.assertIsNotNone(result, "Admin Officer should have default view_student permission")

    def test_hos_has_default_perm(self):
        """HOS should have view_student by default."""
        result = self._get_response(self.hos)
        self.assertIsNotNone(result, "HOS should have default view_student permission")

    def test_finance_has_default_perm(self):
        """Finance Officer should have view_student by default."""
        result = self._get_response(self.finance)
        self.assertIsNotNone(result, "Finance Officer should have default view_student permission")


class StudentDataExportTests(TestCase):
    """PDPA export: every section must query real model fields. The view swallows
    FieldErrors per section, so a bad field name silently exports an empty list."""

    def test_export_includes_welfare_discipline_and_academic_records(self):
        import json
        from datetime import date
        from academics.models import AcademicYear, ExamScore, ReportCard, Term
        from discipline.models import DisciplineIncident
        from welfare.models import WelfareObservation

        admin = User.objects.create_superuser(username="dpo", email="dpo@hodari.edu", password="Pass123!",
                                              role=UserRole.SUPER_ADMIN)
        student = Student.objects.create(admission_no="HCS-X-001", first_name="Asha", last_name="Mushi",
                                         gender="female", class_name="Grade 5")
        term = Term.objects.create(academic_year=AcademicYear.objects.create(name="2026", is_current=True),
                                   name="Term 1", start_date=date(2026, 1, 10), end_date=date(2026, 4, 5))
        WelfareObservation.objects.create(student=student, submitted_by=admin, note_type="concern",
                                          concern_type="health", severity="low",
                                          observation_date=date(2026, 2, 1), observation_text="Frequent headaches")
        DisciplineIncident.objects.create(student=student, reported_by=admin, severity="low",
                                          summary="Late to class", incident_date=date(2026, 2, 2))
        ExamScore.objects.create(student=student, term=term, subject_name="Mathematics", exam_type="midterm", score=78,
                                 entered_by=admin)
        ReportCard.objects.create(student=student, term=term, generated_by=admin)

        self.client.force_login(admin)
        resp = self.client.get(reverse("students:data_export", args=[student.pk]))
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual([w["observation_text"] for w in data["welfare_observations"]], ["Frequent headaches"])
        self.assertEqual([d["summary"] for d in data["discipline_incidents"]], ["Late to class"])
        self.assertEqual([e["subject_name"] for e in data["exam_scores"]], ["Mathematics"])
        self.assertEqual(len(data["report_cards"]), 1)
