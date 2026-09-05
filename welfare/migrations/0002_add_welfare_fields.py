# Generated manually - add missing welfare fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('welfare', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='welfareobservation',
            name='concern_type',
            field=models.CharField(
                choices=[
                    ('behavioral', 'Behavioral'),
                    ('health', 'Health'),
                    ('attendance', 'Attendance'),
                    ('academic', 'Academic'),
                    ('home_situation', 'Home situation'),
                    ('other', 'Other')
                ],
                db_index=True,
                max_length=20,
                default='other'
            ),
        ),
        migrations.AddField(
            model_name='welfareobservation',
            name='parent_contacted',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='welfareobservation',
            name='parent_contact_datetime',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
