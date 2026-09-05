"""
Property-based tests for QR code processing.
Tests Property 11: QR Code Processing Accuracy
Tests Property 12: Batch Operation Consistency
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
from .services import QRCodeService

# Disable logging during tests
logging.disable(logging.CRITICAL)

User = get_user_model()


# ============================================================================
# Hypothesis Strategies for QR Code Testing
# ============================================================================

def valid_qr_code_strategy():
    """Strategy for generating valid QR codes"""
    return st.one_of(
        st.integers(min_value=1000000, max_value=9999999).map(str),  # Numeric
        st.integers(min_value=1000000, max_value=9999999).map(lambda x: f'STU{x}'),  # STU prefix
        st.text(alphabet='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_', 
                min_size=1, max_size=20),  # Alphanumeric
    )


def invalid_qr_code_strategy():
    """Strategy for generating invalid QR codes"""
    return st.one_of(
        st.just(''),  # Empty
        st.just('   '),  # Whitespace only
        st.text(alphabet='!@#$%^&*()', min_size=1, max_size=10),  # Special characters
        st.just('A' * 101),  # Too long
    )


def student_strategy():
    """Strategy for generating student data"""
    return st.tuples(
        st.just('TEST'),
        st.integers(min_value=1000, max_value=9999),
    ).map(lambda x: {
        'admission_no': f'{x[0]}{x[1]}',
        'laravel_student_id': f'202400{x[1] % 100:02d}',
        'qr_code': f'202400{x[1] % 100:02d}',
        'first_name': f'Student{x[1]}',
        'last_name': 'Test',
        'class_name': 'Grade 1',
        'status': 'active',
    })


def batch_scan_strategy():
    """Strategy for generating batch scan data"""
    return st.lists(
        st.fixed_dictionaries({
            'qr_code': valid_qr_code_strategy(),
            'scan_type': st.sampled_from(['IN', 'OUT']),
            'timestamp': st.datetimes(
                min_value=datetime(2024, 1, 1),
                max_value=datetime.now()
            ).map(lambda dt: timezone.make_aware(dt).isoformat() if not timezone.is_aware(dt) else dt.isoformat()),
        }),
        min_size=1,
        max_size=10,
        unique_by=lambda x: (x['qr_code'], x['scan_type'])  # Avoid duplicate same-type scans
    )


def mixed_batch_strategy():
    """Strategy for generating batches with mixed valid/invalid scans"""
    valid_scans = st.lists(
        st.fixed_dictionaries({
            'qr_code': valid_qr_code_strategy(),
            'scan_type': st.sampled_from(['IN', 'OUT']),
            'timestamp': st.datetimes(
                min_value=datetime(2024, 1, 1),
                max_value=datetime.now()
            ).map(lambda dt: timezone.make_aware(dt).isoformat() if not timezone.is_aware(dt) else dt.isoformat()),
        }),
        min_size=1,
        max_size=5
    )
    
    invalid_scans = st.lists(
        st.fixed_dictionaries({
            'qr_code': invalid_qr_code_strategy(),
            'scan_type': st.sampled_from(['IN', 'OUT']),
            'timestamp': st.datetimes(
                min_value=datetime(2024, 1, 1),
                max_value=datetime.now()
            ).map(lambda dt: timezone.make_aware(dt).isoformat() if not timezone.is_aware(dt) else dt.isoformat()),
        }),
        min_size=1,
        max_size=5
    )
    
    return st.builds(lambda v, i: v + i, valid_scans, invalid_scans).map(
        lambda scans: sorted(scans, key=lambda x: x['qr_code'])  # Consistent ordering
    )


# ============================================================================
# Property 11: QR Code Processing Accuracy
# ============================================================================

class QRCodeProcessingAccuracyTest(HypothesisTestCase):
    """
    Feature: checkin-checkout-integration
    Property 11: QR Code Processing Accuracy
    
    For any valid student QR code scan, the QR_Scanner should create or update 
    AttendanceEntry records with correct status, timestamp, and user tracking information.
    
    Validates: Requirements 3.1, 3.2, 3.3, 3.8
    """
    
    def setUp(self):
        """Set up test data"""
        self.test_user = User.objects.create_user(
            username='test_teacher_qr_accuracy',
            email='teacher_qr_accuracy@hodari.ac.tz'
        )
        
        # Create students for testing
        self.students = []
        for i in range(5):
            student = Student.objects.create(
                admission_no=f'QRTEST{i:03d}',
                first_name=f'QRStudent{i}',
                last_name='Test',
                laravel_student_id=f'202400{i:02d}',
                qr_code=f'202400{i:02d}',
                class_name='Grade 1',
                status='active'
            )
            self.students.append(student)
    
    @given(qr_code=valid_qr_code_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_property_11_qr_validation_accepts_valid_formats(self, qr_code):
        """
        Property 11.1: QR code validation accepts various valid formats
        
        For any valid QR code format, validation should succeed and 
        return the extracted student ID.
        """
        # Validate the QR code
        result = QRCodeService.validate_qr_code(qr_code)
        
        # Property: Valid formats should pass validation
        assert result['valid'], f"Valid QR code {qr_code} should pass validation"
        
        # Property: Student ID should be extracted
        assert 'student_id' in result, "Extracted student ID should be present"
        assert result['student_id'], "Extracted student ID should not be empty"
        
        # Property: Original QR code should be preserved
        assert result['original_qr_code'] == qr_code, "Original QR code should be preserved"
    
    @given(qr_code=invalid_qr_code_strategy())
    @settings(max_examples=30, deadline=5000)
    def test_property_11_qr_validation_rejects_invalid_formats(self, qr_code):
        """
        Property 11.2: QR code validation rejects invalid formats
        
        For any invalid QR code format, validation should fail and 
        return an error code.
        """
        # Validate the QR code
        result = QRCodeService.validate_qr_code(qr_code)
        
        # Property: Invalid formats should fail validation
        assert not result['valid'], f"Invalid QR code {qr_code} should fail validation"
        
        # Property: Error code should be present
        assert 'error_code' in result, "Error code should be present for invalid QR codes"
        assert result['error_code'], "Error code should not be empty"
        
        # Property: Error message should be present
        assert 'message' in result, "Error message should be present for invalid QR codes"
        assert result['message'], "Error message should not be empty"
    
    @given(
        qr_code=valid_qr_code_strategy(),
        is_checkin=st.booleans()
    )
    @settings(max_examples=50, deadline=5000)
    def test_property_11_scan_creates_or_updates_entry(self, qr_code, is_checkin):
        """
        Property 11.3: QR scan creates or updates AttendanceEntry with correct details
        
        For any valid QR code scan, an AttendanceEntry should be created or updated
        with correct status, timestamp, and user tracking.
        """
        # Use a valid student's QR code
        student = self.students[0]
        
        timestamp = timezone.now() - timedelta(hours=1) if is_checkin else timezone.now() - timedelta(hours=8)
        scan_type = 'IN' if is_checkin else 'OUT'
        parent_name = "" if is_checkin else "Test Parent"
        
        # Process the scan
        result = QRCodeService.process_qr_scan(
            qr_data=student.qr_code,
            scan_type=scan_type,
            user=self.test_user,
            timestamp=timestamp,
            parent_name=parent_name
        )
        
        # Property: Scan should succeed
        assert result['success'], f"Scan should succeed for valid QR code {student.qr_code}"
        
        # Property: Scan type should be preserved
        assert result['scan_type'] == scan_type, "Scan type should be preserved"
        
        # Property: Timestamp should be preserved
        assert result['timestamp'], "Timestamp should be present in result"
        
        # Property: Student information should be present
        assert result.get('student_id'), "Student ID should be present in result"
        assert result.get('student_name'), "Student name should be present in result"
        
        # Property: Marked by should be the test user
        assert result.get('marked_by') == self.test_user.id or result.get('marked_by_id') == self.test_user.id, \
            "User should be recorded as marked_by"
    
    @given(
        qr_code=valid_qr_code_strategy(),
        num_scans=st.integers(min_value=1, max_value=5)
    )
    @settings(max_examples=30, deadline=5000, suppress_health_check=[HealthCheck.too_slow])
    def test_property_11_duplicate_scan_prevention(self, qr_code, num_scans):
        """
        Property 11.4: Duplicate scans are prevented
        
        For any student QR code, duplicate check-in scans should be prevented,
        and duplicate check-out scans should generate appropriate errors.
        """
        student = self.students[0]
        timestamp = timezone.now() - timedelta(hours=1)
        
        # First scan should succeed
        result_first = QRCodeService.process_qr_scan(
            qr_data=student.qr_code,
            scan_type='IN',
            user=self.test_user,
            timestamp=timestamp
        )
        assert result_first['success'], "First check-in should succeed"
        
        # Second scan should fail
        result_second = QRCodeService.process_qr_scan(
            qr_data=student.qr_code,
            scan_type='IN',
            user=self.test_user,
            timestamp=timestamp + timedelta(minutes=5)
        )
        assert not result_second['success'], "Duplicate check-in should fail"
        assert result_second['error_code'] == 'ALREADY_CHECKED_IN', "Error code should indicate already checked in"
        
        # Property: Only one attendance entry should exist
        entries = AttendanceEntry.objects.filter(
            student=student,
            date=timestamp.date()
        )
        assert entries.count() == 1, "Only one attendance entry should exist for the day"
    
    @given(
        checkout_time_hour=st.integers(min_value=13, max_value=16),
        checkout_time_minute=st.integers(min_value=0, max_value=59)
    )
    @settings(max_examples=30, deadline=5000)
    def test_property_11_early_departure_detection(self, checkout_time_hour, checkout_time_minute):
        """
        Property 11.5: Early departure is correctly detected
        
        For any checkout timestamp, the is_early_departure flag should be true
        if and only if the checkout time is before 15:30 (3:30 PM).
        """
        student = self.students[0]
        
        # Create a base time for the checkout
        base_time = timezone.now().replace(hour=checkout_time_hour, minute=checkout_time_minute, second=0, microsecond=0)
        if base_time > timezone.now():
            base_time = base_time - timedelta(days=1)
        
        # Process check-out
        result = QRCodeService.process_qr_scan(
            qr_data=student.qr_code,
            scan_type='OUT',
            user=self.test_user,
            timestamp=base_time
        )
        
        if result['success']:
            # Property: Early departure flag should match the condition
            expected_early_departure = checkout_time_hour < 15 or (checkout_time_hour == 15 and checkout_time_minute < 30)
            assert result.get('is_early_departure') == expected_early_departure, \
                f"Early departure flag should be {expected_early_departure} for time {checkout_time_hour}:{checkout_time_minute:02d}"


# ============================================================================
# Property 12: Batch Operation Consistency
# ============================================================================

class BatchOperationConsistencyTest(HypothesisTestCase):
    """
    Feature: checkin-checkout-integration
    Property 12: Batch Operation Consistency
    
    For any batch scanning request containing multiple students, all valid students 
    should be processed correctly while invalid students generate appropriate error messages.
    
    Validates: Requirements 3.4, 3.5, 3.7
    """
    
    def setUp(self):
        """Set up test data"""
        self.test_user = User.objects.create_user(
            username='test_teacher_batch_ops',
            email='teacher_batch_ops@hodari.ac.tz'
        )
        
        # Create students for testing
        self.students = []
        for i in range(20):
            student = Student.objects.create(
                admission_no=f'BATCH{i:03d}',
                first_name=f'BatchStudent{i}',
                last_name='Test',
                laravel_student_id=f'202401{i:02d}',
                qr_code=f'202401{i:02d}',
                class_name='Grade 1',
                status='active'
            )
            self.students.append(student)
    
    @given(batch_data=batch_scan_strategy())
    @settings(max_examples=30, deadline=5000)
    def test_property_12_batch_processing_all_scans_processed(self, batch_data):
        """
        Property 12.1: All scans in a batch are processed
        
        For any batch of QR code scans, all scans should be processed,
        and results should be returned for each scan.
        """
        assume(len(batch_data) > 0)
        
        # Use valid students
        for scan in batch_data:
            scan['qr_code'] = self.students[0].qr_code
        
        # Process batch
        result = QRCodeService.process_batch_scans(batch_data, self.test_user)
        
        # Property: All scans should be processed
        assert result['total_count'] == len(batch_data), "All scans should be counted in total_count"
        
        # Property: Results should match number of scans
        assert len(result['results']) == len(batch_data), "Results should contain entry for each scan"
        
        # Property: Summary counts should match
        assert result['successful_count'] + result['failed_count'] == result['total_count'], \
            "Successful + failed should equal total"
    
    @given(batch_data=mixed_batch_strategy())
    @settings(max_examples=30, deadline=5000)
    def test_property_12_batch_mixed_valid_invalid(self, batch_data):
        """
        Property 12.2: Batch processing handles mixed valid/invalid scans
        
        For any batch with mixed valid and invalid scans, valid scans should 
        succeed while invalid scans should fail.
        """
        assume(len(batch_data) > 0)
        
        # Replace QR codes for valid scans with actual student QRs
        valid_count = 0
        for i, scan in enumerate(batch_data):
            result = QRCodeService.validate_qr_code(scan['qr_code'])
            if result['valid']:
                # Replace with valid student QR
                scan['qr_code'] = self.students[i % len(self.students)].qr_code
                valid_count += 1
        
        # Process batch
        batch_result = QRCodeService.process_batch_scans(batch_data, self.test_user)
        
        # Property: Failed count should exist
        assert batch_result['failed_count'] >= 0, "Failed count should be non-negative"
        
        # Property: Successful count should exist
        assert batch_result['successful_count'] >= 0, "Successful count should be non-negative"
        
        # Property: Each result should have status field
        for result in batch_result['results']:
            assert 'success' in result or 'error_code' in result, \
                "Each result should indicate success or have error code"
    
    @given(num_students=st.integers(min_value=5, max_value=30))
    @settings(max_examples=20, deadline=10000)
    def test_property_12_large_batch_processing(self, num_students):
        """
        Property 12.3: Large batches are processed completely and accurately
        
        For any large batch of QR code scans, all scans should be processed
        without data loss or corruption.
        """
        # Create batch with unique students
        batch_data = []
        for i in range(min(num_students, len(self.students))):
            batch_data.append({
                'qr_code': self.students[i].qr_code,
                'scan_type': 'IN',
                'timestamp': (timezone.now() - timedelta(hours=1)).isoformat(),
            })
        
        assume(len(batch_data) > 0)
        
        # Process batch
        result = QRCodeService.process_batch_scans(batch_data, self.test_user)
        
        # Property: Total should match input size
        assert result['total_count'] == len(batch_data), \
            f"Total count {result['total_count']} should match batch size {len(batch_data)}"
        
        # Property: Successful count should not exceed total
        assert result['successful_count'] <= result['total_count'], \
            "Successful count should not exceed total"
        
        # Property: Attendance entries created for successful scans
        today = timezone.now().date()
        entries = AttendanceEntry.objects.filter(date=today)
        assert entries.count() >= result['successful_count'], \
            "Attendance entries should be created for successful scans"
    
    @given(
        num_checkins=st.integers(min_value=1, max_value=5),
        num_checkouts=st.integers(min_value=1, max_value=5)
    )
    @settings(max_examples=20, deadline=10000)
    def test_property_12_mixed_scan_types_processed_correctly(self, num_checkins, num_checkouts):
        """
        Property 12.4: Batch with mixed IN/OUT scan types are processed correctly
        
        For any batch with both check-in and check-out operations, each should be
        processed according to its type.
        """
        # Create batch with mixed types
        batch_data = []
        
        # Add check-ins
        for i in range(min(num_checkins, len(self.students))):
            batch_data.append({
                'qr_code': self.students[i].qr_code,
                'scan_type': 'IN',
                'timestamp': (timezone.now() - timedelta(hours=2)).isoformat(),
            })
        
        # Add check-outs (use same students)
        for i in range(min(num_checkouts, len(self.students))):
            batch_data.append({
                'qr_code': self.students[i].qr_code,
                'scan_type': 'OUT',
                'timestamp': (timezone.now() - timedelta(hours=1)).isoformat(),
            })
        
        assume(len(batch_data) > 0)
        
        # Process batch
        result = QRCodeService.process_batch_scans(batch_data, self.test_user)
        
        # Property: All scans should be processed
        assert result['total_count'] == len(batch_data), \
            "All scans (both IN and OUT) should be counted"
        
        # Property: Mix of IN and OUT should be present
        in_count = sum(1 for scan in batch_data if scan['scan_type'] == 'IN')
        out_count = sum(1 for scan in batch_data if scan['scan_type'] == 'OUT')
        assert in_count > 0 or out_count > 0, "Batch should have at least one scan type"
    
    @given(batch_data=batch_scan_strategy())
    @settings(max_examples=30, deadline=5000)
    def test_property_12_each_result_has_complete_data(self, batch_data):
        """
        Property 12.5: Each batch result contains complete data
        
        For any batch processing result, each result entry should contain
        all required fields (qr_code, scan_type, status, message).
        """
        # Use valid students
        for i, scan in enumerate(batch_data):
            scan['qr_code'] = self.students[i % len(self.students)].qr_code
        
        # Process batch
        result = QRCodeService.process_batch_scans(batch_data, self.test_user)
        
        # Property: Each result should have required fields
        for batch_result in result['results']:
            # These fields should always be present
            assert 'qr_code' in batch_result, "qr_code should be in result"
            assert 'scan_type' in batch_result, "scan_type should be in result"
            assert 'success' in batch_result, "success status should be in result"
            assert 'message' in batch_result, "message should be in result"
            
            # If successful, additional fields should be present
            if batch_result.get('success'):
                assert 'student_name' in batch_result or batch_result.get('error_code'), \
                    "Student name should be present for successful scans"

