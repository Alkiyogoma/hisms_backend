"""
Who may read which welfare note. One set of rules, used by the welfare module
and by the student profile, so a note is never more visible in one place than
the other.

- Safeguarding notes: Safeguarding Lead (``welfare.view_safeguarding_note``)
  and Head of School only. Not HODs, not class teachers, not the author once
  submitted (the author keeps a content-free view to add follow-ups).
- Concerns, and any note tagged Health: the child's class teacher, the HOD of
  the child's department, and leadership (HOS / Super Admin). The author
  always keeps access to their own note.
- Positive notes and observations: anyone with ``welfare.view_welfareobservation``
  who teaches the child, HODs of the department, leadership, and the author.
"""
from django.db.models import Q

from users.models import UserRole

from .models import WelfareConcernType, WelfareNoteType

LEADERSHIP_ROLES = {UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL}


def _hod_department(user):
    from academics.models import Department
    return {
        UserRole.ECD_HOD: Department.ECD,
        UserRole.PRIMARY_HOD: Department.PRIMARY,
        UserRole.LOWER_SECONDARY_HOD: Department.LOWER_SECONDARY,
    }.get(user.role)


def can_view_safeguarding(user):
    return (
        user.is_authenticated
        and (user.role == UserRole.HEAD_OF_SCHOOL or user.has_perm("welfare.view_safeguarding_note"))
    )


def is_leadership(user):
    return user.role in LEADERSHIP_ROLES or user.is_superuser


def class_teacher_classes(user):
    """Classes this user is class teacher of (current term, else any term)."""
    if user.role != UserRole.TEACHER:
        return set()
    from academics.utils import get_current_term
    from hr.models import TeacherClassAssignment

    qs = TeacherClassAssignment.objects.filter(teacher__user=user, is_class_teacher=True)
    term = get_current_term()
    if term and qs.filter(term=term).exists():
        qs = qs.filter(term=term)
    return {n.strip() for n in qs.values_list("grade_class__name", flat=True) if n}


def _sensitive_q():
    return Q(note_type=WelfareNoteType.CONCERN) | Q(concern_type=WelfareConcernType.HEALTH)


def visible_observations(user, qs):
    """Restrict a WelfareObservation queryset to the notes ``user`` may read."""
    if not user.is_authenticated:
        return qs.none()

    safeguarding = Q(note_type=WelfareNoteType.SAFEGUARDING)
    if can_view_safeguarding(user):
        allowed_safeguarding = safeguarding
    else:
        allowed_safeguarding = Q(pk__in=[])
    not_safeguarding = ~safeguarding

    if is_leadership(user):
        return qs.filter(not_safeguarding | allowed_safeguarding)

    if not user.has_perm("welfare.view_welfareobservation"):
        return qs.filter(allowed_safeguarding)

    own = Q(submitted_by=user) & not_safeguarding
    sensitive = _sensitive_q()

    dept = _hod_department(user)
    if dept:
        from academics.models import GradeClass
        dept_classes = GradeClass.objects.filter(department=dept).values_list("name", flat=True)
        in_dept = Q(student__class_name__in=list(dept_classes)) & not_safeguarding
        return qs.filter(in_dept | own | allowed_safeguarding)

    if user.role == UserRole.TEACHER:
        from core.teacher_context import get_teacher_assigned_classes
        taught = list(get_teacher_assigned_classes(user))
        homeroom = list(class_teacher_classes(user))
        everyday = Q(student__class_name__in=taught) & not_safeguarding & ~sensitive
        as_class_teacher = Q(student__class_name__in=homeroom) & not_safeguarding
        return qs.filter(everyday | as_class_teacher | own | allowed_safeguarding)

    # Any other role granted welfare access (e.g. Admin Officer): everyday
    # notes only — never concerns, health or safeguarding.
    return qs.filter((not_safeguarding & ~sensitive) | own | allowed_safeguarding)


def can_view_observation(user, obs):
    """Object-level check matching ``visible_observations``. Drafts are
    visible to their author only."""
    from .models import WelfareObservation
    if obs.is_draft:
        return obs.submitted_by_id == user.pk
    return visible_observations(
        user, WelfareObservation.all_objects.filter(pk=obs.pk)
    ).exists()
