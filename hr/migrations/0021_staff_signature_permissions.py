"""
Give the roles that handle staff signatures their permissions.

Role groups only pick up new ROLE_DEFAULT_PERMISSIONS when someone runs
``seed_roles``, which deploys don't, so add them here (add-only).
Head of School: view, upload and approve. Admin officer (HR): view and
upload. Super admin: everything (the views already let them in; this also
shows the HR "Signatures" tab).
"""
from django.db import migrations

GRANTS = {
    "super_admin": ["view", "add", "change", "delete", "approve"],
    "head_of_school": ["view", "add", "change", "approve"],
    "admin_officer": ["view", "add"],
}
NAMES = {
    "view": "Can view Staff Signature",
    "add": "Can add Staff Signature",
    "change": "Can change Staff Signature",
    "delete": "Can delete Staff Signature",
    "approve": "Can approve staff signatures",
}


def forwards(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Group = apps.get_model("auth", "Group")
    RoleConfig = apps.get_model("users", "RoleConfig")

    ct, _ = ContentType.objects.get_or_create(app_label="hr", model="staffsignature")
    perms = {}
    for action, name in NAMES.items():
        perms[action], _ = Permission.objects.get_or_create(
            codename=f"{action}_staffsignature", content_type=ct, defaults={"name": name},
        )
    for role, actions in GRANTS.items():
        chosen = [perms[a] for a in actions]
        group, _ = Group.objects.get_or_create(name=f"role_{role}")
        group.permissions.add(*chosen)
        config = RoleConfig.objects.filter(role=role).first()
        if config is not None:
            config.permissions.add(*chosen)


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0020_staff_signature"),
        ("users", "0013_parent_login_code_contact"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
