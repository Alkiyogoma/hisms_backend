"""
Celery Tasks for Welfare module.

Periodic background tasks for:
- Auto-generating follow-up reminder notifications on overdue welfare cases
"""

from celery import shared_task
from django.core.management import call_command
import io
import logging

logger = logging.getLogger(__name__)


@shared_task
def generate_welfare_followups_task():
    """
    FR-WEL-003/WEL-006: Scan for welfare observations with overdue
    follow-up dates and generate in-app notifications + Task records.

    Runs on a schedule (daily at 08:00 EAT and 13:00 EAT) via Celery beat.
    Delegates to the ``generate_welfare_followups`` management command
    so the logic is reusable from CLI and from the periodic task.
    """
    out = io.StringIO()
    try:
        call_command("generate_welfare_followups", stdout=out)
        output = out.getvalue().strip()
        logger.info("generate_welfare_followups_task: %s", output)
        return output
    except Exception as exc:
        logger.error("generate_welfare_followups_task failed: %s", exc)
        raise
