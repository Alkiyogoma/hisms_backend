from django.core.management.base import BaseCommand

from finance.reminder_service import process_fee_reminders
from users.models import User


class Command(BaseCommand):
    help = "Legacy alias — runs send_fee_reminders schedules."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        actor = User.objects.filter(is_superuser=True).first()
        counts = process_fee_reminders(dry_run=options["dry_run"], actor=actor)
        self.stdout.write(self.style.SUCCESS(f"Processed {sum(counts.values())} reminder(s)."))
