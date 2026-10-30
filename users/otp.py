"""
Parent sign-in with a phone number and a one-time SMS code.

Flow: the parent enters the phone number the school holds for them, we text a
6-digit code to that number, and the parent types it back to sign in. Codes
are short-lived, single-use, limited in tries, and only their HMAC is stored.
"""
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from users.models import ParentLoginCode, User, UserRole

logger = logging.getLogger(__name__)

CODE_LENGTH = 6
CODE_TTL_SECONDS = int(getattr(settings, "PARENT_OTP_TTL_SECONDS", 300))
MAX_ATTEMPTS = int(getattr(settings, "PARENT_OTP_MAX_ATTEMPTS", 5))
RESEND_COOLDOWN_SECONDS = int(getattr(settings, "PARENT_OTP_RESEND_SECONDS", 60))
MAX_CODES_PER_HOUR = int(getattr(settings, "PARENT_OTP_MAX_PER_HOUR", 5))

# Session flag: this session was opened with an SMS code, not a password.
SESSION_LOGIN_METHOD_KEY = "_login_method"
LOGIN_METHOD_SMS = "sms_code"


class OTPError(Exception):
    """A request the parent can fix; the message is safe to show them."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def normalize_phone(phone):
    from attendance.notification_service import normalize_tz_phone
    return normalize_tz_phone(phone)


def sms_available():
    """SMS really goes out (or, in DEBUG, the console provider logs the code)."""
    from attendance.notification_service import sms_status
    if sms_status()["configured"]:
        return True
    return settings.DEBUG and (getattr(settings, "SMS_PROVIDER", "") or "").lower() == "console"


def find_parent_users(phone):
    """Active parent accounts whose guardian record has this phone (normalised)
    as its main number. Phones are stored in many formats, so compare in Python."""
    from students.models import ParentGuardian
    users = {}
    guardians = (ParentGuardian.objects
                 .filter(user__isnull=False, user__role=UserRole.PARENT, user__is_active=True)
                 .select_related("user").only("phone", "user"))
    for g in guardians.iterator():
        if normalize_phone(g.phone) == phone:
            users[g.user.pk] = g.user
    return list(users.values())


def _throttle(phone):
    """Per-number limits, applied the same way to registered and unknown
    numbers so the replies can't reveal which numbers belong to parents.
    Kept in the shared cache (Redis in production)."""
    from django.core.cache import cache
    cooldown_key, hourly_key = f"parent-otp:cooldown:{phone}", f"parent-otp:hourly:{phone}"
    if not cache.add(cooldown_key, 1, RESEND_COOLDOWN_SECONDS):
        raise OTPError(f"Please wait {RESEND_COOLDOWN_SECONDS} seconds before requesting another code.", 429)
    cache.add(hourly_key, 0, 3600)
    try:
        sent = cache.incr(hourly_key)
    except ValueError:
        cache.set(hourly_key, 1, 3600)
        sent = 1
    if sent > MAX_CODES_PER_HOUR:
        raise OTPError("Too many codes requested for this number. Try again in an hour.", 429)


def _hash(user_id, phone, code):
    return salted_hmac("users.otp.ParentLoginCode", f"{user_id}:{phone}:{code}").hexdigest()


def _can_sign_in(user):
    today = timezone.localdate()
    return user.is_active and not (user.departure_date and user.departure_date <= today)


def request_code(raw_phone):
    """Text a sign-in code to raw_phone if it belongs to exactly one parent.

    Returns normally whether or not the number is registered, so the form can't
    be used to find out which numbers belong to school parents. Raises OTPError
    for problems the parent can act on (bad number, too many requests, SMS down).
    """
    from attendance.notification_service import NotificationService
    from audit.models import log_event

    phone = normalize_phone(raw_phone)
    if not phone:
        raise OTPError("Enter a valid Tanzanian mobile number, e.g. 0712 345 678.")
    if not sms_available():
        raise OTPError("SMS sign-in is not available right now. Please try again later or contact the school.", 503)

    now = timezone.now()
    _throttle(phone)

    users = find_parent_users(phone)
    if not users:
        logger.info("Parent SMS sign-in requested for unregistered number %s", phone)
        return
    school = _school_name()
    if len(users) > 1:
        # Only the phone's owner reads this, so it reveals nothing to anyone else.
        NotificationService.send_sms(phone, (
            f"{school}: this number is linked to more than one parent account, so it can't be "
            f"used to sign in. Please contact the school to update your phone number."
        ))
        return

    user = users[0]
    if not _can_sign_in(user):
        return
    code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
    # Only the newest code works; older unused ones stop working.
    ParentLoginCode.objects.filter(phone=phone, used_at__isnull=True).update(used_at=now)
    ParentLoginCode.objects.create(
        user=user, phone=phone, code_hash=_hash(user.pk, phone, code),
        expires_at=now + timedelta(seconds=CODE_TTL_SECONDS),
    )
    minutes = max(1, CODE_TTL_SECONDS // 60)
    result = NotificationService.send_sms(phone, (
        f"{code} is your {school} parent portal sign-in code. It expires in {minutes} minutes. "
        f"Never share this code with anyone, including school staff."
    ))
    log_event(actor=user, action_type="LOGIN_CODE_SENT", model_name="User", object_id=user.pk,
              description=f"Parent sign-in code sent by SMS to {phone[:6]}***{phone[-2:]}"
                          + ("" if result.get("success") else f" (SMS failed: {result.get('error')})"))
    if not result.get("success"):
        raise OTPError("We couldn't send the SMS. Please try again shortly.", 502)


def verify_code(raw_phone, code):
    """Return the parent User the code signs in, or raise OTPError."""
    from audit.models import log_event

    phone = normalize_phone(raw_phone)
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if not phone or len(code) != CODE_LENGTH:
        raise OTPError(f"Enter the {CODE_LENGTH}-digit code we sent you.")

    now = timezone.now()
    entry = (ParentLoginCode.objects.select_related("user")
             .filter(phone=phone, used_at__isnull=True, expires_at__gt=now).first())
    # One reply for "no code", "expired" and "wrong", so guessing can't reveal
    # whether someone has just requested a code for this number.
    invalid = OTPError("That code is not correct or has expired. Check the SMS or request a new code.", 401)
    if entry is None or entry.attempts >= MAX_ATTEMPTS:
        raise invalid

    if not constant_time_compare(entry.code_hash, _hash(entry.user_id, phone, code)):
        # F() so parallel guesses can't slip past the attempt limit.
        ParentLoginCode.objects.filter(pk=entry.pk).update(attempts=F("attempts") + 1)
        entry.refresh_from_db(fields=["attempts"])
        if entry.attempts >= MAX_ATTEMPTS:
            ParentLoginCode.objects.filter(pk=entry.pk).update(used_at=now)
        log_event(actor=entry.user, action_type="FAILED_LOGIN", model_name="User",
                  object_id=entry.user_id,
                  description=f"Wrong SMS sign-in code (attempt {entry.attempts}/{MAX_ATTEMPTS})")
        raise invalid

    # Conditional update so two simultaneous submissions can't both use it.
    if not ParentLoginCode.objects.filter(pk=entry.pk, used_at__isnull=True).update(used_at=now):
        raise invalid
    user = entry.user
    if user.role != UserRole.PARENT or not _can_sign_in(user):
        raise OTPError("Your account is not active. Contact the school.", 403)
    return user


def _school_name():
    try:
        from core.models import SchoolSettings
        return SchoolSettings.get_settings().school_name or "Hodari"
    except Exception:
        return "Hodari"
