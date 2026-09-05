"""
RBAC tests for staff detail and payslip views after the UAT permissions audit fixes.
"""
from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from django.contrib.auth.models import AnonymousUser

from users.models import User, UserRole
from hr.models import StaffProfile, PayrollEntry, PayrollRun, PayslipStatus, PayrollStatus
from hr.views import StaffDetailView, PayrollPayslipView
from academics.models import Department


def _create_user(username, role=UserRole.TEACHER, password="Pass123!"):
    return User.objects.create_user(
        username=username, email=f"{username}@hodari.edu",
        password=password, role=role,
    )


def _set_staff_department(user, department):
    """Update auto-created StaffProfile department (signal creates one on user creation)."""
    sp = user.staff_profile
    sp.department = department
    sp.save(update_fields=["department"])
    return sp


class StaffDetailViewRBACTests(TestCase):
    """StaffDetailView: TEACHER/PARENT blocked, HODs dept-scoped."""

    def setUp(self):
        self.factory = RequestFactory()

        self.super_admin = _create_user("super", UserRole.SUPER_ADMIN)
        self.hos = _create_user("hos", UserRole.HEAD_OF_SCHOOL)
        self.primary_hod = _create_user("phod", UserRole.PRIMARY_HOD)
        self.ecd_hod = _create_user("ehod", UserRole.ECD_HOD)
        self.admin = _create_user("admin", UserRole.ADMIN_OFFICER)
        self.finance = _create_user("finance", UserRole.FINANCE_OFFICER)
        self.teacher = _create_user("teacher", UserRole.TEACHER)
        self.parent = _create_user("parent", UserRole.PARENT)

        self.staff_primary = _set_staff_department(self.teacher, Department.PRIMARY)
        self.staff_ecd = _set_staff_department(
            _create_user("ecd_teacher"), Department.ECD)

    def _get_response(self, user, staff_pk):
        request = self.factory.get(f"/hr/staff/{staff_pk}/")
        request.user = user
        view = StaffDetailView.as_view()
        try:
            response = view(request, pk=staff_pk)
            return response
        except PermissionDenied:
            return None

    def test_teacher_blocked_from_staff_detail(self):
        result = self._get_response(self.teacher, self.staff_primary.pk)
        self.assertIsNone(result, "TEACHER should be denied StaffDetailView")

    def test_parent_blocked_from_staff_detail(self):
        result = self._get_response(self.parent, self.staff_primary.pk)
        self.assertIsNone(result, "PARENT should be denied StaffDetailView")

    def test_super_admin_sees_any_staff(self):
        result = self._get_response(self.super_admin, self.staff_primary.pk)
        self.assertIsNotNone(result, "SUPER_ADMIN should access StaffDetailView")
        self.assertEqual(result.status_code, 200)

    def test_hos_sees_any_staff(self):
        result = self._get_response(self.hos, self.staff_primary.pk)
        self.assertIsNotNone(result, "HOS should access StaffDetailView")
        self.assertEqual(result.status_code, 200)

    def test_admin_officer_sees_any_staff(self):
        result = self._get_response(self.admin, self.staff_primary.pk)
        self.assertIsNotNone(result, "ADMIN_OFFICER should access StaffDetailView")
        self.assertEqual(result.status_code, 200)

    def test_finance_officer_sees_any_staff(self):
        result = self._get_response(self.finance, self.staff_primary.pk)
        self.assertIsNotNone(result, "FINANCE_OFFICER should access StaffDetailView")
        self.assertEqual(result.status_code, 200)

    def test_primary_hod_sees_primary_staff(self):
        result = self._get_response(self.primary_hod, self.staff_primary.pk)
        self.assertIsNotNone(result, "PRIMARY_HOD should access PRIMARY staff")
        self.assertEqual(result.status_code, 200)

    def test_primary_hod_blocked_from_ecd_staff(self):
        result = self._get_response(self.primary_hod, self.staff_ecd.pk)
        self.assertIsNone(result, "PRIMARY_HOD should be denied ECD staff")

    def test_ecd_hod_blocked_from_primary_staff(self):
        result = self._get_response(self.ecd_hod, self.staff_primary.pk)
        self.assertIsNone(result, "ECD_HOD should be denied PRIMARY staff")

    def test_ecd_hod_sees_ecd_staff(self):
        result = self._get_response(self.ecd_hod, self.staff_ecd.pk)
        self.assertIsNotNone(result, "ECD_HOD should access ECD staff")
        self.assertEqual(result.status_code, 200)


class PayrollPayslipViewRBACTests(TestCase):
    """PayrollPayslipView: TEACHER self-access only, cross-staff blocked."""

    def setUp(self):
        self.factory = RequestFactory()

        self.teacher_a = _create_user("teach_a", UserRole.TEACHER)
        self.teacher_b = _create_user("teach_b", UserRole.TEACHER)
        self.finance = _create_user("finance", UserRole.FINANCE_OFFICER)
        self.hos = _create_user("hos", UserRole.HEAD_OF_SCHOOL)
        self.super_admin = _create_user("super", UserRole.SUPER_ADMIN)

        self.staff_a = _set_staff_department(self.teacher_a, Department.PRIMARY)
        self.staff_b = _set_staff_department(self.teacher_b, Department.PRIMARY)

        self.payroll_run = PayrollRun.objects.create(
            period_name="Test Run",
            period_start="2026-01-01",
            period_end="2026-01-31",
            status=PayrollStatus.APPROVED,
        )

        self.entry_a = PayrollEntry.objects.create(
            payroll_run=self.payroll_run,
            staff=self.staff_a,
            basic_pay=1000000,
            gross_pay=1000000,
            total_deductions=100000,
            net_pay=900000,
        )
        self.entry_b = PayrollEntry.objects.create(
            payroll_run=self.payroll_run,
            staff=self.staff_b,
            basic_pay=2000000,
            gross_pay=2000000,
            total_deductions=200000,
            net_pay=1800000,
        )

    def _get_response(self, user, entry_pk):
        request = self.factory.get(f"/hr/payroll/entry/{entry_pk}/")
        request.user = user
        view = PayrollPayslipView.as_view()
        try:
            response = view(request, pk=entry_pk)
            return response
        except PermissionDenied:
            return None

    def test_teacher_sees_own_payslip(self):
        result = self._get_response(self.teacher_a, self.entry_a.pk)
        self.assertIsNotNone(result, "TEACHER should see own payslip")
        self.assertEqual(result.status_code, 200)

    def test_teacher_blocked_from_other_payslip(self):
        result = self._get_response(self.teacher_a, self.entry_b.pk)
        self.assertIsNone(result, "TEACHER should be denied another staff's payslip")

    def test_finance_sees_any_payslip(self):
        result = self._get_response(self.finance, self.entry_b.pk)
        self.assertIsNotNone(result, "FINANCE_OFFICER should see any payslip")
        self.assertEqual(result.status_code, 200)

    def test_hos_sees_any_payslip(self):
        result = self._get_response(self.hos, self.entry_b.pk)
        self.assertIsNotNone(result, "HOS should see any payslip")
        self.assertEqual(result.status_code, 200)

    def test_super_admin_sees_any_payslip(self):
        result = self._get_response(self.super_admin, self.entry_b.pk)
        self.assertIsNotNone(result, "SUPER_ADMIN should see any payslip")
        self.assertEqual(result.status_code, 200)

    def test_teacher_b_sees_own_payslip(self):
        result = self._get_response(self.teacher_b, self.entry_b.pk)
        self.assertIsNotNone(result, "TEACHER B should see own payslip")
        self.assertEqual(result.status_code, 200)
