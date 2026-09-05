from __future__ import annotations
from django.db import transaction
from timetable.models import TimetableSlot

@transaction.atomic
def copy_timetable_term(from_term_id: int, to_term_id: int, actor) -> int:
    """
    Copies all timetable slots from one term to another.
    Phase 1: Simple clone of all slots with full validation.
    """
    slots = TimetableSlot.objects.filter(term_id=from_term_id)
    new_slots = []
    for slot in slots:
        new_slot = TimetableSlot(
            term_id=to_term_id,
            class_name=slot.class_name,
            subject_name=slot.subject_name,
            teacher_id=slot.teacher_id,
            day_of_week=slot.day_of_week,
            start_time=slot.start_time,
            end_time=slot.end_time,
            room=slot.room,
        )
        new_slot.full_clean()
        new_slots.append(new_slot)

    TimetableSlot.objects.bulk_create(new_slots)

    from audit.models import log_event
    log_event(
        actor=actor,
        action_type="TIMETABLE_COPIED",
        model_name="TimetableSlot",
        object_id=to_term_id,
        description=f"Copied {len(new_slots)} timetable slots from Term {from_term_id} to Term {to_term_id}"
    )

    return len(new_slots)
