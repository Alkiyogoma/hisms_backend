"""
Management command: seed default RoleConfig entries and permissions.

Run:
    python manage.py seed_roles           # Add missing default permissions (never removes any)
    python manage.py seed_roles --prune   # Also take away permissions that aren't in the defaults
    python manage.py seed_roles --reset   # Reset all roles to exactly the defaults

By default this is add-only, so it is safe on a live system: permissions an
admin has granted in Role Management are kept. --prune and --reset only
unlink permissions from roles; they never delete Permission records.
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
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Remove permissions from role groups that aren't in the defaults "
                 "(drops permissions granted in Role Management).",
        )

    def handle(self, *args, **options):
        reset = options["reset"]
        prune = options["prune"]
        total_perms = 0

        for role_value, meta in DEFAULT_ROLE_CONFIGS.items():
            # get_or_create: never overwrite labels/departments/active flags
            # that an admin has edited in Role Management.
            role_config, created = RoleConfig.objects.get_or_create(
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
            status = "CREATED" if created else "KEPT"
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

            # Unlink only — calling .delete() here used to delete the
            # Permission rows themselves, stripping them from every role/user.
            if to_remove and prune:
                group.permissions.remove(*group.permissions.filter(codename__in=to_remove))

            if to_add:
                new_perms = Permission.objects.filter(codename__in=to_add)
                group.permissions.add(*new_perms)

            # Keep the RoleConfig M2M in step with the group
            if reset or prune:
                role_config.permissions.set(group.permissions.all())
            else:
                role_config.permissions.add(*group.permissions.all())

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
