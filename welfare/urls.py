from django.urls import path
from . import views

app_name = "welfare"

urlpatterns = [
    # ECD Welfare routes (default)
    path("", views.WelfareListView.as_view(), name="list"),
    path("submit/", views.WelfareSubmitView.as_view(), name="submit"),
    path("dashboard/", views.WelfareHODDashboardView.as_view(), name="hod_dashboard"),
    path("student-incidents/", views.WelfareStudentIncidentsView.as_view(), name="student_incidents"),
    path("<int:pk>/", views.WelfareDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.WelfareEditView.as_view(), name="edit"),
    path("<int:pk>/review/", views.WelfareHODReviewView.as_view(), name="hod_review"),
    path("<int:pk>/confirm/", views.WelfareParentConfirmView.as_view(), name="parent_confirm"),
    path("<int:pk>/acknowledge/", views.WelfareAcknowledgeView.as_view(), name="acknowledge"),

    # ECD Welfare explicit routes
    path("ecd/", views.WelfareListView.as_view(), name="list_ecd"),
    path("ecd/submit/", views.WelfareSubmitView.as_view(department="ecd"), name="submit_ecd"),
    path("ecd/dashboard/", views.WelfareHODDashboardView.as_view(department="ecd"), name="hod_dashboard_ecd"),
    path("ecd/incidents/", views.WelfareStudentIncidentsView.as_view(department="ecd"), name="student_incidents_ecd"),

    # Primary Welfare routes
    path("primary/", views.WelfareListView.as_view(department="primary"), name="list_primary"),
    path("primary/submit/", views.WelfareSubmitView.as_view(department="primary"), name="submit_primary"),
    path("primary/dashboard/", views.WelfareHODDashboardView.as_view(department="primary"), name="hod_dashboard_primary"),
    path("primary/incidents/", views.WelfareStudentIncidentsView.as_view(department="primary"), name="student_incidents_primary"),
]
