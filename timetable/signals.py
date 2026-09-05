from django.db.models.signals import post_save
from django.dispatch import receiver

from timetable.models import TimetableSlot


@receiver(post_save, sender=TimetableSlot)
def notify_teacher_timetable_change(sender, instance, created, **kwargs):
    """Automatically notify the assigned teacher when a timetable slot is
    created or updated."""
    from communications.email_service import dispatch_notification

    day_label = instance.get_day_of_week_display()
    time_range = f"{instance.start_time:%H:%M}–{instance.end_time:%H:%M}"

    if created:
        title = "New timetable slot assigned"
        message = (
            f"You have been assigned to teach {instance.subject_name} "
            f"in {instance.class_name} on {day_label} at {time_range}."
        )
    else:
        title = "Timetable slot updated"
        message = (
            f"Your timetable has been updated: {instance.subject_name} "
            f"in {instance.class_name} on {day_label} at {time_range}."
        )

    dispatch_notification(
        user=instance.teacher,
        title=title,
        message=message,
        link="/timetable/",
        actor=None,
    )
