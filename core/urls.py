from django.urls import path
from core.views import (
    DashboardRouterView, SchoolSettingsUpdateView,
    SeedDatabaseView, SessionCheckView, SessionExtendView,
    BulkImportView, BulkImportTemplateView, BulkImportUploadView, BulkImportConfirmView,
    StyleGuideView,
)

app_name = "core"

urlpatterns = [
    path("", DashboardRouterView.as_view(), name="dashboard"),
    path("styleguide/", StyleGuideView.as_view(), name="styleguide"),
    path("settings/", SchoolSettingsUpdateView.as_view(), name="school_settings"),
    path("settings/bulk-import/", BulkImportView.as_view(), name="bulk_import"),
    path("settings/bulk-import/template/", BulkImportTemplateView.as_view(), name="bulk_import_template"),
    path("settings/bulk-import/upload/", BulkImportUploadView.as_view(), name="bulk_import_upload"),
    path("settings/bulk-import/confirm/", BulkImportConfirmView.as_view(), name="bulk_import_confirm"),
    path("seed-db/", SeedDatabaseView.as_view(), name="seed_db"),
    path("api/session/check/", SessionCheckView.as_view(), name="session_check"),
    path("api/session/extend/", SessionExtendView.as_view(), name="session_extend"),
]
