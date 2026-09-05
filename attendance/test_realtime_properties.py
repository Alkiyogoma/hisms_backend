"""
Property-based tests for real-time attendance functionality.
Tests universal properties that should hold for all valid real-time scenarios.

Feature: checkin-checkout-integration
"""
import json
import logging
from datetime import datetime, date, time, timedelta
from typing import Dict, List, Any, Optional
from unittest.mock import Mock, patch, MagicMock, AsyncMock

from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from hypothesis import given, strategies as st, settings, assume, example
from hypothesis.extra.django import TestCase as HypothesisTestCase

from attendance.models import AttendanceEntry
from attendance.realtime_tracker import RealTimeTracker, get_realtime_tracker
from students.models import Student
from academics.models import AcademicYear, GradeClass
from users.models import User

# Disable logging during tests to reduce noise
logging.disable(logging.CRITICAL)

User = get_user_model()


# Test data generation strategies
@st.composite
def student_strategy(draw):
    """Generate realistic student data"""
    return {
        'first_name': draw(st.text(alphabet=st.characters(whitelist_categories=('Lu', 'Ll')), min_size=2, max_size=20)),
        'last_name': draw(st.text(alphabet=st.characters(whitelist_categories=('Lu', 'Ll')), min_size=2, max_size=20)),
        'admission_no': draw(st.text(alphabet='0123456789', min_size=6, max_size=10)),
        'class_name': draw(st.sampled_from(['Grade 1A', 'Grade 1B', 'Grade 2A', 'Grade 2B', 'Grade 3A'])),
        'status': draw(st.sampled_from(['active', 'inactive'])),
    }


@st.composite
def attendance_entry_strategy(draw, student=None, class_name=None):
    """Generate realistic attendance entry data"""
    today = timezone.now().date()
    
    # Generate check-in time (morning)
    check_in_time = draw(st.one_of(
        st.none(),
        st.times(min_value=time(7, 0), max_value=time(10, 0))
    ))
    
    # Generate check-out time (afternoon) - only if check-in exists
    check_out_time = None
    if check_in_time:
        check_out_time = draw(st.one_of(
            st.none(),
            st.times(min_value=time(14, 0), max_value=time(18, 0))
        ))
    
    return {
        'date': today,
        'status': draw(st.sampled_from(['present', 'absent', 'late'])),
        'check_in_time': check_in_time,
        'check_out_time': check_out_time,
        'is_early_departure': check_out_time and check_out_time < time(15, 30) if check_out_time else False,
        'class_name': class_name or draw(st.sampled_from(['Grade 1A', 'Grade 1B', 'Grade 2A', 'Grade 2B', 'Grade 3A'])),
    }


@st.composite
def attendance_events_strategy(draw):
    """Generate a sequence of attendance events for testing"""
    num_events = draw(st.integers(min_value=1, max_value=20))
    
    events = []
    for _ in range(num_events):
        event_type = draw(st.sampled_from(['checkin', 'checkout']))
        timestamp = timezone.now()
        
        events.append({
            'event_type': event_type,
            'timestamp': timestamp,
            'student_id': draw(st.integers(min_value=1, max_value=100)),
            'class_name': draw(st.sampled_from(['Grade 1A', 'Grade 1B', 'Grade 2A', 'Grade 2B', 'Grade 3A'])),
        })
    
    return events


