from django.urls import path
from audit.views import AuditLogsListView, PersonalDataExportView, CspReportView, CspReportsListView

app_name = "audit"

urlpatterns = [
    path("logs/", AuditLogsListView.as_view(), name="logs"),
    path("export/", PersonalDataExportView.as_view(), name="dsar_export"),
    path("csp-reports/", CspReportsListView.as_view(), name="csp_reports"),
    path("csp-report-collect/", CspReportView.as_view(), name="csp_report_collect"),
]
