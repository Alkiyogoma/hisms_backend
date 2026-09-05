from django.core.management.base import BaseCommand

from finance.reminder_service import process_fee_reminders
from users.models import User


class Command(BaseCommand):
    help = "Send scheduled parent fee reminders (due soon, due today, overdue 7/14 days)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many reminders would be sent without sending.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        actor = User.objects.filter(is_superuser=True).first()
        counts = process_fee_reminders(dry_run=dry_run, actor=actor)
        total = sum(counts.values())
        label = "Would send" if dry_run else "Sent"
        for key, n in counts.items():
            if n:
                self.stdout.write(f"  {key}: {n}")
        self.stdout.write(self.style.SUCCESS(f"{label} {total} fee reminder(s) total."))