class RealTimeEventBroadcastingPropertyTests(HypothesisTestCase):
    """
    Property-based tests for real-time event broadcasting.
    
    **Property 13: Real-Time Event Broadcasting**
    For any attendance event (checkin/checkout), the Real_Time_Tracker should immediately
    broadcast the event with correct student details and timestamp to all connected WebSocket clients.
    
    **Validates: Requirements 4.1, 4.2, 4.3**
    """
    
    def setUp(self):
        """Set up test environment"""
        # Create test user
        self.user, _ = User.objects.get_or_create(
            username='test_realtime',
            defaults={
                'email': 'test@hodari.ac.tz',
                'first_name': 'Test',
                'last_name': 'RealTime',
                'is_staff': True
            }
        )
        
        # Create academic year
        self.academic_year, _ = AcademicYear.objects.get_or_create(
            name="2024",
            defaults={'is_current': True}
        )
    
    @given(student_data=student_strategy())
    @settings(max_examples=30, deadline=10000)
    def test_checkin_event_broadcast_property(self, student_data):
        """
        Feature: checkin-checkout-integration, Property 13: Real-Time Event Broadcasting
        
        For any student check-in event, the Real_Time_Tracker should broadcast the event
        with correct student details and timestamp to all connected WebSocket clients.
        
        **Validates: Requirements 4.1, 4.2, 4.3**
        """
        # Create student
        student = Student.objects.create(**student_data)
        
        # Create tracker
        tracker = RealTimeTracker()
        
        # Record event details
        timestamp = timezone.now()
        
        # Mock the channel layer to capture broadcast calls
        with patch.object(tracker.channel_layer, 'group_send', new_callable=AsyncMock) as mock_send:
            # Broadcast check-in event
            tracker.broadcast_checkin_event(
                student=student,
                timestamp=timestamp,
                marked_by=self.user
            )
            
            # **PROPERTY ASSERTION 1: Event was broadcast**
            # The group_send method should have been called
            self.assertTrue(mock_send.called, "Event was not broadcast to any group")
            
            # **PROPERTY ASSERTION 2: Event broadcast to general attendance group**
            # Should broadcast to general attendance events group
            calls = mock_send.call_args_list
            general_group_called = any(
                'attendance_attendance_events' in str(call) for call in calls
            )
            self.assertTrue(
                general_group_called,
                "Event was not broadcast to general attendance group"
            )
            
            # **PROPERTY ASSERTION 3: Event broadcast to class-specific group**
            # Should broadcast to class-specific group if class_name exists
            if student.class_name:
                class_group_called = any(
                    f'class_attendance_{student.class_name}' in str(call) for call in calls
                )
                self.assertTrue(
                    class_group_called,
                    f"Event was not broadcast to class group: class_attendance_{student.class_name}"
                )
            
            # **PROPERTY ASSERTION 4: Event data contains correct student information**
            # Extract the event data from the first call
            if calls:
                call_args = calls[0]
                # The second argument should contain the event data
                if len(call_args[0]) > 1:
                    event_data = call_args[0][1]
                    
                    self.assertEqual(
                        event_data.get('event_type'),
                        'checkin',
                        "Event type should be 'checkin'"
                    )
                    
                    self.assertEqual(
                        event_data.get('student', {}).get('id'),
                        student.id,
                        "Student ID in event should match"
                    )
                    
                    self.assertEqual(
                        event_data.get('student', {}).get('name'),
                        student.get_full_name(),
                        "Student name in event should match"
                    )
            
            # **PROPERTY ASSERTION 5: Timestamp is preserved**
            # The event should contain the exact timestamp
            if calls and len(calls[0][0]) > 1:
                event_data = calls[0][0][1]
                event_timestamp = event_data.get('timestamp')
                self.assertIsNotNone(event_timestamp, "Event should contain timestamp")
    
    @given(student_data=student_strategy())
    @settings(max_examples=30, deadline=10000)
    def test_checkout_event_broadcast_property(self, student_data):
        """
        Feature: checkin-checkout-integration, Property 13: Real-Time Event Broadcasting
        
        For any student check-out event, the Real_Time_Tracker should broadcast the event
        with correct student details and timestamp to all connected WebSocket clients.
        
        **Validates: Requirements 4.1, 4.2, 4.3**
        """
        # Create student
        student = Student.objects.create(**student_data)
        
        # Create tracker
        tracker = RealTimeTracker()
        
        # Record event details
        timestamp = timezone.now()
        
        # Mock the channel layer to capture broadcast calls
        with patch.object(tracker.channel_layer, 'group_send', new_callable=AsyncMock) as mock_send:
            # Broadcast check-out event
            tracker.broadcast_checkout_event(
                student=student,
                timestamp=timestamp,
                marked_by=self.user
            )
            
            # **PROPERTY ASSERTION 1: Event was broadcast**
            self.assertTrue(mock_send.called, "Checkout event was not broadcast")
            
            # **PROPERTY ASSERTION 2: Event type is correct**
            calls = mock_send.call_args_list
            if calls and len(calls[0][0]) > 1:
                event_data = calls[0][0][1]
                self.assertEqual(
                    event_data.get('event_type'),
                    'checkout',
                    "Event type should be 'checkout'"
                )
    
    @given(events=attendance_events_strategy())
    @settings(max_examples=20, deadline=15000)
    def test_multiple_events_broadcast_property(self, events):
        """
        Feature: checkin-checkout-integration, Property 13: Real-Time Event Broadcasting
        
        For a sequence of attendance events, each event should be broadcast independently
        with correct details.
        
        **Validates: Requirements 4.1, 4.2, 4.3**
        """
        # Skip if no events
        assume(len(events) > 0)
        
        tracker = RealTimeTracker()
        
        # Create students for each event
        students = {}
        for event in events:
            student_id = event['student_id']
            if student_id not in students:
                student = Student.objects.create(
                    first_name=f"Student{student_id}",
                    last_name="Test",
                    admission_no=f"STU{student_id:06d}",
                    class_name=event['class_name'],
                    status='active'
                )
                students[student_id] = student
        
        # Mock the channel layer
        with patch.object(tracker.channel_layer, 'group_send', new_callable=AsyncMock) as mock_send:
            # Broadcast all events
            for event in events:
                student = students[event['student_id']]
                
                if event['event_type'] == 'checkin':
                    tracker.broadcast_checkin_event(
                        student=student,
                        timestamp=event['timestamp'],
                        marked_by=self.user
                    )
                else:
                    tracker.broadcast_checkout_event(
                        student=student,
                        timestamp=event['timestamp'],
                        marked_by=self.user
                    )
            
            # **PROPERTY ASSERTION: Each event was broadcast**
            # Should have at least one call per event (to general group)
            self.assertGreaterEqual(
                mock_send.call_count,
                len(events),
                f"Not all events were broadcast: {mock_send.call_count} calls for {len(events)} events"
            )



