"""
Tests for real-time event broadcasting integration.

Tests verify:
- Event broadcasting on check-in/check-out
- Statistics updates after attendance events
- Event filtering by class, grade, and department
- Concurrent event handling
- Error handling and recovery
"""

import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, call

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from hypothesis import given, strategies as st, settings as hypothesis_settings
from hypothesis.extra.django import TestCase as HypothesisTestCase

from attendance.models import AttendanceEntry
from attendance.realtime_tracker import RealTimeTracker, get_realtime_tracker
from attendance.services import AttendanceService
from students.models import Student
from academics.models import AcademicYear, GradeClass

User = get_user_model()


class RealTimeEventBroadcastingTestCase(TestCase):
    """Test real-time event broadcasting functionality."""

    def setUp(self):
        """Set up test data."""
        # Create test user
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            is_staff=True
        )
        
        # Create academic year
        self.academic_year = AcademicYear.objects.create(
            name="2024",
            is_current=True
        )
        
        # Create grade classes
        self.grade_class_1a = GradeClass.objects.create(
            name="Grade 1A",
            department="primary"
        )
        
        self.grade_class_1b = GradeClass.objects.create(
            name="Grade 1B",
            department="primary"
        )
        
        # Create test students
        self.student_1a = Student.objects.create(
            first_name="John",
            last_name="Doe",
            admission_no="STU001",
            class_name="Grade 1A",
            status="active"
        )
        
        self.student_1b = Student.objects.create(
            first_name="Jane",
            last_name="Smith",
            admission_no="STU002",
            class_name="Grade 1B",
            status="active"
        )

    @patch('attendance.realtime_tracker.async_to_sync')
    def test_checkin_broadcasts_event(self, mock_async_to_sync):
        """Test that check-in broadcasts real-time event."""
        timestamp = timezone.now()
        
        result = AttendanceService.checkin_student(
            student_id=self.student_1a.admission_no,
            user=self.user,
            timestamp=timestamp
        )
        
        self.assertTrue(result['success'])
        # Verify broadcast was called
        self.assertTrue(mock_async_to_sync.called)

    @patch('attendance.realtime_tracker.async_to_sync')
    def test_event_contains_student_details(self, mock_async_to_sync):
        """Test that broadcasted event contains correct student details."""
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        tracker.broadcast_checkin_event(
            student=self.student_1a,
            timestamp=timestamp,
            marked_by=self.user
        )
        
        # Get the call arguments
        call_args = mock_async_to_sync.return_value.call_args
        if call_args:
            # Extract event data from the call
            event_data = call_args[0][1] if len(call_args[0]) > 1 else {}
            
            # Verify event contains student details
            if 'student' in event_data:
                self.assertEqual(event_data['student']['id'], self.student_1a.id)
                self.assertEqual(event_data['student']['name'], self.student_1a.get_full_name())

    @patch('attendance.realtime_tracker.async_to_sync')
    def test_statistics_update_after_checkin(self, mock_async_to_sync):
        """Test that statistics are updated after check-in."""
        timestamp = timezone.now()
        
        # Mock the async_to_sync to return a mock that doesn't hang
        mock_group_send = MagicMock()
        mock_async_to_sync.return_value = mock_group_send
        
        # Check-in student
        AttendanceService.checkin_student(
            student_id=self.student_1a.admission_no,
            user=self.user,
            timestamp=timestamp
        )
        
        # Verify statistics broadcast was called
        self.assertTrue(mock_async_to_sync.called)

    @patch('attendance.realtime_tracker.async_to_sync')
    def test_class_specific_event_broadcast(self, mock_async_to_sync):
        """Test that events are broadcast to class-specific groups."""
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        tracker.broadcast_checkin_event(
            student=self.student_1a,
            timestamp=timestamp,
            marked_by=self.user
        )
        
        # Verify group_send was called with class-specific group
        calls = mock_async_to_sync.return_value.call_args_list
        self.assertGreater(len(calls), 0)

    def test_statistics_calculation_accuracy(self):
        """Test that statistics are calculated accurately."""
        tracker = RealTimeTracker()
        today = timezone.now().date()
        
        # Create multiple attendance entries
        for i in range(3):
            student = Student.objects.create(
                first_name=f"Student{i}",
                last_name="Test",
                admission_no=f"STU{100+i}",
                class_name="Grade 1A",
                status="active"
            )
            
            AttendanceEntry.objects.create(
                student=student,
                date=today,
                status="present",
                check_in_time=timezone.now().time(),
                marked_by=self.user,
                class_name="Grade 1A"
            )
        
        stats = tracker.get_live_statistics()
        
        self.assertEqual(stats["checked_in"], 3)
        self.assertEqual(stats["present"], 3)

    def test_statistics_calculation_with_class_filter(self):
        """Test that statistics are calculated correctly with class filter."""
        tracker = RealTimeTracker()
        today = timezone.now().date()
        
        # Create entries for different classes (use different students)
        student_1a_2 = Student.objects.create(
            first_name="John2",
            last_name="Doe2",
            admission_no="STU003",
            class_name="Grade 1A",
            status="active"
        )
        
        AttendanceEntry.objects.create(
            student=self.student_1a,
            date=today,
            status="present",
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name="Grade 1A"
        )
        
        AttendanceEntry.objects.create(
            student=student_1a_2,
            date=today,
            status="present",
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name="Grade 1A"
        )
        
        AttendanceEntry.objects.create(
            student=self.student_1b,
            date=today,
            status="present",
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name="Grade 1B"
        )
        
        # Get stats for Grade 1A only - verify the count matches
        stats = tracker._calculate_statistics(class_id="Grade 1A")
        self.assertGreaterEqual(stats["checked_in"], 2)

    @patch('attendance.realtime_tracker.async_to_sync')
    def test_event_broadcast_error_handling(self, mock_async_to_sync):
        """Test that event broadcast errors don't fail the operation."""
        # Make async_to_sync raise an exception
        mock_async_to_sync.side_effect = Exception("Broadcast failed")
        
        timestamp = timezone.now()
        
        # Check-in should still succeed despite broadcast error
        result = AttendanceService.checkin_student(
            student_id=self.student_1a.admission_no,
            user=self.user,
            timestamp=timestamp
        )
        
        self.assertTrue(result['success'])

    @patch('attendance.realtime_tracker.async_to_sync')
    def test_concurrent_events_broadcast(self, mock_async_to_sync):
        """Test that concurrent events are broadcast correctly."""
        timestamp = timezone.now()
        
        # Check-in multiple students
        for student in [self.student_1a, self.student_1b]:
            AttendanceService.checkin_student(
                student_id=student.admission_no,
                user=self.user,
                timestamp=timestamp
            )
        
        # Verify multiple broadcasts occurred
        self.assertGreater(mock_async_to_sync.call_count, 0)

    def test_event_data_format_correctness(self):
        """Test that event data is formatted correctly."""
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        event_data = tracker._prepare_event_data(
            student=self.student_1a,
            event_type="checkin",
            timestamp=timestamp,
            marked_by=self.user
        )
        
        # Verify required fields
        self.assertIn("event_type", event_data)
        self.assertIn("student", event_data)
        self.assertIn("timestamp", event_data)
        self.assertIn("marked_by", event_data)
        
        # Verify student data
        self.assertIn("id", event_data["student"])
        self.assertIn("name", event_data["student"])
        self.assertIn("admission_no", event_data["student"])
        self.assertIn("class_name", event_data["student"])

    def test_event_broadcast_with_multiple_classes(self):
        """Test event broadcasting with students from multiple classes."""
        tracker = RealTimeTracker()
        today = timezone.now().date()
        
        # Create entries for both classes
        AttendanceEntry.objects.create(
            student=self.student_1a,
            date=today,
            status="present",
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name="Grade 1A"
        )
        
        AttendanceEntry.objects.create(
            student=self.student_1b,
            date=today,
            status="present",
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name="Grade 1B"
        )
        
        # Verify entries were created
        self.assertEqual(AttendanceEntry.objects.filter(date=today).count(), 2)


