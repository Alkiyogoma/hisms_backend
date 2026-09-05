from __future__ import annotations

from users.models import UserRole


def get_teacher_assigned_classes(user) -> set[str]:
    if getattr(user, "role", None) != UserRole.TEACHER:
        return set()
    from timetable.models import TimetableSlot

    classes: set[str] = {
        (name or "").strip()
        for name in TimetableSlot.objects.filter(teacher=user).values_list("class_name", flat=True).distinct()
        if (name or "").strip()
    }

    # Also include classes from TeacherClassAssignment (TCA) — teachers may
    # have class assignments without timetable slots yet. Using a union
    # so both sources contribute (not a fallback).
    from hr.models import TeacherClassAssignment

    # Try current term first, then all terms as fallback
    from academics.utils import get_current_term
    term = get_current_term()
    tca_qs = TeacherClassAssignment.objects.filter(
        teacher__user=user,
    ).select_related("grade_class")
    if term:
        tca_qs = tca_qs.filter(term=term)
    classes.update(
        a.grade_class.name.strip()
        for a in tca_qs
        if a.grade_class and a.grade_class.name and a.grade_class.name.strip()
    )

    return classes


def get_teacher_assigned_classes_from_tca(user) -> dict[str, dict]:
    """Return {class_name: {"subjects": {subject_name, ...}, "is_class_teacher": bool}}
    from TeacherClassAssignment (current term), falling back to all terms and
    timetable slots so teachers always see their classes.
    """
    if getattr(user, "role", None) != UserRole.TEACHER:
        return {}
    from academics.utils import get_current_term
    from hr.models import TeacherClassAssignment
    from timetable.models import TimetableSlot

    def _build_result(qs):
        result: dict[str, dict] = {}
        for a in qs:
            name = (a.grade_class.name or "").strip()
            if not name:
                continue
            if name not in result:
                result[name] = {"subjects": set(), "is_class_teacher": False}
            for subj in (a.subjects_taught or []):
                if subj and isinstance(subj, str):
                    result[name]["subjects"].add(subj.strip())
            if a.is_class_teacher:
                result[name]["is_class_teacher"] = True
        return result

    # 1. Try current term
    term = get_current_term()
    if term:
        assignments = TeacherClassAssignment.objects.filter(
            teacher__user=user, term=term
        ).select_related("grade_class")
        result = _build_result(assignments)
        if result:
            return result

    # 2. Current term had no TCA — try any term with assignments
    assignments = TeacherClassAssignment.objects.filter(
        teacher__user=user,
    ).select_related("grade_class").order_by("-term__start_date")
    result = _build_result(assignments)
    if result:
        return result

    # 3. No TCA at all — fall back to timetable slots (include subjects)
    from collections import defaultdict
    slot_classes = TimetableSlot.objects.filter(
        teacher=user,
    ).values("class_name", "subject_name").distinct()
    for row in slot_classes:
        cn = (row.get("class_name") or "").strip()
        sn = (row.get("subject_name") or "").strip()
        if not cn:
            continue
        if cn not in result:
            result[cn] = {"subjects": set(), "is_class_teacher": False}
        if sn:
            result[cn]["subjects"].add(sn)
    return result


def is_ecd_teacher(user) -> bool:
    if getattr(user, "role", None) != UserRole.TEACHER:
        return False

    # Primary signal: StaffProfile.department (set at user creation)
    from hr.models import StaffProfile

    profile = StaffProfile.objects.filter(user=user).values_list("department", flat=True).first()
    if profile == "ECD":
        return True

    # Fallback: check timetable class names for ECD department or class-name heuristics
    from academics.ecd_utils import ecd_template_type_from_class_name
    from academics.models import Department, GradeClass

    classes = get_teacher_assigned_classes(user)
    if not classes:
        return False
    if GradeClass.objects.filter(name__in=classes, department=Department.ECD).exists():
        return True
    return any(ecd_template_type_from_class_name(c) is not None for c in classes)

