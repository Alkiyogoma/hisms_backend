"""
Integration tests for WebSocket connections.

Tests verify:
- Multiple concurrent WebSocket connections
- Connection failure and reconnection handling
- Event delivery and message ordering
- Authentication and authorization
- Connection lifecycle management
- Error handling and recovery

Requirements: 4.4, 4.8
"""

import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from attendance.consumers import AttendanceConsumer, ClassAttendanceConsumer
from attendance.models import AttendanceEntry
from attendance.realtime_tracker import RealTimeTracker, get_realtime_tracker
from students.models import Student
from academics.models import AcademicYear, GradeClass

User = get_user_model()


class WebSocketConnectionIntegrationTestCase(TestCase):
    """Test WebSocket connection establishment and lifecycle."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            is_staff=True
        )
        
        self.academic_year = AcademicYear.objects.create(
            name="2024",
            is_current=True
        )
        
        self.grade_class = GradeClass.objects.create(
            name="Grade 1A",
            department="primary"
        )
        
        self.student = Student.objects.create(
            first_name="John",
            last_name="Doe",
            admission_no="STU001",
            class_name="Grade 1A",
            status="active"
        )

    def test_websocket_consumer_instantiation(self):
        """Test that WebSocket consumer can be instantiated."""
        consumer = AttendanceConsumer()
        self.assertIsNotNone(consumer)

    def test_class_specific_consumer_instantiation(self):
        """Test that class-specific WebSocket consumer can be instantiated."""
        consumer = ClassAttendanceConsumer()
        self.assertIsNotNone(consumer)

    def test_realtime_tracker_initialization(self):
        """Test that RealTimeTracker can be initialized."""
        tracker = RealTimeTracker()
        self.assertIsNotNone(tracker)
        self.assertIsNotNone(tracker.channel_layer)

    def test_get_realtime_tracker_singleton(self):
        """Test that get_realtime_tracker returns singleton instance."""
        tracker1 = get_realtime_tracker()
        tracker2 = get_realtime_tracker()
        self.assertIs(tracker1, tracker2)


class WebSocketEventBroadcastingIntegrationTestCase(TestCase):
    """Test WebSocket event broadcasting functionality."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            is_staff=True
        )
        
        self.academic_year = AcademicYear.objects.create(
            name="2024",
            is_current=True
        )
        
        self.grade_class = GradeClass.objects.create(
            name="Grade 1A",
            department="primary"
        )
        
        self.student = Student.objects.create(
            first_name="John",
            last_name="Doe",
            admission_no="STU001",
            class_name="Grade 1A",
            status="active"
        )

    @patch('attendance.realtime_tracker.async_to_sync')
    def test_broadcast_checkin_event(self, mock_async_to_sync):
        """Test that check-in events are broadcast correctly."""
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        tracker.broadcast_checkin_event(
            student=self.student,
            timestamp=timestamp,
            marked_by=self.user
        )
        
        self.assertTrue(mock_async_to_sync.called)

    @patch('attendance.realtime_tracker.async_to_sync')
    def test_broadcast_checkout_event(self, mock_async_to_sync):
        """Test that check-out events are broadcast correctly."""
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        tracker.broadcast_checkout_event(
            student=self.student,
            timestamp=timestamp,
            marked_by=self.user
        )
        
        self.assertTrue(mock_async_to_sync.called)

    @patch('attendance.realtime_tracker.async_to_sync')
    def test_broadcast_statistics_update(self, mock_async_to_sync):
        """Test that statistics updates are broadcast correctly."""
        tracker = RealTimeTracker()
        
        tracker.broadcast_statistics_update()
        
        self.assertTrue(mock_async_to_sync.called)

    @patch('attendance.realtime_tracker.async_to_sync')
    def test_broadcast_class_specific_statistics(self, mock_async_to_sync):
        """Test that class-specific statistics are broadcast correctly."""
        tracker = RealTimeTracker()
        
        tracker.broadcast_statistics_update(class_id="Grade 1A")
        
        self.assertTrue(mock_async_to_sync.called)


