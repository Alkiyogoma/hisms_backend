from django.urls import path

from ptc.views import (
    PTCStudentPickerView,
    PTCScreenView,
    PTCCommentEntryView,
    PTCAttributeEntryView,
    PTCEnrichmentGradeEntryView,
    PTCEnrichmentConfigView,
    PTCComplianceDashboardView,
    PTCGenerationLogView,
    PTCCalendarConfigView,
    PTCAttributeCompletionAPIView,
)

app_name = "ptc"

urlpatterns = [
    # Student picker (class teacher entry point)
    path("", PTCStudentPickerView.as_view(), name="student_picker"),
    # PTC screen (class teacher view / print)
    path("screen/<int:student_pk>/", PTCScreenView.as_view(), name="ptc_screen"),
    # Comment entry (subject teacher)
    path("comments/", PTCCommentEntryView.as_view(), name="comment_entry"),
    # Learner attributes entry grid (class teacher)
    path("attributes/", PTCAttributeEntryView.as_view(), name="attribute_entry"),
    # Enrichment grade entry (specialist teacher)
    path("enrichment/", PTCEnrichmentGradeEntryView.as_view(), name="enrichment_entry"),
    # Enrichment subject configuration (Super Admin / manage_enrichment_config)
    path("enrichment/config/", PTCEnrichmentConfigView.as_view(), name="enrichment_config"),
    # Compliance dashboard (Primary HOD, read-only)
    path("compliance/", PTCComplianceDashboardView.as_view(), name="compliance_dashboard"),
    # Generation log (HOS, read-only)
    path("generation-log/", PTCGenerationLogView.as_view(), name="generation_log"),
    # Calendar configuration (Admin Officer)
    path("calendar/", PTCCalendarConfigView.as_view(), name="calendar_config"),
    # API: live completion count for attribute grid
    path("api/attribute-completion/", PTCAttributeCompletionAPIView.as_view(), name="api_attribute_completion"),
]
