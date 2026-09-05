"""
Tests for notification service infrastructure.

Tests cover:
- SMS provider integration (CloudService and Hodari APIs)
- Email notification delivery
- Celery task processing
- Notification logging and retry mechanism
- OTP notification delivery
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from django.utils import timezone
from django.test import TestCase, override_settings
from django.core.mail import send_mail

from attendance.notification_service import (
    NotificationService,
    CloudServiceSMSProvider,
    HodariSMSProvider,
    send_sms_notification,
    send_email_notification,
    retry_failed_notifications,
    cleanup_expired_otp_codes,
    generate_daily_health_report,
)
from attendance.models import Message, NotificationLog, OtpCode, AttendanceEntry
from students.models import Student, ParentGuardian, StudentParent
from users.models import User


@pytest.mark.django_db
class TestSMSProviders(TestCase):
    """Test SMS provider implementations"""
    
    def test_cloudservice_provider_initialization(self):
        """Test CloudService provider initializes with environment variables"""
        provider = CloudServiceSMSProvider()
        assert provider.api_key is not None
        assert provider.api_url is not None
        assert provider.sender_id is not None
    
    def test_hodari_provider_initialization(self):
        """Test Hodari provider initializes with environment variables"""
        provider = HodariSMSProvider()
        assert provider.api_key is not None
        assert provider.api_url is not None
        assert provider.sender_id is not None
    
    @patch('attendance.notification_service.requests.post')
    def test_cloudservice_send_success(self, mock_post):
        """Test successful SMS send via CloudService"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'status': 'success',
            'message_id': 'msg_123'
        }
        mock_post.return_value = mock_response
        
        provider = CloudServiceSMSProvider()
        result = provider.send('+255123456789', 'Test message')
        
        assert result['success'] is True
        assert result['message_id'] == 'msg_123'
        assert result['provider'] == 'cloudservice'
    
    @patch('attendance.notification_service.requests.post')
    def test_cloudservice_send_failure(self, mock_post):
        """Test failed SMS send via CloudService"""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = 'Invalid request'
        mock_post.return_value = mock_response
        
        provider = CloudServiceSMSProvider()
        result = provider.send('+255123456789', 'Test message')
        
        assert result['success'] is False
        assert result['message_id'] is None
    
    @patch('attendance.notification_service.requests.post')
    def test_hodari_send_success(self, mock_post):
        """Test successful SMS send via Hodari"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'success': True,
            'id': 'sms_456'
        }
        mock_post.return_value = mock_response
        
        provider = HodariSMSProvider()
        result = provider.send('+255123456789', 'Test message')
        
        assert result['success'] is True
        assert result['message_id'] == 'sms_456'
        assert result['provider'] == 'hodari'


@pytest.mark.django_db
class TestNotificationService(TestCase):
    """Test main notification service"""
    
    def setUp(self):
        """Set up test data"""
        self.service = NotificationService()
        
        # Create test user
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        # Create test student
        self.student = Student.objects.create(
            first_name='John',
            last_name='Doe',
            student_id='STU001',
            is_active=True,
            created_by=self.user
        )
        
        # Create test parent
        self.parent = ParentGuardian.objects.create(
            first_name='Jane',
            last_name='Doe',
            email='parent@example.com',
            phone='+255123456789',
            is_active=True
        )
        
        # Create student-parent relationship
        StudentParent.objects.create(
            student=self.student,
            parent=self.parent,
            relationship='Mother'
        )
    
    def test_get_student_parents(self):
        """Test retrieving student's parents"""
        parents = self.service._get_student_parents(self.student)
        
        assert len(parents) == 1
        assert parents[0]['name'] == 'Jane Doe'
        assert parents[0]['phone'] == '+255123456789'
        assert parents[0]['email'] == 'parent@example.com'
    
    def test_format_checkin_message(self):
        """Test check-in message formatting"""
        timestamp = timezone.now()
        message = self.service._format_checkin_message(self.student, timestamp)
        
        assert 'John Doe' in message
        assert 'checked in' in message
        assert 'Hodari Christian School' in message
    
    def test_format_checkout_message(self):
        """Test check-out message formatting"""
        timestamp = timezone.now()
        message = self.service._format_checkout_message(self.student, timestamp, 'John Smith')
        
        assert 'John Doe' in message
        assert 'picked up' in message
        assert 'John Smith' in message
        assert 'Hodari Christian School' in message
    
    def test_format_otp_message(self):
        """Test OTP message formatting"""
        otp = OtpCode.objects.create(
            parent=self.parent,
            code='123456',
            expires_at=timezone.now() + timedelta(minutes=10)
        )
        
        message = self.service._format_otp_message(otp)
        
        assert '123456' in message
        assert 'OTP' in message
        assert 'authorized pickup' in message
    
    def test_format_absence_alert_message(self):
        """Test absence alert message formatting"""
        date = timezone.now()
        message = self.service._format_absence_alert_message(self.student, date)
        
        assert 'John Doe' in message
        assert 'absent' in message
    
    @patch('attendance.notification_service.send_sms_notification.delay')
    def test_queue_notification_with_phone(self, mock_task):
        """Test queuing notification with phone number"""
        self.service._queue_notification(
            recipient_phone='+255123456789',
            notification_type='checkin',
            message='Test message'
        )
        
        # Check notification log was created
        assert NotificationLog.objects.count() == 1
        notification = NotificationLog.objects.first()
        assert notification.recipient_phone == '+255123456789'
        assert notification.notification_type == 'checkin'
        assert notification.delivery_status == 'pending'
        
        # Check message was queued
        assert Message.objects.count() == 1
        message = Message.objects.first()
        assert message.phone == '+255123456789'
        assert message.status == 0  # Pending
        
        # Check task was triggered
        mock_task.assert_called_once()
    
    @patch('attendance.notification_service.send_email_notification.delay')
    def test_queue_notification_with_email(self, mock_task):
        """Test queuing notification with email"""
        self.service._queue_notification(
            recipient_email='test@example.com',
            notification_type='checkin',
            message='Test message'
        )
        
        # Check notification log was created
        assert NotificationLog.objects.count() == 1
        notification = NotificationLog.objects.first()
        assert notification.recipient_email == 'test@example.com'
        assert notification.notification_type == 'checkin'
        
        # Check task was triggered
        mock_task.assert_called_once()
    
    @patch.object(NotificationService, 'send_sms')
    def test_send_checkin_notification(self, mock_send_sms):
        """Test sending check-in notification"""
        mock_send_sms.return_value = {'success': True, 'message_id': 'msg_123'}
        
        timestamp = timezone.now()
        self.service.send_checkin_notification(self.student, timestamp)
        
        # Check notification was queued
        assert NotificationLog.objects.count() == 1
        notification = NotificationLog.objects.first()
        assert notification.notification_type == 'checkin'
        assert notification.recipient_phone == '+255123456789'
    
    @patch.object(NotificationService, 'send_sms')
    def test_send_checkout_notification(self, mock_send_sms):
        """Test sending check-out notification"""
        mock_send_sms.return_value = {'success': True, 'message_id': 'msg_123'}
        
        timestamp = timezone.now()
        self.service.send_checkout_notification(self.student, timestamp, 'John Smith')
        
        # Check notification was queued
        assert NotificationLog.objects.count() == 1
        notification = NotificationLog.objects.first()
        assert notification.notification_type == 'checkout'
        assert 'John Smith' in notification.message
    
    @patch.object(NotificationService, 'send_sms')
    def test_send_otp_notification(self, mock_send_sms):
        """Test sending OTP notification"""
        mock_send_sms.return_value = {'success': True, 'message_id': 'msg_123'}
        
        otp = OtpCode.objects.create(
            parent=self.parent,
            code='123456',
            expires_at=timezone.now() + timedelta(minutes=10)
        )
        
        self.service.send_otp_notification(otp)
        
        # Check notification was queued
        assert NotificationLog.objects.count() == 1
        notification = NotificationLog.objects.first()
        assert notification.notification_type == 'otp'
        assert '123456' in notification.message


