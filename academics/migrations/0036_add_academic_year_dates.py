from django.db import migrations, models
from django.db.models import Min, Max


def backfill_academic_year_dates(apps, schema_editor):
    AcademicYear = apps.get_model("academics", "AcademicYear")
    Term = apps.get_model("academics", "Term")
    for year in AcademicYear.objects.all():
        result = Term.objects.filter(
            academic_year=year,
            start_date__isnull=False,
            end_date__isnull=False,
        ).aggregate(start=Min("start_date"), end=Max("end_date"))
        if result["start"] and result["end"]:
            AcademicYear.objects.filter(pk=year.pk).update(
                start_date=result["start"],
                end_date=result["end"],
            )


def resolve_duplicate_current_years(apps, schema_editor):
    AcademicYear = apps.get_model("academics", "AcademicYear")
    current = AcademicYear.objects.filter(is_current=True).order_by("-start_date", "-name")
    if current.count() > 1:
        keep = current.first()
        demoted = current.exclude(pk=keep.pk)
        demoted.update(is_current=False)
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(
            "Data migration: %d academic years had is_current=True; "
            "kept '%s' (start_date=%s) as current, demoted: %s",
            current.count(),
            keep.name,
            keep.start_date,
            ", ".join(f"'{y.name}' (start_date={y.start_date})" for y in demoted),
        )


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0035_add_recalc_status_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='academicyear',
            name='end_date',
            field=models.DateField(blank=True, help_text='Last day of this academic year (calendar date).', null=True),
        ),
        migrations.AddField(
            model_name='academicyear',
            name='start_date',
            field=models.DateField(blank=True, help_text='First day of this academic year (calendar date).', null=True),
        ),
        migrations.RunPython(backfill_academic_year_dates, migrations.RunPython.noop),
        migrations.RunPython(resolve_duplicate_current_years, migrations.RunPython.noop),
    ]
