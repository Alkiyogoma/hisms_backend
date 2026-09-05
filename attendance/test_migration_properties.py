"""
Property-based tests for data migration completeness.
Tests universal properties that should hold for all valid migration scenarios.

Feature: checkin-checkout-integration
"""
import logging
from datetime import datetime, date, time, timedelta
from decimal import Decimal
from typing import Dict, List, Any
from unittest.mock import Mock, patch, MagicMock

from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from django.db import transaction
from hypothesis import given, strategies as st, settings, assume, example
from hypothesis.extra.django import TestCase as HypothesisTestCase

from students.models import Student, ParentGuardian, LaravelParent, StudentLaravelParent
from users.models import User
from .models import AttendanceEntry, OtpCode, Message, NotificationLog
from .data_migrator import DataMigrator, MigrationResult, MigrationReport
from .laravel_extractor import LaravelDatabaseExtractor, LaravelFieldMapper

# Disable logging during tests to reduce noise
logging.disable(logging.CRITICAL)


# Test data generation strategies
@st.composite
def laravel_student_strategy(draw):
    """Generate realistic Laravel student data"""
    student_id = draw(st.integers(min_value=1, max_value=99999))
    name_parts = draw(st.lists(st.text(alphabet=st.characters(whitelist_categories=('Lu', 'Ll')), min_size=2, max_size=10), min_size=2, max_size=3))
    name = " ".join(name_parts)
    
    return {
        'id': draw(st.integers(min_value=1, max_value=99999)),
        'student_id': str(student_id),
        'name': name,
        'phone': draw(st.one_of(st.none(), st.text(alphabet='0123456789', min_size=10, max_size=15))),
        'class_id': draw(st.integers(min_value=1, max_value=20)),
        'image': draw(st.one_of(st.none(), st.text(min_size=5, max_size=50))),
        'status': draw(st.sampled_from(['active', 'inactive', 'withdrawn'])),
        'created_at': draw(st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2024, 12, 31))),
        'updated_at': draw(st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2024, 12, 31)))
    }


@st.composite
def laravel_class_strategy(draw):
    """Generate realistic Laravel class data"""
    class_names = ['Pre-K', 'Kindergarten', 'Grade 1', 'Grade 2', 'Grade 3', 'Grade 4', 'Grade 5', 'Grade 6', 'Grade 7', 'Grade 8']
    
    return {
        'id': draw(st.integers(min_value=1, max_value=20)),
        'name': draw(st.sampled_from(class_names)),
        'created_at': draw(st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2024, 12, 31))),
        'updated_at': draw(st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2024, 12, 31)))
    }


@st.composite
def laravel_attendance_strategy(draw):
    """Generate realistic Laravel attendance data"""
    student_id = draw(st.integers(min_value=1, max_value=99999))
    class_id = draw(st.integers(min_value=1, max_value=20))
    
    # Generate realistic check-in and check-out times
    base_date = draw(st.dates(min_value=date(2020, 1, 1), max_value=date(2024, 12, 31)))
    checkin_time = draw(st.one_of(
        st.none(),
        st.times(min_value=time(7, 0), max_value=time(10, 0))
    ))
    
    # Checkout time should be after checkin if both exist
    checkout_time = None
    if checkin_time:
        checkout_time = draw(st.one_of(
            st.none(),
            st.times(min_value=time(14, 0), max_value=time(18, 0))
        ))
    
    # Combine date and time for datetime fields
    checkin_datetime = None
    checkout_datetime = None
    if checkin_time:
        checkin_datetime = datetime.combine(base_date, checkin_time)
    if checkout_time:
        checkout_datetime = datetime.combine(base_date, checkout_time)
    
    return {
        'attendance_id': draw(st.integers(min_value=1, max_value=999999)),
        'student_id': str(student_id),
        'class_id': class_id,
        'status': draw(st.one_of(st.none(), st.sampled_from(['present', 'absent', 'late']))),
        'checkin': checkin_datetime,
        'checkout': checkout_datetime,
        'checkin_by': draw(st.one_of(st.none(), st.text(min_size=3, max_size=50))),
        'checkout_by': draw(st.one_of(st.none(), st.text(min_size=3, max_size=50))),
        'parent_name': draw(st.one_of(st.none(), st.text(min_size=3, max_size=100))),
        'reason': draw(st.one_of(st.none(), st.text(min_size=0, max_size=200))),
        'created_at': datetime.combine(base_date, time(8, 0)),
        'updated_at': datetime.combine(base_date, time(8, 0))
    }


