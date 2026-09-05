"""
Core business-logic helpers shared across the HODARI codebase.

Keeping these in a utility module avoids circular imports between
core.views and attendance.tasks (or other apps that need the same logic).
"""

from datetime import date as _date
from django.db.models import Q
from django.utils import timezone
from events.models import CalendarEvent


def is_school_day(target_date=None):
    """Return True if target_date is a school day (weekend + holiday guard).

    FR-ATT-006: Unconfirmed alerts must only appear on school days.
    A day is considered a school day when:
      1. It falls on a weekday (Mon-Fri), AND
      2. No CalendarEvent with is_public_holiday=True or
         category='holiday' covers that date.

    When settings.BYPASS_SCHOOL_DAY_CHECK is True, always returns True
    (useful for testing on weekends/holidays).
    """
    from django.conf import settings
    if getattr(settings, 'BYPASS_SCHOOL_DAY_CHECK', False):
        return True

    if target_date is None:
        target_date = _date.today()

    if target_date.weekday() >= 5:
        return False

    holiday_q = Q(
        start_date__lte=target_date,
        is_published=True,
    ) & (
        Q(end_date__gte=target_date) |
        (Q(end_date__isnull=True) & Q(start_date=target_date))
    ) & (
        Q(is_public_holiday=True) | Q(category="holiday")
    )

    if CalendarEvent.objects.filter(holiday_q).exists():
        return False

    return True


def get_client_ip(request):
    """Extract the originating client IP from an HTTP request.

    Checks X-Forwarded-For first (taking the first, most-originating IP),
    then falls back to REMOTE_ADDR. Returns None if neither is present.
    """
    if request is None:
        return None
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        for part in x_forwarded_for.split(","):
            stripped = part.strip()
            if stripped:
                return stripped
    return request.META.get("REMOTE_ADDR")


def append_review_comment(existing_notes, author, comment_text):
    """Append a timestamped comment to an incident's review notes."""
    stamp = timezone.localtime().strftime("%d %b %Y, %H:%M")
    name = author.get_full_name() or author.username
    entry = f"[{stamp} — {name}]\n{comment_text.strip()}"
    if existing_notes and existing_notes.strip():
        return f"{existing_notes.rstrip()}\n\n{entry}"
    return entry
