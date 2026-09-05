from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('welfare', '0003_alter_welfareobservation_concern_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='welfareobservation',
            name='time_of_incident',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='welfareobservation',
            name='location',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='welfareobservation',
            name='previous_incidents',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='welfareobservation',
            name='incident_level_1',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='welfareobservation',
            name='incident_level_2',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='welfareobservation',
            name='incident_level_3',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='welfareobservation',
            name='incident_level_4',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='welfareobservation',
            name='actions_taken_detailed',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
