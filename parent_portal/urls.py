"""Parent portal URL patterns."""
from django.urls import path
from . import views

app_name = "parent_portal"

urlpatterns = [
    # v1: Home, learner progress (with published report cards) and the school calendar.
    path("", views.ParentDashboardView.as_view(), name="dashboard"),
    path("learner/<int:student_id>/", views.ParentLearnerProgressView.as_view(), name="progress"),
    path("learner/<int:student_id>/photo/", views.ParentLearnerPhotoView.as_view(), name="learner_photo"),
    path("learner/<int:student_id>/reports/<int:report_id>/", views.ParentReportCardView.as_view(),
         name="report_card"),
    path("learner/<int:student_id>/reports/<int:report_id>/download/",
         views.ParentReportCardDownloadView.as_view(), name="report_download"),
    path("calendar/", views.ParentCalendarView.as_view(), name="calendar"),

    # Earlier portal pages redirect to v1 (welfare, finance and messages are not in v1).
    path("children/", views.LegacyRedirectView.as_view(), name="children_list"),
    path("attendance/", views.LegacyRedirectView.as_view(), name="attendance"),
    path("grades/", views.LegacyRedirectView.as_view(), name="grades"),
    path("invoices/", views.LegacyRedirectView.as_view(), name="invoices"),
    path("communications/", views.LegacyRedirectView.as_view(), name="communications"),
    path("welfare/", views.LegacyRedirectView.as_view(), name="welfare"),
    path("child/<int:student_id>/", views.LegacyRedirectView.as_view(), name="child_detail"),
    path("child/<int:student_id>/overview/", views.LegacyRedirectView.as_view(), name="child_overview"),

    path("child/<int:student_id>/guardians/", views.ParentGuardianListView.as_view(), name="guardian_list"),
    path("child/<int:student_id>/guardians/link/", views.ParentGuardianLinkView.as_view(), name="guardian_link"),
    path("child/<int:student_id>/guardians/<int:sg_pk>/unlink/", views.ParentGuardianUnlinkView.as_view(), name="guardian_unlink"),
    path("admission-form/", views.ParentAdmissionFormView.as_view(), name="admission_form"),
    path("admission-form/generate-invoice/", views.ParentGenerateInvoiceView.as_view(), name="generate_invoice"),
]
