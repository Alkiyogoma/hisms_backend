from django.db import migrations

OLD_PLACEHOLDERS = {"+255 700 000 000", "+255700000000", "0700 000 000", "0700000000"}
SUPPORT_PHONE = "+255 767 016 940"
FIELDS = ("admissions_phone", "admissions_whatsapp", "parent_contact_phone")


def replace_placeholder(apps, schema_editor):
    SchoolSettings = apps.get_model("core", "SchoolSettings")
    for row in SchoolSettings.objects.all():
        changed = []
        for field in FIELDS:
            if (getattr(row, field) or "").strip() in OLD_PLACEHOLDERS:
                setattr(row, field, SUPPORT_PHONE)
                changed.append(field)
        if changed:
            row.save(update_fields=changed)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_alter_schoolsettings_late_pickup_fee"),
    ]

    operations = [
        migrations.RunPython(replace_placeholder, migrations.RunPython.noop),
    ]
