# Generated manually — adds grading_deadline to academics_term

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0007_merge_0006_exam_type_configuration_0006_room'),
    ]

    operations = [
        migrations.AddField(
            model_name='term',
            name='grading_deadline',
            field=models.DateField(
                blank=True,
                null=True,
                help_text='Deadline for teachers to complete score entry.',
            ),
        ),
    ]
