"""
Django management command to generate and export attendance reports.

Usage:
  python manage.py generate_reports --daily [date]       # Daily report
  python manage.py generate_reports --monthly YYYY-MM    # Monthly report  
  python manage.py generate_reports --class ClassName    # Class report
  python manage.py generate_reports --alerts             # Alert summary
  python manage.py generate_reports --export excel       # Export to Excel
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.http import HttpResponse
from datetime import datetime, timedelta
from pathlib import Path
import json

from attendance.reporting_service import ReportingService
from attendance.excel_export import ExcelReportExporter


class Command(BaseCommand):
    help = 'Generate and export attendance reports'

    def add_arguments(self, parser):
        parser.add_argument(
            '--daily',
            action='store_true',
            help='Generate daily report for specified date (default: today)'
        )
        parser.add_argument(
            '--date',
            type=str,
            help='Date for report in YYYY-MM-DD format'
        )
        parser.add_argument(
            '--monthly',
            type=str,
            help='Generate monthly report for specified month (YYYY-MM format)'
        )
        parser.add_argument(
            '--class',
            type=str,
            dest='class_name',
            help='Generate class-wise report for specified class'
        )
        parser.add_argument(
            '--start-date',
            type=str,
            help='Start date for report range (YYYY-MM-DD)'
        )
        parser.add_argument(
            '--end-date',
            type=str,
            help='End date for report range (YYYY-MM-DD)'
        )
        parser.add_argument(
            '--alerts',
            action='store_true',
            help='Generate alert summary for low attendance'
        )
        parser.add_argument(
            '--trend',
            type=str,
            help='Generate trend analysis for student (student ID)'
        )
        parser.add_argument(
            '--compliance',
            action='store_true',
            help='Generate compliance report'
        )
        parser.add_argument(
            '--pickup',
            action='store_true',
            help='Generate parent pickup report'
        )
        parser.add_argument(
            '--export',
            type=str,
            choices=['excel', 'json', 'csv'],
            help='Export format (excel, json, or csv)'
        )
        parser.add_argument(
            '--output',
            type=str,
            help='Output file path for exported report'
        )
        parser.add_argument(
            '--json',
            action='store_true',
            help='Output report as JSON (for piping or API testing)'
        )

    def handle(self, *args, **options):
        """Execute the management command"""
        try:
            # Parse dates
            report_date = None
            if options['date']:
                report_date = datetime.strptime(options['date'], '%Y-%m-%d').date()
            else:
                report_date = timezone.now().date()

            start_date = None
            if options['start_date']:
                start_date = datetime.strptime(options['start_date'], '%Y-%m-%d').date()

            # Generate appropriate report
            if options['daily']:
                report = self.generate_daily_report(report_date, options)
            elif options['monthly']:
                report = self.generate_monthly_report(options['monthly'], options)
            elif options['class_name']:
                report = self.generate_class_report(options['class_name'], start_date, options)
            elif options['alerts']:
                report = self.generate_alerts(start_date, options)
            elif options['trend']:
                report = self.generate_trend(options['trend'], options)
            elif options['compliance']:
                report = self.generate_compliance(options['monthly'], options)
            elif options['pickup']:
                report = self.generate_pickup_report(start_date, options)
            else:
                raise CommandError('Please specify a report type (--daily, --monthly, --class, --alerts, etc.)')

            # Handle export
            if options['export'] and report:
                self.export_report(report, options['export'], options['output'], options)
            elif options['json'] and report:
                self.stdout.write(json.dumps(report, indent=2, default=str))
            elif report:
                self.display_report(report, options)

        except Exception as e:
            raise CommandError(f'Error generating report: {str(e)}')

    def generate_daily_report(self, report_date, options):
        """Generate daily attendance report"""
        self.stdout.write(self.style.SUCCESS(f' Generating Daily Report for {report_date}\n'))

        report = ReportingService.generate_daily_report(report_date)

        if report:
            self.stdout.write(self.style.HTTP_INFO(f'Report generated with {len(report.get("students", []))} students\n'))

        return report

    def generate_monthly_report(self, month_str, options):
        """Generate monthly attendance report"""
        try:
            year, month = map(int, month_str.split('-'))
        except ValueError:
            raise CommandError('Monthly report date must be in YYYY-MM format')

        self.stdout.write(self.style.SUCCESS(f' Generating Monthly Report for {month_str}\n'))

        report = ReportingService.generate_monthly_report(year, month)

        if report:
            summary = report.get('attendance_summary', {})
            self.stdout.write(self.style.HTTP_INFO(
                f'Report generated\n'
                f'  Total Days: {report.get("total_school_days", "N/A")}\n'
                f'  Present: {summary.get("present", 0)}\n'
                f'  Absent: {summary.get("absent", 0)}\n'
            ))

        return report

    def generate_class_report(self, class_name, start_date, options):
        """Generate class-wise attendance report"""
        self.stdout.write(self.style.SUCCESS(f' Generating Class Report for {class_name}\n'))

        report = ReportingService.generate_class_report(
            class_name,
            start_date=start_date
        )

        if report:
            students = report.get('students', [])
            self.stdout.write(self.style.HTTP_INFO(f'Report generated for {len(students)} students\n'))

            # Show summary
            for student in students[:5]:  # Show first 5
                name = student.get('student_name', 'Unknown')
                rate = student.get('attendance_rate', 'N/A')
                self.stdout.write(f'  {name}: {rate}%')

            if len(students) > 5:
                self.stdout.write(f'  ... and {len(students)-5} more students\n')

        return report

    def generate_alerts(self, start_date, options):
        """Generate alert summary"""
        self.stdout.write(self.style.SUCCESS(' Generating Alert Summary\n'))

        alerts = ReportingService.generate_alerts(start_date=start_date)

        if alerts:
            self.stdout.write(self.style.WARNING(f'Found {len(alerts)} alerts:\n'))

            for alert in alerts[:10]:  # Show first 10
                student_name = alert.get('student_name', 'Unknown')
                attendance = alert.get('attendance_rate', 'N/A')
                self.stdout.write(f'  {student_name}: {attendance}% attendance')

            if len(alerts) > 10:
                self.stdout.write(f'  ... and {len(alerts)-10} more alerts\n')
        else:
            self.stdout.write(self.style.SUCCESS(' No alerts. All students have sufficient attendance.\n'))

        return alerts

    def generate_trend(self, student_id, options):
        """Generate trend analysis for a student"""
        from students.models import Student
        
        try:
            student = Student.objects.get(admission_no=student_id)
        except Student.DoesNotExist:
            raise CommandError(f'Student {student_id} not found')

        self.stdout.write(self.style.SUCCESS(f' Generating Trend Analysis for {student.get_full_name()}\n'))

        trends = ReportingService.analyze_attendance_trends(student, days=90)

        if trends:
            self.stdout.write(self.style.HTTP_INFO(
                f'Analyzed {trends.get("days_analyzed", "N/A")} days\n'
                f'  Current Rate: {trends.get("current_rate", "N/A")}%\n'
                f'  Trend: {trends.get("trend", "N/A")}\n'
            ))

        return trends

    def generate_compliance(self, month_str, options):
        """Generate compliance report"""
        if month_str:
            try:
                year, month = map(int, month_str.split('-'))
            except ValueError:
                raise CommandError('Date must be in YYYY-MM format')
        else:
            today = timezone.now().date()
            year = today.year
            month = today.month

        self.stdout.write(self.style.SUCCESS(f' Generating Compliance Report for {year}-{month:02d}\n'))

        report = ReportingService.generate_compliance_report(year, month)

        if report:
            self.stdout.write(self.style.HTTP_INFO(
                f'Report generated\n'
                f'  Compliance Rate: {report.get("compliance_rate", "N/A")}%\n'
                f'  Status: {report.get("status", "N/A")}\n'
            ))

        return report

    def generate_pickup_report(self, start_date, options):
        """Generate parent pickup report"""
        self.stdout.write(self.style.SUCCESS(' Generating Parent Pickup Report\n'))

        report = ReportingService.generate_parent_pickup_report(start_date=start_date)

        if report:
            pickups = report.get('pickups', [])
            self.stdout.write(self.style.HTTP_INFO(f'Report generated for {len(pickups)} pickup records\n'))

        return report

    def export_report(self, report, export_format, output_path, options):
        """Export report to specified format"""
        self.stdout.write(self.style.SUCCESS(f'\n Exporting to {export_format.upper()}\n'))

        if export_format == 'excel':
            self.export_excel(report, output_path)
        elif export_format == 'json':
            self.export_json(report, output_path)
        elif export_format == 'csv':
            self.stdout.write(self.style.WARNING('CSV export not yet implemented\n'))

    def export_excel(self, report, output_path=None):
        """Export report to Excel format"""
        if not report:
            raise CommandError('No report data to export')

        # Determine output filename
        if not output_path:
            timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
            output_path = f'attendance_report_{timestamp}.xlsx'

        self.stdout.write(f'Exporting to {output_path}...')

        try:
            response = ExcelReportExporter.export_daily_report(report)
            
            # Save to file
            with open(output_path, 'wb') as f:
                f.write(response.content)
            
            self.stdout.write(self.style.SUCCESS(f' Report exported to {output_path}\n'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f' Export failed: {str(e)}\n'))

    def export_json(self, report, output_path=None):
        """Export report to JSON format"""
        if not report:
            raise CommandError('No report data to export')

        if not output_path:
            timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
            output_path = f'attendance_report_{timestamp}.json'

        self.stdout.write(f'Exporting to {output_path}...')

        try:
            with open(output_path, 'w') as f:
                json.dump(report, f, indent=2, default=str)
            
            self.stdout.write(self.style.SUCCESS(f' Report exported to {output_path}\n'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f' Export failed: {str(e)}\n'))

    def display_report(self, report, options):
        """Display report in terminal"""
        if isinstance(report, dict):
            # Summary display
            if 'students' in report:
                self.stdout.write(self.style.HTTP_INFO(f"\nStudents: {len(report['students'])}\n"))
            if 'attendance_summary' in report:
                summary = report['attendance_summary']
                self.stdout.write(f"\nAttendance Summary:\n")
                for key, value in summary.items():
                    self.stdout.write(f"  {key}: {value}")

        elif isinstance(report, list):
            # List of records (alerts, etc.)
            self.stdout.write(f"\nRecords: {len(report)}\n")
            for item in report[:5]:
                self.stdout.write(f"  {item}\n")
            if len(report) > 5:
                self.stdout.write(f"  ... and {len(report)-5} more\n")

