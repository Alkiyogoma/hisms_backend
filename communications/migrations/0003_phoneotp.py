from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("communications", "0002_add_weeklyfocus"),
    ]

    operations = [
        migrations.CreateModel(
            name="PhoneOTP",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("phone", models.CharField(db_index=True, max_length=32)),
                ("code_hash", models.CharField(max_length=64)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["phone", "expires_at"], name="communicati_phone_6e8f0a_idx")],
            },
        ),
    ]
