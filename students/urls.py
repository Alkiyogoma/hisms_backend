from django.urls import path
from . import views

app_name = "students"

urlpatterns = [
    path("", views.StudentListView.as_view(), name="list"),
    path("<int:pk>/", views.StudentDetailView.as_view(), name="detail"),
    path("add/", views.StudentCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", views.StudentEditView.as_view(), name="edit"),
    path("<int:pk>/archive/", views.StudentArchiveView.as_view(), name="archive"),
    path("<int:pk>/restore/", views.StudentRestoreView.as_view(), name="restore"),
    path("<int:pk>/upload-photo/", views.StudentPhotoUploadView.as_view(), name="upload_photo"),
    # Parent/Guardian
    path("guardians/", views.GuardianListView.as_view(), name="guardian_list"),
    path("guardians/<int:pk>/", views.GuardianDetailView.as_view(), name="guardian_detail"),
    path("guardians/add/", views.GuardianCreateView.as_view(), name="guardian_create"),
    path("guardians/<int:pk>/edit/", views.GuardianEditView.as_view(), name="guardian_edit"),
    path("guardians/<int:pk>/delete/", views.GuardianDeleteView.as_view(), name="guardian_delete"),
    path("guardians/<int:pk>/consent-withdraw/", views.GuardianConsentWithdrawView.as_view(), name="guardian_consent_withdraw"),
    path("<int:pk>/guardians/link/", views.StudentGuardianLinkView.as_view(), name="guardian_link"),
    path("api/search/", views.StudentSearchAPIView.as_view(), name="api_search"),
    path("api/guardians/search/", views.GuardianSearchView.as_view(), name="api_guardian_search"),
    # PDPA data export
    path("export/<int:pk>/", views.StudentDataExportView.as_view(), name="data_export"),
    # ID Generation
    path("<int:pk>/generate-id/", views.GenerateStudentIDView.as_view(), name="generate_id"),
    path("bulk-generate-ids/", views.BulkGenerateStudentIDsView.as_view(), name="bulk_generate_ids"),
    path("<int:pk>/print-id/", views.PrintStudentIDView.as_view(), name="print_id"),
    path("<int:pk>/leaving-certificate/", views.PrintLeavingCertificateView.as_view(), name="leaving_certificate"),
    path("<int:pk>/delete/", views.StudentDeleteView.as_view(), name="delete"),
]
