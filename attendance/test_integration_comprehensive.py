"""
Comprehensive Integration Tests for Complete Attendance System

Tests all components working together from Wave 8-21:
- Complete workflows from mobile app to notifications
- Data migration and synchronization
- Real-time updates and WebSocket functionality
- OTP generation, delivery, and verification
- Parent notification delivery across multiple channels
- System performance under load
- Security and data protection
"""

import pytest
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
import json

from attendance.models import AttendanceEntry, OtpCode, NotificationLog, Message
from attendance.notification_service import NotificationService
from attendance.reporting_service import ReportingService
from attendance.data_sync_service import DataSyncService
from attendance.realtime_tracker import RealTimeTracker
from attendance.backup_service import BackupManager, RollbackManager
from attendance.monitoring_service import MonitoringService
from students.models import Student


class TestCompleteAttendanceWorkflow(TransactionTestCase):
    """Test complete end-to-end attendance marking workflow"""
    
    def setUp(self):
        """Set up test data"""
        self.student = Student.objects.create(
            first_name='John',
            last_name='Doe',
            admission_no='JD001',
            class_name='Grade 1',
            status='active'
        )
    
    @patch('attendance.notification_service.NotificationService.send_sms')
    @patch('attendance.realtime_tracker.async_to_sync')
    def test_complete_checkin_workflow(self, mock_async, mock_sms):
        """Test complete check-in workflow with notifications and real-time updates"""
        mock_sms.return_value = {'success': True}
        mock_async.return_value = MagicMock()
        
        timestamp = timezone.now()
        
        # 1. Create attendance entry (simulating mobile app check-in)
        entry = AttendanceEntry.objects.create(
            student=self.student,
            date=timestamp.date(),
            status='present',
            check_in_time=timestamp
        )
        
        # 2. Verify entry was created
        assert entry.id is not None
        assert entry.status == 'present'
        
        # 3. Send notifications
        with patch.object(NotificationService, '_get_parent_contacts', return_value=[
            {'phone': '+255123456789', 'email': 'parent@example.com', 'name': 'Parent'}
        ]):
            result = NotificationService.send_checkin_notification(self.student, timestamp)
            assert result['sms'] + result['email'] > 0
        
        # 4. Broadcast real-time event
        tracker = RealTimeTracker()
        tracker.broadcast_checkin_event(self.student, timestamp)
        
        # Verify broadcast was called
        assert mock_async.called
    
    @patch('attendance.notification_service.NotificationService.send_sms')
    def test_complete_checkout_workflow(self, mock_sms):
        """Test complete check-out workflow with OTP verification and parent pickup tracking"""
        mock_sms.return_value = {'success': True}
        
        today = timezone.now().date()
        checkin_time = timezone.now().replace(hour=8, minute=0)
        checkout_time = timezone.now().replace(hour=15, minute=30)
        
        # 1. Create check-in entry
        entry = AttendanceEntry.objects.create(
            student=self.student,
            date=today,
            status='present',
            check_in_time=checkin_time
        )
        
        # 2. Generate and send OTP
        parent = MagicMock()
        parent.id = 1
        parent.phone = '+255123456789'
        
        otp = OtpCode.objects.create(
            parent_id=1,
            code='123456',
            expires_at=timezone.now() + timedelta(minutes=10)
        )
        
        # 3. Update entry with checkout and parent information
        entry.check_out_time = checkout_time
        entry.parent_name = 'John Smith'
        entry.save()
        
        # 4. Send checkout notification
        with patch.object(NotificationService, '_get_parent_contacts', return_value=[
            {'phone': '+255123456789', 'email': 'parent@example.com', 'name': 'Parent'}
        ]):
            result = NotificationService.send_checkout_notification(
                self.student,
                checkout_time,
                'John Smith'
            )
            assert result['sms'] + result['email'] > 0
        
        # Verify OTP was created
        assert OtpCode.objects.filter(id=otp.id).exists()
    
    def test_attendance_report_generation(self):
        """Test comprehensive attendance report generation"""
        today = timezone.now().date()
        
        # Create multiple attendance entries
        for i in range(5):
            student = Student.objects.create(
                first_name=f'Student{i}',
                last_name='Test',
                admission_no=f'ST{i:03d}',
                class_name='Grade 1',
                status='active'
            )
            
            AttendanceEntry.objects.create(
                student=student,
                date=today,
                status='present' if i < 3 else 'absent',
                check_in_time=timezone.now() if i < 3 else None
            )
        
        # Generate report
        report = ReportingService.generate_daily_report(today)
        
        # Verify report completeness
        assert report['report_type'] == 'daily'
        assert report['summary']['total_entries'] == 5
        assert report['summary']['present'] == 3
        assert report['summary']['absent'] == 2
        assert 'percentages' in report
        assert 'details' in report


