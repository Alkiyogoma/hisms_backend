"""
Celery configuration for HISMS backend.
Handles background task processing for notifications, data synchronization, and other async operations.
"""

import os
import requests
from celery import Celery
from celery.schedules import crontab
from config.celery_signals import register_failure_signals
from admissions.tasks import (
    send_assessment_reminders_task,
    flag_expired_offers_task,
    send_admission_fee_reminders_task,
    send_report_reminders_task,
    send_form_reminders_task,
    send_admission_invoice_reminders_task,
)

# Set the default Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('hisms_backend')

# Connect task failure signal handler
register_failure_signals()

# Load configuration from Django settings with CELERY namespace
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks from all registered Django apps
app.autodiscover_tasks()

# Celery Beat schedule for periodic tasks
app.conf.beat_schedule = {
    'retry-failed-notifications': {
        'task': 'attendance.tasks.retry_failed_notifications',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
    },
    'cleanup-expired-otp-codes': {
        'task': 'attendance.tasks.cleanup_expired_otps',
        'schedule': crontab(minute='*/10'),  # Every 10 minutes
    },
    'generate-daily-health-report': {
        'task': 'attendance.tasks.generate_daily_health_report',
        'schedule': crontab(hour=23, minute=59),  # Daily at 23:59
    },
    # FR-ATT-003: Mark unconfirmed students as Absent at end of school day
    # 16:30 EAT = 13:30 UTC (EAT is UTC+3).  School day ends ~15:30 EAT.
    'mark-auto-absent-end-of-day': {
        'task': 'attendance.tasks.mark_auto_absent',
        'schedule': crontab(hour=13, minute=30),  # 16:30 EAT
    },
    # FR-ATT-006: 9 AM unconfirmed attendance alert
    # 09:00 EAT = 06:00 UTC.
    'check-unconfirmed-attendance-9am': {
        'task': 'attendance.tasks.check_unconfirmed_attendance_9am',
        'schedule': crontab(hour=6, minute=0),  # 09:00 EAT
    },
    # FR-FIN-012: Auto-overdue invoice flagging (daily)
    # 09:00 EAT = 06:00 UTC.
    'check-overdue-invoices-daily': {
        'task': 'attendance.tasks.check_overdue_invoices_task',
        'schedule': crontab(hour=6, minute=15),  # 09:15 EAT
    },
    # FR-PTC-003 to FR-PTC-005: PTC notification & reminder triggers.
    # Runs daily at 08:00 EAT = 05:00 UTC to check for window opens and 3-day reminders.
    'send-ptc-reminders': {
        'task': 'ptc.tasks.send_ptc_reminders_task',
        'schedule': crontab(hour=5, minute=0),  # 08:00 EAT
    },
    # FR-PTC-009: Lock enrichment grades after term end.
    # Runs daily at 06:00 EAT = 03:00 UTC.
    'lock-enrichment-grades-post-term': {
        'task': 'ptc.tasks.lock_enrichment_grades_post_term',
        'schedule': crontab(hour=3, minute=0),  # 06:00 EAT
    },

        # --- Admission assessment reminders (2 days before) ---
        'send-assessment-reminders-daily': {
            'task': 'admissions.tasks.send_assessment_reminders_task',
            'schedule': crontab(hour=5, minute=30),  # 08:30 EAT
        },
        # --- Flag expired admission offers (14-day window) ---
        'flag-expired-offers-daily': {
            'task': 'admissions.tasks.flag_expired_offers_task',
            'schedule': crontab(hour=4, minute=0),  # 07:00 EAT
        },
        # --- E07: Assessment fee reminders (3+ days pending) ---
        'send-admission-fee-reminders': {
            'task': 'admissions.tasks.send_admission_fee_reminders_task',
            'schedule': crontab(hour=6, minute=0),  # 09:00 EAT
        },
        # --- E11: Report outstanding reminders (48h after assessment) ---
        'send-report-reminders': {
            'task': 'admissions.tasks.send_report_reminders_task',
            'schedule': crontab(hour=6, minute=0),  # 09:00 EAT
        },
        # --- E16: Form submission reminders (days 7, 12) ---
        'send-form-reminders': {
            'task': 'admissions.tasks.send_form_reminders_task',
            'schedule': crontab(hour=6, minute=0),  # 09:00 EAT
        },
        # --- E19: Admission invoice reminders (days 7, 14, 21) ---
        'send-admission-invoice-reminders': {
            'task': 'admissions.tasks.send_admission_invoice_reminders_task',
            'schedule': crontab(hour=6, minute=0),  # 09:00 EAT
        },
        # --- Meeting reminders (48h + 24h before) ---
        'send-meeting-reminders': {
            'task': 'admissions.tasks.send_meeting_reminders_task',
            'schedule': crontab(hour=6, minute=30),  # 09:30 EAT
        },
        # --- Assessment 24h reminders ---
        'send-assessment-24h-reminders': {
            'task': 'admissions.tasks.send_assessment_24h_reminders_task',
            'schedule': crontab(hour=5, minute=45),  # 08:45 EAT
        },
        # --- E13: HOS review reminders (72h recurring) ---
        'send-hos-review-reminders': {
            'task': 'admissions.tasks.send_hos_review_reminders_task',
            'schedule': crontab(hour=7, minute=0),  # 10:00 EAT
        },
        # --- E17: Day 14 form outstanding ---
        'send-form-outstanding-reminder': {
            'task': 'admissions.tasks.send_form_outstanding_reminder_task',
            'schedule': crontab(hour=7, minute=0),  # 10:00 EAT
        },
        # --- E20: Day 30 invoice outstanding ---
        'send-invoice-30day-reminder': {
            'task': 'admissions.tasks.send_invoice_30day_reminder_task',
            'schedule': crontab(hour=7, minute=0),  # 10:00 EAT
        },
        # --- Event reminders (2 days before) ---
        'send-event-reminders-daily': {
            'task': 'events.tasks.send_event_reminders_task',
            'schedule': crontab(hour=5, minute=45),  # 08:45 EAT
        },
    # FR-WEL-003/WEL-006: Auto-generate welfare follow-up reminders.
    # Runs at 08:00 EAT and 13:00 EAT to catch morning + afternoon windows.
    'generate-welfare-followups-morning': {
        'task': 'welfare.tasks.generate_welfare_followups_task',
        'schedule': crontab(hour=5, minute=0),  # 08:00 EAT
    },
    'generate-welfare-followups-afternoon': {
        'task': 'welfare.tasks.generate_welfare_followups_task',
        'schedule': crontab(hour=10, minute=0),  # 13:00 EAT
    },
    # FR-FIN-011: Auto-send fee reminders (due soon, due today, overdue).
    # Runs daily at 08:00 EAT = 05:00 UTC.
    'send-fee-reminders-daily': {
        'task': 'finance.tasks.send_fee_reminders_task',
        'schedule': crontab(hour=5, minute=0),  # 08:00 EAT
    },
    # FR-CAL-004 / UAT ACD-003: Auto-advance term when end_date passes.
    # Runs daily at 06:00 EAT = 03:00 UTC.
    'check-and-advance-term-daily': {
        'task': 'academics.tasks.check_and_advance_term_task',
        'schedule': crontab(hour=3, minute=0),  # 06:00 EAT
    },
    # FR-LP-004: Auto-mark lesson plans as MISSING after deadline
    # Runs hourly Mon-Fri (08:00-17:00 EAT = 05:00-14:00 UTC)
    'mark-missing-lesson-plans-hourly': {
        'task': 'academics.tasks.mark_missing_lesson_plans_task',
        'schedule': crontab(hour='5-14', minute=0, day_of_week='1-5'),  # Mon-Fri hourly, 08:00-17:00 EAT
    },
    # NFR-DATA-002: Check archive retention policies daily and send warnings
    # 02:00 EAT = 23:00 UTC (previous day)
    'check-archive-retention-daily': {
        'task': 'core.tasks.check_archive_retention_task',
        'schedule': crontab(hour=23, minute=0),  # 02:00 EAT
    },
    # NFR-DATA-002: Prune archived records past retention (weekly, Sundays)
    # 01:00 EAT = 22:00 UTC (Saturday)
    'prune-archived-records-weekly': {
        'task': 'core.tasks.prune_archived_records_task',
        'schedule': crontab(hour=22, minute=0, day_of_week=6),  # Sunday 01:00 EAT
    },
    # NFR-PDPA-003: Weekly DSAR SLA compliance report (Mondays 07:00 EAT = 04:00 UTC)
    'check-dsar-sla-compliance-weekly': {
        'task': 'audit.tasks.check_dsar_sla_compliance_task',
        'schedule': crontab(hour=4, minute=0, day_of_week=1),  # Monday 07:00 EAT
    },
}

# Celery configuration
app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes hard limit
    task_soft_time_limit=25 * 60,  # 25 minutes soft limit
    worker_prefetch_multiplier=4,
    worker_max_tasks_per_child=1000,
)

@app.task(bind=True)
def debug_task(self):
    """Debug task for testing Celery setup"""
    print(f'Request: {self.request!r}')


@app.task(name='config.healthchecks_ping')
def healthchecks_ping():
    """Ping Healthchecks.io to confirm Celery beat is alive."""
    url = os.getenv('HEALTHCHECKS_PING_URL', '')
    if url:
        try:
            resp = requests.get(url, timeout=10)
            return f'pinged: {resp.status_code}'
        except Exception as e:
            return f'ping failed: {e}'
    return 'no HEALTHCHECKS_PING_URL configured'


app.conf.beat_schedule['healthchecks-ping'] = {
    'task': 'config.healthchecks_ping',
    'schedule': crontab(minute='*/1'),  # every 1 minute
}