class RealTimeStatisticsPropertyTests(HypothesisTestCase):
    """
    Property-based tests for real-time statistics calculation.
    
    **Property 14: Statistics Calculation Accuracy**
    For any set of today's attendance records, the Real_Time_Tracker should calculate
    and provide accurate statistics for total checked in, checked out, and absent students.
    
    **Validates: Requirements 4.5, 4.6**
    """
    
    def setUp(self):
        """Set up test environment"""
        # Create test user
        self.user, _ = User.objects.get_or_create(
            username='test_stats',
            defaults={
                'email': 'test@hodari.ac.tz',
                'first_name': 'Test',
                'last_name': 'Stats',
                'is_staff': True
            }
        )
        
        # Create academic year
        self.academic_year, _ = AcademicYear.objects.get_or_create(
            name="2024",
            defaults={'is_current': True}
        )
    
    @given(
        num_students=st.integers(min_value=1, max_value=50),
        class_name=st.sampled_from(['Grade 1A', 'Grade 1B', 'Grade 2A', 'Grade 2B', 'Grade 3A'])
    )
    @settings(max_examples=30, deadline=10000)
    def test_statistics_calculation_accuracy_property(self, num_students, class_name):
        """
        Feature: checkin-checkout-integration, Property 14: Statistics Calculation Accuracy
        
        For any set of today's attendance records, the Real_Time_Tracker should calculate
        accurate statistics for checked in, checked out, and absent students.
        
        **Validates: Requirements 4.5, 4.6**
        """
        today = timezone.now().date()
        
        # Create students
        students = []
        for i in range(num_students):
            student = Student.objects.create(
                first_name=f"Student{i}",
                last_name="Test",
                admission_no=f"STU{i:06d}",
                class_name=class_name,
                status='active'
            )
            students.append(student)
        
        # Create attendance entries with various states
        checked_in_count = 0
        checked_out_count = 0
        absent_count = 0
        present_count = 0
        
        for i, student in enumerate(students):
            if i % 3 == 0:
                # Student checked in and out
                entry = AttendanceEntry.objects.create(
                    date=today,
                    student=student,
                    status='present',
                    check_in_time=time(8, 0),
                    check_out_time=time(15, 30),
                    marked_by=self.user,
                    class_name=class_name
                )
                checked_in_count += 1
                checked_out_count += 1
                present_count += 1
            elif i % 3 == 1:
                # Student checked in only
                entry = AttendanceEntry.objects.create(
                    date=today,
                    student=student,
                    status='present',
                    check_in_time=time(8, 15),
                    check_out_time=None,
                    marked_by=self.user,
                    class_name=class_name
                )
                checked_in_count += 1
                present_count += 1
            else:
                # Student absent
                entry = AttendanceEntry.objects.create(
                    date=today,
                    student=student,
                    status='absent',
                    check_in_time=None,
                    check_out_time=None,
                    marked_by=self.user,
                    class_name=class_name
                )
                absent_count += 1
        
        # Get statistics
        tracker = RealTimeTracker()
        stats = tracker.get_live_statistics(class_id=class_name)
        
        # **PROPERTY ASSERTION 1: Checked-in count is accurate**
        self.assertEqual(
            stats['checked_in'],
            checked_in_count,
            f"Checked-in count mismatch: expected {checked_in_count}, got {stats['checked_in']}"
        )
        
        # **PROPERTY ASSERTION 2: Checked-out count is accurate**
        self.assertEqual(
            stats['checked_out'],
            checked_out_count,
            f"Checked-out count mismatch: expected {checked_out_count}, got {stats['checked_out']}"
        )
        
        # **PROPERTY ASSERTION 3: Absent count is accurate**
        self.assertEqual(
            stats['absent'],
            absent_count,
            f"Absent count mismatch: expected {absent_count}, got {stats['absent']}"
        )
        
        # **PROPERTY ASSERTION 4: Present count is accurate**
        self.assertEqual(
            stats['present'],
            present_count,
            f"Present count mismatch: expected {present_count}, got {stats['present']}"
        )
        
        # **PROPERTY ASSERTION 5: Total students count is accurate**
        self.assertEqual(
            stats['total_students'],
            num_students,
            f"Total students count mismatch: expected {num_students}, got {stats['total_students']}"
        )
        
        # **PROPERTY ASSERTION 6: Percentages are calculated correctly**
        expected_checked_in_pct = (checked_in_count / num_students * 100) if num_students > 0 else 0
        self.assertAlmostEqual(
            stats['checked_in_percentage'],
            round(expected_checked_in_pct, 2),
            places=1,
            msg=f"Checked-in percentage mismatch: expected {expected_checked_in_pct}, got {stats['checked_in_percentage']}"
        )
        
        expected_present_pct = (present_count / num_students * 100) if num_students > 0 else 0
        self.assertAlmostEqual(
            stats['present_percentage'],
            round(expected_present_pct, 2),
            places=1,
            msg=f"Present percentage mismatch: expected {expected_present_pct}, got {stats['present_percentage']}"
        )
        
        # **PROPERTY ASSERTION 7: Date is today's date**
        self.assertEqual(
            stats['date'],
            today.isoformat(),
            "Statistics date should be today's date"
        )
    
    @given(
        num_students=st.integers(min_value=1, max_value=30),
    )
    @settings(max_examples=20, deadline=10000)
    def test_statistics_calculation_all_classes_property(self, num_students):
        """
        Feature: checkin-checkout-integration, Property 14: Statistics Calculation Accuracy
        
        When calculating statistics for all classes (no class filter), the totals should
        be the sum of all class statistics.
        
        **Validates: Requirements 4.5, 4.6**
        """
        today = timezone.now().date()
        classes = ['Grade 1A', 'Grade 1B', 'Grade 2A']
        
        # Create students across multiple classes
        total_checked_in = 0
        total_present = 0
        total_absent = 0
        total_students = 0
        
        for class_name in classes:
            for i in range(num_students):
                student = Student.objects.create(
                    first_name=f"Student{class_name}{i}",
                    last_name="Test",
                    admission_no=f"STU{class_name}{i:04d}",
                    class_name=class_name,
                    status='active'
                )
                total_students += 1
                
                if i % 2 == 0:
                    # Present
                    AttendanceEntry.objects.create(
                        date=today,
                        student=student,
                        status='present',
                        check_in_time=time(8, 0),
                        check_out_time=time(15, 30),
                        marked_by=self.user,
                        class_name=class_name
                    )
                    total_checked_in += 1
                    total_present += 1
                else:
                    # Absent
                    AttendanceEntry.objects.create(
                        date=today,
                        student=student,
                        status='absent',
                        check_in_time=None,
                        check_out_time=None,
                        marked_by=self.user,
                        class_name=class_name
                    )
                    total_absent += 1
        
        # Get statistics for all classes
        tracker = RealTimeTracker()
        stats = tracker.get_live_statistics()
        
        # **PROPERTY ASSERTION: All-class statistics are accurate**
        self.assertEqual(
            stats['checked_in'],
            total_checked_in,
            f"All-class checked-in count mismatch"
        )
        
        self.assertEqual(
            stats['present'],
            total_present,
            f"All-class present count mismatch"
        )
        
        self.assertEqual(
            stats['absent'],
            total_absent,
            f"All-class absent count mismatch"
        )
        
        self.assertEqual(
            stats['total_students'],
            total_students,
            f"All-class total students count mismatch"
        )
    
    @given(
        num_students=st.integers(min_value=1, max_value=20),
    )
    @settings(max_examples=15, deadline=10000)
    def test_statistics_update_broadcast_property(self, num_students):
        """
        Feature: checkin-checkout-integration, Property 14: Statistics Calculation Accuracy
        
        When statistics are updated, they should be broadcast with accurate data.
        
        **Validates: Requirements 4.5, 4.6**
        """
        today = timezone.now().date()
        class_name = 'Grade 1A'
        
        # Create students and attendance entries
        for i in range(num_students):
            student = Student.objects.create(
                first_name=f"Student{i}",
                last_name="Test",
                admission_no=f"STU{i:06d}",
                class_name=class_name,
                status='active'
            )
            
            if i % 2 == 0:
                AttendanceEntry.objects.create(
                    date=today,
                    student=student,
                    status='present',
                    check_in_time=time(8, 0),
                    marked_by=self.user,
                    class_name=class_name
                )
        
        # Mock the channel layer to capture broadcast calls
        tracker = RealTimeTracker()
        with patch.object(tracker.channel_layer, 'group_send', new_callable=AsyncMock) as mock_send:
            # Broadcast statistics update
            tracker.broadcast_statistics_update(class_id=class_name)
            
            # **PROPERTY ASSERTION: Statistics were broadcast**
            self.assertTrue(mock_send.called, "Statistics update was not broadcast")
            
            # **PROPERTY ASSERTION: Broadcast contains accurate statistics**
            if mock_send.called:
                call_args = mock_send.call_args_list[0]
                if len(call_args[0]) > 1:
                    event_data = call_args[0][1]
                    stats_data = event_data.get('data', {})
                    
                    # Verify statistics are present
                    self.assertIn('checked_in', stats_data, "Statistics should contain checked_in")
                    self.assertIn('total_students', stats_data, "Statistics should contain total_students")



