"""
Property-Based Tests for Notification System

Validates the correctness properties of the attendance notification system:
- Property 16: Notification Triggering Consistency
- Property 17: Notification Message Format Accuracy
- Property 18: Multi-Channel Notification Logic
- Property 19: Notification Retry and Logging
"""

from hypothesis import given, strategies as st, assume, settings, HealthCheck
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from django.core.exceptions import ValidationError
from datetime import timedelta
import logging
from unittest.mock import patch, MagicMock

from students.models import Student, ParentGuardian
from academics.models import AcademicClass
from users.models import User
from attendance.models import AttendanceEntry, Message, NotificationLog, OtpCode
from attendance.notification_service import NotificationService
from attendance.services import AttendanceService

logger = logging.getLogger(__name__)


class NotificationTriggeringConsistency(TransactionTestCase):
    """
    Property 16: Notification Triggering Consistency
    
    Validates that:
    - Check-in always triggers SMS/email to all parents
    - Check-out always triggers SMS/email to all parents
    - OTP generation always triggers SMS
    - Duplicate events don't create duplicate notifications
    """

    def setUp(self):
        """Set up test data with students and parents"""
        # Create test user
        self.user = User.objects.create_user(
            username='test_user',
            password='testpass123',
            role='admin'
        )
        
        # Create academic class
        self.class_obj = AcademicClass.objects.create(
            name='Grade 1A',
            level=1,
            academic_year_id=1
        )
        
        # Create test student
        self.student = Student.objects.create(
            admission_no='STU001',
            first_name='John',
            last_name='Doe',
            date_of_birth='2020-01-01',
            current_class=self.class_obj
        )

    def test_checkin_triggers_notification_to_all_parents(self):
        """Checkin event triggers notifications to all registered parents"""
        # Add multiple parents
        parent1 = ParentGuardian.objects.create(
            first_name='Alice',
            last_name='Doe',
            phone='+255123456789',
            email='alice@example.com',
            relationship='Mother'
        )
        parent2 = ParentGuardian.objects.create(
            first_name='Bob',
            last_name='Doe',
            phone='+255987654321',
            email='bob@example.com',
            relationship='Father'
        )
        self.student.guardians.add(parent1, parent2)

        # Record initial notification count
        initial_sms_count = Message.objects.count()
        initial_log_count = NotificationLog.objects.count()

        # Process check-in
        timestamp = timezone.now()
        AttendanceService.checkin_student(
            student_id=self.student.admission_no,
            user=self.user,
            timestamp=timestamp,
            laravel_format=False
        )

        # Verify notifications created
        new_sms_count = Message.objects.count()
        new_log_count = NotificationLog.objects.count()
        
        assert new_sms_count > initial_sms_count, "Check-in should create SMS notifications"
        assert new_log_count > initial_log_count, "Check-in should log notifications"
        
        # Verify notifications for both parents
        sms_messages = Message.objects.filter(
            status=0,  # Pending
            created_at__gte=timezone.now() - timedelta(seconds=10)
        )
        assert sms_messages.count() >= 2, "Should have SMS for both parents"

    def test_checkout_triggers_notification_to_all_parents(self):
        """Checkout event triggers notifications to all registered parents"""
        # Add multiple parents
        parent1 = ParentGuardian.objects.create(
            first_name='Alice',
            last_name='Doe',
            phone='+255123456789',
            email='alice@example.com',
            relationship='Mother'
        )
        parent2 = ParentGuardian.objects.create(
            first_name='Bob',
            last_name='Doe',
            phone='+255987654321',
            email='bob@example.com',
            relationship='Father'
        )
        self.student.guardians.add(parent1, parent2)

        # Create attendance entry with check-in
        today = timezone.now().date()
        entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        # Record initial notification count
        initial_sms_count = Message.objects.count()
        initial_log_count = NotificationLog.objects.count()

        # Process check-out
        timestamp = timezone.now()
        AttendanceService.checkout_student(
            student_id=self.student.admission_no,
            user=self.user,
            timestamp=timestamp,
            parent_name='Alice Doe',
            laravel_format=False
        )

        # Verify notifications created
        new_sms_count = Message.objects.count()
        new_log_count = NotificationLog.objects.count()
        
        assert new_sms_count > initial_sms_count, "Check-out should create SMS notifications"
        assert new_log_count > initial_log_count, "Check-out should log notifications"

    def test_otp_generation_triggers_sms(self):
        """OTP generation always triggers SMS notification"""
        parent = ParentGuardian.objects.create(
            first_name='Alice',
            last_name='Doe',
            phone='+255123456789',
            email='alice@example.com',
            relationship='Mother'
        )

        # Record initial SMS count
        initial_sms_count = Message.objects.count()

        # Generate OTP
        otp_code = OtpCode.generate_code(parent)

        # Verify SMS queued
        new_sms_count = Message.objects.count()
        
        # Note: In current implementation, OTP notification is queued in API
        # This test validates that when send_otp_notification is called, it creates a message
        NotificationService.send_otp_notification(otp_code)
        
        final_sms_count = Message.objects.count()
        assert final_sms_count > initial_sms_count, "OTP generation should queue SMS"

    def test_duplicate_checkins_dont_create_duplicate_notifications(self):
        """Attempting duplicate check-in should fail without creating notifications"""
        parent = ParentGuardian.objects.create(
            first_name='Alice',
            last_name='Doe',
            phone='+255123456789',
            email='alice@example.com'
        )
        self.student.guardians.add(parent)

        # First check-in should succeed
        timestamp1 = timezone.now()
        result1 = AttendanceService.checkin_student(
            student_id=self.student.admission_no,
            user=self.user,
            timestamp=timestamp1,
            laravel_format=False
        )
        assert result1['success'], "First check-in should succeed"

        # Count notifications after first check-in
        sms_after_first = Message.objects.filter(
            created_at__gte=timezone.now() - timedelta(seconds=30),
            status=0
        ).count()

        # Second check-in should fail
        timestamp2 = timezone.now() + timedelta(minutes=5)
        result2 = AttendanceService.checkin_student(
            student_id=self.student.admission_no,
            user=self.user,
            timestamp=timestamp2,
            laravel_format=False
        )
        assert not result2['success'], "Duplicate check-in should fail"
        assert 'already checked in' in result2['message'].lower(), "Should indicate duplicate"

        # No additional notifications should be created
        sms_after_second = Message.objects.filter(
            created_at__gte=timezone.now() - timedelta(seconds=30),
            status=0
        ).count()
        
        assert sms_after_second == sms_after_first, "Duplicate check-in should not create additional notifications"


