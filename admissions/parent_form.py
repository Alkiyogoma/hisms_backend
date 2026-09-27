"""
Parent-completed admission form via a secure link (no parent account needed).

Flow:
  1. Staff enter the parent's name, email and phone -> ``start_parent_form``
     creates an Applicant at the admission-form stage plus an
     AdmissionFormInvite, and emails the parent a one-off link.
  2. The parent fills in learner and family details and uploads documents,
     saving as often as they like (``save_parent_draft``), then submits
     (``submit_parent_form``): the data is written onto the Applicant, the
     admission invoice is generated and the parent gets a confirmation.
  3. Staff review: ``request_changes`` re-opens the form with a message, or
     ``accept_parent_form`` marks the uploaded documents received. On
     enrolment the form's learner details fill the Student record, so nothing
     is retyped.
"""
import json
import secrets
from datetime import date, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from admissions.models import (
    AdmissionFormInvite, AdmissionFormInviteStatus as InviteStatus, Applicant,
    ApplicantDocumentReceipt, ApplicantDocumentType, ApplicantStatus, ApplicantTimelineEntry,
    EntryRoute, InquiryChannel,
)

LINK_VALID_DAYS = 30
# Anyone holding a link can upload, so only accept documents and photos
# (never SVG/HTML, which could carry script when served back to staff).
PUBLIC_UPLOAD_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "webp", "heic"}
PLACEHOLDER_CHILD_NAME = "Awaiting parent form"

# Upload key on the form -> (document type, label). Which are required is
# decided by ``required_uploads``.
UPLOADS = {
    "birth": (ApplicantDocumentType.BIRTH_CERTIFICATE, "Child's birth certificate"),
    "report": (ApplicantDocumentType.PREVIOUS_REPORT, "Previous school report"),
    "photo": (ApplicantDocumentType.STUDENT_PHOTO, "Passport photo of the child"),
    "passport": (ApplicantDocumentType.PASSPORT_PERMIT, "Passport or residence permit"),
    "clearance": (ApplicantDocumentType.CLEARANCE_FORM, "Clearance form from previous school"),
}


def required_uploads(child):
    """Birth certificate and photo always; previous school report unless the
    child has no previous school; passport/residence permit for non-Tanzanians."""
    keys = ["birth", "photo"]
    prev = (child.get("prevSchool") or "").strip().lower()
    if prev and prev not in ("none", "n/a", "na", "-"):
        keys.append("report")
    nationality = (child.get("nationality") or "").strip().lower()
    if nationality and nationality not in ("tanzanian", "tanzania"):
        keys.append("passport")
    return keys


def form_link(token):
    return settings.SITE_URL.rstrip("/") + reverse("admissions:parent_form", args=[token])


def _new_token(invite):
    token = secrets.token_urlsafe(32)
    invite.token_hash = AdmissionFormInvite.hash_token(token)
    invite.expires_at = timezone.now() + timedelta(days=LINK_VALID_DAYS)
    invite.sent_at = timezone.now()
    return token


def find_invite(token):
    return AdmissionFormInvite.objects.select_related("applicant").filter(
        token_hash=AdmissionFormInvite.hash_token(token or "")
    ).first()


def _school_name():
    from core.models import SchoolSettings
    return SchoolSettings.get_settings().school_name or "Hodari Christian School"


def _email_link(invite, token, *, intro):
    from communications.email_service import send_email_safe
    applicant = invite.applicant
    body = (
        f"Dear {applicant.parent_full_name},\n\n"
        f"{intro}\n\n"
        f"{form_link(token)}\n\n"
        "You can save the form and come back to it later using the same link. "
        f"The link is personal to you and works until {invite.expires_at:%d %B %Y}.\n\n"
        "Please have these ready to upload: the child's birth certificate, their most recent "
        "school report, a passport-size photo, and a passport or residence permit if the child "
        "is not Tanzanian.\n\n"
        f"Admissions Office\n{_school_name()}"
    )
    if applicant.parent_email:
        send_email_safe(
            to_email=applicant.parent_email,
            subject=f"Admission form for your child — {_school_name()}",
            body=body,
        )


