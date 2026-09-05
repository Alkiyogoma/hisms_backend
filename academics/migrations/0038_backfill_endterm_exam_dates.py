from django.db import migrations, models


def backfill_endterm_exam_dates(apps, schema_editor):
    """Backfill new endterm exam date fields from deprecated generic exam fields."""
    Term = apps.get_model("academics", "Term")
    updated = Term.objects.filter(
        exam_start_date__isnull=False,
        endterm_exam_start_date__isnull=True,
    ).update(
        endterm_exam_start_date=models.F("exam_start_date"),
        endterm_exam_end_date=models.F("exam_end_date"),
    )
    if updated:
        print(f"Backfilled {updated} terms with endterm exam dates from legacy fields.")


def reverse_backfill(apps, schema_editor):
    """No-op: leaving the new fields populated is fine."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0037_add_exam_date_fields"),
    ]

    operations = [
        migrations.RunPython(backfill_endterm_exam_dates, reverse_backfill),
    ]
