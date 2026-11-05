"""
Parent sign-in with a one-time code sent to the phone (SMS) or email address
the school holds for the parent.

Flow: the parent enters their phone number or email, we send a short code to
it, and the parent types it back to sign in. Codes are short-lived,
single-use, limited in tries, and only their HMAC is stored. Requests are
throttled per contact and per client address, server-side.

The code is never returned to the browser. The only exception is the local
development preview, which needs DEBUG *and* PARENT_OTP_DEV_PREVIEW=1.
"""
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import F, Q
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from users.models import ParentLoginCode, UserRole

logger = logging.getLogger(__name__)

CODE_LENGTH = int(getattr(settings, "PARENT_OTP_CODE_LENGTH", 4))
CODE_TTL_SECONDS = int(getattr(settings, "PARENT_OTP_TTL_SECONDS", 600))
MAX_ATTEMPTS = int(getattr(settings, "PARENT_OTP_MAX_ATTEMPTS", 5))
RESEND_COOLDOWN_SECONDS = int(getattr(settings, "PARENT_OTP_RESEND_SECONDS", 45))
MAX_CODES_PER_HOUR = int(getattr(settings, "PARENT_OTP_MAX_PER_HOUR", 5))
MAX_REQUESTS_PER_CLIENT_HOUR = int(getattr(settings, "PARENT_OTP_MAX_PER_CLIENT_HOUR", 30))
# Codes are short, so wrong guesses are also capped per contact per day:
# at most this many guesses across all codes, then no new codes until tomorrow.
MAX_FAILED_PER_DAY = int(getattr(settings, "PARENT_OTP_MAX_FAILED_PER_DAY", 10))

SMS = ParentLoginCode.CHANNEL_SMS
EMAIL = ParentLoginCode.CHANNEL_EMAIL

# Session flag: this session was opened with a one-time code, not a password.
SESSION_LOGIN_METHOD_KEY = "_login_method"
LOGIN_METHOD_SMS = "sms_code"
LOGIN_METHOD_EMAIL = "email_code"


