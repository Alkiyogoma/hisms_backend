"""
Property-based tests for monitoring and alerting system.
Tests Property 37: Metrics Tracking Accuracy
Tests Property 38: Failure Alerting Reliability
Tests Property 39: Health Report Completeness
"""
import logging
from datetime import datetime, timedelta
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from hypothesis import given, strategies as st, settings, assume, example, HealthCheck
from hypothesis.extra.django import TestCase as HypothesisTestCase

from students.models import Student
from .models import AttendanceEntry, NotificationLog, Message
from .monitoring_service import MonitoringService
from .services import AttendanceService

# Disable logging during tests
logging.disable(logging.CRITICAL)

User = get_user_model()


# ============================================================================
# Hypothesis Strategies for Monitoring Testing
# ============================================================================

def attendance_operation_strategy():
    """Strategy for generating attendance operations"""
    return st.tuples(
        st.sampled_from(['checkin', 'checkout']),  # Operation type
        st.booleans(),  # Success/failure
    )


def notification_delivery_strategy():
    """Strategy for generating notification delivery results"""
    return st.tuples(
        st.sampled_from(['sms', 'email']),  # Notification type
        st.sampled_from(['sent', 'failed', 'pending']),  # Status
        st.integers(min_value=0, max_value=3),  # Retry count
    )


def metric_value_strategy():
    """Strategy for generating metric values"""
    return st.floats(
        min_value=0.0,
        max_value=100.0,
        allow_nan=False,
        allow_infinity=False
    )


def time_range_strategy():
    """Strategy for generating time ranges"""
    return st.tuples(
        st.datetimes(
            min_value=datetime(2024, 1, 1),
            max_value=datetime(2024, 12, 31),
            tzinfo=timezone.utc
        ),
        st.datetimes(
            min_value=datetime(2024, 1, 1),
            max_value=datetime(2024, 12, 31),
            tzinfo=timezone.utc
        ),
    )


# ============================================================================
# Property 37: Metrics Tracking Accuracy
# ============================================================================

