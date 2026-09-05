"""
Celery Tasks for Attendance Notifications

Background tasks for:
- Sending SMS notifications
- Sending email notifications
- Retrying failed notifications
- Cleaning up expired OTP codes
"""

from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
import logging

from .models import Message, NotificationLog, OtpCode
from .notification_service import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def send_sms_notification(self, message_id: int):
    """
    Send SMS notification via external SMS gateway.
    
    Args:
        message_id: ID of Message record to send
    """
    try:
        message = Message.objects.get(id=message_id)
        
        # Check if already sent
        if message.status == 1:  # Sent
            logger.info(f"Message {message_id} already sent")
            return
        
        # Send via configured SMS provider
        result = NotificationService.send_sms(message.phone, message.message)
        
        if result['success']:
            # Mark as sent
            message.mark_sent()
            logger.info(f"SMS sent to {message.phone}")
        else:
            # Mark as failed
            message.mark_failed(result.get('error', 'Unknown error'))
            logger.error(f"SMS send failed to {message.phone}: {result.get('error')}")
            
            # Retry if under max retries
            if message.retry_count < NotificationService.SMS_MAX_RETRIES:
                raise self.retry(exc=Exception(result.get('error')), countdown=NotificationService.SMS_RETRY_DELAY)
        
    except Message.DoesNotExist:
        logger.error(f"Message {message_id} not found")
    except Exception as exc:
        logger.error(f"Error sending SMS {message_id}: {str(exc)}")
        # Retry with exponential backoff
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task(bind=True, max_retries=3)
def send_email_notification(self, notification_id: int):
    """
    Send email notification.
    
    Args:
        notification_id: ID of NotificationLog record to send
    """
    try:
        notification = NotificationLog.objects.get(id=notification_id)
        
        # Check if already sent
        if notification.delivery_status == 'sent':
            logger.info(f"Notification {notification_id} already sent")
            return
        
        # Skip if no email
        if not notification.recipient_email:
            logger.warning(f"No email for notification {notification_id}")
            return
        
        # Try dynamic DB template first
        from core.email_templates import send_dynamic_email
        from core.models import SchoolSettings
        school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"

        if notification.notification_type == 'otp':
            tpl_context = {
                "guardian_name": getattr(notification, 'recipient_name', 'Parent'),
                "otp_code": getattr(notification, 'otp_code', ''),
                "expiry_minutes": "10",
                "school_name": school_name,
            }
            db_sent = send_dynamic_email(
                template_type="otp_code",
                to_email=notification.recipient_email,
                context=tpl_context,
            )
            if db_sent:
                notification.delivery_status = 'sent'
                notification.sent_at = timezone.now()
                notification.save(update_fields=['delivery_status', 'sent_at'])
                return

        # Fallback to static
        if notification.notification_type == 'checkin':
            subject = NotificationService.CHECKIN_EMAIL_SUBJECT
        elif notification.notification_type == 'checkout':
            subject = NotificationService.CHECKOUT_EMAIL_SUBJECT
        elif notification.notification_type == 'otp':
            subject = NotificationService.OTP_EMAIL_SUBJECT
        else:
            subject = "Hodari Christian School Notification"
        
        # Send email
        send_mail(
            subject=subject,
            message=notification.message,
            from_email=NotificationService.EMAIL_FROM,
            recipient_list=[notification.recipient_email],
            fail_silently=False,
        )
        
        # Mark as sent
        notification.delivery_status = 'sent'
        notification.sent_at = timezone.now()
        notification.save(update_fields=['delivery_status', 'sent_at'])
        
        logger.info(f"Email sent to {notification.recipient_email}")
        
    except NotificationLog.DoesNotExist:
        logger.error(f"Notification {notification_id} not found")
    except Exception as exc:
        logger.error(f"Error sending email {notification_id}: {str(exc)}")
        # Mark as failed
        try:
            notification = NotificationLog.objects.get(id=notification_id)
            notification.delivery_status = 'failed'
            notification.error_message = str(exc)
            notification.retry_count += 1
            notification.save(update_fields=['delivery_status', 'error_message', 'retry_count'])
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to update notification %s status after retry failure", notification_id, exc_info=True,
            )
        
        # Retry with exponential backoff
        raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))


