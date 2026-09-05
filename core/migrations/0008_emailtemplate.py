from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_add_admissions_contact_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("template_type", models.CharField(
                    choices=[
                        ("activation", "Staff Account Activation"),
                        ("password_reset", "Password Reset"),
                        ("onboarding_complete", "Onboarding Complete"),
                        ("parent_portal", "Parent Portal Account"),
                        ("admission_inquiry", "Admissions Inquiry Acknowledgement"),
                        ("admission_meeting", "Admissions Meeting Scheduled"),
                        ("admission_denied", "Admissions Application Denied"),
                        ("fee_invoice", "Fee Invoice"),
                        ("fee_reminder", "Fee Payment Reminder"),
                        ("checkin_notification", "Attendance Check-in Notification"),
                        ("checkout_notification", "Attendance Check-out Notification"),
                        ("otp_code", "OTP Code"),
                        ("broadcast", "Broadcast Message"),
                    ],
                    max_length=40,
                    unique=True,
                )),
                ("name", models.CharField(help_text="Display name for this template.", max_length=100)),
                ("subject", models.CharField(
                    blank=True, default="", max_length=255,
                    help_text="Email subject line. Supports {{ variable }} syntax.",
                )),
                ("html_body", models.TextField(
                    blank=True, default="",
                    help_text="HTML email body. Supports Django template syntax.",
                )),
                ("plain_body", models.TextField(
                    blank=True, default="",
                    help_text="Plain-text fallback body. Supports Django template syntax.",
                )),
                ("is_enabled", models.BooleanField(
                    default=True,
                    help_text="Disable to suppress this email type entirely.",
                )),
                ("is_default", models.BooleanField(
                    default=False,
                    help_text="True for seeded default templates.",
                )),
            ],
            options={
                "ordering": ["template_type"],
                "verbose_name": "Email Template",
                "verbose_name_plural": "Email Templates",
            },
        ),
    ]
