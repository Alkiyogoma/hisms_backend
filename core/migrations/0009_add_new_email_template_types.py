from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_emailtemplate"),
    ]

    operations = [
        migrations.AlterField(
            model_name="emailtemplate",
            name="template_type",
            field=models.CharField(
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
                    ("payment_received", "Payment Received Confirmation"),
                    ("payment_reversal", "Payment Reversal Notice"),
                    ("invoice_generated", "Invoice Generated Notification"),
                    ("leave_approved", "Leave Request Approved"),
                    ("leave_rejected", "Leave Request Rejected"),
                ],
                max_length=32,
                unique=True,
            ),
        ),
    ]
