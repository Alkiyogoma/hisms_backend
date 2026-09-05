from pathlib import Path
import os

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-secret-key-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
_hosts_raw = os.getenv("DJANGO_ALLOWED_HOSTS", "192.168.1.142,127.0.0.1,localhost").strip()
ALLOWED_HOSTS = [h.strip() for h in _hosts_raw.split(",") if h.strip()] or ["*"]

INSTALLED_APPS = [
    # "daphne",  # Django Channels ASGI server - temporarily disabled for dev runserver
    # NOTE: daphne causes twisted/attrs incompatibility in test env; infrastructure is configured below
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Third-party apps
    "rest_framework",
    "corsheaders",
    "django_celery_beat",
    # Project apps
    "core",
    "users",
    "students",
    "academics",
    "finance",
    "hr",
    "discipline",
    "audit",
    "reports",
    "admissions",
    "attendance",
    "timetable",
    "communications",
    "welfare",
    "events",
    "tasks",
    "ptc",
    "parent_portal",
]


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "users.middleware.SessionRoleMiddleware",
    "core.middleware.MustChangePasswordMiddleware",
    "core.middleware.ImpersonationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.middleware.RequestContextMiddleware",
    "core.middleware.SessionTimeoutMiddleware",
    "core.middleware.HtmxMessageMiddleware",
    "audit.middleware.AuditMiddleware",
    "core.middleware.ContentSecurityPolicyMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {
            "loaders": [
                ("django.template.loaders.cached.Loader", [
                    "django.template.loaders.filesystem.Loader",
                    "django.template.loaders.app_directories.Loader",
                ]),
            ] if not DEBUG else [
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ],
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.csrf",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.globals",
                "core.context_processors.branding",
                "core.context_processors.navigation",
                "tasks.context_processors.task_counts",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

_sqlite_flag = os.getenv("DJANGO_USE_SQLITE", "").strip().lower()
_use_sqlite = _sqlite_flag in ("1", "true", "yes")
if not _use_sqlite:
    _postgres_host = os.getenv("POSTGRES_HOST", "").strip()
    _postgres_password = os.getenv("POSTGRES_PASSWORD", "").strip()
    _use_sqlite = _postgres_host in ("", "localhost", "127.0.0.1") and not _postgres_password

if _use_sqlite:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "hisms"),
            "USER": os.getenv("POSTGRES_USER", "postgres"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "postgres"),
            "HOST": os.getenv("POSTGRES_HOST", "localhost"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "300")),
        }
    }

AUDIT_LOG_DB_CONSTRAINT = not _use_sqlite

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

SITE_URL = os.getenv("SITE_URL", "http://127.0.0.1:8000")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "users.User"
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

# =============================================================================
# CACHING
# =============================================================================
# Production (deployment/production_settings.py) uses Redis on DB 3/4/5.
# In development without this setting, Django falls back to LocMemCache,
# which is per-process and does NOT enforce rate limits correctly when
# multiple Gunicorn/uWSGI workers share the same server. Each worker
# maintains its own counter, so the effective limit becomes
# configured_limit × num_workers.  Always use a shared backend (Redis,
# Memcached) in any deployment behind more than one worker process.
# DB 3 is reserved for the default cache; DB 4 for sessions; DB 5 for
# templates.  Celery broker=DB 0, result=DB 1, Channels=DB 2.
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
if os.getenv("REDIS_URL"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL.replace("/0", "/3"),  # DB 3 — separate from Celery broker (0)
            "TIMEOUT": 300,
            "KEY_PREFIX": "hodari",
        },
    }
# else: Django default LocMemCache — fine for single-process dev/test,
# but NOT safe for production multi-worker deployments. See warning above.

# Security — FRD NFR-SEC-003 / NFR-SEC-005
SESSION_COOKIE_AGE = 28800  # 8 hours absolute max (idle timeout controls actual expiry)
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_IDLE_TIMEOUT = int(os.getenv("SESSION_IDLE_TIMEOUT", "1800"))  # 30 min total; warning at 28 min via WARNING_BEFORE_SECONDS=120 (FRD NFR-SEC-003)
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_DURATION = int(os.getenv("LOGIN_LOCKOUT_DURATION", "900"))  # 15 min in seconds

# Media files (student photos etc.)
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Lesson Plans — FRD FR-LP-003: Deadline is Monday 8:00 AM of the relevant week
LESSON_PLAN_SUBMISSION_DEADLINE_DAY = int(os.getenv("LP_DEADLINE_DAY", "0"))  # Monday (0=Mon, 6=Sun)
LESSON_PLAN_SUBMISSION_DEADLINE_TIME = os.getenv("LP_DEADLINE_TIME", "08:00")  # 8 AM EAT

# Laravel Database Connection for Migration
LARAVEL_DATABASE = {
    'HOST': os.getenv('LARAVEL_DB_HOST', '127.0.0.1'),
    'PORT': int(os.getenv('LARAVEL_DB_PORT', '3306')),
    'DATABASE': os.getenv('LARAVEL_DB_DATABASE', 'hodari_checkin'),
    'USER': os.getenv('LARAVEL_DB_USER', 'hodari_checkin'),
    'PASSWORD': os.getenv('LARAVEL_DB_PASSWORD', ''),
}

# Django Channels Configuration for Real-Time WebSocket Support
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

# WebSocket settings
WEBSOCKET_ACCEPT_ALL = False  # Require authentication
WEBSOCKET_TIMEOUT = 300  # 5 minutes

# Django REST Framework Configuration
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_THROTTLE_CLASSES': (
        'rest_framework.throttling.UserRateThrottle',
        'rest_framework.throttling.AnonRateThrottle',
    ),
    'DEFAULT_THROTTLE_RATES': {
        'user': '60/minute',
        'anon': '5/minute',
    },
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
}

