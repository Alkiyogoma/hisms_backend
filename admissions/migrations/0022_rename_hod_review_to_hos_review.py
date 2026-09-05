from django.db import migrations, models


class Migration(migrations.Migration):
    """Rename hod_review status to hos_review in admissions."""

    dependencies = [
        ("admissions", "0021_add_reference_number"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                """
                UPDATE admissions_applicant
                SET status = 'hos_review'
                WHERE status = 'hod_review';
                """,
                """
                UPDATE admissions_applicanttimelineentry
                SET from_status = 'hos_review'
                WHERE from_status = 'hod_review';
                """,
                """
                UPDATE admissions_applicanttimelineentry
                SET to_status = 'hos_review'
                WHERE to_status = 'hod_review';
                """,
            ],
            reverse_sql=[
                """
                UPDATE admissions_applicant
                SET status = 'hod_review'
                WHERE status = 'hos_review';
                """,
                """
                UPDATE admissions_applicanttimelineentry
                SET from_status = 'hod_review'
                WHERE from_status = 'hos_review';
                """,
                """
                UPDATE admissions_applicanttimelineentry
                SET to_status = 'hod_review'
                WHERE to_status = 'hos_review';
                """,
            ],
        ),
    ]
