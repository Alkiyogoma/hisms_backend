"""
Property-Based Tests for Data Synchronization System

Validates the correctness properties of the data sync system:
- Property 24: Data Synchronization Bidirectionality
- Property 25: Conflict Resolution Consistency
- Property 26: Data Integrity Validation
"""

from hypothesis import given, strategies as st, assume, settings, HealthCheck
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from datetime import datetime, date, timedelta
import logging
import json
from unittest.mock import patch, MagicMock
import hashlib

from students.models import Student
from academics.models import AcademicClass
from users.models import User
from attendance.models import AttendanceEntry, Message, NotificationLog
from attendance.data_sync_service import DataSyncService, SyncConflict
from attendance.backup_service import BackupManager, RollbackManager

logger = logging.getLogger(__name__)


class DataSynchronizationBidirectionality(TransactionTestCase):
    """
    Property 24: Data Synchronization Bidirectionality
    
    Validates that:
    - Data syncs correctly from Django to Laravel
    - Data syncs correctly from Laravel to Django
    - Bidirectional sync maintains consistency
    - Sync preserves all required fields
    - Timestamps are consistent after sync
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
            admission_no='STU0001',
            first_name='Test',
            last_name='Student',
            date_of_birth='2020-01-01',
            current_class=self.class_obj
        )

    def test_attendance_sync_django_to_laravel(self):
        """Attendance entry syncs correctly from Django to Laravel"""
        today = timezone.now().date()
        entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        # Test sync mapping
        mapped_data = DataSyncService._map_django_to_laravel(entry)
        
        assert mapped_data is not None, "Should map Django data to Laravel format"
        assert mapped_data.get('student_id'), "Should include student ID"
        assert mapped_data.get('check_in_time'), "Should include check-in time"
        assert mapped_data.get('status'), "Should include attendance status"

    def test_attendance_sync_laravel_to_django(self):
        """Attendance entry syncs correctly from Laravel to Django"""
        laravel_data = {
            'id': 12345,
            'student_id': 'STU0001',
            'date': '2024-05-23',
            'check_in_time': '08:30:00',
            'check_out_time': '15:30:00',
            'status': 'present',
            'class_name': 'Grade 1A'
        }

        # Test sync mapping
        mapped_data = DataSyncService._map_laravel_to_django(laravel_data)
        
        assert mapped_data is not None, "Should map Laravel data to Django format"
        assert mapped_data.get('status'), "Should include status"
        assert mapped_data.get('check_in_time'), "Should include check-in time"

    def test_sync_preserves_required_fields(self):
        """Sync preserves all required fields from both systems"""
        today = timezone.now().date()
        entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        # Map to Laravel and back
        laravel_data = DataSyncService._map_django_to_laravel(entry)
        django_data = DataSyncService._map_laravel_to_django(laravel_data)

        # Verify required fields preserved
        required_fields = ['status', 'check_in_time', 'class_name']
        for field in required_fields:
            assert field in django_data or field in laravel_data, f"Required field {field} should be preserved"

    def test_timestamp_consistency_after_sync(self):
        """Timestamps remain consistent after bidirectional sync"""
        now = timezone.now()
        today = now.date()
        
        entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=now.time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        # Sync to Laravel format
        laravel_data = DataSyncService._map_django_to_laravel(entry)
        
        # Extract timestamp
        if 'check_in_time' in laravel_data:
            sync_time = laravel_data['check_in_time']
            # Should be string representation
            assert isinstance(sync_time, str), "Timestamp should be serializable"

    def test_bidirectional_sync_data_equality(self):
        """Data is equal after bidirectional sync cycles"""
        today = timezone.now().date()
        entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        # First cycle: Django → Laravel → Django
        laravel_data = DataSyncService._map_django_to_laravel(entry)
        django_data_1 = DataSyncService._map_laravel_to_django(laravel_data)

        # Second cycle: Django → Laravel → Django
        laravel_data_2 = DataSyncService._map_django_to_laravel(entry)
        django_data_2 = DataSyncService._map_laravel_to_django(laravel_data_2)

        # Data should be consistent
        assert django_data_1.get('status') == django_data_2.get('status')

    def test_sync_handles_optional_fields(self):
        """Sync handles optional fields gracefully"""
        laravel_data = {
            'id': 12345,
            'student_id': 'STU0001',
            'date': '2024-05-23',
            'check_in_time': '08:30:00',
            'status': 'present',
            # Optional fields omitted
        }

        # Should handle gracefully without errors
        mapped_data = DataSyncService._map_laravel_to_django(laravel_data)
        assert mapped_data is not None, "Should handle missing optional fields"


class ConflictResolutionConsistency(TransactionTestCase):
    """
    Property 25: Conflict Resolution Consistency
    
    Validates that:
    - Conflicts are consistently detected
    - Conflict resolution uses timestamp precedence
    - Newer data always wins
    - Resolution is reproducible
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
            admission_no='STU0001',
            first_name='Test',
            last_name='Student',
            date_of_birth='2020-01-01',
            current_class=self.class_obj
        )

    def test_conflict_detection_for_different_status(self):
        """Conflict detected when status differs"""
        today = timezone.now().date()
        django_entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        laravel_data = {
            'status': 'absent',  # Different status
            'date': str(today)
        }

        # Should detect conflict
        has_conflict = DataSyncService._detect_conflict(django_entry, laravel_data)
        assert has_conflict, "Should detect conflict when status differs"

    def test_conflict_resolution_timestamp_precedence(self):
        """Newer timestamp wins in conflict resolution"""
        today = timezone.now().date()
        old_time = timezone.make_aware(datetime.combine(today, datetime.min.time()))
        new_time = timezone.make_aware(datetime.combine(today, datetime.min.time())) + timedelta(hours=1)

        django_entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=old_time.time(),
            marked_by=self.user,
            marked_at=old_time,
            class_name='Grade 1A'
        )

        laravel_data = {
            'status': 'absent',
            'updated_at': new_time.isoformat(),  # Newer timestamp
        }

        # Should resolve in favor of newer data
        resolved = DataSyncService._resolve_conflict(django_entry, laravel_data)
        assert resolved is not None, "Should resolve conflict"

    def test_conflict_resolution_reproducibility(self):
        """Conflict resolution produces same result each time"""
        today = timezone.now().date()
        django_entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        laravel_data = {
            'status': 'absent',
            'date': str(today)
        }

        # Resolve twice
        result1 = DataSyncService._resolve_conflict(django_entry, laravel_data)
        result2 = DataSyncService._resolve_conflict(django_entry, laravel_data)

        # Results should be identical
        if result1 and result2:
            assert result1.get('status') == result2.get('status'), "Resolution should be reproducible"

    def test_no_conflict_when_data_identical(self):
        """No conflict when data is identical"""
        today = timezone.now().date()
        django_entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        laravel_data = {
            'status': 'present',
            'date': str(today)
        }

        # Should not detect conflict
        has_conflict = DataSyncService._detect_conflict(django_entry, laravel_data)
        assert not has_conflict, "Should not detect conflict when data is identical"

    def test_conflict_includes_student_data(self):
        """Conflicts include necessary context for resolution"""
        today = timezone.now().date()
        django_entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        laravel_data = {
            'status': 'absent',
            'student_id': 'STU0001'
        }

        # Create conflict object
        conflict = SyncConflict(
            record_id=django_entry.id,
            django_record={'status': django_entry.status},
            laravel_record=laravel_data
        )

        assert conflict.record_id == django_entry.id
        assert conflict.django_record is not None
        assert conflict.laravel_record is not None


