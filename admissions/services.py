from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from django.utils import timezone

from admissions.models import (
    Applicant,
    ApplicantDocumentReceipt,
    ApplicantDocumentType,
    ApplicantStatus,
    ApplicantTimelineEntry,
    AssessmentSchedule,
    EnrolmentChecklist,
)
from students.models import GuardianRelationship, ParentGuardian, Student, StudentGuardian, PDPAConsentLog
from students.services import generate_admission_number
from users.models import UserRole


def _parse_inquiry_notes(applicant):
    """Return the applicant's ``notes`` JSON as a dict (empty dict if not JSON).

    Staff/public inquiries store structured data in ``notes``:
    ``{submitted_via, gender, current_grade, inquiry_notes, additional_parents}``.
    """
    import json
    raw = getattr(applicant, "notes", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _extract_additional_parents(applicant):
    """Return the list of additional parents/guardians stored on an applicant.

    Captured on the new-inquiry form and persisted in the applicant's ``notes``
    JSON as ``additional_parents`` (a list of dicts with full_name / relationship
    / phone / email). Returns an empty list when absent or unparseable.
    """
    extras = _parse_inquiry_notes(applicant).get("additional_parents") or []
    return [e for e in extras if isinstance(e, dict)]


def _next_consent_version(current_version):
    """Return the next consent version after `current_version`.

    Accepts the ``vMAJOR.MINOR`` scheme used by the guardian consent fields
    (e.g. ``v1.0`` -> ``v1.1``). An empty/unknown version starts at ``v1.0``.
    """
    current = (current_version or "").strip().lower()
    if current.startswith("v"):
        current = current[1:]
    try:
        major_s, minor_s = current.split(".", 1)
        major = int(major_s)
        minor = int(minor_s.split()[0]) if minor_s.strip() else 0
    except (ValueError, AttributeError):
        return "v1.0"
    return f"v{major}.{minor + 1}"


# FR-ADM-014: Shared checklist for assessment logistics emails
ASSESSMENT_BRING_CHECKLIST = (
    "\nWhat to bring on assessment day:\n"
    "  \u2022 Birth certificate (original + copy)\n"
    "  \u2022 Previous school report / transfer letter\n"
    "  \u2022 Completed admission form (if not yet submitted)\n"
    "  \u2022 One passport-size photograph of the child\n"
    "  \u2022 Any medical or allergy information\n"
)


STATUS_ORDER = [
    ApplicantStatus.INQUIRY_RECEIVED,
    ApplicantStatus.MEETING_SCHEDULED,
    ApplicantStatus.MEETING_COMPLETED,
    ApplicantStatus.DECLINED_AT_MEETING,
    ApplicantStatus.ASSESSMENT_PENDING,
    ApplicantStatus.ASSESSMENT_FEE_PAID,
    ApplicantStatus.ASSESSMENT_CONFIRMED,
    ApplicantStatus.ASSESSMENT_COMPLETED,
    ApplicantStatus.REPORT_PENDING,
    ApplicantStatus.ASSESSMENT_FAILED,
    ApplicantStatus.HOS_REVIEW,
    ApplicantStatus.HOS_DECISION,
    ApplicantStatus.ADMITTED,
    ApplicantStatus.CONDITIONAL,
    ApplicantStatus.FORM_SUBMITTED,
    ApplicantStatus.INVOICE_GENERATED,
    ApplicantStatus.INVOICE_PAID,
    ApplicantStatus.ENROLLED,
    ApplicantStatus.FLAGGED_FOR_REVIEW,
    ApplicantStatus.WAITLISTED,
    ApplicantStatus.DENIED,
    ApplicantStatus.WITHDRAWN,
]

ALLOWED_NEXT_STATUSES: dict[str, set[str]] = {
    ApplicantStatus.INQUIRY_RECEIVED: {ApplicantStatus.MEETING_SCHEDULED, ApplicantStatus.WITHDRAWN},
    ApplicantStatus.MEETING_SCHEDULED: {ApplicantStatus.MEETING_COMPLETED, ApplicantStatus.DECLINED_AT_MEETING, ApplicantStatus.WITHDRAWN},
    ApplicantStatus.ASSESSMENT_PENDING: {ApplicantStatus.ASSESSMENT_FEE_PAID, ApplicantStatus.ASSESSMENT_CONFIRMED, ApplicantStatus.WITHDRAWN},
    ApplicantStatus.ASSESSMENT_FEE_PAID: {ApplicantStatus.ASSESSMENT_CONFIRMED, ApplicantStatus.ASSESSMENT_COMPLETED, ApplicantStatus.WITHDRAWN},
    ApplicantStatus.ASSESSMENT_CONFIRMED: {ApplicantStatus.ASSESSMENT_COMPLETED, ApplicantStatus.WITHDRAWN},
    ApplicantStatus.ASSESSMENT_COMPLETED: {ApplicantStatus.HOS_DECISION, ApplicantStatus.REPORT_PENDING, ApplicantStatus.ASSESSMENT_FAILED},
    ApplicantStatus.HOS_REVIEW: {ApplicantStatus.HOS_DECISION},
    ApplicantStatus.HOS_DECISION: {
        ApplicantStatus.ADMITTED,
        ApplicantStatus.CONDITIONAL,
        ApplicantStatus.DENIED,
        ApplicantStatus.WAITLISTED,
    },
    ApplicantStatus.ADMITTED: {ApplicantStatus.FORM_SUBMITTED, ApplicantStatus.WITHDRAWN},
    ApplicantStatus.CONDITIONAL: {ApplicantStatus.ENROLLED, ApplicantStatus.FORM_SUBMITTED, ApplicantStatus.WITHDRAWN},
    ApplicantStatus.WAITLISTED: {ApplicantStatus.ADMITTED, ApplicantStatus.CONDITIONAL, ApplicantStatus.DENIED, ApplicantStatus.WITHDRAWN},
    ApplicantStatus.DENIED: {ApplicantStatus.HOS_DECISION, ApplicantStatus.WITHDRAWN},
    ApplicantStatus.ENROLLED: {ApplicantStatus.WITHDRAWN, ApplicantStatus.FLAGGED_FOR_REVIEW, ApplicantStatus.ADMITTED, ApplicantStatus.CONDITIONAL, ApplicantStatus.HOS_DECISION, ApplicantStatus.HOS_REVIEW, ApplicantStatus.ASSESSMENT_COMPLETED},
    ApplicantStatus.WITHDRAWN: {ApplicantStatus.INQUIRY_RECEIVED, ApplicantStatus.ASSESSMENT_PENDING},
    ApplicantStatus.MEETING_COMPLETED: {ApplicantStatus.ASSESSMENT_PENDING, ApplicantStatus.DECLINED_AT_MEETING, ApplicantStatus.WITHDRAWN},
    ApplicantStatus.DECLINED_AT_MEETING: {ApplicantStatus.MEETING_SCHEDULED, ApplicantStatus.WITHDRAWN},
    ApplicantStatus.REPORT_PENDING: {ApplicantStatus.HOS_REVIEW, ApplicantStatus.HOS_DECISION, ApplicantStatus.ASSESSMENT_FAILED},
    ApplicantStatus.ASSESSMENT_FAILED: {ApplicantStatus.ASSESSMENT_PENDING, ApplicantStatus.ADMITTED, ApplicantStatus.WITHDRAWN},
    ApplicantStatus.FORM_SUBMITTED: {ApplicantStatus.INVOICE_GENERATED, ApplicantStatus.WITHDRAWN},
    ApplicantStatus.INVOICE_GENERATED: {ApplicantStatus.INVOICE_PAID, ApplicantStatus.WITHDRAWN},
    ApplicantStatus.INVOICE_PAID: {ApplicantStatus.ENROLLED, ApplicantStatus.WITHDRAWN},
    ApplicantStatus.FLAGGED_FOR_REVIEW: {ApplicantStatus.ENROLLED, ApplicantStatus.WITHDRAWN},
}

TARGET_STATUS_REQUIRED_PERMISSIONS: dict[str, str] = {
    ApplicantStatus.MEETING_SCHEDULED: "admissions.transition_applicant_status",
    ApplicantStatus.ASSESSMENT_PENDING: "admissions.transition_applicant_status",
    ApplicantStatus.ASSESSMENT_FEE_PAID: "admissions.confirm_assessment_fee",
    ApplicantStatus.ASSESSMENT_CONFIRMED: "admissions.transition_applicant_status",
    ApplicantStatus.ASSESSMENT_COMPLETED: "admissions.transition_applicant_status",
    ApplicantStatus.HOS_REVIEW: "admissions.submit_hos_review",
    ApplicantStatus.HOS_DECISION: "admissions.transition_applicant_status",
    ApplicantStatus.ADMITTED: "admissions.transition_applicant_status",
    ApplicantStatus.CONDITIONAL: "admissions.transition_applicant_status",
    ApplicantStatus.DENIED: "admissions.transition_applicant_status",
    ApplicantStatus.ENROLLED: "admissions.complete_enrolment",
    ApplicantStatus.WAITLISTED: "admissions.transition_applicant_status",
    ApplicantStatus.WITHDRAWN: "admissions.transition_applicant_status",
    ApplicantStatus.MEETING_COMPLETED: "admissions.transition_applicant_status",
    ApplicantStatus.DECLINED_AT_MEETING: "admissions.transition_applicant_status",
    ApplicantStatus.REPORT_PENDING: "admissions.transition_applicant_status",
    ApplicantStatus.ASSESSMENT_FAILED: "admissions.transition_applicant_status",
    ApplicantStatus.FORM_SUBMITTED: "admissions.transition_applicant_status",
    ApplicantStatus.INVOICE_GENERATED: "admissions.transition_applicant_status",
    ApplicantStatus.INVOICE_PAID: "admissions.transition_applicant_status",
    ApplicantStatus.FLAGGED_FOR_REVIEW: "admissions.transition_applicant_status",
}


def _status_index(value: str) -> int:
    try:
        return STATUS_ORDER.index(value)
    except ValueError:
        return 10_000


def get_allowed_transition_targets(*, from_status: str, actor_role: str | None, actor=None) -> list[str]:
    """Return list of status keys the actor can transition to from from_status.

    Permission-based: checks the actor's effective permissions against
    TARGET_STATUS_REQUIRED_PERMISSIONS. If actor is provided, uses has_perm();
    otherwise falls back to role-based check for backward compatibility.
    """
    # If Super Admin or HOS, they can jump to ANY status except current
    if actor and actor.is_superuser:
        return [s for s, _ in ApplicantStatus.choices if s != from_status]
    if actor_role in {UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL}:
        return [s for s, _ in ApplicantStatus.choices if s != from_status]

    next_steps = ALLOWED_NEXT_STATUSES.get(from_status, set())
    allowed = []
    for s in next_steps:
        required_perm = TARGET_STATUS_REQUIRED_PERMISSIONS.get(s)
        if not required_perm:
            continue
        if actor and hasattr(actor, "has_perm"):
            if actor.has_perm(required_perm):
                allowed.append(s)
        elif actor_role:
            # Fallback: map permission to role check for backward compatibility
            _perm_role_fallback = {
                "admissions.confirm_assessment_fee": {UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL},
                "admissions.submit_hos_review": {UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.SUPER_ADMIN},
                "admissions.complete_enrolment": {UserRole.ADMIN_OFFICER, UserRole.SUPER_ADMIN},
            }
            allowed_roles = _perm_role_fallback.get(required_perm, {UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN})
            if actor_role in allowed_roles:
                allowed.append(s)
    return allowed


@transaction.atomic
def transition_applicant_status(
    *,
    applicant: Applicant,
    to_status: str,
    actor,
    reason: str = "",
) -> Applicant:
    if to_status not in dict(ApplicantStatus.choices):
        raise ValidationError("Invalid status transition target.")

    from_status = applicant.status
    if from_status == to_status:
        raise ValidationError("Applicant is already in that status.")

    actor_role = getattr(actor, "role", None)
    allowed_targets = get_allowed_transition_targets(from_status=from_status, actor_role=actor_role, actor=actor)
    
    # Validation block
    if to_status not in allowed_targets:
        # Check if it's a super-user override that should be logged
        if actor_role in {UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL}:
            # This case is actually handled by get_allowed_transition_targets now, 
            # but we keep this here as a secondary safety check.
            pass
        else:
            raise ValidationError("Invalid transition for current applicant stage or your role.")

    applicant.status = to_status
    applicant.full_clean()
    applicant.save(update_fields=["status", "updated_at"])

    ApplicantTimelineEntry.objects.create(
        applicant=applicant,
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        reason=(reason or "").strip(),
    )
    
    # Central Audit Log
    from audit.models import log_event
    log_event(
        actor=actor,
        action_type="ADMISSION_STATUS_TRANSITION",
        model_name="Applicant",
        object_id=applicant.pk,
        description=f"Applicant {applicant.child_full_name} moved from {from_status} to {to_status}",
        before_value=from_status,
        after_value=to_status,
    )

    # NOTIF-09: Space available — notify Admin when enrolled/admitted/conditional student withdraws
    if to_status == ApplicantStatus.WITHDRAWN and from_status in (
        ApplicantStatus.ENROLLED, ApplicantStatus.ADMITTED, ApplicantStatus.CONDITIONAL,
    ):
        grade = applicant.grade_applying_for.strip()
        waitlisted_count = Applicant.objects.filter(
            grade_applying_for__iexact=grade,
            status=ApplicantStatus.WAITLISTED,
        ).count()
        if waitlisted_count > 0:
            from communications.email_service import dispatch_notification
            from users.models import User
            admins = User.objects.filter(role=UserRole.ADMIN_OFFICER, is_active=True)
            for admin in admins:
                dispatch_notification(
                    user=admin,
                    title=f"Space Available in {grade}",
                    message=(
                        f"A space is now available in {grade}. "
                        f"{waitlisted_count} applicant(s) are on the waitlist. "
                        f"Review and offer the space to the next applicant in date order."
                    ),
                    link="/admissions/waitlist/",
                    actor=actor,
                )

    return applicant


@transaction.atomic
def ensure_default_documents(applicant: Applicant) -> None:
    for dt, _label in ApplicantDocumentType.choices:
        ApplicantDocumentReceipt.objects.get_or_create(applicant=applicant, document_type=dt)


@transaction.atomic
def ensure_enrolment_checklist(applicant: Applicant) -> EnrolmentChecklist:
    checklist, _ = EnrolmentChecklist.objects.get_or_create(applicant=applicant)
    return checklist


@transaction.atomic
def schedule_assessment(
    *,
    applicant: Applicant,
    actor,
    scheduled_date,
    scheduled_time,
    location: str,
    facilitating_teacher_name: str,
    assessment_fee_amount,
) -> AssessmentSchedule:
    assessment, _created = AssessmentSchedule.objects.update_or_create(
        applicant=applicant,
        defaults={
            "scheduled_date": scheduled_date,
            "scheduled_time": scheduled_time,
            "location": location.strip(),
            "facilitating_teacher_name": facilitating_teacher_name.strip(),
            "assessment_fee_amount": assessment_fee_amount,
        },
    )
    ensure_default_documents(applicant)
    ensure_enrolment_checklist(applicant)
    
    # FR-ADM-008: Create Miscellaneous invoice for assessment fee
    from finance.models import Invoice, InvoiceLineItem, InvoiceStatus, FinancePeriod
    # Get or create an active finance period
    period = FinancePeriod.objects.filter(is_reconciled=False).first()
    
    # Check if invoice already exists to avoid duplicates
    if not Invoice.objects.filter(applicant=applicant).exists():
        inv_no = f"AST-{applicant.id:04d}-{timezone.now().strftime('%y%m%d')}"
        invoice = Invoice.objects.create(
            applicant=applicant,
            amount_due=assessment_fee_amount,
            total_due=assessment_fee_amount,
            due_date=timezone.now().date(),
            status=InvoiceStatus.UNPAID,
            period=period,
            invoice_number=inv_no
        )
        InvoiceLineItem.objects.create(
            invoice=invoice,
            description=f"Assessment Fee for {applicant.child_full_name}",
            amount=assessment_fee_amount
        )

    # This action implies the meeting/scheduling stage.

    if applicant.status == ApplicantStatus.INQUIRY_RECEIVED:
        transition_applicant_status(
            applicant=applicant,
            to_status=ApplicantStatus.MEETING_SCHEDULED,
            actor=actor,
            reason="Assessment scheduled from inquiry.",
        )

    # NOTIF-02: Notify Finance Officer that assessment fee invoice was auto-generated
    from communications.email_service import dispatch_notification
    from users.models import User, UserRole
    finance_officers = User.objects.filter(role=UserRole.FINANCE_OFFICER, is_active=True)
    for fo in finance_officers:
        dispatch_notification(
            user=fo,
            title="Assessment Fee Invoice Generated",
            message=(
                f"An assessment fee invoice (TZS {assessment_fee_amount:,.0f}) has been "
                f"auto-generated for {applicant.child_full_name} ({applicant.grade_applying_for}). "
                f"Please confirm payment once received."
            ),
            link=f"/admissions/applicant/{applicant.pk}/",
            actor=actor,
        )

    # Notify facilitating teacher that they've been assigned to an assessment
    if facilitating_teacher_name:
        from core.models import SchoolSettings
        _school = SchoolSettings.get_settings()
        _school_name = _school.school_name or "the school"
        teacher_users = User.objects.filter(
            is_active=True,
            role__in=[UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD],
        )
        for tu in teacher_users:
            full_name = f"{tu.first_name} {tu.last_name}".strip()
            if full_name.lower() == facilitating_teacher_name.strip().lower() or tu.get_username().lower() == facilitating_teacher_name.strip().lower():
                # Build detailed time display
                try:
                    hour = scheduled_time.hour if hasattr(scheduled_time, 'hour') else 0
                    minute = scheduled_time.minute if hasattr(scheduled_time, 'minute') else 0
                    ampm = 'AM' if hour < 12 else 'PM'
                    display_hour = hour % 12 or 12
                    time_display = f"{display_hour}:{minute:02d} {ampm}"
                except Exception:
                    time_display = str(scheduled_time)

                msg = (
                    f"Dear {tu.first_name or facilitating_teacher_name},\n\n"
                    f"You have been assigned as the facilitating teacher for an assessment at {_school_name}.\n\n"
                    f"Applicant: {applicant.child_full_name}\n"
                    f"Grade applying for: {applicant.grade_applying_for}\n"
                    f"Parent: {applicant.parent_full_name}\n"
                    f"Date: {scheduled_date}\n"
                    f"Time: {time_display}\n"
                    f"Location: {location}\n"
                    f"Assessment Fee: TSh {_school.assessment_fee:,.0f}\n\n"
                    f"Reference: {applicant.reference_number}\n\n"
                    f"Please prepare the assessment materials and be available at the scheduled time.\n\n"
                    f"Admissions Office\n{_school_name}"
                )
                dispatch_notification(
                    user=tu,
                    title=f"Assessment Assigned — {applicant.child_full_name}",
                    message=msg,
                    link=f"/admissions/applicant/{applicant.pk}/",
                    actor=actor,
                )
                # Create task for teacher to fill assessment report
                from tasks.services import generate_assessment_report_task
                generate_assessment_report_task(applicant, tu)
                break

    return assessment


@transaction.atomic
def confirm_assessment_fee_paid(*, applicant: Applicant, actor, payment_method: str = "", payment_reference: str = "") -> None:
    """FR-ADM-013: Confirm assessment fee paid with payment method and reference."""
    if not hasattr(applicant, "assessment"):
        raise ValidationError("Assessment is not scheduled yet.")
    assessment: AssessmentSchedule = applicant.assessment
    assessment.assessment_fee_confirmed_paid = True
    assessment.assessment_fee_confirmed_at = timezone.now()
    assessment.assessment_fee_confirmed_by = actor
    assessment.assessment_fee_method = payment_method
    assessment.assessment_fee_reference = payment_reference
    assessment.full_clean()
    assessment.save(
        update_fields=[
            "assessment_fee_confirmed_paid",
            "assessment_fee_confirmed_at",
            "assessment_fee_confirmed_by_id",
            "assessment_fee_method",
            "assessment_fee_reference",
            "updated_at",
        ]
    )
    if applicant.status != ApplicantStatus.ASSESSMENT_FEE_PAID:
        # If assessment already completed (teacher submitted before fee was recorded), skip ahead
        if assessment.result and applicant.status not in (
            ApplicantStatus.ASSESSMENT_COMPLETED, ApplicantStatus.HOS_REVIEW,
            ApplicantStatus.HOS_DECISION, ApplicantStatus.ADMITTED,
            ApplicantStatus.CONDITIONAL, ApplicantStatus.ENROLLED,
        ):
            transition_applicant_status(
                applicant=applicant,
                to_status=ApplicantStatus.ASSESSMENT_COMPLETED,
                actor=actor,
                reason="Assessment fee confirmed. Assessment already completed — skipping ahead.",
            )
        else:
            # Skip straight to assessment_confirmed (fee_paid column removed from pipeline)
            transition_applicant_status(
                applicant=applicant,
                to_status=ApplicantStatus.ASSESSMENT_CONFIRMED,
                actor=actor,
                reason="Assessment fee confirmed paid.",
            )
        
    # NOTIF-02: Assessment fee paid -> Admin Officer
    from communications.email_service import dispatch_notification
    from users.models import User, UserRole
    admins = User.objects.filter(role=UserRole.ADMIN_OFFICER, is_active=True)
    for admin in admins:
        dispatch_notification(
            user=admin,
            title="Assessment Fee Paid",
            message=f"Assessment fee confirmed paid for {applicant.child_full_name} ({applicant.grade_applying_for}). Ready to send logistics.",
            link=f"/admissions/applicant/{applicant.pk}/",
            actor=actor
        )


@transaction.atomic
def mark_logistics_sent(*, applicant: Applicant, actor) -> None:
    if not hasattr(applicant, "assessment"):
        raise ValidationError("Assessment is not scheduled yet.")
    assessment: AssessmentSchedule = applicant.assessment
    if not assessment.assessment_fee_confirmed_paid:
        raise ValidationError("Assessment logistics cannot be sent until the assessment fee is confirmed by Finance.")
    assessment.logistics_sent_at = timezone.now()
    assessment.full_clean()
    assessment.save(update_fields=["logistics_sent_at", "updated_at"])
    if applicant.status != ApplicantStatus.ASSESSMENT_CONFIRMED:
        transition_applicant_status(
            applicant=applicant,
            to_status=ApplicantStatus.ASSESSMENT_CONFIRMED,
            actor=actor,
            reason="Assessment logistics sent to parent.",
        )
        
    # FR-ADM-014: Notification to parent via email + in-app
    from communications.email_service import dispatch_notification
    parent_email = (applicant.parent_email or "").strip() or None
    parent_phone = (applicant.parent_phone or "").strip() or None
    if parent_email or parent_phone:
        # Build proper 12-hour time display
        time_str = str(assessment.scheduled_time)
        try:
            hour = assessment.scheduled_time.hour
            minute = assessment.scheduled_time.minute
            ampm = 'AM' if hour < 12 else 'PM'
            display_hour = hour % 12 or 12
            time_str = f"{display_hour}:{minute:02d} {ampm}"
        except Exception:
            pass

        from core.models import SchoolSettings
        contact = SchoolSettings.get_settings().get_admissions_contact()
        school_name = SchoolSettings.get_settings().school_name or "the school"

        if parent_email:
            from core.email_templates import send_dynamic_email
            tpl_context = {
                "parent_name": applicant.parent_full_name or "Parent/Guardian",
                "child_name": applicant.child_full_name,
                "assessment_date": assessment.scheduled_date,
                "assessment_dates": assessment.scheduled_date.strftime("%A, %d %B %Y") if assessment.scheduled_date else "",
                "assessment_time": time_str,
                "arrival_time": time_str,
                "assessment_start_time": time_str,
                "collection_time": "After assessment",
                "assessment_location": assessment.location,
                "assessment_venue": assessment.location,
                "teacher_name": assessment.facilitating_teacher_name,
                "ref": applicant.reference_number,
                "reference_number": applicant.reference_number,
                "school_name": school_name,
                "contact_phone": contact.get('phone', ''),
                "admissions_phone": contact.get('phone', ''),
            }
            db_sent = send_dynamic_email(
                template_type="admission_assessment_logistics",
                to_email=parent_email,
                context=tpl_context,
            )
            if not db_sent:
                msg = (
                    f"Dear {applicant.parent_full_name},\n\n"
                    f"The assessment for {applicant.child_full_name} has been confirmed.\n\n"
                    f"Date: {assessment.scheduled_date}\n"
                    f"Time: {time_str}\n"
                    f"Location: {assessment.location}\n"
                    f"Facilitating Teacher: {assessment.facilitating_teacher_name}"
                    f"{ASSESSMENT_BRING_CHECKLIST}\n"
                    "We look forward to seeing you."
                )
                dispatch_notification(
                    user=None, title="School Assessment Confirmed", message=msg,
                    link=f"/admissions/applicant/{applicant.pk}/",
                    actor=actor, external_email=parent_email, phone=parent_phone,
                )
        else:
            # S02: Assessment logistics SMS (spec copy)
            sms_msg = (
                f"Hodari: {applicant.child_full_name}'s assessment is "
                f"{assessment.scheduled_date}, arrive {time_str}. "
                f"Details sent by email. Ref {applicant.reference_number}."
            )
            dispatch_notification(
                user=None, title="School Assessment Confirmed", message=sms_msg,
                link=f"/admissions/applicant/{applicant.pk}/",
                actor=actor, external_email=None, phone=parent_phone,
            )

@transaction.atomic
def sign_off_assessment_result(*, applicant: Applicant, actor, result: str, teacher_comments: str = "", hod_comments: str = "", parent_facing_comments: str = "") -> None:
    """Teacher submits assessment result → status moves to HOS_REVIEW."""
    if not hasattr(applicant, "assessment"):
        raise ValidationError("Assessment is not scheduled yet.")
    
    assessment = applicant.assessment
    assessment.result = result
    assessment.teacher_comments = teacher_comments
    assessment.hod_comments = hod_comments
    assessment.parent_facing_comments = parent_facing_comments
    assessment.hod_signed_off = True
    assessment.hod_signed_off_at = timezone.now()
    assessment.hod_signed_off_by = actor
    assessment.full_clean()
    assessment.save()
    
    # Auto-advance through skipped steps if fee is paid but status is behind
    _behind = {
        ApplicantStatus.ASSESSMENT_FEE_PAID,
        ApplicantStatus.MEETING_COMPLETED,
        ApplicantStatus.ASSESSMENT_PENDING,
        ApplicantStatus.MEETING_SCHEDULED,
        ApplicantStatus.INQUIRY_RECEIVED,
    }
    if applicant.status in _behind and assessment.assessment_fee_confirmed_paid:
        transition_applicant_status(
            applicant=applicant,
            to_status=ApplicantStatus.ASSESSMENT_COMPLETED,
            actor=actor,
            reason="Auto-advanced: fee paid and assessment already completed.",
        )
    
    transition_applicant_status(
        applicant=applicant,
        to_status=ApplicantStatus.HOS_REVIEW,
        actor=actor,
        reason=f"Assessment result submitted as {result.upper()} by {actor.get_username()}."
    )

    # NOTIF-04: Notify relevant HOD(s) that assessment result is ready for review
    from communications.email_service import dispatch_notification
    from users.models import User, UserRole
    hod_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD]
    hods = User.objects.filter(role__in=hod_roles, is_active=True)
    for hod in hods:
        dispatch_notification(
            user=hod,
            title="Assessment Result Submitted",
            message=(
                f"{actor.get_username()} submitted an assessment result for "
                f"{applicant.child_full_name} ({applicant.grade_applying_for}). "
                f"Recommendation: {result}. Please review and forward to HOS."
            ),
            link=f"/admissions/applicant/{applicant.pk}/",
            actor=actor,
        )

    # E12: Report submitted → HOS email
    from core.email_templates import send_dynamic_email
    from core.models import SchoolSettings
    _ss = SchoolSettings.get_settings()
    hos_users = User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True)
    review_link = f"/admissions/applicant/{applicant.pk}/"
    for hos in hos_users:
        if hos.email:
            send_dynamic_email(
                template_type="admission_report_submitted_hos",
                to_email=hos.email,
                context={
                    "hos_name": hos.get_full_name() or "Head of School",
                    "child_name": applicant.child_full_name,
                    "grade": applicant.grade_applying_for,
                    "recommendation": result,
                    "reference_number": applicant.reference_number,
                    "review_link": review_link,
                    "school_name": _ss.school_name or "the school",
                },
            )




