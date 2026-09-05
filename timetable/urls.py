from django.urls import path

from timetable.views import (
    TimetableSlotCreateView,
    TimetableSlotUpdateView,
    TimetableSlotDeleteView,
    BulkDeleteTimetableView,
    TimetableView,
    CloneTimetableView,
    PublishTimetableView,
    UnpublishTimetableView,
)
from timetable.views_break import (
    EcdBreakConfigListView,
    EcdBreakConfigCreateView,
    EcdBreakConfigUpdateView,
)

app_name = "timetable"

urlpatterns = [
    path("", TimetableView.as_view(), name="index"),
    path("new/", TimetableSlotCreateView.as_view(), name="new"),
    path("slots/<int:pk>/edit/", TimetableSlotUpdateView.as_view(), name="slot_edit"),
    path("slots/<int:pk>/delete/", TimetableSlotDeleteView.as_view(), name="slot_delete"),
    path("bulk-delete/", BulkDeleteTimetableView.as_view(), name="bulk_delete"),
    path("clone/", CloneTimetableView.as_view(), name="clone"),
    path("publish/", PublishTimetableView.as_view(), name="publish"),
    path("unpublish/", UnpublishTimetableView.as_view(), name="unpublish"),
    # ECD Break Config (FR-TT-007)
    path("breaks/", EcdBreakConfigListView.as_view(), name="break_config_list"),
    path("breaks/new/", EcdBreakConfigCreateView.as_view(), name="break_config_create"),
    path("breaks/<int:pk>/edit/", EcdBreakConfigUpdateView.as_view(), name="break_config_edit"),
]

