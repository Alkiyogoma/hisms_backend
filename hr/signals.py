"""
Signal handlers for automatic assignment rollover.

When a new Term is created, all TeacherClassAssignment records marked
repeat_across_terms=True from the most recent prior term are copied to
the new term. Conflicts (inactive teachers, missing classes, subject
duplicates, contract expiry) are surfaced as RolloverConflict records
for admin review — never silently dropped or forced through.
"""

import logging
from datetime import timezone as tz

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _find_previous_term(new_term):
    """Return the most recent term that ended before new_term started."""
    from academics.models import Term
    if not new_term.start_date:
        return None
    return (
        Term.objects.filter(end_date__lt=new_term.start_date)
        .exclude(pk=new_term.pk)
        .order_by("-end_date")
        .first()
    )


def _rollover_assignments(new_term):
    """
    Copy repeat_across_terms assignments from the previous term to new_term.
    Each assignment is run through conflict detection. Conflicts become
    RolloverConflict records; clean assignments are created immediately.
    """
    from hr.models import TeacherClassAssignment, RolloverConflict
    from academics.models import GradeClass

    prev_term = _find_previous_term(new_term)
    if not prev_term:
        logger.info("No previous term found for %s — skipping rollover.", new_term.name)
        return

    source_assignments = TeacherClassAssignment.objects.filter(
        term=prev_term,
        repeat_across_terms=True,
    ).select_related("teacher", "grade_class")

    if not source_assignments.exists():
        logger.info("No repeat assignments in %s — skipping rollover.", prev_term.name)
        return

    created_count = 0
    conflict_count = 0

    for src in source_assignments:
        teacher = src.teacher
        gc = src.grade_class

        # --- Conflict check 1: teacher inactive ---
        if not teacher.is_active:
            RolloverConflict.objects.create(
                source_assignment=src,
                target_term=new_term,
                teacher=teacher,
                grade_class=gc,
                conflict_type="teacher_inactive",
                conflict_detail=(
                    f"{teacher.full_name} is no longer active. "
                    f"Assignment to {gc.name} was not rolled over."
                ),
                subjects_taught=src.subjects_taught,
                is_class_teacher=src.is_class_teacher,
            )
            conflict_count += 1
            continue

        # --- Conflict check 2: class no longer exists ---
        if not GradeClass.objects.filter(pk=gc.pk).exists():
            RolloverConflict.objects.create(
                source_assignment=src,
                target_term=new_term,
                teacher=teacher,
                grade_class=gc,
                conflict_type="class_missing",
                conflict_detail=(
                    f"Class {gc.name} no longer exists. "
                    f"Assignment for {teacher.full_name} was not rolled over."
                ),
                subjects_taught=src.subjects_taught,
                is_class_teacher=src.is_class_teacher,
            )
            conflict_count += 1
            continue

        # --- Conflict check 3: contract expired ---
        if teacher.contract_end_date and new_term.end_date:
            if teacher.contract_end_date < new_term.start_date:
                RolloverConflict.objects.create(
                    source_assignment=src,
                    target_term=new_term,
                    teacher=teacher,
                    grade_class=gc,
                    conflict_type="contract_expired",
                    conflict_detail=(
                        f"{teacher.full_name}'s contract expired on "
                        f"{teacher.contract_end_date.strftime('%b %d, %Y')}, "
                        f"before {new_term.name} starts on "
                        f"{new_term.start_date.strftime('%b %d, %Y')}."
                    ),
                    subjects_taught=src.subjects_taught,
                    is_class_teacher=src.is_class_teacher,
                )
                conflict_count += 1
                continue

        # --- Conflict check 4: subject/class conflict with existing assignment ---
        # Inline check (check_assignment_conflicts uses __contains which fails on SQLite)
        subject_conflict_found = False
        subject_conflict_detail = ""
        for subject_name in (src.subjects_taught or []):
            conflicting = TeacherClassAssignment.objects.filter(
                term=new_term, grade_class=gc,
            ).exclude(teacher=teacher)
            for existing in conflicting:
                if subject_name in (existing.subjects_taught or []):
                    subject_conflict_found = True
                    subject_conflict_detail = (
                        f"{existing.teacher.full_name} is already assigned to teach "
                        f"{subject_name} in {gc.name} for {new_term.name}."
                    )
                    break
            if subject_conflict_found:
                break
        if subject_conflict_found:
            RolloverConflict.objects.create(
                source_assignment=src,
                target_term=new_term,
                teacher=teacher,
                grade_class=gc,
                conflict_type="subject_conflict",
                conflict_detail=subject_conflict_detail,
                subjects_taught=src.subjects_taught,
                is_class_teacher=src.is_class_teacher,
            )
            conflict_count += 1
            continue

        # --- Conflict check 5: class teacher conflict ---
        if src.is_class_teacher:
            existing_ct = TeacherClassAssignment.objects.filter(
                teacher=teacher, term=new_term, is_class_teacher=True,
            ).exists()
            if existing_ct:
                RolloverConflict.objects.create(
                    source_assignment=src,
                    target_term=new_term,
                    teacher=teacher,
                    grade_class=gc,
                    conflict_type="class_teacher_conflict",
                    conflict_detail=(
                        f"{teacher.full_name} is already a class teacher "
                        f"for another class in {new_term.name}."
                    ),
                    subjects_taught=src.subjects_taught,
                    is_class_teacher=True,
                )
                conflict_count += 1
                continue

        # --- No conflicts: create the assignment ---
        TeacherClassAssignment.objects.create(
            teacher=teacher,
            term=new_term,
            grade_class=gc,
            subjects_taught=src.subjects_taught,
            is_class_teacher=src.is_class_teacher,
            is_assistant_class_teacher=src.is_assistant_class_teacher,
            repeat_across_terms=True,
        )
        created_count += 1

    logger.info(
        "Rollover for %s: %d created, %d conflicts from %d source assignments.",
        new_term.name, created_count, conflict_count, source_assignments.count(),
    )


@receiver(post_save, sender="academics.Term")
def trigger_assignment_rollover(sender, instance, created, **kwargs):
    """
    When a new Term is saved for the first time, automatically roll over
    all repeat_across_terms assignments from the previous term.
    """
    if not created:
        return
    # Defer to after the transaction commits to avoid partial writes
    transaction.on_commit(lambda: _rollover_assignments(instance))
