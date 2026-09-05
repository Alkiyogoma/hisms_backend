"""
Unit tests for backup and rollback functionality.

Tests validate:
- Backup creation and integrity
- Rollback functionality
- Backup versioning and management
"""

import os
import tempfile
import gzip
import json
from datetime import datetime, timedelta
from django.test import TestCase
from django.utils import timezone

from attendance.models import AttendanceEntry, Message, NotificationLog, OtpCode, AttendanceStatus
from attendance.backup_service import BackupManager, RollbackManager, BackupMetadata
from students.models import Student, ParentGuardian
from users.models import User


class TestBackupManager(TestCase):
    """Tests for backup creation and management"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.backup_manager = BackupManager(backup_dir=self.temp_dir)
        
        # Create test user
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        # Create test student
        self.student = Student.objects.create(
            first_name='Test',
            last_name='Student',
            student_id='TS001',
            laravel_student_id='1',
            current_class='Grade 1A',
            is_active=True
        )
        
        # Create test parent
        self.parent = ParentGuardian.objects.create(
            first_name='Parent',
            last_name='Test',
            email='parent@example.com',
            phone='+255123456789'
        )
    
    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
    
    def test_backup_creation(self):
        """Test that backups are created successfully"""
        # Create test data
        AttendanceEntry.objects.create(
            student=self.student,
            date=timezone.now().date(),
            status=AttendanceStatus.PRESENT,
            class_name='Grade 1A',
            marked_by=self.user,
        )
        
        # Create backup
        backup_id, metadata = self.backup_manager.create_backup()
        
        # Verify backup was created
        self.assertIsNotNone(backup_id)
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata.record_count, 1)
        self.assertIsNotNone(metadata.checksum)
        
        # Verify backup file exists
        backup_path = self.backup_manager._get_backup_path(backup_id)
        self.assertTrue(os.path.exists(backup_path))
    
    def test_backup_contains_all_data_types(self):
        """Test that backup includes all data types"""
        # Create test data of each type
        attendance = AttendanceEntry.objects.create(
            student=self.student,
            date=timezone.now().date(),
            status=AttendanceStatus.PRESENT,
            class_name='Grade 1A',
            marked_by=self.user,
        )
        
        message = Message.objects.create(
            phone='+255123456789',
            message='Test message',
            status=0,
        )
        
        notification = NotificationLog.objects.create(
            attendance_entry=attendance,
            recipient_phone='+255123456789',
            notification_type='checkin',
            message='Test notification',
        )
        
        otp = OtpCode.objects.create(
            parent=self.parent,
            code='123456',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        
        # Create backup
        backup_id, metadata = self.backup_manager.create_backup()
        
        # Verify record count
        self.assertEqual(metadata.record_count, 4)
        
        # Load backup and verify content
        backup_path = self.backup_manager._get_backup_path(backup_id)
        with gzip.open(backup_path, 'rt', encoding='utf-8') as f:
            backup_content = json.load(f)
        
        data = backup_content['data']
        self.assertEqual(len(data['attendance_entries']), 1)
        self.assertEqual(len(data['messages']), 1)
        self.assertEqual(len(data['notification_logs']), 1)
        self.assertEqual(len(data['otp_codes']), 1)
    
    def test_backup_metadata_accuracy(self):
        """Test that backup metadata is accurate"""
        # Create test data
        for i in range(3):
            AttendanceEntry.objects.create(
                student=self.student,
                date=timezone.now().date() - timedelta(days=i),
                status=AttendanceStatus.PRESENT,
                class_name='Grade 1A',
                marked_by=self.user,
            )
        
        # Create backup
        backup_id, metadata = self.backup_manager.create_backup()
        
        # Verify metadata
        self.assertEqual(metadata.record_count, 3)
        self.assertIsNotNone(metadata.checksum)
        self.assertEqual(metadata.version, '1.0')
        self.assertFalse(metadata.restored)
    
    def test_backup_verification_success(self):
        """Test successful backup verification"""
        # Create test data
        AttendanceEntry.objects.create(
            student=self.student,
            date=timezone.now().date(),
            status=AttendanceStatus.PRESENT,
            class_name='Grade 1A',
            marked_by=self.user,
        )
        
        # Create and verify backup
        backup_id, _ = self.backup_manager.create_backup()
        is_valid, message = self.backup_manager.verify_backup(backup_id)
        
        self.assertTrue(is_valid)
        self.assertIn('successfully', message.lower())
    
    def test_backup_verification_detects_corruption(self):
        """Test that verification detects corrupted backups"""
        # Create test data
        AttendanceEntry.objects.create(
            student=self.student,
            date=timezone.now().date(),
            status=AttendanceStatus.PRESENT,
            class_name='Grade 1A',
            marked_by=self.user,
        )
        
        # Create backup
        backup_id, _ = self.backup_manager.create_backup()
        
        # Corrupt the backup by modifying checksum
        backup_path = self.backup_manager._get_backup_path(backup_id)
        with gzip.open(backup_path, 'rt', encoding='utf-8') as f:
            backup_content = json.load(f)
        
        backup_content['metadata']['checksum'] = 'corrupted_checksum'
        
        with gzip.open(backup_path, 'wt', encoding='utf-8') as f:
            json.dump(backup_content, f)
        
        # Verify backup detects corruption
        is_valid, message = self.backup_manager.verify_backup(backup_id)
        
        self.assertFalse(is_valid)
        self.assertIn('checksum', message.lower())
    
    def test_list_backups(self):
        """Test listing available backups"""
        # Create multiple backups
        backup_ids = []
        for i in range(3):
            backup_id, _ = self.backup_manager.create_backup()
            backup_ids.append(backup_id)
        
        # List backups
        backups = self.backup_manager.list_backups()
        
        # Verify all backups are listed
        self.assertEqual(len(backups), 3)
        
        # Verify backups are sorted by date (newest first)
        for i in range(len(backups) - 1):
            self.assertGreaterEqual(
                backups[i][1].timestamp,
                backups[i + 1][1].timestamp
            )
    
    def test_delete_old_backups(self):
        """Test deletion of old backups"""
        # Create backup
        backup_id, _ = self.backup_manager.create_backup()
        
        # Verify backup exists
        backups_before = self.backup_manager.list_backups()
        self.assertEqual(len(backups_before), 1)
        
        # Delete backups older than 0 days (should delete all)
        deleted_count = self.backup_manager.delete_old_backups(days=0)
        
        # Verify backup was deleted
        self.assertEqual(deleted_count, 1)
        backups_after = self.backup_manager.list_backups()
        self.assertEqual(len(backups_after), 0)


class TestRollbackManager(TestCase):
    """Tests for rollback functionality"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.backup_manager = BackupManager(backup_dir=self.temp_dir)
        self.rollback_manager = RollbackManager(backup_manager=self.backup_manager)
        
        # Create test user
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        # Create test student
        self.student = Student.objects.create(
            first_name='Test',
            last_name='Student',
            student_id='TS001',
            laravel_student_id='1',
            current_class='Grade 1A',
            is_active=True
        )
        
        # Create test parent
        self.parent = ParentGuardian.objects.create(
            first_name='Parent',
            last_name='Test',
            email='parent@example.com',
            phone='+255123456789'
        )
    
    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
    
    def test_rollback_dry_run(self):
        """Test rollback dry run without making changes"""
        # Create test data
        AttendanceEntry.objects.create(
            student=self.student,
            date=timezone.now().date(),
            status=AttendanceStatus.PRESENT,
            class_name='Grade 1A',
            marked_by=self.user,
        )
        
        # Create backup
        backup_id, _ = self.backup_manager.create_backup()
        
        # Modify data
        AttendanceEntry.objects.all().delete()
        self.assertEqual(AttendanceEntry.objects.count(), 0)
        
        # Perform dry run rollback
        result = self.rollback_manager.rollback_to_backup(backup_id, dry_run=True)
        
        # Verify dry run didn't restore data
        self.assertTrue(result['dry_run'])
        self.assertEqual(AttendanceEntry.objects.count(), 0)
        self.assertEqual(result['attendance_entries']['restored'], 1)
    
    def test_rollback_restores_data(self):
        """Test that rollback restores data correctly"""
        # Create test data
        original_entry = AttendanceEntry.objects.create(
            student=self.student,
            date=timezone.now().date(),
            status=AttendanceStatus.PRESENT,
            class_name='Grade 1A',
            marked_by=self.user,
        )
        original_id = original_entry.id
        
        # Create backup
        backup_id, _ = self.backup_manager.create_backup()
        
        # Delete data
        AttendanceEntry.objects.all().delete()
        self.assertEqual(AttendanceEntry.objects.count(), 0)
        
        # Perform rollback
        result = self.rollback_manager.rollback_to_backup(backup_id, dry_run=False)
        
        # Verify data was restored
        self.assertFalse(result['dry_run'])
        self.assertEqual(result['attendance_entries']['restored'], 1)
        self.assertEqual(AttendanceEntry.objects.count(), 1)
        
        # Verify restored data matches original
        restored_entry = AttendanceEntry.objects.first()
        self.assertEqual(restored_entry.student_id, self.student.id)
        self.assertEqual(restored_entry.status, AttendanceStatus.PRESENT)
    
    def test_rollback_restores_all_data_types(self):
        """Test that rollback restores all data types"""
        # Create test data
        attendance = AttendanceEntry.objects.create(
            student=self.student,
            date=timezone.now().date(),
            status=AttendanceStatus.PRESENT,
            class_name='Grade 1A',
            marked_by=self.user,
        )
        
        message = Message.objects.create(
            phone='+255123456789',
            message='Test message',
            status=0,
        )
        
        notification = NotificationLog.objects.create(
            attendance_entry=attendance,
            recipient_phone='+255123456789',
            notification_type='checkin',
            message='Test notification',
        )
        
        otp = OtpCode.objects.create(
            parent=self.parent,
            code='123456',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        
        # Create backup
        backup_id, _ = self.backup_manager.create_backup()
        
        # Delete all data
        AttendanceEntry.objects.all().delete()
        Message.objects.all().delete()
        NotificationLog.objects.all().delete()
        OtpCode.objects.all().delete()
        
        # Perform rollback
        result = self.rollback_manager.rollback_to_backup(backup_id, dry_run=False)
        
        # Verify all data was restored
        self.assertEqual(result['attendance_entries']['restored'], 1)
        self.assertEqual(result['messages']['restored'], 1)
        self.assertEqual(result['notification_logs']['restored'], 1)
        self.assertEqual(result['otp_codes']['restored'], 1)
        
        self.assertEqual(AttendanceEntry.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(NotificationLog.objects.count(), 1)
        self.assertEqual(OtpCode.objects.count(), 1)
    
    def test_rollback_fails_with_invalid_backup(self):
        """Test that rollback fails gracefully with invalid backup"""
        # Attempt rollback with non-existent backup
        result = self.rollback_manager.rollback_to_backup('invalid_backup_id', dry_run=False)
        
        # Verify rollback failed
        self.assertGreater(len(result['errors']), 0)
        self.assertIn('verification', result['errors'][0].lower())
    
    def test_rollback_preserves_data_integrity(self):
        """Test that rollback preserves data integrity"""
        # Create test data with specific values
        entry = AttendanceEntry.objects.create(
            student=self.student,
            date=timezone.now().date(),
            status=AttendanceStatus.PRESENT,
            check_in_time=timezone.now().time(),
            class_name='Grade 1A',
            marked_by=self.user,
            parent_name='Test Parent',
            reason='Test reason',
        )
        
        # Create backup
        backup_id, _ = self.backup_manager.create_backup()
        
        # Delete and restore
        AttendanceEntry.objects.all().delete()
        self.rollback_manager.rollback_to_backup(backup_id, dry_run=False)
        
        # Verify data integrity
        restored = AttendanceEntry.objects.first()
        self.assertEqual(restored.student_id, self.student.id)
        self.assertEqual(restored.status, AttendanceStatus.PRESENT)
        self.assertEqual(restored.parent_name, 'Test Parent')
        self.assertEqual(restored.reason, 'Test reason')
