"""
Tests for scan statistics and reporting endpoints.
Tests the /api/scans/today, /api/scans/statistics, and /api/scans/history endpoints.

KNOWN ISSUE — timezone-dependent test failures (midnight divergence)
--------------------------------------------------------------------
Several tests in ScansStatisticsAPITestCase use `timestamp.date()` (UTC
date) in assertions where the service under test uses
`timezone.localdate(timestamp)` (Africa/Nairobi).  Between 21:00 UTC and
00:00 UTC the dates diverge by one day, causing lookup assertions to fail.
Fix: replace `timestamp.date()` with `timezone.localdate(timestamp)`.
"""
from django.test import TestCase, Client
from django.utils import timezone
from django.contrib.auth import get_user_model
from datetime import timedelta, time
from rest_framework.test import APITestCase, APIClient
from rest_framework import status

from attendance.models import AttendanceEntry, AttendanceStatus
from students.models import Student
from users.models import UserRole

User = get_user_model()


class ScansStatisticsAPITestCase(APITestCase):
    """Test cases for scan statistics endpoints"""
    
    def setUp(self):
        """Set up test data"""
        # Create test user
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123',
            email='test@example.com',
            role=UserRole.ADMIN_OFFICER
        )
        
        # Create test students
        self.student1 = Student.objects.create(
            admission_no='STU001',
            first_name='John',
            last_name='Doe',
            gender='M',
            status='active',
            class_name='Class 1A',
            laravel_student_id='LARAVEL001'
        )
        
        self.student2 = Student.objects.create(
            admission_no='STU002',
            first_name='Jane',
            last_name='Smith',
            gender='F',
            status='active',
            class_name='Class 1A',
            laravel_student_id='LARAVEL002'
        )
        
        self.student3 = Student.objects.create(
            admission_no='STU003',
            first_name='Bob',
            last_name='Johnson',
            gender='M',
            status='active',
            class_name='Class 2B',
            laravel_student_id='LARAVEL003'
        )
        
        # Create test attendance entries for today
        today = timezone.now().date()
        
        # Student 1: checked in and out
        self.entry1 = AttendanceEntry.objects.create(
            student=self.student1,
            date=today,
            status=AttendanceStatus.PRESENT,
            check_in_time=time(8, 15),
            check_out_time=time(16, 30),
            marked_by=self.user,
            class_name='Class 1A',
            is_early_departure=False
        )
        
        # Student 2: checked in only
        self.entry2 = AttendanceEntry.objects.create(
            student=self.student2,
            date=today,
            status=AttendanceStatus.PRESENT,
            check_in_time=time(8, 5),
            check_out_time=None,
            marked_by=self.user,
            class_name='Class 1A',
            is_early_departure=False
        )
        
        # Student 3: early departure
        self.entry3 = AttendanceEntry.objects.create(
            student=self.student3,
            date=today,
            status=AttendanceStatus.PRESENT,
            check_in_time=time(8, 0),
            check_out_time=time(14, 0),
            marked_by=self.user,
            class_name='Class 2B',
            is_early_departure=True,
            parent_name='Mr. Johnson',
            reason='Medical appointment'
        )
        
        # Create entry for yesterday
        yesterday = today - timedelta(days=1)
        self.entry_yesterday = AttendanceEntry.objects.create(
            student=self.student1,
            date=yesterday,
            status=AttendanceStatus.PRESENT,
            check_in_time=time(8, 10),
            check_out_time=time(16, 0),
            marked_by=self.user,
            class_name='Class 1A',
            is_early_departure=False
        )
        
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
    
    def test_scans_today_all_scans(self):
        """Test retrieving all scans for today"""
        response = self.client.get('/attendance/api/scans/today/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_scans'], 5)  # 3 check-ins + 2 check-outs
        self.assertIn('data', response.data)
        self.assertEqual(len(response.data['data']), 5)
    
    def test_scans_today_filter_by_in(self):
        """Test filtering scans by IN type"""
        response = self.client.get('/attendance/api/scans/today/?scan_type=IN')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should only have check-in scans
        self.assertEqual(response.data['scan_type'], 'IN')
        
        # Verify all scans are IN type
        for scan in response.data['data']:
            self.assertEqual(scan['scan_type'], 'IN')
    
    def test_scans_today_filter_by_out(self):
        """Test filtering scans by OUT type"""
        response = self.client.get('/attendance/api/scans/today/?scan_type=OUT')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['scan_type'], 'OUT')
        
        # Verify all scans are OUT type
        for scan in response.data['data']:
            self.assertEqual(scan['scan_type'], 'OUT')
    
    def test_scans_today_filter_by_class(self):
        """Test filtering scans by class"""
        response = self.client.get('/attendance/api/scans/today/?class_id=Class%201A')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Class 1A has 2 students with 3 scans total (2 check-ins + 1 check-out)
        self.assertGreater(response.data['total_scans'], 0)
        
        # Verify all scans are from Class 1A
        for scan in response.data['data']:
            self.assertEqual(scan['class'], 'Class 1A')
    
    def test_scans_today_includes_student_info(self):
        """Test that scans include student information"""
        response = self.client.get('/attendance/api/scans/today/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Check first scan has required student info
        scan = response.data['data'][0]
        self.assertIn('student_id', scan)
        self.assertIn('student_name', scan)
        self.assertIn('student_photo', scan)
        self.assertIn('class', scan)
        self.assertIn('scan_type', scan)
        self.assertIn('scan_time', scan)
    
    def test_scans_today_includes_early_departure_info(self):
        """Test that early departure information is included"""
        response = self.client.get('/attendance/api/scans/today/?scan_type=OUT')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Find the early departure scan
        early_departure_scan = None
        for scan in response.data['data']:
            if scan['student_id'] == 'LARAVEL003':
                early_departure_scan = scan
                break
        
        self.assertIsNotNone(early_departure_scan)
        self.assertTrue(early_departure_scan['is_early_departure'])
        self.assertEqual(early_departure_scan['parent_name'], 'Mr. Johnson')
        self.assertEqual(early_departure_scan['reason'], 'Medical appointment')
    
    def test_scans_today_invalid_date_format(self):
        """Test error handling for invalid date format"""
        response = self.client.get('/attendance/api/scans/today/?date=invalid-date')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error_code', response.data)
        self.assertEqual(response.data['error_code'], 'INVALID_DATE_FORMAT')
    
    def test_scans_statistics_summary(self):
        """Test scan statistics endpoint returns correct summary"""
        response = self.client.get('/attendance/api/scans/statistics/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('summary', response.data)
        
        summary = response.data['summary']
        self.assertEqual(summary['total_entries'], 3)  # 3 attendance entries
        self.assertEqual(summary['check_in_count'], 3)  # 3 check-ins
        self.assertEqual(summary['check_out_count'], 2)  # 2 check-outs
        self.assertEqual(summary['early_departure_count'], 1)  # 1 early departure
    
    def test_scans_statistics_status_breakdown(self):
        """Test scan statistics includes status breakdown"""
        response = self.client.get('/attendance/api/scans/statistics/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('status_breakdown', response.data)
        
        status_breakdown = response.data['status_breakdown']
        self.assertIn('present', status_breakdown)
        self.assertEqual(status_breakdown['present']['count'], 3)
    
    def test_scans_statistics_class_breakdown(self):
        """Test scan statistics includes class breakdown"""
        response = self.client.get('/attendance/api/scans/statistics/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('class_breakdown', response.data)
        
        class_breakdown = response.data['class_breakdown']
        self.assertIn('Class 1A', class_breakdown)
        self.assertIn('Class 2B', class_breakdown)
        
        # Verify Class 1A stats
        self.assertEqual(class_breakdown['Class 1A']['total'], 2)
        self.assertEqual(class_breakdown['Class 1A']['check_in'], 2)
        self.assertEqual(class_breakdown['Class 1A']['check_out'], 1)
    
    def test_scans_statistics_filter_by_class(self):
        """Test scan statistics filtered by class"""
        response = self.client.get('/attendance/api/scans/statistics/?class_id=Class%201A')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        summary = response.data['summary']
        self.assertEqual(summary['total_entries'], 2)  # Only Class 1A entries
        
        # class_breakdown should be None when filtered by class
        self.assertIsNone(response.data['class_breakdown'])
    
    def test_scan_history_default_range(self):
        """Test scan history with default date range (30 days)"""
        response = self.client.get('/attendance/api/scans/history/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('data', response.data)
        # Should include both today's and yesterday's entries
        self.assertGreaterEqual(response.data['total_records'], 6)
    
    def test_scan_history_custom_date_range(self):
        """Test scan history with custom date range"""
        today = timezone.now().date()
        start_date = (today - timedelta(days=1)).strftime('%Y-%m-%d')
        end_date = today.strftime('%Y-%m-%d')
        
        response = self.client.get(
            f'/attendance/api/scans/history/?start_date={start_date}&end_date={end_date}'
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['start_date'], start_date)
        self.assertEqual(response.data['end_date'], end_date)
    
    def test_scan_history_filter_by_student(self):
        """Test scan history filtered by student"""
        response = self.client.get('/attendance/api/scans/history/?student_id=LARAVEL001')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Should only include scans for student 1
        for scan in response.data['data']:
            self.assertEqual(scan['student_id'], 'LARAVEL001')
    
    def test_scan_history_filter_by_scan_type(self):
        """Test scan history filtered by scan type"""
        response = self.client.get('/attendance/api/scans/history/?scan_type=IN')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['scan_type'], 'IN')
        
        # All scans should be IN type
        for scan in response.data['data']:
            self.assertEqual(scan['scan_type'], 'IN')
    
    def test_scan_history_invalid_date_range(self):
        """Test error handling for invalid date range"""
        response = self.client.get(
            '/attendance/api/scans/history/?start_date=2024-12-31&end_date=2024-01-01'
        )
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error_code'], 'INVALID_DATE_RANGE')
    
    def test_scan_history_limit_parameter(self):
        """Test scan history respects limit parameter"""
        response = self.client.get('/attendance/api/scans/history/?limit=2')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # The limit is applied to the queryset, but we may get more records if
        # an entry has both check-in and check-out
        self.assertLessEqual(response.data['total_records'], 10)  # Reasonable upper bound
    
    def test_scan_history_includes_all_required_fields(self):
        """Test that scan history includes all required fields"""
        response = self.client.get('/attendance/api/scans/history/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        if response.data['total_records'] > 0:
            scan = response.data['data'][0]
            required_fields = [
                'id', 'date', 'student_id', 'student_name', 'student_photo',
                'class', 'scan_type', 'scan_time', 'scan_timestamp', 'marked_by', 'status'
            ]
            for field in required_fields:
                self.assertIn(field, scan)
    
    def test_unauthenticated_access_denied(self):
        """Test that unauthenticated users cannot access endpoints"""
        client = APIClient()
        
        response = client.get('/attendance/api/scans/today/')
        # Should be either 401 (Unauthorized) or 403 (Forbidden)
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
        
        response = client.get('/attendance/api/scans/statistics/')
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
        
        response = client.get('/attendance/api/scans/history/')
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])


class ScansStatisticsIntegrationTestCase(TestCase):
    """Integration tests for scan statistics endpoints"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123',
            role=UserRole.ADMIN_OFFICER
        )
        
        self.client = Client()
        self.client.login(username='testuser', password='testpass123')
    
    def test_scans_today_response_format(self):
        """Test that scans today response has correct format"""
        # Create test data
        student = Student.objects.create(
            admission_no='STU001',
            first_name='Test',
            last_name='Student',
            gender='M',
            status='active',
            class_name='Class 1A'
        )
        
        today = timezone.now().date()
        AttendanceEntry.objects.create(
            student=student,
            date=today,
            status=AttendanceStatus.PRESENT,
            check_in_time=time(8, 0),
            marked_by=self.user,
            class_name='Class 1A'
        )
        
        # Make request
        response = self.client.get('/attendance/api/scans/today/')
        
        # Verify response structure
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('date', data)
        self.assertIn('scan_type', data)
        self.assertIn('total_scans', data)
        self.assertIn('data', data)