@transaction.atomic
def start_parent_form(*, actor, parent_full_name, parent_email, parent_phone, grade=""):
    """Create the application and email the parent their form link."""
    applicant = Applicant.objects.create(
        parent_full_name=parent_full_name.strip(),
        parent_email=parent_email.strip().lower(),
        parent_phone=parent_phone.strip(),
        child_full_name=PLACEHOLDER_CHILD_NAME,
        child_date_of_birth=None,
        grade_applying_for=grade.strip(),
        inquiry_channel=InquiryChannel.WALK_IN,
        status=ApplicantStatus.ADMITTED,
        entry_route=EntryRoute.PARENT_LINK,
    )
    ApplicantTimelineEntry.objects.create(
        applicant=applicant, from_status=ApplicantStatus.INQUIRY_RECEIVED,
        to_status=ApplicantStatus.ADMITTED, actor=actor,
        reason="Application started by staff; admission form link sent to parent.",
    )
    invite = AdmissionFormInvite(applicant=applicant, created_by=actor)
    token = _new_token(invite)
    invite.save()
    _email_link(invite, token, intro=(
        f"{_school_name()} has started an admission application for your child. "
        "Please complete the admission form online using this link:"
    ))
    from audit.models import log_event
    log_event(
        actor=actor, action_type="ADMISSION_FORM_LINK_SENT", model_name="Applicant",
        object_id=applicant.pk,
        description=f"Admission form link sent to {applicant.parent_full_name} <{applicant.parent_email}>",
    )
    return applicant, token


@transaction.atomic
def resend_link(*, invite, actor):
    """Issue a fresh link (the old one stops working) and email it again."""
    token = _new_token(invite)
    if invite.status == InviteStatus.SENT and invite.opened_at:
        invite.status = InviteStatus.IN_PROGRESS
    invite.save()
    _email_link(invite, token, intro="Here is a new link to your child's admission form:")
    ApplicantTimelineEntry.objects.create(
        applicant=invite.applicant, from_status=invite.applicant.status,
        to_status=invite.applicant.status, actor=actor, reason="Admission form link re-sent to parent.",
    )
    return token


def mark_opened(invite):
    if invite.opened_at is None:
        invite.opened_at = timezone.now()
        if invite.status == InviteStatus.SENT:
            invite.status = InviteStatus.IN_PROGRESS
        invite.save(update_fields=["opened_at", "status", "updated_at"])


def _store_uploads(invite, files):
    """Save uploaded documents against the applicant. Files are validated
    strictly — this endpoint is public."""
    from academics.validators import validate_attachment_file
    saved = {}
    for key, (doc_type, label) in UPLOADS.items():
        upload = files.get("file_" + key)
        if not upload:
            continue
        ext = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
        if ext not in PUBLIC_UPLOAD_EXTENSIONS:
            raise ValidationError(f"{label}: please upload a PDF or a photo (JPG, PNG, WEBP or HEIC).")
        try:
            validate_attachment_file(upload, area="admission_documents")
        except ValidationError as exc:
            raise ValidationError(f"{label}: {' '.join(exc.messages)}")
        receipt, _ = ApplicantDocumentReceipt.objects.get_or_create(
            applicant=invite.applicant, document_type=doc_type,
        )
        receipt.file = upload
        receipt.save(update_fields=["file"])
        saved[key] = upload.name
    uploaded = dict(invite.draft_data.get("uploaded") or {})
    uploaded.update(saved)
    return uploaded


@transaction.atomic
def save_parent_draft(*, invite, data, files):
    if not invite.parent_can_edit:
        raise ValidationError("This form can no longer be changed. Please contact the school office.")
    uploaded = _store_uploads(invite, files)
    data = dict(data or {})
    data["uploaded"] = uploaded
    data.pop("files", None)
    invite.draft_data = data
    if invite.status == InviteStatus.SENT:
        invite.status = InviteStatus.IN_PROGRESS
    invite.save(update_fields=["draft_data", "status", "updated_at"])
    return uploaded


def _clean_str(value, limit):
    import re
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()[:limit]


