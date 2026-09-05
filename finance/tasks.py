"""
Celery Tasks for Finance module.

Periodic background tasks for:
- Auto-sending scheduled fee reminders (due soon, due today, overdue)
"""

from celery import shared_task
from django.core.management import call_command
import io
import logging

logger = logging.getLogger(__name__)


@shared_task
def send_fee_reminders_task():
    """
    FR-FIN-011/FIN-012: Evaluate all open invoices and send scheduled
    parent reminders (due_soon, due_today, overdue_7, overdue_14).

    Runs daily at 08:00 EAT via Celery beat.
    Delegates to the ``send_fee_reminders`` management command
    so the logic is reusable from CLI and from the periodic task.
    """
    out = io.StringIO()
    try:
        call_command("send_fee_reminders", stdout=out)
        output = out.getvalue().strip()
        logger.info("send_fee_reminders_task: %s", output)
        return output
    except Exception as exc:
        logger.error("send_fee_reminders_task failed: %s", exc)
        raise
