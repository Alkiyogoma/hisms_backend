"""
Notification Service for Attendance Events

Handles SMS and email notifications for check-in/check-out events with:
- Reliable delivery with retry logic
- Support for multiple parent contacts
- Integration with real-time event system
- Comprehensive logging and monitoring
- SMS provider integration (CloudService API and Hodari SMS API)
"""

from django.core.mail import send_mail
from django.utils import timezone
from django.conf import settings
from datetime import timedelta
import logging
from typing import List, Dict, Optional
import requests
import json

from .models import Message, NotificationLog, OtpCode, AttendanceEntry
from students.models import Student, ParentGuardian

logger = logging.getLogger(__name__)


class SMSProvider:
    """Base class for SMS providers"""
    
    def send(self, phone: str, message: str) -> Dict:
        """Send SMS message. Must be implemented by subclasses."""
        raise NotImplementedError


class CloudServiceSMSProvider(SMSProvider):
    """SMS provider for CloudService API"""
    
    def __init__(self):
        self.api_key = settings.CLOUDSERVICE_API_KEY
        self.api_url = settings.CLOUDSERVICE_API_URL
        self.sender_id = settings.CLOUDSERVICE_SENDER_ID
    
    def send(self, phone: str, message: str) -> Dict:
        """
        Send SMS via CloudService API.
        
        Args:
            phone: Recipient phone number
            message: Message text
            
        Returns:
            Dictionary with success status and message ID
        """
        try:
            if not self.api_key:
                logger.warning("CloudService API key not configured")
                return {
                    'success': False,
                    'error': 'API key not configured',
                    'provider': 'cloudservice'
                }
            
            payload = {
                'api_key': self.api_key,
                'phone': phone,
                'message': message,
                'sender_id': self.sender_id
            }
            
            response = requests.post(
                self.api_url,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    'success': True,
                    'message_id': data.get('message_id', ''),
                    'provider': 'cloudservice'
                }
            else:
                logger.error(f"CloudService API error: {response.status_code} - {response.text}")
                return {
                    'success': False,
                    'error': f"API error: {response.status_code}",
                    'provider': 'cloudservice'
                }
                
        except requests.exceptions.RequestException as e:
            logger.error(f"CloudService API request failed: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'provider': 'cloudservice'
            }
        except Exception as e:
            logger.error(f"Unexpected error in CloudService provider: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'provider': 'cloudservice'
            }


class HodariSMSProvider(SMSProvider):
    """SMS provider for Hodari SMS API"""
    
    def __init__(self):
        self.api_key = settings.HODARI_SMS_API_KEY
        self.api_url = settings.HODARI_SMS_API_URL
        self.sender_id = settings.HODARI_SMS_SENDER_ID
    
    def send(self, phone: str, message: str) -> Dict:
        """
        Send SMS via Hodari SMS API.
        
        Args:
            phone: Recipient phone number
            message: Message text
            
        Returns:
            Dictionary with success status and message ID
        """
        try:
            if not self.api_key:
                logger.warning("Hodari SMS API key not configured")
                return {
                    'success': False,
                    'error': 'API key not configured',
                    'provider': 'hodari'
                }
            
            payload = {
                'api_key': self.api_key,
                'phone': phone,
                'message': message,
                'sender_id': self.sender_id
            }
            
            response = requests.post(
                self.api_url,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    'success': True,
                    'message_id': data.get('message_id', ''),
                    'provider': 'hodari'
                }
            else:
                logger.error(f"Hodari SMS API error: {response.status_code} - {response.text}")
                return {
                    'success': False,
                    'error': f"API error: {response.status_code}",
                    'provider': 'hodari'
                }
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Hodari SMS API request failed: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'provider': 'hodari'
            }
        except Exception as e:
            logger.error(f"Unexpected error in Hodari SMS provider: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'provider': 'hodari'
            }


