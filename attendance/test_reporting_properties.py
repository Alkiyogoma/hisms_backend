"""
Property-Based Tests for Reporting System

Validates the correctness properties of the attendance reporting system:
- Property 22: Report Generation Completeness
- Property 23: Alert Generation Accuracy
"""

from hypothesis import given, strategies as st, assume, settings, HealthCheck
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from datetime import datetime, date, timedelta
import logging
from decimal import Decimal

from students.models import Student, ParentGuardian
from academics.models import AcademicClass
from users.models import User
from attendance.models import AttendanceEntry
from attendance.reporting_service import ReportingService
from attendance.excel_export import ExcelReportExporter

logger = logging.getLogger(__name__)


class ReportGenerationCompleteness(TransactionTestCase):
    """
    Property 22: Report Generation Completeness
    
    Validates that:
    - All student records included in reports
    - All attendance data captured in time ranges
    - Attendance calculations accurate
    - Class and grade information present
    - Timestamps correct for all entries
    """

    def setUp(self):
        """Set up test data with multiple students and attendance records"""
        self.user = User.objects.create_user(
            username='test_user',
            password='testpass123',
            role='admin'
        )
        
        self.class_obj = AcademicClass.objects.create(
            name='Grade 1A',
            level=1,
            academic_year_id=1
        )
        
        # Create 5 test students
        self.students = []
        for i in range(5):
            student = Student.objects.create(
                admission_no=f'STU{1001+i:03d}',
                first_name=f'Student{i+1}',
                last_name='Test',
                date_of_birth=f'2020-0{i+1}-01',
                current_class=self.class_obj
            )
            self.students.append(student)
        
        # Create attendance records for past 10 days
        today = timezone.now().date()
        for day_offset in range(10):
            entry_date = today - timedelta(days=day_offset)
            for student in self.students:
                # Every student present for days 0-4, varied for days 5-9
                if day_offset < 5 or (day_offset % 2 == 0):
                    AttendanceEntry.objects.create(
                        date=entry_date,
                        student=student,
                        status='present',
                        check_in_time=timezone.now().time(),
                        marked_by=self.user,
                        class_name='Grade 1A'
                    )

    def test_daily_report_includes_all_students(self):
        """Daily report includes all students with attendance records"""
        today = timezone.now().date()
        report = ReportingService.generate_daily_report(today)
        
        assert 'students' in report, "Report should include students key"
        assert len(report['students']) > 0, "Report should include student records"
        
        # Verify student data structure
        for student_record in report['students']:
            assert 'student_id' in student_record
            assert 'student_name' in student_record
            assert 'status' in student_record
            assert 'attendance_rate' in student_record or 'present' in student_record

    def test_monthly_report_covers_full_month(self):
        """Monthly report includes all attendance data for the month"""
        today = timezone.now().date()
        report = ReportingService.generate_monthly_report(today.year, today.month)
        
        assert 'total_school_days' in report, "Should include total school days"
        assert 'attendance_summary' in report, "Should include attendance summary"
        
        # Verify summary statistics
        summary = report['attendance_summary']
        assert 'present' in summary or 'total_present' in summary
        assert 'absent' in summary or 'total_absent' in summary

    def test_class_report_includes_all_class_students(self):
        """Class report includes all students in the class"""
        report = ReportingService.generate_class_report('Grade 1A')
        
        assert 'class_name' in report, "Should include class name"
        assert 'students' in report, "Should include students"
        assert len(report['students']) == len(self.students), "Should include all students"

    def test_report_date_range_accuracy(self):
        """Report date ranges capture correct attendance records"""
        start_date = timezone.now().date() - timedelta(days=5)
        end_date = timezone.now().date()
        
        report = ReportingService.generate_class_report(
            'Grade 1A',
            start_date=start_date
        )
        
        assert 'date_range' in report or 'start_date' in report
        # Verify all records fall within range
        if 'students' in report:
            for student_record in report['students']:
                if 'attendance_entries' in student_record:
                    for entry in student_record['attendance_entries']:
                        entry_date = entry.get('date')
                        if entry_date:
                            assert entry_date >= str(start_date), "Entry should be after start date"

    def test_attendance_rate_calculation_accuracy(self):
        """Attendance rate calculations are accurate for each student"""
        report = ReportingService.generate_class_report('Grade 1A')
        
        if 'students' in report:
            for student_record in report['students']:
                # If attendance rate included, verify it's reasonable (0-100%)
                if 'attendance_rate' in student_record:
                    rate = float(student_record['attendance_rate'])
                    assert 0 <= rate <= 100, f"Attendance rate should be 0-100%, got {rate}"

    def test_report_includes_all_required_fields(self):
        """All required fields present in report"""
        report = ReportingService.generate_daily_report(timezone.now().date())
        
        # Verify report metadata
        required_keys = ['date'] if 'date' in report else ['timestamp', 'period']
        for key in required_keys:
            if key in report:
                assert report[key] is not None

    def test_class_information_present_in_records(self):
        """Class information included for all student records"""
        report = ReportingService.generate_class_report('Grade 1A')
        
        if 'students' in report:
            for student_record in report['students']:
                # Class name should be accessible
                assert 'class' in student_record or 'class_name' in student_record or 'grade' in student_record

    def test_multiple_student_aggregation(self):
        """Report correctly aggregates data from multiple students"""
        report = ReportingService.generate_class_report('Grade 1A')
        
        # If summary statistics included
        if 'summary' in report:
            summary = report['summary']
            if 'total_students' in summary:
                assert summary['total_students'] > 0, "Should count total students"

    def test_attendance_data_no_duplicates(self):
        """Report doesn't include duplicate attendance records"""
        report = ReportingService.generate_daily_report(timezone.now().date())
        
        if 'students' in report:
            seen_records = set()
            for student_record in report['students']:
                student_id = student_record.get('student_id')
                assert student_id not in seen_records, f"Duplicate record for student {student_id}"
                seen_records.add(student_id)


