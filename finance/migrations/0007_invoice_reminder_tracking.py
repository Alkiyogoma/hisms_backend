from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0006_unmatchedpayment"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="last_reminder_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="last_reminder_type",
            field=models.CharField(blank=True, db_index=True, max_length=20),
        ),
    ]
