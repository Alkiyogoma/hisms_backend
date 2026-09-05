from django.urls import path
from . import views

app_name = "hr"

urlpatterns = [
    # Staff Management
    path("", views.HRDashboardView.as_view(), name="dashboard"),
    path("staff/", views.StaffListView.as_view(), name="staff_list"),
    path("staff/new/", views.StaffCreateView.as_view(), name="staff_create"),
    path("staff/<int:pk>/", views.StaffDetailView.as_view(), name="staff_detail"),
    path("staff/<int:pk>/print-id/", views.PrintStaffIDCardView.as_view(), name="staff_print_id"),
    path("staff/<int:pk>/edit/", views.StaffEditView.as_view(), name="staff_edit"),
    path("staff/<int:pk>/departure/", views.StaffDepartureView.as_view(), name="staff_departure"),
    path("staff/<int:pk>/document/upload/", views.StaffDocumentUploadView.as_view(), name="staff_document_upload"),

    # Teacher Assignments
    path("assignments/<int:pk>/edit/", views.TeacherAssignmentEditView.as_view(), name="teacher_assignment_edit"),
    path("assignments/<int:pk>/delete/", views.TeacherAssignmentDeleteView.as_view(), name="teacher_assignment_delete"),
    path("document/<int:document_id>/download/", views.StaffDocumentDownloadView.as_view(), name="staff_document_download"),
    path("document/<int:document_id>/delete/", views.StaffDocumentDeleteView.as_view(), name="staff_document_delete"),
    path("document/duplicate-check/", views.StaffDocumentDuplicateCheckView.as_view(), name="staff_document_duplicate_check"),

    # B3 — Onboarding Workflow (10 steps)
    path("onboarding/<int:pk>/start/", views.OnboardingSetupView.as_view(), name="onboarding_start"),
    path("onboarding/<int:pk>/", views.OnboardingTabbedView.as_view(), name="onboarding_tabbed"),
    path("onboarding/<int:pk>/step/<int:step>/", views.OnboardingStepView.as_view(), name="onboarding_step"),

    # B4 — Payroll Module
    path("payroll/", views.PayrollRunListView.as_view(), name="payroll_list"),
    path("payroll/new/", views.PayrollRunCreateView.as_view(), name="payroll_create"),
    path("payroll/<int:pk>/", views.PayrollRunDetailView.as_view(), name="payroll_detail"),
    path("payroll/<int:pk>/auto-populate/", views.PayrollEntryAutoPopulateView.as_view(), name="payroll_auto_populate"),
    path("payroll/<int:pk>/submit/", views.PayrollSubmitView.as_view(), name="payroll_submit"),
    path("payroll/<int:pk>/approve/", views.PayrollApproveView.as_view(), name="payroll_approve"),
    path("payroll/<int:pk>/lock/", views.PayrollLockView.as_view(), name="payroll_lock"),
    path("payroll/entries/<int:pk>/edit/", views.PayrollEntryEditView.as_view(), name="payroll_entry_edit"),
    path("payroll/entries/<int:pk>/payslip/", views.PayrollPayslipView.as_view(), name="payroll_payslip"),

    # B4b — Payroll Configuration & PAYE Tax Bands
    path("payroll-config/", views.PayrollConfigUpdateView.as_view(), name="payroll_config"),
    path("payroll-config/paye-bands/", views.PAYETaxBandListView.as_view(), name="paye_tax_band_list"),
    path("payroll-config/paye-bands/create/", views.PAYETaxBandCreateView.as_view(), name="paye_tax_band_create"),
    path("payroll-config/paye-bands/<int:pk>/delete/", views.PAYETaxBandDeleteView.as_view(), name="paye_tax_band_delete"),

    # B5 — Statutory Filing
    path("statutory/", views.StatutoryFilingListView.as_view(), name="statutory_filing_list"),
    path("statutory/generate/<int:pk>/", views.StatutoryFilingCreateView.as_view(), name="statutory_filing_generate"),
    path("statutory/<int:pk>/submit/", views.StatutoryFilingSubmitView.as_view(), name="statutory_filing_submit"),

    # B6 — Leave Management
    path("leave/", views.LeaveRequestListView.as_view(), name="leave_list"),
    path("leave/new/", views.LeaveRequestCreateView.as_view(), name="leave_create"),
    path("leave/<int:pk>/approve/", views.LeaveRequestApproveView.as_view(), name="leave_approve"),

    # B7 — Offboarding
    path("offboarding/<int:pk>/", views.OffboardingDetailView.as_view(), name="offboarding_detail"),

    # Rollover conflicts
    path("rollover-conflicts/resolve/", views.RolloverConflictResolveView.as_view(), name="rollover_conflict_resolve"),

    # B8 — Dashboard data (JSON)
    path("widget-data/", views.FinanceDashboardWidgetDataView.as_view(), name="widget_data"),
]
