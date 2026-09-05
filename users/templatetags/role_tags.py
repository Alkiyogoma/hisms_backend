"""
Template tags for role-based permission checks.

Usage in templates:
    {% load role_tags %}

    {% has_perm user "academics.add_lessonplan" as can_add_lp %}
    {% if can_add_lp %}...{% endif %}

    {% has_role user "super_admin" as is_sa %}
    {% if is_sa %}...{% endif %}

    {% has_any_role user "super_admin,head_of_school" as is_admin %}
    {% if is_admin %}...{% endif %}

    {{ user|has_perm_filter:"academics.add_lessonplan" }}
    {{ user|has_role_filter:"super_admin" }}
    {{ role_value|get_role_color }}
    {{ role_value|get_role_name }}
"""

from django import template
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

register = template.Library()


def _build_user_perms(user):
    """Build the full permission set for a user (expensive — DB queries).
    Result is cached on user._hodari_perms so subsequent calls are free."""
    cache_attr = "_hodari_perms"
    cached = getattr(user, cache_attr, None)
    if cached is not None:
        return cached

    perms = set()
    perms.add("*")  # wildcard for super admin check below

    if user.is_superuser or getattr(user, "role", None) == "super_admin":
        setattr(user, cache_attr, perms)
        return perms

    from django.contrib.auth.models import Group

    # Primary role group
    group_name = f"role_{user.role}"
    try:
        for codename in Group.objects.filter(
            name=group_name
        ).values_list("permissions__codename", flat=True):
            if codename:
                perms.add(codename)
    except Exception:
        pass

    # Extra roles groups
    try:
        extra_roles_qs = user.extra_roles.values_list("pk", "role")
        group_names = []
        for pk, role_value in extra_roles_qs:
            group_names.append(f"role_custom_{pk}")
            group_names.append(f"role_{role_value}")
        if group_names:
            for codename in Group.objects.filter(
                name__in=group_names
            ).values_list("permissions__codename", flat=True).distinct():
                if codename:
                    perms.add(codename)
    except Exception:
        pass

    # Direct user_permissions
    try:
        for codename in user.user_permissions.values_list("codename", flat=True):
            if codename:
                perms.add(codename)
    except Exception:
        pass

    # Custom RoleConfig
    user_role = getattr(user, "role", "")
    if user_role and user_role.startswith("custom_"):
        try:
            pk = int(user_role.split("_", 1)[1])
            from users.role_models import RoleConfig
            rc = RoleConfig.objects.filter(pk=pk, is_active=True).first()
            if rc:
                for codename in rc.permissions.values_list("codename", flat=True):
                    if codename:
                        perms.add(codename)
        except (ValueError, IndexError):
            pass

    setattr(user, cache_attr, perms)
    return perms


@register.simple_tag(takes_context=True)
def has_perm(context, user, perm_codename):
    """
    Check if user has a specific Django permission.
    Returns True/False.

    Uses a precomputed permission set (stored on request._user_perms by the view)
    for O(1) lookup. Falls back to per-call DB queries if not cached.
    """
    if not user or not user.is_authenticated:
        return False

    # Super Admin bypasses ALL permission checks
    if user.is_superuser or getattr(user, "role", None) == "super_admin":
        return True

    # Extract codename from "app_label.codename" format
    if "." in perm_codename:
        codename = perm_codename.split(".")[-1]
    else:
        codename = perm_codename

    # FAST PATH: check precomputed permission set on request
    request = context.get("request")
    if request is not None:
        user_perms = getattr(request, "_user_perms", None)
        if user_perms is not None:
            return codename in user_perms or "*" in user_perms

    # FAST PATH: check permission set cached on user instance
    user_perms = getattr(user, "_hodari_perms", None)
    if user_perms is not None:
        return codename in user_perms or "*" in user_perms

    # SLOW PATH: build permission set now (first call in this request)
    user_perms = _build_user_perms(user)
    return codename in user_perms or "*" in user_perms


@register.simple_tag()
def has_role(user, role_value):
    """
    Check if user has a specific role.
    {% has_role user "super_admin" as is_sa %}
    """
    if not user or not user.is_authenticated:
        return False
    return user.role == role_value


@register.simple_tag()
def has_any_role(user, role_csv):
    """
    Check if user has any of the comma-separated roles.
    {% has_any_role user "super_admin,head_of_school" as is_admin %}
    """
    if not user or not user.is_authenticated:
        return False
    roles = [r.strip() for r in role_csv.split(",")]
    return user.role in roles