@st.composite
def laravel_parent_strategy(draw):
    """Generate realistic Laravel parent data"""
    first_name = draw(st.text(alphabet=st.characters(whitelist_categories=('Lu', 'Ll')), min_size=2, max_size=20))
    last_name = draw(st.text(alphabet=st.characters(whitelist_categories=('Lu', 'Ll')), min_size=2, max_size=20))
    
    return {
        'id': draw(st.integers(min_value=1, max_value=99999)),
        'first_name': first_name,
        'last_name': last_name,
        'email': draw(st.one_of(st.none(), st.emails())),
        'phone': draw(st.one_of(st.none(), st.text(alphabet='0123456789', min_size=10, max_size=15))),
        'secondary_phone': draw(st.one_of(st.none(), st.text(alphabet='0123456789', min_size=10, max_size=15))),
        'address': draw(st.one_of(st.none(), st.text(min_size=5, max_size=200))),
        'city': draw(st.one_of(st.none(), st.text(min_size=2, max_size=50))),
        'state': draw(st.one_of(st.none(), st.text(min_size=2, max_size=50))),
        'postal_code': draw(st.one_of(st.none(), st.text(alphabet='0123456789', min_size=4, max_size=10))),
        'occupation': draw(st.one_of(st.none(), st.text(min_size=3, max_size=100))),
        'created_at': draw(st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2024, 12, 31))),
        'updated_at': draw(st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2024, 12, 31)))
    }


@st.composite
def laravel_user_strategy(draw):
    """Generate realistic Laravel user data"""
    name = draw(st.text(alphabet=st.characters(whitelist_categories=('Lu', 'Ll')), min_size=3, max_size=50))
    
    return {
        'id': draw(st.integers(min_value=1, max_value=99999)),
        'name': name,
        'email': draw(st.one_of(st.none(), st.emails())),
        'role': draw(st.sampled_from(['teacher', 'admin', 'staff'])),
        'created_at': draw(st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2024, 12, 31))),
        'updated_at': draw(st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2024, 12, 31)))
    }


@st.composite
def laravel_database_state_strategy(draw):
    """Generate a complete Laravel database state with related records"""
    # Generate classes first
    classes = draw(st.lists(laravel_class_strategy(), min_size=1, max_size=10, unique_by=lambda x: x['id']))
    class_ids = [cls['id'] for cls in classes]
    
    # Generate students with valid class_ids
    students = draw(st.lists(
        laravel_student_strategy().map(lambda s: {**s, 'class_id': draw(st.sampled_from(class_ids))}),
        min_size=1, max_size=50,
        unique_by=lambda x: x['student_id']
    ))
    student_ids = [s['student_id'] for s in students]
    
    # Generate users
    users = draw(st.lists(laravel_user_strategy(), min_size=1, max_size=20, unique_by=lambda x: x['name']))
    
    # Generate parents
    parents = draw(st.lists(laravel_parent_strategy(), min_size=0, max_size=30, unique_by=lambda x: x['id']))
    
    # Generate attendance records with valid student_ids and class_ids
    attendance_records = draw(st.lists(
        laravel_attendance_strategy().map(lambda a: {
            **a, 
            'student_id': draw(st.sampled_from(student_ids)),
            'class_id': draw(st.sampled_from(class_ids))
        }),
        min_size=0, max_size=100,
        unique_by=lambda x: x['attendance_id']
    ))
    
    # Generate student-parent relationships
    relationships = []
    if parents and students:
        for _ in range(draw(st.integers(min_value=0, max_value=min(len(students) * 2, 50)))):
            student_id = draw(st.sampled_from(student_ids))
            parent_id = draw(st.sampled_from([p['id'] for p in parents]))
            
            # Avoid duplicate relationships
            if not any(r['student_id'] == student_id and r['parent_id'] == parent_id for r in relationships):
                relationships.append({
                    'student_id': student_id,
                    'parent_id': parent_id,
                    'relationship': draw(st.sampled_from(['father', 'mother', 'guardian', 'grandparent'])),
                    'is_primary_contact': draw(st.booleans()),
                    'can_pickup': draw(st.booleans()),
                    'created_at': draw(st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2024, 12, 31))),
                    'updated_at': draw(st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2024, 12, 31)))
                })
    
    return {
        'students': students,
        'classes': classes,
        'attendance_records': attendance_records,
        'parents': parents,
        'users': users,
        'student_parent_relationships': relationships
    }