@transaction.atomic
def submit_hos_review(*, applicant: Applicant, actor, hos_comments: str = "", parent_facing_comments: str = "") -> None:
    """HOS reviews assessment result and forwards recommendation to HOS."""
    if not hasattr(applicant, "assessment"):
        raise ValidationError("Assessment is not scheduled yet.")
    if applicant.status != ApplicantStatus.HOS_REVIEW:
        raise ValidationError("Applicant is not in HOS review status.")

    assessment = applicant.assessment
    assessment.hod_comments = hos_comments
    if parent_facing_comments:
        assessment.parent_facing_comments = parent_facing_comments
    assessment.hod_signed_off = True
    assessment.hod_signed_off_at = timezone.now()
    assessment.hod_signed_off_by = actor
    assessment.full_clean()
    assessment.save()

    transition_applicant_status(
        applicant=applicant,
        to_status=ApplicantStatus.HOS_DECISION,
        actor=actor,
        reason=f"HOS review completed by {actor.get_username()}. Forwarded to HOS for decision."
    )

    # NOTIF-05: Notify HOS that HOS review is complete and ready for decision
    from communications.email_service import dispatch_notification
    from users.models import User, UserRole
    hos_users = User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True)
    for hos in hos_users:
        dispatch_notification(
            user=hos,
            title="HOS Review Complete (Decision Required)",
            message=(
                f"{actor.get_username()} completed HOS review for "
                f"{applicant.child_full_name} ({applicant.grade_applying_for}). "
                f"Result: {assessment.get_result_display()}. "
                "Please review and make an admission decision."
            ),
            link=f"/admissions/applicant/{applicant.pk}/",
            actor=actor,
        )


