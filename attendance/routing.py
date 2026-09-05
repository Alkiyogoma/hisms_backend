"""
WebSocket URL routing for attendance real-time features.

This module defines the URL patterns for WebSocket connections
related to attendance tracking and notifications.
"""

from django.urls import re_path
from attendance.consumers import AttendanceConsumer, ClassAttendanceConsumer

websocket_urlpatterns = [
    # General attendance events WebSocket
    re_path(r"ws/attendance/events/$", AttendanceConsumer.as_asgi()),
    
    # Class-specific attendance WebSocket
    re_path(r"ws/attendance/class/(?P<class_name>[^/]+)/$", ClassAttendanceConsumer.as_asgi()),
]
