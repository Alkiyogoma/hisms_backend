"""
Production Settings Overlay for HODARI
Import this in settings.py or use via environment.

This file contains production-specific optimizations:
- Caching configuration
- Database connection pooling
- Template caching
- Security hardening
- Performance optimizations
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# =============================================================================
# CACHING - Redis-backed caching for production
# Redis DB allocation: broker=0, result=1, channels=2, cache=5, sessions=3, templates=4
# Note: DB 0 was previously shared with Celery broker — moved to DB 5 to avoid
# key eviction contention between cache writes and Celery message pub/sub.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.getenv("REDIS_URL", "redis://localhost:6379/5"),  # DB 5 — separate from Celery broker (0)
        "TIMEOUT": 300,  # 5 minutes default
        "KEY_PREFIX": "hodari",
        "VERSION": 1,
    },
    # Separate cache for sessions
    "sessions": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.getenv("REDIS_URL", "redis://localhost:6379/3"),
        "TIMEOUT": 1800,  # 30 minutes
        "KEY_PREFIX": "hodari_session",
    },
    # Template fragment cache
    "templates": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.getenv("REDIS_URL", "redis://localhost:6379/4"),
        "TIMEOUT": 600,  # 10 minutes
        "KEY_PREFIX": "hodari_template",
    },
}

# =============================================================================
# SESSION ENGINE - Redis-backed sessions (overrides default)
# =============================================================================
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "sessions"

# =============================================================================
# LOGGING - Production logging configuration
# =============================================================================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "filters": {
        "require_debug_false": {
            "()": "django.utils.log.RequireDebugFalse",
        },
    },
    "handlers": {
        "console": {
            "level": "WARNING",
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
        "file": {
            "level": "WARNING",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "/var/log/hodari/django.log",
            "maxBytes": 1024 * 1024 * 10,  # 10MB
            "backupCount": 5,
            "formatter": "verbose",
        },
        "mail_admins": {
            "level": "ERROR",
            "filters": ["require_debug_false"],
            "class": "django.utils.log.AdminEmailHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": "WARNING",
            "propagate": True,
        },
        "django.request": {
            "handlers": ["console", "file", "mail_admins"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console", "file", "mail_admins"],
            "level": "WARNING",
            "propagate": False,
        },
        "attendance": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "finance": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "WARNING",
    },
}

# =============================================================================
# SECURITY - Additional production security (applied when DEBUG=False)
# =============================================================================
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
CSRF_TRUSTED_ORIGINS = [
    "https://connect.hodari.ac.tz",
    "http://connect.hodari.ac.tz",
    "https://hodari.nguzo.co.tz",
    "https://hodari.nguzo.co.tz:8443",
    "http://hodari.nguzo.co.tz",
]
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# =============================================================================
# CORS - Restrict to production domain
# =============================================================================
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [
    "https://connect.hodari.ac.tz",
    "http://connect.hodari.ac.tz",
    "https://hodari.nguzo.co.tz",
    "http://hodari.nguzo.co.tz",
]
CORS_ALLOW_CREDENTIALS = True

# =============================================================================
# REST FRAMEWORK - Production optimizations with rate limiting
# =============================================================================
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/hour",
        "user": "1000/hour",
    },
}

# =============================================================================
# STATIC FILES - Production optimization (Django 5.1+ STORAGES API)
# =============================================================================
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Django 5.1+ uses STORAGES instead of STATICFILES_STORAGE
STORAGES = {
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
    },
}

# =============================================================================
# MEDIA FILES
# =============================================================================
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# =============================================================================
# CELERY - Production configuration
# =============================================================================
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Africa/Nairobi"
CELERY_ENABLE_UTC = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes hard limit
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60  # 25 minutes soft limit
CELERY_WORKER_PREFETCH_MULTIPLIER = 4
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1000
CELERY_WORKER_CONCURRENCY = 4

# =============================================================================
# CHANNELS - WebSocket configuration
# =============================================================================
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [os.getenv("CHANNELS_REDIS_URL", "redis://localhost:6379/2")],
            "capacity": 1500,
            "expiry": 10,
        },
    },
}

# =============================================================================
# DATABASE BACKUP
# =============================================================================
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/var/backups/hodari"))
BACKUP_RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))
