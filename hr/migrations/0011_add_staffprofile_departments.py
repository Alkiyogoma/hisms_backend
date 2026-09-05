from django.db import migrations, models


def migrate_existing_department(apps, schema_editor):
    StaffProfile = apps.get_model("hr", "StaffProfile")
    for profile in StaffProfile.objects.all():
        if profile.department and not profile.departments:
            profile.departments = [profile.department]
            profile.save(update_fields=["departments"])


class Migration(migrations.Migration):
    dependencies = [
        ("hr", "0010_add_perf_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffprofile",
            name="departments",
            field=models.JSONField(default=list, blank=True),
        ),
        migrations.RunPython(migrate_existing_department, reverse_code=migrations.RunPython.noop),
    ]
