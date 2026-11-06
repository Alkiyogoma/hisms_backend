"""
Give the ECD HOD role the ECD Report Setup permissions.

The permissions were added to ROLE_DEFAULT_PERMISSIONS with the setup page
(0069), but role groups only pick up new defaults when someone runs
``seed_roles``, so the ECD HOD was refused the page. Add-only: nothing an
admin granted or removed for other roles changes.
"""
from django.db import migrations

APP_LABEL = "academics"
MODELS = {"ecdsection": "ECD section", "ecditem": "ECD item", "ecdreportlayout": "ECD report layout"}
ACTIONS = ("view", "add", "change", "delete")
ROLE = "ecd_hod"


def forwards(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Group = apps.get_model("auth", "Group")
    RoleConfig = apps.get_model("users", "RoleConfig")

    perms = []
    for model, verbose in MODELS.items():
        ct, _ = ContentType.objects.get_or_create(app_label=APP_LABEL, model=model)
        for action in ACTIONS:
            perm, _ = Permission.objects.get_or_create(
                codename=f"{action}_{model}", content_type=ct,
                defaults={"name": f"Can {action} {verbose}"},
            )
            perms.append(perm)

    group, _ = Group.objects.get_or_create(name=f"role_{ROLE}")
    group.permissions.add(*perms)
    config = RoleConfig.objects.filter(role=ROLE).first()
    if config is not None:
        config.permissions.add(*perms)


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0071_ecd_evaluation_item"),
        ("users", "0013_parent_login_code_contact"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
