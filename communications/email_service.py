import logging
import time
from django.conf import settings
from django.core.mail import send_mail
from audit.models import log_event
from .models import Notification

logger = logging.getLogger(__name__)

# SLA thresholds in seconds
SLA_IN_APP_SECONDS = 60
SLA_EMAIL_SECONDS = 300


def send_email_safe(to_email, subject, body, html_body=None, actor=None, action_type=""):
    """
    Safely dispatches an email using Django's SMTP backend.
    Catches failures so they don't crash the calling action.
    Logs every attempt to EmailSendLog (OP 5.2).
    """
    if not to_email:
        return False

    ip = None
    try:
        from core.threadlocal import _request_ctx
        req = _request_ctx.get(None)
        if req:
            from core.utils import get_client_ip
            ip = get_client_ip(req)
    except Exception:
        pass

    try:
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@hodarischool.com')
        send_mail(
            subject=subject,
            message=body,
            from_email=from_email,
            recipient_list=[to_email],
            fail_silently=False,
            html_message=html_body,
        )
        _log_email_send(to_email, subject, True, "", actor, action_type, ip)
        return True
    except Exception as e:
        logger.error(f"Email failed to {to_email}: {e}")
        _log_email_send(to_email, subject, False, str(e), actor, action_type, ip)
        log_event(
            actor=actor,
            action_type="EMAIL_FAILED",
            model_name="Email",
            object_id=0,
            description=f"Failed to send email to {to_email} with subject '{subject}'. Error: {e}",
        )
        return False


def _log_email_send(to_email, subject, success, error_message, actor, action_type, ip):
    """Write an immutable entry to EmailSendLog."""
    try:
        from .models import EmailSendLog
        EmailSendLog.objects.create(
            recipient_email=to_email,
            subject=subject,
            success=success,
            error_message=error_message,
            action_type=action_type,
            actor=actor,
            ip_address=ip,
        )
    except Exception:
        logger.warning("Failed to write EmailSendLog for %s", to_email)

def dispatch_notification(user, title, message, link=None, html_body=None, actor=None, external_email=None, phone=None, category=None, action_type=""):
    """
    Creates an in-app notification (if user provided) and attempts to dispatch an email
    to the user or to the provided external_email. Falls back to SMS if no email is
    available but a phone number is provided.

    Tracks delivery times against SLA thresholds (60s in-app, 5min email).
    Logs warnings when thresholds are breached.

    Args:
        category: NotificationCategory value. Defaults to BROADCAST (not SYSTEM)
                 to prevent accidental classification of dispatched notifications.
        action_type: Label for EmailSendLog (e.g. PARENT_PORTAL_ACCOUNT, BROADCAST).

    Returns the Notification instance (or None if no user was provided).
    """
    from .models import NotificationCategory
    notif = None
    dispatch_start = time.monotonic()
    try:
        # 1. Create in-app Notification if user exists
        if user:
            notif = Notification.objects.create(
                recipient=user,
                category=category or NotificationCategory.BROADCAST,
                title=title,
                body=message,
                link=link or ""
            )
            in_app_elapsed = round(time.monotonic() - dispatch_start, 2)
            if in_app_elapsed > SLA_IN_APP_SECONDS:
                logger.warning(
                    "SLA BREACH: In-app notification took %.2fs (threshold: %ds) for '%s'",
                    in_app_elapsed, SLA_IN_APP_SECONDS, title
                )
                _alert_sla_breach("in_app", in_app_elapsed, title, user)

        # 2. Fire email if user has an email or external_email is provided
        to_email = external_email or (user.email if user and user.email else None)

        if to_email:
            email_start = time.monotonic()
            site_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
            if link:
                email_body = f"{message}\n\nView details: {site_url}{link}"
            else:
                email_body = message
            send_email_safe(
                to_email=to_email,
                subject=title,
                body=email_body,
                html_body=html_body or email_body,
                actor=actor,
                action_type=action_type,
            )
            email_elapsed = round(time.monotonic() - email_start, 2)
            if email_elapsed > SLA_EMAIL_SECONDS:
                logger.warning(
                    "SLA BREACH: Email delivery took %.2fs (threshold: %ds) for '%s' to %s",
                    email_elapsed, SLA_EMAIL_SECONDS, title, to_email
                )
                _alert_sla_breach("email", email_elapsed, title, user)
        elif phone:
            # 3. SMS fallback when no email is available
            _send_sms_notification(phone, message)
    except Exception as e:
        logger.error(f"dispatch_notification failed (title='{title}'): {e}")

    return notif


def _alert_sla_breach(channel, elapsed, title, user):
    """Create an in-app alert for SLA breach, visible to Super Admin."""
    from .models import NotificationCategory
    try:
        from users.models import User, UserRole
        admins = User.objects.filter(role=UserRole.SUPER_ADMIN, is_active=True)
        for admin in admins:
            Notification.objects.create(
                recipient=admin,
                category=NotificationCategory.SYSTEM,
                title=f"SLA breach: {channel} notification",
                body=(
                    f"Notification '{title}' to {user} exceeded the {channel} SLA. "
                    f"Delivery took {elapsed}s."
                ),
                link="",
            )
    except Exception:
        pass


def _send_sms_notification(phone, message):
    """Send an SMS notification via the configured provider."""
    try:
        from attendance.notification_service import NotificationService
        result = NotificationService.send_sms(phone, message)
        if result.get('success'):
            logger.info(f"SMS notification sent to {phone}")
        else:
            logger.warning(f"SMS notification failed to {phone}: {result.get('error')}")
    except Exception as e:
        logger.error(f"_send_sms_notification failed to {phone}: {e}")


def send_parent_notification(guardian, title, message, link=None, actor=None, html_body=None, action_type=""):
    """
    Dispatches a notification to a parent/guardian through all available channels:

    - In-app notification (if guardian has a user account)
    - Email (if guardian has an email address)
    - SMS (if guardian has a phone number)

    This is a convenience wrapper around dispatch_notification that extracts
    the guardian's contact details automatically.

    Args:
        guardian: ParentGuardian instance
        title: Notification title/subject
        message: Notification body
        link: Optional deep-link URL
        actor: User performing the action (for audit logging)
        html_body: Optional HTML version of the email
        action_type: Label for EmailSendLog (e.g. PARENT_PORTAL_ACCOUNT)

    Returns:
        The Notification instance if created, else None
    """
    return dispatch_notification(
        user=guardian.user if guardian.user_id else None,
        title=title,
        message=message,
        link=link,
        html_body=html_body,
        actor=actor,
        external_email=guardian.email if guardian.email else None,
        phone=guardian.phone if guardian.phone else None,
        action_type=action_type,
    )
