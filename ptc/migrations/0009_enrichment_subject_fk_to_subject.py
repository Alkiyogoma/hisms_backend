"""
Refactor EnrichmentSubject to link to academics.Subject via FK
instead of maintaining a duplicate name field.
"""

from django.db import migrations, models


def forwards(apps, schema_editor):
    Subject = apps.get_model("academics", "Subject")
    EnrichmentSubject = apps.get_model("ptc", "EnrichmentSubject")

    for es in EnrichmentSubject.objects.all():
        subj, _ = Subject.objects.get_or_create(
            name=es.name,
            defaults={"is_enrichment": True, "is_active": True, "department": "PRIMARY"},
        )
        es.subject = subj
        es.save(update_fields=["subject"])


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0054_alter_progressioncase_options"),
        ("ptc", "0008_seed_enrichment_defaults"),
    ]

    operations = [
        # Step 1: Add nullable FK
        migrations.AddField(
            model_name="enrichmentsubject",
            name="subject",
            field=models.OneToOneField(
                "academics.Subject",
                on_delete=models.PROTECT,
                related_name="enrichment_config",
                null=True,
                help_text="Academic subject marked as enrichment (is_enrichment=True)",
            ),
        ),
        # Step 2: Migrate data
        migrations.RunPython(forwards, migrations.RunPython.noop),
        # Step 3: Make non-nullable
        migrations.AlterField(
            model_name="enrichmentsubject",
            name="subject",
            field=models.OneToOneField(
                "academics.Subject",
                on_delete=models.PROTECT,
                related_name="enrichment_config",
                help_text="Academic subject marked as enrichment (is_enrichment=True)",
            ),
        ),
        # Step 4: Remove old name field
        migrations.RemoveField(
            model_name="enrichmentsubject",
            name="name",
        ),
        # Step 5: Update ordering
        migrations.AlterModelOptions(
            name="enrichmentsubject",
            options={
                "ordering": ["subject__name"],
                "permissions": [
                    ("manage_enrichment_config", "Can manage enrichment subject configuration")
                ],
                "verbose_name": "Enrichment Subject",
                "verbose_name_plural": "Enrichment Subjects",
            },
        ),
    ]