# Backward-compatible alias
submit_hod_review = submit_hos_review


@transaction.atomic
def toggle_document_received(*, applicant: Applicant, doc_type: str, actor, received: bool, file=None) -> None:
    ensure_default_documents(applicant)
    doc = ApplicantDocumentReceipt.objects.get(applicant=applicant, document_type=doc_type)

    # A document cannot be marked as received without a supporting file attachment.
    if received and not file and not doc.file:
        raise ValidationError(
            f"'{doc.get_document_type_display()}' cannot be marked as received without "
            "a supporting file attachment. Please upload the document first."
        )

    doc.is_received = received
    doc.received_at = timezone.now() if received else None
    doc.received_by = actor if received else None
    if file:
        doc.file = file
        doc.is_received = True  # Auto-mark as received if file is uploaded
        if not doc.received_at:
            doc.received_at = timezone.now()
        if not doc.received_by:
            doc.received_by = actor

    doc.full_clean()
    doc.save()



@transaction.atomic
def mark_orientation_visit_completed(*, applicant: Applicant, actor, completed: bool = True) -> EnrolmentChecklist:
    checklist = ensure_enrolment_checklist(applicant)
    checklist.orientation_visit_completed = completed
    checklist.orientation_visit_completed_at = timezone.now() if completed else None
    checklist.orientation_visit_completed_by = actor if completed else None
    checklist.full_clean()
    checklist.save(
        update_fields=[
            "orientation_visit_completed",
            "orientation_visit_completed_at",
            "orientation_visit_completed_by_id",
            "updated_at",
        ]
    )
    return checklist


