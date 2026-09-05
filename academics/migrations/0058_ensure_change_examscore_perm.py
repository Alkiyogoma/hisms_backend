"""
Ensure 'change_examscore' permission exists and is assigned to all role groups
that need it.

Background: ExamScore lives in the academics app but the auto-generated
Permission row for 'change_examscore' was missing from the database, causing
403 errors for teachers and HODs trying to access score-entry pages.
"""
from django.db import migrations


PERM_CODENAME = "change_examscore"
APP_LABEL = "academics"
MODEL_NAME = "examscore"
PERM_NAME = "Can change examscore"

# All role groups that should have this permission
ROLE_GROUPS = [
    "role_teacher",
    "role_primary_hod",
    "role_ecd_hod",
    "role_lower_secondary_hod",
    "role_head_of_school",
    "role_super_admin",
    "role_admin_officer",
]


def forwards(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Group = apps.get_model("auth", "Group")

    ct = ContentType.objects.filter(app_label=APP_LABEL, model=MODEL_NAME).first()
    if ct is None:
        # Content type not yet created; skip — a later migration will create it.
        return

    perm, created = Permission.objects.get_or_create(
        codename=PERM_CODENAME,
        content_type=ct,
        defaults={"name": PERM_NAME},
    )

    # Assign to every role group that needs it
    for group_name in ROLE_GROUPS:
        group = Group.objects.filter(name=group_name).first()
        if group is not None and not group.permissions.filter(pk=perm.pk).exists():
            group.permissions.add(perm)


def backwards(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Permission.objects.filter(codename=PERM_CODENAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0057_alter_lessonplan_options"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