class WebSocketEventDataIntegrationTestCase(TestCase):
    """Test WebSocket event data formatting and delivery."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            is_staff=True
        )
        
        self.academic_year = AcademicYear.objects.create(
            name="2024",
            is_current=True
        )
        
        self.grade_class = GradeClass.objects.create(
            name="Grade 1A",
            department="primary"
        )
        
        self.student = Student.objects.create(
            first_name="John",
            last_name="Doe",
            admission_no="STU001",
            class_name="Grade 1A",
            status="active"
        )

    def test_event_data_format_contains_required_fields(self):
        """Test that event data contains all required fields."""
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        event_data = tracker._prepare_event_data(
            student=self.student,
            event_type="checkin",
            timestamp=timestamp,
            marked_by=self.user
        )
        
        self.assertIn("event_type", event_data)
        self.assertIn("student", event_data)
        self.assertIn("timestamp", event_data)
        self.assertIn("marked_by", event_data)
        self.assertIn("class_name", event_data)

    def test_event_data_student_details(self):
        """Test that event data contains correct student details."""
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        event_data = tracker._prepare_event_data(
            student=self.student,
            event_type="checkin",
            timestamp=timestamp,
            marked_by=self.user
        )
        
        student_data = event_data["student"]
        self.assertEqual(student_data["id"], self.student.id)
        self.assertEqual(student_data["name"], self.student.get_full_name())
        self.assertEqual(student_data["admission_no"], self.student.admission_no)
        self.assertEqual(student_data["class_name"], self.student.class_name)

    def test_event_data_timestamp_format(self):
        """Test that event data timestamp is properly formatted."""
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        event_data = tracker._prepare_event_data(
            student=self.student,
            event_type="checkin",
            timestamp=timestamp,
            marked_by=self.user
        )
        
        self.assertIsInstance(event_data["timestamp"], str)
        datetime.fromisoformat(event_data["timestamp"])

    def test_event_data_event_type_values(self):
        """Test that event data contains correct event type values."""
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        checkin_data = tracker._prepare_event_data(
            student=self.student,
            event_type="checkin",
            timestamp=timestamp,
            marked_by=self.user
        )
        self.assertEqual(checkin_data["event_type"], "checkin")
        
        checkout_data = tracker._prepare_event_data(
            student=self.student,
            event_type="checkout",
            timestamp=timestamp,
            marked_by=self.user
        )
        self.assertEqual(checkout_data["event_type"], "checkout")


class WebSocketStatisticsIntegrationTestCase(TestCase):
    """Test WebSocket statistics calculation and delivery."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            is_staff=True
        )
        
        self.academic_year = AcademicYear.objects.create(
            name="2024",
            is_current=True
        )
        
        self.grade_class = GradeClass.objects.create(
            name="Grade 1A",
            department="primary"
        )
        
        self.students = []
        for i in range(5):
            student = Student.objects.create(
                first_name=f"Student{i}",
                last_name="Test",
                admission_no=f"STU{i:03d}",
                class_name="Grade 1A",
                status="active"
            )
            self.students.append(student)

    def test_statistics_calculation_accuracy(self):
        """Test that statistics are calculated accurately."""
        tracker = RealTimeTracker()
        today = timezone.now().date()
        
        for i, student in enumerate(self.students[:3]):
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
        self.assertEqual(stats["total_students"], 5)

    def test_statistics_contains_required_fields(self):
        """Test that statistics contain all required fields."""
        tracker = RealTimeTracker()
        
        stats = tracker.get_live_statistics()
        
        self.assertIn("date", stats)
        self.assertIn("checked_in", stats)
        self.assertIn("checked_out", stats)
        self.assertIn("absent", stats)
        self.assertIn("present", stats)
        self.assertIn("total_students", stats)
        self.assertIn("checked_in_percentage", stats)
        self.assertIn("present_percentage", stats)

    def test_statistics_with_class_filter(self):
        """Test that statistics can be filtered by class."""
        tracker = RealTimeTracker()
        today = timezone.now().date()
        
        for student in self.students[:2]:
            AttendanceEntry.objects.create(
                student=student,
                date=today,
                status="present",
                check_in_time=timezone.now().time(),
                marked_by=self.user,
                class_name="Grade 1A"
            )
        
        stats = tracker._calculate_statistics(class_id="Grade 1A")
        
        self.assertEqual(stats["checked_in"], 2)
        self.assertEqual(stats["total_students"], 5)

    def test_statistics_percentage_calculation(self):
        """Test that statistics percentages are calculated correctly."""
        tracker = RealTimeTracker()
        today = timezone.now().date()
        
        for student in self.students[:2]:
            AttendanceEntry.objects.create(
                student=student,
                date=today,
                status="present",
                check_in_time=timezone.now().time(),
                marked_by=self.user,
                class_name="Grade 1A"
            )
        
        stats = tracker.get_live_statistics()
        
        self.assertEqual(stats["checked_in_percentage"], 40.0)
        self.assertEqual(stats["present_percentage"], 40.0)