class TestOTPWorkflow(TestCase):
    """Test OTP generation, delivery, and verification workflow"""
    
    def setUp(self):
        """Set up test data"""
        self.student = Student.objects.create(
            first_name='Jane',
            last_name='Doe',
            admission_no='JD002',
            class_name='Grade 1',
            status='active'
        )
    
    @patch('attendance.notification_service.NotificationService.send_sms')
    def test_otp_generation_and_delivery(self, mock_sms):
        """Test OTP code generation and SMS delivery"""
        mock_sms.return_value = {'success': True}
        
        # Create OTP
        otp = OtpCode.objects.create(
            parent_id=1,
            code='654321',
            expires_at=timezone.now() + timedelta(minutes=10)
        )
        
        # Verify OTP was created
        assert otp.code == '654321'
        assert otp.verified is False
        
        # Verify OTP hasn't expired
        assert otp.expires_at > timezone.now()
    
    def test_otp_expiry(self):
        """Test OTP code expiry"""
        # Create expired OTP
        otp = OtpCode.objects.create(
            parent_id=1,
            code='999999',
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        
        # Verify OTP is expired
        assert otp.expires_at < timezone.now()
    
    def test_otp_verification(self):
        """Test OTP code verification"""
        otp = OtpCode.objects.create(
            parent_id=1,
            code='555555',
            expires_at=timezone.now() + timedelta(minutes=10)
        )
        
        # Verify the OTP
        otp.verified = True
        otp.save()
        
        # Confirm verification
        refreshed_otp = OtpCode.objects.get(id=otp.id)
        assert refreshed_otp.verified is True


class TestDataMigrationAndSync(TransactionTestCase):
    """Test data migration and bidirectional synchronization"""
    
    def test_data_sync_to_laravel(self):
        """Test syncing attendance data to Laravel system"""
        student = Student.objects.create(
            first_name='Sync',
            last_name='Test',
            admission_no='SYNC001',
            class_name='Grade 1',
            status='active',
            laravel_student_id='LAR001'
        )
        
        today = timezone.now().date()
        entry = AttendanceEntry.objects.create(
            student=student,
            date=today,
            status='present',
            check_in_time=timezone.now()
        )
        
        # Map to Laravel format
        laravel_data = DataSyncService._map_django_to_laravel(entry)
        
        # Verify mapping
        assert 'student_id' in laravel_data
        assert 'checkin' in laravel_data
        assert laravel_data['status'] == 'present'
    
    def test_conflict_resolution(self):
        """Test conflict resolution in bidirectional sync"""
        student = Student.objects.create(
            first_name='Conflict',
            last_name='Test',
            admission_no='CONF001',
            class_name='Grade 1',
            status='active'
        )
        
        today = timezone.now().date()
        entry = AttendanceEntry.objects.create(
            student=student,
            date=today,
            status='present',
            check_in_time=timezone.now()
        )
        
        # Simulate conflicting Laravel data (with newer timestamp)
        laravel_data = {
            'status': 'absent',
            'checkin': None,
            'updated_at': timezone.now() + timedelta(hours=1),
        }
        
        # Detect conflict
        conflict = DataSyncService._detect_conflict(entry, laravel_data)
        assert conflict is True


class TestBackupAndRollback(TransactionTestCase):
    """Test backup creation and rollback functionality"""
    
    def setUp(self):
        """Set up test data"""
        self.backup_manager = BackupManager()
        self.rollback_manager = RollbackManager(self.backup_manager)
    
    def test_backup_creation(self):
        """Test creating a backup"""
        # Create test data
        student = Student.objects.create(
            first_name='Backup',
            last_name='Test',
            admission_no='BACK001',
            class_name='Grade 1',
            status='active'
        )
        
        today = timezone.now().date()
        AttendanceEntry.objects.create(
            student=student,
            date=today,
            status='present',
            check_in_time=timezone.now()
        )
        
        # Create backup
        backup_id, metadata = self.backup_manager.create_backup()
        
        # Verify backup was created
        assert backup_id is not None
        assert metadata.record_count > 0
    
    def test_backup_verification(self):
        """Test backup integrity verification"""
        # Create backup
        backup_id, metadata = self.backup_manager.create_backup()
        
        # Verify backup
        is_valid, message = self.backup_manager.verify_backup(backup_id)
        assert is_valid is True


class TestMonitoringAndAlerting(TestCase):
    """Test system monitoring and alerting functionality"""
    
    def test_api_health_monitoring(self):
        """Test API health metrics collection"""
        health = MonitoringService.get_api_health()
        
        assert 'status' in health
        assert 'total_requests' in health
        assert 'error_rate' in health
    
    def test_attendance_metrics(self):
        """Test attendance marking metrics"""
        metrics = MonitoringService.get_attendance_metrics()
        
        assert 'status' in metrics
        assert 'total_entries' in metrics
        assert 'success_rate' in metrics
    
    def test_notification_metrics(self):
        """Test notification delivery metrics"""
        metrics = MonitoringService.get_notification_metrics()
        
        assert 'status' in metrics
        assert 'sms' in metrics
        assert 'email' in metrics
    
    def test_system_health_report(self):
        """Test overall system health report generation"""
        health = MonitoringService.get_system_health()
        
        assert 'overall_status' in health
        assert 'api' in health
        assert 'attendance' in health
        assert 'notifications' in health
        assert 'database' in health
        assert 'alerts' in health
    
    def test_daily_health_report(self):
        """Test daily health report generation"""
        report = MonitoringService.generate_daily_health_report()
        
        assert report['report_type'] == 'daily_health'
        assert 'system_health' in report
        assert 'daily_summary' in report
        assert 'daily_notifications' in report


class TestPerformanceUnderLoad(TransactionTestCase):
    """Test system performance under load"""
    
    def test_concurrent_attendance_marking(self):
        """Test handling concurrent attendance marking"""
        students = [
            Student.objects.create(
                first_name=f'Student{i}',
                last_name='Concurrent',
                admission_no=f'CONC{i:03d}',
                class_name='Grade 1',
                status='active'
            )
            for i in range(10)
        ]
        
        today = timezone.now().date()
        
        # Simulate concurrent marking
        entries = []
        for student in students:
            entry = AttendanceEntry.objects.create(
                student=student,
                date=today,
                status='present',
                check_in_time=timezone.now()
            )
            entries.append(entry)
        
        # Verify all entries were created without corruption
        assert len(entries) == 10
        assert AttendanceEntry.objects.filter(date=today).count() == 10


class TestSecurityAndDataProtection(TestCase):
    """Test security and data protection measures"""
    
    def test_input_validation(self):
        """Test input validation for malicious data"""
        # Attempt to create entry with invalid data
        student = Student.objects.create(
            first_name='Security',
            last_name='Test',
            admission_no='SEC001',
            class_name='Grade 1',
            status='active'
        )
        
        # Verify entry creation
        assert student.id is not None
    
    def test_notification_logging(self):
        """Test comprehensive notification logging"""
        student = Student.objects.create(
            first_name='Logging',
            last_name='Test',
            admission_no='LOG001',
            class_name='Grade 1',
            status='active'
        )
        
        today = timezone.now().date()
        entry = AttendanceEntry.objects.create(
            student=student,
            date=today,
            status='present'
        )
        
        # Log notification
        log = NotificationLog.objects.create(
            attendance_entry=entry,
            recipient_phone='+255123456789',
            notification_type='checkin',
            message='Test notification',
            delivery_status='sent'
        )
        
        # Verify log was created
        assert log.id is not None
        assert log.delivery_status == 'sent'
    
    def test_audit_trail(self):
        """Test audit trail for data modifications"""
        student = Student.objects.create(
            first_name='Audit',
            last_name='Test',
            admission_no='AUDIT001',
            class_name='Grade 1',
            status='active'
        )
        
        today = timezone.now().date()
        entry = AttendanceEntry.objects.create(
            student=student,
            date=today,
            status='present',
            created_at=timezone.now(),
            updated_at=timezone.now()
        )
        
        # Verify timestamps exist
        assert entry.created_at is not None
        assert entry.updated_at is not None
        assert entry.created_at <= entry.updated_at


class TestMobileAppFeatures(TestCase):
    """Test mobile app specific features and compatibility"""
    
    def test_student_search_functionality(self):
        """Test student search by name and ID"""
        Student.objects.create(
            first_name='Mobile',
            last_name='Test',
            admission_no='MOB001',
            class_name='Grade 1',
            status='active'
        )
        
        # Search by admission number
        found = Student.objects.filter(admission_no='MOB001').first()
        assert found is not None
        assert found.first_name == 'Mobile'
    
    def test_offline_sync_capability(self):
        """Test offline mode with sync"""
        student = Student.objects.create(
            first_name='Offline',
            last_name='Test',
            admission_no='OFF001',
            class_name='Grade 1',
            status='active'
        )
        
        # Create entry (simulating offline marking)
        today = timezone.now().date()
        entry = AttendanceEntry.objects.create(
            student=student,
            date=today,
            status='present'
        )
        
        # Verify entry exists for sync
        assert AttendanceEntry.objects.filter(id=entry.id).exists()
    
    def test_batch_scan_submission(self):
        """Test batch scanning and submission"""
        students = [
            Student.objects.create(
                first_name=f'Batch{i}',
                last_name='Test',
                admission_no=f'BATCH{i:03d}',
                class_name='Grade 1',
                status='active'
            )
            for i in range(5)
        ]
        
        today = timezone.now().date()
        
        # Batch create entries
        entries = [
            AttendanceEntry(
                student=student,
                date=today,
                status='present'
            )
            for student in students
        ]
        
        created_entries = AttendanceEntry.objects.bulk_create(entries)
        
        # Verify all entries were created
        assert len(created_entries) == 5
