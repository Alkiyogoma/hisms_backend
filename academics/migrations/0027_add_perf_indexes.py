from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0026_add_lessonplan_missing_status'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='lessonplan',
            index=models.Index(fields=['teacher'], name='idx_lp_teacher'),
        ),
        migrations.AddIndex(
            model_name='lessonplan',
            index=models.Index(fields=['submitted_at'], name='idx_lp_submitted_at'),
        ),
        migrations.AddIndex(
            model_name='reportcard',
            index=models.Index(fields=['student'], name='idx_rc_student'),
        ),
        migrations.AddIndex(
            model_name='reportcard',
            index=models.Index(fields=['term'], name='idx_rc_term'),
        ),
        migrations.AddIndex(
            model_name='reportcard',
            index=models.Index(fields=['is_ecd_report'], name='idx_rc_is_ecd'),
        ),
    ]
