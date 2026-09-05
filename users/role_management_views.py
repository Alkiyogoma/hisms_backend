"""
Role Management Views — FRD OP3.3: Permission group → Read / Update

Super Admin can view all roles, see user counts, configure default
departments, and manage which Django permissions are assigned to each role.

Roles are system-defined (TextChoices) and cannot be created or deleted,
but their permission sets and department mappings are configurable.
"""

import json
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import (
    TemplateView, FormView, CreateView, UpdateView, DeleteView,
)

from core.permissions import RoleRequiredMixin
from users.models import User, UserRole
from users.role_models import RoleConfig, DEFAULT_ROLE_CONFIGS

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────

def _get_role_user_counts():
    """Return a dict mapping role value → active user count."""
    counts = (
        User.objects.filter(is_active=True)
        .values("role")
        .annotate(count=Count("id"))
    )
    return {item["role"]: item["count"] for item in counts}


def _get_role_permissions_map():
    """Return a dict mapping role value → list of permission codenames."""
    result = {}
    for role_value in UserRole.values:
        group_name = f"role_{role_value}"
        try:
            group = Group.objects.get(name=group_name)
            perms = list(
                group.permissions.values_list("codename", flat=True)
            )
            result[role_value] = perms
        except Group.DoesNotExist:
            result[role_value] = []
    return result


def _get_all_permission_categories():
    """Return permissions grouped by content type (app_label.model)."""
    from django.apps import apps
    HIDDEN_APPS = {"django_celery_beat", "contenttypes", "auth"}
    permissions = Permission.objects.select_related("content_type").order_by(
        "content_type__app_label", "content_type__model", "codename"
    )
    categories = {}
    for perm in permissions:
        ct = perm.content_type
        if ct.app_label in HIDDEN_APPS:
            continue
        key = f"{ct.app_label}.{ct.model}"
        if key not in categories:
            model_label = ct.model
            try:
                model_class = apps.get_model(ct.app_label, ct.model)
                model_label = model_class._meta.verbose_name.title()
            except (LookupError, AttributeError):
                model_label = ct.model.replace("_", " ").title()
            categories[key] = {
                "app_label": ct.app_label,
                "model": model_label,
                "permissions": [],
            }
        categories[key]["permissions"].append({
            "codename": perm.codename,
            "name": perm.name,
        })
    return categories


def _get_role_config_or_none(role_value):
    """Get RoleConfig for a system role, or build from defaults."""
    try:
        return RoleConfig.objects.get(role=role_value)
    except RoleConfig.DoesNotExist:
        meta = DEFAULT_ROLE_CONFIGS.get(role_value, {})
        if not meta:
            return None
        return RoleConfig(
            role=role_value,
            label=meta["label"],
            description=meta["description"],
            departments=meta["departments"],
            icon_color=meta["icon_color"],
            is_system=True,
            is_active=True,
        )


# ──────────────────────────────────────────────────────────────
# Views
# ──────────────────────────────────────────────────────────────


class RoleListView(RoleRequiredMixin, TemplateView):
    """
    FRD OP3.3: Role definitions → Read
    Displays all system roles with user counts and department mappings.
    """
    template_name = "users/role_list.html"
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "auth.view_group"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user_counts = _get_role_user_counts()
        roles_configured = _get_role_permissions_map()

        roles = []
        for role_value, config in DEFAULT_ROLE_CONFIGS.items():
            rc = _get_role_config_or_none(role_value)
            roles.append({
                "value": role_value,
                "label": config["label"],
                "description": config["description"],
                "departments": rc.departments if rc else config["departments"],
                "icon_color": rc.icon_color if rc else config["icon_color"],
                "is_system": True,
                "is_active": rc.is_active if rc else True,
                "user_count": user_counts.get(role_value, 0),
                "permission_count": len(roles_configured.get(role_value, [])),
            })

        # Custom roles (non-system RoleConfig entries)
        custom_roles = RoleConfig.objects.filter(is_system=False)
        for rc in custom_roles:
            group_name = f"role_custom_{rc.pk}"
            perm_count = 0
            try:
                grp = Group.objects.get(name=group_name)
                perm_count = grp.permissions.count()
            except Group.DoesNotExist:
                pass
            user_count = User.objects.filter(extra_roles=rc).count()
            roles.append({
                "value": f"custom_{rc.pk}",
                "label": rc.label,
                "description": rc.description,
                "departments": rc.departments,
                "icon_color": rc.icon_color,
                "is_system": False,
                "is_active": rc.is_active,
                "user_count": user_count,
                "permission_count": perm_count,
                "custom_pk": rc.pk,
            })

        ctx["roles"] = roles
        ctx["total_users"] = User.objects.filter(is_active=True).count()
        ctx["total_roles"] = len(roles)
        ctx["system_count"] = len(DEFAULT_ROLE_CONFIGS)
        ctx["custom_count"] = custom_roles.count()
        return ctx


