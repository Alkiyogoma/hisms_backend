from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0021_reportcard_parent_signature_name_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='reportcard',
            name='ecd_remarks',
            field=models.CharField(blank=True, help_text='Override auto-computed ECD remarks label (Outstanding/Good/Satisfactory/Needs Improvement)', max_length=50),
        ),
    ]
