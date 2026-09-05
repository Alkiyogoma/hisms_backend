from django.db import migrations


def forward(apps, schema_editor):
    """Remove indexes only if they exist (safe for fresh DBs)."""
    table_map = {
        'idx_lp_teacher': 'academics_lessonplan',
        'idx_lp_submitted_at': 'academics_lessonplan',
        'idx_rc_student': 'academics_reportcard',
        'idx_rc_term': 'academics_reportcard',
        'idx_rc_is_ecd': 'academics_reportcard',
    }
    for idx_name, table_name in table_map.items():
        sql = f"DROP INDEX IF EXISTS {idx_name}"
        schema_editor.execute(sql)


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0027_add_perf_indexes'),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
