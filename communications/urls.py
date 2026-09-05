from django.urls import path
from . import views

app_name = "communications"

urlpatterns = [
    path("", views.NotificationListView.as_view(), name="inbox"),
    path("mark-read/<int:pk>/", views.MarkNotificationReadView.as_view(), name="mark_read"),
    path("mark-all-read/", views.MarkAllReadView.as_view(), name="mark_all_read"),
    path("broadcasts/", views.BroadcastListView.as_view(), name="broadcast_list"),
    path("broadcasts/compose/", views.BroadcastComposeView.as_view(), name="broadcast_compose"),
    path("broadcasts/<int:pk>/", views.BroadcastDetailView.as_view(), name="broadcast_detail"),
    path("broadcasts/<int:pk>/edit/", views.BroadcastUpdateView.as_view(), name="broadcast_edit"),
    path("broadcasts/<int:pk>/delete/", views.BroadcastDeleteView.as_view(), name="broadcast_delete"),
    path("broadcasts/<int:pk>/send/", views.BroadcastSendView.as_view(), name="broadcast_send"),
    # ECD Weekly Focus — FR-COM-007…010
    path("weekly-focus/", views.WeeklyFocusListView.as_view(), name="weekly_focus_list"),
    path("weekly-focus/submit/", views.WeeklyFocusSubmitView.as_view(), name="weekly_focus_submit"),
    path("weekly-focus/<int:pk>/edit/", views.WeeklyFocusEditView.as_view(), name="weekly_focus_edit"),
    path("weekly-focus/<int:pk>/review/", views.WeeklyFocusReviewView.as_view(), name="weekly_focus_review"),
    path("weekly-focus/<int:pk>/", views.WeeklyFocusDetailView.as_view(), name="weekly_focus_detail"),
    path("weekly-focus/parent/", views.WeeklyFocusParentView.as_view(), name="weekly_focus_parent"),
    path("weekly-focus/compliance/", views.WeeklyFocusComplianceView.as_view(), name="weekly_focus_compliance"),
    path("api/otp/request/", views.RequestOTPView.as_view(), name="otp_request"),
    path("api/otp/verify/", views.VerifyOTPView.as_view(), name="otp_verify"),
]