class NotificationService:
    """
    Service for sending attendance notifications via SMS and email.
    Integrates with Celery for background task processing.
    Supports multiple SMS providers (CloudService and Hodari APIs).
    """
    
    # SMS Configuration
    SMS_MAX_RETRIES = 3
    SMS_RETRY_DELAY = 300  # 5 minutes
    
    # Email Configuration
    EMAIL_FROM = settings.DEFAULT_FROM_EMAIL if hasattr(settings, 'DEFAULT_FROM_EMAIL') else 'noreply@hodari.ac.tz'
    EMAIL_MAX_RETRIES = 3
    EMAIL_RETRY_DELAY = 600  # 10 minutes
    
    # SMS Provider selection (can be 'cloudservice' or 'hodari')
    SMS_PROVIDER = getattr(settings, 'SMS_PROVIDER', 'cloudservice')
    
    # Notification Templates
    CHECKIN_SMS_TEMPLATE = "Your child {student_name} has been checked in at {timestamp} to Hodari Christian School."
    CHECKOUT_SMS_TEMPLATE = "Your child {student_name} has been picked up{pickup_info} on {timestamp} from Hodari Christian School."
    OTP_SMS_TEMPLATE = "Your OTP code for Hodari Christian School is: {code}. Valid for 10 minutes."
    
    CHECKIN_EMAIL_SUBJECT = "Check-in Notification - Hodari Christian School"
    CHECKOUT_EMAIL_SUBJECT = "Check-out Notification - Hodari Christian School"
    OTP_EMAIL_SUBJECT = "OTP Code - Hodari Christian School"
    
    CHECKIN_EMAIL_BODY = """
Dear {parent_name},

Your child {student_name} has been checked in at {timestamp} to Hodari Christian School.

Best regards,
Hodari Christian School
"""
    
    CHECKOUT_EMAIL_BODY = """
Dear {parent_name},

Your child {student_name} has been picked up{pickup_info} on {timestamp} from Hodari Christian School.

Best regards,
Hodari Christian School
"""
    
    OTP_EMAIL_BODY = """
Dear {parent_name},

Your OTP code for Hodari Christian School is: {code}

This code is valid for 10 minutes. Please do not share this code with anyone.

Best regards,
Hodari Christian School
"""
    
    @staticmethod
    def _get_sms_provider() -> SMSProvider:
        """Get configured SMS provider instance"""
        provider_name = NotificationService.SMS_PROVIDER.lower()
        
        if provider_name == 'hodari':
            return HodariSMSProvider()
        else:
            return CloudServiceSMSProvider()
    
    @staticmethod
    def send_sms(phone: str, message: str) -> Dict:
        """
        Send SMS via configured provider.
        
        Args:
            phone: Recipient phone number
            message: Message text
            
        Returns:
            Dictionary with success status and message ID
        """
        try:
            provider = NotificationService._get_sms_provider()
            result = provider.send(phone, message)
            
            if result['success']:
                logger.info(f"SMS sent successfully to {phone} via {result.get('provider', 'unknown')}")
            else:
                logger.error(f"SMS send failed to {phone}: {result.get('error', 'Unknown error')}")
            
            return result
            
        except Exception as e:
            logger.error(f"Error sending SMS to {phone}: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'provider': 'unknown'
            }
    
    @staticmethod
    def send_checkin_notification(student: Student, timestamp: timezone.datetime) -> Dict[str, int]:
        """
        Send check-in notifications to all student's parents via SMS and email.
        
        Args:
            student: Student instance
            timestamp: Check-in timestamp
            
        Returns:
            Dictionary with counts of SMS and email notifications sent
        """
        try:
            # Get today's attendance entry
            attendance_entry = AttendanceEntry.objects.filter(
                student=student,
                date=timezone.now().date()
            ).first()
            
            if not attendance_entry:
                logger.warning(f"No attendance entry found for student {student.id} on {timezone.now().date()}")
                return {'sms': 0, 'email': 0}
            
            # Get all parent contacts
            parents = NotificationService._get_parent_contacts(student)
            
            if not parents:
                logger.warning(f"No parent contacts found for student {student.id}")
                return {'sms': 0, 'email': 0}
            
            sms_count = 0
            email_count = 0
            
            # Send notifications to each parent
            for parent in parents:
                # Send SMS
                if parent.get('phone'):
                    sms_count += NotificationService._queue_sms(
                        phone=parent['phone'],
                        message=NotificationService.CHECKIN_SMS_TEMPLATE.format(
                            student_name=student.get_full_name(),
                            timestamp=timestamp.strftime('%Y-%m-%d %H:%M:%S')
                        ),
                        attendance_entry=attendance_entry,
                        notification_type='checkin'
                    )
                
                # Send Email
                if parent.get('email'):
                    # Try dynamic DB template first
                    from core.email_templates import send_dynamic_email
                    from core.models import SchoolSettings
                    school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"
                    tpl_context = {
                        "guardian_name": parent.get('name', 'Parent'),
                        "student_name": student.get_full_name(),
                        "checkin_time": timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                        "school_name": school_name,
                    }
                    db_sent = send_dynamic_email(
                        template_type="checkin_notification",
                        to_email=parent['email'],
                        context=tpl_context,
                    )
                    if not db_sent:
                        email_count += NotificationService._queue_email(
                            email=parent['email'],
                            subject=NotificationService.CHECKIN_EMAIL_SUBJECT,
                            body=NotificationService.CHECKIN_EMAIL_BODY.format(
                                parent_name=parent.get('name', 'Parent'),
                                student_name=student.get_full_name(),
                                timestamp=timestamp.strftime('%Y-%m-%d %H:%M:%S')
                            ),
                            attendance_entry=attendance_entry,
                            notification_type='checkin'
                        )
            
            logger.info(f"Check-in notifications queued for student {student.id}: {sms_count} SMS, {email_count} emails")
            return {'sms': sms_count, 'email': email_count}
            
        except Exception as e:
            logger.error(f"Error sending check-in notification for student {student.id}: {str(e)}")
            return {'sms': 0, 'email': 0}
    
    @staticmethod
    def send_checkout_notification(
        student: Student, 
        timestamp: timezone.datetime, 
        parent_name: str = ""
    ) -> Dict[str, int]:
        """
        Send check-out notifications to all student's parents via SMS and email.
        
        Args:
            student: Student instance
            timestamp: Check-out timestamp
            parent_name: Name of person picking up student (optional)
            
        Returns:
            Dictionary with counts of SMS and email notifications sent
        """
        try:
            # Get today's attendance entry
            attendance_entry = AttendanceEntry.objects.filter(
                student=student,
                date=timezone.now().date()
            ).first()
            
            if not attendance_entry:
                logger.warning(f"No attendance entry found for student {student.id} on {timezone.now().date()}")
                return {'sms': 0, 'email': 0}
            
            # Get all parent contacts
            parents = NotificationService._get_parent_contacts(student)
            
            if not parents:
                logger.warning(f"No parent contacts found for student {student.id}")
                return {'sms': 0, 'email': 0}
            
            sms_count = 0
            email_count = 0
            
            # Format pickup info
            pickup_info = f" by {parent_name}" if parent_name else ""
            
            # Send notifications to each parent
            for parent in parents:
                # Send SMS
                if parent.get('phone'):
                    sms_count += NotificationService._queue_sms(
                        phone=parent['phone'],
                        message=NotificationService.CHECKOUT_SMS_TEMPLATE.format(
                            student_name=student.get_full_name(),
                            pickup_info=pickup_info,
                            timestamp=timestamp.strftime('%Y-%m-%d %H:%M:%S')
                        ),
                        attendance_entry=attendance_entry,
                        notification_type='checkout'
                    )
                
                # Send Email
                if parent.get('email'):
                    # Try dynamic DB template first
                    from core.email_templates import send_dynamic_email
                    from core.models import SchoolSettings
                    school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"
                    tpl_context = {
                        "guardian_name": parent.get('name', 'Parent'),
                        "student_name": student.get_full_name(),
                        "checkout_time": timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                        "pickup_info": pickup_info,
                        "school_name": school_name,
                    }
                    db_sent = send_dynamic_email(
                        template_type="checkout_notification",
                        to_email=parent['email'],
                        context=tpl_context,
                    )
                    if not db_sent:
                        email_count += NotificationService._queue_email(
                            email=parent['email'],
                            subject=NotificationService.CHECKOUT_EMAIL_SUBJECT,
                            body=NotificationService.CHECKOUT_EMAIL_BODY.format(
                                parent_name=parent.get('name', 'Parent'),
                                student_name=student.get_full_name(),
                                pickup_info=pickup_info,
                                timestamp=timestamp.strftime('%Y-%m-%d %H:%M:%S')
                            ),
                            attendance_entry=attendance_entry,
                            notification_type='checkout'
                        )
            
            logger.info(f"Check-out notifications queued for student {student.id}: {sms_count} SMS, {email_count} emails")
            return {'sms': sms_count, 'email': email_count}
            
        except Exception as e:
            logger.error(f"Error sending check-out notification for student {student.id}: {str(e)}")
            return {'sms': 0, 'email': 0}
    
    @staticmethod
    def _get_parent_contacts(student: Student) -> List[Dict[str, str]]:
        """
        Get all parent contacts for a student from both Django and Laravel systems.
        
        Args:
            student: Student instance
            
        Returns:
            List of parent contact dictionaries with phone, email, and name
        """
        parents = []
        
        # Get from Django guardian system
        for guardian_rel in student.guardians.all():
            if guardian_rel.phone or guardian_rel.email:
                parents.append({
                    'phone': guardian_rel.phone or '',
                    'email': guardian_rel.email or '',
                    'name': guardian_rel.full_name
                })
        
        # Get from Laravel parent system if available
        if hasattr(student, 'laravel_parents'):
            for parent_rel in student.laravel_parents.all():
                if parent_rel.parent.phone or parent_rel.parent.email:
                    parents.append({
                        'phone': parent_rel.parent.phone or '',
                        'email': parent_rel.parent.email or '',
                        'name': parent_rel.parent.full_name
                    })
        
        # Remove duplicates based on phone number
        seen_phones = set()
        unique_parents = []
        for parent in parents:
            phone = parent['phone'].strip()
            if phone and phone not in seen_phones:
                seen_phones.add(phone)
                unique_parents.append(parent)
            elif not phone and parent['email']:
                # Include email-only contacts
                unique_parents.append(parent)
        
        return unique_parents
    
    @staticmethod
    def _queue_sms(
        phone: str,
        message: str,
        attendance_entry: AttendanceEntry,
        notification_type: str
    ) -> int:
        """
        Queue an SMS message for delivery.
        
        Args:
            phone: Recipient phone number
            message: Message text
            attendance_entry: Related attendance entry
            notification_type: Type of notification (checkin, checkout, otp, etc.)
            
        Returns:
            1 if successfully queued, 0 otherwise
        """
        try:
            # Create Message record for SMS queue
            msg_record = Message.objects.create(
                phone=phone,
                message=message,
                status=0  # Pending
            )
            
            # Log notification attempt
            NotificationLog.objects.create(
                attendance_entry=attendance_entry,
                recipient_phone=phone,
                notification_type=notification_type,
                message=message,
                delivery_status='pending'
            )
            
            logger.info(f"SMS queued to {phone} for {notification_type}")
            return 1
            
        except Exception as e:
            logger.error(f"Error queuing SMS to {phone}: {str(e)}")
            return 0
    
    @staticmethod
    def _queue_email(
        email: str,
        subject: str,
        body: str,
        attendance_entry: AttendanceEntry,
        notification_type: str
    ) -> int:
        """
        Queue an email notification for delivery.
        
        Args:
            email: Recipient email address
            subject: Email subject
            body: Email body
            attendance_entry: Related attendance entry
            notification_type: Type of notification (checkin, checkout, etc.)
            
        Returns:
            1 if successfully queued, 0 otherwise
        """
        try:
            # Log notification attempt
            NotificationLog.objects.create(
                attendance_entry=attendance_entry,
                recipient_email=email,
                notification_type=notification_type,
                message=body,
                delivery_status='pending'
            )
            
            logger.info(f"Email queued to {email} for {notification_type}")
            return 1
            
        except Exception as e:
            logger.error(f"Error queuing email to {email}: {str(e)}")
            return 0
    
    @staticmethod
    def send_otp_notification(otp_code: OtpCode) -> bool:
        """
        Send OTP code via SMS to parent.
        
        Args:
            otp_code: OtpCode instance
            
        Returns:
            True if successfully queued, False otherwise
        """
        try:
            parent = otp_code.parent
            
            if not parent.phone:
                logger.warning(f"No phone number for parent {parent.id}")
                return False
            
            message = NotificationService.OTP_SMS_TEMPLATE.format(
                code=otp_code.code
            )
            
            # Queue SMS
            msg_record = Message.objects.create(
                phone=parent.phone,
                message=message,
                status=0  # Pending
            )
            
            # Log notification
            NotificationLog.objects.create(
                otp_code=otp_code,
                recipient_phone=parent.phone,
                notification_type='otp',
                message=message,
                delivery_status='pending'
            )
            
            logger.info(f"OTP notification queued for parent {parent.id}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending OTP notification: {str(e)}")
            return False
    
    @staticmethod
    def retry_failed_notifications():
        """
        Retry failed SMS and email notifications.
        Called periodically by Celery beat.
        """
        try:
            # Retry failed SMS messages
            failed_messages = Message.objects.filter(
                status=2,  # Failed
                retry_count__lt=NotificationService.SMS_MAX_RETRIES
            )
            
            for message in failed_messages:
                message.mark_retry()
                logger.info(f"Marked message {message.id} for retry")
            
            # Retry failed email notifications
            failed_emails = NotificationLog.objects.filter(
                delivery_status='failed',
                retry_count__lt=NotificationService.EMAIL_MAX_RETRIES,
                recipient_email__isnull=False
            ).exclude(recipient_email='')
            
            for notification in failed_emails:
                notification.delivery_status = 'retry'
                notification.save(update_fields=['delivery_status'])
                logger.info(f"Marked notification {notification.id} for retry")
            
            logger.info(f"Retry process completed: {failed_messages.count()} SMS, {failed_emails.count()} emails")
            
        except Exception as e:
            logger.error(f"Error in retry_failed_notifications: {str(e)}")
    
    @staticmethod
    def cleanup_expired_otps():
        """
        Clean up expired OTP codes.
        Called periodically by Celery beat.
        """
        try:
            expired_count = OtpCode.objects.filter(
                expires_at__lt=timezone.now(),
                verified=False
            ).delete()[0]
            
            logger.info(f"Cleaned up {expired_count} expired OTP codes")
            
        except Exception as e:
            logger.error(f"Error cleaning up expired OTPs: {str(e)}")
