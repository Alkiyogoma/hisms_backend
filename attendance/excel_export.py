"""
Excel export functionality for attendance reports.
Provides configurable export with filters and formatting.
"""

import io
from datetime import date
from typing import Dict, List, Optional

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False
    PatternFill = None
    Font = None
    Alignment = None
    Border = None
    Side = None
    get_column_letter = None

from django.http import HttpResponse
from django.utils import timezone


class ExcelReportExporter:
    """
    Exports attendance reports to Excel format with formatting and styling.
    """

    # Color scheme for reports - initialized only if openpyxl is available
    HEADER_FILL = None
    HEADER_FONT = None
    SUBHEADER_FILL = None
    SUBHEADER_FONT = None
    ALERT_FILL = None
    WARNING_FILL = None
    SUCCESS_FILL = None
    BORDER = None
    
    def __init__(self):
        """Initialize styling if openpyxl is available."""
        if OPENPYXL_AVAILABLE:
            self.__class__.HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
            self.__class__.HEADER_FONT = Font(bold=True, color="FFFFFF", size=12)
            self.__class__.SUBHEADER_FILL = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
            self.__class__.SUBHEADER_FONT = Font(bold=True, size=11)
            self.__class__.ALERT_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
            self.__class__.WARNING_FILL = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
            self.__class__.SUCCESS_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
            self.__class__.BORDER = Border(
                left=Side(style='thin'),
                right=Side(style='thin'),
                top=Side(style='thin'),
                bottom=Side(style='thin')
            )

    @staticmethod
    def export_daily_report(report_data: Dict) -> HttpResponse:
        """
        Export daily attendance report to Excel.
        
        Args:
            report_data: Daily report data from AttendanceReportGenerator
            
        Returns:
            HttpResponse with Excel file
        """
        if not OPENPYXL_AVAILABLE:
            raise ImportError("openpyxl is required for Excel export. Install with: pip install openpyxl")

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Daily Report"

        # Title and metadata
        ws['A1'] = "Daily Attendance Report"
        ws['A1'].font = Font(bold=True, size=14)
        ws['A2'] = f"Date: {report_data['report_date']}"
        ws['A3'] = f"Generated: {report_data['generated_at']}"

        # Summary statistics
        row = 5
        ws[f'A{row}'] = "Summary Statistics"
        ws[f'A{row}'].font = ExcelReportExporter.SUBHEADER_FONT
        ws[f'A{row}'].fill = ExcelReportExporter.SUBHEADER_FILL

        row += 1
        stats = report_data['statistics']
        ws[f'A{row}'] = "Total Students:"
        ws[f'B{row}'] = report_data['total_students']
        row += 1
        ws[f'A{row}'] = "Present:"
        ws[f'B{row}'] = stats['present']
        row += 1
        ws[f'A{row}'] = "Absent:"
        ws[f'B{row}'] = stats['absent']
        row += 1
        ws[f'A{row}'] = "Late:"
        ws[f'B{row}'] = stats['late']
        row += 1
        ws[f'A{row}'] = "Excused:"
        ws[f'B{row}'] = stats['excused']
        row += 1
        ws[f'A{row}'] = "Present %:"
        ws[f'B{row}'] = f"{stats['present_percentage']}%"

        # Detailed records by class
        row += 2
        for class_name, records in report_data['by_class'].items():
            ws[f'A{row}'] = f"Class: {class_name}"
            ws[f'A{row}'].font = ExcelReportExporter.SUBHEADER_FONT
            ws[f'A{row}'].fill = ExcelReportExporter.SUBHEADER_FILL
            row += 1

            # Headers
            headers = ['Student ID', 'Student Name', 'Status', 'Check-in', 'Check-out', 'Early Departure', 'Marked By', 'Checkout By']
            for col, header in enumerate(headers, 1):
                cell = ws.cell(row=row, column=col)
                cell.value = header
                cell.font = ExcelReportExporter.HEADER_FONT
                cell.fill = ExcelReportExporter.HEADER_FILL
                cell.border = ExcelReportExporter.BORDER
                cell.alignment = Alignment(horizontal='center', vertical='center')

            row += 1

            # Data rows
            for record in records:
                ws.cell(row=row, column=1).value = record['student_id']
                ws.cell(row=row, column=2).value = record['student_name']
                ws.cell(row=row, column=3).value = record['status']
                ws.cell(row=row, column=4).value = record['check_in_time']
                ws.cell(row=row, column=5).value = record['check_out_time']
                ws.cell(row=row, column=6).value = "Yes" if record['is_early_departure'] else "No"
                ws.cell(row=row, column=7).value = record['marked_by']
                ws.cell(row=row, column=8).value = record['checkout_by']

                # Apply borders
                for col in range(1, 9):
                    ws.cell(row=row, column=col).border = ExcelReportExporter.BORDER

                row += 1

            row += 1

        # Adjust column widths
        ws.column_dimensions['A'].width = 15
        ws.column_dimensions['B'].width = 20
        ws.column_dimensions['C'].width = 12
        ws.column_dimensions['D'].width = 12
        ws.column_dimensions['E'].width = 12
        ws.column_dimensions['F'].width = 15
        ws.column_dimensions['G'].width = 15
        ws.column_dimensions['H'].width = 15

        return ExcelReportExporter._create_response(wb, "daily_attendance_report")

    @staticmethod
    def export_monthly_report(report_data: Dict) -> HttpResponse:
        """
        Export monthly attendance summary to Excel.
        
        Args:
            report_data: Monthly report data from AttendanceReportGenerator
            
        Returns:
            HttpResponse with Excel file
        """
        if not OPENPYXL_AVAILABLE:
            raise ImportError("openpyxl is required for Excel export. Install with: pip install openpyxl")

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Monthly Report"

        # Title and metadata
        ws['A1'] = "Monthly Attendance Summary"
        ws['A1'].font = Font(bold=True, size=14)
        ws['A2'] = f"Period: {report_data['report_period']}"
        ws['A3'] = f"Generated: {report_data['generated_at']}"

        row = 5
        ws[f'A{row}'] = f"Students Below 85% Threshold: {report_data['total_students_below_threshold']}"
        ws[f'A{row}'].font = Font(bold=True)

        # Detailed records by class
        row += 2
        for class_name, data in report_data['by_class'].items():
            ws[f'A{row}'] = f"Class: {class_name}"
            ws[f'A{row}'].font = ExcelReportExporter.SUBHEADER_FONT
            ws[f'A{row}'].fill = ExcelReportExporter.SUBHEADER_FILL
            row += 1

            # Class statistics
            stats = data['stats']
            ws[f'A{row}'] = "Class Statistics:"
            ws[f'A{row}'].font = Font(bold=True)
            row += 1
            ws[f'A{row}'] = f"Total Students: {stats['total_students']}"
            row += 1
            ws[f'A{row}'] = f"Average Presence Rate: {stats['average_presence_rate']}%"
            row += 1
            ws[f'A{row}'] = f"Students Below Threshold: {stats['students_below_threshold']}"
            row += 1

            # Headers
            headers = ['Student ID', 'Student Name', 'Total Days', 'Present', 'Absent', 'Late', 'Excused', 'Presence Rate %', 'Alert']
            for col, header in enumerate(headers, 1):
                cell = ws.cell(row=row, column=col)
                cell.value = header
                cell.font = ExcelReportExporter.HEADER_FONT
                cell.fill = ExcelReportExporter.HEADER_FILL
                cell.border = ExcelReportExporter.BORDER
                cell.alignment = Alignment(horizontal='center', vertical='center')

            row += 1

            # Data rows
            for student in data['students']:
                ws.cell(row=row, column=1).value = student['student_id']
                ws.cell(row=row, column=2).value = student['student_name']
                ws.cell(row=row, column=3).value = student['total_days']
                ws.cell(row=row, column=4).value = student['present']
                ws.cell(row=row, column=5).value = student['absent']
                ws.cell(row=row, column=6).value = student['late']
                ws.cell(row=row, column=7).value = student['excused']
                ws.cell(row=row, column=8).value = f"{student['presence_rate']}%"
                ws.cell(row=row, column=9).value = "YES" if student['below_threshold'] else "NO"

                # Color code alerts
                if student['below_threshold']:
                    ws.cell(row=row, column=9).fill = ExcelReportExporter.ALERT_FILL

                # Apply borders
                for col in range(1, 10):
                    ws.cell(row=row, column=col).border = ExcelReportExporter.BORDER

                row += 1

            row += 1

        # Adjust column widths
        ws.column_dimensions['A'].width = 15
        ws.column_dimensions['B'].width = 20
        ws.column_dimensions['C'].width = 12
        ws.column_dimensions['D'].width = 10
        ws.column_dimensions['E'].width = 10
        ws.column_dimensions['F'].width = 10
        ws.column_dimensions['G'].width = 10
        ws.column_dimensions['H'].width = 15
        ws.column_dimensions['I'].width = 10

        return ExcelReportExporter._create_response(wb, "monthly_attendance_report")

    @staticmethod
    def export_classwise_report(report_data: Dict) -> HttpResponse:
        """
        Export class-wise attendance report to Excel.
        
        Args:
            report_data: Class-wise report data from AttendanceReportGenerator
            
        Returns:
            HttpResponse with Excel file
        """
        if not OPENPYXL_AVAILABLE:
            raise ImportError("openpyxl is required for Excel export. Install with: pip install openpyxl")

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Class-wise Report"

        # Title and metadata
        ws['A1'] = "Class-wise Attendance Report"
        ws['A1'].font = Font(bold=True, size=14)
        ws['A2'] = f"Date Range: {report_data['date_range']}"
        ws['A3'] = f"Generated: {report_data['generated_at']}"

        row = 5

        # Headers
        headers = ['Class', 'Total Records', 'Present', 'Absent', 'Late', 'Excused', 'Early Departures', 'Present %', 'Absent %', 'Late %']
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=row, column=col)
            cell.value = header
            cell.font = ExcelReportExporter.HEADER_FONT
            cell.fill = ExcelReportExporter.HEADER_FILL
            cell.border = ExcelReportExporter.BORDER
            cell.alignment = Alignment(horizontal='center', vertical='center')

        row += 1

        # Data rows
        for class_name, stats in report_data['classes'].items():
            ws.cell(row=row, column=1).value = class_name
            ws.cell(row=row, column=2).value = stats['total_records']
            ws.cell(row=row, column=3).value = stats['present']
            ws.cell(row=row, column=4).value = stats['absent']
            ws.cell(row=row, column=5).value = stats['late']
            ws.cell(row=row, column=6).value = stats['excused']
            ws.cell(row=row, column=7).value = stats['early_departures']
            ws.cell(row=row, column=8).value = f"{stats['present_percentage']}%"
            ws.cell(row=row, column=9).value = f"{stats['absent_percentage']}%"
            ws.cell(row=row, column=10).value = f"{stats['late_percentage']}%"

            # Apply borders
            for col in range(1, 11):
                ws.cell(row=row, column=col).border = ExcelReportExporter.BORDER

            row += 1

        # Adjust column widths
        for col in range(1, 11):
            ws.column_dimensions[get_column_letter(col)].width = 15

        return ExcelReportExporter._create_response(wb, "classwise_attendance_report")

    @staticmethod
    def export_parent_pickup_report(report_data: Dict) -> HttpResponse:
        """
        Export parent pickup report to Excel.
        
        Args:
            report_data: Parent pickup report data from AttendanceReportGenerator
            
        Returns:
            HttpResponse with Excel file
        """
        if not OPENPYXL_AVAILABLE:
            raise ImportError("openpyxl is required for Excel export. Install with: pip install openpyxl")

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Pickup Report"

        # Title and metadata
        ws['A1'] = "Parent Pickup Report"
        ws['A1'].font = Font(bold=True, size=14)
        ws['A2'] = f"Date Range: {report_data['date_range']}"
        ws['A3'] = f"Total Pickups: {report_data['total_pickups']}"
        ws['A4'] = f"Generated: {report_data['generated_at']}"

        row = 6

        # Headers
        headers = ['Date', 'Time', 'Student ID', 'Student Name', 'Class', 'Parent Name', 'Authorized By', 'Early Departure', 'Reason']
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=row, column=col)
            cell.value = header
            cell.font = ExcelReportExporter.HEADER_FONT
            cell.fill = ExcelReportExporter.HEADER_FILL
            cell.border = ExcelReportExporter.BORDER
            cell.alignment = Alignment(horizontal='center', vertical='center')

        row += 1

        # Data rows
        for record in report_data['all_records']:
            ws.cell(row=row, column=1).value = record['date']
            ws.cell(row=row, column=2).value = record['time']
            ws.cell(row=row, column=3).value = record['student_id']
            ws.cell(row=row, column=4).value = record['student_name']
            ws.cell(row=row, column=5).value = record['class']
            ws.cell(row=row, column=6).value = record['parent_name']
            ws.cell(row=row, column=7).value = record['authorized_by']
            ws.cell(row=row, column=8).value = "Yes" if record['is_early_departure'] else "No"
            ws.cell(row=row, column=9).value = record['reason']

            # Apply borders
            for col in range(1, 10):
                ws.cell(row=row, column=col).border = ExcelReportExporter.BORDER

            row += 1

        # Adjust column widths
        ws.column_dimensions['A'].width = 12
        ws.column_dimensions['B'].width = 10
        ws.column_dimensions['C'].width = 12
        ws.column_dimensions['D'].width = 20
        ws.column_dimensions['E'].width = 12
        ws.column_dimensions['F'].width = 20
        ws.column_dimensions['G'].width = 15
        ws.column_dimensions['H'].width = 15
        ws.column_dimensions['I'].width = 20

        return ExcelReportExporter._create_response(wb, "parent_pickup_report")

    @staticmethod
    def export_compliance_report(report_data: Dict) -> HttpResponse:
        """
        Export regulatory compliance report to Excel.
        
        Args:
            report_data: Compliance report data from AttendanceReportGenerator
            
        Returns:
            HttpResponse with Excel file
        """
        if not OPENPYXL_AVAILABLE:
            raise ImportError("openpyxl is required for Excel export. Install with: pip install openpyxl")

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Compliance Report"

        # Title and metadata
        ws['A1'] = "Regulatory Compliance Report"
        ws['A1'].font = Font(bold=True, size=14)
        ws['A2'] = f"Period: {report_data['report_period']}"
        ws['A3'] = f"Compliance Threshold: {report_data['compliance_threshold']}%"
        ws['A4'] = f"Generated: {report_data['generated_at']}"

        row = 6

        # Summary
        summary = report_data['summary']
        ws[f'A{row}'] = "Summary"
        ws[f'A{row}'].font = ExcelReportExporter.SUBHEADER_FONT
        ws[f'A{row}'].fill = ExcelReportExporter.SUBHEADER_FILL
        row += 1
        ws[f'A{row}'] = f"Total Students: {summary['total_students']}"
        row += 1
        ws[f'A{row}'] = f"Compliant Students: {summary['compliant_students']}"
        row += 1
        ws[f'A{row}'] = f"Non-Compliant Students: {summary['non_compliant_students']}"
        row += 1
        ws[f'A{row}'] = f"Compliance Rate: {summary['compliance_rate']}%"
        row += 1

        row += 1

        # Headers
        headers = ['Student ID', 'Student Name', 'Class', 'School Days', 'Days Present', 'Days Absent', 'Days Excused', 'Attendance %', 'Compliance Status']
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=row, column=col)
            cell.value = header
            cell.font = ExcelReportExporter.HEADER_FONT
            cell.fill = ExcelReportExporter.HEADER_FILL
            cell.border = ExcelReportExporter.BORDER
            cell.alignment = Alignment(horizontal='center', vertical='center')

        row += 1

        # Data rows
        for student in report_data['student_details']:
            ws.cell(row=row, column=1).value = student['student_id']
            ws.cell(row=row, column=2).value = student['student_name']
            ws.cell(row=row, column=3).value = student['class']
            ws.cell(row=row, column=4).value = student['total_school_days']
            ws.cell(row=row, column=5).value = student['days_present']
            ws.cell(row=row, column=6).value = student['days_absent']
            ws.cell(row=row, column=7).value = student['days_excused']
            ws.cell(row=row, column=8).value = f"{student['attendance_percentage']}%"
            ws.cell(row=row, column=9).value = student['compliance_status'].upper()

            # Color code compliance status
            if student['compliance_status'] == 'compliant':
                ws.cell(row=row, column=9).fill = ExcelReportExporter.SUCCESS_FILL
            else:
                ws.cell(row=row, column=9).fill = ExcelReportExporter.ALERT_FILL

            # Apply borders
            for col in range(1, 10):
                ws.cell(row=row, column=col).border = ExcelReportExporter.BORDER

            row += 1

        # Adjust column widths
        ws.column_dimensions['A'].width = 12
        ws.column_dimensions['B'].width = 20
        ws.column_dimensions['C'].width = 12
        ws.column_dimensions['D'].width = 12
        ws.column_dimensions['E'].width = 12
        ws.column_dimensions['F'].width = 12
        ws.column_dimensions['G'].width = 12
        ws.column_dimensions['H'].width = 12
        ws.column_dimensions['I'].width = 15

        return ExcelReportExporter._create_response(wb, "compliance_report")

    @staticmethod
    def _create_response(workbook, filename: str) -> HttpResponse:
        """
        Create HTTP response with Excel file.
        
        Args:
            workbook: openpyxl Workbook object
            filename: Base filename for the export
            
        Returns:
            HttpResponse with Excel file
        """
        # Save to bytes
        output = io.BytesIO()
        workbook.save(output)
        output.seek(0)

        # Create response
        response = HttpResponse(
            output.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'

        return response