def _split_name(full_name: str) -> tuple[str, str]:
    parts = [p for p in (full_name or "").strip().split(" ") if p]
    if not parts:
        return ("Student", "Unknown")
    if len(parts) == 1:
        return (parts[0], parts[0])
    return (parts[0], " ".join(parts[1:]))


def ensure_admission_grades() -> int:
    """DEF-9: Seed AdmissionGrade from the configured GradeClass list.

    Called whenever the admissions grade list is needed, so a fresh install with
    classes configured (but no AdmissionGrade rows) can still admit students.
    Returns the number of grades created.
    """
    from admissions.models import AdmissionGrade
    if AdmissionGrade.objects.exists():
        return 0

    created = 0
    from academics.models import GradeClass, Department
    for gc in GradeClass.objects.order_by("sort_order", "name"):
        department = (gc.department or Department.PRIMARY)
        if department not in dict(Department.choices):
            department = Department.PRIMARY
        _, was_created = AdmissionGrade.objects.get_or_create(
            name=gc.name,
            defaults={"department": department, "sort_order": gc.sort_order, "is_active": True},
        )
        if was_created:
            created += 1

    # Fallback defaults if the school has no GradeClass rows configured yet.
    if created == 0:
        from admissions.models import Department as _Dept
        fallback = [
            ("Pre-K", _Dept.ECD, 10), ("KG", _Dept.ECD, 20),
            ("Grade 1", _Dept.PRIMARY, 30), ("Grade 2", _Dept.PRIMARY, 40),
            ("Grade 3", _Dept.PRIMARY, 50), ("Grade 4", _Dept.PRIMARY, 60),
            ("Grade 5", _Dept.PRIMARY, 70), ("Grade 6", _Dept.PRIMARY, 80),
            ("Grade 7", _Dept.LOWER_SECONDARY, 90),
        ]
        for name, dept, order in fallback:
            _, was_created = AdmissionGrade.objects.get_or_create(
                name=name,
                defaults={"department": dept, "sort_order": order, "is_active": True},
            )
            if was_created:
                created += 1

    return created