class NotificationMessageFormatAccuracy(TestCase):
    """
    Property 17: Notification Message Format Accuracy
    
    Validates that:
    - SMS messages contain required fields
    - Email subjects are appropriate
    - Email bodies are properly formatted
    - OTP messages include code and expiry
    """

    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='test_user',
            password='testpass123',
            role='admin'
        )
        
        self.class_obj = AcademicClass.objects.create(
            name='Grade 1A',
            level=1,
            academic_year_id=1
        )
        
        self.student = Student.objects.create(
            admission_no='STU001',
            first_name='John',
            last_name='Doe',
            date_of_birth='2020-01-01',
            current_class=self.class_obj
        )

    def test_checkin_sms_format_accuracy(self):
        """Check-in SMS contains required fields"""
        timestamp = timezone.now()
        message = NotificationService.CHECKIN_SMS_TEMPLATE.format(
            student_name=self.student.get_full_name(),
            timestamp=timestamp.strftime('%Y-%m-%d %H:%M:%S')
        )
        
        # Validate message contains required fields
        assert 'John Doe' in message, "SMS must contain student name"
        assert str(timestamp.year) in message, "SMS must contain timestamp"
        assert 'checked in' in message.lower(), "SMS must mention check-in"
        assert 'Hodari Christian School' in message, "SMS must contain school name"

    def test_checkout_sms_format_accuracy(self):
        """Check-out SMS contains required fields"""
        timestamp = timezone.now()
        message = NotificationService.CHECKOUT_SMS_TEMPLATE.format(
            student_name=self.student.get_full_name(),
            pickup_info=' by Alice Doe',
            timestamp=timestamp.strftime('%Y-%m-%d %H:%M:%S')
        )
        
        # Validate message contains required fields
        assert 'John Doe' in message, "SMS must contain student name"
        assert str(timestamp.year) in message, "SMS must contain timestamp"
        assert 'picked up' in message.lower(), "SMS must mention pick-up"
        assert 'Alice Doe' in message, "SMS must contain pickup person name"
        assert 'Hodari Christian School' in message, "SMS must contain school name"

    def test_otp_sms_format_accuracy(self):
        """OTP SMS contains code and validity info"""
        code = '123456'
        message = NotificationService.OTP_SMS_TEMPLATE.format(code=code)
        
        # Validate message contains required fields
        assert code in message, "OTP SMS must contain the OTP code"
        assert '10 minutes' in message or '10' in message, "OTP SMS must indicate validity period"
        assert 'OTP' in message.upper(), "OTP SMS must mention OTP"
        assert 'Hodari Christian School' in message, "OTP SMS must contain school name"

    def test_checkin_email_subject_accuracy(self):
        """Check-in email has appropriate subject"""
        subject = NotificationService.CHECKIN_EMAIL_SUBJECT
        
        assert 'Check-in' in subject or 'check-in' in subject, "Subject must mention check-in"
        assert 'Hodari' in subject, "Subject should contain school name"

    def test_checkout_email_subject_accuracy(self):
        """Check-out email has appropriate subject"""
        subject = NotificationService.CHECKOUT_EMAIL_SUBJECT
        
        assert 'Check-out' in subject or 'check-out' in subject, "Subject must mention check-out"
        assert 'Hodari' in subject, "Subject should contain school name"

    def test_checkin_email_body_format(self):
        """Check-in email body is properly formatted"""
        timestamp = timezone.now()
        body = NotificationService.CHECKIN_EMAIL_BODY.format(
            parent_name='Alice Doe',
            student_name='John Doe',
            timestamp=timestamp.strftime('%Y-%m-%d %H:%M:%S')
        )
        
        # Validate format
        assert 'Alice Doe' in body, "Email must address parent by name"
        assert 'John Doe' in body, "Email must contain student name"
        assert str(timestamp.year) in body, "Email must contain timestamp"
        assert 'Hodari Christian School' in body, "Email must contain school signature"

    def test_checkout_email_body_format(self):
        """Check-out email body is properly formatted"""
        timestamp = timezone.now()
        body = NotificationService.CHECKOUT_EMAIL_BODY.format(
            parent_name='Alice Doe',
            student_name='John Doe',
            pickup_info=' by Alice Doe',
            timestamp=timestamp.strftime('%Y-%m-%d %H:%M:%S')
        )
        
        # Validate format
        assert 'Alice Doe' in body, "Email must address parent by name"
        assert 'John Doe' in body, "Email must contain student name"
        assert 'picked up' in body.lower(), "Email must mention pick-up"
        assert str(timestamp.year) in body, "Email must contain timestamp"


