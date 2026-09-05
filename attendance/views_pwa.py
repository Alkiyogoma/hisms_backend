"""Attendance PWA views — manifest, config, service worker, download page, and app shell."""
from __future__ import annotations

import os

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views import View
from django.views.generic import TemplateView

from core.models import SchoolSettings
from users.models import UserRole

# Path to the PWA static directory containing sw.js and icons
_PWA_STATIC_DIR = os.path.join(
    os.path.dirname(__file__), "static", "attendance", "pwa"
)


class ServiceWorkerView(View):
    """Serves sw.js from the PWA scope directory.

    The browser restricts a service worker's scope to the directory it is
    served from.  By serving sw.js at ``/attendance/pwa/sw.js`` (inside the
    ``/attendance/pwa/`` scope) the worker can control the PWA app shell.
    No auth is required — the SW runs in the browser without cookies.
    """

    def get(self, request):
        sw_path = os.path.join(_PWA_STATIC_DIR, "sw.js")
        response = FileResponse(open(sw_path, "rb"), content_type="application/javascript")
        # Never cache the service worker — browsers check byte-equality for updates
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response


class PWAManifestView(View):
    """Serves the Web App Manifest dynamically so the domain is configurable."""

    def get(self, request):
        settings = SchoolSettings.get_settings()
        base_url = settings.pwa_domain.rstrip("/")
        # Use current request domain if pwa_domain doesn't match
        req_host = request.get_host()
        if req_host and req_host not in base_url:
            scheme = "https" if request.is_secure() else "http"
            base_url = f"{scheme}://{req_host}"

        manifest = {
            "name": "Hodari Attendance",
            "short_name": "Attendance",
            "description": "Mark student attendance with QR code scanning",
            "id": "/attendance/pwa/",
            "start_url": "/attendance/pwa/app/",
            "display": "standalone",
            "orientation": "portrait",
            "scope": "/attendance/pwa/",
            "background_color": "#023AA5",
            "theme_color": "#023AA5",
            "icons": [
                {
                    "src": f"{base_url}/static/attendance/pwa/icons/icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": f"{base_url}/static/attendance/pwa/icons/icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": f"{base_url}/static/attendance/pwa/icons/icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
        }
        return JsonResponse(manifest)


class PWAConfigView(View):
    """Returns PWA config for the service worker to read.
    No auth required — the SW runs without cookies during install/update.
    Only exposes base_url, version, and school_name (no sensitive data).
    """

    def get(self, request):
        settings = SchoolSettings.get_settings()
        return JsonResponse({
            "base_url": settings.pwa_domain.rstrip("/"),
            "version": settings.pwa_version,
            "school_name": settings.school_name,
        })


class PWADownloadView(LoginRequiredMixin, TemplateView):
    """Hidden download page — only accessible via direct URL or footer link.
    Not linked in the main navigation. Requires attendance.view_attendanceentry.
    """
    template_name = "attendance/pwa_download.html"
    login_url = "/accounts/login/"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_perm("attendance.view_attendanceentry"):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        settings = SchoolSettings.get_settings()
        base_url = settings.pwa_domain.rstrip("/")
        # Use the current request domain if pwa_domain doesn't match the host
        # being accessed. This prevents QR codes pointing to a wrong domain.
        req_host = self.request.get_host()
        if req_host and req_host not in base_url:
            scheme = "https" if self.request.is_secure() else "http"
            base_url = f"{scheme}://{req_host}"
        ctx["pwa_url"] = f"{base_url}/attendance/pwa/app/"
        ctx["pwa_manifest_url"] = f"{base_url}/attendance/pwa/manifest.json"
        ctx["school_name"] = settings.school_name
        ctx["pwa_version"] = settings.pwa_version
        return ctx


class PWAAttendanceShellView(LoginRequiredMixin, TemplateView):
    """The standalone PWA app shell — full-screen, mobile-first, no sidebar.
    Uses the existing attendance API for real-time data.
    """
    template_name = "attendance/pwa_app.html"
    login_url = "/accounts/login/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if not self.request.user.has_perm("attendance.view_attendanceentry"):
            raise PermissionDenied()

        settings = SchoolSettings.get_settings()
        pwa_domain = settings.pwa_domain.rstrip("/")
        # Use current request domain if pwa_domain doesn't match
        req_host = self.request.get_host()
        if req_host and req_host not in pwa_domain:
            scheme = "https" if self.request.is_secure() else "http"
            pwa_domain = f"{scheme}://{req_host}"
        ctx["pwa_domain"] = pwa_domain
        ctx["school_name"] = settings.school_name
        ctx["is_teacher"] = self.request.user.role == UserRole.TEACHER
        ctx["is_admin"] = self.request.user.has_perm("attendance.change_attendanceentry")
        ctx["user_role_display"] = self.request.user.get_role_display()
        return ctx