@transaction.atomic
def submit_parent_form(*, invite, data, files):
    """Validate, copy the form onto the Applicant, generate the invoice and
    confirm to the parent. Returns the invoice."""
    from admissions.services import generate_admission_invoice, transition_applicant_status

    uploaded = save_parent_draft(invite=invite, data=data, files=files)
    data = invite.draft_data
    child = (data.get("children") or [{}])[0]
    guardian = (data.get("guardians") or [{}])[0]

    errors = []
    name = _clean_str(child.get("name"), 150)
    grade = _clean_str(child.get("grade"), 32)
    try:
        dob = date.fromisoformat(child.get("dob") or "")
    except ValueError:
        dob = None
    if not name:
        errors.append("Enter the child's full name.")
    if not dob or dob >= date.today():
        errors.append("Enter the child's date of birth.")
    from academics.models import GradeClass
    if not grade or not GradeClass.objects.filter(name__iexact=grade).exists():
        errors.append("Choose the grade the child is joining.")
    if not _clean_str(guardian.get("name"), 150) or not _clean_str(guardian.get("phone"), 32):
        errors.append("Enter the parent or guardian's name and phone number.")
    if not (data.get("consent") or {}).get("core"):
        errors.append("Consent to hold the child's data is required.")
    missing = [UPLOADS[k][1] for k in required_uploads(child) if not uploaded.get(k)]
    if missing:
        errors.append("Please upload: " + ", ".join(missing) + ".")
    if errors:
        raise ValidationError(errors)

    applicant = invite.applicant
    applicant.child_full_name = name
    applicant.child_date_of_birth = dob
    applicant.grade_applying_for = grade
    applicant.previous_school = _clean_str(child.get("prevSchool"), 120)
    applicant.parent_full_name = _clean_str(guardian.get("name"), 150)
    applicant.parent_phone = _clean_str(guardian.get("phone"), 32)
    if guardian.get("email"):
        applicant.parent_email = _clean_str(guardian.get("email"), 254).lower()
    rel = _clean_str(guardian.get("rel"), 30).lower().replace(" ", "_")
    applicant.parent_relationship = rel if rel in dict(Applicant.PARENT_RELATIONSHIP_CHOICES) else "guardian"
    notes = {k: v for k, v in data.items() if k not in ("uploaded", "step", "files", "submitted")}
    notes["submitted_via"] = "parent_link"
    # A second guardian becomes a linked (non-primary) guardian on enrolment.
    extra = (data.get("guardians") or [])[1:2]
    notes["additional_parents"] = [
        {"full_name": g.get("name", ""), "relationship": g.get("rel", ""), "phone": g.get("phone", ""), "email": g.get("email", "")}
        for g in extra if g.get("name")
    ]
    applicant.notes = json.dumps(notes)
    applicant.full_clean()
    applicant.save()

    now = timezone.now()
    ApplicantDocumentReceipt.objects.update_or_create(
        applicant=applicant, document_type=ApplicantDocumentType.ADMISSION_FORM,
        defaults={"is_received": True, "received_at": now},
    )

    resubmission = invite.status == InviteStatus.CHANGES_REQUESTED
    invite.status = InviteStatus.SUBMITTED
    invite.submitted_at = now
    invite.save(update_fields=["status", "submitted_at", "updated_at"])

    actor = invite.created_by
    if applicant.status in (ApplicantStatus.ADMITTED, ApplicantStatus.CONDITIONAL):
        transition_applicant_status(
            applicant=applicant, to_status=ApplicantStatus.FORM_SUBMITTED, actor=actor,
            reason="Parent completed the admission form via their link.",
        )
    elif resubmission:
        ApplicantTimelineEntry.objects.create(
            applicant=applicant, from_status=applicant.status, to_status=applicant.status,
            actor=actor, reason="Parent re-submitted the admission form after changes were requested.",
        )
    invoice, _created = generate_admission_invoice(
        applicant=applicant, actor=actor, reason="generated when the parent submitted the online form",
    )

    _confirm_to_parent(applicant, invoice, resubmission)
    _notify_admissions(applicant, resubmission)
    return invoice


