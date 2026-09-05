from django.db import migrations


def drop_indexes(apps, schema_editor):
    for idx in ('idx_la_staff', 'idx_lr_staff', 'idx_tca_teacher', 'idx_tca_term'):
        schema_editor.execute(f'DROP INDEX IF EXISTS {idx}')


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0010_add_perf_indexes'),
    ]

    operations = [
        migrations.RunPython(drop_indexes, migrations.RunPython.noop),
    ]
