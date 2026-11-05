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


def ensure_parent_user(guardian):
    """Return (user, created): the PARENT login linked to this guardian,
    creating it when missing and re-enabling it when it was switched off.
    A created login has no usable password: parents sign in with a one-time
    code sent to their phone or email (parent_portal.auth_views)."""
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
            is_active=True,
        )
        user.set_unusable_password()
        user.save()
        Group.objects.get_or_create(name="role_parent")[0].user_set.add(user)
        guardian.user = user
        guardian.save(update_fields=["user", "updated_at"])
    elif not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])
    return user, created