class OTPError(Exception):
    """A request the parent can fix; the message is safe to show them."""

    def __init__(self, message, status=400, code="invalid"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


@dataclass(frozen=True)
class Contact:
    channel: str   # SMS or EMAIL
    value: str     # 255XXXXXXXXX or lower-case email

    @property
    def masked(self):
        if self.channel == SMS:
            return f"+{self.value[:3]} ••• ••• {self.value[-3:]}"
        local, _, domain = self.value.partition("@")
        if len(local) <= 2:
            return f"{local[:1]}•••@{domain}"
        return f"{local[0]}{'•' * min(len(local) - 2, 5)}{local[-1]}@{domain}"


def normalize_phone(phone):
    from attendance.notification_service import normalize_tz_phone
    return normalize_tz_phone(phone)


def parse_contact(raw):
    """Contact for a phone number or email typed by the parent, or OTPError."""
    value = str(raw or "").strip()
    if not value:
        raise OTPError("Enter the phone number or email the school has on record.")
    if "@" in value:
        try:
            validate_email(value)
        except ValidationError:
            raise OTPError("That email does not look right. Check it and try again.")
        return Contact(EMAIL, value.lower())
    phone = normalize_phone(value)
    if not phone:
        raise OTPError("That phone number does not look right. Use a Tanzanian mobile number, "
                       "e.g. 0712 345 678.")
    return Contact(SMS, phone)


def sms_available():
    """SMS really goes out (or, in DEBUG, the console provider logs the code)."""
    from attendance.notification_service import sms_status
    if sms_status()["configured"]:
        return True
    return settings.DEBUG and (getattr(settings, "SMS_PROVIDER", "") or "").lower() == "console"


def email_available():
    from core.email_backend import resolve_delivery
    try:
        return resolve_delivery()[2]["delivering"] or settings.DEBUG
    except Exception:
        logger.exception("Could not resolve email delivery for parent sign-in")
        return False


def dev_preview_enabled():
    """Show the sent message on screen: local development only, opt-in."""
    return bool(settings.DEBUG and getattr(settings, "PARENT_OTP_DEV_PREVIEW", False))


def school_contact_phone():
    try:
        from core.models import SchoolSettings
        return SchoolSettings.get_settings().get_parent_contact_phone()
    except Exception:
        return ""


def _call_school(prefix):
    phone = school_contact_phone()
    return f"{prefix} call the school on {phone}." if phone else f"{prefix} contact the school office."


def not_registered_message():
    return _call_school("We can't find that phone number or email in our records. "
                        "Check it and try again, or")


def find_parent_users(contact):
    """Active parent accounts whose guardian record has this contact.

    Phones are stored in many formats, so they are compared in Python after
    normalising. Emails match the guardian record or the parent's account."""
    from students.models import ParentGuardian
    if isinstance(contact, str):  # legacy callers pass a normalised phone
        contact = Contact(SMS, contact)
    guardians = (ParentGuardian.objects
                 .filter(user__isnull=False, user__role=UserRole.PARENT, user__is_active=True)
                 .select_related("user"))
    users = {}
    if contact.channel == EMAIL:
        for g in guardians.filter(Q(email__iexact=contact.value) | Q(user__email__iexact=contact.value)):
            users[g.user.pk] = g.user
        return list(users.values())
    for g in guardians.only("phone", "user").iterator():
        if normalize_phone(g.phone) == contact.value:
            users[g.user.pk] = g.user
    return list(users.values())


def _throttle(contact):
    """Per-contact limits, applied before the lookup so registered and unknown
    contacts are limited the same way. Kept in the shared cache (Redis in
    production)."""
    from django.core.cache import cache
    cooldown_key, hourly_key = f"parent-otp:cooldown:{contact}", f"parent-otp:hourly:{contact}"
    if (cache.get(_failed_key(contact)) or 0) >= MAX_FAILED_PER_DAY:
        raise OTPError(_call_school("Too many wrong codes were entered today. Try again tomorrow, or"),
                       429, "daily_limit")
    if not cache.add(cooldown_key, 1, RESEND_COOLDOWN_SECONDS):
        raise OTPError(f"Please wait {RESEND_COOLDOWN_SECONDS} seconds before requesting another code.",
                       429, "cooldown")
    if _incr(cache, hourly_key) > MAX_CODES_PER_HOUR:
        raise OTPError("Too many codes requested for this phone number or email. Try again in an hour.",
                       429, "hourly_limit")


def throttle_client(client_ip):
    """Per-address limit on sign-in requests, so the form can't be used to
    check many contacts quickly."""
    if not client_ip:
        return
    from django.core.cache import cache
    if _incr(cache, f"parent-otp:client:{client_ip}") > MAX_REQUESTS_PER_CLIENT_HOUR:
        raise OTPError("Too many sign-in attempts from this device. Please try again later.",
                       429, "client_limit")


def _failed_key(contact):
    return f"parent-otp:failed:{contact}"


def _incr(cache, key, timeout=3600):
    cache.add(key, 0, timeout)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout)
        return 1


def _hash(user_id, contact, code):
    return salted_hmac("users.otp.ParentLoginCode", f"{user_id}:{contact}:{code}").hexdigest()


def _can_sign_in(user):
    today = timezone.localdate()
    return user.is_active and not (user.departure_date and user.departure_date <= today)


def _send(contact, subject, text):
    """Send text to the contact; True on success."""
    if contact.channel == SMS:
        from attendance.notification_service import NotificationService
        result = NotificationService.send_sms(contact.value, text)
        if not result.get("success"):
            logger.error("Parent sign-in SMS failed: %s", result.get("error"))
        return bool(result.get("success"))
    from communications.email_service import send_email_safe
    return bool(send_email_safe(to_email=contact.value, subject=subject, body=text,
                                action_type="parent_login_code"))


