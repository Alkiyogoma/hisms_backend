"""
Permission-completeness regression test.

Enforces the dynamic-RBAC invariant: views gate access by Django permissions
(assigned through the role-assignment UI / ROLE_DEFAULT_PERMISSIONS), never by
hard-coded role lists.

1. Every ``required_permission`` / ``required_permissions_any`` referenced by a
   routed view must correspond to a real Django permission (a default CRUD
   permission or a custom permission declared on a model Meta). A dangling
   permission string means the page is an unreachable 403 for every
   non-super-admin user.

2. No new role-only gated views may be introduced. A fixed, documented list of
   legacy ``allowed_roles``-only views is permitted until they are converted;
   anything beyond that list fails the test.

Requires Django settings to be importable (DJANGO_SETTINGS_MODULE is set when
the suite runs). No database is needed.
"""
import os
from pathlib import Path

import pytest

_THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _THIS_DIR.parent.parent  # hisms_backend/

_SKIP_DIRS = {"__pycache__", ".git", "migrations", "node_modules", ".tox", "venv", "env"}


def _setup_django():
    os.environ.setdefault("DJANGO_USE_SQLITE", "1")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.apps import apps
    if not apps.ready:
        import django
        django.setup()


def _iter_routed_views(pattern=None):
    _setup_django()
    from django.urls import get_resolver
    from core.permissions import RoleRequiredMixin
    if pattern is None:
        pattern = get_resolver()
    if hasattr(pattern, "url_patterns"):
        for sub in pattern.url_patterns:
            yield from _iter_routed_views(sub)
        return
    callback = getattr(pattern, "callback", None)
    if callback is None:
        return
    view_class = getattr(callback, "view_class", None)
    if view_class and isinstance(view_class, type) and issubclass(view_class, RoleRequiredMixin):
        yield view_class


def _valid_permissions():
    """Compute the set of all real permissions from the model registry (no DB)."""
    _setup_django()
    from django.apps import apps
    valid = set()
    for app_config in apps.get_app_configs():
        app_label = app_config.label
        for model in app_config.get_models():
            name = model._meta.model_name
            valid.add(f"{app_label}.add_{name}")
            valid.add(f"{app_label}.change_{name}")
            valid.add(f"{app_label}.delete_{name}")
            valid.add(f"{app_label}.view_{name}")
            for codename, _desc in model._meta.permissions:
                valid.add(f"{app_label}.{codename}")
    return valid


# Legacy role-only views (allowed_roles but no permission requirement) that have
# not yet been converted. Access for these is still role-driven; they must be
# converted to permission-driven gating (Phase 2). The set must never grow.
KNOWN_ROLE_ONLY_VIEWS = {
    "core.views.DashboardRouterView",
    "core.views.SeedDatabaseView",
    "core.views.SessionCheckView",
    "core.views.SessionExtendView",
    "users.role_management_views.RoleUserCountAPIView",
    "users.views.HISMSPasswordChangeDoneView",
    "users.views.HISMSPasswordChangeView",
    "users.views.UserProfileView",
}


def _view_requirements(view_class):
    perms = []
    p = getattr(view_class, "required_permission", None)
    if isinstance(p, str):
        perms.append(p)
    any_p = getattr(view_class, "required_permissions_any", None) or []
    perms.extend(p for p in any_p if isinstance(p, str))
    return perms


def test_every_routed_view_permission_is_real():
    valid = _valid_permissions()
    dangling = {}
    for view_class in _iter_routed_views():
        for perm in _view_requirements(view_class):
            if perm not in valid:
                dangling.setdefault(perm, []).append(f"{view_class.__module__}.{view_class.__name__}")

    if dangling:
        lines = [
            f"  {perm}  (referenced by {', '.join(sorted(set(loc)))}"
            for perm, loc in sorted(dangling.items())
        ]
        pytest.fail(
            "Routed views reference permissions that are not real Django permissions. "
            "These pages are an unreachable 403 for every non-super-admin user:\n\n"
            + "\n".join(lines)
        )


def test_no_new_role_only_gated_views():
    role_only = {
        f"{v.__module__}.{v.__name__}"
        for v in _iter_routed_views()
        if getattr(v, "allowed_roles", None) and not _view_requirements(v)
    }
    unexpected = role_only - KNOWN_ROLE_ONLY_VIEWS
    if unexpected:
        pytest.fail(
            "Found routed views gated by hard-coded allowed_roles with no permission "
            "requirement. Access must be permission-driven (assigned via the role "
            "assignment UI). Convert these and remove them from the known list:\n\n"
            + "\n".join(f"  {name}" for name in sorted(unexpected))
        )