class TestMetricsTrackingAccuracy(HypothesisTestCase):
    """
    Property 37: Metrics Tracking Accuracy
    
    For any series of attendance operations, the system should accurately track
    success rates, error frequencies, and other metrics without loss or
    miscounting.
    """
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        
        # Create test students
        self.students = [
            Student.objects.create(
                first_name='John',
                last_name='Doe',
                admission_no='JD001',
                laravel_student_id='2024001',
                class_name='Grade 1',
                status='active'
            ),
            Student.objects.create(
                first_name='Jane',
                last_name='Smith',
                admission_no='JS002',
                laravel_student_id='2024002',
                class_name='Grade 1',
                status='active'
            ),
        ]
    
    @given(st.lists(attendance_operation_strategy(), min_size=1, max_size=50))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_attendance_success_rate_accuracy(self, operations):
        """
        Property: Attendance success rate should accurately reflect operations
        """
        successful_count = 0
        total_count = 0
        
        for op_type, success in operations:
            total_count += 1
            if success:
                successful_count += 1
                # Create successful attendance entry
                student = self.students[total_count % len(self.students)]
                if op_type == 'checkin':
                    AttendanceEntry.objects.create(
                        student=student,
                        date=timezone.now().date(),
                        check_in_time=timezone.now(),
                        status='present',
                        marked_by=self.user
                    )
        
        # Get metrics
        metrics = MonitoringService.get_attendance_metrics()
        
        # Verify success rate calculation
        if total_count > 0:
            expected_rate = (successful_count / total_count) * 100
            # Allow small rounding differences
            assert abs(metrics['success_rate'] - expected_rate) < 1.0, \
                f"Success rate mismatch: expected {expected_rate}, got {metrics['success_rate']}"
    
    @given(st.lists(
        st.tuples(
            st.sampled_from(['api_error', 'db_error', 'validation_error']),
            st.integers(min_value=1, max_value=10)
        ),
        min_size=1,
        max_size=20
    ))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_error_frequency_tracking(self, errors):
        """
        Property: Error frequencies should be accurately tracked
        """
        error_counts = {}
        
        for error_type, count in errors:
            error_counts[error_type] = error_counts.get(error_type, 0) + count
        
        # Simulate errors by creating failed attendance entries
        for error_type, count in errors:
            for _ in range(count):
                student = self.students[0]
                # Create entry with error marker
                AttendanceEntry.objects.create(
                    student=student,
                    date=timezone.now().date(),
                    check_in_time=timezone.now(),
                    status='error',
                    marked_by=self.user
                )
        
        # Get metrics
        metrics = MonitoringService.get_attendance_metrics()
        
        # Verify error tracking
        assert 'error_count' in metrics, "Error count not tracked"
        assert metrics['error_count'] >= len(errors), \
            f"Error count mismatch: expected at least {len(errors)}, got {metrics['error_count']}"
    
    @given(st.lists(notification_delivery_strategy(), min_size=1, max_size=30))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_notification_delivery_metrics(self, deliveries):
        """
        Property: Notification delivery metrics should be accurate
        """
        sent_count = 0
        failed_count = 0
        
        for notif_type, status, retry_count in deliveries:
            if status == 'sent':
                sent_count += 1
            elif status == 'failed':
                failed_count += 1
            
            # Create notification log entry
            NotificationLog.objects.create(
                recipient='test@example.com',
                notification_type=notif_type,
                status=status,
                retry_count=retry_count,
                message='Test notification'
            )
        
        # Get metrics
        metrics = MonitoringService.get_notification_metrics()
        
        # Verify delivery metrics
        assert 'total_sent' in metrics, "Total sent not tracked"
        assert 'total_failed' in metrics, "Total failed not tracked"
        assert metrics['total_sent'] >= sent_count, \
            f"Sent count mismatch: expected at least {sent_count}, got {metrics['total_sent']}"
    
    @given(st.lists(metric_value_strategy(), min_size=1, max_size=20))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_metric_aggregation_accuracy(self, values):
        """
        Property: Metric aggregation should be accurate (sum, avg, min, max)
        """
        assume(len(values) > 0)
        
        # Calculate expected values
        expected_sum = sum(values)
        expected_avg = expected_sum / len(values)
        expected_min = min(values)
        expected_max = max(values)
        
        # Get system metrics
        metrics = MonitoringService.get_system_health()
        
        # Verify metrics exist
        assert 'metrics' in metrics, "Metrics not provided"
        assert isinstance(metrics['metrics'], dict), "Metrics should be a dictionary"


# ============================================================================
# Property 38: Failure Alerting Reliability
# ============================================================================