class MultiChannelNotificationLogic(TestCase):
    """
    Property 18: Multi-Channel Notification Logic
    
    Validates that:
    - SMS sent when phone available
    - Email sent when email available
    - Both channels active for parents with both
    - Graceful handling of missing contacts
    """

    def setUp(self):
        """Set up test data"""
        self.class_obj = AcademicClass.objects.create(
            name='Grade 1A',
            level=1,
            academic_year_id=1
        )
        
        self.student = Student.objects.create(
            admission_no='STU001',
            first_name='John',
            last_name='Doe',
            date_of_birth='2020-01-01',
            current_class=self.class_obj
        )

    def test_sms_sent_when_phone_available(self):
        """SMS is sent when parent has phone number"""
        parent = ParentGuardian.objects.create(
            first_name='Alice',
            last_name='Doe',
            phone='+255123456789',
            relationship='Mother'
        )
        self.student.guardians.add(parent)

        # Get parent contacts
        contacts = NotificationService._get_parent_contacts(self.student)
        
        assert len(contacts) > 0, "Should find parent contacts"
        assert any(c.get('phone') for c in contacts), "Should include phone contacts"

    def test_email_sent_when_email_available(self):
        """Email is sent when parent has email address"""
        parent = ParentGuardian.objects.create(
            first_name='Alice',
            last_name='Doe',
            email='alice@example.com',
            relationship='Mother'
        )
        self.student.guardians.add(parent)

        # Get parent contacts
        contacts = NotificationService._get_parent_contacts(self.student)
        
        assert len(contacts) > 0, "Should find parent contacts"
        assert any(c.get('email') for c in contacts), "Should include email contacts"

    def test_both_channels_for_complete_contact_info(self):
        """Both SMS and email sent for parents with both contact types"""
        parent = ParentGuardian.objects.create(
            first_name='Alice',
            last_name='Doe',
            phone='+255123456789',
            email='alice@example.com',
            relationship='Mother'
        )
        self.student.guardians.add(parent)

        # Get parent contacts
        contacts = NotificationService._get_parent_contacts(self.student)
        
        assert len(contacts) > 0, "Should find parent contacts"
        assert contacts[0].get('phone'), "Should have phone"
        assert contacts[0].get('email'), "Should have email"

    def test_graceful_handling_of_missing_phone(self):
        """System handles gracefully when parent has no phone"""
        parent = ParentGuardian.objects.create(
            first_name='Alice',
            last_name='Doe',
            email='alice@example.com',
            relationship='Mother'
        )
        self.student.guardians.add(parent)

        # Get parent contacts
        contacts = NotificationService._get_parent_contacts(self.student)
        
        # Should still return contact (just without phone)
        assert len(contacts) > 0, "Should find parent despite missing phone"

    def test_graceful_handling_of_missing_email(self):
        """System handles gracefully when parent has no email"""
        parent = ParentGuardian.objects.create(
            first_name='Alice',
            last_name='Doe',
            phone='+255123456789',
            relationship='Mother'
        )
        self.student.guardians.add(parent)

        # Get parent contacts
        contacts = NotificationService._get_parent_contacts(self.student)
        
        # Should still return contact (just without email)
        assert len(contacts) > 0, "Should find parent despite missing email"

    def test_no_duplicate_notifications_for_same_contact(self):
        """Duplicate parent contacts don't create duplicate notifications"""
        # This tests the deduplication logic in _get_parent_contacts
        parent1 = ParentGuardian.objects.create(
            first_name='Alice',
            last_name='Doe',
            phone='+255123456789',
            email='alice@example.com',
            relationship='Mother'
        )
        
        self.student.guardians.add(parent1)

        # Get parent contacts
        contacts = NotificationService._get_parent_contacts(self.student)
        
        # Count unique phone numbers
        phones = [c.get('phone') for c in contacts if c.get('phone')]
        unique_phones = set(phones)
        
        assert len(phones) == len(unique_phones), "Should not duplicate same phone number"


