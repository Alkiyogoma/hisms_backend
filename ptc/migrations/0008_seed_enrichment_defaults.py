"""
Seed the seven default enrichment subjects into the database.
These were previously hardcoded in ENRICHMENT_SUBJECT_CHOICES.
"""

from django.db import migrations

DEFAULT_SUBJECTS = [
    "Bible",
    "ICT",
    "Global Perspective",
    "PE",
    "Swimming",
    "French",
    "Music",
]


def seed_defaults(apps, schema_editor):
    EnrichmentSubject = apps.get_model("ptc", "EnrichmentSubject")
    for name in DEFAULT_SUBJECTS:
        EnrichmentSubject.objects.get_or_create(
            name=name,
            defaults={"is_active": True, "weight_percentage": 0},
        )


def remove_defaults(apps, schema_editor):
    EnrichmentSubject = apps.get_model("ptc", "EnrichmentSubject")
    EnrichmentSubject.objects.filter(name__in=DEFAULT_SUBJECTS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("ptc", "0007_dynamic_enrichment_subjects"),
    ]

    operations = [
        migrations.RunPython(seed_defaults, remove_defaults),
    ]
