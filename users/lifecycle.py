"""Shared account handling for reversible directory archival."""
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from users.models import UserRole


def invalidate_user_sessions(user_id):
    for session in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
        if session.get_decoded().get("_auth_user_id") == str(user_id):
            session.delete()


def check_staff_scope(actor, target):
    if target.role == UserRole.PARENT:
        raise PermissionDenied("Manage parent accounts through the Parent Directory.")
    if (target.is_superuser or target.has_role(UserRole.SUPER_ADMIN)) and not (
        actor.is_superuser or actor.has_role(UserRole.SUPER_ADMIN)
    ):
        raise PermissionDenied("Only a Super Admin can manage a Super Admin account.")
    if not actor.is_school_wide and actor.section_departments:
        if not target.__class__.objects.filter(
            staff_profile__department__in=actor.section_departments, pk=target.pk,
        ).exists():
            raise PermissionDenied("This staff member is outside your departments.")