def request_code(raw_contact, *, reveal_unregistered=False, client_ip=None):
    """Send a sign-in code to the phone or email if it belongs to exactly one
    active parent.

    With reveal_unregistered (the parent portal's sign-in screen), an unknown
    contact raises OTPError(code="not_registered") with the school's number.
    Otherwise (mobile API) it returns normally, so the reply can't be used to
    find out which contacts are registered.

    Returns {"channel", "masked", "sent", "preview"}. "preview" is the message
    text only when the local development preview is enabled.
    """
    from audit.models import log_event

    contact = parse_contact(raw_contact)
    if contact.channel == SMS and not sms_available():
        raise OTPError(_call_school("SMS sign-in is not available right now. Try your email instead, or"),
                       503, "unavailable")
    if contact.channel == EMAIL and not email_available():
        raise OTPError(_call_school("Email sign-in is not available right now. Try your phone number instead, or"),
                       503, "unavailable")

    throttle_client(client_ip)
    _throttle(contact.value)

    result = {"channel": contact.channel, "masked": contact.masked, "sent": False, "preview": ""}
    users = find_parent_users(contact)
    if not users:
        logger.info("Parent sign-in requested for an unregistered %s", contact.channel)
        if reveal_unregistered:
            raise OTPError(not_registered_message(), 404, "not_registered")
        return result
    school = _school_name()
    if len(users) > 1:
        # Only the contact's owner reads this, so it reveals nothing to anyone else.
        _send(contact, f"{school} parent portal", (
            f"{school}: this phone number or email is linked to more than one parent account, so it "
            f"can't be used to sign in. Please contact the school to update your details."
        ))
        if reveal_unregistered:
            raise OTPError(_call_school("This phone number or email is linked to more than one parent "
                                        "account, so it can't be used to sign in. Please"), 409, "shared")
        return result

    user = users[0]
    if not _can_sign_in(user):
        if reveal_unregistered:
            raise OTPError(_call_school("Your parent account is not active. Please"), 403, "inactive")
        return result

    now = timezone.now()
    code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
    # Only the newest code works; older unused ones stop working.
    ParentLoginCode.objects.filter(contact=contact.value, used_at__isnull=True).update(used_at=now)
    ParentLoginCode.objects.create(
        user=user, contact=contact.value, channel=contact.channel,
        code_hash=_hash(user.pk, contact.value, code),
        expires_at=now + timedelta(seconds=CODE_TTL_SECONDS),
    )
    minutes = max(1, CODE_TTL_SECONDS // 60)
    text = (f"{code} is your {school} parent portal sign-in code. It expires in {minutes} minutes. "
            f"Never share this code with anyone, including school staff.")
    sent = _send(contact, f"Your {school} parent portal code", text)
    log_event(actor=user, action_type="LOGIN_CODE_SENT", model_name="User", object_id=user.pk,
              description=f"Parent sign-in code sent by {contact.channel} to {contact.masked}"
                          + ("" if sent else " (sending failed)"))
    if not sent:
        raise OTPError("We couldn't send the code. Please try again shortly.", 502, "send_failed")
    result["sent"] = True
    if dev_preview_enabled():
        result["preview"] = text
    return result


def verify_code(raw_contact, code):
    """Return the parent User the code signs in, or raise OTPError."""
    from audit.models import log_event

    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    try:
        contact = parse_contact(raw_contact)
    except OTPError:
        contact = None
    if contact is None or len(code) != CODE_LENGTH:
        raise OTPError(f"Enter the {CODE_LENGTH}-digit code we sent you.")

    now = timezone.now()
    entry = (ParentLoginCode.objects.select_related("user")
             .filter(contact=contact.value, used_at__isnull=True, expires_at__gt=now).first())
    # One reply for "no code", "expired" and "wrong", so guessing can't reveal
    # whether someone has just requested a code for this contact.
    invalid = OTPError("That code is not correct or has expired. Check the message or request a new code.", 401)
    if entry is None or entry.attempts >= MAX_ATTEMPTS:
        raise invalid

    if not constant_time_compare(entry.code_hash, _hash(entry.user_id, contact.value, code)):
        # F() so parallel guesses can't slip past the attempt limit.
        ParentLoginCode.objects.filter(pk=entry.pk).update(attempts=F("attempts") + 1)
        entry.refresh_from_db(fields=["attempts"])
        from django.core.cache import cache
        failed_today = _incr(cache, _failed_key(contact.value), 86400)
        if entry.attempts >= MAX_ATTEMPTS or failed_today >= MAX_FAILED_PER_DAY:
            ParentLoginCode.objects.filter(pk=entry.pk).update(used_at=now)
        log_event(actor=entry.user, action_type="FAILED_LOGIN", model_name="User",
                  object_id=entry.user_id,
                  description=f"Wrong sign-in code (attempt {entry.attempts}/{MAX_ATTEMPTS})")
        raise invalid

    # Conditional update so two simultaneous submissions can't both use it.
    if not ParentLoginCode.objects.filter(pk=entry.pk, used_at__isnull=True).update(used_at=now):
        raise invalid
    user = entry.user
    if user.role != UserRole.PARENT or not _can_sign_in(user):
        raise OTPError("Your account is not active. Contact the school.", 403)
    user.otp_channel = entry.channel
    return user


def _school_name():
    try:
        from core.models import SchoolSettings
        return SchoolSettings.get_settings().school_name or "Hodari"
    except Exception:
        return "Hodari"