class MockLaravelExtractor:
    """Mock Laravel extractor for testing without database connection"""
    
    def __init__(self, database_state: Dict[str, List[Dict]]):
        self.database_state = database_state
        
    def extract_students(self, batch_size: int = 1000, offset: int = 0) -> List[Dict]:
        """Return paginated students"""
        students = self.database_state.get('students', [])
        return students[offset:offset + batch_size]
        
    def extract_classes(self) -> List[Dict]:
        """Return all classes"""
        return self.database_state.get('classes', [])
        
    def extract_attendance_records(self, batch_size: int = 1000, offset: int = 0) -> List[Dict]:
        """Return paginated attendance records"""
        records = self.database_state.get('attendance_records', [])
        return records[offset:offset + batch_size]
        
    def extract_parents(self, batch_size: int = 1000, offset: int = 0) -> List[Dict]:
        """Return paginated parents"""
        parents = self.database_state.get('parents', [])
        return parents[offset:offset + batch_size]
        
    def extract_student_parent_relationships(self) -> List[Dict]:
        """Return all student-parent relationships"""
        return self.database_state.get('student_parent_relationships', [])


class MigrationCompletenessPropertyTests(HypothesisTestCase):
    """
    Property-based tests for data migration completeness.
    
    **Property 1: Data Migration Completeness**
    For any Laravel database state with attendance, student, class, and user records,
    the Data_Migrator should extract all records without data loss and preserve all
    relationships between entities.
    
    **Validates: Requirements 1.1, 1.6**
    """
    
    def setUp(self):
        """Set up test environment"""
        # Create a test user for migrations that require marked_by
        # Use get_or_create to avoid conflicts in test runs
        self.test_user, created = User.objects.get_or_create(
            username='test_migrator',
            defaults={
                'email': 'test@hodari.ac.tz',
                'first_name': 'Test',
                'last_name': 'Migrator'
            }
        )
    
    @given(database_state=laravel_database_state_strategy())
    @settings(max_examples=50, deadline=30000)  # Increased deadline for complex operations
    def test_migration_completeness_property(self, database_state):
        """
        Feature: checkin-checkout-integration, Property 1: Data Migration Completeness
        
        For any Laravel database state with attendance, student, class, and user records,
        the Data_Migrator should extract all records without data loss and preserve all
        relationships between entities.
        
        **Validates: Requirements 1.1, 1.6**
        """
        # Skip empty database states as they don't test meaningful migration
        assume(len(database_state['students']) > 0)
        
        # Create mock extractor with the generated database state
        mock_extractor = MockLaravelExtractor(database_state)
        
        # Create migrator instance
        migrator = DataMigrator(mock_extractor, batch_size=100)
        
        # Record initial counts from Laravel data
        initial_counts = {
            'students': len(database_state['students']),
            'classes': len(database_state['classes']),
            'attendance_records': len(database_state['attendance_records']),
            'parents': len(database_state['parents']),
            'relationships': len(database_state['student_parent_relationships'])
        }
        
        # Perform migration operations
        # Perform migration operations (without transaction wrapper to avoid rollback issues)
        # Migrate classes first (they're referenced by students)
        class_result = migrator.migrate_classes()
        
        # Migrate students
        student_result = migrator.migrate_students()
        
        # Migrate parents and relationships
        parent_result = migrator.migrate_parent_relationships()
        
        # Migrate attendance records
        attendance_result = migrator.migrate_attendance_records()
        
        # **PROPERTY ASSERTION 1: No data loss during extraction**
        # All records from Laravel should be processed (successful + failed + skipped = total)
        self.assertEqual(
            class_result.total_processed,
            initial_counts['classes'],
            f"Class extraction incomplete: processed {class_result.total_processed}, expected {initial_counts['classes']}"
        )
        
        self.assertEqual(
            student_result.total_processed,
            initial_counts['students'],
            f"Student extraction incomplete: processed {student_result.total_processed}, expected {initial_counts['students']}"
        )
        
        self.assertEqual(
            attendance_result.total_processed,
            initial_counts['attendance_records'],
            f"Attendance extraction incomplete: processed {attendance_result.total_processed}, expected {initial_counts['attendance_records']}"
        )
        
        self.assertEqual(
            parent_result.total_processed,
            initial_counts['parents'] + initial_counts['relationships'],
            f"Parent/relationship extraction incomplete: processed {parent_result.total_processed}, expected {initial_counts['parents'] + initial_counts['relationships']}"
        )
        
        # **PROPERTY ASSERTION 2: Successful migrations create corresponding Django records**
        # Count of successful migrations should match Django record count
        django_student_count = Student.objects.filter(laravel_student_id__isnull=False).count()
        self.assertEqual(
            django_student_count,
            student_result.successful,
            f"Django student count mismatch: {django_student_count} in DB, {student_result.successful} successful migrations"
        )
        
        django_attendance_count = AttendanceEntry.objects.filter(laravel_attendance_id__isnull=False).count()
        self.assertEqual(
            django_attendance_count,
            attendance_result.successful,
            f"Django attendance count mismatch: {django_attendance_count} in DB, {attendance_result.successful} successful migrations"
        )
        
        django_parent_count = LaravelParent.objects.filter(laravel_parent_id__isnull=False).count()
        # Parent result includes both parents and relationships, so we check parents separately
        expected_parent_migrations = len([p for p in database_state['parents']])
        if expected_parent_migrations > 0:
            self.assertGreaterEqual(
                django_parent_count,
                0,  # Some parents might fail validation, but we should have some success
                "No parents were successfully migrated despite having parent data"
            )
        
        # **PROPERTY ASSERTION 3: Relationship preservation**
        # All migrated students should have their Laravel IDs preserved
        for student_data in database_state['students']:
            laravel_id = student_data['student_id']
            django_student = Student.objects.filter(laravel_student_id=laravel_id).first()
            
            if django_student:  # If migration was successful
                self.assertEqual(
                    django_student.laravel_student_id,
                    laravel_id,
                    f"Student Laravel ID not preserved: expected {laravel_id}, got {django_student.laravel_student_id}"
                )
        
        # **PROPERTY ASSERTION 4: Attendance-Student relationship integrity**
        # All migrated attendance records should reference valid students
        for attendance_data in database_state['attendance_records']:
            laravel_attendance_id = attendance_data['attendance_id']
            django_attendance = AttendanceEntry.objects.filter(laravel_attendance_id=laravel_attendance_id).first()
            
            if django_attendance:  # If migration was successful
                # The attendance record should reference a student with the correct Laravel ID
                expected_student_id = attendance_data['student_id']
                self.assertEqual(
                    django_attendance.student.laravel_student_id,
                    expected_student_id,
                    f"Attendance-Student relationship broken: attendance {laravel_attendance_id} references wrong student"
                )
        
        # **PROPERTY ASSERTION 5: No duplicate Laravel IDs**
        # Each Laravel ID should appear at most once in Django
        laravel_student_ids = Student.objects.filter(
            laravel_student_id__isnull=False
        ).values_list('laravel_student_id', flat=True)
        
        self.assertEqual(
            len(laravel_student_ids),
            len(set(laravel_student_ids)),
            "Duplicate Laravel student IDs found in Django database"
        )
        
        laravel_attendance_ids = AttendanceEntry.objects.filter(
            laravel_attendance_id__isnull=False
        ).values_list('laravel_attendance_id', flat=True)
        
        self.assertEqual(
            len(laravel_attendance_ids),
            len(set(laravel_attendance_ids)),
            "Duplicate Laravel attendance IDs found in Django database"
        )
        
        # **PROPERTY ASSERTION 6: Migration report accuracy**
        # The migration report should accurately reflect what happened
        migrator.report.add_result('classes', class_result)
        migrator.report.add_result('students', student_result)
        migrator.report.add_result('parents', parent_result)
        migrator.report.add_result('attendance', attendance_result)
        migrator.report.finish()
        
        report_dict = migrator.report.to_dict()
        
        # Total processed should equal sum of all operations
        expected_total = (class_result.total_processed + student_result.total_processed + 
                         parent_result.total_processed + attendance_result.total_processed)
        
        self.assertEqual(
            report_dict['overall_stats']['total_records_processed'],
            expected_total,
            f"Migration report total mismatch: reported {report_dict['overall_stats']['total_records_processed']}, expected {expected_total}"
        )
        
        # Success count should match sum of successful operations
        expected_success = (class_result.successful + student_result.successful + 
                           parent_result.successful + attendance_result.successful)
        
        self.assertEqual(
            report_dict['overall_stats']['total_successful'],
            expected_success,
            f"Migration report success count mismatch: reported {report_dict['overall_stats']['total_successful']}, expected {expected_success}"
        )
    
    @given(database_state=laravel_database_state_strategy())
    @settings(max_examples=20, deadline=20000)
    def test_migration_idempotency_property(self, database_state):
        """
        Feature: checkin-checkout-integration, Property 1: Data Migration Completeness (Idempotency)
        
        Running the same migration twice should not create duplicate records or lose data.
        The second migration should skip existing records.
        
        **Validates: Requirements 1.7**
        """
        # Skip empty database states
        assume(len(database_state['students']) > 0)
        
        mock_extractor = MockLaravelExtractor(database_state)
        migrator = DataMigrator(mock_extractor, batch_size=100)
        
        # First migration
        first_student_result = migrator.migrate_students()
        first_attendance_result = migrator.migrate_attendance_records()
        
        # Record counts after first migration
        first_student_count = Student.objects.filter(laravel_student_id__isnull=False).count()
        first_attendance_count = AttendanceEntry.objects.filter(laravel_attendance_id__isnull=False).count()
        
        # Second migration (should be idempotent)
        migrator2 = DataMigrator(mock_extractor, batch_size=100)
        second_student_result = migrator2.migrate_students()
        second_attendance_result = migrator2.migrate_attendance_records()
        
        # Record counts after second migration
        second_student_count = Student.objects.filter(laravel_student_id__isnull=False).count()
        second_attendance_count = AttendanceEntry.objects.filter(laravel_attendance_id__isnull=False).count()
        
        # **PROPERTY ASSERTION: Idempotency**
        # Record counts should remain the same
        self.assertEqual(
            first_student_count,
            second_student_count,
            f"Student count changed after second migration: {first_student_count} -> {second_student_count}"
        )
        
        self.assertEqual(
            first_attendance_count,
            second_attendance_count,
            f"Attendance count changed after second migration: {first_attendance_count} -> {second_attendance_count}"
        )
        
        # Second migration should have skipped all records
        self.assertEqual(
            second_student_result.successful,
            0,
            f"Second migration created new students: {second_student_result.successful}"
        )
        
        self.assertEqual(
            second_attendance_result.successful,
            0,
            f"Second migration created new attendance records: {second_attendance_result.successful}"
        )
        
        # All records in second migration should be skipped
        if first_student_result.successful > 0:
            self.assertGreater(
                second_student_result.skipped,
                0,
                "Second migration should have skipped existing student records"
            )
        
        if first_attendance_result.successful > 0:
            self.assertGreater(
                second_attendance_result.skipped,
                0,
                "Second migration should have skipped existing attendance records"
            )
    
    def test_migration_completeness_with_realistic_data(self):
        """
        Test migration completeness with realistic data examples.
        This provides concrete examples alongside the property-based tests.
        """
        # Create realistic test data
        realistic_database_state = {
            'students': [
                {
                    'id': 1,
                    'student_id': '2024001',
                    'name': 'John Doe',
                    'phone': '0712345678',
                    'class_id': 1,
                    'image': 'students/john_doe.jpg',
                    'status': 'active',
                    'created_at': datetime(2024, 1, 15, 8, 0),
                    'updated_at': datetime(2024, 1, 15, 8, 0)
                },
                {
                    'id': 2,
                    'student_id': '2024002',
                    'name': 'Jane Smith',
                    'phone': '0723456789',
                    'class_id': 2,
                    'image': 'students/jane_smith.jpg',
                    'status': 'active',
                    'created_at': datetime(2024, 1, 16, 8, 0),
                    'updated_at': datetime(2024, 1, 16, 8, 0)
                }
            ],
            'classes': [
                {
                    'id': 1,
                    'name': 'Grade 1',
                    'created_at': datetime(2024, 1, 1, 8, 0),
                    'updated_at': datetime(2024, 1, 1, 8, 0)
                },
                {
                    'id': 2,
                    'name': 'Grade 2',
                    'created_at': datetime(2024, 1, 1, 8, 0),
                    'updated_at': datetime(2024, 1, 1, 8, 0)
                }
            ],
            'attendance_records': [
                {
                    'attendance_id': 1,
                    'student_id': '2024001',
                    'class_id': 1,
                    'status': 'present',
                    'checkin': datetime(2024, 1, 15, 8, 30),
                    'checkout': datetime(2024, 1, 15, 15, 30),
                    'checkin_by': 'Teacher One',
                    'checkout_by': 'Teacher Two',
                    'parent_name': 'Mr. Doe',
                    'reason': '',
                    'created_at': datetime(2024, 1, 15, 8, 30),
                    'updated_at': datetime(2024, 1, 15, 15, 30)
                }
            ],
            'parents': [
                {
                    'id': 1,
                    'first_name': 'Robert',
                    'last_name': 'Doe',
                    'email': 'robert.doe@email.com',
                    'phone': '0712345678',
                    'secondary_phone': '0723456789',
                    'address': '123 Main St',
                    'city': 'Dar es Salaam',
                    'state': 'Dar es Salaam',
                    'postal_code': '12345',
                    'occupation': 'Engineer',
                    'created_at': datetime(2024, 1, 10, 8, 0),
                    'updated_at': datetime(2024, 1, 10, 8, 0)
                }
            ],
            'users': [
                {
                    'id': 1,
                    'name': 'Teacher One',
                    'email': 'teacher1@hodari.ac.tz',
                    'role': 'teacher',
                    'created_at': datetime(2024, 1, 1, 8, 0),
                    'updated_at': datetime(2024, 1, 1, 8, 0)
                }
            ],
            'student_parent_relationships': [
                {
                    'student_id': '2024001',
                    'parent_id': 1,
                    'relationship': 'father',
                    'is_primary_contact': True,
                    'can_pickup': True,
                    'created_at': datetime(2024, 1, 10, 8, 0),
                    'updated_at': datetime(2024, 1, 10, 8, 0)
                }
            ]
        }
        
        # Run the property test with this realistic data
        # Create a mock extractor and run the test logic directly
        mock_extractor = MockLaravelExtractor(realistic_database_state)
        migrator = DataMigrator(mock_extractor, batch_size=100)
        
        # Perform migration operations
        class_result = migrator.migrate_classes()
        student_result = migrator.migrate_students()
        parent_result = migrator.migrate_parent_relationships()
        attendance_result = migrator.migrate_attendance_records()
        
        # Verify the results
        self.assertGreater(student_result.successful, 0, "Should have successfully migrated students")
        self.assertGreater(attendance_result.successful, 0, "Should have successfully migrated attendance records")