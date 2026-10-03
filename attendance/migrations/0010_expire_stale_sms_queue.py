"""Expire every SMS still queued before instant sending was enabled.

Attendance SMS used to be queued but never sent, so the queue holds weeks-old
check-in alerts and pickup codes. They must never go out now, so mark them
failed (with retries exhausted) instead of letting the new sender pick them up.
"""
from django.db import migrations

REASON = "Expired: queued before SMS sending was enabled; never sent."


def expire_stale_sms(apps, schema_editor):
    Message = apps.get_model("attendance", "Message")
    NotificationLog = apps.get_model("attendance", "NotificationLog")
    Message.objects.filter(status__in=[0, 3]).update(status=2, retry_count=3, error_message=REASON)
    (NotificationLog.objects.filter(delivery_status__in=["pending", "retry"], recipient_email="")
     .exclude(recipient_phone="")
     .update(delivery_status="failed", retry_count=3, error_message=REASON))


class Migration(migrations.Migration):
    dependencies = [("attendance", "0009_attendance_correction_request")]
    operations = [migrations.RunPython(expire_stale_sms, migrations.RunPython.noop)]
