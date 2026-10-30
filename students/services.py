import re
from datetime import datetime

from django.db.models import Max

from students.models import Student


def generate_admission_number(year: int | None = None) -> str:
    """FRD format: ADM-[YEAR]-[3-digit sequence], e.g. ADM-2026-001."""
    year = year or datetime.now().year
    pattern = rf"^ADM-{year}-(\d{{3}})$"

    max_seq = 0
    for admission_no in Student.objects.filter(admission_no__startswith=f"ADM-{year}-").values_list(
        "admission_no", flat=True
    ):
        m = re.match(pattern, admission_no)
        if m:
            max_seq = max(max_seq, int(m.group(1)))

    return f"ADM-{year}-{max_seq + 1:03d}"


def _unique_parent_email(guardian):
    """users_user.email is UNIQUE: parents without an email, or sharing one,
    get a distinct address instead of colliding (DEF-10)."""
    from users.models import User

    email = (guardian.email or "").strip().lower()
    if not email:
        return f"parent{guardian.pk}@hodari.local"
    base, _at, domain = email.partition("@")
    candidate, suffix = email, 2
    while User.objects.filter(email__iexact=candidate).exists():
        candidate = f"{base}{suffix}@{domain}"
        suffix += 1
    return candidate


def send_portal_credentials(guardian, username, raw_password, actor, reset=False):
    """Send parent portal login details by email (when the parent has one) and
    by SMS (when they have a phone and an SMS provider is set up). Returns the
    list of places the details went; see describe_delivery()."""
    from django.conf import settings
    from django.template.loader import render_to_string

    from attendance.notification_service import NotificationService, sms_status
    from communications.email_service import send_parent_notification
    from core.email_templates import send_dynamic_email
    from core.models import SchoolSettings

    school_name = SchoolSettings.get_settings().school_name or "Hodari School"
    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    login_url = site_url + "/parent/"
    delivered = []

    if guardian.email:
        intro = (
            "Your parent portal password has been reset."
            if reset else
            "Your parent portal account has been created. You can now access your child's "
            "attendance records, report cards, invoices, and school communications."
        )
        tpl_context = {
            "guardian_name": guardian.full_name,
            "email": guardian.email,
            "username": username,
            "temp_password": raw_password,
            "login_url": login_url,
            "portal_link": login_url,
            "school_name": school_name,
        }
        result = send_dynamic_email(template_type="parent_portal", to_email=guardian.email,
                                    context=tpl_context, actor=actor)
        if not result:
            # No admin-editable template: send the built-in email instead.
            html_body = render_to_string("registration/parent_portal_email.html", {
                **tpl_context, "site_url": site_url,
                "static_url": getattr(settings, "STATIC_URL", "/static/"),
            })
            send_parent_notification(
                guardian=guardian,
                title="Your Parent Portal Login Details" if reset else "Your Parent Portal Account Has Been Created",
                message=(
                    f"Dear {guardian.full_name},\n\n{intro}\n\n"
                    f"Username: {username}\n"
                    f"Password: {raw_password}\n\n"
                    f"IMPORTANT: You will be required to change your password on first login.\n\n"
                    f"Parent Portal: {login_url}\n\n"
                    f"Thank you for choosing {school_name}."
                ),
                html_body=html_body, actor=actor, action_type="PARENT_PORTAL_ACCOUNT",
            )
            delivered.append(f"emailed to {guardian.email}")
        elif result.sent:
            delivered.append(f"emailed to {guardian.email}")

    if guardian.phone and sms_status()["configured"]:
        sms = (
            f"{school_name} parent portal is ready: {login_url} "
            f"Choose Parent Login, enter this phone number and we'll text you a sign-in code."
        )
        if NotificationService.send_sms(guardian.phone, sms).get("success"):
            delivered.append(f"sent by SMS to {guardian.phone}")

    return delivered


def describe_delivery(delivered):
    """Staff-facing wording for where login details went."""
    if not delivered:
        return "were NOT sent (no email went out and SMS is not set up); use Resend Login Details"
    return " and ".join(delivered)


def grant_portal_access(guardian, actor):
    """Give a parent portal access, or reissue it. Creates a PARENT login when
    the parent has none; otherwise sets a new temporary password. Either way
    the parent must change the password on first login. Returns
    (created, username, delivered) where delivered lists where the details went."""
    from django.contrib.auth.models import Group
    from django.utils.crypto import get_random_string

    from users.models import User, UserRole

    raw_password = get_random_string(length=10)
    user = guardian.user
    created = user is None
    if created:
        phone_digits = "".join(filter(str.isdigit, guardian.phone or ""))
        base_username = f"parent_{phone_digits[:12]}" if phone_digits else f"parent_{guardian.pk}"
        username, counter = base_username, 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}_{counter}"
            counter += 1
        name_parts = (guardian.full_name or "").strip().split(maxsplit=1)
        user = User(
            username=username,
            email=_unique_parent_email(guardian),
            role=UserRole.PARENT,
            first_name=name_parts[0] if name_parts else "",
            last_name=name_parts[1] if len(name_parts) > 1 else "",
        )
    user.must_change_password = True
    user.is_active = True
    user.set_password(raw_password)
    user.save()
    if created:
        Group.objects.get_or_create(name="role_parent")[0].user_set.add(user)
        guardian.user = user
        guardian.save(update_fields=["user", "updated_at"])

    delivered = send_portal_credentials(guardian, user.username, raw_password, actor, reset=not created)
    return created, user.username, delivered
