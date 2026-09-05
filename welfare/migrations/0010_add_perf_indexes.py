from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('welfare', '0009_welfareacknowledgment'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='welfareobservation',
            index=models.Index(fields=['hod_status'], name='idx_wei_hod_status'),
        ),
        migrations.AddIndex(
            model_name='welfareobservation',
            index=models.Index(fields=['submitted_by'], name='idx_wei_submitted_by'),
        ),
    ]