class RoleDetailView(RoleRequiredMixin, TemplateView):
    """
    FRD OP3.3: Permission group → Read / Update
    Shows role details, assigned users, and permission configuration.
    """
    template_name = "users/role_detail.html"
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "auth.view_group"

    def get(self, request, *args, **kwargs):
        role_value = kwargs.get("role")
        if role_value not in DEFAULT_ROLE_CONFIGS and not role_value.startswith("custom_"):
            messages.error(request, "Role not found.")
            return redirect("users:role_list")
        if role_value.startswith("custom_") and not role_value.split("_", 1)[1].isdigit():
            messages.error(request, "Role not found.")
            return redirect("users:role_list")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        role_value = kwargs.get("role")
        config = DEFAULT_ROLE_CONFIGS.get(role_value, {})
        rc = _get_role_config_or_none(role_value)

        # Users with this role
        if role_value.startswith("custom_"):
            custom_pk = int(role_value.split("_", 1)[1])
            rc = RoleConfig.objects.filter(pk=custom_pk).first()
            users_with_role = User.objects.filter(extra_roles=rc).select_related("staff_profile").order_by("-is_active", "username")
            # Populate role_config dict from RoleConfig object for template
            config = {
                "label": rc.label,
                "description": rc.description,
                "departments": rc.departments,
                "icon_color": rc.icon_color,
            }
        else:
            users_with_role = (
                User.objects.filter(role=role_value)
                .select_related("staff_profile")
                .order_by("-is_active", "username")
            )

        # Permission categories
        all_categories = _get_all_permission_categories()

        # Current permissions for this role
        if role_value.startswith("custom_"):
            group_name = f"role_custom_{custom_pk}"
            try:
                grp = Group.objects.get(name=group_name)
                role_perms = list(grp.permissions.values_list("codename", flat=True))
            except Group.DoesNotExist:
                role_perms = []
        else:
            role_perms = _get_role_permissions_map().get(role_value, [])

        # Add checked_count to each category for the template
        role_perms_set = set(role_perms)
        for cat in all_categories.values():
            cat["checked_count"] = sum(
                1 for p in cat["permissions"] if p["codename"] in role_perms_set
            )

        ctx["role_value"] = role_value
        ctx["role_config"] = config
        ctx["role_config_obj"] = rc
        ctx["users_with_role"] = users_with_role
        ctx["user_count"] = users_with_role.filter(is_active=True).count()
        ctx["all_categories"] = all_categories
        ctx["role_permissions"] = role_perms
        ctx["role_permissions_json"] = json.dumps(role_perms)

        return ctx


