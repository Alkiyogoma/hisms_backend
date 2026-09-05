from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0009_add_induction_checklist_completion'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='teacherclassassignment',
            index=models.Index(fields=['teacher'], name='idx_tca_teacher'),
        ),
        migrations.AddIndex(
            model_name='teacherclassassignment',
            index=models.Index(fields=['term'], name='idx_tca_term'),
        ),
        migrations.AddIndex(
            model_name='leaverequest',
            index=models.Index(fields=['staff'], name='idx_lr_staff'),
        ),
        migrations.AddIndex(
            model_name='leaveallocation',
            index=models.Index(fields=['staff'], name='idx_la_staff'),
        ),
    ]