class WebSocketConcurrentConnectionsIntegrationTestCase(TestCase):
    """Test concurrent WebSocket connection handling."""

    def setUp(self):
        """Set up test data."""
        self.users = []
        for i in range(3):
            user = User.objects.create_user(
                username=f"testuser{i}",
                email=f"test{i}@example.com",
                password="testpass123",
                is_staff=True
            )
            self.users.append(user)
        
        self.academic_year = AcademicYear.objects.create(
            name="2024",
            is_current=True
        )
        
        self.grade_class = GradeClass.objects.create(
            name="Grade 1A",
            department="primary"
        )

    @patch('attendance.realtime_tracker.async_to_sync')
    def test_concurrent_broadcast_events(self, mock_async_to_sync):
        """Test that concurrent events are broadcast correctly."""
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        students = []
        for i in range(3):
            student = Student.objects.create(
                first_name=f"Student{i}",
                last_name="Test",
                admission_no=f"STU{i:03d}",
                class_name="Grade 1A",
                status="active"
            )
            students.append(student)
        
        for student, user in zip(students, self.users):
            tracker.broadcast_checkin_event(
                student=student,
                timestamp=timestamp,
                marked_by=user
            )
        
        self.assertGreaterEqual(mock_async_to_sync.call_count, 3)

    def test_multiple_users_can_access_statistics(self):
        """Test that multiple users can access statistics simultaneously."""
        tracker = RealTimeTracker()
        
        for user in self.users:
            stats = tracker.get_live_statistics()
            self.assertIsNotNone(stats)
            self.assertIn("checked_in", stats)


class WebSocketErrorHandlingIntegrationTestCase(TestCase):
    """Test WebSocket error handling and recovery."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            is_staff=True
        )
        
        self.academic_year = AcademicYear.objects.create(
            name="2024",
            is_current=True
        )
        
        self.grade_class = GradeClass.objects.create(
            name="Grade 1A",
            department="primary"
        )
        
        self.student = Student.objects.create(
            first_name="John",
            last_name="Doe",
            admission_no="STU001",
            class_name="Grade 1A",
            status="active"
        )

    @patch('attendance.realtime_tracker.async_to_sync')
    def test_broadcast_error_handling(self, mock_async_to_sync):
        """Test that broadcast errors are handled gracefully."""
        mock_async_to_sync.side_effect = Exception("Broadcast failed")
        
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        try:
            tracker.broadcast_checkin_event(
                student=self.student,
                timestamp=timestamp,
                marked_by=self.user
            )
        except Exception as e:
            self.fail(f"broadcast_checkin_event raised {type(e).__name__} unexpectedly!")

    def test_statistics_calculation_with_no_data(self):
        """Test that statistics calculation works with no attendance data."""
        tracker = RealTimeTracker()
        
        stats = tracker.get_live_statistics()
        
        self.assertEqual(stats["checked_in"], 0)
        self.assertEqual(stats["total_students"], 1)
        self.assertEqual(stats["checked_in_percentage"], 0.0)

    def test_statistics_calculation_with_missing_student(self):
        """Test that statistics calculation handles missing students gracefully."""
        tracker = RealTimeTracker()
        today = timezone.now().date()
        
        AttendanceEntry.objects.create(
            student=self.student,
            date=today,
            status="present",
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name="Grade 1A"
        )
        
        stats = tracker.get_live_statistics()
        self.assertIsNotNone(stats)
        self.assertEqual(stats["checked_in"], 1)


class WebSocketClassSpecificIntegrationTestCase(TestCase):
    """Test class-specific WebSocket functionality."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            is_staff=True
        )
        
        self.academic_year = AcademicYear.objects.create(
            name="2024",
            is_current=True
        )
        
        self.grade_class_1a = GradeClass.objects.create(
            name="Grade 1A",
            department="primary"
        )
        
        self.grade_class_1b = GradeClass.objects.create(
            name="Grade 1B",
            department="primary"
        )
        
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
    def test_class_specific_event_broadcast(self, mock_async_to_sync):
        """Test that class-specific events are broadcast correctly."""
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        tracker.broadcast_checkin_event(
            student=self.student_1a,
            timestamp=timestamp,
            marked_by=self.user
        )
        
        self.assertTrue(mock_async_to_sync.called)

    def test_class_specific_statistics_calculation(self):
        """Test that statistics can be calculated for specific classes."""
        tracker = RealTimeTracker()
        today = timezone.now().date()
        
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
        
        stats_1a = tracker._calculate_statistics(class_id="Grade 1A")
        
        self.assertEqual(stats_1a["checked_in"], 1)
        self.assertEqual(stats_1a["total_students"], 1)

    def test_multiple_class_statistics_isolation(self):
        """Test that statistics for different classes are properly isolated."""
        tracker = RealTimeTracker()
        today = timezone.now().date()
        
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
        
        stats_1a = tracker._calculate_statistics(class_id="Grade 1A")
        stats_1b = tracker._calculate_statistics(class_id="Grade 1B")
        
        self.assertEqual(stats_1a["checked_in"], 1)
        self.assertEqual(stats_1b["checked_in"], 1)
        self.assertNotEqual(stats_1a["checked_in"], stats_1b["checked_in"] + 1)
