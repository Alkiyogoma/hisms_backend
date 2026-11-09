"""
ECD team feedback after testing the reports:

* Keep the reviewer's reason on a returned report so the class teacher can
  see what to correct (it was only sent in a notification).
* ABC report: rating key "1 - Area of Concern" reads "1 - Not Yet" and the
  accent is yellow like the other ECD reports.
* Bible Memory (ABC and Preschool) is a full-width section header; the verses
  are its line items, added per term in Report Setup. The old single
  "Bible Memory" line (with the verse typed as a note) is retired.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def abc_forwards(apps, schema_editor):
    Layout = apps.get_model("academics", "ECDReportLayout")
    Section = apps.get_model("academics", "ECDSection")
    Item = apps.get_model("academics", "ECDItem")
    ReportCard = apps.get_model("academics", "ReportCard")
    ReportCard.objects.filter(is_ecd_report=True, ecd_remarks__iexact="area of concern").update(ecd_remarks="Not Yet")

    for layout in Layout.objects.filter(class_type="abc"):
        layout.academic_key = [
            [v, "Not Yet" if str(label).strip().lower() == "area of concern" else label]
            for v, label in (layout.academic_key or [])
        ]
        if layout.accent == "blue":
            layout.accent = "yellow"
        layout.save(update_fields=["academic_key", "accent"])

    verse = Section.objects.filter(style="verse")
    verse.update(column="full")
    Item.objects.filter(section__in=verse, label__iexact="bible memory").update(is_active=False)


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0072_ecd_setup_perms_for_ecd_hod"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="reportcard",
            name="return_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="reportcard",
            name="returned_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="reports_returned", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="reportcard",
            name="returned_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="ecdsection",
            name="style",
            field=models.CharField(
                choices=[("table", "Rated list"), ("strip", "Subject points row"),
                         ("marks", "Marks out of 100"), ("verse", "Bible memory verses")],
                default="table", max_length=10,
            ),
        ),
        migrations.RunPython(abc_forwards, migrations.RunPython.noop),
    ]
