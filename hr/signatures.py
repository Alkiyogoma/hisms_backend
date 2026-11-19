"""Staff signatures: validation, import, approval.

A signature is a transparent PNG of someone's handwritten signature. It is
stored privately (see ``StaffSignature``) and printed on report cards only
once the Head of School has approved it.
"""
import csv
import io
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from audit.models import log_event

from .models import SignatureStatus, StaffProfile, StaffSignature

MAX_BYTES = 2 * 1024 * 1024
MAX_SIDE = 4000
MIN_SIDE = 40


def validate_signature_png(raw: bytes) -> tuple[int, int]:
    """Check ``raw`` is a real PNG of sensible size. Returns (width, height)."""
    from PIL import Image, UnidentifiedImageError

    if not raw:
        raise ValidationError("The file is empty.")
    if len(raw) > MAX_BYTES:
        raise ValidationError("The signature image must be under 2 MB.")
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValidationError("The signature must be a PNG image.")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img.verify()
        with Image.open(io.BytesIO(raw)) as img:
            width, height = img.size
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValidationError("The PNG file is damaged or unreadable.") from exc
    if max(width, height) > MAX_SIDE or min(width, height) < MIN_SIDE:
        raise ValidationError(
            f"The signature image is {width}×{height} px; each side must be {MIN_SIDE}–{MAX_SIDE} px."
        )
    return width, height


def save_signature(user, raw: bytes, *, source="", signed_at=None, consent_at=None, uploaded_by=None):
    """Store ``raw`` as ``user``'s signature, waiting for approval.

    Replacing an existing signature (approved or not) sends it back for
    approval, so a new image is never printed unseen."""
    validate_signature_png(raw)
    with transaction.atomic():
        sig = StaffSignature.objects.select_for_update().filter(user=user).first()
        old_file = sig.image.name if sig and sig.image else None
        created = sig is None
        if sig is None:
            sig = StaffSignature(user=user)
        sig.status = SignatureStatus.PENDING
        sig.source = source[:40]
        sig.signed_at = signed_at
        sig.consent_at = consent_at
        sig.uploaded_by = uploaded_by
        sig.reviewed_by = None
        sig.reviewed_at = None
        sig.reject_reason = ""
        sig.image.save("signature.png", ContentFile(raw), save=False)
        sig.save()
        if old_file:
            storage = sig.image.storage
            transaction.on_commit(lambda: storage.delete(old_file))
    log_event(
        actor=uploaded_by,
        action_type="SIGNATURE_UPLOADED" if created else "SIGNATURE_REPLACED",
        model_name="StaffSignature",
        object_id=sig.pk,
        description=f"Signature for {user.get_full_name() or user.username} stored ({source or 'upload'}); waiting for approval.",
    )
    return sig


def can_approve(user) -> bool:
    from users.models import UserRole
    return user.is_authenticated and (
        user.role == UserRole.SUPER_ADMIN or user.has_perm("hr.approve_staffsignature")
    )


def approve_signature(sig: StaffSignature, by, request=None):
    if not can_approve(by):
        raise ValidationError("Only the Head of School can approve signatures.")
    if sig.status == SignatureStatus.APPROVED:
        return sig
    sig.status = SignatureStatus.APPROVED
    sig.reviewed_by = by
    sig.reviewed_at = timezone.now()
    sig.reject_reason = ""
    sig.save(update_fields=["status", "reviewed_by", "reviewed_at", "reject_reason", "updated_at"])
    log_event(
        actor=by, action_type="SIGNATURE_APPROVED", model_name="StaffSignature", object_id=sig.pk,
        description=f"Approved signature of {sig.user.get_full_name() or sig.user.username}.", request=request,
    )
    return sig


def reject_signature(sig: StaffSignature, by, reason="", request=None):
    if not can_approve(by):
        raise ValidationError("Only the Head of School can reject signatures.")
    sig.status = SignatureStatus.REJECTED
    sig.reviewed_by = by
    sig.reviewed_at = timezone.now()
    sig.reject_reason = (reason or "").strip()[:255]
    sig.save(update_fields=["status", "reviewed_by", "reviewed_at", "reject_reason", "updated_at"])
    log_event(
        actor=by, action_type="SIGNATURE_REJECTED", model_name="StaffSignature", object_id=sig.pk,
        description=f"Rejected signature of {sig.user.get_full_name() or sig.user.username}"
                    + (f": {sig.reject_reason}" if sig.reject_reason else "."),
        request=request,
    )
    return sig


# ---------------------------------------------------------------------------
# Import from the signing kiosk export (manifest.csv + png/)
# ---------------------------------------------------------------------------

@dataclass
class ImportResult:
    imported: list = field(default_factory=list)    # (staff_id, name, matched_by)
    unchanged: list = field(default_factory=list)   # (staff_id, name)
    unmatched: list = field(default_factory=list)   # (staff_id, name, email, reason)
    failed: list = field(default_factory=list)      # (staff_id, name, reason)


def _parse_dt(value):
    dt = parse_datetime((value or "").strip())
    if dt is not None and timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def _match_user(staff_id, email):
    """Staff ID first (emails in the export can be shared inboxes or typos),
    then email. Returns (user, matched_by) or (None, reason)."""
    from users.models import User

    staff_id = (staff_id or "").strip()
    email = (email or "").strip()
    if staff_id and not staff_id.upper().startswith("NOID"):
        profile = StaffProfile.objects.select_related("user").filter(employee_id__iexact=staff_id).first()
        if profile:
            return profile.user, "staff ID"
    if email:
        users = list(User.objects.filter(email__iexact=email)[:2])
        if len(users) == 1:
            return users[0], "email"
    return None, "no staff profile with this ID and no user with this email"


def import_signature_export(folder, *, by=None, dry_run=False) -> ImportResult:
    """Import every row of ``folder/manifest.csv``. Each signature arrives as
    *waiting for approval*. A row whose PNG is byte-identical to the stored
    one is left alone, so re-running the import is safe."""
    folder = Path(folder)
    manifest = folder / "manifest.csv"
    if not manifest.is_file():
        raise ValidationError(f"{manifest} not found.")
    root = folder.resolve()
    result = ImportResult()
    with manifest.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        staff_id = (row.get("staff_id") or "").strip()
        name = (row.get("name") or "").strip()
        user, how = _match_user(staff_id, row.get("email"))
        if user is None:
            result.unmatched.append((staff_id, name, (row.get("email") or "").strip(), how))
            continue
        rel = (row.get("png_file") or "").strip()
        path = (folder / rel).resolve()
        if not rel or root not in path.parents or not path.is_file():
            result.failed.append((staff_id, name, f"PNG file '{rel}' not found in the export"))
            continue
        raw = path.read_bytes()
        try:
            validate_signature_png(raw)
        except ValidationError as exc:
            result.failed.append((staff_id, name, "; ".join(exc.messages)))
            continue
        existing = StaffSignature.objects.filter(user=user).first()
        if existing and _same_bytes(existing, raw):
            result.unchanged.append((staff_id, name))
            continue
        if not dry_run:
            save_signature(
                user, raw,
                source=(row.get("signed_via") or "signing kiosk").strip(),
                signed_at=_parse_dt(row.get("signed_at_utc")),
                consent_at=_parse_dt(row.get("consent_at_utc")),
                uploaded_by=by,
            )
        result.imported.append((staff_id, name, how))
    return result


def _same_bytes(sig, raw):
    try:
        with sig.image.open("rb") as f:
            return f.read() == raw
    except (OSError, ValueError):
        return False