def _confirm_to_parent(applicant, invoice, resubmission):
    from communications.email_service import send_email_safe
    if not applicant.parent_email:
        return
    what = "updated admission form" if resubmission else "admission form"
    send_email_safe(
        to_email=applicant.parent_email,
        subject=f"We've received the admission form for {applicant.child_full_name}",
        body=(
            f"Dear {applicant.parent_full_name},\n\n"
            f"Thank you. We have received the {what} for {applicant.child_full_name} "
            f"({applicant.grade_applying_for}). Your reference is {applicant.reference_number}.\n\n"
            f"Admission invoice {invoice.invoice_number}: TZS {invoice.total_due:,.0f}, "
            f"due {invoice.due_date:%d %B %Y}.\n\n"
            "Our admissions team will review the form and contact you if anything else is needed.\n\n"
            f"Admissions Office\n{_school_name()}"
        ),
    )


def _notify_admissions(applicant, resubmission):
    from communications.email_service import dispatch_notification
    from users.models import User, UserRole
    verb = "re-submitted" if resubmission else "submitted"
    for user in User.objects.filter(role=UserRole.ADMIN_OFFICER, is_active=True):
        dispatch_notification(
            user=user,
            title=f"Admission form {verb}: {applicant.child_full_name}",
            message=f"{applicant.parent_full_name} {verb} the admission form. Please review it.",
            link=f"/admissions/applicant/{applicant.pk}/",
            actor=None,
        )


@transaction.atomic
def request_changes(*, invite, actor, message):
    """Re-open the form for the parent with a note on what's missing."""
    message = (message or "").strip()
    if not message:
        raise ValidationError("Tell the parent what needs to be added or corrected.")
    if invite.status not in (InviteStatus.SUBMITTED, InviteStatus.CHANGES_REQUESTED):
        raise ValidationError("Changes can only be requested once the parent has submitted the form.")
    invite.status = InviteStatus.CHANGES_REQUESTED
    invite.review_message = message
    invite.reviewed_by = actor
    invite.reviewed_at = timezone.now()
    token = _new_token(invite)
    invite.save()
    _email_link(invite, token, intro=(
        "Thank you for completing the admission form. We need a little more from you:\n\n"
        f"{message}\n\nPlease update the form using this link:"
    ))
    ApplicantTimelineEntry.objects.create(
        applicant=invite.applicant, from_status=invite.applicant.status, to_status=invite.applicant.status,
        actor=actor, reason=f"Asked parent to update the admission form: {message}",
    )
    return token


@transaction.atomic
def accept_parent_form(*, invite, actor):
    """Accept the parent's form: every document they uploaded is marked received."""
    if invite.status != InviteStatus.SUBMITTED:
        raise ValidationError("Only a submitted form can be accepted.")
    now = timezone.now()
    invite.applicant.documents.filter(file__gt="").exclude(file__isnull=True).update(
        is_received=True, received_by=actor, received_at=now,
    )
    invite.status = InviteStatus.ACCEPTED
    invite.reviewed_by = actor
    invite.reviewed_at = now
    invite.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    ApplicantTimelineEntry.objects.create(
        applicant=invite.applicant, from_status=invite.applicant.status, to_status=invite.applicant.status,
        actor=actor, reason="Parent's admission form reviewed and accepted.",
    )


def student_details_from_form(applicant):
    """Learner fields captured on the online admission form, ready for the
    Student record (used on enrolment so nothing is retyped)."""
    from admissions.services import _parse_inquiry_notes
    notes = _parse_inquiry_notes(applicant)
    if notes.get("submitted_via") not in ("parent_link", "parent_portal_wizard"):
        return {}
    child = (notes.get("children") or [{}])[0]
    gender = (child.get("gender") or "").strip().lower()
    medical = "\n".join(
        f"{label}: {child[key]}" for key, label in (
            ("allergies", "Allergies"), ("meds", "Medication"), ("health", "General health"),
            ("sen", "Special needs"), ("disabilities", "Disabilities"),
        ) if (child.get(key) or "").strip()
    )
    details = {
        "gender": gender if gender in ("male", "female", "other") else "",
        "nationality": _clean_str(child.get("nationality"), 64),
        "religion": _clean_str(child.get("religion"), 64),
        "allergies_medical": medical,
    }
    return {k: v for k, v in details.items() if v}