class RolePermissionUpdateView(RoleRequiredMixin, View):
    """
    FRD OP3.3: Permission group → Update
    AJAX endpoint to update permissions for a role.
    Only Super Admin can modify role permissions.
    """
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "auth.change_group"

    def post(self, request, role):
        if role not in DEFAULT_ROLE_CONFIGS and not role.startswith("custom_"):
            return JsonResponse({"error": "Invalid role"}, status=400)

        if role.startswith("custom_") and not role.split("_", 1)[1].isdigit():
            return JsonResponse({"error": "Invalid custom role"}, status=400)

        try:
            data = json.loads(request.body)
            permission_codenames = data.get("permissions", [])
        except (json.JSONDecodeError, TypeError):
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        # Validate all permission codenames exist
        valid_perms = set(
            Permission.objects.values_list("codename", flat=True)
        )
        invalid = set(permission_codenames) - valid_perms
        if invalid:
            return JsonResponse(
                {"error": f"Invalid permissions: {', '.join(sorted(invalid))}"},
                status=400,
            )

        # Get or create the group for this role
        if role.startswith("custom_"):
            custom_pk = int(role.split("_", 1)[1])
            group_name = f"role_custom_{custom_pk}"
        else:
            group_name = f"role_{role}"
        group, created = Group.objects.get_or_create(name=group_name)

        # Get old permissions for audit
        old_perms = set(group.permissions.values_list("codename", flat=True))
        new_perms = set(permission_codenames)

        # Update permissions
        group.permissions.clear()
        if permission_codenames:
            perms = Permission.objects.filter(codename__in=permission_codenames)
            group.permissions.add(*perms)

        # Sync RoleConfig M2M if it exists
        if role.startswith("custom_"):
            rc = RoleConfig.objects.filter(pk=custom_pk).first()
        else:
            rc = RoleConfig.objects.filter(role=role).first()
        if rc:
            rc.permissions.set(group.permissions.all())

        # Audit log
        added = new_perms - old_perms
        removed = old_perms - new_perms

        if added or removed:
            try:
                from audit.models import log_event
                desc_parts = []
                if added:
                    desc_parts.append(f"Added: {', '.join(sorted(added))}")
                if removed:
                    desc_parts.append(f"Removed: {', '.join(sorted(removed))}")

                log_event(
                    actor=request.user,
                    action_type="ROLE_PERMISSIONS_UPDATED",
                    model_name="Group",
                    object_id=group.pk,
                    description=(
                        f"Permissions updated for role '{DEFAULT_ROLE_CONFIGS[role]['label']}'. "
                        + "; ".join(desc_parts)
                    ),
                    before_value=json.dumps(sorted(old_perms)),
                    after_value=json.dumps(sorted(new_perms)),
                    request=request,
                )
            except Exception:
                logger.warning("Audit log failed for permission update", exc_info=True)

        return JsonResponse({
            "success": True,
            "message": f"Permissions updated for {DEFAULT_ROLE_CONFIGS[role]['label']}",
            "permission_count": len(new_perms),
        })


class RoleUserCountAPIView(RoleRequiredMixin, View):
    """AJAX endpoint: get user count for a specific role."""
    allowed_roles = [UserRole.SUPER_ADMIN]

    def get(self, request, role):
        if role not in DEFAULT_ROLE_CONFIGS:
            return JsonResponse({"error": "Invalid role"}, status=400)

        count = User.objects.filter(role=role, is_active=True).count()
        return JsonResponse({"role": role, "user_count": count})


class RoleCreateView(RoleRequiredMixin, CreateView):
    """Create a new custom role (non-system roles only)."""
    model = RoleConfig
    template_name = "users/role_form.html"
    fields = ["label", "description", "departments", "icon_color"]
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "auth.add_group"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["color_choices"] = [
            "#7C3AED","#2563EB","#059669","#D97706","#DC2626",
            "#0891B2","#BE185D","#4F46E5","#6B7280","#F59E0B",
            "#EC4899","#14B8A6",
        ]
        ctx["dept_choices"] = [
            ("ECD", "Early Childhood"),
            ("PRIMARY", "Primary"),
            ("LOWER_SECONDARY", "Lower Secondary"),
            ("ADMINISTRATION", "Administration"),
        ]
        return ctx

    def form_valid(self, form):
        import re
        form.instance.is_system = False
        form.instance.is_active = True
        # Auto-generate unique role slug from label
        slug = re.sub(r'[^a-z0-9]+', '_', form.instance.label.lower()).strip('_')
        base = f"custom_{slug}"
        role_slug = base
        counter = 1
        while RoleConfig.objects.filter(role=role_slug).exists():
            role_slug = f"{base}_{counter}"
            counter += 1
        form.instance.role = role_slug
        response = super().form_valid(form)

        # Create auth Group and sync permissions
        group_name = f"role_custom_{self.object.pk}"
        group, _ = Group.objects.get_or_create(name=group_name)

        messages.success(self.request, f"Role '{self.object.label}' created successfully.")
        return response

    def get_success_url(self):
        return reverse_lazy("users:role_list")