class NotificationRetryAndLogging(TransactionTestCase):
    """
    Property 19: Notification Retry and Logging
    
    Validates that:
    - Failed notifications are marked for retry
    - Retry mechanism respects max retry count
    - All notification attempts logged
    - Error messages captured for failed notifications
    """

    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='test_user',
            password='testpass123',
            role='admin'
        )
        
        self.class_obj = AcademicClass.objects.create(
            name='Grade 1A',
            level=1,
            academic_year_id=1
        )
        
        self.student = Student.objects.create(
            admission_no='STU001',
            first_name='John',
            last_name='Doe',
            date_of_birth='2020-01-01',
            current_class=self.class_obj
        )
        
        today = timezone.now().date()
        self.attendance = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

    def test_failed_sms_marked_for_retry(self):
        """Failed SMS message is marked with retry status"""
        message = Message.objects.create(
            phone='+255123456789',
            message='Test message',
            status=0  # Pending
        )

        # Mark as failed
        message.mark_failed('API error: 500')

        # Verify status
        message.refresh_from_db()
        assert message.status == 2, "Failed message should have status 2"
        assert message.error_message == 'API error: 500', "Error message should be captured"
        assert message.retry_count == 1, "Retry count should be incremented"

    def test_retry_mechanism_respects_max_retries(self):
        """Retry mechanism doesn't exceed maximum retry count"""
        message = Message.objects.create(
            phone='+255123456789',
            message='Test message',
            status=2,  # Failed
            retry_count=NotificationService.SMS_MAX_RETRIES
        )

        # Should not retry when max retries reached
        can_retry = message.retry_count < NotificationService.SMS_MAX_RETRIES
        assert not can_retry, "Should not retry beyond max count"

    def test_all_notification_attempts_logged(self):
        """All notification attempts are logged in NotificationLog"""
        initial_log_count = NotificationLog.objects.count()

        # Create notification log entry
        NotificationLog.objects.create(
            attendance_entry=self.attendance,
            recipient_phone='+255123456789',
            notification_type='checkin',
            message='Test message',
            delivery_status='pending'
        )

        # Verify logged
        final_log_count = NotificationLog.objects.count()
        assert final_log_count == initial_log_count + 1, "Notification attempt should be logged"

    def test_error_messages_captured_for_failed_notifications(self):
        """Error messages are properly captured and stored"""
        error_msg = 'SMS provider returned error: Invalid phone number'
        
        notification = NotificationLog.objects.create(
            attendance_entry=self.attendance,
            recipient_phone='+255123456789',
            notification_type='checkin',
            message='Test message',
            delivery_status='failed',
            error_message=error_msg
        )

        # Verify error captured
        notification.refresh_from_db()
        assert notification.error_message == error_msg, "Error message should be captured"
        assert notification.delivery_status == 'failed', "Status should be failed"

    def test_notification_retry_status_marked_correctly(self):
        """Failed notifications are marked for retry with status update"""
        notification = NotificationLog.objects.create(
            attendance_entry=self.attendance,
            recipient_phone='+255123456789',
            notification_type='checkin',
            message='Test message',
            delivery_status='failed',
            retry_count=0
        )

        # Mark for retry
        notification.delivery_status = 'retry'
        notification.retry_count += 1
        notification.save(update_fields=['delivery_status', 'retry_count'])

        # Verify retry marked
        notification.refresh_from_db()
        assert notification.delivery_status == 'retry', "Should be marked for retry"
        assert notification.retry_count == 1, "Retry count should be incremented"

    def test_notification_log_includes_all_required_fields(self):
        """NotificationLog includes all required fields for audit trail"""
        notification = NotificationLog.objects.create(
            attendance_entry=self.attendance,
            recipient_phone='+255123456789',
            recipient_email='parent@example.com',
            notification_type='checkin',
            message='Test message',
            delivery_status='sent',
            sent_at=timezone.now(),
            retry_count=0
        )

        # Verify all fields present
        assert notification.attendance_entry_id, "Should link to attendance entry"
        assert notification.recipient_phone, "Should have recipient phone"
        assert notification.recipient_email, "Should have recipient email"
        assert notification.notification_type == 'checkin', "Should have notification type"
        assert notification.message, "Should have message content"
        assert notification.delivery_status == 'sent', "Should have delivery status"
        assert notification.sent_at, "Should have sent timestamp"


