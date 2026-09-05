from django.db import migrations


def seed_grades(apps, schema_editor):
    AdmissionGrade = apps.get_model("admissions", "AdmissionGrade")
    defaults = [
        "ECD",
        "Grade 1",
        "Grade 2",
        "Grade 3",
        "Grade 4",
        "Grade 5",
        "Grade 6",
        "Grade 7",
    ]
    for idx, name in enumerate(defaults, start=1):
        AdmissionGrade.objects.get_or_create(
            name=name,
            defaults={"sort_order": idx * 10, "is_active": True},
        )


def unseed_grades(apps, schema_editor):
    AdmissionGrade = apps.get_model("admissions", "AdmissionGrade")
    AdmissionGrade.objects.filter(
        name__in=[
            "ECD",
            "Grade 1",
            "Grade 2",
            "Grade 3",
            "Grade 4",
            "Grade 5",
            "Grade 6",
            "Grade 7",
        ]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("admissions", "0005_admissiongrade"),
    ]

    operations = [
        migrations.RunPython(seed_grades, unseed_grades),
    ]

