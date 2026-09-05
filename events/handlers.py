from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from academics.models import Term
from events.models import CalendarEvent, EventCategory
from ptc.models import PTCWindow, TermSlot


# ---------------------------------------------------------------------------
# PTC Window -> CalendarEvent
# ---------------------------------------------------------------------------

@receiver(post_save, sender=PTCWindow)
def create_ptc_calendar_event(sender, instance, **kwargs):
    """Auto-create a CalendarEvent when a PTCWindow is saved."""
    if not instance.ptc_date:
        return

    term_label = dict(TermSlot.choices).get(instance.term_slot, instance.term_slot)

    CalendarEvent.objects.update_or_create(
        source_model="ptc",
        source_id=instance.id,
        defaults={
            "title": f"Parent-Teacher Conference ({term_label})",
            "category": EventCategory.ACADEMIC,
            "description": (
                f"Parent-Teacher Conference for {term_label} "
                f"of {instance.academic_year.name}."
            ),
            "start_date": instance.ptc_date,
            "end_date": instance.ptc_date,
            "is_public_holiday": False,
            "notify_parents": True,
            "notify_staff": True,
            "is_published": instance.is_published,
            "auto_generated": True,
        },
    )


@receiver(post_delete, sender=PTCWindow)
def delete_ptc_calendar_event(sender, instance, **kwargs):
    """Remove the auto-generated CalendarEvent when a PTCWindow is deleted."""
    CalendarEvent.objects.filter(
        source_model="ptc", source_id=instance.id, auto_generated=True,
    ).delete()


# ---------------------------------------------------------------------------
# Term -> CalendarEvent (start, end, exam period)
# ---------------------------------------------------------------------------

@receiver(post_save, sender=Term)
def create_term_calendar_events(sender, instance, **kwargs):
    """Auto-create CalendarEvents for term start, end, and exam period."""
    year_name = instance.academic_year.name

    if instance.start_date:
        CalendarEvent.objects.update_or_create(
            source_model="term_start",
            source_id=instance.id,
            defaults={
                "title": f"{instance.name} Begins",
                "category": EventCategory.ACADEMIC,
                "description": f"Start of {instance.name} ({year_name}).",
                "start_date": instance.start_date,
                "end_date": instance.start_date,
                "is_public_holiday": False,
                "notify_parents": True,
                "notify_staff": True,
                "is_published": True,
                "auto_generated": True,
            },
        )
    else:
        CalendarEvent.objects.filter(
            source_model="term_start",
            source_id=instance.id,
            auto_generated=True,
        ).delete()

    if instance.end_date:
        CalendarEvent.objects.update_or_create(
            source_model="term_end",
            source_id=instance.id,
            defaults={
                "title": f"{instance.name} Ends",
                "category": EventCategory.ACADEMIC,
                "description": f"End of {instance.name} ({year_name}).",
                "start_date": instance.end_date,
                "end_date": instance.end_date,
                "is_public_holiday": False,
                "notify_parents": True,
                "notify_staff": True,
                "is_published": True,
                "auto_generated": True,
            },
        )
    else:
        CalendarEvent.objects.filter(
            source_model="term_end",
            source_id=instance.id,
            auto_generated=True,
        ).delete()

    # Mid-Term exam period
    if instance.midterm_exam_start_date and instance.midterm_exam_end_date:
        CalendarEvent.objects.update_or_create(
            source_model="term_midterm_exam",
            source_id=instance.id,
            defaults={
                "title": f"Mid-Term Exams - {instance.name}",
                "category": EventCategory.ACADEMIC,
                "description": (
                    f"Mid-term examination period for {instance.name} ({year_name})."
                ),
                "start_date": instance.midterm_exam_start_date,
                "end_date": instance.midterm_exam_end_date,
                "is_public_holiday": False,
                "notify_parents": True,
                "notify_staff": True,
                "is_published": True,
                "auto_generated": True,
            },
        )
    else:
        CalendarEvent.objects.filter(
            source_model="term_midterm_exam",
            source_id=instance.id,
            auto_generated=True,
        ).delete()

    # Quiz period
    if instance.quiz_start_date and instance.quiz_end_date:
        CalendarEvent.objects.update_or_create(
            source_model="term_quiz",
            source_id=instance.id,
            defaults={
                "title": f"Quiz Period - {instance.name}",
                "category": EventCategory.ACADEMIC,
                "description": (
                    f"Quiz period for {instance.name} ({year_name})."
                ),
                "start_date": instance.quiz_start_date,
                "end_date": instance.quiz_end_date,
                "is_public_holiday": False,
                "notify_parents": True,
                "notify_staff": True,
                "is_published": True,
                "auto_generated": True,
            },
        )
    else:
        CalendarEvent.objects.filter(
            source_model="term_quiz",
            source_id=instance.id,
            auto_generated=True,
        ).delete()

    # End-Term exam period
    if instance.endterm_exam_start_date and instance.endterm_exam_end_date:
        CalendarEvent.objects.update_or_create(
            source_model="term_endterm_exam",
            source_id=instance.id,
            defaults={
                "title": f"End-Term Exams - {instance.name}",
                "category": EventCategory.ACADEMIC,
                "description": (
                    f"End-term examination period for {instance.name} ({year_name})."
                ),
                "start_date": instance.endterm_exam_start_date,
                "end_date": instance.endterm_exam_end_date,
                "is_public_holiday": False,
                "notify_parents": True,
                "notify_staff": True,
                "is_published": True,
                "auto_generated": True,
            },
        )
    else:
        CalendarEvent.objects.filter(
            source_model="term_endterm_exam",
            source_id=instance.id,
            auto_generated=True,
        ).delete()

    # Legacy term_exam events — clean up if deprecated fields are empty
    if instance.exam_start_date and instance.exam_end_date:
        CalendarEvent.objects.update_or_create(
            source_model="term_exam",
            source_id=instance.id,
            defaults={
                "title": f"Exams - {instance.name}",
                "category": EventCategory.ACADEMIC,
                "description": (
                    f"Examination period for {instance.name} ({year_name})."
                ),
                "start_date": instance.exam_start_date,
                "end_date": instance.exam_end_date,
                "is_public_holiday": False,
                "notify_parents": True,
                "notify_staff": True,
                "is_published": True,
                "auto_generated": True,
            },
        )
    else:
        CalendarEvent.objects.filter(
            source_model="term_exam",
            source_id=instance.id,
            auto_generated=True,
        ).delete()


@receiver(post_delete, sender=Term)
def delete_term_calendar_events(sender, instance, **kwargs):
    """Remove auto-generated CalendarEvents when a Term is deleted."""
    CalendarEvent.objects.filter(
        source_model__in=(
            "term_start", "term_end",
            "term_midterm_exam", "term_endterm_exam",
            "term_quiz",
            "term_exam",  # legacy
        ),
        source_id=instance.id,
        auto_generated=True,
    ).delete()