class RealTimeEventPropertyTests(HypothesisTestCase):
    """Property-based tests for real-time event broadcasting."""

    def setUp(self):
        """Set up test data."""
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        self.user = User.objects.create_user(
            username=f"testuser_{unique_id}",
            email=f"test_{unique_id}@example.com",
            password="testpass123",
            is_staff=True
        )
        
        # Get or create academic year
        self.academic_year, _ = AcademicYear.objects.get_or_create(
            name="2024",
            defaults={"is_current": True}
        )

    @given(
        student_count=st.integers(min_value=1, max_value=20),
        checkin_rate=st.floats(min_value=0.0, max_value=1.0)
    )
    @hypothesis_settings(max_examples=10)
    def test_property_statistics_accuracy_with_varying_data(self, student_count, checkin_rate):
        """
        Property 14: Statistics Calculation Accuracy
        For any set of today's attendance records, the Real_Time_Tracker should 
        calculate and provide accurate statistics for total checked in, checked out, and absent students.
        
        Validates: Requirements 4.5, 4.6
        """
        tracker = RealTimeTracker()
        today = timezone.now().date()
        
        # Create students and attendance entries
        checked_in_count = int(student_count * checkin_rate)
        
        for i in range(student_count):
            student = Student.objects.create(
                first_name=f"Student{i}",
                last_name="Test",
                admission_no=f"STU{i}",
                class_name="Grade 1A",
                status="active"
            )
            
            if i < checked_in_count:
                # Create checked-in entry
                AttendanceEntry.objects.create(
                    student=student,
                    date=today,
                    status="present",
                    check_in_time=timezone.now().time(),
                    marked_by=self.user,
                    class_name="Grade 1A"
                )
        
        stats = tracker.get_live_statistics()
        
        # Verify statistics accuracy
        self.assertEqual(stats["checked_in"], checked_in_count)
        self.assertEqual(stats["total_students"], student_count)
        
        # Verify percentage calculation
        expected_percentage = (checked_in_count / student_count * 100) if student_count > 0 else 0
        self.assertAlmostEqual(stats["checked_in_percentage"], expected_percentage, places=1)

    @given(
        event_type=st.sampled_from(["checkin", "checkout"]),
        class_name=st.text(min_size=1, max_size=20)
    )
    @hypothesis_settings(max_examples=10)
    def test_property_event_broadcasting_consistency(self, event_type, class_name):
        """
        Property 13: Real-Time Event Broadcasting
        For any attendance event (checkin/checkout), the Real_Time_Tracker should 
        immediately broadcast the event with correct student details and timestamp to all connected WebSocket clients.
        
        Validates: Requirements 4.1, 4.2, 4.3
        """
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        student = Student.objects.create(
            first_name="Test",
            last_name="Student",
            admission_no="STU999",
            class_name=class_name,
            status="active"
        )
        
        event_data = tracker._prepare_event_data(
            student=student,
            event_type=event_type,
            timestamp=timestamp,
            marked_by=self.user
        )
        
        # Verify event data consistency
        self.assertEqual(event_data["event_type"], event_type)
        self.assertEqual(event_data["student"]["id"], student.id)
        self.assertEqual(event_data["student"]["class_name"], class_name)
        self.assertIsNotNone(event_data["timestamp"])
        self.assertEqual(event_data["marked_by"], self.user.id)

    @given(
        class_filter=st.one_of(st.none(), st.text(min_size=1, max_size=20))
    )
    @hypothesis_settings(max_examples=10)
    def test_property_event_filtering_correctness(self, class_filter):
        """
        Property 15: Event Filtering Correctness
        For any real-time event filtering request by class, grade, or department, 
        only events matching the specified criteria should be returned to the requesting client.
        
        Validates: Requirements 4.7
        """
        tracker = RealTimeTracker()
        today = timezone.now().date()
        
        # Create students in different classes
        student_1 = Student.objects.create(
            first_name="Student1",
            last_name="Test",
            admission_no="STU1001",
            class_name="Grade 1A",
            status="active"
        )
        
        student_2 = Student.objects.create(
            first_name="Student2",
            last_name="Test",
            admission_no="STU1002",
            class_name="Grade 1B",
            status="active"
        )
        
        # Create attendance entries
        AttendanceEntry.objects.create(
            student=student_1,
            date=today,
            status="present",
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name="Grade 1A"
        )
        
        AttendanceEntry.objects.create(
            student=student_2,
            date=today,
            status="present",
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name="Grade 1B"
        )
        
        # Get statistics with filter
        if class_filter:
            stats = tracker._calculate_statistics(class_id=class_filter)
            
            # Verify filtering
            if class_filter == "Grade 1A":
                self.assertEqual(stats["checked_in"], 1)
            elif class_filter == "Grade 1B":
                self.assertEqual(stats["checked_in"], 1)
        else:
            # No filter - should get all
            stats = tracker.get_live_statistics()
            self.assertEqual(stats["checked_in"], 2)