@pytest.mark.django_db
class TestCeleryTasks(TestCase):
    """Test Celery background tasks"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.student = Student.objects.create(
            first_name='John',
            last_name='Doe',
            student_id='STU001',
            is_active=True,
            created_by=self.user
        )
        
        self.parent = ParentGuardian.objects.create(
            first_name='Jane',
            last_name='Doe',
            email='parent@example.com',
            phone='+255123456789',
            is_active=True
        )
    
    @patch('attendance.notification_service.NotificationService.send_sms')
    def test_send_sms_notification_task_success(self, mock_send_sms):
        """Test SMS notification Celery task success"""
        mock_send_sms.return_value = {'success': True, 'message_id': 'msg_123'}
        
        notification = NotificationLog.objects.create(
            recipient_phone='+255123456789',
            notification_type='checkin',
            message='Test message',
            delivery_status='pending'
        )
        
        result = send_sms_notification(notification.id)
        
        assert result['success'] is True
        
        # Check notification was updated
        notification.refresh_from_db()
        assert notification.delivery_status == 'sent'
        assert notification.sent_at is not None
    
    @patch('attendance.notification_service.NotificationService.send_sms')
    def test_send_sms_notification_task_failure(self, mock_send_sms):
        """Test SMS notification Celery task failure"""
        mock_send_sms.return_value = {'success': False, 'error': 'API error'}
        
        notification = NotificationLog.objects.create(
            recipient_phone='+255123456789',
            notification_type='checkin',
            message='Test message',
            delivery_status='pending'
        )
        
        # Mock the retry method
        with patch('attendance.notification_service.send_sms_notification.retry'):
            result = send_sms_notification(notification.id)
        
        # Check notification was updated
        notification.refresh_from_db()
        assert notification.retry_count == 1
    
    def test_cleanup_expired_otp_codes_task(self):
        """Test OTP cleanup Celery task"""
        # Create expired OTP
        expired_otp = OtpCode.objects.create(
            parent=self.parent,
            code='111111',
            expires_at=timezone.now() - timedelta(minutes=1),
            verified=False
        )
        
        # Create valid OTP
        valid_otp = OtpCode.objects.create(
            parent=self.parent,
            code='222222',
            expires_at=timezone.now() + timedelta(minutes=5),
            verified=False
        )
        
        result = cleanup_expired_otp_codes()
        
        assert result['success'] is True
        assert result['expired_count'] == 1
        
        # Check expired OTP was marked
        expired_otp.refresh_from_db()
        assert expired_otp.verified is True
        
        # Check valid OTP was not affected
        valid_otp.refresh_from_db()
        assert valid_otp.verified is False
    
    def test_generate_daily_health_report_task(self):
        """Test daily health report generation task"""
        today = timezone.now().date()
        
        # Create test attendance entries
        AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Class 1'
        )
        
        result = generate_daily_health_report()
        
        assert result['success'] is True
        assert 'report' in result
        report = result['report']
        assert report['total_checkins'] == 1
        assert report['date'] == today.isoformat()


@pytest.mark.django_db
class TestNotificationIntegration(TestCase):
    """Integration tests for notification system"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.student = Student.objects.create(
            first_name='John',
            last_name='Doe',
            student_id='STU001',
            is_active=True,
            created_by=self.user
        )
        
        self.parent = ParentGuardian.objects.create(
            first_name='Jane',
            last_name='Doe',
            email='parent@example.com',
            phone='+255123456789',
            is_active=True
        )
        
        StudentParent.objects.create(
            student=self.student,
            parent=self.parent,
            relationship='Mother'
        )
    
    @patch('attendance.notification_service.NotificationService.send_sms')
    def test_end_to_end_checkin_notification(self, mock_send_sms):
        """Test end-to-end check-in notification flow"""
        mock_send_sms.return_value = {'success': True, 'message_id': 'msg_123'}
        
        service = NotificationService()
        timestamp = timezone.now()
        
        # Create attendance entry
        AttendanceEntry.objects.create(
            date=timezone.now().date(),
            student=self.student,
            status='present',
            check_in_time=timestamp.time(),
            marked_by=self.user,
            class_name='Class 1'
        )
        
        # Send notification
        service.send_checkin_notification(self.student, timestamp)
        
        # Verify notification was queued
        assert NotificationLog.objects.count() == 1
        assert Message.objects.count() == 1
        
        notification = NotificationLog.objects.first()
        assert notification.notification_type == 'checkin'
        assert notification.recipient_phone == '+255123456789'
        assert 'John Doe' in notification.message
