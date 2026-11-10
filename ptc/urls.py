from django.urls import path

from ptc.views import (
    PTCStudentPickerView,
    PTCScreenView,
    PTCCommentEntryView,
    PTCAttributeEntryView,
    PTCGenerationLogView,
    PTCCalendarConfigView,
    PTCAttributeCompletionAPIView,
)

app_name = "ptc"

urlpatterns = [
    # Student picker (class teacher entry point)
    path("", PTCStudentPickerView.as_view(), name="student_picker"),
    # PTC report for one learner (view / print)
    path("screen/<int:student_pk>/", PTCScreenView.as_view(), name="ptc_screen"),
    # Subject comments (subject teacher, mark-bearing subjects)
    path("comments/", PTCCommentEntryView.as_view(), name="comment_entry"),
    # Student attributes (class teacher)
    path("attributes/", PTCAttributeEntryView.as_view(), name="attribute_entry"),
    # Log of every PTC report opening (HOS / HODs, read-only)
    path("generation-log/", PTCGenerationLogView.as_view(), name="generation_log"),
    # Calendar configuration (Admin Officer)
    path("calendar/", PTCCalendarConfigView.as_view(), name="calendar_config"),
    # API: live completion count for attribute grid
    path("api/attribute-completion/", PTCAttributeCompletionAPIView.as_view(), name="api_attribute_completion"),
]
