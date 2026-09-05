"""
Celery signal handlers for task failure alerting.

Connects to celery.signals.task_failure to send email (and optionally
Slack) alerts when any scheduled task fails unexpectedly.

FR-NFR-OPS-001: System health monitoring - failed task alerts.
"""
import logging
import traceback as tb_mod
from collections import defaultdict

from celery.signals import task_failure
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)

# Rate-limiting: max one alert per task name per 24 hours
_last_alerts = defaultdict(lambda: timezone.now() - timezone.timedelta(hours=24))


def _get_alert_recipients():
    """Return the list of admin emails to alert on task failure."""
    recipients = getattr(settings, 'TASK_FAILURE_ALERT_EMAILS', [])
    if not recipients:
        recipients = [email for _, email in getattr(settings, 'ADMINS', [])]
    return recipients


def _format_failure_email(task_id, task_name, exception, traceback_str):
    """Build subject and body for the failure alert email."""
    subject = f"[HODARI SMS] Task FAILED: {task_name}"
    body = (
        f"HODARI SMS - Scheduled Task Failure Alert\n"
        f"{'=' * 50}\n\n"
        f"Task name : {task_name}\n"
        f"Task ID   : {task_id}\n"
        f"Timestamp : {timezone.now().isoformat()}\n"
        f"Exception : {type(exception).__name__}: {exception}\n\n"
        f"Traceback:\n"
        f"{traceback_str}\n"
    )
    return subject, body


def _send_slack_alert(subject, body):
    """Send a Slack webhook notification if SLACK_WEBHOOK_URL is configured."""
    webhook_url = getattr(settings, 'SLACK_WEBHOOK_URL', '')
    if not webhook_url:
        return
    try:
        import json
        import urllib.request
        payload = json.dumps({'text': f'*{subject}*\n\n```{body[:2000]}```'}).encode('utf-8')
        req = urllib.request.Request(
            webhook_url, data=payload,
            headers={'Content-Type': 'application/json'}, method='POST'
        )
        urllib.request.urlopen(req, timeout=10)
        logger.info('Slack alert sent for task failure: %s', subject)
    except Exception as exc:
        logger.warning('Failed to send Slack alert: %s', exc)


def _send_email_alert(subject, body):
    """Send an email alert to configured recipients."""
    recipients = _get_alert_recipients()
    if not recipients:
        logger.warning(
            'No TASK_FAILURE_ALERT_EMAILS or ADMINS configured - '
            'skipping email alert for: %s', subject
        )
        return
    try:
        send_mail(
            subject=subject, message=body,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@hodari.ac.tz'),
            recipient_list=recipients, fail_silently=True,
        )
        logger.info('Email alert sent to %s for: %s', recipients, subject)
    except Exception as exc:
        logger.error('Failed to send email alert: %s', exc)


def on_task_failure(sender, task_id, exception, traceback, **kwargs):
    """
    Signal handler for celery.signals.task_failure.

    Rate-limited to max one alert per task name per 24 hours.
    """
    task_name = getattr(sender, 'name', None) or getattr(sender, '__name__', str(sender))

    # Rate-limit: max one alert per task per 24 hours
    now = timezone.now()
    if (now - _last_alerts[task_name]).total_seconds() < 86400:
        logger.debug('Rate-limited failure alert for %s', task_name)
        return
    _last_alerts[task_name] = now

    tb_str = ''.join(tb_mod.format_exception(type(exception), exception, traceback))

    logger.error(
        'Task failure detected: %s (id=%s) - %s: %s',
        task_name, task_id, type(exception).__name__, exception
    )

    subject, body = _format_failure_email(task_id, task_name, exception, tb_str)

    # Send alerts independently - failure in one channel must not block the other
    _send_email_alert(subject, body)
    _send_slack_alert(subject, body)


_registered = False


def register_failure_signals():
    """
    Connect the task_failure signal. Safe to call multiple times.
    Called once from config/celery.py after the Celery app is created.
    """
    global _registered
    if not _registered:
        task_failure.connect(on_task_failure)
        _registered = True
        logger.info('Celery task_failure signal handler registered')
