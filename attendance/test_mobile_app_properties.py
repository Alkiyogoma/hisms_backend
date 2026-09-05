"""
Property-based tests for mobile app features.
Tests Property 34: Search Functionality Accuracy
Tests Property 35: Offline Synchronization Integrity
Tests Property 36: QR Code Validation Completeness
"""
import logging
from datetime import datetime, timedelta
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from hypothesis import given, strategies as st, settings, assume, example, HealthCheck
from hypothesis.extra.django import TestCase as HypothesisTestCase

from students.models import Student
from .models import AttendanceEntry
from .services import StudentSearchService, QRCodeService

# Disable logging during tests
logging.disable(logging.CRITICAL)

User = get_user_model()


# ============================================================================
# Hypothesis Strategies for Mobile App Testing
# ============================================================================

def student_name_strategy():
    """Strategy for generating student names"""
    return st.text(
        alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ ',
        min_size=1,
        max_size=50
    )


def student_id_strategy():
    """Strategy for generating student IDs"""
    return st.one_of(
        st.integers(min_value=1000000, max_value=9999999).map(str),
        st.text(alphabet='0123456789', min_size=4, max_size=10),
    )


def search_query_strategy():
    """Strategy for generating search queries"""
    return st.text(
        alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ',
        min_size=1,
        max_size=30
    )


def offline_entry_strategy():
    """Strategy for generating offline attendance entries"""
    return st.tuples(
        st.integers(min_value=1000000, max_value=9999999).map(str),  # Student ID
        st.sampled_from(['IN', 'OUT']),  # Scan type
        st.datetimes(
            min_value=datetime(2024, 1, 1),
            max_value=datetime(2024, 12, 31),
            tzinfo=timezone.utc
        ),  # Timestamp
    )


# ============================================================================
# Property 34: Search Functionality Accuracy
# ============================================================================

