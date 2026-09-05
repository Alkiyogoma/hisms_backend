from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from users.views import HISMSLoginView, HISMSLogoutView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

admin.site.site_header = "Hodari Integrated School Management System"
admin.site.site_title = "HISMS Admin"
admin.site.index_title = "Operations"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("parent/", include("parent_portal.urls")),

    # JWT Authentication endpoints for Flutter app
    path("api/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/login/", TokenObtainPairView.as_view(), name="api_login"),

    # Custom auth (login with lockout) overrides django.contrib.auth.urls login
    path("accounts/", include("users.urls")),
    path("login/", HISMSLoginView.as_view(), name="login"),
    path("logout/", HISMSLogoutView.as_view(), name="logout"),

    # Feature modules
    path("academics/", include("academics.urls")),
    path("admissions/", include("admissions.urls")),
    path("attendance/", include("attendance.urls")),
    path("timetable/", include("timetable.urls")),
    path("finance/", include("finance.urls")),
    path("communications/", include("communications.urls")),
    path("welfare/", include("welfare.urls")),
    path("students/", include("students.urls")),
    path("hr/", include("hr.urls")),
    path("behaviour/", include("discipline.urls")),
    path("events/", include("events.urls")),
    path("audit/", include("audit.urls")),
    path("reports/", include("reports.urls")),
    path("tasks/", include("tasks.urls")),
    path("ptc/", include("ptc.urls")),
    path("", include("core.urls")),
    path("emails/", lambda r: __import__("core.email_viewer", fromlist=["email_viewer"]).email_viewer(r), name="email_viewer"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Custom branded error pages (work in DEBUG=False; Django uses these names by convention)
handler400 = "django.views.defaults.bad_request"
handler403 = "django.views.defaults.permission_denied"
handler404 = "django.views.defaults.page_not_found"
handler500 = "django.views.defaults.server_error"
