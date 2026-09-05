"""
WebSocket consumers for real-time attendance tracking and notifications.

This module provides WebSocket consumers for handling real-time attendance events,
including check-in/check-out notifications and live statistics updates.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth.models import AnonymousUser

from attendance.models import AttendanceEntry
from students.models import Student

logger = logging.getLogger(__name__)


class AttendanceConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time attendance events.
    
    Handles:
    - Student check-in/check-out events
    - Real-time statistics updates
    - Event filtering by class, grade, or department
    - Connection management and authentication
    """

    async def connect(self):
        """
        Handle WebSocket connection.
        
        Authenticates the user and subscribes to attendance events.
        """
        self.user = self.scope["user"]
        self.room_name = "attendance_events"
        self.room_group_name = f"attendance_{self.room_name}"
        
        # Check if user is authenticated
        if isinstance(self.user, AnonymousUser):
            await self.close(code=4001)  # Unauthorized
            return
        
        # Check if user has permission to view attendance
        if not await self._has_attendance_permission():
            await self.close(code=4003)  # Forbidden
            return
        
        # Join the attendance events group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
        logger.info(f"User {self.user.id} connected to attendance WebSocket")

    async def disconnect(self, close_code):
        """
        Handle WebSocket disconnection.
        
        Removes the consumer from the group.
        """
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
        logger.info(f"User {self.user.id} disconnected from attendance WebSocket")

    async def receive(self, text_data):
        """
        Handle incoming WebSocket messages.
        
        Supports:
        - Subscription to filtered events (by class, grade, department)
        - Statistics requests
        """
        try:
            data = json.loads(text_data)
            message_type = data.get("type")
            
            if message_type == "subscribe":
                await self._handle_subscribe(data)
            elif message_type == "get_statistics":
                await self._handle_get_statistics(data)
            else:
                await self.send_error("Unknown message type")
                
        except json.JSONDecodeError:
            await self.send_error("Invalid JSON format")
        except Exception as e:
            logger.error(f"Error processing WebSocket message: {str(e)}")
            await self.send_error("Internal server error")

    async def _handle_subscribe(self, data: Dict[str, Any]):
        """
        Handle subscription to filtered attendance events.
        
        Args:
            data: Message data containing filter criteria
        """
        filters = data.get("filters", {})
        
        # Store filter preferences in the consumer instance
        self.class_name = filters.get("class_name")
        self.grade = filters.get("grade")
        self.department = filters.get("department")
        
        await self.send(text_data=json.dumps({
            "type": "subscription_confirmed",
            "filters": filters,
            "timestamp": datetime.now().isoformat()
        }))

    async def _handle_get_statistics(self, data: Dict[str, Any]):
        """
        Handle request for current attendance statistics.
        
        Args:
            data: Message data (may contain date filter)
        """
        stats = await self._get_today_statistics()
        await self.send(text_data=json.dumps({
            "type": "statistics",
            "data": stats,
            "timestamp": datetime.now().isoformat()
        }))

    async def attendance_event(self, event):
        """
        Handle attendance event broadcast.
        
        Called when an attendance event is broadcast to the group.
        Filters events based on subscription preferences.
        """
        # Check if event matches subscription filters
        if not await self._matches_filters(event):
            return
        
        # Send event to WebSocket
        await self.send(text_data=json.dumps({
            "type": "attendance_event",
            "event_type": event.get("event_type"),  # "checkin" or "checkout"
            "student": event.get("student"),
            "timestamp": event.get("timestamp"),
            "class_id": event.get("class_id"),
            "grade": event.get("grade"),
        }))

    async def statistics_update(self, event):
        """
        Handle statistics update broadcast.
        
        Called when statistics are updated and broadcast to the group.
        """
        await self.send(text_data=json.dumps({
            "type": "statistics_update",
            "data": event.get("data"),
            "timestamp": event.get("timestamp"),
        }))

    async def send_error(self, message: str):
        """
        Send error message to client.
        
        Args:
            message: Error message text
        """
        await self.send(text_data=json.dumps({
            "type": "error",
            "message": message,
            "timestamp": datetime.now().isoformat()
        }))

    @database_sync_to_async
    def _has_attendance_permission(self) -> bool:
        """
        Check if user has permission to view attendance data.
        
        Returns:
            True if user has permission, False otherwise
        """
        # Check if user is staff or has attendance view permission
        if self.user.is_staff:
            return True
        
        # Check for specific permission
        return self.user.has_perm("attendance.view_attendanceentry")

    @database_sync_to_async
    def _matches_filters(self, event: Dict[str, Any]) -> bool:
        """
        Check if event matches subscription filters.
        
        Args:
            event: Event data to check
            
        Returns:
            True if event matches filters or no filters set, False otherwise
        """
        # If no filters set, accept all events
        if not hasattr(self, "class_name") and not hasattr(self, "grade") and not hasattr(self, "department"):
            return True
        
        # Check class filter
        if hasattr(self, "class_name") and self.class_name:
            if event.get("class_name") != self.class_name:
                return False
        
        # Check grade filter
        if hasattr(self, "grade") and self.grade:
            if event.get("grade") != self.grade:
                return False
        
        # Check department filter
        if hasattr(self, "department") and self.department:
            if event.get("department") != self.department:
                return False
        
        return True

    @database_sync_to_async
    def _get_today_statistics(self) -> Dict[str, Any]:
        """
        Get today's attendance statistics.
        
        Returns:
            Dictionary containing attendance statistics
        """
        from django.utils import timezone
        from django.db.models import Q, Count
        
        today = timezone.now().date()
        
        # Get all attendance entries for today
        today_entries = AttendanceEntry.objects.filter(date=today)
        
        # Apply filters if set
        if hasattr(self, "class_name") and self.class_name:
            today_entries = today_entries.filter(class_name=self.class_name)
        
        # Calculate statistics
        checked_in = today_entries.filter(check_in_time__isnull=False).count()
        checked_out = today_entries.filter(check_out_time__isnull=False).count()
        absent = today_entries.filter(status="absent").count()
        present = today_entries.filter(status="present").count()
        
        # Get total active students for percentage calculation
        total_students = Student.objects.filter(status="active")
        if hasattr(self, "class_name") and self.class_name:
            total_students = total_students.filter(class_name=self.class_name)
        total_count = total_students.count()
        
        return {
            "checked_in": checked_in,
            "checked_out": checked_out,
            "absent": absent,
            "present": present,
            "total_students": total_count,
            "date": today.isoformat(),
        }


class ClassAttendanceConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for class-specific real-time attendance.
    
    Handles real-time updates for a specific class's attendance.
    """

    async def connect(self):
        """
        Handle WebSocket connection for class-specific attendance.
        """
        self.user = self.scope["user"]
        self.class_name = self.scope["url_route"]["kwargs"].get("class_name")
        self.room_group_name = f"class_attendance_{self.class_name}"
        
        # Check if user is authenticated
        if isinstance(self.user, AnonymousUser):
            await self.close(code=4001)
            return
        
        # Check if user has permission to view this class's attendance
        if not await self._has_class_permission():
            await self.close(code=4003)
            return
        
        # Join the class-specific group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
        logger.info(f"User {self.user.id} connected to class {self.class_name} attendance WebSocket")

    async def disconnect(self, close_code):
        """
        Handle WebSocket disconnection.
        """
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        """
        Handle incoming messages for class attendance.
        """
        try:
            data = json.loads(text_data)
            message_type = data.get("type")
            
            if message_type == "get_statistics":
                await self._handle_get_statistics()
            else:
                await self.send_error("Unknown message type")
                
        except json.JSONDecodeError:
            await self.send_error("Invalid JSON format")
        except Exception as e:
            logger.error(f"Error processing class attendance message: {str(e)}")
            await self.send_error("Internal server error")

    async def class_attendance_event(self, event):
        """
        Handle class-specific attendance event.
        """
        await self.send(text_data=json.dumps({
            "type": "attendance_event",
            "event_type": event.get("event_type"),
            "student": event.get("student"),
            "timestamp": event.get("timestamp"),
        }))

    async def class_statistics_update(self, event):
        """
        Handle class-specific statistics update.
        """
        await self.send(text_data=json.dumps({
            "type": "statistics_update",
            "data": event.get("data"),
            "timestamp": event.get("timestamp"),
        }))

    async def send_error(self, message: str):
        """
        Send error message to client.
        """
        await self.send(text_data=json.dumps({
            "type": "error",
            "message": message,
            "timestamp": datetime.now().isoformat()
        }))

    @database_sync_to_async
    def _has_class_permission(self) -> bool:
        """
        Check if user has permission to view this class's attendance.
        """
        if self.user.is_staff:
            return True
        
        return self.user.has_perm("attendance.view_attendanceentry")

    @database_sync_to_async
    def _handle_get_statistics(self):
        """
        Get statistics for the specific class.
        """
        from django.utils import timezone
        
        today = timezone.now().date()
        
        today_entries = AttendanceEntry.objects.filter(
            date=today,
            class_name=self.class_name
        )
        
        checked_in = today_entries.filter(check_in_time__isnull=False).count()
        checked_out = today_entries.filter(check_out_time__isnull=False).count()
        absent = today_entries.filter(status="absent").count()
        present = today_entries.filter(status="present").count()
        
        total_students = Student.objects.filter(
            status="active",
            class_name=self.class_name
        ).count()
        
        stats = {
            "checked_in": checked_in,
            "checked_out": checked_out,
            "absent": absent,
            "present": present,
            "total_students": total_students,
            "date": today.isoformat(),
        }
        
        # Send statistics back to client
        import asyncio
        asyncio.create_task(self._send_statistics(stats))

    async def _send_statistics(self, stats: Dict[str, Any]):
        """
        Send statistics to client.
        """
        await self.send(text_data=json.dumps({
            "type": "statistics",
            "data": stats,
            "timestamp": datetime.now().isoformat()
        }))