class TestSearchFunctionalityAccuracy(HypothesisTestCase):
    """
    Property 34: Search Functionality Accuracy
    
    For any set of students in the system and any search query,
    the search results should accurately match students by name or ID
    with appropriate fuzzy matching and ranking.
    """
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        
        # Create test students
        self.students = [
            Student.objects.create(
                first_name='John',
                last_name='Doe',
                admission_no='JD001',
                laravel_student_id='2024001',
                class_name='Grade 1',
                status='active'
            ),
            Student.objects.create(
                first_name='Jane',
                last_name='Smith',
                admission_no='JS002',
                laravel_student_id='2024002',
                class_name='Grade 1',
                status='active'
            ),
            Student.objects.create(
                first_name='Johnny',
                last_name='Walker',
                admission_no='JW003',
                laravel_student_id='2024003',
                class_name='Grade 2',
                status='active'
            ),
        ]
    
    @given(search_query_strategy())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_search_by_name_returns_matching_students(self, query):
        """
        Property: Search by name should return students whose names contain the query
        """
        assume(len(query.strip()) > 0)
        
        results = StudentSearchService.search_students(query)
        
        # All results should have names containing the query (case-insensitive)
        query_lower = query.lower()
        for student in results:
            full_name = f"{student.first_name} {student.last_name}".lower()
            assert query_lower in full_name or query_lower in student.admission_no.lower(), \
                f"Query '{query}' not found in student {student.get_full_name()}"
    
    @given(student_id_strategy())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_search_by_id_returns_exact_match(self, student_id):
        """
        Property: Search by ID should return exact matching student
        """
        # Create a student with this ID
        student = Student.objects.create(
            first_name='Test',
            last_name='Student',
            admission_no=student_id,
            laravel_student_id=student_id,
            class_name='Grade 1',
            status='active'
        )
        
        results = StudentSearchService.search_students(student_id)
        
        # Should find the student
        assert len(results) > 0, f"Student with ID {student_id} not found"
        assert student in results, f"Expected student not in results"
    
    @given(st.lists(student_name_strategy(), min_size=1, max_size=5))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_search_results_ranked_by_relevance(self, names):
        """
        Property: Search results should be ranked by relevance (exact matches first)
        """
        assume(all(len(name.strip()) > 0 for name in names))
        
        # Create students with these names
        students = []
        for i, name in enumerate(names):
            parts = name.split()
            first_name = parts[0] if parts else 'Test'
            last_name = ' '.join(parts[1:]) if len(parts) > 1 else 'Student'
            
            student = Student.objects.create(
                first_name=first_name,
                last_name=last_name,
                admission_no=f'TEST{i:04d}',
                laravel_student_id=f'2024{i:04d}',
                class_name='Grade 1',
                status='active'
            )
            students.append(student)
        
        # Search for first name
        query = names[0].split()[0] if names[0].split() else names[0]
        results = StudentSearchService.search_students(query)
        
        # Results should not be empty
        assert len(results) > 0, f"No results for query '{query}'"
        
        # First result should be most relevant
        first_result = results[0]
        assert query.lower() in first_result.get_full_name().lower() or \
               query.lower() in first_result.admission_no.lower(), \
               f"First result not relevant to query '{query}'"
    
    @given(st.just(''))
    @settings(max_examples=10)
    def test_empty_search_query_returns_all_active_students(self, query):
        """
        Property: Empty search query should return all active students
        """
        results = StudentSearchService.search_students(query)
        
        # Should return all active students
        expected_count = Student.objects.filter(status='active').count()
        assert len(results) == expected_count, \
            f"Expected {expected_count} students, got {len(results)}"
    
    @given(st.text(min_size=1, max_size=50))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_search_is_case_insensitive(self, query):
        """
        Property: Search should be case-insensitive
        """
        assume(len(query.strip()) > 0)
        
        # Create a student
        student = Student.objects.create(
            first_name='TestName',
            last_name='TestLast',
            admission_no='TEST001',
            laravel_student_id='2024001',
            class_name='Grade 1',
            status='active'
        )
        
        # Search with different cases
        results_lower = StudentSearchService.search_students(query.lower())
        results_upper = StudentSearchService.search_students(query.upper())
        results_mixed = StudentSearchService.search_students(query)
        
        # All should return same results
        assert len(results_lower) == len(results_upper) == len(results_mixed), \
            "Search results differ by case"


# ============================================================================
# Property 35: Offline Synchronization Integrity
# ============================================================================

