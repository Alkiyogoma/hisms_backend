"""
Focused tests for task 2.4: Implement attendance record migration with timestamp mapping.
Tests the specific functionality of migrating Laravel attendance records to Django AttendanceEntry format.
"""
import logging
from datetime import datetime, date, time
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model

from students.models import Student
from .models import AttendanceEntry
from .data_migrator import DataMigrator
from .laravel_extractor import LaravelFieldMapper

# Disable logging during tests
logging.disable(logging.CRITICAL)

User = get_user_model()


class MockLaravelExtractor:
    """Mock Laravel extractor for testing attendance migration"""
    
    def __init__(self, attendance_records):
        self.attendance_records = attendance_records
        
    def extract_attendance_records(self, batch_size=1000, offset=0):
        """Return paginated attendance records"""
        return self.attendance_records[offset:offset + batch_size]


class AttendanceMigrationTest(TestCase):
    """Test attendance record migration with timestamp mapping"""
    
    def setUp(self):
        """Set up test data"""
        # Create test user for marked_by field
        self.test_user = User.objects.create_user(
            username='test_migrator',
            email='test@hodari.ac.tz',
            first_name='Test',
            last_name='Migrator'
        )
        
        # Create test student
        self.student = Student.objects.create(
            admission_no='TEST001',
            first_name='John',
            last_name='Doe',
            laravel_student_id='2024001',
            class_name='Grade 1',
            status='active'
        )
    
    def test_attendance_migration_with_checkin_only(self):
        """Test migration of attendance record with only check-in time"""
        # Laravel attendance record with only checkin
        laravel_record = {
            'attendance_id': 1,
            'student_id': '2024001',
            'class_id': 1,
            'status': 'present',
            'checkin': datetime(2024, 1, 15, 8, 30, 0),
            'checkout': None,
            'checkin_by': 'Teacher One',
            'checkout_by': None,
            'parent_name': '',
            'reason': '',
            'created_at': datetime(2024, 1, 15, 8, 30, 0),
            'updated_at': datetime(2024, 1, 15, 8, 30, 0)
        }
        
        # Create mock extractor and migrator
        mock_extractor = MockLaravelExtractor([laravel_record])
        migrator = DataMigrator(mock_extractor, batch_size=100)
        
        # Run migration
        result = migrator.migrate_attendance_records()
        
        # Verify migration success
        self.assertEqual(result.successful, 1)
        self.assertEqual(result.failed, 0)
        
        # Verify Django record was created correctly
        django_record = AttendanceEntry.objects.get(laravel_attendance_id=1)
        self.assertEqual(django_record.student, self.student)
        self.assertEqual(django_record.status, 'present')
        self.assertEqual(django_record.check_in_time, time(8, 30, 0))
        self.assertIsNone(django_record.check_out_time)
        self.assertFalse(django_record.is_early_departure)
        self.assertEqual(django_record.date, date(2024, 1, 15))
    
    def test_attendance_migration_with_checkin_and_checkout(self):
        """Test migration of attendance record with both check-in and check-out times"""
        # Laravel attendance record with both checkin and checkout
        laravel_record = {
            'attendance_id': 2,
            'student_id': '2024001',
            'class_id': 1,
            'status': 'present',
            'checkin': datetime(2024, 1, 15, 8, 30, 0),
            'checkout': datetime(2024, 1, 15, 15, 45, 0),  # Normal checkout after 15:30
            'checkin_by': 'Teacher One',
            'checkout_by': 'Teacher Two',
            'parent_name': 'Mr. Doe',
            'reason': '',
            'created_at': datetime(2024, 1, 15, 8, 30, 0),
            'updated_at': datetime(2024, 1, 15, 15, 45, 0)
        }
        
        # Create mock extractor and migrator
        mock_extractor = MockLaravelExtractor([laravel_record])
        migrator = DataMigrator(mock_extractor, batch_size=100)
        
        # Run migration
        result = migrator.migrate_attendance_records()
        
        # Verify migration success
        self.assertEqual(result.successful, 1)
        self.assertEqual(result.failed, 0)
        
        # Verify Django record was created correctly
        django_record = AttendanceEntry.objects.get(laravel_attendance_id=2)
        self.assertEqual(django_record.student, self.student)
        self.assertEqual(django_record.status, 'present')
        self.assertEqual(django_record.check_in_time, time(8, 30, 0))
        self.assertEqual(django_record.check_out_time, time(15, 45, 0))
        self.assertFalse(django_record.is_early_departure)  # 15:45 is after 15:30
        self.assertEqual(django_record.parent_name, 'Mr. Doe')
        self.assertEqual(django_record.date, date(2024, 1, 15))
    
    def test_attendance_migration_with_early_departure(self):
        """Test migration of attendance record with early departure (checkout before 15:30)"""
        # Laravel attendance record with early checkout
        laravel_record = {
            'attendance_id': 3,
            'student_id': '2024001',
            'class_id': 1,
            'status': 'present',
            'checkin': datetime(2024, 1, 15, 8, 30, 0),
            'checkout': datetime(2024, 1, 15, 14, 15, 0),  # Early checkout before 15:30
            'checkin_by': 'Teacher One',
            'checkout_by': 'Teacher Two',
            'parent_name': 'Mrs. Doe',
            'reason': 'Medical appointment',
            'created_at': datetime(2024, 1, 15, 8, 30, 0),
            'updated_at': datetime(2024, 1, 15, 14, 15, 0)
        }
        
        # Create mock extractor and migrator
        mock_extractor = MockLaravelExtractor([laravel_record])
        migrator = DataMigrator(mock_extractor, batch_size=100)
        
        # Run migration
        result = migrator.migrate_attendance_records()
        
        # Verify migration success
        self.assertEqual(result.successful, 1)
        self.assertEqual(result.failed, 0)
        
        # Verify Django record was created correctly
        django_record = AttendanceEntry.objects.get(laravel_attendance_id=3)
        self.assertEqual(django_record.student, self.student)
        self.assertEqual(django_record.status, 'present')
        self.assertEqual(django_record.check_in_time, time(8, 30, 0))
        self.assertEqual(django_record.check_out_time, time(14, 15, 0))
        self.assertTrue(django_record.is_early_departure)  # 14:15 is before 15:30
        self.assertEqual(django_record.parent_name, 'Mrs. Doe')
        self.assertEqual(django_record.reason, 'Medical appointment')
        self.assertEqual(django_record.date, date(2024, 1, 15))
    
    def test_attendance_migration_status_derivation(self):
        """Test status derivation from checkin presence"""
        # Test cases for status derivation
        test_cases = [
            {
                'name': 'Present with checkin',
                'laravel_record': {
                    'attendance_id': 4,
                    'student_id': '2024001',
                    'class_id': 1,
                    'status': None,  # No explicit status
                    'checkin': datetime(2024, 1, 15, 8, 30, 0),
                    'checkout': None,
                    'checkin_by': 'Teacher One',
                    'checkout_by': None,
                    'parent_name': '',
                    'reason': '',
                    'created_at': datetime(2024, 1, 15, 8, 30, 0),
                    'updated_at': datetime(2024, 1, 15, 8, 30, 0)
                },
                'expected_status': 'present'
            },
            {
                'name': 'Absent without checkin',
                'laravel_record': {
                    'attendance_id': 5,
                    'student_id': '2024001',
                    'class_id': 1,
                    'status': None,  # No explicit status
                    'checkin': None,  # No checkin
                    'checkout': None,
                    'checkin_by': None,
                    'checkout_by': None,
                    'parent_name': '',
                    'reason': 'Sick',
                    'created_at': datetime(2024, 1, 16, 8, 0, 0),
                    'updated_at': datetime(2024, 1, 16, 8, 0, 0)
                },
                'expected_status': 'absent'
            }
        ]
        
        for test_case in test_cases:
            with self.subTest(test_case['name']):
                # Clear previous records
                AttendanceEntry.objects.filter(laravel_attendance_id__in=[4, 5]).delete()
                
                # Create mock extractor and migrator
                mock_extractor = MockLaravelExtractor([test_case['laravel_record']])
                migrator = DataMigrator(mock_extractor, batch_size=100)
                
                # Run migration
                result = migrator.migrate_attendance_records()
                
                # Verify migration success
                self.assertEqual(result.successful, 1, f"Migration failed for {test_case['name']}")
                
                # Verify status derivation
                django_record = AttendanceEntry.objects.get(
                    laravel_attendance_id=test_case['laravel_record']['attendance_id']
                )
                self.assertEqual(
                    django_record.status, 
                    test_case['expected_status'],
                    f"Status derivation incorrect for {test_case['name']}"
                )
    
    def test_timestamp_mapping_accuracy(self):
        """Test that Laravel timestamps are accurately mapped to Django time fields"""
        # Laravel record with specific timestamps
        laravel_record = {
            'attendance_id': 6,
            'student_id': '2024001',
            'class_id': 1,
            'status': 'present',
            'checkin': datetime(2024, 1, 15, 7, 45, 30),  # Specific time with seconds
            'checkout': datetime(2024, 1, 15, 16, 20, 45),  # Specific time with seconds
            'checkin_by': 'Teacher One',
            'checkout_by': 'Teacher Two',
            'parent_name': '',
            'reason': '',
            'created_at': datetime(2024, 1, 15, 7, 45, 30),
            'updated_at': datetime(2024, 1, 15, 16, 20, 45)
        }
        
        # Create mock extractor and migrator
        mock_extractor = MockLaravelExtractor([laravel_record])
        migrator = DataMigrator(mock_extractor, batch_size=100)
        
        # Run migration
        result = migrator.migrate_attendance_records()
        
        # Verify migration success
        self.assertEqual(result.successful, 1)
        
        # Verify timestamp mapping accuracy
        django_record = AttendanceEntry.objects.get(laravel_attendance_id=6)
        
        # Check that times are mapped exactly (ignoring seconds for time fields)
        self.assertEqual(django_record.check_in_time.hour, 7)
        self.assertEqual(django_record.check_in_time.minute, 45)
        self.assertEqual(django_record.check_in_time.second, 30)
        
        self.assertEqual(django_record.check_out_time.hour, 16)
        self.assertEqual(django_record.check_out_time.minute, 20)
        self.assertEqual(django_record.check_out_time.second, 45)
        
        # Check that date is derived correctly from checkin datetime
        self.assertEqual(django_record.date, date(2024, 1, 15))
    
    def test_duplicate_prevention(self):
        """Test that duplicate attendance records are properly handled"""
        # Laravel attendance record
        laravel_record = {
            'attendance_id': 7,
            'student_id': '2024001',
            'class_id': 1,
            'status': 'present',
            'checkin': datetime(2024, 1, 15, 8, 30, 0),
            'checkout': None,
            'checkin_by': 'Teacher One',
            'checkout_by': None,
            'parent_name': '',
            'reason': '',
            'created_at': datetime(2024, 1, 15, 8, 30, 0),
            'updated_at': datetime(2024, 1, 15, 8, 30, 0)
        }
        
        # Create mock extractor and migrator
        mock_extractor = MockLaravelExtractor([laravel_record])
        migrator = DataMigrator(mock_extractor, batch_size=100)
        
        # Run migration first time
        result1 = migrator.migrate_attendance_records()
        self.assertEqual(result1.successful, 1)
        self.assertEqual(result1.skipped, 0)
        
        # Run migration second time (should skip duplicate)
        migrator2 = DataMigrator(mock_extractor, batch_size=100)
        result2 = migrator2.migrate_attendance_records()
        self.assertEqual(result2.successful, 0)
        self.assertEqual(result2.skipped, 1)
        
        # Verify only one record exists
        self.assertEqual(AttendanceEntry.objects.filter(laravel_attendance_id=7).count(), 1)