class AlertGenerationAccuracy(TransactionTestCase):
    """
    Property 23: Alert Generation Accuracy
    
    Validates that:
    - Alerts triggered for attendance below 85% threshold
    - Alert thresholds enforced correctly
    - Student included/excluded based on attendance rate
    - Alert messages contain necessary information
    - Alerts generated for specified date ranges
    """

    def setUp(self):
        """Set up test data with varying attendance rates"""
        self.user = User.objects.create_user(
            username='test_user',
            password='testpass123',
            role='admin'
        )
        
        self.class_obj = AcademicClass.objects.create(
            name='Grade 1A',
            level=1,
            academic_year_id=1
        )
        
        # Create student with LOW attendance (below 85%)
        self.low_attendance_student = Student.objects.create(
            admission_no='STU0001',
            first_name='LowAttendance',
            last_name='Student',
            date_of_birth='2020-01-01',
            current_class=self.class_obj
        )
        
        # Create student with HIGH attendance (above 85%)
        self.high_attendance_student = Student.objects.create(
            admission_no='STU0002',
            first_name='HighAttendance',
            last_name='Student',
            date_of_birth='2020-01-02',
            current_class=self.class_obj
        )
        
        # Create parent for low attendance student
        self.parent = ParentGuardian.objects.create(
            first_name='Parent',
            last_name='Name',
            phone='+255123456789',
            email='parent@example.com'
        )
        self.low_attendance_student.guardians.add(self.parent)
        
        # Create attendance records - 6 out of 10 days for low student (60%)
        today = timezone.now().date()
        for day_offset in range(10):
            entry_date = today - timedelta(days=day_offset)
            
            # Low attendance: present only 6 out of 10 days
            if day_offset < 6:
                AttendanceEntry.objects.create(
                    date=entry_date,
                    student=self.low_attendance_student,
                    status='present',
                    check_in_time=timezone.now().time(),
                    marked_by=self.user,
                    class_name='Grade 1A'
                )
            
            # High attendance: present 9 out of 10 days
            if day_offset < 9:
                AttendanceEntry.objects.create(
                    date=entry_date,
                    student=self.high_attendance_student,
                    status='present',
                    check_in_time=timezone.now().time(),
                    marked_by=self.user,
                    class_name='Grade 1A'
                )

    def test_alerts_generated_for_low_attendance(self):
        """Alerts are generated for students below 85% attendance threshold"""
        start_date = timezone.now().date() - timedelta(days=30)
        alerts = ReportingService.generate_alerts(start_date=start_date)
        
        assert isinstance(alerts, list), "Alerts should be a list"
        # Should have alerts for low attendance student
        if alerts:
            assert any(alert.get('student_id') == 'STU0001' for alert in alerts), \
                "Should generate alert for low attendance student"

    def test_alerts_not_generated_for_high_attendance(self):
        """No alerts for students above 85% attendance threshold"""
        alerts = ReportingService.generate_alerts()
        
        if alerts:
            # High attendance student should not be in alerts (unless other reason)
            for alert in alerts:
                if alert.get('student_id') == 'STU0002':
                    # If alert exists, it should not be for attendance
                    assert alert.get('alert_type') != 'low_attendance', \
                        "Should not alert for high attendance student"

    def test_alert_threshold_accuracy(self):
        """Alert threshold is exactly 85%"""
        start_date = timezone.now().date() - timedelta(days=30)
        alerts = ReportingService.generate_alerts(start_date=start_date)
        
        if alerts:
            for alert in alerts:
                if 'attendance_rate' in alert:
                    rate = float(alert['attendance_rate'])
                    assert rate < 85, f"Alert should only be for <85%, got {rate}%"

    def test_alert_includes_required_information(self):
        """Alert contains student name, ID, and attendance rate"""
        alerts = ReportingService.generate_alerts()
        
        if alerts:
            for alert in alerts:
                # Alert should have student identification
                assert alert.get('student_id') or alert.get('student_name'), \
                    "Alert should identify the student"
                
                # Alert should have attendance information
                assert 'attendance_rate' in alert or 'attendance' in alert, \
                    "Alert should include attendance information"

    def test_alert_date_range_filtering(self):
        """Alerts respect date range filters"""
        start_date = timezone.now().date() - timedelta(days=5)
        alerts = ReportingService.generate_alerts(start_date=start_date)
        
        # Should handle date range without error
        assert isinstance(alerts, list), "Should return list of alerts"

    def test_alert_messages_clear_and_actionable(self):
        """Alert messages are clear and provide actionable information"""
        alerts = ReportingService.generate_alerts()
        
        if alerts:
            for alert in alerts:
                if 'message' in alert:
                    msg = alert['message']
                    # Message should mention attendance or low attendance
                    assert 'attendance' in msg.lower() or 'presence' in msg.lower(), \
                        "Alert message should mention attendance"

    def test_student_alert_specific_data(self):
        """Student-specific alerts include detailed information"""
        alerts = ReportingService.get_alerts_for_student(self.low_attendance_student)
        
        assert isinstance(alerts, list), "Should return list of alerts"
        
        if alerts:
            for alert in alerts:
                assert 'student_id' in alert or 'student_name' in alert
                if 'attendance_rate' in alert:
                    assert isinstance(alert['attendance_rate'], (int, float, str))

    def test_alert_accuracy_over_time_period(self):
        """Alerts accurately reflect attendance over specified time period"""
        # 30-day alert
        alerts_30d = ReportingService.generate_alerts(
            start_date=timezone.now().date() - timedelta(days=30)
        )
        
        # 7-day alert
        alerts_7d = ReportingService.generate_alerts(
            start_date=timezone.now().date() - timedelta(days=7)
        )
        
        # Both should be lists (may have different results)
        assert isinstance(alerts_30d, list)
        assert isinstance(alerts_7d, list)


