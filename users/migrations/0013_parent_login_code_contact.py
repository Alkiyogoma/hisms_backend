from django.db import migrations, models


class Migration(migrations.Migration):
    """Parent sign-in codes can go by SMS or email: phone -> contact + channel."""

    dependencies = [
        ("users", "0012_parent_login_code"),
    ]

    operations = [
        migrations.RenameField(model_name="parentlogincode", old_name="phone", new_name="contact"),
        migrations.AlterField(
            model_name="parentlogincode", name="contact",
            field=models.CharField(db_index=True, max_length=254,
                                   help_text="Normalised phone (255XXXXXXXXX) or lower-case email"),
        ),
        migrations.AddField(
            model_name="parentlogincode", name="channel",
            field=models.CharField(choices=[("sms", "SMS"), ("email", "Email")], default="sms", max_length=8),
        ),
    ]
