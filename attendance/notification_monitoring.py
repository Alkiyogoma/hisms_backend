"""
Notification Monitoring and Alerting System

Provides comprehensive monitoring, alerting, and analytics for the notification system.
Tracks SMS delivery rates, email delivery, costs, and system health.
"""

from django.utils import timezone
from django.db.models import Count, Q, F
from django.core.mail import send_mail
from django.conf import settings
from datetime import timedelta, datetime
import logging
from typing import Dict, List, Tuple
from decimal import Decimal

from .models import Message, NotificationLog, OtpCode

logger = logging.getLogger(__name__)


class NotificationMetrics:
    """
    Collects and calculates notification system metrics for monitoring and alerting.
    """

    # SMS cost estimation (in USD per message)
    SMS_COST_PER_MESSAGE = Decimal('0.05')
    
    # SLA thresholds
    ALERT_THRESHOLD_SMS_DELIVERY_RATE = 0.95  # 95% delivery rate
    ALERT_THRESHOLD_EMAIL_DELIVERY_RATE = 0.90  # 90% delivery rate
    ALERT_THRESHOLD_FAILED_MESSAGES = 10  # Alert if 10+ messages failed

    @staticmethod
    def get_daily_metrics(date=None) -> Dict:
        """
        Get comprehensive metrics for a specific day.
        
        Args:
            date: Date to get metrics for (defaults to today)
            
        Returns:
            Dictionary with all notification metrics
        """
        if date is None:
            date = timezone.now().date()

        start_time = timezone.make_aware(datetime.combine(date, datetime.min.time()))
        end_time = timezone.make_aware(datetime.combine(date, datetime.max.time()))

        # SMS Metrics
        sms_metrics = NotificationMetrics._get_sms_metrics(start_time, end_time)
        
        # Email Metrics
        email_metrics = NotificationMetrics._get_email_metrics(start_time, end_time)
        
        # OTP Metrics
        otp_metrics = NotificationMetrics._get_otp_metrics(start_time, end_time)
        
        # Cost Metrics
        cost_metrics = NotificationMetrics._calculate_cost_metrics(sms_metrics, email_metrics)
        
        return {
            'date': str(date),
            'sms': sms_metrics,
            'email': email_metrics,
            'otp': otp_metrics,
            'cost': cost_metrics,
            'timestamp': timezone.now().isoformat()
        }

    @staticmethod
    def _get_sms_metrics(start_time, end_time) -> Dict:
        """Calculate SMS-specific metrics"""
        total_sms = Message.objects.filter(
            created_at__range=(start_time, end_time)
        ).count()
        
        sent_sms = Message.objects.filter(
            created_at__range=(start_time, end_time),
            status=1  # Sent
        ).count()
        
        failed_sms = Message.objects.filter(
            created_at__range=(start_time, end_time),
            status=2  # Failed
        ).count()
        
        pending_sms = Message.objects.filter(
            created_at__range=(start_time, end_time),
            status=0  # Pending
        ).count()
        
        retry_sms = Message.objects.filter(
            created_at__range=(start_time, end_time),
            status=3  # Retry
        ).count()
        
        delivery_rate = (sent_sms / total_sms * 100) if total_sms > 0 else 0

        return {
            'total': total_sms,
            'sent': sent_sms,
            'failed': failed_sms,
            'pending': pending_sms,
            'retry': retry_sms,
            'delivery_rate': round(delivery_rate, 2),
            'average_retry_count': NotificationMetrics._calculate_average_retries(start_time, end_time, 'sms')
        }

    @staticmethod
    def _get_email_metrics(start_time, end_time) -> Dict:
        """Calculate email-specific metrics"""
        total_emails = NotificationLog.objects.filter(
            created_at__range=(start_time, end_time),
            recipient_email__isnull=False
        ).exclude(recipient_email='').count()
        
        sent_emails = NotificationLog.objects.filter(
            created_at__range=(start_time, end_time),
            recipient_email__isnull=False,
            delivery_status='sent'
        ).exclude(recipient_email='').count()
        
        failed_emails = NotificationLog.objects.filter(
            created_at__range=(start_time, end_time),
            recipient_email__isnull=False,
            delivery_status='failed'
        ).exclude(recipient_email='').count()
        
        pending_emails = NotificationLog.objects.filter(
            created_at__range=(start_time, end_time),
            recipient_email__isnull=False,
            delivery_status='pending'
        ).exclude(recipient_email='').count()
        
        delivery_rate = (sent_emails / total_emails * 100) if total_emails > 0 else 0

        return {
            'total': total_emails,
            'sent': sent_emails,
            'failed': failed_emails,
            'pending': pending_emails,
            'delivery_rate': round(delivery_rate, 2),
            'average_retry_count': NotificationMetrics._calculate_average_retries(start_time, end_time, 'email')
        }

    @staticmethod
    def _get_otp_metrics(start_time, end_time) -> Dict:
        """Calculate OTP-specific metrics"""
        total_otps = OtpCode.objects.filter(
            created_at__range=(start_time, end_time)
        ).count()
        
        verified_otps = OtpCode.objects.filter(
            created_at__range=(start_time, end_time),
            verified=True
        ).count()
        
        expired_otps = OtpCode.objects.filter(
            created_at__range=(start_time, end_time),
            expires_at__lt=timezone.now(),
            verified=False
        ).count()
        
        verification_rate = (verified_otps / total_otps * 100) if total_otps > 0 else 0

        return {
            'total_generated': total_otps,
            'verified': verified_otps,
            'expired': expired_otps,
            'verification_rate': round(verification_rate, 2)
        }

    @staticmethod
    def _calculate_cost_metrics(sms_metrics, email_metrics) -> Dict:
        """Calculate cost-related metrics"""
        sms_cost = sms_metrics['sent'] * NotificationMetrics.SMS_COST_PER_MESSAGE
        
        return {
            'sms_sent': sms_metrics['sent'],
            'sms_cost_usd': str(sms_cost),
            'estimated_monthly_cost_usd': str(sms_cost * 30),
            'email_sent': email_metrics['sent'],
            'email_cost_usd': '0.00'  # Email is typically free or included
        }

    @staticmethod
    def _calculate_average_retries(start_time, end_time, message_type: str) -> float:
        """Calculate average retry count for a message type"""
        if message_type == 'sms':
            items = Message.objects.filter(created_at__range=(start_time, end_time))
            retry_counts = [item.retry_count for item in items]
        else:  # email
            items = NotificationLog.objects.filter(
                created_at__range=(start_time, end_time),
                recipient_email__isnull=False
            ).exclude(recipient_email='')
            retry_counts = [item.retry_count for item in items]
        
        if not retry_counts:
            return 0.0
        
        return round(sum(retry_counts) / len(retry_counts), 2)

    @staticmethod
    def get_weekly_metrics(end_date=None) -> Dict:
        """Get metrics for the past 7 days"""
        if end_date is None:
            end_date = timezone.now().date()

        start_date = end_date - timedelta(days=7)
        metrics_list = []

        current_date = start_date
        while current_date <= end_date:
            daily_metrics = NotificationMetrics.get_daily_metrics(current_date)
            metrics_list.append(daily_metrics)
            current_date += timedelta(days=1)

        # Calculate weekly aggregates
        total_sms = sum(m['sms']['total'] for m in metrics_list)
        total_emails = sum(m['email']['total'] for m in metrics_list)
        avg_sms_delivery = sum(m['sms']['delivery_rate'] for m in metrics_list) / 7
        avg_email_delivery = sum(m['email']['delivery_rate'] for m in metrics_list) / 7

        return {
            'period': f"{start_date} to {end_date}",
            'days': metrics_list,
            'aggregate': {
                'total_sms': total_sms,
                'total_emails': total_emails,
                'average_sms_delivery_rate': round(avg_sms_delivery, 2),
                'average_email_delivery_rate': round(avg_email_delivery, 2),
                'total_cost_usd': str(Decimal(total_sms) * NotificationMetrics.SMS_COST_PER_MESSAGE)
            }
        }

    @staticmethod
    def get_notification_health_status() -> Dict:
        """Get current health status of notification system"""
        now = timezone.now()
        last_24_hours = now - timedelta(hours=24)

        # Get metrics for last 24 hours
        sms_last_24h = Message.objects.filter(created_at__gte=last_24_hours)
        email_last_24h = NotificationLog.objects.filter(
            created_at__gte=last_24_hours,
            recipient_email__isnull=False
        ).exclude(recipient_email='')

        # Calculate delivery rates
        sms_total = sms_last_24h.count()
        sms_sent = sms_last_24h.filter(status=1).count()
        sms_rate = (sms_sent / sms_total * 100) if sms_total > 0 else 0

        email_total = email_last_24h.count()
        email_sent = email_last_24h.filter(delivery_status='sent').count()
        email_rate = (email_sent / email_total * 100) if email_total > 0 else 0

        # Check for alerts
        alerts = []
        if sms_rate < NotificationMetrics.ALERT_THRESHOLD_SMS_DELIVERY_RATE * 100:
            alerts.append(f"SMS delivery rate below {NotificationMetrics.ALERT_THRESHOLD_SMS_DELIVERY_RATE * 100}%: {sms_rate:.2f}%")

        if email_rate < NotificationMetrics.ALERT_THRESHOLD_EMAIL_DELIVERY_RATE * 100:
            alerts.append(f"Email delivery rate below {NotificationMetrics.ALERT_THRESHOLD_EMAIL_DELIVERY_RATE * 100}%: {email_rate:.2f}%")

        failed_count = sms_last_24h.filter(status=2).count() + email_last_24h.filter(delivery_status='failed').count()
        if failed_count > NotificationMetrics.ALERT_THRESHOLD_FAILED_MESSAGES:
            alerts.append(f"High number of failed notifications in last 24h: {failed_count}")

        pending_count = sms_last_24h.filter(status=0).count() + email_last_24h.filter(delivery_status='pending').count()
        if pending_count > 100:
            alerts.append(f"High number of pending notifications: {pending_count}")

        # Determine overall health
        if alerts:
            health = 'warning'
        else:
            health = 'healthy'

        return {
            'status': health,
            'timestamp': now.isoformat(),
            'last_24h': {
                'sms': {
                    'sent': sms_sent,
                    'total': sms_total,
                    'delivery_rate': round(sms_rate, 2)
                },
                'email': {
                    'sent': email_sent,
                    'total': email_total,
                    'delivery_rate': round(email_rate, 2)
                },
                'failed_total': failed_count,
                'pending_total': pending_count
            },
            'alerts': alerts
        }