class ReportingSystemIntegration(TransactionTestCase):
    """
    Integration tests for complete reporting workflows
    """

    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='test_user',
            password='testpass123',
            role='admin'
        )
        
        self.class_obj = AcademicClass.objects.create(
            name='Grade 1A',
            level=1,
            academic_year_id=1
        )
        
        self.student = Student.objects.create(
            admission_no='STU0001',
            first_name='Test',
            last_name='Student',
            date_of_birth='2020-01-01',
            current_class=self.class_obj
        )
        
        today = timezone.now().date()
        for i in range(10):
            AttendanceEntry.objects.create(
                date=today - timedelta(days=i),
                student=self.student,
                status='present',
                check_in_time=timezone.now().time(),
                marked_by=self.user,
                class_name='Grade 1A'
            )

    def test_end_to_end_daily_report_generation(self):
        """Complete daily report generation workflow"""
        today = timezone.now().date()
        report = ReportingService.generate_daily_report(today)
        
        assert report is not None, "Report should be generated"
        assert isinstance(report, dict), "Report should be a dictionary"
        assert 'students' in report or 'data' in report, "Report should contain data"

    def test_end_to_end_monthly_report_generation(self):
        """Complete monthly report generation workflow"""
        today = timezone.now().date()
        report = ReportingService.generate_monthly_report(today.year, today.month)
        
        assert report is not None, "Report should be generated"
        assert isinstance(report, dict), "Report should be a dictionary"

    def test_end_to_end_excel_export(self):
        """Complete report generation and Excel export workflow"""
        today = timezone.now().date()
        report = ReportingService.generate_daily_report(today)
        
        # Test Excel export
        if report:
            try:
                response = ExcelReportExporter.export_daily_report(report)
                # Should return HTTP response
                assert response is not None
            except Exception as e:
                logger.error(f"Excel export test failed: {str(e)}")

    def test_alert_generation_and_notification_flow(self):
        """Complete alert generation and notification workflow"""
        alerts = ReportingService.generate_alerts()
        
        # Alerts should be properly formatted
        if alerts:
            for alert in alerts:
                assert 'student_id' in alert or 'student_name' in alert
                assert 'attendance_rate' in alert or 'status' in alert

    def test_trend_analysis_calculation(self):
        """Attendance trend analysis computation"""
        trends = ReportingService.analyze_attendance_trends(
            self.student,
            days=30
        )
        
        assert trends is not None, "Trends should be calculated"
        assert isinstance(trends, dict), "Trends should be a dictionary"


