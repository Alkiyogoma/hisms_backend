# Generated manually - make LessonPlan content fields optional since they're no longer collected in the form

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0012_alter_timetableentry_unique_together_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='lessonplan',
            name='lesson_title',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AlterField(
            model_name='lessonplan',
            name='objectives',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='lessonplan',
            name='activities',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='lessonplan',
            name='assessment_strategy',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='lessonplan',
            name='resources',
            field=models.TextField(blank=True, default=''),
        ),
    ]
