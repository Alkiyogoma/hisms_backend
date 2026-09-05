from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from core.permissions import RoleRequiredMixin
from users.models import UserRole

class ReportsDashboardView(RoleRequiredMixin, TemplateView):
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.ADMIN_OFFICER, UserRole.FINANCE_OFFICER]
    required_permission = "academics.view_reportcard"
    template_name = "reports/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add quick links to different reporting areas
        context["report_categories"] = [
            {
                "title": "Academic Reports",
                "icon": "book-open",
                "description": "Grading, assessment performance, and class analytics.",
                "url": "academics:analytics",
            },
            {
                "title": "Admissions Reports",
                "icon": "users",
                "description": "Enrolment trends, inquiry conversion, and demographic data.",
                "url": "admissions:pipeline",
            },
            {
                "title": "Financial Reports",
                "icon": "credit-card",
                "description": "Fee collection, balance tracking, and revenue forecasts.",
                "url": "finance:dashboard",
            },
            {
                "title": "Attendance Analytics",
                "icon": "check-circle",
                "description": "Student and staff attendance trends.",
                "url": "attendance:today",
            },
        ]
        return context