_HOD_LIKE_ROLES = {
    "super_admin", "head_of_school", "primary_hod", "ecd_hod", "lower_secondary_hod",
}

_HOS_SIGNOFF_ROLES = {"super_admin", "head_of_school"}


@register.simple_tag()
def is_hod_like(user):
    """
    Check if user has a HOD-like role (can review lesson plans, view compliance, etc.).
    {% is_hod_like user as user_is_hod %}
    """
    if not user or not user.is_authenticated:
        return False
    return getattr(user, "role", "") in _HOD_LIKE_ROLES


@register.simple_tag()
def is_hos_signoff(user):
    """
    Check if user can sign off/publish reports (HOS or Super Admin only).
    {% is_hos_signoff user as can_publish %}
    """
    if not user or not user.is_authenticated:
        return False
    return getattr(user, "role", "") in _HOS_SIGNOFF_ROLES


@register.filter(name="has_perm_filter")
def has_perm_filter(user, perm_codename):
    """
    Template filter version: {{ user|has_perm_filter:"academics.add_lessonplan" }}
    Same resolution order as has_perm tag.
    """
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser or getattr(user, "role", None) == "super_admin":
        return True

    # Extract codename from "app_label.codename" format
    if "." in perm_codename:
        codename = perm_codename.split(".")[-1]
    else:
        codename = perm_codename

    # FAST PATH: check permission set cached on user instance
    user_perms = getattr(user, "_hodari_perms", None)
    if user_perms is not None:
        return codename in user_perms or "*" in user_perms

    # SLOW PATH: build permission set now
    user_perms = _build_user_perms(user)
    return codename in user_perms or "*" in user_perms


@register.filter(name="has_role_filter")
def has_role_filter(user, role_value):
    """
    Template filter: {{ user|has_role_filter:"super_admin" }}
    """
    if not user or not user.is_authenticated:
        return False
    return user.role == role_value


@register.filter(name="get_role_color")
def get_role_color(role_value):
    """
    Return hex colour for a role value.
    {{ role_value|get_role_color }}
    """
    from users.role_models import DEFAULT_ROLE_CONFIGS
    config = DEFAULT_ROLE_CONFIGS.get(role_value, {})
    return config.get("icon_color", "#6B7280")


@register.filter(name="get_role_name")
def get_role_name(role_value):
    """
    Return human-readable name for a role value.
    {{ role_value|get_role_name }}
    """
    from users.role_models import DEFAULT_ROLE_CONFIGS
    config = DEFAULT_ROLE_CONFIGS.get(role_value, {})
    return config.get("label", role_value.replace("_", " ").title())


@register.simple_tag()
def can_manage_role(current_user, target_role):
    """
    Check if current_user can manage (edit permissions of) target_role.
    Only Super Admin can manage roles.
    {% can_manage_role user role_value as can_manage %}
    """
    if not current_user or not current_user.is_authenticated:
        return False
    return current_user.role == "super_admin"


@register.filter(name="humanize_model")
def humanize_model(value):
    """
    Convert snake_case or camelCase model names to readable labels.
    'abc_general_assignment' → 'Abc General Assignment'
    'AbcGeneralAssignment' → 'Abc General Assignment'
    'academics' → 'Academics'
    """
    import re
    if not value:
        return value
    s = value.replace("_", " ")
    s = re.sub(r'([a-z])([A-Z])', r'\1 \2', s)
    return " ".join(w.capitalize() for w in s.split())


@register.simple_tag()
def has_dept(user, dept_key):
    """
    Check if user's staff_profile.departments includes the given department key.
    HOS/Super Admin always return True.
    {% has_dept user "ECD" as can_see_ecd %}
    """
    if not user or not user.is_authenticated:
        return False
    if getattr(user, "role", "") in _HOD_LIKE_ROLES:
        return True
    profile = getattr(user, "staff_profile", None)
    if not profile:
        return False
    depts = getattr(profile, "departments", []) or []
    return dept_key in depts


@register.simple_tag()
def has_any_dept(user, dept_csv):
    """
    Check if user has ANY of the comma-separated department keys.
    {% has_any_dept user "ECD,PRIMARY" as can_see_tab %}
    """
    if not user or not user.is_authenticated:
        return False
    if getattr(user, "role", "") in _HOD_LIKE_ROLES:
        return True
    profile = getattr(user, "staff_profile", None)
    if not profile:
        return False
    depts = getattr(profile, "departments", []) or []
    targets = [d.strip() for d in dept_csv.split(",")]
    return bool(set(targets) & set(depts))