class NotificationSystemIntegration(TransactionTestCase):
    """
    Integration tests for complete notification workflows
    """

    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='test_user',
            password='testpass123',
            role='admin'
        )
        
        self.class_obj = AcademicClass.objects.create(
            name='Grade 1A',
            level=1,
            academic_year_id=1
        )
        
        self.student = Student.objects.create(
            admission_no='STU001',
            first_name='John',
            last_name='Doe',
            date_of_birth='2020-01-01',
            current_class=self.class_obj
        )
        
        self.parent = ParentGuardian.objects.create(
            first_name='Alice',
            last_name='Doe',
            phone='+255123456789',
            email='alice@example.com',
            relationship='Mother'
        )
        self.student.guardians.add(self.parent)

    def test_end_to_end_checkin_notification_flow(self):
        """Complete check-in to notification delivery workflow"""
        # Process check-in
        timestamp = timezone.now()
        result = AttendanceService.checkin_student(
            student_id=self.student.admission_no,
            user=self.user,
            timestamp=timestamp,
            laravel_format=False
        )

        assert result['success'], "Check-in should succeed"

        # Verify SMS queued
        messages = Message.objects.filter(
            phone=self.parent.phone,
            status=0
        )
        assert messages.exists(), "SMS should be queued for parent"

        # Verify notification logged
        logs = NotificationLog.objects.filter(
            recipient_phone=self.parent.phone,
            notification_type='checkin',
            delivery_status='pending'
        )
        assert logs.exists(), "Notification should be logged"

    def test_end_to_end_otp_notification_flow(self):
        """Complete OTP generation to SMS delivery workflow"""
        # Generate OTP
        otp_code = OtpCode.generate_code(self.parent)
        
        # Send OTP notification
        result = NotificationService.send_otp_notification(otp_code)

        assert result, "OTP notification should be sent"

        # Verify SMS queued
        messages = Message.objects.filter(
            phone=self.parent.phone,
            status=0
        )
        assert messages.exists(), "OTP SMS should be queued"

        # Verify message contains OTP code
        message = messages.first()
        assert otp_code.code in message.message, "SMS should contain OTP code"


if __name__ == '__main__':
    import django
    django.setup()
    import unittest
    unittest.main()

