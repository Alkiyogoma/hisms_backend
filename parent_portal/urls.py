"""Parent portal URL patterns."""
from django.urls import path
from . import views

app_name = "parent_portal"

urlpatterns = [
    path("", views.ParentDashboardView.as_view(), name="dashboard"),
    path("children/", views.ParentChildrenListView.as_view(), name="children_list"),
    path("attendance/", views.ParentAttendanceView.as_view(), name="attendance"),
    path("grades/", views.ParentGradesView.as_view(), name="grades"),
    path("invoices/", views.ParentInvoicesView.as_view(), name="invoices"),
    path("communications/", views.ParentCommunicationsView.as_view(), name="communications"),
    path("calendar/", views.ParentCalendarView.as_view(), name="calendar"),
    path("child/<int:student_id>/", views.ParentStudentDetailView.as_view(), name="child_detail"),
    path("child/<int:student_id>/overview/", views.ParentChildDetailView.as_view(), name="child_overview"),
    path("child/<int:student_id>/guardians/", views.ParentGuardianListView.as_view(), name="guardian_list"),
    path("child/<int:student_id>/guardians/link/", views.ParentGuardianLinkView.as_view(), name="guardian_link"),
    path("child/<int:student_id>/guardians/<int:sg_pk>/unlink/", views.ParentGuardianUnlinkView.as_view(), name="guardian_unlink"),
    path("welfare/", views.ParentWelfareView.as_view(), name="welfare"),
    path("admission-form/", views.ParentAdmissionFormView.as_view(), name="admission_form"),
    path("admission-form/generate-invoice/", views.ParentGenerateInvoiceView.as_view(), name="generate_invoice"),
]
