from django.urls import path

from attendance.views import (
    AttendanceCorrectionView,
    AttendanceMarkView,
    AttendanceTodayView,
    ParentAttendanceView,
    StaffAttendanceView,
    CheckInCheckOutAPIView,
    QRCodeImageView,
    StudentQRCodeView,
    BlankAttendanceRegisterView,
)
from attendance.api_views import (
    send_otp,
    verify_otp,
    checkin_students,
    checkout_students,
    fetch_checklist,
    search_students,
    student_detail,
    qr_lookup,
    attendance_statistics,
    scans_today,
    scans_statistics,
    scan_history,
    submit_scans,
    mark_excused,
)
from attendance.reporting_api import (
    daily_attendance_report,
    monthly_attendance_report,
    classwise_attendance_report,
    parent_pickup_report,
    attendance_trend_analysis,
    regulatory_compliance_report,
    low_attendance_alerts,
    attendance_alerts_summary,
)
from attendance.webhook_views import (
    webhook_attendance_push,
    webhook_health,
)
from attendance.views_pwa import (
    PWADownloadView,
    PWAManifestView,
    PWAConfigView,
    PWAAttendanceShellView,
    ServiceWorkerView,
)

app_name = "attendance"

urlpatterns = [
    # Web views
    path("", AttendanceTodayView.as_view(), name="today"),
    
    # PWA
    path("pwa/sw.js", ServiceWorkerView.as_view(), name="pwa_sw"),
    path("pwa/", PWADownloadView.as_view(), name="pwa_download"),
    path("pwa/manifest.json", PWAManifestView.as_view(), name="pwa_manifest"),
    path("pwa/config/", PWAConfigView.as_view(), name="pwa_config"),
    path("pwa/app/", PWAAttendanceShellView.as_view(), name="pwa_app"),
    path("mark/", AttendanceMarkView.as_view(), name="mark"),
    path("correct/", AttendanceCorrectionView.as_view(), name="correct"),
    path("parent/", ParentAttendanceView.as_view(), name="parent"),
    path("staff/", StaffAttendanceView.as_view(), name="staff"),
    path("print-register/", BlankAttendanceRegisterView.as_view(), name="print_register"),
    
    # Legacy API
    path("api/checkin-legacy/", CheckInCheckOutAPIView.as_view(), name="api_checkin_legacy"),
    
    # Laravel-compatible API endpoints
    path("api/send-otp/", send_otp, name="api_send_otp"),
    path("api/verify-otp/", verify_otp, name="api_verify_otp"),
    path("api/checkin/", checkin_students, name="api_checkin"),
    path("api/checkout/", checkout_students, name="api_checkout"),
    path("api/fetch-checklist/", fetch_checklist, name="api_fetch_checklist"),
    
    # Enhanced API endpoints
    path("api/students/search/", search_students, name="api_search_students"),
    path("api/students/<str:student_id>/", student_detail, name="api_student_detail"),
    path("api/qr/lookup/", qr_lookup, name="api_qr_lookup"),
    path("api/attendance/statistics/", attendance_statistics, name="api_attendance_stats"),
    
    # Scan statistics and reporting endpoints
    path("api/scans/today/", scans_today, name="api_scans_today"),
    path("api/scans/statistics/", scans_statistics, name="api_scans_statistics"),
    path("api/scans/history/", scan_history, name="api_scans_history"),
    path("api/submit-scans/", submit_scans, name="api_submit_scans"),
    path("api/attendance/mark-excused/", mark_excused, name="api_mark_excused"),
    
    # Comprehensive reporting endpoints
    path("api/reports/daily/", daily_attendance_report, name="api_daily_report"),
    path("api/reports/monthly/", monthly_attendance_report, name="api_monthly_report"),
    path("api/reports/classwise/", classwise_attendance_report, name="api_classwise_report"),
    path("api/reports/parent-pickup/", parent_pickup_report, name="api_parent_pickup_report"),
    path("api/reports/trend-analysis/", attendance_trend_analysis, name="api_trend_analysis"),
    path("api/reports/compliance/", regulatory_compliance_report, name="api_compliance_report"),
    path("api/reports/alerts/", low_attendance_alerts, name="api_low_attendance_alerts"),
    path("api/reports/alerts-summary/", attendance_alerts_summary, name="api_alerts_summary"),
    
    # QR code endpoints
    path("qr/generate/", QRCodeImageView.as_view(), name="qr_generate"),
    path("qr/student/<str:student_id>/", StudentQRCodeView.as_view(), name="qr_student"),

    # Laravel webhook endpoints (no CSRF for external calls)
    path("webhook/attendance-push/", webhook_attendance_push, name="webhook_attendance_push"),
    path("webhook/health/", webhook_health, name="webhook_health"),
]

