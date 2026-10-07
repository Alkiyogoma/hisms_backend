"""
Attendance marking window.

Attendance can only be marked for today, between 00:00 and 18:00 local time.
After that the register is locked: only a super admin may change a record
(every such change is audited), and anyone else must request a correction.
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from users.models import UserRole


def lock_hour() -> int:
    return getattr(settings, "ATTENDANCE_LOCK_HOUR", 18)


class AttendanceWindowError(PermissionDenied):
    """Raised when a change falls outside the marking window."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code  # "future" or "locked"


def is_super_admin(user) -> bool:
    return bool(user and getattr(user, "is_authenticated", False) and user.role == UserRole.SUPER_ADMIN)


def is_register_locked(date, now=None) -> bool:
    """True once the register for ``date`` is closed to normal marking."""
    now = timezone.localtime(now) if now else timezone.localtime()
    today = now.date()
    return date < today or (date == today and now.hour >= lock_hour())


def check_can_modify(actor, date, now=None) -> bool:
    """Raise AttendanceWindowError unless ``actor`` may mark/change ``date``.

    Returns True when the change is a post-lock super-admin override (so the
    caller can record it as a correction), False for a normal in-window mark.
    """
    now = timezone.localtime(now) if now else timezone.localtime()
    if date > now.date():
        raise AttendanceWindowError("Attendance cannot be marked for a future date.", "future")
    if is_register_locked(date, now):
        if is_super_admin(actor):
            return True
        raise AttendanceWindowError(
            f"The register is locked (attendance can only be marked on the day, before "
            f"{lock_hour():02d}:00). Request a correction from the super admin.",
            "locked",
        )
    return False


# Excusal reasons are often medical, so only these roles (and whoever recorded
# the excusal) may read them. Everything else in the module is open to all
# staff. Reasons are never printed.
EXCUSE_REASON_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.HEAD_OF_SCHOOL,
    UserRole.PRIMARY_HOD,
    UserRole.ECD_HOD,
    UserRole.LOWER_SECONDARY_HOD,
    UserRole.ADMIN_OFFICER,
}


def can_view_excuse_reasons(user) -> bool:
    return bool(user and getattr(user, "is_authenticated", False) and user.role in EXCUSE_REASON_ROLES)


def can_view_excuse_reason(user, entry) -> bool:
    return can_view_excuse_reasons(user) or bool(entry and entry.marked_by_id == getattr(user, "pk", None))