class NotificationAlerting:
    """
    Handles alerting when notification system issues are detected.
    """

    @staticmethod
    def check_and_alert():
        """Check health and send alerts if issues detected"""
        health = NotificationMetrics.get_notification_health_status()
        
        if health['status'] == 'warning' and health['alerts']:
            NotificationAlerting.send_admin_alert(health)
            logger.warning(f"Notification system alerts: {health['alerts']}")

    @staticmethod
    def send_admin_alert(health_status: Dict):
        """Send alert email to admin about notification issues"""
        try:
            alert_message = "Notification System Alert\n\n"
            alert_message += f"Status: {health_status['status'].upper()}\n"
            alert_message += f"Timestamp: {health_status['timestamp']}\n\n"
            
            alert_message += "Last 24h Metrics:\n"
            alert_message += f"  SMS: {health_status['last_24h']['sms']['sent']}/{health_status['last_24h']['sms']['total']} sent "
            alert_message += f"({health_status['last_24h']['sms']['delivery_rate']}%)\n"
            alert_message += f"  Email: {health_status['last_24h']['email']['sent']}/{health_status['last_24h']['email']['total']} sent "
            alert_message += f"({health_status['last_24h']['email']['delivery_rate']}%)\n"
            alert_message += f"  Failed: {health_status['last_24h']['failed_total']}\n"
            alert_message += f"  Pending: {health_status['last_24h']['pending_total']}\n\n"
            
            alert_message += "Alerts:\n"
            for alert in health_status['alerts']:
                alert_message += f"  • {alert}\n"

            # Send to admin email
            admin_email = getattr(settings, 'ADMIN_EMAIL', 'admin@hodari.ac.tz')
            
            send_mail(
                subject='[ALERT] Hodari Notification System Issues',
                message=alert_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[admin_email],
                fail_silently=True
            )
            
            logger.info(f"Admin alert sent to {admin_email}")
            
        except Exception as e:
            logger.error(f"Error sending admin alert: {str(e)}")

    @staticmethod
    def check_sms_provider_status():
        """Check if SMS provider is responsive"""
        # This would be called periodically to verify provider connectivity
        try:
            # Try to get recent SMS that were sent successfully
            recent_success = Message.objects.filter(
                status=1,
                created_at__gte=timezone.now() - timedelta(hours=1)
            ).exists()
            
            if not recent_success:
                logger.warning("No successful SMS deliveries in last hour - provider may be down")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error checking SMS provider status: {str(e)}")
            return False