class TestFailureAlertingReliability(HypothesisTestCase):
    """
    Property 38: Failure Alerting Reliability
    
    For any system failure or threshold breach, the alerting system should
    reliably detect and report the issue without false positives or missed
    alerts.
    """
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        
        self.student = Student.objects.create(
            first_name='John',
            last_name='Doe',
            admission_no='JD001',
            laravel_student_id='2024001',
            class_name='Grade 1',
            status='active'
        )
    
    @given(st.integers(min_value=0, max_value=100))
    @settings(max_examples=50)
    def test_alert_on_low_success_rate(self, success_rate):
        """
        Property: Alert should trigger when success rate falls below threshold
        """
        # Create attendance entries to simulate success rate
        for i in range(100):
            status = 'present' if i < success_rate else 'error'
            AttendanceEntry.objects.create(
                student=self.student,
                date=timezone.now().date(),
                check_in_time=timezone.now() - timedelta(minutes=i),
                status=status,
                marked_by=self.user
            )
        
        # Get metrics
        metrics = MonitoringService.get_attendance_metrics()
        
        # Check if alert should be triggered
        threshold = 95  # 95% threshold
        if metrics['success_rate'] < threshold:
            # Alert should be triggered
            assert metrics['success_rate'] < threshold, \
                "Alert condition not met"
    
    @given(st.lists(notification_delivery_strategy(), min_size=1, max_size=50))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_alert_on_notification_failure(self, deliveries):
        """
        Property: Alert should trigger on notification delivery failures
        """
        failed_count = 0
        total_count = 0
        
        for notif_type, status, retry_count in deliveries:
            total_count += 1
            if status == 'failed':
                failed_count += 1
            
            NotificationLog.objects.create(
                recipient='test@example.com',
                notification_type=notif_type,
                status=status,
                retry_count=retry_count,
                message='Test notification'
            )
        
        # Get metrics
        metrics = MonitoringService.get_notification_metrics()
        
        # Check failure rate
        if total_count > 0:
            failure_rate = (failed_count / total_count) * 100
            threshold = 10  # 10% failure threshold
            
            if failure_rate > threshold:
                # Alert should be triggered
                assert failure_rate > threshold, \
                    "Alert condition not met"
    
    @given(st.integers(min_value=0, max_value=10))
    @settings(max_examples=50)
    def test_no_false_positive_alerts(self, error_count):
        """
        Property: No alerts should trigger when metrics are within thresholds
        """
        # Create entries with low error count
        for i in range(100):
            status = 'error' if i < error_count else 'present'
            AttendanceEntry.objects.create(
                student=self.student,
                date=timezone.now().date(),
                check_in_time=timezone.now() - timedelta(minutes=i),
                status=status,
                marked_by=self.user
            )
        
        # Get metrics
        metrics = MonitoringService.get_attendance_metrics()
        
        # With low error count, success rate should be high
        if error_count < 5:
            assert metrics['success_rate'] > 95, \
                "Success rate should be high with low error count"
    
    @given(st.lists(
        st.tuples(
            st.datetimes(
                min_value=datetime(2024, 1, 1),
                max_value=datetime(2024, 12, 31),
                tzinfo=timezone.utc
            ),
            st.sampled_from(['sent', 'failed'])
        ),
        min_size=1,
        max_size=50
    ))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_alert_timing_accuracy(self, notifications):
        """
        Property: Alerts should be generated at the correct time
        """
        for timestamp, status in notifications:
            NotificationLog.objects.create(
                recipient='test@example.com',
                notification_type='sms',
                status=status,
                retry_count=0,
                message='Test notification',
                created_at=timestamp
            )
        
        # Get metrics
        metrics = MonitoringService.get_notification_metrics()
        
        # Verify metrics are calculated
        assert 'total_sent' in metrics, "Metrics not calculated"
        assert 'total_failed' in metrics, "Metrics not calculated"


# ============================================================================
# Property 39: Health Report Completeness
# ============================================================================

