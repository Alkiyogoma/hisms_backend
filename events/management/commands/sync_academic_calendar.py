from django.core.management.base import BaseCommand

from academics.models import Term
from events.handlers import create_ptc_calendar_event, create_term_calendar_events
from ptc.models import PTCWindow


class Command(BaseCommand):
    help = "Backfill CalendarEvent entries from existing PTCWindow and Term data."

    def handle(self, *args, **options):
        count = 0

        for ptc in PTCWindow.objects.all():
            create_ptc_calendar_event(PTCWindow, ptc)
            count += 1

        for term in Term.objects.all():
            create_term_calendar_events(Term, term)
            count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Synced {count} objects to CalendarEvent."
        ))
