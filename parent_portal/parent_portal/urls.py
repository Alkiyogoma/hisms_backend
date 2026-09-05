"""Parent portal URL patterns."""
from django.urls import path
from . import views

app_name = "parent_portal"

urlpatterns = [
    path("", views.ParentDashboardView.as_view(), name="dashboard"),
    path("attendance/", views.ParentAttendanceView.as_view(), name="attendance"),
    path("grades/", views.ParentGradesView.as_view(), name="grades"),
    path("child/<int:student_id>/", views.ParentChildDetailView.as_view(), name="child_detail"),
]