# SimpleJWT Configuration
from datetime import timedelta
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=12),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': False,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
}

# CORS Configuration for Flutter mobile app
CORS_ALLOW_ALL_ORIGINS = os.getenv('CORS_ALLOW_ALL_ORIGINS', 'True').lower() in ('true', '1', 'yes')
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv('CORS_ALLOWED_ORIGINS', '').split(',')
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]

# Celery Configuration for Background Task Processing
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_ENABLE_UTC = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes hard limit
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60  # 25 minutes soft limit
CELERY_WORKER_PREFETCH_MULTIPLIER = 4
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1000

# Email Configuration for Notifications
# Default: JSON file backend only under DEBUG (local).  In production the
# DatabaseEmailBackend reads SMTP config from SchoolSettings (DB) and falls
# back to EMAIL_HOST below, so real emails are always attempted on the server.
_DEFAULT_EMAIL_BACKEND = (
    'core.json_email_backend.JsonFileEmailBackend'
    if bool(os.getenv("DJANGO_DEBUG", "1") == "1")
    else 'core.email_backend.DatabaseEmailBackend'
)
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', _DEFAULT_EMAIL_BACKEND)
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'noreply@hodari.ac.tz')

# --- Task failure alerting (Celery task_failure signal) ---
# Email recipients for scheduled task failure alerts.
# Falls back to Django ADMINS if empty.
TASK_FAILURE_ALERT_EMAILS = [
    e.strip() for e in os.getenv('TASK_FAILURE_ALERT_EMAILS', '').split(',') if e.strip()
]
# Optional: Slack incoming webhook URL for real-time failure alerts.
SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL', '')

# SMS Provider Configuration
CLOUDSERVICE_API_KEY = os.getenv('CLOUDSERVICE_API_KEY', '')
CLOUDSERVICE_API_URL = os.getenv('CLOUDSERVICE_API_URL', 'https://api.cloudservice.com/sms/send')
CLOUDSERVICE_SENDER_ID = os.getenv('CLOUDSERVICE_SENDER_ID', 'HODARI')

HODARI_SMS_API_KEY = os.getenv('HODARI_SMS_API_KEY', '')
HODARI_SMS_API_URL = os.getenv('HODARI_SMS_API_URL', 'https://api.hodari.ac.tz/sms/send')
HODARI_SMS_SENDER_ID = os.getenv('HODARI_SMS_SENDER_ID', 'HODARI')

# SMS Provider selection: 'cloudservice' or 'hodari'
SMS_PROVIDER = os.getenv('SMS_PROVIDER', 'cloudservice')

# Admissions contact details — stored in SchoolSettings model, env vars are fallback only
ADMISSIONS_PHONE = os.getenv('ADMISSIONS_PHONE', '')
ADMISSIONS_EMAIL = os.getenv('ADMISSIONS_EMAIL', '')
ADMISSIONS_WHATSAPP = os.getenv('ADMISSIONS_WHATSAPP', '')
ADMISSIONS_REPLY_TO = os.getenv('ADMISSIONS_REPLY_TO', '')

# Notification Settings
NOTIFICATION_MAX_RETRIES = int(os.getenv('NOTIFICATION_MAX_RETRIES', '3'))
NOTIFICATION_RETRY_DELAY_MINUTES = int(os.getenv('NOTIFICATION_RETRY_DELAY_MINUTES', '5'))
ATTENDANCE_ALERT_THRESHOLD = float(os.getenv('ATTENDANCE_ALERT_THRESHOLD', '0.85'))  # 85%

# Webhook authentication for physical attendance hardware (FRD NFR-SEC-004)
# Empty default = fail-closed: endpoint rejects all requests if unset
ATTENDANCE_WEBHOOK_SECRET = os.getenv("ATTENDANCE_WEBHOOK_SECRET", "")

# Password reset token expires after 1 hour (FRD AUTH-RESET-001)
PASSWORD_RESET_TIMEOUT = 3600

#  FRD NFR-SEC-001: HTTPS / TLS Security Settings 
# Only enforced when DEBUG is False (production).
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Cookie security
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True

# CSRF trusted origins for HTTPS behind reverse proxy (Nginx)
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "https://connect.hodari.ac.tz,https://hodari.elimcoregroup.com").split(",")
    if origin.strip()
]

# Browser security headers
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"

#  FRD NFR-DATA-004: Automated Backup Configuration 
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(BASE_DIR / "backups")))
BACKUP_RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))

# FRD NFR-PDPA-005: Archived student record retention period (years).
# Records are retained for this period after archival, then eligible for
# permanent deletion via the manage_archived_records management command.
ARCHIVE_RETENTION_YEARS = int(os.getenv("ARCHIVE_RETENTION_YEARS", "7"))

# Testing bypass: when True, allows attendance marking on non-school days (weekends, holidays)
BYPASS_SCHOOL_DAY_CHECK = os.getenv("BYPASS_SCHOOL_DAY_CHECK", "0") == "1"

# FRD FR-TT-006: Auto-select class in attendance module based on timetable.
# When a teacher opens attendance, the system automatically displays the
# correct class and subject for that teacher at the current time.
FRD_TT006_ATTENDANCE_AUTO_CLASS = True

# =============================================================================
# Sentry Error Tracking & Session Replay
# =============================================================================
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.redis import RedisIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(),
            RedisIntegration(),
        ],
        traces_sample_rate=0.1,
        send_default_pii=True,
        environment="production" if not DEBUG else "development",
    )

# =============================================================================
# Healthchecks.io – Celery Beat liveness monitoring
# =============================================================================
HEALTHCHECKS_PING_URL = os.getenv("HEALTHCHECKS_PING_URL", "")