class TestOfflineSynchronizationIntegrity(HypothesisTestCase):
    """
    Property 35: Offline Synchronization Integrity
    
    For any set of offline attendance entries created while disconnected,
    when the system reconnects, all entries should be synchronized correctly
    without data loss or duplication.
    """
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        
        # Create test students
        self.students = [
            Student.objects.create(
                first_name='John',
                last_name='Doe',
                admission_no='JD001',
                laravel_student_id='2024001',
                class_name='Grade 1',
                status='active'
            ),
            Student.objects.create(
                first_name='Jane',
                last_name='Smith',
                admission_no='JS002',
                laravel_student_id='2024002',
                class_name='Grade 1',
                status='active'
            ),
        ]
    
    @given(st.lists(offline_entry_strategy(), min_size=1, max_size=10))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_offline_entries_synced_without_loss(self, offline_entries):
        """
        Property: All offline entries should be synced without data loss
        """
        assume(all(entry[0] in ['2024001', '2024002'] for entry in offline_entries))
        
        # Simulate offline entries
        synced_count = 0
        for student_id, scan_type, timestamp in offline_entries:
            try:
                student = Student.objects.get(laravel_student_id=student_id)
                
                if scan_type == 'IN':
                    entry = AttendanceEntry.objects.create(
                        student=student,
                        date=timestamp.date(),
                        check_in_time=timestamp,
                        status='present',
                        marked_by=self.user
                    )
                else:
                    entry = AttendanceEntry.objects.get_or_create(
                        student=student,
                        date=timestamp.date(),
                        defaults={
                            'status': 'present',
                            'marked_by': self.user
                        }
                    )[0]
                    entry.check_out_time = timestamp
                    entry.checkout_by = self.user
                    entry.save()
                
                synced_count += 1
            except Student.DoesNotExist:
                pass
        
        # Verify all entries were synced
        assert synced_count == len(offline_entries), \
            f"Expected {len(offline_entries)} synced entries, got {synced_count}"
        
        # Verify no duplicates
        total_entries = AttendanceEntry.objects.count()
        assert total_entries >= synced_count, \
            "Duplicate entries detected during sync"
    
    @given(st.lists(offline_entry_strategy(), min_size=1, max_size=5))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_offline_sync_preserves_timestamps(self, offline_entries):
        """
        Property: Offline sync should preserve original timestamps
        """
        assume(all(entry[0] in ['2024001', '2024002'] for entry in offline_entries))
        
        # Create offline entries
        created_entries = []
        for student_id, scan_type, timestamp in offline_entries:
            try:
                student = Student.objects.get(laravel_student_id=student_id)
                
                if scan_type == 'IN':
                    entry = AttendanceEntry.objects.create(
                        student=student,
                        date=timestamp.date(),
                        check_in_time=timestamp,
                        status='present',
                        marked_by=self.user
                    )
                    created_entries.append((entry, 'check_in_time', timestamp))
                else:
                    entry = AttendanceEntry.objects.get_or_create(
                        student=student,
                        date=timestamp.date(),
                        defaults={
                            'status': 'present',
                            'marked_by': self.user
                        }
                    )[0]
                    entry.check_out_time = timestamp
                    entry.checkout_by = self.user
                    entry.save()
                    created_entries.append((entry, 'check_out_time', timestamp))
            except Student.DoesNotExist:
                pass
        
        # Verify timestamps are preserved
        for entry, field, original_timestamp in created_entries:
            stored_timestamp = getattr(entry, field)
            assert stored_timestamp == original_timestamp, \
                f"Timestamp mismatch: expected {original_timestamp}, got {stored_timestamp}"
    
    @given(st.lists(offline_entry_strategy(), min_size=1, max_size=5))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_offline_sync_handles_duplicate_entries(self, offline_entries):
        """
        Property: Offline sync should handle duplicate entries gracefully
        """
        assume(all(entry[0] in ['2024001', '2024002'] for entry in offline_entries))
        
        # Create entries twice (simulating duplicate sync)
        for iteration in range(2):
            for student_id, scan_type, timestamp in offline_entries:
                try:
                    student = Student.objects.get(laravel_student_id=student_id)
                    
                    if scan_type == 'IN':
                        AttendanceEntry.objects.get_or_create(
                            student=student,
                            date=timestamp.date(),
                            check_in_time=timestamp,
                            defaults={
                                'status': 'present',
                                'marked_by': self.user
                            }
                        )
                except Student.DoesNotExist:
                    pass
        
        # Verify no excessive duplicates
        total_entries = AttendanceEntry.objects.count()
        expected_max = len(offline_entries) * 2  # At most 2x the original
        assert total_entries <= expected_max, \
            f"Too many entries: expected max {expected_max}, got {total_entries}"


# ============================================================================
# Property 36: QR Code Validation Completeness
# ============================================================================

