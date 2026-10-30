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


def send_portal_credentials(guardian, username, actor, reset=False):
    """Tell a parent their portal is ready: by email (when they have one) and by
    SMS (when they have a phone and an SMS provider is set up). Parents sign in
    with their phone number and an SMS code, so no password is sent. Returns
    the list of places the message went; see describe_delivery()."""
    from django.conf import settings
    from django.template.loader import render_to_string

    from attendance.notification_service import NotificationService, sms_status
    from communications.email_service import send_parent_notification
    from core.email_templates import send_dynamic_email
    from core.models import SchoolSettings

    school_name = SchoolSettings.get_settings().school_name or "Hodari School"
    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    login_url = site_url + "/parent/"
    how_to = (f"Open {login_url}, choose Parent Login, enter your phone number "
              f"({guardian.phone}) and type in the code we send you by SMS. No password is needed.")
    delivered = []

    if guardian.email:
        intro = (
            "Here is how to sign in to the parent portal."
            if reset else
            "Your parent portal account has been created. You can now access your child's "
            "attendance records, report cards, invoices, and school communications."
        )
        tpl_context = {
            "guardian_name": guardian.full_name,
            "email": guardian.email,
            "username": username,
            "phone": guardian.phone,
            # Kept for admin-customised templates that still mention a password.
            "temp_password": "Not needed: sign in with your phone number",
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
                title="How to Sign In to the Parent Portal" if reset else "Your Parent Portal Account Has Been Created",
                message=(
                    f"Dear {guardian.full_name},\n\n{intro}\n\n{how_to}\n\n"
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
    """Give a parent portal access, or resend how to sign in. Creates a PARENT
    login when the parent has none. Parents sign in with their phone number and
    an SMS code, so the account has no usable password. Returns
    (created, username, delivered) where delivered lists where the details went."""
    from django.contrib.auth.models import Group

    from users.models import User, UserRole

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
    user.is_active = True
    if user.role == UserRole.PARENT:
        # Parents sign in with their phone and an SMS code, never a password.
        user.set_unusable_password()
        user.must_change_password = False
    user.save()
    if created:
        Group.objects.get_or_create(name="role_parent")[0].user_set.add(user)
        guardian.user = user
        guardian.save(update_fields=["user", "updated_at"])

    delivered = send_portal_credentials(guardian, user.username, actor, reset=not created)
    return created, user.username, delivered
