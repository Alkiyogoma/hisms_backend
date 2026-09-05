"""
Centralized utilities for academic year and term resolution.

All views and services should import ``get_current_term`` from here
instead of querying ``Term`` directly, to ensure consistent behaviour
across the codebase.
"""

from __future__ import annotations

from django.utils import timezone


def get_current_term():
    """
    Return the single authoritative "current" term.

    Resolution order:
      1. A term whose date range includes today **and** is unlocked
         (``Term.get_current()``).
      2. The most recently started unlocked term that belongs to the
         currently active academic year.
      3. The most recently started unlocked term regardless of year.
      4. ``None`` if no unlocked term exists at all.
    """
    from academics.models import Term

    # 1 — Date-range match
    term = Term.get_current()
    if term is not None:
        return term

    today = timezone.now().date()

    # 2 — Current academic year, most recently **ended** (covers gaps between terms)
    term = (
        Term.objects.filter(
            academic_year__is_current=True,
            is_locked=False,
            end_date__lte=today,
        )
        .order_by("-end_date")
        .first()
    )
    if term is not None:
        return term

    # 3 — Current academic year, next upcoming term (e.g. before a term has begun)
    term = (
        Term.objects.filter(
            academic_year__is_current=True,
            is_locked=False,
            start_date__gte=today,
        )
        .order_by("start_date")
        .first()
    )
    if term is not None:
        return term

    # 3b — Current academic year, most recently started (fallback)
    term = (
        Term.objects.filter(
            academic_year__is_current=True,
            is_locked=False,
        )
        .order_by("-start_date")
        .first()
    )
    if term is not None:
        return term

    # 4 — Any unlocked term, most recently started
    term = (
        Term.objects.filter(is_locked=False)
        .order_by("-start_date")
        .first()
    )
    return term


def get_current_academic_year():
    """Return the currently active academic year, or the latest one."""
    from academics.models import AcademicYear

    year = AcademicYear.objects.filter(is_current=True).first()
    if year is not None:
        return year
    return AcademicYear.objects.order_by("-name").first()


# ---------------------------------------------------------------------------
# Score entry window gating
# ---------------------------------------------------------------------------

_EXAM_WINDOW_MAP = {
    "quiz": (None, None),                      # quiz: no exam window restriction
    "QZ": (None, None),                        # alias — seed scripts may use abbreviation
    "mid_term": ("midterm_exam_start_date", "midterm_exam_end_date"),
    "MT": ("midterm_exam_start_date", "midterm_exam_end_date"),
    "end_of_term": ("endterm_exam_start_date", "endterm_exam_end_date"),
    "ET": ("endterm_exam_start_date", "endterm_exam_end_date"),
}

# Normalise abbreviated / legacy codes → canonical keys used by _EXAM_WINDOW_MAP
_CODE_TO_TYPE = {
    "QZ": "quiz",
    "MT": "mid_term",
    "ET": "end_of_term",
}


def check_score_entry_allowed(term, exam_type, today=None):
    """Return ``(allowed, message)`` for saving a score of *exam_type*.

    - Quiz: always allowed once the term has started.
    - Mid-Term / End-Term: soft-block (save returns warning) before the
      exam window opens; hard-block (submission rejects) before the window
      opens and past ``grading_deadline``.

    Returns:
        (True, None)  — entry allowed.
        (False, "…")  — entry blocked with a user-facing message.
    """
    if today is None:
        today = timezone.now().date()

    # Normalise abbreviated codes (QZ/MT/ET) to canonical form
    exam_type = _CODE_TO_TYPE.get(exam_type, exam_type)

    # Past grading deadline → hard block for all types
    if term.grading_deadline and today > term.grading_deadline:
        return False, "Grading deadline has passed. Contact the admin to reopen score entry."

    if exam_type == "quiz":
        if term.start_date and today < term.start_date:
            return False, "Term has not started yet."
        if term.quiz_end_date and today > term.quiz_end_date:
            return False, "Quiz period has ended."
        return True, None

    if exam_type not in _EXAM_WINDOW_MAP:
        return False, (
            f"Unknown exam type '{exam_type}'. "
            f"Contact the admin to configure this exam type."
        )
    field_start, field_end = _EXAM_WINDOW_MAP[exam_type]
    if not field_start:
        return True, None

    exam_start = getattr(term, field_start, None)
    exam_end = getattr(term, field_end, None)

    if not exam_start and not exam_end:
        return False, (
            f"No exam window configured for {exam_type.replace('_', ' ').title()}. "
            f"Contact the admin to set exam dates."
        )

    if exam_start and today < exam_start:
        return False, (
            f"Score entry for {exam_type.replace('_', '-').title()} "
            f"is not open yet. Opens on {exam_start}."
        )

    if exam_end and today > exam_end:
        return False, (
            f"{exam_type.replace('-', ' ').title()} exam window has closed."
        )

    return True, None
