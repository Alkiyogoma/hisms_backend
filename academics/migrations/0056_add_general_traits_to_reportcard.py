from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0055_add_day_of_week_to_lessonplan'),
    ]

    operations = [
        migrations.AddField(
            model_name='reportcard',
            name='general_traits',
            field=models.JSONField(blank=True, default=dict, help_text='JSON dict of trait scores, e.g. {"wh_follows_directions": "E", "pt_honest": "G", ...}'),
        ),
    ]
