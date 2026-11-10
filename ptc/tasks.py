"""
Celery Tasks for PTC (Learner Progress Report) module.

Periodic background tasks for:
- Sending PTC notification window open and 3-day reminder notifications
- Locking enrichment grades after term end
"""

from celery import shared_task
from django.core.management import call_command
import io
import logging

logger = logging.getLogger(__name__)


@shared_task
def send_ptc_reminders_task():
    """
    FR-PTC-003 to FR-PTC-005: Send PTC notification/reminder notifications.

    Runs daily at 08:00 EAT via Celery beat.
    Delegates to the ``send_ptc_reminders`` management command
    so the logic is reusable from CLI and from the periodic task.
    """
    out = io.StringIO()
    try:
        call_command("send_ptc_reminders", stdout=out)
        output = out.getvalue().strip()
        logger.info("send_ptc_reminders_task: %s", output)
        return output
    except Exception as exc:
        logger.error("send_ptc_reminders_task failed: %s", exc)
        raise