@transaction.atomic
def complete_enrolment(*, applicant: Applicant, actor, override_duplicate: bool = False, pdpa_consent_given: bool = False, pdpa_consent_version: str = "", confirm_sibling: bool = False) -> Student:

    ensure_default_documents(applicant)
    checklist = ensure_enrolment_checklist(applicant)

    # FR-ADM-031: Enrolment requires invoice paid
    if applicant.status not in (ApplicantStatus.INVOICE_PAID, ApplicantStatus.ENROLLED):
        raise ValidationError("Enrolment cannot be completed until the admission invoice has been paid in full.")

    # FR-ADM-024 prerequisites: all docs + orientation visit complete.
    required_types = {dt for dt, _label in ApplicantDocumentType.choices}
    received_types = set(
        applicant.documents.filter(is_received=True).values_list("document_type", flat=True)
    )
    missing = required_types - received_types
    if missing:
        raise ValidationError("Enrolment cannot be completed until all required documents are received.")

    # FR-ADM-022 sequencing: clearance form must be received before issuing admission package.
    clearance = applicant.documents.filter(
        document_type=ApplicantDocumentType.CLEARANCE_FORM, is_received=True
    ).exists()
    if not clearance:
        raise ValidationError("Clearance form must be received before enrolment completion.")

    # FR-PDPA-001: Mandatory PDPA consent
    if not pdpa_consent_given:
        raise ValidationError("PDPA consent must be recorded before this record can be saved.")
    # FR-PAR-004: the version of the consent statement presented is mandatory;
    # consent may not be recorded without an explicit statement version.
    if not (pdpa_consent_version or "").strip():
        raise ValidationError("The version of the PDPA consent statement must be recorded with the consent.")

    # FR-ADM-027: Capacity hard-block
    from academics.models import GradeClass, get_class_capacity
    grade_name = applicant.grade_applying_for.strip()
    gc = GradeClass.objects.filter(name__iexact=grade_name).first()
    if gc:
        cap = get_class_capacity(gc)
        if cap:
            student_count = Student.objects.filter(class_name__iexact=grade_name, is_archived=False).count()
            if student_count >= cap:
                raise ValidationError(f"Cannot complete enrolment: {grade_name} is full ({cap}/{cap}). Please move to waitlist or increase capacity.")

    first_name, last_name = _split_name(applicant.child_full_name)

    
    # NFR-DATA-001: Duplicate prevention
    existing = Student.objects.filter(
        first_name=first_name, 
        last_name=last_name, 
        date_of_birth=applicant.child_date_of_birth
    ).first()
    if existing and not override_duplicate:
        raise ValidationError(f"Duplicate student detected: {existing.admission_no} ({existing.first_name} {existing.last_name}). Super Admin override required.")
    elif existing and override_duplicate:
        # Log override
        from audit.models import log_event
        log_event(
            actor=actor,
            action_type="DUPLICATE_ENROLMENT_OVERRIDE",
            model_name="Student",
            object_id=existing.pk,
            description=f"Super Admin override for duplicate enrolment of {applicant.child_full_name}",
        )

    from academics.utils import get_current_academic_year
    ay = get_current_academic_year()
    student = Student.objects.create(

        admission_no=generate_admission_number(),
        first_name=first_name,
        last_name=last_name,
        date_of_birth=applicant.child_date_of_birth,
        class_name=applicant.grade_applying_for.strip(),
        stream_name="",
        photo=applicant.photo,
        academic_year=ay,
    )

    from audit.models import log_event
    log_event(
        actor=actor,
        action_type="STUDENT_CREATED",
        model_name="Student",
        object_id=student.pk,
        description=f"Student {student.admission_no} ({student.first_name} {student.last_name}) created via enrolment of applicant {applicant.child_full_name} (id {applicant.pk})",
    )

    from students.models import EnrollmentHistory
    EnrollmentHistory.objects.create(
        student=student,
        academic_year=ay,
        class_name=student.class_name,
        action="enrolled",
    )
    phone_clean = applicant.parent_phone.strip()
    email_clean = (applicant.parent_email or "").strip().lower()

    guardian = None

    if phone_clean:
        guardian = ParentGuardian.objects.filter(phone=phone_clean).first()

    if not guardian and email_clean:
        guardian = ParentGuardian.objects.filter(email__iexact=email_clean).first()

    if not guardian:
        guardian = ParentGuardian.objects.create(
            phone=phone_clean,
            full_name=applicant.parent_full_name.strip(),
            email=email_clean,
            preferred_invoice_name=(applicant.parent_invoice_name or "").strip()
        )
    else:
        # Update missing info if applicable
        update_fields = []
        if not guardian.email and email_clean:
            guardian.email = email_clean
            update_fields.append("email")
        if not guardian.preferred_invoice_name and (applicant.parent_invoice_name or "").strip():
            guardian.preferred_invoice_name = applicant.parent_invoice_name.strip()
            update_fields.append("preferred_invoice_name")
        if update_fields:
            update_fields.append("updated_at")
            guardian.save(update_fields=update_fields)
    
    # Save PDPA consent — FR-PDPA-004: never overwrite in place; append a
    # versioned record (who/when/version) and preserve the previous version.
    old_version = guardian.pdpa_consent_version or ""
    old_method = guardian.pdpa_consent_method or ""
    was_given = guardian.has_given_consent()
    new_version = (pdpa_consent_version or "").strip()

    guardian.pdpa_consent_given = True
    guardian.pdpa_consent_method = "in_person"
    guardian.pdpa_consent_version = new_version
    guardian.pdpa_consented_at = timezone.now()
    guardian.save(update_fields=[
        "preferred_invoice_name", "pdpa_consent_given", "pdpa_consent_method", 
        "pdpa_consent_version", "pdpa_consented_at", "updated_at"
    ])
    PDPAConsentLog.objects.create(
        guardian=guardian,
        action=PDPAConsentLog.Action.UPDATED if was_given else PDPAConsentLog.Action.GIVEN,
        method="in_person", version=new_version, previous_version=old_version,
        actor=actor,
        notes="Consent recorded during enrolment completion.",
    )

    # Normalize parent_relationship to match GuardianRelationship choices (lowercase)
    rel_value = (applicant.parent_relationship or "").strip().lower()
    valid_rels = {choice[0] for choice in GuardianRelationship.choices}
    if rel_value not in valid_rels:
        rel_value = GuardianRelationship.GUARDIAN

    StudentGuardian.objects.get_or_create(
        student=student, guardian=guardian,
        defaults={"relationship": rel_value, "is_primary": True}
    )

    # Link any additional parents / guardians captured on the inquiry form.
    # These are stored in the applicant's notes JSON as `additional_parents`;
    # each becomes a non-primary guardian on the student record.
    for extra in _extract_additional_parents(applicant):
        extra_phone = (extra.get("phone") or "").strip()
        extra_email = (extra.get("email") or "").strip().lower()
        extra_name = (extra.get("full_name") or "").strip()
        if not (extra_phone or extra_email or extra_name):
            continue

        extra_guardian = None
        if extra_phone:
            extra_guardian = ParentGuardian.objects.filter(phone=extra_phone).first()
        if not extra_guardian and extra_email:
            extra_guardian = ParentGuardian.objects.filter(email__iexact=extra_email).first()
        if not extra_guardian:
            extra_guardian = ParentGuardian.objects.create(
                phone=extra_phone,
                full_name=extra_name,
                email=extra_email,
            )

        # Don't duplicate the primary guardian if the extra points to the same person.
        if extra_guardian.pk == guardian.pk:
            continue

        extra_rel = (extra.get("relationship") or "").strip().lower()
        if extra_rel not in valid_rels:
            extra_rel = GuardianRelationship.GUARDIAN
        StudentGuardian.objects.get_or_create(
            student=student, guardian=extra_guardian,
            defaults={"relationship": extra_rel, "is_primary": False}
        )

    # FR-PARENT-001: Auto-create parent User account for portal access
    if guardian and not guardian.user:
        from users.models import User
        from django.utils.crypto import get_random_string

        phone_digits = "".join(filter(str.isdigit, guardian.phone or ""))
        base_username = f"parent_{phone_digits[:12]}" if phone_digits else f"parent_{guardian.pk}"
        username = base_username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}_{counter}"
            counter += 1

        raw_password = get_random_string(length=10)
        name_parts = (guardian.full_name or "").strip().split(maxsplit=1)
        parent_first = name_parts[0] if name_parts else ""
        parent_last = name_parts[1] if len(name_parts) > 1 else ""

        # DEF-10: Never collide on users_user.email UNIQUE — generate a unique
        # fallback for parents without an email instead of saving an empty string.
        email = (guardian.email or "").strip().lower()
        if not email:
            email = f"parent{guardian.pk}@hodari.local"
        else:
            base, at, domain = email.partition("@")
            suffix = 2
            while User.objects.filter(email=email).exists():
                email = f"{base}{suffix}@{domain}"
                suffix += 1

        parent_user = User.objects.create(
            username=username,
            email=email,
            role=UserRole.PARENT,
            first_name=parent_first,
            last_name=parent_last,
            must_change_password=True,
        )
        parent_user.set_password(raw_password)
        parent_user.save()

        guardian.user = parent_user
        guardian.save(update_fields=["user", "updated_at"])

        # Send credentials to parent via available channels
        from communications.email_service import send_parent_notification
        from django.template.loader import render_to_string
        from django.conf import settings as django_settings
        from core.models import SchoolSettings

        school_name = SchoolSettings.get_settings().school_name or "Hodari School"
        _site = getattr(django_settings, 'SITE_URL', 'http://127.0.0.1:8000')
        _portal_url = _site.rstrip('/') + '/parent/'
        _admission_form_url = _site.rstrip('/') + '/parent/admission-form/'

        creds_message = (
            f"Dear {guardian.full_name},\n\n"
            f"Your parent portal account has been created. You can now access your child's "
            f"attendance records, report cards, invoices, and school communications.\n\n"
            f"Username: {username}\n"
            f"Password: {raw_password}\n\n"
            f"IMPORTANT: You will be required to change your password on first login.\n\n"
            f"Parent Portal: {_portal_url}\n\n"
            f"Complete Admission Form: {_admission_form_url}\n\n"
            f"Thank you for choosing {school_name}."
        )

        tpl_context = {
            "guardian_name": guardian.full_name,
            "username": username,
            "temp_password": raw_password,
            "login_url": _admission_form_url,
            "school_name": school_name,
            "site_url": _site,
            "static_url": getattr(django_settings, 'STATIC_URL', '/static/'),
            "portal_link": _admission_form_url,
        }

        # Try dynamic DB template first
        from core.email_templates import send_dynamic_email
        db_sent = send_dynamic_email(
            template_type="parent_portal",
            to_email=guardian.email,
            context=tpl_context,
            actor=actor,
        )

        if not db_sent:
            html_body = render_to_string("registration/parent_portal_email.html", tpl_context)

            send_parent_notification(
                guardian=guardian,
                title="Your Parent Portal Account Has Been Created",
                message=creds_message,
                html_body=html_body,
                actor=actor,
                action_type="PARENT_PORTAL_ACCOUNT",
            )

    applicant.enrolled_student = student
    applicant.save(update_fields=["enrolled_student_id", "updated_at"])
    
    # FR-STU-004: Sibling auto-detection on enrolment
    from students.models import StudentSibling
    other_sibs = Student.objects.filter(studentguardian__guardian=guardian).exclude(pk=student.pk)
    
    if other_sibs.exists() and confirm_sibling:
        for sib in other_sibs:
            # Create link in both directions (model logic might handle it, but we ensure one row or two)
            # This model says "stored in one row", but we use get_or_create to be safe.
            # Order IDs to avoid A-B and B-A rows if intended as undirected.
            a_id, b_id = sorted([student.pk, sib.pk])
            StudentSibling.objects.get_or_create(
                student_a_id=a_id,
                student_b_id=b_id,
                defaults={"created_by": actor}
            )
            
        student.sibling_discount_eligible = True
        student.save(update_fields=["sibling_discount_eligible"])
        
        # Notify Finance Officer
        from communications.email_service import dispatch_notification
        from users.models import User
        finance_officers = User.objects.filter(role=UserRole.FINANCE_OFFICER, is_active=True)
        sib_names = ", ".join([f"{s.first_name} {s.last_name}" for s in other_sibs])
        msg = f"Sibling link confirmed for {student.first_name} {student.last_name}. Siblings: {sib_names}. Review fee structure for sibling discount eligibility."
        for fo in finance_officers:
            dispatch_notification(
                user=fo,
                title="Sibling Link Confirmed",
                message=msg,
                link=f"/finance/invoices/?q={student.admission_no}",
                actor=actor
            )
            
        # Log to audit trail
        from audit.models import log_event
        for sib in other_sibs:
            log_event(
                actor=actor,
                action_type="SIBLING_LINKED",
                model_name="Student",
                object_id=student.pk,
                description=f"Student {student.admission_no} linked as sibling to {sib.admission_no}",
            )

    transition_applicant_status(
        applicant=applicant,
        to_status=ApplicantStatus.ENROLLED,
        actor=actor,
        reason=f"Enrolled and student record created ({student.admission_no}).",
    )

    # E21: Welcome email to parent on enrolment
    parent_email = (applicant.parent_email or "").strip() or None
    if parent_email:
        from core.email_templates import send_dynamic_email
        from core.models import SchoolSettings as _SS
        _ss = _SS.get_settings()
        _contact = _ss.get_admissions_contact()
        send_dynamic_email(
            template_type="admission_welcome",
            to_email=parent_email,
            context={
                "parent_name": applicant.parent_full_name or "Parent/Guardian",
                "child_name": applicant.child_full_name,
                "admission_no": student.admission_no,
                "grade": student.class_name,
                "class_name": student.class_name,
                "academic_year": str(timezone.now().year),
                "term_start_date": "See school calendar",
                "reference_number": applicant.reference_number,
                "school_name": _ss.school_name or "Hodari Christian School",
                "portal_link": "/parent/admission-form/",
                "admissions_phone": _contact.get("phone", ""),
                "uniform_notes": "Ensure correct uniform is worn from day one.",
                "meals": "School meals available — see fee schedule.",
                "transport_notes": "Transport available for qualifying zones.",
            },
        )

    # NOTIF-10: Notify Finance Officer to set up term fee invoicing
    from communications.email_service import dispatch_notification
    from users.models import User
    finance_officers = User.objects.filter(role=UserRole.FINANCE_OFFICER, is_active=True)
    for fo in finance_officers:
        dispatch_notification(
            user=fo,
            title="New Student Enrolled (Set Up Fee Invoicing)",
            message=(
                f"{student.first_name} {student.last_name} ({student.admission_no}) has been "
                f"enrolled in {student.class_name}. "
                f"Please set up term fee invoicing for this student."
            ),
            link=f"/finance/invoices/?q={student.admission_no}",
            actor=actor,
        )

    return student