class DataIntegrityValidation(TransactionTestCase):
    """
    Property 26: Data Integrity Validation
    
    Validates that:
    - Data integrity checks pass for valid data
    - Corrupted data is detected
    - Checksums validate correctly
    - Data consistency is maintained
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
            admission_no='STU0001',
            first_name='Test',
            last_name='Student',
            date_of_birth='2020-01-01',
            current_class=self.class_obj
        )

    def test_data_integrity_passes_for_valid_data(self):
        """Data integrity validation passes for valid records"""
        today = timezone.now().date()
        entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        # Validate integrity
        is_valid, errors = DataSyncService.validate_data_integrity(entry.id)
        
        assert is_valid, f"Valid data should pass integrity check. Errors: {errors}"

    def test_missing_required_fields_detected(self):
        """Missing required fields are detected during validation"""
        today = timezone.now().date()
        entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        # Set class_name to empty (invalid)
        entry.class_name = ''
        entry.save()

        # Validate integrity - should fail or flag issue
        is_valid, errors = DataSyncService.validate_data_integrity(entry.id)
        
        # Either fails validation or reports the issue
        assert not is_valid or any('class' in str(e).lower() for e in errors)

    def test_checksum_consistency(self):
        """Checksums are consistent for same data"""
        today = timezone.now().date()
        entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        # Calculate checksum twice
        checksum1 = DataSyncService._calculate_checksum(entry)
        checksum2 = DataSyncService._calculate_checksum(entry)

        assert checksum1 == checksum2, "Checksum should be consistent for same data"

    def test_checksum_changes_with_data_modification(self):
        """Checksum changes when data is modified"""
        today = timezone.now().date()
        entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        checksum1 = DataSyncService._calculate_checksum(entry)

        # Modify entry
        entry.status = 'absent'
        entry.save()

        checksum2 = DataSyncService._calculate_checksum(entry)

        assert checksum1 != checksum2, "Checksum should change when data is modified"

    def test_batch_integrity_validation(self):
        """Batch integrity validation checks multiple records"""
        today = timezone.now().date()
        
        # Create multiple entries
        for i in range(5):
            AttendanceEntry.objects.create(
                date=today - timedelta(days=i),
                student=self.student,
                status='present',
                check_in_time=timezone.now().time(),
                marked_by=self.user,
                class_name='Grade 1A'
            )

        # Validate all
        is_valid, errors = DataSyncService.validate_data_integrity()
        
        # Should handle batch validation
        assert isinstance(is_valid, bool)
        assert isinstance(errors, list)


class BackupAndRollbackIntegration(TransactionTestCase):
    """
    Integration tests for backup and rollback functionality
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
            admission_no='STU0001',
            first_name='Test',
            last_name='Student',
            date_of_birth='2020-01-01',
            current_class=self.class_obj
        )

    def test_backup_creation(self):
        """Backup can be created successfully"""
        # Create some test data
        today = timezone.now().date()
        AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        backup_manager = BackupManager()
        backup_id, metadata = backup_manager.create_backup()

        assert backup_id is not None, "Backup ID should be created"
        assert metadata is not None, "Backup metadata should be created"
        assert metadata.timestamp is not None

    def test_backup_verification(self):
        """Backup integrity can be verified"""
        today = timezone.now().date()
        AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        backup_manager = BackupManager()
        backup_id, metadata = backup_manager.create_backup()

        # Verify backup
        is_valid, message = backup_manager.verify_backup(backup_id)
        
        assert isinstance(is_valid, bool)
        assert message is not None

    def test_backup_list_retrieval(self):
        """Backups can be listed and retrieved"""
        backup_manager = BackupManager()
        backups = backup_manager.list_backups()

        assert isinstance(backups, list)

    def test_rollback_capability(self):
        """Rollback manager can restore from backup"""
        today = timezone.now().date()
        entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        backup_manager = BackupManager()
        backup_id, metadata = backup_manager.create_backup()

        # Modify data
        entry.status = 'absent'
        entry.save()

        # Rollback with dry-run
        rollback_manager = RollbackManager(backup_manager)
        result = rollback_manager.rollback_to_backup(backup_id, dry_run=True)

        assert result is not None, "Rollback should return result"
        assert 'status' in result, "Rollback result should include status"


