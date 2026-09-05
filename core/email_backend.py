"""
Database-backed email backend for Hodari.

Reads SMTP configuration from the SchoolSettings model in the database,
falling back to Django settings (settings.py / env vars) when DB config
is not available.
"""
import logging

from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend as SmtpEmailBackend
from django.core.mail.backends.console import EmailBackend as ConsoleEmailBackend

logger = logging.getLogger(__name__)


class DatabaseEmailBackend:
    """
    A wrapper that delegates to either SMTP or console backend
    based on the SchoolSettings stored in the database.
    Falls back to settings.py EMAIL_* config when DB has no SMTP config.
    """

    def __init__(self, **kwargs):
        self._backend = None
        self._db_key = None
        self._init_kwargs = kwargs

    def _get_backend(self):
        try:
            from core.models import SchoolSettings
            settings_obj = SchoolSettings.get_settings()
        except Exception as exc:
            logger.error("Failed to load SchoolSettings for email backend: %s", exc)
            return self._fallback_to_settings()

        # Build a fingerprint of current DB settings so we detect changes
        db_key = (
            settings_obj.email_backend,
            settings_obj.email_host,
            settings_obj.email_port,
            settings_obj.email_use_tls,
            settings_obj.email_host_user,
            settings_obj.email_host_password,
            settings_obj.default_from_email,
        )
        if self._backend is not None and self._db_key == db_key:
            return self._backend

        backend_type = (settings_obj.email_backend or '').lower()
        if 'smtp' in backend_type:
            backend_kwargs = {}
            if settings_obj.email_host:
                backend_kwargs['host'] = settings_obj.email_host
            if settings_obj.email_port:
                backend_kwargs['port'] = settings_obj.email_port
            backend_kwargs['use_tls'] = settings_obj.email_use_tls
            if settings_obj.email_host_user:
                backend_kwargs['username'] = settings_obj.email_host_user
            if settings_obj.email_host_password:
                backend_kwargs['password'] = settings_obj.email_host_password
            if settings_obj.default_from_email:
                backend_kwargs['from_email'] = settings_obj.default_from_email
            try:
                self._backend = SmtpEmailBackend(**backend_kwargs)
                self._db_key = db_key
                logger.info("Initialized SMTP email backend (%s:%s)",
                            settings_obj.email_host, settings_obj.email_port)
                return self._backend
            except Exception as exc:
                logger.error("Failed to initialize SMTP backend, falling back to settings: %s", exc)
                return self._fallback_to_settings()
        else:
            return self._fallback_to_settings()

    def _fallback_to_settings(self):
        """Fall back to Django settings (env vars) for SMTP config."""
        host = getattr(settings, 'EMAIL_HOST', '')
        if not host:
            logger.info("No EMAIL_HOST configured — using console backend")
            self._backend = ConsoleEmailBackend()
            return self._backend

        backend_kwargs = dict(self._init_kwargs)
        backend_kwargs.setdefault('host', host)
        backend_kwargs.setdefault('port', getattr(settings, 'EMAIL_PORT', 587))
        backend_kwargs.setdefault('use_tls', getattr(settings, 'EMAIL_USE_TLS', True))
        backend_kwargs.setdefault('username', getattr(settings, 'EMAIL_HOST_USER', ''))
        backend_kwargs.setdefault('password', getattr(settings, 'EMAIL_HOST_PASSWORD', ''))
        try:
            self._backend = SmtpEmailBackend(**backend_kwargs)
            logger.info("Initialized SMTP email backend from settings (%s:%s)",
                        host, backend_kwargs.get('port'))
        except Exception as exc:
            logger.error("Failed to initialize SMTP from settings, falling back to console: %s", exc)
            self._backend = ConsoleEmailBackend()
        return self._backend

    def open(self):
        return self._get_backend().open()

    def close(self):
        if self._backend:
            self._backend.close()

    def send_messages(self, email_messages):
        return self._get_backend().send_messages(email_messages)