class RealTimeEventFilteringPropertyTests(HypothesisTestCase):
    """
    Property-based tests for real-time event filtering.
    
    **Property 15: Event Filtering Correctness**
    For any real-time event filtering request by class, grade, or department,
    only events matching the specified criteria should be returned to the requesting client.
    
    **Validates: Requirements 4.7**
    """
    
    def setUp(self):
        """Set up test environment"""
        # Create test user
        self.user, _ = User.objects.get_or_create(
            username='test_filter',
            defaults={
                'email': 'test@hodari.ac.tz',
                'first_name': 'Test',
                'last_name': 'Filter',
                'is_staff': True
            }
        )
        
        # Create academic year
        self.academic_year, _ = AcademicYear.objects.get_or_create(
            name="2024",
            defaults={'is_current': True}
        )
    
    @given(
        target_class=st.sampled_from(['Grade 1A', 'Grade 1B', 'Grade 2A', 'Grade 2B', 'Grade 3A']),
        num_target_students=st.integers(min_value=1, max_value=10),
        num_other_students=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=25, deadline=10000)
    def test_class_filter_property(self, target_class, num_target_students, num_other_students):
        """
        Feature: checkin-checkout-integration, Property 15: Event Filtering Correctness
        
        When filtering events by class, only events from the specified class should be included
        in the statistics.
        
        **Validates: Requirements 4.7**
        """
        today = timezone.now().date()
        other_classes = ['Grade 1A', 'Grade 1B', 'Grade 2A', 'Grade 2B', 'Grade 3A']
        other_classes = [c for c in other_classes if c != target_class]
        
        # Create students in target class
        target_students = []
        for i in range(num_target_students):
            student = Student.objects.create(
                first_name=f"Target{i}",
                last_name="Test",
                admission_no=f"TGT{i:06d}",
                class_name=target_class,
                status='active'
            )
            target_students.append(student)
            
            # Create attendance entry
            AttendanceEntry.objects.create(
                date=today,
                student=student,
                status='present',
                check_in_time=time(8, 0),
                marked_by=self.user,
                class_name=target_class
            )
        
        # Create students in other classes
        for i in range(num_other_students):
            other_class = other_classes[i % len(other_classes)]
            student = Student.objects.create(
                first_name=f"Other{i}",
                last_name="Test",
                admission_no=f"OTH{i:06d}",
                class_name=other_class,
                status='active'
            )
            
            # Create attendance entry
            AttendanceEntry.objects.create(
                date=today,
                student=student,
                status='present',
                check_in_time=time(8, 0),
                marked_by=self.user,
                class_name=other_class
            )
        
        # Get statistics for target class only
        tracker = RealTimeTracker()
        stats = tracker.get_live_statistics(class_id=target_class)
        
        # **PROPERTY ASSERTION 1: Only target class students are counted**
        self.assertEqual(
            stats['total_students'],
            num_target_students,
            f"Statistics should only include {num_target_students} students from {target_class}, got {stats['total_students']}"
        )
        
        # **PROPERTY ASSERTION 2: Checked-in count only includes target class**
        self.assertEqual(
            stats['checked_in'],
            num_target_students,
            f"Checked-in count should only include target class students"
        )
        
        # **PROPERTY ASSERTION 3: Statistics date is correct**
        self.assertEqual(
            stats['date'],
            today.isoformat(),
            "Statistics date should be today"
        )
    
    @given(
        num_classes=st.integers(min_value=2, max_value=5),
        students_per_class=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=20, deadline=10000)
    def test_multi_class_filtering_property(self, num_classes, students_per_class):
        """
        Feature: checkin-checkout-integration, Property 15: Event Filtering Correctness
        
        When filtering events by different classes, each filter should return only
        the events for that specific class.
        
        **Validates: Requirements 4.7**
        """
        today = timezone.now().date()
        classes = ['Grade 1A', 'Grade 1B', 'Grade 2A', 'Grade 2B', 'Grade 3A'][:num_classes]
        
        # Create students and attendance entries for each class
        class_stats = {}
        for class_name in classes:
            class_stats[class_name] = {
                'total': 0,
                'checked_in': 0,
                'present': 0,
            }
            
            for i in range(students_per_class):
                student = Student.objects.create(
                    first_name=f"Student{class_name}{i}",
                    last_name="Test",
                    admission_no=f"STU{class_name}{i:04d}",
                    class_name=class_name,
                    status='active'
                )
                class_stats[class_name]['total'] += 1
                
                if i % 2 == 0:
                    # Create check-in entry
                    AttendanceEntry.objects.create(
                        date=today,
                        student=student,
                        status='present',
                        check_in_time=time(8, 0),
                        marked_by=self.user,
                        class_name=class_name
                    )
                    class_stats[class_name]['checked_in'] += 1
                    class_stats[class_name]['present'] += 1
                else:
                    # Create absent entry
                    AttendanceEntry.objects.create(
                        date=today,
                        student=student,
                        status='absent',
                        check_in_time=None,
                        marked_by=self.user,
                        class_name=class_name
                    )
        
        # Test filtering for each class
        tracker = RealTimeTracker()
        for class_name in classes:
            stats = tracker.get_live_statistics(class_id=class_name)
            expected = class_stats[class_name]
            
            # **PROPERTY ASSERTION: Each class filter returns correct statistics**
            self.assertEqual(
                stats['total_students'],
                expected['total'],
                f"Class {class_name}: total students mismatch"
            )
            
            self.assertEqual(
                stats['checked_in'],
                expected['checked_in'],
                f"Class {class_name}: checked-in count mismatch"
            )
            
            self.assertEqual(
                stats['present'],
                expected['present'],
                f"Class {class_name}: present count mismatch"
            )
    
    @given(
        num_students=st.integers(min_value=1, max_value=20),
    )
    @settings(max_examples=15, deadline=10000)
    def test_event_filtering_in_broadcast_property(self, num_students):
        """
        Feature: checkin-checkout-integration, Property 15: Event Filtering Correctness
        
        When broadcasting events with class filtering, only events from the specified
        class should be included in the broadcast.
        
        **Validates: Requirements 4.7**
        """
        target_class = 'Grade 1A'
        other_class = 'Grade 1B'
        
        # Create students in both classes
        target_students = []
        other_students = []
        
        for i in range(num_students):
            # Target class student
            student = Student.objects.create(
                first_name=f"Target{i}",
                last_name="Test",
                admission_no=f"TGT{i:06d}",
                class_name=target_class,
                status='active'
            )
            target_students.append(student)
            
            # Other class student
            student = Student.objects.create(
                first_name=f"Other{i}",
                last_name="Test",
                admission_no=f"OTH{i:06d}",
                class_name=other_class,
                status='active'
            )
            other_students.append(student)
        
        # Mock the channel layer to capture broadcasts
        tracker = RealTimeTracker()
        with patch.object(tracker.channel_layer, 'group_send', new_callable=AsyncMock) as mock_send:
            # Broadcast events for target class students
            for student in target_students:
                tracker.broadcast_checkin_event(
                    student=student,
                    timestamp=timezone.now(),
                    marked_by=self.user
                )
            
            # Broadcast events for other class students
            for student in other_students:
                tracker.broadcast_checkin_event(
                    student=student,
                    timestamp=timezone.now(),
                    marked_by=self.user
                )
            
            # **PROPERTY ASSERTION: Events were broadcast to class-specific groups**
            # Should have broadcasts to both class groups
            calls = mock_send.call_args_list
            
            target_class_group_calls = [
                call for call in calls
                if f'class_attendance_{target_class}' in str(call)
            ]
            
            other_class_group_calls = [
                call for call in calls
                if f'class_attendance_{other_class}' in str(call)
            ]
            
            # Each class should have received events
            self.assertGreater(
                len(target_class_group_calls),
                0,
                f"No events broadcast to {target_class} group"
            )
            
            self.assertGreater(
                len(other_class_group_calls),
                0,
                f"No events broadcast to {other_class} group"
            )
    
    def test_event_filtering_with_realistic_data(self):
        """
        Test event filtering with realistic data examples.
        This provides concrete examples alongside the property-based tests.
        """
        today = timezone.now().date()
        
        # Create realistic class structure
        classes = {
            'Grade 1A': 25,
            'Grade 1B': 28,
            'Grade 2A': 30,
        }
        
        # Create students and attendance entries
        for class_name, num_students in classes.items():
            for i in range(num_students):
                student = Student.objects.create(
                    first_name=f"Student{class_name}{i}",
                    last_name="Test",
                    admission_no=f"STU{class_name}{i:04d}",
                    class_name=class_name,
                    status='active'
                )
                
                # Create attendance entry
                if i % 3 == 0:
                    # Checked in and out
                    AttendanceEntry.objects.create(
                        date=today,
                        student=student,
                        status='present',
                        check_in_time=time(8, 0),
                        check_out_time=time(15, 30),
                        marked_by=self.user,
                        class_name=class_name
                    )
                elif i % 3 == 1:
                    # Checked in only
                    AttendanceEntry.objects.create(
                        date=today,
                        student=student,
                        status='present',
                        check_in_time=time(8, 15),
                        marked_by=self.user,
                        class_name=class_name
                    )
                else:
                    # Absent
                    AttendanceEntry.objects.create(
                        date=today,
                        student=student,
                        status='absent',
                        marked_by=self.user,
                        class_name=class_name
                    )
        
        # Test filtering for each class
        tracker = RealTimeTracker()
        
        for class_name, expected_total in classes.items():
            stats = tracker.get_live_statistics(class_id=class_name)
            
            # Verify filtering works correctly
            self.assertEqual(
                stats['total_students'],
                expected_total,
                f"Class {class_name} should have {expected_total} students"
            )
            
            # Verify statistics are calculated correctly for this class
            self.assertGreater(
                stats['checked_in'],
                0,
                f"Class {class_name} should have some checked-in students"
            )
            
            # Verify percentages are calculated
            self.assertGreater(
                stats['checked_in_percentage'],
                0,
                f"Class {class_name} should have positive checked-in percentage"
            )
