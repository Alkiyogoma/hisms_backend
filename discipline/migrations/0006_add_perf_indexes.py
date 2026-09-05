from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discipline', '0005_add_parent_contact_fields'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='disciplineincident',
            index=models.Index(fields=['reported_by'], name='idx_di_reported_by'),
        ),
        migrations.AddIndex(
            model_name='disciplineincident',
            index=models.Index(fields=['severity'], name='idx_di_severity'),
        ),
    ]
