"""
Comprehensive tests for task 4.2: Implement attendance processing logic.
Tests the enhanced attendance processing logic for check-in/check-out operations,
status derivation, and early departure detection.
"""
import logging
from datetime import datetime, date, time
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status

from students.models import Student
from .models import AttendanceEntry
from .services import AttendanceService

# Disable logging during tests
logging.disable(logging.CRITICAL)

User = get_user_model()


def _past_weekday() -> date:
    """Return the most recent weekday (Mon-Fri) before today."""
    d = timezone.now().date() - timezone.timedelta(days=1)
    while d.weekday() >= 5:  # weekend → go back more
        d -= timezone.timedelta(days=1)
    return d


class AttendanceProcessingLogicTest(TestCase):
    """Test enhanced attendance processing logic"""
    
    def setUp(self):
        """Set up test data"""
        # Create test user
        self.test_user = User.objects.create_user(
            username='test_teacher',
            email='teacher@hodari.ac.tz',
            first_name='Test',
            last_name='Teacher'
        )
        
        # Create test students
        self.student1 = Student.objects.create(
            admission_no='TEST001',
            first_name='John',
            last_name='Doe',
            laravel_student_id='2024001',
            class_name='Grade 1',
            status='active'
        )
        
        self.student2 = Student.objects.create(
            admission_no='TEST002',
            first_name='Jane',
            last_name='Smith',
            laravel_student_id='2024002',
            class_name='Grade 2',
            status='active'
        )
        
        self.inactive_student = Student.objects.create(
            admission_no='TEST003',
            first_name='Inactive',
            last_name='Student',
            laravel_student_id='2024003',
            class_name='Grade 1',
            status='inactive'
        )
    
    def test_enhanced_checkin_validation(self):
        """Test enhanced check-in validation logic"""
        # Test missing student ID
        result = AttendanceService.checkin_student(
            student_id=None,
            user=self.test_user
        )
        self.assertFalse(result['success'])
        self.assertEqual(result['error_code'], 'MISSING_STUDENT_ID')
        
        # Test missing user
        result = AttendanceService.checkin_student(
            student_id='TEST001',
            user=None
        )
        self.assertFalse(result['success'])
        self.assertEqual(result['error_code'], 'MISSING_USER')
        
        # Test future timestamp
        future_time = timezone.now() + timezone.timedelta(hours=1)
        result = AttendanceService.checkin_student(
            student_id='TEST001',
            user=self.test_user,
            timestamp=future_time
        )
        self.assertFalse(result['success'])
        self.assertEqual(result['error_code'], 'FUTURE_TIMESTAMP')
        
        # Test student not found
        result = AttendanceService.checkin_student(
            student_id='NONEXISTENT',
            user=self.test_user,
            laravel_format=True
        )
        self.assertFalse(result['success'])
        self.assertEqual(result['error_code'], 'STUDENT_NOT_FOUND')
        
        # Test inactive student
        result = AttendanceService.checkin_student(
            student_id='2024003',
            user=self.test_user,
            laravel_format=True
        )
        self.assertFalse(result['success'])
        self.assertEqual(result['error_code'], 'STUDENT_NOT_FOUND')
    
    def test_status_derivation_logic(self):
        """Test attendance status derivation based on check-in time"""
        # Test early check-in (7:30 AM) - should be 'present'
        early_time = timezone.now().replace(hour=7, minute=30, second=0, microsecond=0)
        result = AttendanceService.checkin_student(
            student_id='TEST001',
            user=self.test_user,
            timestamp=early_time
        )
        self.assertTrue(result['success'])
        self.assertEqual(result['status'], 'present')
        
        # Clear the entry for next test
        AttendanceEntry.objects.filter(student=self.student1).delete()
        
        # Test on-time check-in (8:00 AM) - should be 'present'
        ontime_time = timezone.now().replace(hour=8, minute=0, second=0, microsecond=0)
        result = AttendanceService.checkin_student(
            student_id='TEST001',
            user=self.test_user,
            timestamp=ontime_time
        )
        self.assertTrue(result['success'])
        self.assertEqual(result['status'], 'present')
        
        # Clear the entry for next test
        AttendanceEntry.objects.filter(student=self.student1).delete()
        
        # Test late check-in (8:45 AM) - should be 'late'
        late_time = timezone.now().replace(hour=8, minute=45, second=0, microsecond=0)
        result = AttendanceService.checkin_student(
            student_id='TEST001',
            user=self.test_user,
            timestamp=late_time
        )
        self.assertTrue(result['success'])
        self.assertEqual(result['status'], 'late')
    
    def test_duplicate_checkin_prevention(self):
        """Test prevention of duplicate check-ins"""
        # First check-in should succeed
        result1 = AttendanceService.checkin_student(
            student_id='TEST001',
            user=self.test_user
        )
        self.assertTrue(result1['success'])
        
        # Second check-in should fail with appropriate error
        result2 = AttendanceService.checkin_student(
            student_id='TEST001',
            user=self.test_user
        )
        self.assertFalse(result2['success'])
        self.assertEqual(result2['error_code'], 'ALREADY_CHECKED_IN')
        self.assertIn('already checked in today', result2['message'])
        self.assertIn('existing_checkin_time', result2)
    
    def test_early_departure_detection(self):
        """Test early departure detection logic"""
        weekday = _past_weekday()

        # Test normal checkout (4:00 PM) - not early departure
        normal_checkout = timezone.make_aware(
            timezone.datetime.combine(weekday, time(16, 0))
        )
        result = AttendanceService.checkout_student(
            student_id='TEST001',
            user=self.test_user,
            timestamp=normal_checkout
        )
        self.assertTrue(result['success'])
        self.assertFalse(result['is_early_departure'])

        # Clear the entry for next test
        AttendanceEntry.objects.filter(student=self.student1).delete()

        # Test early checkout (2:00 PM) - should be early departure
        early_checkout = timezone.make_aware(
            timezone.datetime.combine(weekday, time(14, 0))
        )
        result = AttendanceService.checkout_student(
            student_id='TEST001',
            user=self.test_user,
            timestamp=early_checkout,
            parent_name='Mrs. Doe',
            reason='Medical appointment'
        )
        self.assertTrue(result['success'])
        self.assertTrue(result['is_early_departure'])
        self.assertEqual(result['parent_name'], 'Mrs. Doe')
        self.assertEqual(result['reason'], 'Medical appointment')

        # Clear the entry for next test
        AttendanceEntry.objects.filter(student=self.student1).delete()

        # Test boundary case (3:30 PM exactly) - should not be early departure
        boundary_checkout = timezone.make_aware(
            timezone.datetime.combine(weekday, time(15, 30))
        )
        result = AttendanceService.checkout_student(
            student_id='TEST001',
            user=self.test_user,
            timestamp=boundary_checkout
        )
        self.assertTrue(result['success'])
        self.assertFalse(result['is_early_departure'])
    
    def test_duplicate_checkout_prevention(self):
        """Test prevention of duplicate check-outs"""
        # First checkout should succeed
        result1 = AttendanceService.checkout_student(
            student_id='TEST001',
            user=self.test_user
        )
        self.assertTrue(result1['success'])
        
        # Second checkout should fail with appropriate error
        result2 = AttendanceService.checkout_student(
            student_id='TEST001',
            user=self.test_user
        )
        self.assertFalse(result2['success'])
        self.assertEqual(result2['error_code'], 'ALREADY_CHECKED_OUT')
        self.assertIn('already checked out today', result2['message'])
        self.assertIn('existing_checkout_time', result2)
    
    def test_checkout_without_checkin(self):
        """Test checkout without prior check-in creates both timestamps"""
        weekday = _past_weekday()
        checkout_time = timezone.make_aware(
            timezone.datetime.combine(weekday, time(15, 0))
        )
        result = AttendanceService.checkout_student(
            student_id='TEST001',
            user=self.test_user,
            timestamp=checkout_time,
            parent_name='Mr. Doe'
        )
        
        self.assertTrue(result['success'])
        
        # Verify both check-in and check-out times are set
        entry = AttendanceEntry.objects.get(student=self.student1)
        self.assertIsNotNone(entry.check_in_time)
        self.assertIsNotNone(entry.check_out_time)
        self.assertEqual(entry.check_in_time, checkout_time.time())
        self.assertEqual(entry.check_out_time, checkout_time.time())
        self.assertEqual(entry.parent_name, 'Mr. Doe')
    
    def test_batch_checkin_processing(self):
        """Test batch check-in processing"""
        students_data = [
            {'id': '2024001', 'checkin_time': '2024-01-15T08:00:00Z'},
            {'id': '2024002', 'checkin_time': '2024-01-15T08:15:00Z'},
            {'id': 'INVALID', 'checkin_time': '2024-01-15T08:30:00Z'}  # Invalid student
        ]
        
        result = AttendanceService.process_batch_checkin(
            students_data=students_data,
            user=self.test_user
        )
        
        self.assertTrue(result['success'])  # At least one succeeded
        self.assertEqual(result['total_count'], 3)
        self.assertEqual(result['successful_count'], 2)
        self.assertEqual(result['failed_count'], 1)
        
        # Verify individual results
        self.assertEqual(len(result['results']), 3)
        self.assertTrue(result['results'][0]['success'])  # Valid student 1
        self.assertTrue(result['results'][1]['success'])  # Valid student 2
        self.assertFalse(result['results'][2]['success'])  # Invalid student
    
    def test_batch_checkout_processing(self):
        """Test batch check-out processing"""
        # First check in the students
        AttendanceService.checkin_student('2024001', self.test_user, laravel_format=True)
        AttendanceService.checkin_student('2024002', self.test_user, laravel_format=True)
        
        students_data = [
            {
                'id': '2024001', 
                'checkout_time': '2024-01-15T15:45:00Z',
                'parent_name': 'Mrs. Doe',
                'reason': ''
            },
            {
                'id': '2024002', 
                'checkout_time': '2024-01-15T14:30:00Z',  # Early departure
                'parent_name': 'Mr. Smith',
                'reason': 'Doctor appointment'
            },
            {
                'id': 'INVALID', 
                'checkout_time': '2024-01-15T15:00:00Z'  # Invalid student
            }
        ]
        
        result = AttendanceService.process_batch_checkout(
            students_data=students_data,
            user=self.test_user
        )
        
        self.assertTrue(result['success'])  # At least one succeeded
        self.assertEqual(result['total_count'], 3)
        self.assertEqual(result['successful_count'], 2)
        self.assertEqual(result['failed_count'], 1)
        
        # Verify individual results
        self.assertEqual(len(result['results']), 3)
        self.assertTrue(result['results'][0]['success'])  # Valid student 1
        self.assertFalse(result['results'][0]['is_early_departure'])  # 15:45 is not early
        self.assertTrue(result['results'][1]['success'])  # Valid student 2
        self.assertTrue(result['results'][1]['is_early_departure'])  # 14:30 is early
        self.assertFalse(result['results'][2]['success'])  # Invalid student
    
    def test_helper_methods(self):
        """Test helper methods for attendance processing"""
        # Test _find_student method
        student = AttendanceService._find_student('TEST001', laravel_format=False)
        self.assertEqual(student, self.student1)
        
        student = AttendanceService._find_student('2024001', laravel_format=True)
        self.assertEqual(student, self.student1)
        
        student = AttendanceService._find_student('NONEXISTENT', laravel_format=False)
        self.assertIsNone(student)
        
        # Test _derive_checkin_status method
        early_time = timezone.now().replace(hour=7, minute=30)
        status = AttendanceService._derive_checkin_status(early_time)
        self.assertEqual(status, 'present')
        
        late_time = timezone.now().replace(hour=9, minute=0)
        status = AttendanceService._derive_checkin_status(late_time)
        self.assertEqual(status, 'late')
        
        # Test _is_early_departure method
        early_time = time(14, 30)
        self.assertTrue(AttendanceService._is_early_departure(early_time))
        
        normal_time = time(16, 0)
        self.assertFalse(AttendanceService._is_early_departure(normal_time))
        
        boundary_time = time(15, 30)
        self.assertFalse(AttendanceService._is_early_departure(boundary_time))


