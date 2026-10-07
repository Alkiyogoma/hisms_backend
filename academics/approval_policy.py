"""
Who may approve grades.

Approving (and returning) submitted scores and signing off reports is limited
to Heads of Department, the Head of School and Super Admin. Teachers can see
the status of their own scores but never approve them, and nobody may approve
scores for a class they teach or scores they entered themselves.

Role checks here are deliberate: RoleRequiredMixin grants access by
permission alone, and teachers hold the view/change permissions they need for
score entry and comments.
"""
from django.core.exceptions import PermissionDenied

from academics.models import Department, GradeClass
from users.models import UserRole

SECTION_HEAD_ROLES = (UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD)
GRADE_APPROVER_ROLES = SECTION_HEAD_ROLES + (UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN)
# Only these may change a grade after HOD approval.
GRADE_AMENDER_ROLES = (UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN)


def can_approve_grades(user) -> bool:
    return bool(user and user.is_authenticated) and (
        user.is_superuser or user.has_role(*GRADE_APPROVER_ROLES)
    )


def can_amend_approved_grades(user) -> bool:
    return bool(user and user.is_authenticated) and (
        user.is_superuser or user.has_role(*GRADE_AMENDER_ROLES)
    )


def approver_class_names(user):
    """Class names whose scores ``user`` may approve, or None for every class."""
    if user.is_school_wide:
        return None
    depts = user.section_departments or [
        d for role, d in (
            (UserRole.PRIMARY_HOD, Department.PRIMARY),
            (UserRole.ECD_HOD, Department.ECD),
            (UserRole.LOWER_SECONDARY_HOD, Department.LOWER_SECONDARY),
        ) if user.has_role(role)
    ]
    return set(GradeClass.objects.filter(department__in=depts).values_list("name", flat=True))


def taught_class_names(user, term) -> set:
    """Classes ``user`` teaches (any subject, or as class teacher) in ``term``."""
    from hr.models import TeacherClassAssignment
    qs = TeacherClassAssignment.objects.filter(teacher__user=user)
    if term is not None:
        qs = qs.filter(term=term)
    return set(qs.values_list("grade_class__name", flat=True))


def approval_block_reason(user, score, taught=None) -> str:
    """Why ``user`` may not approve ``score`` ("" when they may)."""
    if not can_approve_grades(user):
        return "Only a Head of Department or the Head of School can approve grades."
    if score.entered_by_id == user.pk:
        return "You entered this score, so someone else must approve it."
    if taught is None:
        taught = taught_class_names(user, score.term)
    if score.student.class_name in taught:
        return f"You teach {score.student.class_name}, so another approver must approve its grades."
    return ""


class AllowedRolesEnforcedMixin:
    """Put before RoleRequiredMixin: also require one of ``allowed_roles``
    (RoleRequiredMixin alone lets anyone holding the permission through)."""

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if user.is_authenticated and not (user.is_superuser or user.has_role(*self.allowed_roles)):
            raise PermissionDenied("Only a Head of Department or the Head of School can review and approve grades.")
        return super().dispatch(request, *args, **kwargs)
