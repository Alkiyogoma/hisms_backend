"""
API endpoints for attendance reporting and export functionality.
Provides endpoints for generating various attendance reports and exporting to Excel.
"""

from datetime import datetime, date
from typing import Optional

from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.contrib.auth.decorators import user_passes_test

from attendance.reporting_service import ReportingService
from attendance.excel_export import ExcelReportExporter


def _check_report_perm(user):
    """NFR-SEC-004: Attendance reports require view_attendanceentry permission."""
    return user.is_superuser or user.has_perm("attendance.view_attendanceentry")


@login_required
@user_passes_test(_check_report_perm)
@require_http_methods(["GET"])
def daily_attendance_report(request):
    """
    Generate daily attendance report with check-in/check-out times.
    
    Query Parameters:
        - date: Report date (YYYY-MM-DD format, defaults to today)
        - format: Export format ('json' or 'excel', defaults to 'json')
    """
    try:
        # Get date parameter
        date_str = request.GET.get('date')
        if date_str:
            report_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        else:
            report_date = timezone.now().date()

        # Generate report
        report_data = ReportingService.generate_daily_report(report_date)

        # Check export format
        export_format = request.GET.get('format', 'json').lower()
        if export_format == 'excel':
            return ExcelReportExporter.export_daily_report(report_data)

        return JsonResponse(report_data)

    except ValueError as e:
        return JsonResponse({'error': f'Invalid date format: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@user_passes_test(_check_report_perm)
@require_http_methods(["GET"])
def monthly_attendance_report(request):
    """
    Generate monthly attendance summary with presence rates.
    
    Query Parameters:
        - year: Year (YYYY format, defaults to current year)
        - month: Month (1-12, defaults to current month)
        - format: Export format ('json' or 'excel', defaults to 'json')
    """
    try:
        # Get year and month parameters
        year = int(request.GET.get('year', timezone.now().year))
        month = int(request.GET.get('month', timezone.now().month))

        # Generate report
        report_data = ReportingService.generate_monthly_report(year, month)

        # Check export format
        export_format = request.GET.get('format', 'json').lower()
        if export_format == 'excel':
            return ExcelReportExporter.export_monthly_report(report_data)

        return JsonResponse(report_data)

    except ValueError as e:
        return JsonResponse({'error': f'Invalid parameters: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@user_passes_test(_check_report_perm)
@require_http_methods(["GET"])
def classwise_attendance_report(request):
    """
    Generate class-wise attendance report with detailed statistics.
    
    Query Parameters:
        - class_name: Specific class name (optional)
        - date_from: Start date (YYYY-MM-DD format)
        - date_to: End date (YYYY-MM-DD format)
        - format: Export format ('json' or 'excel', defaults to 'json')
    """
    try:
        # Get parameters
        class_name = request.GET.get('class_name')
        date_from_str = request.GET.get('date_from')
        date_to_str = request.GET.get('date_to')

        # Parse dates
        date_from = None
        date_to = None
        if date_from_str:
            date_from = datetime.strptime(date_from_str, '%Y-%m-%d').date()
        if date_to_str:
            date_to = datetime.strptime(date_to_str, '%Y-%m-%d').date()

        # Generate report
        report_data = ReportingService.generate_classwise_report(
            class_name=class_name,
            date_from=date_from,
            date_to=date_to
        )

        # Check export format
        export_format = request.GET.get('format', 'json').lower()
        if export_format == 'excel':
            return ExcelReportExporter.export_classwise_report(report_data)

        return JsonResponse(report_data)

    except ValueError as e:
        return JsonResponse({'error': f'Invalid date format: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@user_passes_test(_check_report_perm)
@require_http_methods(["GET"])
def parent_pickup_report(request):
    """
    Generate parent pickup report with authorized person tracking.
    
    Query Parameters:
        - date_from: Start date (YYYY-MM-DD format)
        - date_to: End date (YYYY-MM-DD format)
        - format: Export format ('json' or 'excel', defaults to 'json')
    """
    try:
        # Get parameters
        date_from_str = request.GET.get('date_from')
        date_to_str = request.GET.get('date_to')

        # Parse dates
        date_from = None
        date_to = None
        if date_from_str:
            date_from = datetime.strptime(date_from_str, '%Y-%m-%d').date()
        if date_to_str:
            date_to = datetime.strptime(date_to_str, '%Y-%m-%d').date()

        # Generate report
        report_data = ReportingService.generate_parent_pickup_report(
            date_from=date_from,
            date_to=date_to
        )

        # Check export format
        export_format = request.GET.get('format', 'json').lower()
        if export_format == 'excel':
            return ExcelReportExporter.export_parent_pickup_report(report_data)

        return JsonResponse(report_data)

    except ValueError as e:
        return JsonResponse({'error': f'Invalid date format: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@user_passes_test(_check_report_perm)
@require_http_methods(["GET"])
def attendance_trend_analysis(request):
    """
    Generate attendance trend analysis over time periods.
    
    Query Parameters:
        - student_id: Specific student ID (optional)
        - class_name: Specific class name (optional)
        - days: Number of days to analyze (default 30)
    """
    try:
        # Get parameters
        student_id = request.GET.get('student_id')
        class_name = request.GET.get('class_name')
        days = int(request.GET.get('days', 30))

        # Convert student_id to int if provided
        if student_id:
            student_id = int(student_id)

        # Generate report
        report_data = ReportingService.generate_attendance_trend_analysis(
            student_id=student_id,
            class_name=class_name,
            days=days
        )

        return JsonResponse(report_data)

    except ValueError as e:
        return JsonResponse({'error': f'Invalid parameters: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@user_passes_test(_check_report_perm)
@require_http_methods(["GET"])
def regulatory_compliance_report(request):
    """
    Generate regulatory compliance report meeting education department requirements.
    
    Query Parameters:
        - year: Year (YYYY format, defaults to current year)
        - month: Month (1-12, defaults to current month)
        - format: Export format ('json' or 'excel', defaults to 'json')
    """
    try:
        # Get parameters
        year = int(request.GET.get('year', timezone.now().year))
        month = int(request.GET.get('month', timezone.now().month))

        # Generate report
        report_data = ReportingService.generate_regulatory_compliance_report(year, month)

        # Check export format
        export_format = request.GET.get('format', 'json').lower()
        if export_format == 'excel':
            return ExcelReportExporter.export_compliance_report(report_data)

        return JsonResponse(report_data)

    except ValueError as e:
        return JsonResponse({'error': f'Invalid parameters: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@user_passes_test(_check_report_perm)
@require_http_methods(["GET"])
def low_attendance_alerts(request):
    """
    Generate alerts for students with attendance below threshold.
    
    Query Parameters:
        - threshold: Attendance percentage threshold (default 85)
        - date_from: Start date (YYYY-MM-DD format)
        - date_to: End date (YYYY-MM-DD format)
    """
    try:
        # Get parameters
        threshold = int(request.GET.get('threshold', 85))
        date_from_str = request.GET.get('date_from')
        date_to_str = request.GET.get('date_to')

        # Parse dates
        date_from = None
        date_to = None
        if date_from_str:
            date_from = datetime.strptime(date_from_str, '%Y-%m-%d').date()
        if date_to_str:
            date_to = datetime.strptime(date_to_str, '%Y-%m-%d').date()

        # Generate alerts
        alerts_data = ReportingService.generate_attendance_alerts(
            threshold=threshold,
            date_from=date_from,
            date_to=date_to
        )

        return JsonResponse(alerts_data)

    except ValueError as e:
        return JsonResponse({'error': f'Invalid parameters: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@user_passes_test(_check_report_perm)
@require_http_methods(["GET"])
def attendance_alerts_summary(request):
    """
    Get summary of attendance alerts by alert level.
    
    Query Parameters:
        - threshold: Attendance percentage threshold (default 85)
        - date_from: Start date (YYYY-MM-DD format)
        - date_to: End date (YYYY-MM-DD format)
    """
    try:
        # Get parameters
        threshold = int(request.GET.get('threshold', 85))
        date_from_str = request.GET.get('date_from')
        date_to_str = request.GET.get('date_to')

        # Parse dates
        date_from = None
        date_to = None
        if date_from_str:
            date_from = datetime.strptime(date_from_str, '%Y-%m-%d').date()
        if date_to_str:
            date_to = datetime.strptime(date_to_str, '%Y-%m-%d').date()

        # Generate alerts
        alerts_data = ReportingService.generate_attendance_alerts(
            threshold=threshold,
            date_from=date_from,
            date_to=date_to
        )

        # Create summary
        summary = {
            'report_type': 'Attendance Alerts Summary',
            'threshold': threshold,
            'total_alerts': alerts_data['total_alerts'],
            'by_alert_level': {
                'warning': len(alerts_data['by_alert_level'].get('warning', [])),
                'critical': len(alerts_data['by_alert_level'].get('critical', [])),
                'severe': len(alerts_data['by_alert_level'].get('severe', [])),
            },
            'generated_at': timezone.now().isoformat(),
        }

        return JsonResponse(summary)

    except ValueError as e:
        return JsonResponse({'error': f'Invalid parameters: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
