"""
Celery Tasks for Events module.

Periodic background tasks for:
- Sending 2-day event reminders
- Dispatching event notifications asynchronously
"""

from celery import shared_task
from django.core.management import call_command
import io
import logging

logger = logging.getLogger(__name__)


@shared_task
def send_event_notifications_task(event_id):
    """
    Dispatch in-app + email notifications for a created/updated event.
    Runs asynchronously so the HTTP response returns immediately.
    """
    from events.models import CalendarEvent
    from communications.email_service import dispatch_notification
    from users.models import User, UserRole

    try:
        event = CalendarEvent.objects.get(pk=event_id)
    except CalendarEvent.DoesNotExist:
        logger.error("send_event_notifications_task: event %s not found", event_id)
        return

    if event.notify_staff:
        staff = User.objects.exclude(role=UserRole.PARENT).filter(is_active=True)
        for s in staff:
            dispatch_notification(
                user=s,
                title=f"New School Event: {event.title}",
                message=f"Event: {event.title}\nDate: {event.start_date}\nCategory: {event.category}",
                link="/events/",
            )

    if event.notify_parents:
        parents = User.objects.filter(role=UserRole.PARENT, is_active=True)
        for p in parents:
            dispatch_notification(
                user=p,
                title=f"New School Event: {event.title}",
                message=f"Event: {event.title}\nDate: {event.start_date}\nCategory: {event.category}",
                link="/events/",
            )


@shared_task
def send_event_notifications_update_task(event_id, date_changed=False):
    """
    Dispatch notifications for an updated event (only if date changed).
    """
    from events.models import CalendarEvent
    from communications.email_service import dispatch_notification
    from users.models import User, UserRole

    try:
        event = CalendarEvent.objects.get(pk=event_id)
    except CalendarEvent.DoesNotExist:
        logger.error("send_event_notifications_update_task: event %s not found", event_id)
        return

    suffix = " (date changed)" if date_changed else ""

    if event.notify_staff:
        staff = User.objects.exclude(role=UserRole.PARENT).filter(is_active=True)
        for s in staff:
            dispatch_notification(
                user=s,
                title=f"Updated School Event: {event.title}{suffix}",
                message=f"Updated event: {event.title}\nDate: {event.start_date}\nCategory: {event.category}",
                link="/events/",
            )

    if event.notify_parents:
        parents = User.objects.filter(role=UserRole.PARENT, is_active=True)
        for p in parents:
            dispatch_notification(
                user=p,
                title=f"Updated School Event: {event.title}{suffix}",
                message=f"Updated event: {event.title}\nDate: {event.start_date}\nCategory: {event.category}",
                link="/events/",
            )


@shared_task
def send_event_cancellation_task(event_title, event_start_date, event_category):
    """
    Dispatch cancellation notifications for a deleted event.
    Called with event data directly since the event is deleted before this runs.
    """
    from communications.email_service import dispatch_notification
    from users.models import User, UserRole

    if event_title:
        staff = User.objects.exclude(role=UserRole.PARENT).filter(is_active=True)
        for s in staff:
            dispatch_notification(
                user=s,
                title=f"Event Cancelled: {event_title}",
                message=f"The event '{event_title}' scheduled for {event_start_date} has been cancelled.",
                link="/events/",
            )
        parents = User.objects.filter(role=UserRole.PARENT, is_active=True)
        for p in parents:
            dispatch_notification(
                user=p,
                title=f"Event Cancelled: {event_title}",
                message=f"The event '{event_title}' scheduled for {event_start_date} has been cancelled.",
                link="/events/",
            )


@shared_task
def send_event_reminders_task():
    """
    CAL-003: Send event reminders 2 days before each published event.

    Runs daily at 08:45 EAT via Celery beat.
    Delegates to the ``send_event_reminders`` management command
    so the logic is reusable from CLI and from the periodic task.
    """
    out = io.StringIO()
    try:
        call_command("send_event_reminders", stdout=out)
        output = out.getvalue().strip()
        logger.info("send_event_reminders_task: %s", output)
        return output
    except Exception as exc:
        logger.error("send_event_reminders_task failed: %s", exc)
        raise
