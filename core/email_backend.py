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
      delivering: bool — False means messages would be printed/discarded
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
        info = {
            "mode": "smtp", "delivering": bool(host),
            "host": host, "port": port, "security": security, "user": user, "from_email": default_from,
            "detail": f"SMTP {host}:{port}" + (f" as {user}" if user else "") if host
                      else "Not configured: EMAIL_HOST is empty in the server's .env file.",
        }
        return get_connection(fail_silently=False), default_from, info

    return get_connection(), default_from, {"mode": "env", "delivering": True, "detail": f"Server default ({backend})"}
