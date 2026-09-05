from django.urls import path
from .views import ReportsDashboardView

app_name = "reports"

urlpatterns = [
    path("", ReportsDashboardView.as_view(), name="index"),
]
