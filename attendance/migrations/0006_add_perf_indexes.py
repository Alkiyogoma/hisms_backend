from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0005_alter_attendanceentry_status_and_more'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='staffattendanceentry',
            index=models.Index(fields=['staff'], name='idx_sae_staff'),
        ),
    ]
