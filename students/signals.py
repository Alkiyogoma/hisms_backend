from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from students.models import Student, StudentGuardian, StudentSibling


def _ordered_pair(a_id: int, b_id: int) -> tuple[int, int]:
    return (a_id, b_id) if a_id < b_id else (b_id, a_id)


@receiver(post_save, sender=StudentGuardian)
def auto_detect_siblings(sender, instance: StudentGuardian, created: bool, **kwargs):
    # FR-STU-004: when a new student is linked to an existing parent, detect siblings.
    if not created:
        return

    student = instance.student
    guardian = instance.guardian
    other_students = (
        Student.objects.filter(studentguardian__guardian=guardian)
        .exclude(id=student.id)
        .distinct()
    )

    created_any = False
    for other in other_students:
        a_id, b_id = _ordered_pair(student.id, other.id)
        _, did = StudentSibling.objects.get_or_create(student_a_id=a_id, student_b_id=b_id)
        created_any = created_any or did

    if created_any:
        # FR-STU-005: sibling discount eligibility flag (simple rule: any sibling link)
        Student.objects.filter(id__in=[student.id, *[s.id for s in other_students]]).update(
            sibling_discount_eligible=True
        )

        # FR-STU-005: Notify Finance Officers when new sibling links are detected
        _notify_finance_officers_sibling_detected(student, other_students, actor=None)


def _notify_finance_officers_sibling_detected(
    student: Student,
    other_students,
    actor=None,
) -> None:
    """FR-STU-005: Notify all Finance Officers when a sibling relationship is
    auto-detected via shared parent/guardian linkage.

    This fires from the ``auto_detect_siblings`` signal when new
    ``StudentSibling`` records are created.  If ``notify_sibling_confirmation``
    in ``students/views.py`` also runs later (explicit admin confirmation),
    the Finance Officer may receive a second notification — which is acceptable
    for this compliance requirement.
    """
    from communications.email_service import dispatch_notification
    from users.models import User, UserRole
    from audit.models import log_event

    finance_officers = User.objects.filter(
        role=UserRole.FINANCE_OFFICER, is_active=True
    )
    if not finance_officers.exists():
        return

    sib_names = ", ".join(
        f"{s.first_name} {s.last_name} ({s.class_name})" for s in other_students
    )
    msg = (
        f"Sibling match auto-detected for {student.first_name} {student.last_name} "
        f"({student.admission_no}). Existing sibling(s): {sib_names}. "
        "Review fee structure for sibling discount eligibility."
    )
    for fo in finance_officers:
        dispatch_notification(
            user=fo,
            title="Sibling Match Detected",
            message=msg,
            link=f"/finance/invoices/?q={student.admission_no}",
            actor=actor,
        )

    # Audit trail
    for sib in other_students:
        log_event(
            actor=actor,
            action_type="SIBLING_MATCH_DETECTED",
            model_name="Student",
            object_id=student.pk,
            description=(
                f"Auto-detected sibling link: {student.admission_no} "
                f"shares guardian with {sib.admission_no}"
            ),
        )

