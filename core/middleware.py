from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone

from core.threadlocal import reset_current_request, set_current_request

SESSION_IDLE_TIMEOUT = getattr(settings, "SESSION_IDLE_TIMEOUT", 1800)  # 30 min (matches settings.py default)


class RequestContextMiddleware:
    """Stores the current request for audit logging (IP, actor context)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = set_current_request(request)
        try:
            return self.get_response(request)
        finally:
            reset_current_request(token)


class SessionTimeoutMiddleware:
    """
    FRD NFR-SEC-003: Expire sessions after SESSION_IDLE_TIMEOUT of inactivity.
    Stores timestamps so the frontend can show a 2-minute warning modal.
    """
    EXEMPT_PATHS = ("/accounts/login/", "/accounts/logout/", "/logout/", "/admin/")
    WARNING_BEFORE_SECONDS = 120  # 2-minute countdown warning

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            # Skip exempt paths (login page itself, etc.)
            if not any(request.path.startswith(p) for p in self.EXEMPT_PATHS):
                now_ts = timezone.now().timestamp()
                last_activity = request.session.get("_last_activity", now_ts)
                idle_seconds = now_ts - last_activity

                if idle_seconds > SESSION_IDLE_TIMEOUT:
                    # Save to notification tray/bell before logging out
                    try:
                        from communications.models import Notification, NotificationCategory
                        Notification.objects.create(
                            recipient=request.user,
                            category=NotificationCategory.SYSTEM,
                            title="Session Expired",
                            body="Your session expired due to inactivity. Please sign in again.",
                        )
                    except Exception:
                        pass
                    from django.contrib.auth import logout
                    current_path = request.path
                    logout(request)
                    # FR-CAL-003: Use query param instead of messages framework
                    # to avoid stale messages persisting after re-login.
                    # Preserve the next param so user returns to their page after re-login,
                    # except for PWA paths — session expiry should go to dashboard.
                    next_param = "" if current_path.startswith("/attendance/pwa/") else f"&next={current_path}"
                    return redirect(f"{settings.LOGIN_URL}?session_expired=1{next_param}")

                # Don't reset timestamp for the check API — otherwise the frontend
                # poll always sees a fresh session and the warning modal never triggers.
                if not request.path.startswith("/api/session/check/"):
                    request.session["_last_activity"] = now_ts
                # Expose session expiry info to the frontend template context
                request.session_expiry_seconds = max(0, int(SESSION_IDLE_TIMEOUT - idle_seconds))
                request.session_warning_at = max(0, int(SESSION_IDLE_TIMEOUT - self.WARNING_BEFORE_SECONDS - idle_seconds))

        return self.get_response(request)


class ImpersonationMiddleware:
    """FRD FR-DASH-008: Super Admin 'View As' impersonation mode.

    - Swaps request.user to the target user for read-only viewing
    - Blocks all write actions (POST/PUT/PATCH/DELETE) during impersonation
    - Logs start/end of impersonation sessions to the audit trail
    """
    BLOCKED_HTTP_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            impersonate_id = request.session.get("impersonate_user_id")
            if impersonate_id:
                from users.models import User
                try:
                    target_user = User.objects.get(pk=impersonate_id)
                    request.real_user = request.user
                    request.user = target_user
                    request.is_impersonating = True

                    # FR-DASH-008: Block write actions during impersonation
                    # Exempt impersonation endpoints so users can switch targets or stop
                    _impersonate_exempt = any(
                        request.path.startswith(p)
                        for p in ("/accounts/impersonate/", "/users/impersonate/")
                    )
                    if request.method in self.BLOCKED_HTTP_METHODS and not _impersonate_exempt:
                        from django.http import HttpResponseRedirect
                        if request.headers.get("HX-Request") == "true":
                            from django.http import HttpResponseForbidden
                            return HttpResponseForbidden(
                                "Write actions are disabled in View-As mode."
                            )
                        # Redirect back with warning param — base.html JS picks it up as a toast
                        referer = request.META.get("HTTP_REFERER", "/")
                        separator = "&" if "?" in referer else "?"
                        return HttpResponseRedirect(f"{referer}{separator}view_as_warning=1")

                    # Log impersonation start (once per session)
                    if not request.session.get("_impersonation_logged"):
                        request.session["_impersonation_logged"] = True
                        self._log_impersonation(
                            request=request,
                            actor=request.real_user,
                            target=target_user,
                            action_type="IMPERSONATION_STARTED",
                            description=(
                                f"Super Admin '{request.real_user.username}' started "
                                f"View-As session as '{target_user.username}' "
                                f"({target_user.get_role_display()})."
                            ),
                        )
                except User.DoesNotExist:
                    request.session.pop("impersonate_user_id", None)
                    request.session.pop("_impersonation_logged", None)

        return self.get_response(request)

    def _log_impersonation(self, request, actor, target, action_type, description):
        """Write an audit log entry for impersonation start/end."""
        try:
            from core.utils import get_client_ip
            from audit.models import AuditLog
            AuditLog.objects.create(
                actor=actor,
                action_type=action_type,
                model_name="User",
                object_id=str(target.pk) if target else "",
                description=description,
                ip_address=get_client_ip(request),
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Failed to log impersonation: %s", exc)


class MustChangePasswordMiddleware:
    """
    OP 4.5 / NFR-SEC-003: Force password change on first login.

    Redirects any authenticated user with must_change_password=True to the
    password-change page. Exempts the password-change endpoint itself,
    logout, and static/media paths to avoid redirect loops.
    """
    EXEMPT_PATHS = (
        "/accounts/password-change/",
        "/accounts/logout/",
        "/accounts/login/",
        "/static/",
        "/media/",
        "/api/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and request.user.must_change_password
            and not any(request.path.startswith(p) for p in self.EXEMPT_PATHS)
        ):
            from django.urls import reverse
            from django.shortcuts import redirect as http_redirect
            return http_redirect(reverse("users:password_change"))
        return self.get_response(request)


class HtmxMessageMiddleware:
    """
    Bridges Django messages to HTMX showToast event via HX-Trigger header.
    Works with the showToast JS function in base.html.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.headers.get("HX-Request") == "true":
            storage = messages.get_messages(request)
            for message in storage:
                # We only take the first message for now to trigger the toast
                level = "info"
                if message.tags == "error": level = "error"
                elif message.tags == "success": level = "success"
                elif message.tags == "warning": level = "warning"
                
                import json
                trigger = response.headers.get("HX-Trigger", "{}")
                try:
                    trigger_data = json.loads(trigger) if trigger.startswith("{") else {trigger: True}
                except json.JSONDecodeError:
                    trigger_data = {trigger: True}
                
                trigger_data["showToast"] = {"message": str(message), "level": level}
                response["HX-Trigger"] = json.dumps(trigger_data)
                break # Only one toast per response for simplicity

        return response


