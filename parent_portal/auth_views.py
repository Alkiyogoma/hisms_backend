"""Parent sign-in by one-time code (phone number or email).

Step 1: the parent types the phone number or email the school has on record.
Step 2: a 4-digit code goes to that number (SMS) or address (email); entering
it signs the parent in.

Any parent on record with at least one active child can sign in. There is no
separate "activate portal access" step: the PARENT login is created (or
re-enabled) the first time the code is verified. Archived parents are refused.
"""
import logging

from django.conf import settings
from django.contrib.auth import login
from django.core.cache import cache
from django.core.mail import send_mail
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from attendance.notification_service import NotificationService, normalize_tz_phone
from communications.otp_service import cancel_codes, issue_code, verify_otp
from students.models import ParentGuardian, StudentStatus
from students.services import ensure_parent_user
from users.models import UserRole

logger = logging.getLogger(__name__)

CODE_LENGTH = 4
CODE_TTL_SECONDS = 600          # code expires in 10 minutes
RESEND_AFTER_SECONDS = 45       # "Resend code" unlocks after this
MAX_CODES_PER_HOUR = 5          # per parent
MAX_WRONG_TRIES = 5             # per code; then the code is voided
MAX_LOOKUPS_PER_15_MIN = 20     # per IP, step 1
SESSION_KEY = "parent_signin"


def _school_settings():
    try:
        from core.models import SchoolSettings
        return SchoolSettings.get_settings()
    except Exception:
        return None


def _school_phone():
    return (getattr(_school_settings(), "admissions_phone", "") or "").strip() or "0767 016 940"


def _client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR", "")) or "unknown"


def _hit(key, limit, window):
    """Count one use of ``key``; True when the limit for the window is exceeded."""
    cache.add(key, 0, window)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        count = 1
    return count > limit


def _eligible(qs):
    return (qs.filter(is_archived=False, studentguardian__student__status=StudentStatus.ACTIVE)
            .distinct())


def find_guardian(identifier):
    """Resolve what the parent typed to one ParentGuardian.

    Returns (guardian, channel, address) or (None, error_code, None) where
    error_code is "bad_email", "bad_phone", "not_found" or "ambiguous"."""
    value = (identifier or "").strip()
    if "@" in value:
        local, _, domain = value.partition("@")
        if not local or "." not in domain or " " in value:
            return None, "bad_email", None
        matches = list(_eligible(ParentGuardian.objects.filter(email__iexact=value))[:2])
        if not matches:
            matches = list(_eligible(ParentGuardian.objects.filter(user__email__iexact=value))[:2])
        if len(matches) > 1:
            return None, "ambiguous", None
        return (matches[0], "email", value.lower()) if matches else (None, "not_found", None)

    phone = normalize_tz_phone(value)
    if not phone:
        return None, "bad_phone", None
    # Stored numbers come in every format ("0712 345 678", "+255712..."), so
    # normalise in Python rather than matching text in the database.
    candidates = _eligible(ParentGuardian.objects.all())
    matches = [g for g in candidates
               if phone in (normalize_tz_phone(g.phone), normalize_tz_phone(g.secondary_phone))]
    if len(matches) > 1:
        return None, "ambiguous", None
    return (matches[0], "sms", phone) if matches else (None, "not_found", None)


def _mask(channel, address):
    if channel == "sms":
        return "•••• " + address[-3:]
    local, _, domain = address.partition("@")
    if len(local) <= 2:
        return local[:1] + "•••@" + domain
    return local[0] + "•" * (len(local) - 2) + local[-1] + "@" + domain


def _deliver(channel, address, code):
    school = getattr(_school_settings(), "school_name", "") or "Hodari Christian School"
    if channel == "sms":
        msg = f"Your Hodari code is {code}. Do not share this message. Expires in {CODE_TTL_SECONDS // 60} minutes."
        provider = (getattr(settings, "SMS_PROVIDER", "") or "console").lower()
        if provider == "console":
            logger.warning("SMS_PROVIDER=console; parent sign-in code not sent by SMS to %s", address)
            return settings.DEBUG
        return bool(NotificationService.send_sms(address, msg).get("success"))
    try:
        send_mail(
            subject="Your parent portal code",
            message=(f"Your {school} parent portal code is {code}.\n\n"
                     f"It expires in {CODE_TTL_SECONDS // 60} minutes. If you did not ask for it, ignore this email."),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[address],
        )
        return True
    except Exception:
        logger.exception("Parent sign-in code email to %s failed", address)
        return False


def _otp_key(guardian_id):
    return f"pg-signin:{guardian_id}"


