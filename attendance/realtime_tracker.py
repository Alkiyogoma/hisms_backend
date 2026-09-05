"""
Real-time attendance tracking and event broadcasting.

This module provides functionality for broadcasting attendance events
to connected WebSocket clients in real-time.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone

from attendance.models import AttendanceEntry
from students.models import Student

logger = logging.getLogger(__name__)


class RealTimeTracker:
    """
    Handles real-time broadcasting of attendance events and statistics.
    
    Provides methods for:
    - Broadcasting check-in/check-out events
    - Updating real-time statistics
    - Filtering events by class, grade, or department
    """

    def __init__(self):
        """Initialize the real-time tracker."""
        self.channel_layer = get_channel_layer()

    def broadcast_checkin_event(self, student: Student, timestamp: datetime, marked_by=None):
        """
        Broadcast a student check-in event to all connected clients.
        
        Args:
            student: The Student object that checked in
            timestamp: The check-in timestamp
            marked_by: The user who marked the attendance (optional)
        """
        try:
            event_data = self._prepare_event_data(
                student=student,
                event_type="checkin",
                timestamp=timestamp,
                marked_by=marked_by
            )
            
            # Broadcast to general attendance group
            async_to_sync(self.channel_layer.group_send)(
                "attendance_attendance_events",
                {
                    "type": "attendance_event",
                    **event_data
                }
            )
            
            # Broadcast to class-specific group
            if student.class_name:
                async_to_sync(self.channel_layer.group_send)(
                    f"class_attendance_{student.class_name}",
                    {
                        "type": "class_attendance_event",
                        **event_data
                    }
                )
            
            logger.info(f"Broadcasted check-in event for student {student.id}")
            
        except Exception as e:
            logger.error(f"Error broadcasting check-in event: {str(e)}")

    def broadcast_checkout_event(self, student: Student, timestamp: datetime, marked_by=None):
        """
        Broadcast a student check-out event to all connected clients.
        
        Args:
            student: The Student object that checked out
            timestamp: The check-out timestamp
            marked_by: The user who marked the attendance (optional)
        """
        try:
            event_data = self._prepare_event_data(
                student=student,
                event_type="checkout",
                timestamp=timestamp,
                marked_by=marked_by
            )
            
            # Broadcast to general attendance group
            async_to_sync(self.channel_layer.group_send)(
                "attendance_attendance_events",
                {
                    "type": "attendance_event",
                    **event_data
                }
            )
            
            # Broadcast to class-specific group
            if student.class_name:
                async_to_sync(self.channel_layer.group_send)(
                    f"class_attendance_{student.class_name}",
                    {
                        "type": "class_attendance_event",
                        **event_data
                    }
                )
            
            logger.info(f"Broadcasted check-out event for student {student.id}")
            
        except Exception as e:
            logger.error(f"Error broadcasting check-out event: {str(e)}")

    def broadcast_statistics_update(self, class_id: Optional[int] = None):
        """
        Broadcast updated attendance statistics to all connected clients.
        
        Args:
            class_id: Optional class ID to broadcast class-specific statistics
        """
        try:
            stats = self._calculate_statistics(class_id)
            
            if class_id:
                # Broadcast to class-specific group
                async_to_sync(self.channel_layer.group_send)(
                    f"class_attendance_{class_id}",
                    {
                        "type": "class_statistics_update",
                        "data": stats,
                        "timestamp": datetime.now().isoformat()
                    }
                )
            else:
                # Broadcast to general attendance group
                async_to_sync(self.channel_layer.group_send)(
                    "attendance_attendance_events",
                    {
                        "type": "statistics_update",
                        "data": stats,
                        "timestamp": datetime.now().isoformat()
                    }
                )
            
            logger.info(f"Broadcasted statistics update for class {class_id or 'all'}")
            
        except Exception as e:
            logger.error(f"Error broadcasting statistics update: {str(e)}")

    def get_live_statistics(self, class_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Get current live attendance statistics.
        
        Args:
            class_id: Optional class ID to get class-specific statistics
            
        Returns:
            Dictionary containing attendance statistics
        """
        return self._calculate_statistics(class_id)

    def _prepare_event_data(
        self,
        student: Student,
        event_type: str,
        timestamp: datetime,
        marked_by=None
    ) -> Dict[str, Any]:
        """
        Prepare event data for broadcasting.
        
        Args:
            student: The student involved in the event
            event_type: Type of event ("checkin" or "checkout")
            timestamp: Event timestamp
            marked_by: User who marked the attendance
            
        Returns:
            Dictionary containing formatted event data
        """
        return {
            "event_type": event_type,
            "student": {
                "id": student.id,
                "name": student.get_full_name(),
                "admission_no": student.admission_no,
                "class_name": student.class_name,
            },
            "timestamp": timestamp.isoformat(),
            "marked_by": marked_by.id if marked_by else None,
            "class_name": student.class_name,
        }

    def _calculate_statistics(self, class_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Calculate current attendance statistics.
        
        Args:
            class_id: Optional class name to calculate class-specific statistics
            
        Returns:
            Dictionary containing calculated statistics
        """
        today = timezone.now().date()
        
        # Get today's attendance entries
        entries = AttendanceEntry.objects.filter(date=today)
        
        if class_id:
            # class_id is actually a class_name string in this context
            entries = entries.filter(class_name=class_id)
        
        # Calculate counts
        checked_in = entries.filter(check_in_time__isnull=False).count()
        checked_out = entries.filter(check_out_time__isnull=False).count()
        absent = entries.filter(status="absent").count()
        present = entries.filter(status="present").count()
        
        # Get total active students
        total_students = Student.objects.filter(status="active")
        if class_id:
            total_students = total_students.filter(class_name=class_id)
        total_count = total_students.count()
        
        # Calculate percentages
        checked_in_percentage = (checked_in / total_count * 100) if total_count > 0 else 0
        present_percentage = (present / total_count * 100) if total_count > 0 else 0
        
        return {
            "date": today.isoformat(),
            "checked_in": checked_in,
            "checked_out": checked_out,
            "absent": absent,
            "present": present,
            "total_students": total_count,
            "checked_in_percentage": round(checked_in_percentage, 2),
            "present_percentage": round(present_percentage, 2),
        }


# Global instance for easy access
_tracker = None


def get_realtime_tracker() -> RealTimeTracker:
    """
    Get or create the global RealTimeTracker instance.
    
    Returns:
        RealTimeTracker instance
    """
    global _tracker
    if _tracker is None:
        _tracker = RealTimeTracker()
    return _tracker
