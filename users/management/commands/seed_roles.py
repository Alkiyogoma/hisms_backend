"""
Management command: seed default RoleConfig entries and permissions.

Run:
    python manage.py seed_roles
    python manage.py seed_roles --reset   # Reset all roles to defaults
"""

from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission

from users.models import UserRole
from users.role_models import (
    RoleConfig,
    DEFAULT_ROLE_CONFIGS,
    ROLE_DEFAULT_PERMISSIONS,
)


class Command(BaseCommand):
    help = "Seed default RoleConfig entries and assign default permissions per role."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Clear all existing permissions from role groups before seeding.",
        )

    def handle(self, *args, **options):
        reset = options["reset"]
        total_perms = 0

        for role_value, meta in DEFAULT_ROLE_CONFIGS.items():
            role_config, created = RoleConfig.objects.update_or_create(
                role=role_value,
                defaults={
                    "label": meta["label"],
                    "description": meta["description"],
                    "departments": meta["departments"],
                    "icon_color": meta["icon_color"],
                    "is_system": True,
                    "is_active": True,
                },
            )
            status = "CREATED" if created else "UPDATED"
            self.stdout.write(f"  {status}: {meta['label']}")

            # Get or create the auth Group for this role
            group_name = f"role_{role_value}"
            group, _ = Group.objects.get_or_create(name=group_name)

            # Resolve default permission codenames
            codenames = ROLE_DEFAULT_PERMISSIONS.get(role_value, [])
            perms = Permission.objects.filter(codename__in=codenames)

            if reset:
                group.permissions.clear()

            # Sync permissions
            existing = set(group.permissions.values_list("codename", flat=True))
            target = set(codenames)

            to_add = target - existing
            to_remove = existing - target

            if to_remove:
                group.permissions.filter(codename__in=to_remove).delete()

            if to_add:
                new_perms = Permission.objects.filter(codename__in=to_add)
                group.permissions.add(*new_perms)

            # Also set on the RoleConfig M2M
            role_config.permissions.set(group.permissions.all())

            count = group.permissions.count()
            total_perms += count
            self.stdout.write(
                f"    {count} permissions assigned (Group: {group_name})"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {len(DEFAULT_ROLE_CONFIGS)} roles seeded, "
                f"{total_perms} total permissions assigned."
            )
        )
