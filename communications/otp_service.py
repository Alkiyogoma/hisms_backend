"""
OTP foundation for parent verification (USSD / SMS gateway to be wired in settings).

Configure in .env:
  SMS_PROVIDER=darsms|console  (see config/settings.py)
  DARSMS_API_KEY=...
"""

import hashlib
import logging
import os
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone


logger = logging.getLogger(__name__)

OTP_LENGTH = 6
OTP_TTL_SECONDS = int(os.getenv("OTP_TTL_SECONDS", "300"))


def _hash_code(phone: str, code: str) -> str:
    pepper = os.getenv("DJANGO_SECRET_KEY", "dev")
    return hashlib.sha256(f"{pepper}:{phone}:{code}".encode()).hexdigest()


def issue_code(key: str, length: int = OTP_LENGTH, ttl_seconds: int = OTP_TTL_SECONDS) -> str:
    """Store a fresh one-time code under ``key`` (a phone number or any short
    id, max 32 chars), cancelling earlier unused ones, and return it. The
    caller is responsible for delivering it."""
    from communications.models import PhoneOTP

    code = "".join(secrets.choice("0123456789") for _ in range(length))
    PhoneOTP.objects.filter(phone=key, consumed_at__isnull=True).update(consumed_at=timezone.now())
    PhoneOTP.objects.create(
        phone=key,
        code_hash=_hash_code(key, code),
        expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
    )
    return code


def cancel_codes(key: str) -> None:
    """Void every unused code stored under ``key``."""
    from communications.models import PhoneOTP

    PhoneOTP.objects.filter(phone=key, consumed_at__isnull=True).update(consumed_at=timezone.now())


def generate_otp(phone: str) -> str:
    """Create and store a one-time code for the given phone number."""
    code = issue_code(phone)
    _dispatch_sms(phone, f"Hodari School: your verification code is {code}. Valid for {OTP_TTL_SECONDS // 60} min.")
    return code if os.getenv("OTP_DEBUG", "0") == "1" else ""


def verify_otp(phone: str, code: str) -> bool:
    from communications.models import PhoneOTP

    record = (
        PhoneOTP.objects.filter(phone=phone, consumed_at__isnull=True, expires_at__gte=timezone.now())
        .order_by("-created_at")
        .first()
    )
    if not record or record.code_hash != _hash_code(phone, code):
        return False
    record.consumed_at = timezone.now()
    record.save(update_fields=["consumed_at", "updated_at"])
    return True


def _dispatch_sms(phone: str, message: str) -> None:
    provider = (getattr(settings, "SMS_PROVIDER", "") or "console").lower()
    if provider == "console":
        if settings.DEBUG:
            print(f"[SMS:{phone}] {message}")
        else:
            logger.warning("SMS_PROVIDER=console but DEBUG=False; no SMS sent to %s", phone)
        return
    from attendance.notification_service import NotificationService
    result = NotificationService.send_sms(phone, message)
    if not result.get("success"):
        logger.error("OTP SMS to %s failed: %s", phone, result.get("error"))
