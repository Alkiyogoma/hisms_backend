from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class UserRole(models.TextChoices):
    SUPER_ADMIN = "super_admin", "Super Admin"
    HEAD_OF_SCHOOL = "head_of_school", "Head of School (HOS)"
    PRIMARY_HOD = "primary_hod", "Primary HOD"
    ECD_HOD = "ecd_hod", "ECD HOD"
    LOWER_SECONDARY_HOD = "lower_secondary_hod", "Lower Secondary HOD"
    ADMIN_OFFICER = "admin_officer", "Admin Officer"
    FINANCE_OFFICER = "finance_officer", "Finance Officer"
    TEACHER = "teacher", "Teacher"
    PARENT = "parent", "Parent / Guardian"


class User(AbstractUser):
    email = models.EmailField(unique=True)

    role = models.CharField(
        max_length=40,
        choices=UserRole.choices,
        default=UserRole.TEACHER,
        db_index=True,
    )
    profile_picture = models.FileField(upload_to="users/photos/", null=True, blank=True)

    # Additional roles (for custom roles beyond the 9 system roles)
    extra_roles = models.ManyToManyField(
        "users.RoleConfig",
        blank=True,
        related_name="users",
        help_text="Additional custom roles assigned to this user.",
    )

    # FRD NFR-SEC-005: login lockout after 5 failed attempts for 15 min
    failed_login_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    # FR-STAFF-003: force password change on first login
    must_change_password = models.BooleanField(default=False, help_text="If true, user must change password before accessing any screen.")

    # FR-STF-003: Staff departure tracking
    departure_date = models.DateField(null=True, blank=True, help_text="Date staff member left the school.")
    departure_reason = models.CharField(max_length=120, blank=True, help_text="Reason for departure (resignation, termination, transfer, etc.).")

    def __str__(self) -> str:
        return f"{self.username} ({self.role})"

    def get_effective_permissions(self) -> set[str]:
        """All "app_label.codename" permissions the user effectively holds.

        Combines:
          - Django role-group / user-permission permissions (native)
          - Extra-role group permissions
        Super Admin bypasses with a wildcard ("*").
        Result is cached on the instance for the request lifetime.
        """
        cache_attr = "_hodari_effective_perms"
        cached = getattr(self, cache_attr, None)
        if cached is not None:
            return cached

        perms: set[str] = set()
        if self.is_superuser or self.role == UserRole.SUPER_ADMIN:
            perms.add("*")
        else:
            try:
                perms |= set(self.get_group_permissions())
            except Exception:
                pass
            try:
                perms |= {
                    f"{p.content_type.app_label}.{p.codename}"
                    for p in self.user_permissions.select_related("content_type").all()
                }
            except Exception:
                pass

            # Extra roles groups (custom + system roles assigned via extra_roles)
            try:
                from django.contrib.auth.models import Group
                group_names = []
                for extra_pk, role_value in self.extra_roles.values_list("pk", "role"):
                    group_names.append(f"role_custom_{extra_pk}")
                    group_names.append(f"role_{role_value}")
                if group_names:
                    perms |= {
                        f"{app}.{cod}"
                        for app, cod in Group.objects.filter(name__in=group_names)
                        .values_list("permissions__content_type__app_label", "permissions__codename")
                        .distinct()
                    }
            except Exception:
                pass

        setattr(self, cache_attr, perms)
        return perms

    def has_perm(self, perm, obj=None):
        """
        Permission check driven by the user's effective permission set
        (see get_effective_permissions). Row-level (obj) checks fall back to
        Django's default backend.
        """
        if obj is not None:
            return super().has_perm(perm, obj)
        if self.is_superuser or self.role == UserRole.SUPER_ADMIN:
            return True

        effective = self.get_effective_permissions()
        if "*" in effective:
            return True
        if perm in effective:
            return True
        # Also honour bare-codename checks (callers passing "view_invoice").
        codename = perm.split(".")[-1] if "." in perm else perm
        return codename in effective

    def has_module_perms(self, app_label):
        """Override to include extra_roles in module-level permission checks."""
        if self.is_superuser or self.role == UserRole.SUPER_ADMIN:
            return True

        try:
            from django.contrib.auth.models import Group
            extra_roles_qs = self.extra_roles.values_list("pk", "role")
            group_names = []
            for pk, role_value in extra_roles_qs:
                group_names.append(f"role_custom_{pk}")
                group_names.append(f"role_{role_value}")
            if self.role and self.role.startswith("custom_"):
                group_names.append(f"role_{self.role}")
            if group_names:
                if Group.objects.filter(name__in=group_names, permissions__content_type__app_label=app_label).exists():
                    return True
        except Exception:
            pass

        # Clear Django's permission cache
        for attr in ('_perm_cache', '_user_perm_cache', '_group_perm_cache'):
            if hasattr(self, attr):
                delattr(self, attr)

        return super().has_module_perms(app_label)

    @property
    def is_locked_out(self) -> bool:
        if self.locked_until and self.locked_until > timezone.now():
            return True
        return False

    def record_failed_login(self, max_attempts: int = 5, lockout_seconds: int = 900):
        """Increment failed attempts; lock account if threshold reached."""
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= max_attempts:
            self.locked_until = timezone.now() + timezone.timedelta(seconds=lockout_seconds)
            self.failed_login_attempts = 0
        self.save(update_fields=["failed_login_attempts", "locked_until"])

    def clear_failed_logins(self):
        """Reset on successful login."""
        if self.failed_login_attempts or self.locked_until:
            self.failed_login_attempts = 0
            self.locked_until = None
            self.save(update_fields=["failed_login_attempts", "locked_until"])

    _admin_delete_allowed = False

    def delete(self, *args, **kwargs):
        """FRD OP 10.5: Block hard-deletion except via Django admin by superuser."""
        from django.core.exceptions import PermissionDenied
        cls = self.__class__
        if not cls._admin_delete_allowed:
            raise PermissionDenied(
                "Staff records cannot be permanently deleted from code. "
                "Use the staff departure/deactivation flow, or Django admin (superuser only)."
            )
        cls._admin_delete_allowed = False
        return super().delete(*args, **kwargs)
