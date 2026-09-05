from django.db import migrations


def drop_indexes(apps, schema_editor):
    for idx in ('idx_lp_teacher', 'idx_lp_submitted_at', 'idx_rc_student', 'idx_rc_term', 'idx_rc_is_ecd'):
        schema_editor.execute(f'DROP INDEX IF EXISTS {idx}')


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0027_add_perf_indexes'),
    ]

    operations = [
        migrations.RunPython(drop_indexes, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name='abcgeneralassignment',
            unique_together={('report_card', 'quarter', 'item_name')},
        ),
        migrations.AlterUniqueTogether(
            name='abcpaceprogress',
            unique_together={('report_card', 'subject', 'pace_no')},
        ),
        migrations.AlterUniqueTogether(
            name='abcreadingprogramme',
            unique_together={('report_card', 'quarter')},
        ),
        migrations.AlterUniqueTogether(
            name='abcscripture',
            unique_together={('report_card', 'quarter')},
        ),
    ]
