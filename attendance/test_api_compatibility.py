"""
Property-based tests for API compatibility (Task 4.5).
Tests universal properties that should hold for all valid API requests and responses.

Feature: checkin-checkout-integration
Property 8: API Format Compatibility
Property 9: Attendance Record Creation Consistency

Validates: Requirements 2.2, 2.3, 2.4, 2.5, 2.6
"""
import logging
from datetime import datetime, date, time, timedelta
from typing import Dict, List, Any
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status
from hypothesis import given, strategies as st, settings, assume, example
from hypothesis.extra.django import TestCase as HypothesisTestCase

from students.models import Student
from users.models import User
from .models import AttendanceEntry
from .services import AttendanceService

# Disable logging during tests
logging.disable(logging.CRITICAL)


# Test data generation strategies
@st.composite
def student_id_strategy(draw):
    """Generate valid student IDs (both Django and Laravel formats)"""
    return draw(st.one_of(
        st.integers(min_value=1, max_value=99999).map(str),  # Django format
        st.text(alphabet='0123456789', min_size=4, max_size=10)  # Laravel format
    ))


@st.composite
def timestamp_strategy(draw):
    """Generate realistic school day timestamps"""
    base_date = draw(st.dates(
        min_value=date(2024, 1, 1),
        max_value=date(2024, 12, 31)
    ))
    
    # School hours: 7:00 AM to 6:00 PM
    hour = draw(st.integers(min_value=7, max_value=18))
    minute = draw(st.integers(min_value=0, max_value=59))
    second = draw(st.integers(min_value=0, max_value=59))
    
    dt = datetime.combine(base_date, time(hour, minute, second))
    return dt.isoformat() + 'Z'


@st.composite
def checkin_request_strategy(draw):
    """Generate valid Laravel-format check-in requests"""
    num_students = draw(st.integers(min_value=1, max_value=10))
    
    students = []
    for _ in range(num_students):
        students.append({
            'id': draw(student_id_strategy()),
            'checkin_time': draw(timestamp_strategy())
        })
    
    return {'students': students}


