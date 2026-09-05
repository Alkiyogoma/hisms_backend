from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from django.db.models import Q
from events.models import CalendarEvent
from communications.email_service import dispatch_notification
from users.models import User, UserRole

class Command(BaseCommand):
    help = "Send event reminders 2 days before"

    def handle(self, *args, **options):
        target_date = timezone.now().date() + timedelta(days=2)
        events = CalendarEvent.objects.filter(
            start_date=target_date,
            is_published=True,
            is_archived=False,
            reminder_sent=False
        )
        
        count = 0
        for event in events:
            # FR-CAL-006/007: Target audience — build queryset correctly
            if event.notify_parents and event.notify_staff:
                users = User.objects.filter(is_active=True)
            elif event.notify_parents:
                users = User.objects.filter(is_active=True, role=UserRole.PARENT)
            elif event.notify_staff:
                users = User.objects.filter(is_active=True).exclude(role=UserRole.PARENT)
            else:
                users = User.objects.none()
            
            msg = f"Reminder: {event.title} is happening in 2 days on {event.start_date}. {event.description[:100]}"
            
            for user in users:
                dispatch_notification(
                    user=user,
                    title=f"Event Reminder: {event.title}",
                    message=msg,
                    link="/events/",
                    actor=None
                )
            
            event.reminder_sent = True
            event.save(update_fields=['reminder_sent'])
            count += 1
            
        self.stdout.write(self.style.SUCCESS(f"Sent reminders for {count} events."))
