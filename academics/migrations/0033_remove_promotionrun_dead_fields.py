# Generated manually — removes dead / unused fields from PromotionRun
# and renames repeated_count → retained_count for consistency with the codebase.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0032_add_promotionrun_resume_fields'),
    ]

    operations = [
        # Rename: repeated_count → retained_count (views already use retained_count)
        migrations.RenameField(
            model_name='promotionrun',
            old_name='repeated_count',
            new_name='retained_count',
        ),
        # Remove dead fields from the old CLI-script era
        migrations.RemoveField(
            model_name='promotionrun',
            name='min_average',
        ),
        migrations.RemoveField(
            model_name='promotionrun',
            name='min_attendance',
        ),
        migrations.RemoveField(
            model_name='promotionrun',
            name='is_committed',
        ),
        migrations.RemoveField(
            model_name='promotionrun',
            name='total_students',
        ),
        migrations.RemoveField(
            model_name='promotionrun',
            name='skipped_count',
        ),
        migrations.RemoveField(
            model_name='promotionrun',
            name='results_json',
        ),
    ]