class LaravelFieldMapperTest(TestCase):
    """Test the LaravelFieldMapper for attendance data mapping"""
    
    def test_early_departure_flag_calculation(self):
        """Test early departure flag calculation based on checkout time"""
        test_cases = [
            {
                'name': 'Early departure - 14:30',
                'checkout': datetime(2024, 1, 15, 14, 30, 0),
                'expected': True
            },
            {
                'name': 'Early departure - 15:29',
                'checkout': datetime(2024, 1, 15, 15, 29, 59),
                'expected': True
            },
            {
                'name': 'Normal departure - 15:30',
                'checkout': datetime(2024, 1, 15, 15, 30, 0),
                'expected': False
            },
            {
                'name': 'Late departure - 16:00',
                'checkout': datetime(2024, 1, 15, 16, 0, 0),
                'expected': False
            },
            {
                'name': 'No checkout',
                'checkout': None,
                'expected': False
            }
        ]
        
        for test_case in test_cases:
            with self.subTest(test_case['name']):
                laravel_data = {
                    'attendance_id': 1,
                    'student_id': '2024001',
                    'checkin': datetime(2024, 1, 15, 8, 30, 0),
                    'checkout': test_case['checkout'],
                    'status': 'present'
                }
                
                mapped_data = LaravelFieldMapper.map_attendance_data(laravel_data)
                
                self.assertEqual(
                    mapped_data.get('is_early_departure', False),
                    test_case['expected'],
                    f"Early departure flag incorrect for {test_case['name']}"
                )
    
    def test_status_derivation_from_checkin(self):
        """Test status derivation when no explicit status is provided"""
        test_cases = [
            {
                'name': 'Has checkin - should be present',
                'laravel_data': {
                    'attendance_id': 1,
                    'student_id': '2024001',
                    'checkin': datetime(2024, 1, 15, 8, 30, 0),
                    'checkout': None,
                    'status': None
                },
                'expected_status': 'present'
            },
            {
                'name': 'No checkin - should be absent',
                'laravel_data': {
                    'attendance_id': 2,
                    'student_id': '2024001',
                    'checkin': None,
                    'checkout': None,
                    'status': None
                },
                'expected_status': 'absent'
            },
            {
                'name': 'Explicit status overrides checkin',
                'laravel_data': {
                    'attendance_id': 3,
                    'student_id': '2024001',
                    'checkin': datetime(2024, 1, 15, 8, 30, 0),
                    'checkout': None,
                    'status': 'late'
                },
                'expected_status': 'absent'  # Non-'present' status becomes 'absent'
            }
        ]
        
        for test_case in test_cases:
            with self.subTest(test_case['name']):
                mapped_data = LaravelFieldMapper.map_attendance_data(test_case['laravel_data'])
                
                self.assertEqual(
                    mapped_data.get('status'),
                    test_case['expected_status'],
                    f"Status derivation incorrect for {test_case['name']}"
                )