class AttendanceAPIProcessingTest(APITestCase):
    """Test API endpoints with enhanced processing logic"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='api_user',
            email='api@hodari.ac.tz',
            password='testpass123'
        )
        
        self.student = Student.objects.create(
            admission_no='API001',
            first_name='API',
            last_name='Student',
            laravel_student_id='2024100',
            class_name='Grade 1',
            status='active'
        )
        
        # Authenticate the user
        self.client.force_authenticate(user=self.user)
    
    def test_enhanced_checkin_api(self):
        """Test enhanced check-in API endpoint"""
        data = {
            'students': [
                {
                    'id': '2024100',
                    'checkin_time': '2024-01-15T08:00:00Z'
                }
            ]
        }
        
        response = self.client.post('/attendance/api/checkin/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('summary', response.data)
        self.assertEqual(response.data['summary']['successful'], 1)
        self.assertEqual(response.data['summary']['failed'], 0)
        
        # Verify detailed response data
        student_result = response.data['data'][0]
        self.assertEqual(student_result['status'], 'success')
        self.assertEqual(student_result['student_id'], '2024100')
        self.assertIn('student_name', student_result)
        self.assertIn('check_in_time', student_result)
    
    def test_enhanced_checkout_api(self):
        """Test enhanced check-out API endpoint"""
        # First check in the student
        AttendanceService.checkin_student('2024100', self.user, laravel_format=True)
        
        data = {
            'students': [
                {
                    'id': '2024100',
                    'checkout_time': '2024-01-15T14:30:00Z',  # Early departure
                    'parent_name': 'Test Parent',
                    'reason': 'Medical appointment'
                }
            ]
        }
        
        response = self.client.post('/attendance/api/checkout/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('summary', response.data)
        self.assertEqual(response.data['summary']['successful'], 1)
        self.assertEqual(response.data['summary']['failed'], 0)
        
        # Verify detailed response data
        student_result = response.data['data'][0]
        self.assertEqual(student_result['status'], 'success')
        self.assertEqual(student_result['student_id'], '2024100')
        self.assertTrue(student_result['is_early_departure'])
        self.assertEqual(student_result['parent_name'], 'Test Parent')
        self.assertEqual(student_result['reason'], 'Medical appointment')
    
    def test_api_error_handling(self):
        """Test API error handling with enhanced processing"""
        # Test empty students array
        data = {'students': []}
        response = self.client.post('/attendance/api/checkin/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error_code'], 'NO_STUDENTS')
        
        # Test invalid student ID
        data = {
            'students': [
                {'id': 'INVALID_ID'}
            ]
        }
        response = self.client.post('/attendance/api/checkin/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['summary']['successful'], 0)
        self.assertEqual(response.data['summary']['failed'], 1)
        
        student_result = response.data['data'][0]
        self.assertEqual(student_result['status'], 'error')
        self.assertEqual(student_result['error_code'], 'STUDENT_NOT_FOUND')
    
    def test_mixed_batch_processing(self):
        """Test batch processing with mixed valid and invalid students"""
        data = {
            'students': [
                {'id': '2024100'},  # Valid student
                {'id': 'INVALID'},  # Invalid student
                {'id': ''},         # Empty ID
            ]
        }
        
        response = self.client.post('/attendance/api/checkin/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['summary']['total'], 3)
        self.assertEqual(response.data['summary']['successful'], 1)
        self.assertEqual(response.data['summary']['failed'], 2)
        
        # Verify individual results
        results = response.data['data']
        self.assertEqual(results[0]['status'], 'success')  # Valid student
        self.assertEqual(results[1]['status'], 'error')    # Invalid student
        self.assertEqual(results[2]['status'], 'error')    # Empty ID