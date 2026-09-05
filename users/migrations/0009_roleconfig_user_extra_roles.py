"""
0009_roleconfig_user_extra_roles - Create RoleConfig model and add extra_roles to User.

Run: python manage.py migrate users
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_roleconfig_table(apps, schema_editor):
    """Create the RoleConfig table if it doesn't exist."""
    RoleConfig = apps.get_model("users", "RoleConfig")
    # Table is created by the migration, nothing to pre-populate here.
    # Use `manage.py seed_roles` to populate after migrate.


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("users", "0008_alter_user_email"),
    ]

    operations = [
        # Create RoleConfig model
        migrations.CreateModel(
            name="RoleConfig",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("super_admin", "Super Admin"),
                            ("head_of_school", "Head of School (HOS)"),
                            ("primary_hod", "Primary HOD"),
                            ("ecd_hod", "ECD HOD"),
                            ("lower_secondary_hod", "Lower Secondary HOD"),
                            ("admin_officer", "Admin Officer"),
                            ("finance_officer", "Finance Officer"),
                            ("teacher", "Teacher"),
                            ("parent", "Parent / Guardian"),
                        ],
                        help_text="System role identifier from UserRole TextChoices.",
                        max_length=40,
                        unique=True,
                    ),
                ),
                ("label", models.CharField(max_length=80)),
                ("description", models.TextField(blank=True)),
                (
                    "departments",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text='List of department keys, e.g. ["ECD","PRIMARY"].',
                    ),
                ),
                (
                    "icon_color",
                    models.CharField(
                        default="#6B7280",
                        help_text="Hex colour for the role badge.",
                        max_length=7,
                    ),
                ),
                (
                    "is_system",
                    models.BooleanField(
                        default=True,
                        help_text="System roles cannot be deleted.",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Inactive roles are hidden from user assignment.",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "permissions",
                    models.ManyToManyField(
                        blank=True,
                        related_name="role_configs",
                        to="auth.permission",
                        help_text="Default Django permissions for this role.",
                    ),
                ),
            ],
            options={
                "ordering": ["role"],
            },
        ),
        # Add extra_roles M2M to User
        migrations.AddField(
            model_name="user",
            name="extra_roles",
            field=models.ManyToManyField(
                blank=True,
                help_text="Additional custom roles assigned to this user.",
                related_name="users",
                to="users.roleconfig",
            ),
        ),
    ]