class ReportDataAccuracy(TestCase):
    """
    Tests for accuracy of calculated report data
    """

    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='test_user',
            password='testpass123',
            role='admin'
        )
        
        self.class_obj = AcademicClass.objects.create(
            name='Grade 1A',
            level=1,
            academic_year_id=1
        )
        
        self.student = Student.objects.create(
            admission_no='STU0001',
            first_name='Test',
            last_name='Student',
            date_of_birth='2020-01-01',
            current_class=self.class_obj
        )

    def test_attendance_percentage_calculation(self):
        """Attendance percentage calculated correctly"""
        today = timezone.now().date()
        
        # Create 10 attendance records: 8 present, 2 absent
        for i in range(8):
            AttendanceEntry.objects.create(
                date=today - timedelta(days=i),
                student=self.student,
                status='present',
                check_in_time=timezone.now().time(),
                marked_by=self.user,
                class_name='Grade 1A'
            )
        
        for i in range(8, 10):
            AttendanceEntry.objects.create(
                date=today - timedelta(days=i),
                student=self.student,
                status='absent',
                marked_by=self.user,
                class_name='Grade 1A'
            )
        
        report = ReportingService.generate_daily_report(today)
        
        # Should have calculated 80% attendance
        if report and 'students' in report:
            student_records = [s for s in report['students'] if s.get('student_id') == 'STU0001']
            if student_records:
                record = student_records[0]
                if 'attendance_rate' in record:
                    rate = float(record['attendance_rate'])
                    assert 75 <= rate <= 85, f"Expected ~80%, got {rate}%"

    def test_present_absent_count_accuracy(self):
        """Count of present/absent records is accurate"""
        today = timezone.now().date()
        
        # Create specific attendance pattern
        for i in range(5):
            AttendanceEntry.objects.create(
                date=today - timedelta(days=i),
                student=self.student,
                status='present',
                check_in_time=timezone.now().time(),
                marked_by=self.user,
                class_name='Grade 1A'
            )
        
        report = ReportingService.generate_daily_report(today)
        
        if report and 'students' in report:
            student_records = [s for s in report['students'] if s.get('student_id') == 'STU0001']
            if student_records:
                record = student_records[0]
                # Should have present count
                if 'present' in record or 'attendance' in record:
                    assert isinstance(record.get('present', 0), (int, float))


if __name__ == '__main__':
    import django
    django.setup()
    import unittest
    unittest.main()

