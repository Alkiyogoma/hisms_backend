"""
OTP foundation for parent verification (USSD / SMS gateway to be wired in settings).

Configure in .env:
  SMS_PROVIDER=console|africas_talking|...
  SMS_API_KEY=...
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


def generate_otp(phone: str) -> str:
    """Create and store a one-time code for the given phone number."""
    from communications.models import PhoneOTP

    code = "".join(secrets.choice("0123456789") for _ in range(OTP_LENGTH))
    PhoneOTP.objects.filter(phone=phone, consumed_at__isnull=True).update(consumed_at=timezone.now())
    PhoneOTP.objects.create(
        phone=phone,
        code_hash=_hash_code(phone, code),
        expires_at=timezone.now() + timedelta(seconds=OTP_TTL_SECONDS),
    )
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
    provider = os.getenv("SMS_PROVIDER", "console")
    if provider == "console":
        if settings.DEBUG:
            print(f"[SMS:{phone}] {message}")
        else:
            logger.warning("SMS_PROVIDER=console but DEBUG=False; no SMS sent to %s", phone)
        return
    # Future: AfricasTalking, Twilio, etc.
