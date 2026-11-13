"""
Email delivery for Hodari.

SMTP is configured only through environment variables (.env): EMAIL_BACKEND,
EMAIL_HOST, EMAIL_PORT, EMAIL_USE_TLS / EMAIL_USE_SSL, EMAIL_HOST_USER,
EMAIL_HOST_PASSWORD and DEFAULT_FROM_EMAIL. Nothing is read from the database.
"""
import logging

from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend as SmtpEmailBackend

logger = logging.getLogger(__name__)


class DatabaseEmailBackend(SmtpEmailBackend):
    """Deprecated alias kept so existing .env files with
    EMAIL_BACKEND=core.email_backend.DatabaseEmailBackend keep working.
    Behaves exactly like Django's SMTP backend (settings come from .env)."""


# ── Effective delivery (used by every outgoing email) ────────────────────────

_NON_DELIVERING = ("console", "dummy", "filebased", "json_email_backend", "jsonfile")


def resolve_delivery():
    """Work out how email will actually be delivered right now (from .env).

    Returns (connection, from_email, info) where info has:
      mode: "smtp" | "env" | "test" | "off"
      delivering: bool  False means messages would be printed/discarded
      detail: human-readable description for the settings page / logs
      host, port, security, user, from_email: SMTP summary (never the password)
    """
    from django.core.mail import get_connection

    backend = getattr(settings, "EMAIL_BACKEND", "") or ""
    default_from = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@hodari.ac.tz")
    lowered = backend.lower()

    if "locmem" in lowered:  # test runner: keep Django's outbox
        return get_connection(), default_from, {"mode": "test", "delivering": True, "detail": "Test outbox"}

    if any(tag in lowered for tag in _NON_DELIVERING):
        return get_connection(), default_from, {
            "mode": "off", "delivering": False,
            "detail": "Not configured: emails are only printed to the server log. "
                      "Set EMAIL_BACKEND and the SMTP details in the server's .env file, then restart.",
        }

    if "smtp" in lowered or backend.endswith("DatabaseEmailBackend"):
        host = getattr(settings, "EMAIL_HOST", "")
        port = getattr(settings, "EMAIL_PORT", 587)
        user = getattr(settings, "EMAIL_HOST_USER", "")
        security = "SSL" if getattr(settings, "EMAIL_USE_SSL", False) else (
            "STARTTLS" if getattr(settings, "EMAIL_USE_TLS", False) else "none")
        password = getattr(settings, "EMAIL_HOST_PASSWORD", "")
        # Django only logs in when BOTH user and password are set; otherwise it
        # sends unauthenticated and Gmail/Workspace rejects every message with
        # "530 5.7.0 Authentication Required". Catch that before sending.
        needs_auth = bool(user) or "gmail" in host.lower() or "google" in host.lower()
        if not host:
            detail = "Not configured: EMAIL_HOST is empty in the server's .env file."
        elif needs_auth and not (user and password):
            missing = "EMAIL_HOST_PASSWORD" if user else "EMAIL_HOST_USER and EMAIL_HOST_PASSWORD"
            detail = (f"Not configured: {missing} is empty in the server's .env file, so "
                      f"{host} refuses to send. Add the mailbox App Password, then restart the web and worker services.")
        else:
            detail = f"SMTP {host}:{port}" + (f" as {user}" if user else "")
        info = {
            "mode": "smtp", "delivering": bool(host) and not (needs_auth and not (user and password)),
            "host": host, "port": port, "security": security, "user": user, "from_email": default_from,
            "detail": detail,
        }
        return get_connection(fail_silently=False), default_from, info

    return get_connection(), default_from, {"mode": "env", "delivering": True, "detail": f"Server default ({backend})"}