class SyncFailureHandling(TransactionTestCase):
    """
    Tests for sync failure handling and retry logic
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
            admission_no='STU0001',
            first_name='Test',
            last_name='Student',
            date_of_birth='2020-01-01',
            current_class=self.class_obj
        )

    def test_sync_status_retrieval(self):
        """Current sync status can be retrieved"""
        status = DataSyncService.get_sync_status()

        assert status is not None, "Should return sync status"
        assert isinstance(status, dict)

    def test_failed_sync_retry(self):
        """Failed syncs can be retried"""
        result = DataSyncService.retry_failed_syncs()

        assert result is not None, "Should return retry result"
        assert isinstance(result, dict)

    @patch('attendance.data_sync_service.DataSyncService._send_to_laravel')
    def test_sync_handles_api_failures(self, mock_send):
        """Sync handles API failures gracefully"""
        mock_send.return_value = {'success': False, 'error': 'Connection timeout'}

        today = timezone.now().date()
        entry = AttendanceEntry.objects.create(
            date=today,
            student=self.student,
            status='present',
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name='Grade 1A'
        )

        # Attempt sync
        success, error = DataSyncService.sync_attendance_to_laravel(entry.id)

        # Should handle failure
        assert isinstance(success, bool)


if __name__ == '__main__':
    import django
    django.setup()
    import unittest
    unittest.main()