class NotificationDashboardData:
    """
    Provides data for notification dashboard/reporting.
    """

    @staticmethod
    def get_realtime_dashboard_data() -> Dict:
        """Get real-time dashboard data for admin interface"""
        now = timezone.now()
        today = now.date()
        
        # Today's metrics
        today_start = timezone.make_aware(datetime.combine(today, datetime.min.time()))
        today_end = timezone.make_aware(datetime.combine(today, datetime.max.time()))
        
        today_sms = Message.objects.filter(created_at__range=(today_start, today_end))
        today_emails = NotificationLog.objects.filter(
            created_at__range=(today_start, today_end),
            recipient_email__isnull=False
        ).exclude(recipient_email='')
        
        # Current queue status
        pending_sms = Message.objects.filter(status=0).count()
        pending_emails = NotificationLog.objects.filter(delivery_status='pending', recipient_email__isnull=False).exclude(recipient_email='').count()

        return {
            'timestamp': now.isoformat(),
            'today': {
                'sms': {
                    'sent': today_sms.filter(status=1).count(),
                    'failed': today_sms.filter(status=2).count(),
                    'total': today_sms.count()
                },
                'email': {
                    'sent': today_emails.filter(delivery_status='sent').count(),
                    'failed': today_emails.filter(delivery_status='failed').count(),
                    'total': today_emails.count()
                }
            },
            'queue': {
                'pending_sms': pending_sms,
                'pending_emails': pending_emails,
                'total_pending': pending_sms + pending_emails
            },
            'health': NotificationMetrics.get_notification_health_status()
        }

    @staticmethod
    def get_delivery_report(start_date, end_date) -> Dict:
        """Get detailed delivery report for a date range"""
        start_time = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
        end_time = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))

        sms_data = Message.objects.filter(created_at__range=(start_time, end_time))
        email_data = NotificationLog.objects.filter(
            created_at__range=(start_time, end_time),
            recipient_email__isnull=False
        ).exclude(recipient_email='')

        # SMS breakdown by status
        sms_by_status = {
            'sent': sms_data.filter(status=1).count(),
            'failed': sms_data.filter(status=2).count(),
            'pending': sms_data.filter(status=0).count(),
            'retry': sms_data.filter(status=3).count(),
        }

        # Email breakdown by status
        email_by_status = {
            'sent': email_data.filter(delivery_status='sent').count(),
            'failed': email_data.filter(delivery_status='failed').count(),
            'pending': email_data.filter(delivery_status='pending').count(),
            'retry': email_data.filter(delivery_status='retry').count(),
        }

        # Notification type breakdown
        sms_by_type = {}
        for notification_type in ['checkin', 'checkout', 'otp', 'absence']:
            count = email_data.filter(notification_type=notification_type).count()
            if count > 0:
                sms_by_type[notification_type] = count

        return {
            'date_range': f"{start_date} to {end_date}",
            'sms': {
                'by_status': sms_by_status,
                'total': sms_data.count(),
                'delivery_rate': (sms_by_status['sent'] / sms_data.count() * 100) if sms_data.count() > 0 else 0
            },
            'email': {
                'by_status': email_by_status,
                'total': email_data.count(),
                'delivery_rate': (email_by_status['sent'] / email_data.count() * 100) if email_data.count() > 0 else 0
            },
            'by_type': sms_by_type,
            'cost_estimate': {
                'sms_cost_usd': str(Decimal(sms_by_status['sent']) * NotificationMetrics.SMS_COST_PER_MESSAGE),
                'email_cost_usd': '0.00'
            }
        }