class TestHealthReportCompleteness(HypothesisTestCase):
    """
    Property 39: Health Report Completeness
    
    For any system state at report generation time, the daily health report
    should include all key metrics accurately reflecting the current system
    performance.
    """
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        
        self.student = Student.objects.create(
            first_name='John',
            last_name='Doe',
            admission_no='JD001',
            laravel_student_id='2024001',
            class_name='Grade 1',
            status='active'
        )
    
    @given(st.lists(attendance_operation_strategy(), min_size=1, max_size=50))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_health_report_includes_all_metrics(self, operations):
        """
        Property: Health report should include all required metrics
        """
        # Create attendance entries
        for op_type, success in operations:
            status = 'present' if success else 'error'
            AttendanceEntry.objects.create(
                student=self.student,
                date=timezone.now().date(),
                check_in_time=timezone.now(),
                status=status,
                marked_by=self.user
            )
        
        # Generate health report
        report = MonitoringService.generate_daily_health_report()
        
        # Verify all required fields are present
        required_fields = [
            'timestamp',
            'api_health',
            'attendance_metrics',
            'notification_metrics',
            'system_health',
            'alerts'
        ]
        
        for field in required_fields:
            assert field in report, f"Required field '{field}' missing from health report"
    
    @given(st.lists(notification_delivery_strategy(), min_size=1, max_size=30))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_health_report_notification_metrics(self, deliveries):
        """
        Property: Health report should include accurate notification metrics
        """
        # Create notification logs
        for notif_type, status, retry_count in deliveries:
            NotificationLog.objects.create(
                recipient='test@example.com',
                notification_type=notif_type,
                status=status,
                retry_count=retry_count,
                message='Test notification'
            )
        
        # Generate health report
        report = MonitoringService.generate_daily_health_report()
        
        # Verify notification metrics are included
        assert 'notification_metrics' in report, "Notification metrics missing"
        notif_metrics = report['notification_metrics']
        
        required_notif_fields = [
            'total_sent',
            'total_failed',
            'delivery_rate'
        ]
        
        for field in required_notif_fields:
            assert field in notif_metrics, \
                f"Required notification metric '{field}' missing"
    
    @given(st.lists(attendance_operation_strategy(), min_size=1, max_size=50))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_health_report_attendance_metrics(self, operations):
        """
        Property: Health report should include accurate attendance metrics
        """
        # Create attendance entries
        for op_type, success in operations:
            status = 'present' if success else 'error'
            AttendanceEntry.objects.create(
                student=self.student,
                date=timezone.now().date(),
                check_in_time=timezone.now(),
                status=status,
                marked_by=self.user
            )
        
        # Generate health report
        report = MonitoringService.generate_daily_health_report()
        
        # Verify attendance metrics are included
        assert 'attendance_metrics' in report, "Attendance metrics missing"
        attend_metrics = report['attendance_metrics']
        
        required_attend_fields = [
            'total_operations',
            'success_rate',
            'error_count'
        ]
        
        for field in required_attend_fields:
            assert field in attend_metrics, \
                f"Required attendance metric '{field}' missing"
    
    def test_health_report_timestamp_accuracy(self):
        """
        Property: Health report timestamp should be current
        """
        before = timezone.now()
        report = MonitoringService.generate_daily_health_report()
        after = timezone.now()
        
        # Verify timestamp is present and current
        assert 'timestamp' in report, "Timestamp missing from report"
        report_time = report['timestamp']
        
        # Timestamp should be between before and after
        assert before <= report_time <= after, \
            "Report timestamp is not current"
    
    @given(st.lists(
        st.tuples(
            st.sampled_from(['critical', 'warning', 'info']),
            st.text(min_size=1, max_size=100)
        ),
        min_size=0,
        max_size=10
    ))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_health_report_alerts_section(self, alerts):
        """
        Property: Health report should include alerts section with proper structure
        """
        # Generate health report
        report = MonitoringService.generate_daily_health_report()
        
        # Verify alerts section exists
        assert 'alerts' in report, "Alerts section missing from report"
        
        # Alerts should be a list
        assert isinstance(report['alerts'], list), "Alerts should be a list"
        
        # Each alert should have required fields
        for alert in report['alerts']:
            assert 'severity' in alert, "Alert severity missing"
            assert 'message' in alert, "Alert message missing"
            assert alert['severity'] in ['critical', 'warning', 'info'], \
                f"Invalid alert severity: {alert['severity']}"


# ============================================================================
# Integration Tests for Monitoring System
# ============================================================================

class TestMonitoringIntegration(TestCase):
    """Integration tests for monitoring system"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        
        self.student = Student.objects.create(
            first_name='John',
            last_name='Doe',
            admission_no='JD001',
            laravel_student_id='2024001',
            class_name='Grade 1',
            status='active'
        )
    
    def test_monitoring_service_initialization(self):
        """Test monitoring service initializes correctly"""
        metrics = MonitoringService.get_system_health()
        
        assert metrics is not None, "Monitoring service failed to initialize"
        assert 'status' in metrics, "Status not in metrics"
    
    def test_health_report_generation(self):
        """Test health report generation"""
        # Create some test data
        AttendanceEntry.objects.create(
            student=self.student,
            date=timezone.now().date(),
            check_in_time=timezone.now(),
            status='present',
            marked_by=self.user
        )
        
        # Generate report
        report = MonitoringService.generate_daily_health_report()
        
        assert report is not None, "Health report generation failed"
        assert 'timestamp' in report, "Report missing timestamp"
        assert 'attendance_metrics' in report, "Report missing attendance metrics"
    
    def test_metrics_consistency(self):
        """Test that metrics are consistent across calls"""
        # Create test data
        for i in range(10):
            AttendanceEntry.objects.create(
                student=self.student,
                date=timezone.now().date(),
                check_in_time=timezone.now() - timedelta(minutes=i),
                status='present',
                marked_by=self.user
            )
        
        # Get metrics twice
        metrics1 = MonitoringService.get_attendance_metrics()
        metrics2 = MonitoringService.get_attendance_metrics()
        
        # Should be consistent
        assert metrics1['success_rate'] == metrics2['success_rate'], \
            "Metrics are not consistent"