@transaction.atomic
def revert_applicant_from_enrolled(*, applicant: Applicant, to_status: str, actor, reason: str = "") -> None:
    """Revert an enrolled applicant to a previous stage and archive the student record."""
    if applicant.status != ApplicantStatus.ENROLLED:
        raise ValidationError("Only enrolled applicants can be reverted.")

    student = applicant.enrolled_student
    if student:
        student.is_archived = True
        student.save(update_fields=["is_archived", "updated_at"])

        from audit.models import log_event
        log_event(
            actor=actor,
            action_type="STUDENT_ARCHIVED",
            model_name="Student",
            object_id=student.pk,
            description=f"Student {student.admission_no} ({student.first_name} {student.last_name}) archived — applicant {applicant.child_full_name} reverted from enrolled to {to_status}",
        )

        from students.models import EnrollmentHistory
        EnrollmentHistory.objects.create(
            student=student,
            academic_year=student.academic_year,
            class_name=student.class_name,
            action="withdrawn",
        )

    transition_applicant_status(
        applicant=applicant,
        to_status=to_status,
        actor=actor,
        reason=reason or f"Reverted from enrolled to {to_status}.",
    )


def get_critical_actions() -> list[dict]:
    actions = []
    
    # 1. Past due assessments without fee confirmation
    today = timezone.now().date()
    apps_unpaid = Applicant.objects.filter(
        assessment__isnull=False,
        assessment__assessment_fee_confirmed_paid=False,
        status__in=[ApplicantStatus.MEETING_SCHEDULED]
    ).select_related("assessment")
    
    for app in apps_unpaid:
        severity = "high" if app.assessment.scheduled_date < today else "amber"
        actions.append({
            "applicant": app,
            "title": "Assessment Fee Unpaid",
            "description": f"Assessment scheduled for {app.assessment.scheduled_date}. Fee must be confirmed before sending logistics.",
            "severity": severity,
            "link": f"/admissions/applicant/{app.id}/",
            "type": "finance"
        })

    # 2. Assessment completed but HOS review pending
    apps_hod = Applicant.objects.filter(status=ApplicantStatus.HOS_REVIEW)
    for app in apps_hod:
        actions.append({
            "applicant": app,
            "title": "HOS Review Pending",
            "description": "Assessment result submitted. HOS needs to sign off and provide recommendation.",
            "severity": "amber",
            "link": f"/admissions/applicant/{app.id}/",
            "type": "academic"
        })

    # 3. Admitted students with missing mandatory documents
    from django.db.models import Count, Q
    apps_admitted = Applicant.objects.filter(
        status__in=[ApplicantStatus.ADMITTED, ApplicantStatus.CONDITIONAL]
    ).annotate(
        missing_doc_count=Count("documents", filter=Q(documents__is_received=False))
    ).filter(missing_doc_count__gt=0)
    for app in apps_admitted:
        actions.append({
            "applicant": app,
            "title": f"Missing {app.missing_doc_count} Documents",
            "description": f"Admitted but cannot complete enrolment until all checklist items are received.",
            "severity": "amber",
            "link": f"/admissions/applicant/{app.id}/",
            "type": "admin"
        })

    # 4. HOS Decision pending
    apps_hos = Applicant.objects.filter(status=ApplicantStatus.HOS_DECISION)
    for app in apps_hos:
        actions.append({
            "applicant": app,
            "title": "HOS Decision Pending",
            "description": "HOD review complete. Final decision required by Head of School.",
            "severity": "high",
            "link": f"/admissions/applicant/{app.id}/",
            "type": "admin"
        })

    return actions
