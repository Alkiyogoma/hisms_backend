"""
API views for Laravel-compatible attendance endpoints.
Provides mobile app compatibility while integrating with Django HISMS.
"""
from django.db import models
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from core.permissions import IsAttendanceOperator
from django.utils import timezone
from django.http import JsonResponse
from django.utils.decorators import method_decorator
import json
from datetime import datetime, timedelta

from .services import OTPService, AttendanceService, StudentSearchService, QRCodeService, correct_attendance
from .models import AttendanceEntry, Message
from students.models import Student, ParentGuardian
from core.teacher_context import get_teacher_assigned_classes
from core.utils import is_school_day
from users.models import UserRole


def _get_api_allowed_classes(user) -> set[str] | None:
    """
    Return the set of class names a user is permitted to see, or None for "all".
    Teachers see only their assigned classes; all other attendance operators see everything.
    A teacher with no assigned classes sees nothing (empty set filters to zero results).
    """
    if getattr(user, "role", None) != UserRole.TEACHER:
        return None  # None means no filter (show all)
    return get_teacher_assigned_classes(user)  # may be set() — empty means no students visible


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAttendanceOperator])
def send_otp(request):
    """
    Send OTP code to parent for verification.
    Laravel-compatible endpoint: POST /api/send-otp
    """
    from core.throttles import rate_limit_or_429
    blocked = rate_limit_or_429(request, "att_otp_send", limit=10, period=300)
    if blocked:
        return blocked
    try:
        data = request.data
        parent_id = data.get('parent_id')
        
        if not parent_id:
            return Response({
                'message': 'Validation error',
                'errors': {'parent_id': ['Parent ID is required']}
            }, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        
        # Generate and send OTP
        otp = OTPService.generate_otp(parent_id)
        
        return Response({
            'message': 'OTP sent successfully',
            'expires_in': 600  # 10 minutes in seconds
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'message': 'Failed to send OTP',
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAttendanceOperator])
def verify_otp(request):
    """
    Verify OTP code for parent.
    Laravel-compatible endpoint: POST /api/verify-otp
    """
    from core.throttles import rate_limit_or_429
    blocked = rate_limit_or_429(request, "att_otp_verify", limit=10, period=300)
    if blocked:
        return blocked
    try:
        data = request.data
        parent_id = data.get('parent_id')
        otp_code = data.get('otp')
        
        if not parent_id or not otp_code:
            return Response({
                'message': 'Validation error',
                'errors': {
                    'parent_id': ['Parent ID is required'] if not parent_id else [],
                    'otp': ['OTP code is required'] if not otp_code else []
                }
            }, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        
        # Verify OTP
        result = OTPService.verify_otp(parent_id, otp_code)
        
        if result['verified']:
            return Response({
                'message': result['message'],
                'verified': True
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                'message': result['message']
            }, status=status.HTTP_400_BAD_REQUEST)
            
    except Exception as e:
        return Response({
            'message': 'Failed to verify OTP',
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAttendanceOperator])
def checkin_students(request):
    """
    Enhanced check-in endpoint with improved processing logic and validation.
    Laravel-compatible endpoint: POST /api/checkin
    """
    try:
        data = request.data
        students_data = data.get('students', [])
        
        if not students_data:
            return Response({
                'message': 'No students provided for check-in',
                'data': [],
                'error_code': 'NO_STUDENTS'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Process batch check-in using enhanced service
        result = AttendanceService.process_batch_checkin(
            students_data=students_data,
            user=request.user
        )
        
        # Format response for Laravel compatibility
        response_data = []
        for individual_result in result['results']:
            response_data.append({
                'student_id': individual_result['student_id'],
                'status': 'success' if individual_result['success'] else 'error',
                'message': individual_result['message'],
                'error_code': individual_result.get('error_code'),
                'student_name': individual_result.get('student_name'),
                'check_in_time': individual_result.get('check_in_time'),
                'attendance_status': individual_result.get('status'),
                'class_name': individual_result.get('class_name')
            })
        
        return Response({
            'message': result['message'],
            'data': response_data,
            'summary': {
                'total': result['total_count'],
                'successful': result['successful_count'],
                'failed': result['failed_count']
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'message': 'Failed to process check-in request',
            'error': str(e),
            'error_code': 'SYSTEM_ERROR'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAttendanceOperator])
def checkout_students(request):
    """
    Enhanced check-out endpoint with early departure detection and validation.
    Laravel-compatible endpoint: POST /api/checkout
    """
    try:
        data = request.data
        students_data = data.get('students', [])
        
        if not students_data:
            return Response({
                'message': 'No students provided for check-out',
                'data': [],
                'error_code': 'NO_STUDENTS'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Process batch check-out using enhanced service
        result = AttendanceService.process_batch_checkout(
            students_data=students_data,
            user=request.user
        )
        
        # Format response for Laravel compatibility
        response_data = []
        for individual_result in result['results']:
            response_data.append({
                'student_id': individual_result['student_id'],
                'status': 'success' if individual_result['success'] else 'error',
                'message': individual_result['message'],
                'error_code': individual_result.get('error_code'),
                'student_name': individual_result.get('student_name'),
                'check_out_time': individual_result.get('check_out_time'),
                'is_early_departure': individual_result.get('is_early_departure'),
                'parent_name': individual_result.get('parent_name'),
                'reason': individual_result.get('reason'),
                'class_name': individual_result.get('class_name')
            })
        
        return Response({
            'message': result['message'],
            'data': response_data,
            'summary': {
                'total': result['total_count'],
                'successful': result['successful_count'],
                'failed': result['failed_count']
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'message': 'Failed to process check-out request',
            'error': str(e),
            'error_code': 'SYSTEM_ERROR'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAttendanceOperator])
def fetch_checklist(request):
    """
    Fetch attendance checklist for mobile app.
    Laravel-compatible endpoint: GET /api/fetch-checklist
    """
    try:
        # Get query parameters
        created_at = request.GET.get('created_at', timezone.localdate().isoformat())
        student_id = request.GET.get('student_id')
        class_id = request.GET.get('class_id')
        
        # Build query
        queryset = AttendanceEntry.objects.select_related('student').filter(
            date=created_at
        )
        
        if student_id:
            queryset = queryset.filter(student__laravel_student_id=student_id)
        
        if class_id:
            queryset = queryset.filter(student__class_name=class_id)
        
        # Format response data
        attendances = []
        for entry in queryset.order_by('-check_in_time'):
            attendances.append({
                'attendance_id': entry.laravel_attendance_id or entry.id,
                'student_id': entry.student.laravel_student_id or entry.student.admission_no,
                'status': entry.status,
                'created_at': entry.created_at.isoformat(),
                'updated_at': entry.updated_at.isoformat(),
                'checkin': entry.check_in_time.isoformat() if entry.check_in_time else None,
                'checkout': entry.check_out_time.isoformat() if entry.check_out_time else None,
                'checkin_by': entry.marked_by.id if entry.marked_by else None,
                'checkout_by': entry.checkout_by.id if entry.checkout_by else None,
                'class_id': entry.class_name
            })
        
        return Response({
            'message': 'Check list processed',
            'data': attendances
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'message': 'Failed to fetch checklist',
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAttendanceOperator])
def search_students(request):
    """
    Search students by name or ID.
    Enhanced endpoint for mobile app: GET /api/students/search
    """
    try:
        query = request.GET.get('q', '').strip()
        
        if len(query) < 2:
            return Response({
                'data': []
            }, status=status.HTTP_200_OK)
        
        students = StudentSearchService.search_students(query)
        
        # Filter to teacher's assigned classes if applicable
        allowed = _get_api_allowed_classes(request.user)
        if allowed is not None:
            students = [s for s in students if s.class_name in allowed]
        
        # Format response
        data = []
        for student in students:
            # Get parents
            parents = []
            for guardian_rel in student.guardians.all():
                parents.append({
                    'id': guardian_rel.id,
                    'name': guardian_rel.full_name,
                    'email': guardian_rel.email or '',
                    'phone': guardian_rel.phone,
                    'relation': 'Guardian'
                })
            
            for parent_rel in student.laravel_parents.all():
                parents.append({
                    'id': parent_rel.parent.id,
                    'name': parent_rel.parent.full_name,
                    'email': parent_rel.parent.email,
                    'phone': parent_rel.parent.phone,
                    'relation': parent_rel.get_relationship_display()
                })
            
            data.append({
                'id': student.laravel_student_id or student.admission_no,
                'name': student.get_full_name(),
                'photo': student.image.url if student.image else (student.photo.url if student.photo else None),
                'sex': student.gender,
                'class': student.class_name,
                'academic_year': timezone.now().year,
                'parents': parents
            })
        
        return Response({
            'data': data
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'message': 'Search failed',
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAttendanceOperator])
def student_detail(request, student_id):
    """
    Get detailed student information.
    Enhanced endpoint: GET /api/students/{id}
    """
    try:
        student_data = StudentSearchService.get_student_detail(student_id, laravel_format=True)
        
        if not student_data:
            return Response({
                'message': 'Student not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        return Response({
            'data': student_data
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'message': 'Failed to get student details',
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAttendanceOperator])
def qr_lookup(request):
    """Look up student from raw QR code payload. POST /api/qr/lookup/"""
    try:
        qr_code = request.data.get('qr_code', '').strip()
        if not qr_code:
            return Response({'message': 'No QR code provided'}, status=status.HTTP_400_BAD_REQUEST)
        student_data = StudentSearchService.get_student_detail(qr_code, laravel_format=True)
        if not student_data:
            return Response({'message': 'Student not found', 'data': None}, status=status.HTTP_404_NOT_FOUND)
        return Response({'data': student_data}, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({'message': 'Lookup failed', 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAttendanceOperator])
def attendance_statistics(request):
    """
    Get today's attendance statistics.
    Enhanced endpoint: GET /api/attendance/statistics
    """
    try:
        today_local = timezone.localdate()
        date = request.GET.get('date', today_local.isoformat())
        allowed = _get_api_allowed_classes(request.user)
        
        # Base student queryset with optional class filter
        student_qs = Student.objects.filter(status='active')
        entry_qs = AttendanceEntry.objects.filter(date=date)
        if allowed is not None:
            student_qs = student_qs.filter(class_name__in=allowed)
            entry_qs = entry_qs.filter(class_name__in=allowed)
        
        total_students = student_qs.count()
        
        checkin_count = entry_qs.filter(
            status='present',
            check_in_time__isnull=False
        ).count()
        
        checkout_count = entry_qs.filter(
            check_out_time__isnull=False
        ).count()
        
        excused_count = entry_qs.filter(
            status='excused'
        ).count()
        
        return Response({
            'date': date,
            'total_students': total_students,
            'check_in': checkin_count,
            'check_out': checkout_count,
            'excused': excused_count,
            'present_rate': round((checkin_count / total_students * 100), 1) if total_students > 0 else 0
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'message': 'Failed to get statistics',
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAttendanceOperator])
def scans_today(request):
    """
    Get today's scan records with IN/OUT filtering.
    Endpoint: GET /api/scans/today
    
    Query Parameters:
    - scan_type: 'IN' or 'OUT' to filter by scan type (optional)
    - class_id: Filter by class (optional)
    - date: Date in YYYY-MM-DD format (optional, defaults to today)
    """
    try:
        # Get query parameters
        date_str = request.GET.get('date', timezone.localdate().isoformat())
        scan_type = request.GET.get('scan_type', '').upper()  # 'IN', 'OUT', or empty for all
        class_id = request.GET.get('class_id', '')
        
        # Parse date
        try:
            date = timezone.datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response({
                'message': 'Invalid date format. Use YYYY-MM-DD',
                'error_code': 'INVALID_DATE_FORMAT'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Build query
        queryset = AttendanceEntry.objects.select_related('student').filter(date=date)
        
        # Auto-scope to teacher's classes if no explicit class_id
        allowed = _get_api_allowed_classes(request.user)
        if class_id:
            queryset = queryset.filter(class_name=class_id)
        elif allowed is not None:
            queryset = queryset.filter(class_name__in=allowed)
        
        # Order by check-in time (most recent first)
        queryset = queryset.order_by('-check_in_time', '-created_at')
        
        # Format response data
        scans = []
        for entry in queryset:
            student = entry.student
            
            # Determine scan type based on what times exist
            if entry.check_in_time:
                # Only add if scan_type filter is not set or matches 'IN'
                if not scan_type or scan_type == 'IN':
                    scans.append({
                        'id': entry.id,
                        'student_id': student.laravel_student_id or student.admission_no,
                        'student_name': student.get_full_name(),
                        'student_photo': student.image.url if student.image else (student.photo.url if student.photo else None),
                        'class': entry.class_name,
                        'scan_type': 'IN',
                        'scan_time': entry.check_in_time.strftime('%H:%M:%S'),
                        'scan_timestamp': entry.check_in_time.strftime('%H:%M:%S'),
                        'marked_by': entry.marked_by.get_full_name() if entry.marked_by else 'System',
                        'status': entry.status,
                        'is_early_departure': entry.is_early_departure,
                        'parent_name': entry.parent_name or None,
                        'reason': entry.reason or None
                    })
            
            if entry.check_out_time:
                # Only add if scan_type filter is not set or matches 'OUT'
                if not scan_type or scan_type == 'OUT':
                    scans.append({
                        'id': entry.id,
                        'student_id': student.laravel_student_id or student.admission_no,
                        'student_name': student.get_full_name(),
                        'student_photo': student.image.url if student.image else (student.photo.url if student.photo else None),
                        'class': entry.class_name,
                        'scan_type': 'OUT',
                        'scan_time': entry.check_out_time.strftime('%H:%M:%S'),
                        'scan_timestamp': entry.check_out_time.strftime('%H:%M:%S'),
                        'marked_by': entry.checkout_by.get_full_name() if entry.checkout_by else 'System',
                        'status': entry.status,
                        'is_early_departure': entry.is_early_departure,
                        'parent_name': entry.parent_name or None,
                        'reason': entry.reason or None
                    })
        
        return Response({
            'date': date_str,
            'scan_type': scan_type or 'ALL',
            'total_scans': len(scans),
            'data': scans
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'message': 'Failed to retrieve scans',
            'error': str(e),
            'error_code': 'SYSTEM_ERROR'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAttendanceOperator])
def scans_statistics(request):
    """
    Get daily scan statistics with counts by type.
    Endpoint: GET /api/scans/statistics
    
    Query Parameters:
    - date: Date in YYYY-MM-DD format (optional, defaults to today)
    - class_id: Filter by class (optional)
    """
    try:
        # Get query parameters
        date_str = request.GET.get('date', timezone.localdate().isoformat())
        class_id = request.GET.get('class_id', '')
        
        # Parse date
        try:
            date = timezone.datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response({
                'message': 'Invalid date format. Use YYYY-MM-DD',
                'error_code': 'INVALID_DATE_FORMAT'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Build query
        queryset = AttendanceEntry.objects.filter(date=date)
        
        # Auto-scope to teacher's classes if no explicit class_id
        allowed = _get_api_allowed_classes(request.user)
        if class_id:
            queryset = queryset.filter(class_name=class_id)
        elif allowed is not None:
            queryset = queryset.filter(class_name__in=allowed)
        
        # Calculate statistics
        total_entries = queryset.count()
        check_in_count = queryset.filter(check_in_time__isnull=False).count()
        check_out_count = queryset.filter(check_out_time__isnull=False).count()
        early_departure_count = queryset.filter(is_early_departure=True).count()
        
        # Get status breakdown
        status_breakdown = {}
        for status_choice in AttendanceEntry.status.field.choices:
            status_key = status_choice[0]
            status_label = status_choice[1]
            count = queryset.filter(status=status_key).count()
            status_breakdown[status_key] = {
                'label': status_label,
                'count': count,
                'percentage': round((count / total_entries * 100), 1) if total_entries > 0 else 0
            }
        
        # Get class-wise breakdown if not filtered by class
        class_breakdown = {}
        if not class_id:
            classes = queryset.values('class_name').distinct()
            for class_entry in classes:
                class_name = class_entry['class_name']
                class_count = queryset.filter(class_name=class_name).count()
                class_checkin = queryset.filter(class_name=class_name, check_in_time__isnull=False).count()
                class_checkout = queryset.filter(class_name=class_name, check_out_time__isnull=False).count()
                
                class_breakdown[class_name] = {
                    'total': class_count,
                    'check_in': class_checkin,
                    'check_out': class_checkout,
                    'check_in_rate': round((class_checkin / class_count * 100), 1) if class_count > 0 else 0,
                    'check_out_rate': round((class_checkout / class_count * 100), 1) if class_count > 0 else 0
                }
        
        return Response({
            'date': date_str,
            'summary': {
                'total_entries': total_entries,
                'check_in_count': check_in_count,
                'check_out_count': check_out_count,
                'early_departure_count': early_departure_count,
                'check_in_rate': round((check_in_count / total_entries * 100), 1) if total_entries > 0 else 0,
                'check_out_rate': round((check_out_count / total_entries * 100), 1) if total_entries > 0 else 0
            },
            'status_breakdown': status_breakdown,
            'class_breakdown': class_breakdown if not class_id else None
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'message': 'Failed to retrieve statistics',
            'error': str(e),
            'error_code': 'SYSTEM_ERROR'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAttendanceOperator])
def mark_excused(request):
    """
    Mark a student's attendance as excused.
    POST /api/attendance/mark-excused/
    Body: { "student_id": 123, "date": "2024-01-15", "reason": "Parent notified: medical appointment" }
    Or legacy: { "entry_id": 123, "reason": "..." }
    """
    try:
        EXCUSED_ROLES = {UserRole.ADMIN_OFFICER, UserRole.TEACHER}
        if request.user.role not in EXCUSED_ROLES:
            return Response(
                {'message': 'Only Admin Officers and Teachers can mark excused absences'},
                status=status.HTTP_403_FORBIDDEN
            )

        reason = request.data.get('reason', '').strip()
        if not reason:
            return Response({'message': 'reason is required', 'error_code': 'MISSING_REASON'}, status=status.HTTP_400_BAD_REQUEST)

        student_id = request.data.get('student_id')
        date_str = request.data.get('date')

        if student_id and date_str:
            from datetime import date as date_type
            try:
                d = date_type.fromisoformat(date_str)
            except ValueError:
                return Response({'message': 'Invalid date format. Use YYYY-MM-DD', 'error_code': 'INVALID_DATE'}, status=status.HTTP_400_BAD_REQUEST)

            student = Student.objects.filter(
                models.Q(laravel_student_id=student_id) | models.Q(admission_no=student_id),
                status='active'
            ).first()
            if not student:
                return Response({'message': 'Student not found', 'error_code': 'STUDENT_NOT_FOUND'}, status=status.HTTP_404_NOT_FOUND)

            from attendance.services import create_excused_absence
            result = create_excused_absence(
                actor=request.user,
                student=student,
                date=d,
                reason=reason,
            )
            if not result['success']:
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
            return Response(result, status=status.HTTP_200_OK)

        entry_id = request.data.get('entry_id')
        if not entry_id:
            return Response({'message': 'entry_id or student_id+date is required', 'error_code': 'MISSING_ENTRY_ID'}, status=status.HTTP_400_BAD_REQUEST)

        result = correct_attendance(
            actor=request.user,
            entry=entry_id,
            status='excused',
            reason=reason,
        )

        if not result['success']:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({'message': 'Failed to mark excused', 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAttendanceOperator])
def scan_history(request):
    """
    Get scan history with date range and type filtering.
    Endpoint: GET /api/scans/history
    
    Query Parameters:
    - start_date: Start date in YYYY-MM-DD format (optional, defaults to 30 days ago)
    - end_date: End date in YYYY-MM-DD format (optional, defaults to today)
    - student_id: Filter by student ID (optional)
    - scan_type: 'IN' or 'OUT' to filter by scan type (optional)
    - class_id: Filter by class (optional)
    - limit: Maximum number of records to return (optional, defaults to 100)
    """
    try:
        # Get query parameters
        today = timezone.localdate()
        start_date_str = request.GET.get('start_date', (today - timedelta(days=30)).strftime('%Y-%m-%d'))
        end_date_str = request.GET.get('end_date', today.strftime('%Y-%m-%d'))
        student_id = request.GET.get('student_id', '')
        scan_type = request.GET.get('scan_type', '').upper()  # 'IN', 'OUT', or empty for all
        class_id = request.GET.get('class_id', '')
        limit = int(request.GET.get('limit', 100))
        
        # Validate limit
        if limit < 1 or limit > 1000:
            limit = 100
        
        # Parse dates
        try:
            start_date = timezone.datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = timezone.datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response({
                'message': 'Invalid date format. Use YYYY-MM-DD',
                'error_code': 'INVALID_DATE_FORMAT'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate date range
        if start_date > end_date:
            return Response({
                'message': 'start_date must be before or equal to end_date',
                'error_code': 'INVALID_DATE_RANGE'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Build query
        queryset = AttendanceEntry.objects.select_related('student').filter(
            date__gte=start_date,
            date__lte=end_date
        )
        
        # Filter by student if provided
        if student_id:
            queryset = queryset.filter(
                student__laravel_student_id=student_id
            ) | queryset.filter(
                student__admission_no=student_id
            )
        
        # Filter by class if provided
        if class_id:
            queryset = queryset.filter(class_name=class_id)
        
        # Order by date and time (most recent first)
        queryset = queryset.order_by('-date', '-check_in_time', '-created_at')
        
        # Format response data
        history = []
        for entry in queryset[:limit]:
            student = entry.student
            
            # Create records for each scan type that exists
            if entry.check_in_time:
                history.append({
                    'id': f"{entry.id}_in",
                    'date': entry.date.strftime('%Y-%m-%d'),
                    'student_id': student.laravel_student_id or student.admission_no,
                    'student_name': student.get_full_name(),
                    'student_photo': student.image.url if student.image else (student.photo.url if student.photo else None),
                    'class': entry.class_name,
                    'scan_type': 'IN',
                    'scan_time': entry.check_in_time.strftime('%H:%M:%S'),
                    'scan_timestamp': entry.date.isoformat() + 'T' + entry.check_in_time.strftime('%H:%M:%S'),
                    'marked_by': entry.marked_by.get_full_name() if entry.marked_by else 'System',
                    'status': entry.status
                })
            
            if entry.check_out_time:
                history.append({
                    'id': f"{entry.id}_out",
                    'date': entry.date.strftime('%Y-%m-%d'),
                    'student_id': student.laravel_student_id or student.admission_no,
                    'student_name': student.get_full_name(),
                    'student_photo': student.image.url if student.image else (student.photo.url if student.photo else None),
                    'class': entry.class_name,
                    'scan_type': 'OUT',
                    'scan_time': entry.check_out_time.strftime('%H:%M:%S'),
                    'scan_timestamp': entry.date.isoformat() + 'T' + entry.check_out_time.strftime('%H:%M:%S'),
                    'marked_by': entry.checkout_by.get_full_name() if entry.checkout_by else 'System',
                    'is_early_departure': entry.is_early_departure,
                    'parent_name': entry.parent_name or None,
                    'reason': entry.reason or None
                })
        
        # Apply scan_type filter if specified
        if scan_type:
            history = [h for h in history if h['scan_type'] == scan_type]
        
        return Response({
            'start_date': start_date_str,
            'end_date': end_date_str,
            'scan_type': scan_type or 'ALL',
            'total_records': len(history),
            'limit': limit,
            'data': history
        }, status=status.HTTP_200_OK)
        
    except ValueError as e:
        return Response({
            'message': f'Invalid parameter: {str(e)}',
            'error_code': 'INVALID_PARAMETER'
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({
            'message': 'Failed to retrieve scan history',
            'error': str(e),
            'error_code': 'SYSTEM_ERROR'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAttendanceOperator])
def submit_scans(request):
    """
    Process QR code scans for attendance marking.
    Endpoint: POST /api/submit-scans
    
    Supports both single and batch QR scanning operations with IN/OUT scan types.
    Provides comprehensive error handling for invalid codes and duplicate scans.
    
    Request Format:
    {
        "scans": [
            {
                "qr_code": "2024001",
                "scan_type": "IN",
                "timestamp": "2024-01-15T08:30:00Z",
                "parent_name": "John Doe",  // Optional, for OUT scans
                "reason": "Early departure"  // Optional, for OUT scans
            }
        ]
    }
    
    Response Format:
    {
        "message": "Batch scan processing completed: 1 successful, 0 failed",
        "data": [
            {
                "qr_code": "2024001",
                "scan_type": "IN",
                "status": "success",
                "message": "Student John Doe checked in successfully",
                "student_id": "2024001",
                "student_name": "John Doe",
                "timestamp": "2024-01-15T08:30:00Z",
                "attendance_status": "present",
                "class_name": "Grade 1"
            }
        ],
        "summary": {
            "total": 1,
            "successful": 1,
            "failed": 0
        }
    }
    """
    try:
        data = request.data
        scans = data.get('scans', [])
        
        # Validate scans data
        if not scans:
            return Response({
                'message': 'No scans provided for processing',
                'data': [],
                'error_code': 'NO_SCANS',
                'summary': {
                    'total': 0,
                    'successful': 0,
                    'failed': 0
                }
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if not isinstance(scans, list):
            return Response({
                'message': 'Scans must be a list',
                'data': [],
                'error_code': 'INVALID_FORMAT',
                'summary': {
                    'total': 0,
                    'successful': 0,
                    'failed': 0
                }
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate individual scans
        validation_result = QRCodeService.validate_scans(scans)
        if not validation_result['valid']:
            return Response({
                'message': 'Validation errors in scan data',
                'errors': validation_result['errors'],
                'error_code': 'VALIDATION_ERROR',
                'data': [],
                'summary': {
                    'total': len(scans),
                    'successful': 0,
                    'failed': len(scans)
                }
            }, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        
        # Process batch scans
        batch_result = QRCodeService.process_batch_scans(scans, request.user)
        
        # Format response data
        response_data = []
        for result in batch_result['results']:
            response_item = {
                'qr_code': result.get('qr_code', ''),
                'scan_type': result.get('scan_type', ''),
                'status': 'success' if result.get('success') else 'error',
                'message': result.get('message', ''),
                'error_code': result.get('error_code'),
                'timestamp': result.get('timestamp'),
            }
            
            # Add student information if successful
            if result.get('success'):
                response_item.update({
                    'student_id': result.get('student_id'),
                    'student_name': result.get('student_name'),
                    'attendance_status': result.get('status'),
                    'class_name': result.get('class_name'),
                    'check_in_time': result.get('check_in_time'),
                    'check_out_time': result.get('check_out_time'),
                    'is_early_departure': result.get('is_early_departure'),
                    'parent_name': result.get('parent_name'),
                    'reason': result.get('reason'),
                })
            
            response_data.append(response_item)
        
        return Response({
            'message': batch_result['message'],
            'data': response_data,
            'summary': {
                'total': batch_result['total_count'],
                'successful': batch_result['successful_count'],
                'failed': batch_result['failed_count']
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'message': 'Failed to process scans',
            'error': str(e),
            'error_code': 'SYSTEM_ERROR',
            'data': [],
            'summary': {
                'total': 0,
                'successful': 0,
                'failed': 0
            }
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