class ParentLoginView(View):
    template_name = "parent_portal/login.html"

    # ---------- helpers ----------
    def _state(self, request):
        return request.session.get(SESSION_KEY) or {}

    def _render(self, request, step="in", **ctx):
        state = self._state(request)
        wait = 0
        if step == "code" and state.get("sent_at"):
            wait = max(0, RESEND_AFTER_SECONDS - int(timezone.now().timestamp() - state["sent_at"]))
        ctx.setdefault("who", state.get("who", ""))
        ctx.setdefault("error", "")
        ctx.setdefault("notice", "")
        return render(request, self.template_name, {
            "step": step,
            "channel": state.get("channel"),
            "masked": state.get("masked"),
            "resend_wait": wait,
            "code_length": range(CODE_LENGTH),
            "school_phone": _school_phone(),
            "debug_code": state.get("debug_code") if settings.DEBUG else None,
            "next": request.GET.get("next") or request.POST.get("next") or "",
            **ctx,
        })

    def _send_code(self, request, guardian, channel, address):
        if _hit(f"pg-signin-codes:{guardian.pk}", MAX_CODES_PER_HOUR, 3600):
            return "Too many codes have been sent. Wait an hour and try again, or call the school on " + _school_phone() + "."
        code = issue_code(_otp_key(guardian.pk), length=CODE_LENGTH, ttl_seconds=CODE_TTL_SECONDS)
        if not _deliver(channel, address, code):
            cancel_codes(_otp_key(guardian.pk))
            return ("We could not send the code just now. Try again in a minute, or call the school on "
                    + _school_phone() + ".")
        cache.delete(f"pg-signin-wrong:{guardian.pk}")
        state = self._state(request)
        state.update({
            "gid": guardian.pk, "channel": channel, "masked": _mask(channel, address),
            "address": address, "sent_at": int(timezone.now().timestamp()),
        })
        if settings.DEBUG:
            state["debug_code"] = code
        request.session[SESSION_KEY] = state
        return None

    # ---------- handlers ----------
    def get(self, request):
        if request.user.is_authenticated and request.user.role == UserRole.PARENT:
            return redirect("parent_portal:dashboard")
        if request.GET.get("change"):
            who = self._state(request).get("who", "")
            request.session.pop(SESSION_KEY, None)
            return self._render(request, "in", who=who)
        if not self._state(request).get("gid"):
            return self._render(request, "in")
        return self._render(request, "code")

    def post(self, request):
        action = request.POST.get("action", "lookup")
        if action == "lookup":
            return self._lookup(request)
        if action == "resend":
            return self._resend(request)
        if action == "verify":
            return self._verify(request)
        return redirect("parent_portal:login")

    def _lookup(self, request):
        who = (request.POST.get("who") or "").strip()
        if not who:
            return self._render(request, "in", who=who,
                                error="Enter the phone number or email the school has on record.")
        if _hit(f"pg-signin-ip:{_client_ip(request)}", MAX_LOOKUPS_PER_15_MIN, 900):
            return self._render(request, "in", who=who,
                                error="Too many attempts. Wait a few minutes and try again.")
        guardian, channel, address = find_guardian(who)
        if guardian is None:
            messages_by_code = {
                "bad_email": "That email does not look right. Check it and try again.",
                "bad_phone": "That phone number does not look right. Include all digits, e.g. 0712 345 678.",
                "ambiguous": ("More than one parent record uses that number or email. "
                              f"Call the school on {_school_phone()} to sort it out."),
            }
            error = messages_by_code.get(channel) or (
                "We can't find that phone number or email in our records. Check it and try again, "
                f"or call the school on {_school_phone()}.")
            return self._render(request, "in", who=who, error=error)
        request.session[SESSION_KEY] = {"who": who}
        error = self._send_code(request, guardian, channel, address)
        if error:
            return self._render(request, "in", who=who, error=error)
        return redirect(self._self_url(request))

    def _resend(self, request):
        state = self._state(request)
        guardian = ParentGuardian.objects.filter(pk=state.get("gid"), is_archived=False).first()
        if not guardian:
            return redirect("parent_portal:login")
        if int(timezone.now().timestamp()) - state.get("sent_at", 0) < RESEND_AFTER_SECONDS:
            return self._render(request, "code")
        error = self._send_code(request, guardian, state["channel"], state["address"])
        if error:
            return self._render(request, "code", error=error)
        return self._render(request, "code", notice="A new code is on its way.")

    def _verify(self, request):
        state = self._state(request)
        guardian = ParentGuardian.objects.filter(pk=state.get("gid"), is_archived=False).first()
        if not guardian:
            return redirect("parent_portal:login")
        code = "".join(ch for ch in (request.POST.get("code") or "") if ch.isdigit())
        if len(code) != CODE_LENGTH:
            return self._render(request, "code", error=f"Enter the {CODE_LENGTH} digits sent to you.")
        if not verify_otp(_otp_key(guardian.pk), code):
            if _hit(f"pg-signin-wrong:{guardian.pk}", MAX_WRONG_TRIES - 1, CODE_TTL_SECONDS):
                cancel_codes(_otp_key(guardian.pk))
                state.pop("debug_code", None)
                request.session[SESSION_KEY] = state
                return self._render(request, "code",
                                    error="Too many wrong tries, so that code has been cancelled. Tap Resend code for a new one.")
            return self._render(request, "code", error="That code is not right, or it has expired. Check it and try again.")

        user, created = ensure_parent_user(guardian)
        if user.role != UserRole.PARENT:
            logger.warning("Guardian %s is linked to non-parent user %s; sign-in refused", guardian.pk, user.pk)
            return self._render(request, "code", error=f"This record can't use the parent portal. Call the school on {_school_phone()}.")
        if user.must_change_password:
            user.must_change_password = False      # parents sign in by code; no password to change
            user.save(update_fields=["must_change_password"])
        if hasattr(user, "clear_failed_logins"):
            user.clear_failed_logins()

        request.session.pop(SESSION_KEY, None)
        login(request, user, backend="users.backends.EmailOrUsernameBackend")
        from users.middleware import SESSION_ROLE_KEY
        request.session[SESSION_ROLE_KEY] = user.role
        try:
            from audit.models import log_event
            log_event(actor=user, action_type="LOGIN", model_name="User", object_id=user.pk,
                      description=(f"Parent signed in with a one-time code by {state.get('channel')}"
                                   + (" (portal login created automatically)" if created else "")),
                      request=request)
        except Exception:
            logger.exception("Could not audit parent sign-in")

        next_url = request.POST.get("next") or ""
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return redirect(next_url)
        return redirect("parent_portal:dashboard")

    @staticmethod
    def _self_url(request):
        from django.urls import reverse
        url = reverse("parent_portal:login")
        nxt = request.POST.get("next") or request.GET.get("next")
        if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
            from urllib.parse import urlencode
            url += "?" + urlencode({"next": nxt})
        return url
