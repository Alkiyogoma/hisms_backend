"""Give the Head of School role the new "Can view safeguarding notes"
permission so safeguarding notes stay visible after deploy.

Add-only: nothing is removed from any role, so permissions admins have
already granted or revoked in Role Management are left as they are.
"""
from django.db import migrations

CODENAME = "view_safeguarding_note"
ROLE = "head_of_school"


def grant(apps, schema_editor):
    from django.contrib.auth.management import create_permissions

    # Permissions are normally created after migrate finishes; make sure this
    # one exists now so it can be granted.
    welfare_config = apps.get_app_config("welfare")
    welfare_config.models_module = True
    create_permissions(welfare_config, apps=apps, verbosity=0)
    welfare_config.models_module = None

    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    RoleConfig = apps.get_model("users", "RoleConfig")

    perm = Permission.objects.filter(
        codename=CODENAME, content_type__app_label="welfare"
    ).first()
    if perm is None:
        return
    group = Group.objects.filter(name=f"role_{ROLE}").first()
    if group is not None:
        group.permissions.add(perm)
    role_config = RoleConfig.objects.filter(role=ROLE).first()
    if role_config is not None:
        role_config.permissions.add(perm)


class Migration(migrations.Migration):

    dependencies = [
        ("welfare", "0014_safeguarding_permission_label"),
        ("users", "0010_alter_roleconfig_id"),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
