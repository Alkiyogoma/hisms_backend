from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('timetable', '0006_timetableslot_subject'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='timetableslot',
            index=models.Index(fields=['teacher'], name='idx_tt_teacher'),
        ),
    ]