@shared_task
def retry_failed_notifications():
    """
    Retry failed SMS and email notifications.
    Called periodically by Celery beat.
    """
    try:
        NotificationService.retry_failed_notifications()
        logger.info("Retry failed notifications task completed")
    except Exception as e:
        logger.error(f"Error in retry_failed_notifications task: {str(e)}")


@shared_task
def cleanup_expired_otps():
    """
    Clean up expired OTP codes.
    Called periodically by Celery beat.
    """
    try:
        NotificationService.cleanup_expired_otps()
        logger.info("Cleanup expired OTPs task completed")
    except Exception as e:
        logger.error(f"Error in cleanup_expired_otps task: {str(e)}")


@shared_task
def process_pending_sms():
    """
    Process all pending SMS messages.
    Called periodically by Celery beat.
    """
    try:
        pending_messages = Message.objects.filter(status=0)  # Pending
        
        for message in pending_messages:
            send_sms_notification.delay(message.id)
        
        logger.info(f"Queued {pending_messages.count()} SMS messages for processing")
        
    except Exception as e:
        logger.error(f"Error in process_pending_sms task: {str(e)}")@shared_task
def process_pending_emails():
    """
    Process all pending email notifications.
    Called periodically by Celery beat.
    """
    try:
        pending_notifications = NotificationLog.objects.filter(
            delivery_status='pending',
            recipient_email__isnull=False
        ).exclude(recipient_email='')

        for notification in pending_notifications:
            send_email_notification.delay(notification.id)

        logger.info(f"Queued {pending_notifications.count()} email notifications for processing")

    except Exception as e:
        logger.error(f"Error in process_pending_emails task: {str(e)}")


# ---------------------------------------------------------------------------
# FR-ATT-003: Auto-Absent at end of school day
# ---------------------------------------------------------------------------

@shared_task
def mark_auto_absent():
    """
    FR-ATT-003: Mark all UNCONFIRMED students as ABSENT for today.

    Runs at 16:30 EAT (school day ends ~15:30) via Celery beat.
    Delegates to the ``mark_auto_absent`` management command so the
    logic is reusable from CLI and from the periodic task.
    """
    from django.core.management import call_command
    import io

    out = io.StringIO()
    try:
        call_command("mark_auto_absent", stdout=out)
        output = out.getvalue().strip()
        logger.info("mark_auto_absent task: %s", output)
        return output
    except Exception as exc:
        logger.error("mark_auto_absent task failed: %s", exc)
        raise


@shared_task
def cleanup_expired_otp_codes():
    """
    Clean up expired OTP codes.
    Called periodically by Celery beat.
    """
    try:
        NotificationService.cleanup_expired_otps()
        logger.info("Cleanup expired OTPs task completed")
    except Exception as e:
        logger.error(f"Error in cleanup_expired_otp_codes task: {str(e)}")


# ---------------------------------------------------------------------------
# FR-ATT-006: 9 AM unconfirmed attendance alert
# ---------------------------------------------------------------------------

@shared_task
def check_unconfirmed_attendance_9am():
    """
    FR-ATT-006: At 9:00 AM, alert teachers and admins about unconfirmed students.
    Skips on weekends and public holidays.
    Delegates to the check_unconfirmed_attendance management command.
    """
    from core.utils import is_school_day
    from datetime import date as _date

    if not is_school_day(_date.today()):
        logger.info("check_unconfirmed_attendance_9am: skipped — not a school day")
        return "Skipped: not a school day"

    from django.core.management import call_command
    import io

    out = io.StringIO()
    try:
        call_command("check_unconfirmed_attendance", stdout=out)
        output = out.getvalue().strip()
        logger.info("check_unconfirmed_attendance_9am task: %s", output)
        return output
    except Exception as exc:
        logger.error("check_unconfirmed_attendance_9am task failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# FR-FIN-012: Auto-overdue invoice flagging (daily)
# ---------------------------------------------------------------------------

@shared_task
def check_overdue_invoices_task():
    """
    FR-FIN-012: Daily task to flag overdue invoices and send reminders.
    Delegates to the check_overdue_invoices management command.
    """
    from django.core.management import call_command
    import io

    out = io.StringIO()
    try:
        call_command("check_overdue_invoices", stdout=out)
        output = out.getvalue().strip()
        logger.info("check_overdue_invoices_task: %s", output)
        return output
    except Exception as exc:
        logger.error("check_overdue_invoices_task failed: %s", exc)
        raise
