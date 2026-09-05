from django.db import migrations, models


def assign_departments(apps, schema_editor):
    AdmissionGrade = apps.get_model("admissions", "AdmissionGrade")
    ecd_tokens = ("ecd", "pre-", "kindergarten", "nursery", "abc", "pre-school", "pre school")
    secondary_tokens = ("grade 7", "grade 8", "form ")

    for grade in AdmissionGrade.objects.all():
        name = (grade.name or "").lower()
        if any(tok in name for tok in ecd_tokens) or name == "ecd":
            grade.department = "ECD"
        elif any(tok in name for tok in secondary_tokens):
            grade.department = "LOWER_SECONDARY"
        else:
            grade.department = "PRIMARY"
        grade.save(update_fields=["department"])


def sync_from_grade_classes(apps, schema_editor):
    """Align admission grades with academic GradeClass where names match."""
    AdmissionGrade = apps.get_model("admissions", "AdmissionGrade")
    GradeClass = apps.get_model("academics", "GradeClass")
    for gc in GradeClass.objects.all():
        ag, created = AdmissionGrade.objects.get_or_create(
            name=gc.name,
            defaults={
                "department": gc.department,
                "sort_order": 100,
                "is_active": True,
            },
        )
        if not created and ag.department != gc.department:
            ag.department = gc.department
            ag.save(update_fields=["department"])


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0004_gradeclass_subject_reportcard_ecd_template_type_and_more"),
        ("admissions", "0010_applicant_photo"),
    ]

    operations = [
        migrations.AddField(
            model_name="admissiongrade",
            name="department",
            field=models.CharField(
                choices=[
                    ("ECD", "ECD"),
                    ("PRIMARY", "Primary"),
                    ("LOWER_SECONDARY", "Lower Secondary"),
                    ("ADMINISTRATION", "Administration"),
                ],
                db_index=True,
                default="PRIMARY",
                max_length=32,
            ),
        ),
        migrations.RunPython(assign_departments, migrations.RunPython.noop),
        migrations.RunPython(sync_from_grade_classes, migrations.RunPython.noop),
    ]