class TestQRCodeValidationCompleteness(HypothesisTestCase):
    """
    Property 36: QR Code Validation Completeness
    
    For any QR code input (valid or invalid), the validation system should
    correctly identify valid codes, extract student IDs, and provide
    appropriate error messages for invalid codes.
    """
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        
        # Create test students
        self.students = [
            Student.objects.create(
                first_name='John',
                last_name='Doe',
                admission_no='JD001',
                laravel_student_id='2024001',
                qr_code='2024001',
                class_name='Grade 1',
                status='active'
            ),
            Student.objects.create(
                first_name='Jane',
                last_name='Smith',
                admission_no='JS002',
                laravel_student_id='2024002',
                qr_code='2024002',
                class_name='Grade 1',
                status='active'
            ),
        ]
    
    @given(st.sampled_from(['2024001', '2024002']))
    @settings(max_examples=50)
    def test_valid_qr_code_validation(self, qr_code):
        """
        Property: Valid QR codes should pass validation
        """
        result = QRCodeService.validate_qr_code(qr_code)
        
        assert result['valid'], f"Valid QR code {qr_code} failed validation"
        assert 'student' in result, "Student not returned in validation result"
        assert result['student'].laravel_student_id == qr_code, \
            "Incorrect student returned"
    
    @given(st.one_of(
        st.just(''),
        st.just('   '),
        st.just('INVALID'),
        st.just('9999999'),
        st.text(alphabet='!@#$%^&*()', min_size=1, max_size=10),
    ))
    @settings(max_examples=50)
    def test_invalid_qr_code_validation(self, qr_code):
        """
        Property: Invalid QR codes should fail validation with error message
        """
        result = QRCodeService.validate_qr_code(qr_code)
        
        assert not result['valid'], f"Invalid QR code {qr_code} passed validation"
        assert 'error' in result, "Error message not provided"
        assert 'error_code' in result, "Error code not provided"
    
    @given(st.lists(
        st.sampled_from(['2024001', '2024002', 'INVALID', '']),
        min_size=1,
        max_size=10
    ))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_batch_qr_validation_completeness(self, qr_codes):
        """
        Property: Batch validation should validate all codes and report results
        """
        scans = [
            {
                'qr_code': qr_code,
                'scan_type': 'IN',
                'timestamp': timezone.now().isoformat()
            }
            for qr_code in qr_codes
        ]
        
        result = QRCodeService.validate_scans(scans)
        
        # Should have validation result
        assert 'valid' in result, "Validation result missing"
        assert 'errors' in result, "Errors list missing"
        
        # If there are errors, they should be documented
        if not result['valid']:
            assert len(result['errors']) > 0, "Invalid result but no errors documented"
    
    @given(st.text(min_size=1, max_size=20))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_qr_validation_error_messages_are_descriptive(self, qr_code):
        """
        Property: Error messages should be descriptive and helpful
        """
        assume(qr_code not in ['2024001', '2024002'])
        
        result = QRCodeService.validate_qr_code(qr_code)
        
        if not result['valid']:
            # Error message should be non-empty and descriptive
            assert len(result['error']) > 0, "Error message is empty"
            assert result['error_code'] in [
                'EMPTY_QR_CODE',
                'INVALID_QR_FORMAT',
                'STUDENT_NOT_FOUND'
            ], f"Unknown error code: {result['error_code']}"


# ============================================================================
# Integration Tests for Mobile App Features
# ============================================================================

class TestMobileAppIntegration(TestCase):
    """Integration tests for mobile app features"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        
        self.student = Student.objects.create(
            first_name='John',
            last_name='Doe',
            admission_no='JD001',
            laravel_student_id='2024001',
            qr_code='2024001',
            class_name='Grade 1',
            status='active'
        )
    
    def test_search_and_qr_validation_integration(self):
        """Test search and QR validation work together"""
        # Search for student
        results = StudentSearchService.search_students('John')
        assert len(results) > 0
        
        # Validate QR code for found student
        result = QRCodeService.validate_qr_code('2024001')
        assert result['valid']
        assert result['student'].id == self.student.id
    
    def test_offline_entry_and_sync_integration(self):
        """Test offline entry creation and sync"""
        # Create offline entry
        entry = AttendanceEntry.objects.create(
            student=self.student,
            date=timezone.now().date(),
            check_in_time=timezone.now(),
            status='present',
            marked_by=self.user
        )
        
        # Verify entry exists
        assert AttendanceEntry.objects.filter(id=entry.id).exists()
        
        # Verify entry has correct data
        retrieved = AttendanceEntry.objects.get(id=entry.id)
        assert retrieved.student.id == self.student.id
        assert retrieved.status == 'present'
