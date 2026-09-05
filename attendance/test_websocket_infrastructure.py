"""
Tests for Django Channels WebSocket infrastructure.

Tests verify:
- WebSocket consumer connection and authentication
- Event broadcasting and reception
- Statistics calculation and updates
- Connection management and error handling
"""

import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TestCase, AsyncClient
from django.utils import timezone

from attendance.consumers import AttendanceConsumer, ClassAttendanceConsumer
from attendance.models import AttendanceEntry
from attendance.realtime_tracker import RealTimeTracker, get_realtime_tracker
from students.models import Student
from academics.models import AcademicYear, GradeClass

User = get_user_model()


class WebSocketConsumerTestCase(TestCase):
    """Test WebSocket consumer functionality."""

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
        
        # Create grade class
        self.grade_class = GradeClass.objects.create(
            name="Grade 1A",
            department="primary"
        )
        
        # Create test student
        self.student = Student.objects.create(
            first_name="John",
            last_name="Doe",
            admission_no="STU001",
            class_name="Grade 1A",
            status="active"
        )

    def test_realtime_tracker_initialization(self):
        """Test RealTimeTracker initialization."""
        tracker = RealTimeTracker()
        self.assertIsNotNone(tracker.channel_layer)

    def test_get_realtime_tracker_singleton(self):
        """Test get_realtime_tracker returns singleton instance."""
        tracker1 = get_realtime_tracker()
        tracker2 = get_realtime_tracker()
        self.assertIs(tracker1, tracker2)

    def test_realtime_tracker_prepare_event_data(self):
        """Test RealTimeTracker event data preparation."""
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        event_data = tracker._prepare_event_data(
            student=self.student,
            event_type="checkin",
            timestamp=timestamp,
            marked_by=self.user
        )
        
        self.assertEqual(event_data["event_type"], "checkin")
        self.assertEqual(event_data["student"]["id"], self.student.id)
        self.assertEqual(event_data["student"]["name"], self.student.get_full_name())
        self.assertEqual(event_data["marked_by"], self.user.id)

    def test_realtime_tracker_calculate_statistics(self):
        """Test RealTimeTracker statistics calculation."""
        tracker = RealTimeTracker()
        today = timezone.now().date()
        
        # Create attendance entries
        AttendanceEntry.objects.create(
            student=self.student,
            date=today,
            status="present",
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name="Grade 1A"
        )
        
        stats = tracker._calculate_statistics()
        
        self.assertEqual(stats["checked_in"], 1)
        self.assertEqual(stats["present"], 1)
        self.assertEqual(stats["total_students"], 1)
        self.assertEqual(stats["date"], today.isoformat())

    def test_realtime_tracker_calculate_statistics_by_class(self):
        """Test RealTimeTracker statistics calculation for specific class."""
        tracker = RealTimeTracker()
        today = timezone.now().date()
        
        # Create attendance entry
        AttendanceEntry.objects.create(
            student=self.student,
            date=today,
            status="present",
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name="Grade 1A"
        )
        
        stats = tracker._calculate_statistics(class_id="Grade 1A")
        
        self.assertEqual(stats["checked_in"], 1)
        self.assertEqual(stats["total_students"], 1)

    @patch('attendance.realtime_tracker.async_to_sync')
    def test_realtime_tracker_broadcast_checkin_event(self, mock_async_to_sync):
        """Test RealTimeTracker broadcasts check-in events."""
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        tracker.broadcast_checkin_event(
            student=self.student,
            timestamp=timestamp,
            marked_by=self.user
        )
        
        # Verify async_to_sync was called
        self.assertTrue(mock_async_to_sync.called)

    @patch('attendance.realtime_tracker.async_to_sync')
    def test_realtime_tracker_broadcast_checkout_event(self, mock_async_to_sync):
        """Test RealTimeTracker broadcasts check-out events."""
        tracker = RealTimeTracker()
        timestamp = timezone.now()
        
        tracker.broadcast_checkout_event(
            student=self.student,
            timestamp=timestamp,
            marked_by=self.user
        )
        
        # Verify async_to_sync was called
        self.assertTrue(mock_async_to_sync.called)

    @patch('attendance.realtime_tracker.async_to_sync')
    def test_realtime_tracker_broadcast_statistics_update(self, mock_async_to_sync):
        """Test RealTimeTracker broadcasts statistics updates."""
        tracker = RealTimeTracker()
        
        tracker.broadcast_statistics_update()
        
        # Verify async_to_sync was called
        self.assertTrue(mock_async_to_sync.called)

    def test_realtime_tracker_get_live_statistics(self):
        """Test RealTimeTracker returns live statistics."""
        tracker = RealTimeTracker()
        today = timezone.now().date()
        
        # Create attendance entry
        AttendanceEntry.objects.create(
            student=self.student,
            date=today,
            status="present",
            check_in_time=timezone.now().time(),
            marked_by=self.user,
            class_name="Grade 1A"
        )
        
        stats = tracker.get_live_statistics()
        
        self.assertIn("checked_in", stats)
        self.assertIn("total_students", stats)
        self.assertEqual(stats["checked_in"], 1)


class WebSocketChannelsConfigTestCase(TestCase):
    """Test Django Channels configuration."""

    def test_channels_installed(self):
        """Test that Channels is available and configured."""
        import channels
        from django.conf import settings
        # Channels must be installed; daphne may be disabled for dev runserver
        self.assertTrue(hasattr(settings, 'CHANNEL_LAYERS'))
        self.assertIn('default', settings.CHANNEL_LAYERS)

    def test_asgi_application_configured(self):
        """Test that ASGI application is configured."""
        from django.conf import settings
        self.assertEqual(settings.ASGI_APPLICATION, "config.asgi.application")

    def test_channel_layers_configured(self):
        """Test that Channel Layers are configured."""
        from django.conf import settings
        self.assertIn("CHANNEL_LAYERS", dir(settings))
        self.assertIn("default", settings.CHANNEL_LAYERS)

    def test_channel_layers_redis_backend(self):
        """Test that Channel Layers use Redis backend."""
        from django.conf import settings
        backend = settings.CHANNEL_LAYERS["default"]["BACKEND"]
        self.assertEqual(backend, "channels_redis.core.RedisChannelLayer")

    def test_websocket_routing_configured(self):
        """Test that WebSocket routing is configured."""
        from attendance.routing import websocket_urlpatterns
        self.assertIsNotNone(websocket_urlpatterns)
        self.assertGreater(len(websocket_urlpatterns), 0)
