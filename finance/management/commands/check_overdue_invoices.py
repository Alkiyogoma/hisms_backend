"""
FR-FIN-012: Daily command to flag overdue invoices and send reminders.

Run via Celery beat at 06:00 UTC (09:00 EAT) each school day:
    python manage.py check_overdue_invoices
"""

import logging
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "FR-FIN-012: Flag overdue invoices and send 7-day reminders."

    def handle(self, *args, **options):
        from finance.services import check_overdue_invoices

        try:
            count = check_overdue_invoices()
            self.stdout.write(self.style.SUCCESS(
                f"Overdue invoice check complete. {count} invoice(s) flagged as overdue."
            ))
        except Exception as exc:
            logger.error("check_overdue_invoices command failed: %s", exc)
            self.stderr.write(self.style.ERROR(f"Error: {exc}"))