@st.composite
def checkout_request_strategy(draw):
    """Generate valid Laravel-format check-out requests"""
    num_students = draw(st.integers(min_value=1, max_value=10))
    
    students = []
    for _ in range(num_students):
        students.append({
            'id': draw(student_id_strategy()),
            'checkout_time': draw(timestamp_strategy()),
            'parent_name': draw(st.one_of(
                st.none(),
                st.text(alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Pd')), min_size=3, max_size=100)
            )),
            'reason': draw(st.one_of(
                st.none(),
                st.text(min_size=0, max_size=200)
            ))
        })
    
    return {'students': students}


@st.composite
def laravel_response_strategy(draw):
    """Generate expected Laravel-format response structure"""
    return {
        'message': draw(st.text(min_size=1, max_size=200)),
        'data': draw(st.lists(
            st.fixed_dictionaries({
                'student_id': draw(student_id_strategy()),
                'status': draw(st.sampled_from(['success', 'error'])),
                'message': draw(st.text(min_size=1, max_size=200))
            }),
            min_size=0,
            max_size=10
        )),
        'summary': st.fixed_dictionaries({
            'total': draw(st.integers(min_value=0, max_value=100)),
            'successful': draw(st.integers(min_value=0, max_value=100)),
            'failed': draw(st.integers(min_value=0, max_value=100))
        })
    }


class APIFormatCompatibilityPropertyTests(APITestCase):
    """
    Property-based tests for API format compatibility.
    
    **Property 8: API Format Compatibility**
    For any valid Laravel-format checkin/checkout request, the API_Gateway should accept
    the request and return a response containing status, message, and data fields in
    Laravel-compatible format.
    
    **Validates: Requirements 2.2, 2.3, 2.4**
    """
    
    def setUp(self):
        """Set up test environment"""
        self.user = User.objects.create_user(
            username='api_test_user',
            email='api@hodari.ac.tz',
            password='testpass123'
        )
        
        # Create test students
        self.students = []
        for i in range(5):
            student = Student.objects.create(
                admission_no=f'API{i:03d}',
                first_name=f'Student{i}',
                last_name='Test',
                laravel_student_id=f'2024{i:03d}',
                class_name='Grade 1',
                status='active'
            )
            self.students.append(student)
        
        # Authenticate the client
        self.client.force_authenticate(user=self.user)
    
    @given(request_data=checkin_request_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_api_format_compatibility_checkin(self, request_data):
        """
        Feature: checkin-checkout-integration, Property 8: API Format Compatibility
        
        For any valid Laravel-format checkin request, the API should accept the request
        and return a response with status, message, and data fields in Laravel format.
        
        **Validates: Requirements 2.2, 2.3**
        """
        # Skip if no students in request
        assume(len(request_data['students']) > 0)
        
        # Ensure at least one student exists in database
        assume(len(self.students) > 0)
        
        # Replace student IDs with valid ones from database
        for i, student_data in enumerate(request_data['students']):
            if i < len(self.students):
                student_data['id'] = self.students[i].laravel_student_id
        
        # Make API request
        response = self.client.post(
            '/attendance/api/checkin/',
            request_data,
            format='json'
        )
        
        # **PROPERTY ASSERTION 1: Response status code is valid**
        self.assertIn(
            response.status_code,
            [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST, status.HTTP_500_INTERNAL_SERVER_ERROR],
            f"Unexpected HTTP status code: {response.status_code}"
        )
        
        # **PROPERTY ASSERTION 2: Response has required Laravel format fields**
        response_data = response.json()
        
        self.assertIn('message', response_data, "Response missing 'message' field")
        self.assertIsInstance(response_data['message'], str, "'message' field must be string")
        self.assertGreater(len(response_data['message']), 0, "'message' field cannot be empty")
        
        # **PROPERTY ASSERTION 3: Response has data array**
        self.assertIn('data', response_data, "Response missing 'data' field")
        self.assertIsInstance(response_data['data'], list, "'data' field must be array")
        
        # **PROPERTY ASSERTION 4: Each data item has required fields**
        for item in response_data['data']:
            self.assertIn('student_id', item, "Data item missing 'student_id'")
            self.assertIn('status', item, "Data item missing 'status'")
            self.assertIn('message', item, "Data item missing 'message'")
            
            # Status must be 'success' or 'error'
            self.assertIn(
                item['status'],
                ['success', 'error'],
                f"Invalid status value: {item['status']}"
            )
        
        # **PROPERTY ASSERTION 5: Response has summary**
        self.assertIn('summary', response_data, "Response missing 'summary' field")
        summary = response_data['summary']
        
        self.assertIn('total', summary, "Summary missing 'total'")
        self.assertIn('successful', summary, "Summary missing 'successful'")
        self.assertIn('failed', summary, "Summary missing 'failed'")
        
        # **PROPERTY ASSERTION 6: Summary counts are consistent**
        self.assertEqual(
            summary['total'],
            len(response_data['data']),
            "Summary total doesn't match data array length"
        )
        
        self.assertEqual(
            summary['successful'] + summary['failed'],
            summary['total'],
            "Summary counts don't add up correctly"
        )
        
        # **PROPERTY ASSERTION 7: Data array length matches request**
        self.assertEqual(
            len(response_data['data']),
            len(request_data['students']),
            "Response data count doesn't match request count"
        )
        
        # **PROPERTY ASSERTION 8: Student IDs in response match request**
        response_ids = [item['student_id'] for item in response_data['data']]
        request_ids = [s['id'] for s in request_data['students']]
        
        self.assertEqual(
            set(response_ids),
            set(request_ids),
            "Response student IDs don't match request IDs"
        )
    
    @given(request_data=checkout_request_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_api_format_compatibility_checkout(self, request_data):
        """
        Feature: checkin-checkout-integration, Property 8: API Format Compatibility
        
        For any valid Laravel-format checkout request, the API should accept the request
        and return a response with status, message, and data fields in Laravel format.
        
        **Validates: Requirements 2.2, 2.3**
        """
        # Skip if no students in request
        assume(len(request_data['students']) > 0)
        assume(len(self.students) > 0)
        
        # Replace student IDs with valid ones
        for i, student_data in enumerate(request_data['students']):
            if i < len(self.students):
                student_data['id'] = self.students[i].laravel_student_id
                # First check in the student
                AttendanceService.checkin_student(
                    student_data['id'],
                    self.user,
                    laravel_format=True
                )
        
        # Make API request
        response = self.client.post(
            '/attendance/api/checkout/',
            request_data,
            format='json'
        )
        
        # **PROPERTY ASSERTION 1: Response status code is valid**
        self.assertIn(
            response.status_code,
            [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST, status.HTTP_500_INTERNAL_SERVER_ERROR],
            f"Unexpected HTTP status code: {response.status_code}"
        )
        
        # **PROPERTY ASSERTION 2: Response has required Laravel format fields**
        response_data = response.json()
        
        self.assertIn('message', response_data, "Response missing 'message' field")
        self.assertIn('data', response_data, "Response missing 'data' field")
        self.assertIn('summary', response_data, "Response missing 'summary' field")
        
        # **PROPERTY ASSERTION 3: Checkout-specific fields in response**
        for item in response_data['data']:
            if item['status'] == 'success':
                # Successful checkout should have these fields
                self.assertIn('check_out_time', item, "Success checkout missing 'check_out_time'")
                self.assertIn('is_early_departure', item, "Success checkout missing 'is_early_departure'")
                self.assertIsInstance(item['is_early_departure'], bool, "'is_early_departure' must be boolean")
    
    def _get_token(self):
        """Helper to get authentication token"""
        # Already authenticated in setUp
        return 'dummy_token'


class AttendanceRecordCreationConsistencyPropertyTests(APITestCase):
    """
    Property-based tests for attendance record creation consistency.
    
    **Property 9: Attendance Record Creation Consistency**
    For any valid checkin request processed by the API_Gateway, a corresponding Django
    AttendanceEntry record should be created or updated with the appropriate status
    and timestamp.
    
    **Validates: Requirements 2.5, 2.6**
    """
    
    def setUp(self):
        """Set up test environment"""
        self.user = User.objects.create_user(
            username='record_test_user',
            email='record@hodari.ac.tz',
            password='testpass123'
        )
        
        # Create test students
        self.students = []
        for i in range(5):
            student = Student.objects.create(
                admission_no=f'REC{i:03d}',
                first_name=f'RecStudent{i}',
                last_name='Test',
                laravel_student_id=f'2025{i:03d}',
                class_name='Grade 2',
                status='active'
            )
            self.students.append(student)
        
        # Authenticate the client
        self.client.force_authenticate(user=self.user)
    
    @given(request_data=checkin_request_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_attendance_record_creation_on_checkin(self, request_data):
        """
        Feature: checkin-checkout-integration, Property 9: Attendance Record Creation Consistency
        
        For any valid checkin request, a corresponding AttendanceEntry record should be
        created with appropriate status and check_in_time.
        
        **Validates: Requirements 2.5**
        """
        assume(len(request_data['students']) > 0)
        assume(len(self.students) > 0)
        
        # Replace student IDs with valid ones
        for i, student_data in enumerate(request_data['students']):
            if i < len(self.students):
                student_data['id'] = self.students[i].laravel_student_id
        
        # Clear any existing attendance records
        AttendanceEntry.objects.all().delete()
        
        # Make API request
        response = self.client.post(
            '/attendance/api/checkin/',
            request_data,
            format='json'
        )
        
        response_data = response.json()
        
        # **PROPERTY ASSERTION 1: For each successful checkin, an AttendanceEntry exists**
        for item in response_data['data']:
            if item['status'] == 'success':
                student_id = item['student_id']
                
                # Find the student
                student = Student.objects.filter(laravel_student_id=student_id).first()
                self.assertIsNotNone(student, f"Student {student_id} not found")
                
                # Check that an AttendanceEntry exists for today
                today = timezone.now().date()
                entry = AttendanceEntry.objects.filter(
                    student=student,
                    date=today
                ).first()
                
                self.assertIsNotNone(
                    entry,
                    f"No AttendanceEntry created for student {student_id} on {today}"
                )
                
                # **PROPERTY ASSERTION 2: check_in_time is set**
                self.assertIsNotNone(
                    entry.check_in_time,
                    f"check_in_time not set for student {student_id}"
                )
                
                # **PROPERTY ASSERTION 3: status is 'present' or 'late'**
                self.assertIn(
                    entry.status,
                    ['present', 'late'],
                    f"Invalid status '{entry.status}' for student {student_id}"
                )
                
                # **PROPERTY ASSERTION 4: marked_by is set to the current user**
                self.assertEqual(
                    entry.marked_by,
                    self.user,
                    f"marked_by not set correctly for student {student_id}"
                )
                
                # **PROPERTY ASSERTION 5: class_name is preserved**
                self.assertEqual(
                    entry.class_name,
                    student.class_name,
                    f"class_name not preserved for student {student_id}"
                )
    
    @given(request_data=checkout_request_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_attendance_record_update_on_checkout(self, request_data):
        """
        Feature: checkin-checkout-integration, Property 9: Attendance Record Creation Consistency
        
        For any valid checkout request, the corresponding AttendanceEntry record should be
        updated with check_out_time and early departure flag.
        
        **Validates: Requirements 2.6**
        """
        assume(len(request_data['students']) > 0)
        assume(len(self.students) > 0)
        
        # Replace student IDs with valid ones and check them in first
        for i, student_data in enumerate(request_data['students']):
            if i < len(self.students):
                student_data['id'] = self.students[i].laravel_student_id
                # Check in the student first
                AttendanceService.checkin_student(
                    student_data['id'],
                    self.user,
                    laravel_format=True
                )
        
        # Make API request
        response = self.client.post(
            '/attendance/api/checkout/',
            request_data,
            format='json'
        )
        
        response_data = response.json()
        
        # **PROPERTY ASSERTION 1: For each successful checkout, check_out_time is set**
        for item in response_data['data']:
            if item['status'] == 'success':
                student_id = item['student_id']
                
                # Find the student
                student = Student.objects.filter(laravel_student_id=student_id).first()
                self.assertIsNotNone(student, f"Student {student_id} not found")
                
                # Check that the AttendanceEntry has check_out_time
                today = timezone.now().date()
                entry = AttendanceEntry.objects.filter(
                    student=student,
                    date=today
                ).first()
                
                self.assertIsNotNone(entry, f"No AttendanceEntry for student {student_id}")
                
                self.assertIsNotNone(
                    entry.check_out_time,
                    f"check_out_time not set for student {student_id}"
                )
                
                # **PROPERTY ASSERTION 2: is_early_departure is set correctly**
                self.assertIsInstance(
                    entry.is_early_departure,
                    bool,
                    f"is_early_departure not boolean for student {student_id}"
                )
                
                # **PROPERTY ASSERTION 3: parent_name is preserved if provided**
                if 'parent_name' in item and item['parent_name']:
                    self.assertEqual(
                        entry.parent_name,
                        item['parent_name'],
                        f"parent_name not preserved for student {student_id}"
                    )
                
                # **PROPERTY ASSERTION 4: reason is preserved if provided**
                if 'reason' in item and item['reason']:
                    self.assertEqual(
                        entry.reason,
                        item['reason'],
                        f"reason not preserved for student {student_id}"
                    )
    
    @given(request_data=checkin_request_strategy())
    @settings(max_examples=30, deadline=5000)
    def test_attendance_record_uniqueness_per_day(self, request_data):
        """
        Feature: checkin-checkout-integration, Property 9: Attendance Record Creation Consistency
        
        For any student, there should be at most one AttendanceEntry per day.
        Multiple checkins on the same day should update the existing record, not create new ones.
        
        **Validates: Requirements 2.5, 2.6**
        """
        assume(len(request_data['students']) > 0)
        assume(len(self.students) > 0)
        
        # Use only the first student to test uniqueness
        request_data['students'] = [request_data['students'][0]]
        request_data['students'][0]['id'] = self.students[0].laravel_student_id
        
        # Clear attendance records
        AttendanceEntry.objects.all().delete()
        
        # Make first checkin request
        response1 = self.client.post(
            '/attendance/api/checkin/',
            request_data,
            format='json'
        )
        
        # Count records after first checkin
        today = timezone.now().date()
        count_after_first = AttendanceEntry.objects.filter(
            student=self.students[0],
            date=today
        ).count()
        
        # **PROPERTY ASSERTION 1: First checkin creates exactly one record**
        self.assertEqual(
            count_after_first,
            1,
            f"First checkin should create exactly 1 record, got {count_after_first}"
        )
        
        # Make second checkin request (should fail with duplicate error)
        response2 = self.client.post(
            '/attendance/api/checkin/',
            request_data,
            format='json'
        )
        
        # Count records after second checkin
        count_after_second = AttendanceEntry.objects.filter(
            student=self.students[0],
            date=today
        ).count()
        
        # **PROPERTY ASSERTION 2: Second checkin doesn't create new record**
        self.assertEqual(
            count_after_second,
            count_after_first,
            f"Second checkin should not create new record, count changed from {count_after_first} to {count_after_second}"
        )
        
        # **PROPERTY ASSERTION 3: Second checkin returns error**
        response2_data = response2.json()
        second_item = response2_data['data'][0]
        
        self.assertEqual(
            second_item['status'],
            'error',
            "Second checkin should return error status"
        )
        
        self.assertIn(
            'already checked in',
            second_item['message'].lower(),
            "Error message should indicate student already checked in"
        )