class RoleUpdateView(RoleRequiredMixin, UpdateView):
    """Edit a role's metadata (label, description, departments, color).
    System roles are immutable — same contract enforced by Delete/Toggle.
    """
    model = RoleConfig
    template_name = "users/role_form.html"
    fields = ["label", "description", "departments", "icon_color"]
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "auth.change_group"
    pk_url_kwarg = "role_pk"

    def dispatch(self, request, *args, **kwargs):
        rc = RoleConfig.objects.filter(pk=kwargs.get("role_pk")).first()
        if rc and rc.is_system:
            messages.error(request, "System roles cannot be edited.")
            return redirect("users:role_list")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["color_choices"] = [
            "#7C3AED","#2563EB","#059669","#D97706","#DC2626",
            "#0891B2","#BE185D","#4F46E5","#6B7280","#F59E0B",
            "#EC4899","#14B8A6",
        ]
        ctx["dept_choices"] = [
            ("ECD", "Early Childhood"),
            ("PRIMARY", "Primary"),
            ("LOWER_SECONDARY", "Lower Secondary"),
            ("ADMINISTRATION", "Administration"),
        ]
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Role '{self.object.label}' updated successfully.")
        return response

    def get_success_url(self):
        return reverse_lazy("users:role_list")


class RoleDeleteView(RoleRequiredMixin, DeleteView):
    """Delete a custom role (non-system roles only)."""
    model = RoleConfig
    template_name = "users/role_confirm_delete.html"
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "auth.delete_group"
    pk_url_kwarg = "role_pk"

    def form_valid(self, form):
        if self.object.is_system:
            messages.error(self.request, "System roles cannot be deleted.")
            return redirect("users:role_list")

        # Check for users assigned to this role
        user_count = User.objects.filter(extra_roles=self.object).count()
        if user_count > 0:
            messages.error(
                self.request,
                f"Cannot delete '{self.object.label}' — {user_count} user(s) are assigned to this role. "
                "Remove all users first."
            )
            return redirect("users:role_list")

        # Delete the auth Group
        group_name = f"role_custom_{self.object.pk}"
        Group.objects.filter(name=group_name).delete()

        messages.success(self.request, f"Role '{self.object.label}' deleted successfully.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("users:role_list")


class RoleToggleActiveView(RoleRequiredMixin, View):
    """AJAX endpoint to toggle a role's active status."""
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "auth.change_group"

    def post(self, request, role_pk):
        try:
            rc = RoleConfig.objects.get(pk=role_pk)
        except RoleConfig.DoesNotExist:
            return JsonResponse({"error": "Role not found"}, status=404)

        if rc.is_system:
            return JsonResponse({"error": "System roles cannot be deactivated"}, status=400)

        rc.is_active = not rc.is_active
        rc.save(update_fields=["is_active"])

        return JsonResponse({
            "success": True,
            "is_active": rc.is_active,
            "message": f"Role '{rc.label}' is now {'active' if rc.is_active else 'inactive'}.",
        })


class RoleUserAssignView(RoleRequiredMixin, View):
    """AJAX endpoint to assign a user to a custom role (extra_roles)."""
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "auth.change_group"

    def post(self, request, role_pk):
        try:
            rc = RoleConfig.objects.get(pk=role_pk)
        except RoleConfig.DoesNotExist:
            return JsonResponse({"error": "Role not found"}, status=404)

        if not rc.is_active:
            return JsonResponse({"error": "This role is inactive and cannot be assigned."}, status=400)

        user_id = request.POST.get("user_id")
        if not user_id:
            return JsonResponse({"error": "user_id required"}, status=400)

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return JsonResponse({"error": "User not found"}, status=404)

        user.extra_roles.add(rc)
        return JsonResponse({
            "success": True,
            "message": f"User '{user.username}' assigned to '{rc.label}'.",
        })


class RoleUserRemoveView(RoleRequiredMixin, View):
    """AJAX endpoint to remove a user from a custom role (extra_roles)."""
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "auth.change_group"

    def post(self, request, role_pk):
        try:
            rc = RoleConfig.objects.get(pk=role_pk)
        except RoleConfig.DoesNotExist:
            return JsonResponse({"error": "Role not found"}, status=404)

        user_id = request.POST.get("user_id")
        if not user_id:
            return JsonResponse({"error": "user_id required"}, status=400)

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return JsonResponse({"error": "User not found"}, status=404)

        user.extra_roles.remove(rc)
        return JsonResponse({
            "success": True,
            "message": f"User '{user.username}' removed from '{rc.label}'.",
        })


# ──────────────────────────────────────────────────────────────
# Per-User Permission Overrides & Extra Roles (Option A + C)
# ──────────────────────────────────────────────────────────────

class UserPermissionOverrideView(RoleRequiredMixin, View):
    """
    AJAX endpoint to load/save per-user permission overrides.
    GET:  returns current user_permissions + extra_roles info
    POST: saves user_permissions (replaces entire set)
    Only Super Admin can modify.
    """
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "users.change_user"

    def get(self, request, user_id):
        try:
            target = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return JsonResponse({"error": "User not found"}, status=404)

        # Current direct user_permissions
        direct_perms = list(
            target.user_permissions.values_list("codename", flat=True)
        )

        # Permissions inherited from primary role group
        primary_role_perms = set()
        group_name = f"role_{target.role}"
        try:
            grp = Group.objects.get(name=group_name)
            primary_role_perms = set(grp.permissions.values_list("codename", flat=True))
        except Group.DoesNotExist:
            pass

        # Permissions inherited from extra roles
        extra_role_perms = set()
        extra_roles_info = []
        for rc in target.extra_roles.all():
            # System roles use role_<role_value>, custom roles use role_custom_<pk>
            eg_name = f"role_{rc.role}" if rc.is_system else f"role_custom_{rc.pk}"
            try:
                eg = Group.objects.get(name=eg_name)
                extra_role_perms |= set(eg.permissions.values_list("codename", flat=True))
            except Group.DoesNotExist:
                pass
            extra_roles_info.append({
                "id": rc.pk,
                "label": rc.label,
                "color": rc.icon_color,
                "is_system": rc.is_system,
            })

        # All assignable roles (system + custom, active)
        all_custom_roles = list(
            RoleConfig.objects.filter(is_active=True).values(
                "id", "label", "icon_color", "is_system"
            )
        )

        # Categorize permissions
        inherited_perms = primary_role_perms | extra_role_perms
        pure_overrides = set(direct_perms) - inherited_perms

        return JsonResponse({
            "user_id": target.pk,
            "username": target.username,
            "role": target.role,
            "direct_perms": sorted(direct_perms),
            "inherited_perms": sorted(inherited_perms),
            "pure_overrides": sorted(pure_overrides),
            "extra_roles": extra_roles_info,
            "all_custom_roles": all_custom_roles,
        })

    def post(self, request, user_id):
        try:
            target = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return JsonResponse({"error": "User not found"}, status=404)

        data = json.loads(request.body)
        perm_codenames = data.get("permissions", [])

        # Validate all permission codenames exist
        valid_perms = set(
            Permission.objects.values_list("codename", flat=True)
        )
        invalid = set(perm_codenames) - valid_perms
        if invalid:
            return JsonResponse(
                {"error": f"Invalid permissions: {', '.join(sorted(invalid))}"},
                status=400,
            )

        # Get inherited permissions (from role + extra_roles) to avoid redundancy
        inherited_perms = set()
        group_name = f"role_{target.role}"
        try:
            grp = Group.objects.get(name=group_name)
            inherited_perms |= set(grp.permissions.values_list("codename", flat=True))
        except Group.DoesNotExist:
            pass
        for rc in target.extra_roles.all():
            eg_name = f"role_custom_{rc.pk}"
            try:
                eg = Group.objects.get(name=eg_name)
                inherited_perms |= set(eg.permissions.values_list("codename", flat=True))
            except Group.DoesNotExist:
                pass

        # Only store permissions that are NOT already inherited (pure overrides)
        overrides_only = set(perm_codenames) - inherited_perms

        # Audit: what changed?
        old_perms = set(target.user_permissions.values_list("codename", flat=True))
        added = overrides_only - old_perms
        removed = old_perms - overrides_only

        # Save
        target.user_permissions.clear()
        if overrides_only:
            perms = Permission.objects.filter(codename__in=overrides_only)
            target.user_permissions.add(*perms)

        # Audit log
        if added or removed:
            try:
                from audit.models import log_event
                desc_parts = []
                if added:
                    desc_parts.append(f"Added: {', '.join(sorted(added))}")
                if removed:
                    desc_parts.append(f"Removed: {', '.join(sorted(removed))}")
                log_event(
                    actor=request.user,
                    action_type="USER_PERMISSIONS_UPDATED",
                    model_name="User",
                    object_id=target.pk,
                    description=(
                        f"Per-user permission overrides for {target.username}: "
                        + "; ".join(desc_parts)
                    ),
                    request=request,
                )
            except Exception:
                pass

        return JsonResponse({
            "success": True,
            "message": f"Updated permission overrides for {target.username}.",
            "direct_perms": sorted(overrides_only),
        })


class UserExtraRolesView(RoleRequiredMixin, View):
    """
    AJAX endpoint to load/save extra roles for a user.
    GET:  returns current extra_roles
    POST: replaces extra_roles set
    """
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permissions_any = ["auth.change_group", "auth.view_group"]

    def post(self, request, user_id):
        try:
            target = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return JsonResponse({"error": "User not found"}, status=404)

        data = json.loads(request.body)
        role_ids = data.get("role_ids", [])

        # Validate all role IDs exist and are active
        valid_roles = RoleConfig.objects.filter(is_active=True)
        invalid_ids = set(role_ids) - set(valid_roles.values_list("pk", flat=True))
        if invalid_ids:
            return JsonResponse(
                {"error": f"Invalid role IDs: {', '.join(str(i) for i in sorted(invalid_ids))}"},
                status=400,
            )

        old_roles = set(target.extra_roles.values_list("pk", flat=True))
        new_roles = set(role_ids)

        target.extra_roles.set(valid_roles.filter(pk__in=role_ids))

        # Sync group memberships for extra roles
        for rc in valid_roles:
            # System roles use role_<role_value>, custom roles use role_custom_<pk>
            grp_name = f"role_{rc.role}" if rc.is_system else f"role_custom_{rc.pk}"
            grp, _ = Group.objects.get_or_create(name=grp_name)
            if rc.pk in new_roles:
                grp.user_set.add(target)
            else:
                grp.user_set.remove(target)

        # Audit log
        added = new_roles - old_roles
        removed = old_roles - new_roles
        if added or removed:
            try:
                from audit.models import log_event
                added_labels = list(valid_roles.filter(pk__in=added).values_list("label", flat=True))
                removed_labels = list(valid_roles.filter(pk__in=removed).values_list("label", flat=True))
                desc_parts = []
                if added_labels:
                    desc_parts.append(f"Added roles: {', '.join(added_labels)}")
                if removed_labels:
                    desc_parts.append(f"Removed roles: {', '.join(removed_labels)}")
                log_event(
                    actor=request.user,
                    action_type="USER_EXTRA_ROLES_UPDATED",
                    model_name="User",
                    object_id=target.pk,
                    description=(
                        f"Extra roles updated for {target.username}: "
                        + "; ".join(desc_parts)
                    ),
                    request=request,
                )
            except Exception:
                pass

        return JsonResponse({
            "success": True,
            "message": f"Updated extra roles for {target.username}.",
            "extra_roles": list(target.extra_roles.values_list("pk", flat=True)),
        })