class ContentSecurityPolicyMiddleware:
    """
    FRD NFR-SEC-001: Sets Content-Security-Policy header to mitigate XSS attacks.

    Restricts script sources, style sources, and other resource loading
    to trusted origins. Reporting is enabled via report-uri for monitoring
    policy violations without blocking (report-only mode first).

    In production (DEBUG=False), switches to enforce mode.
    """

    # Base CSP policy — permissive enough for Hodari's HTMX + Quill + Flatpickr stack
    CSP_DEFAULT = (
        "default-src 'self'; "
        "script-src 'self' https://unpkg.com https://cdn.jsdelivr.net https://cdn.quilljs.com https://cdnjs.cloudflare.com 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' https://cdn.jsdelivr.net https://fonts.googleapis.com https://cdn.quilljs.com https://cdnjs.cloudflare.com 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' ws: wss: https://cdn.jsdelivr.net https://unpkg.com https://cdn.quilljs.com https://cdnjs.cloudflare.com; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "base-uri 'self'; "
        "object-src 'none'"
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self.report_only = getattr(settings, "DEBUG", True)

    def __call__(self, request):
        response = self.get_response(request)

        # Only set CSP on HTML responses (not API, static files, etc.)
        content_type = response.get("Content-Type", "")
        if "text/html" not in content_type:
            return response

        policy = self.CSP_DEFAULT

        if self.report_only:
            response["Content-Security-Policy-Report-Only"] = policy + "; report-uri /audit/csp-reports/"
        else:
            response["Content-Security-Policy"] = policy

        return response
