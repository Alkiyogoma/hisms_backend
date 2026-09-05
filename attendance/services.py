"""
Attendance services for Laravel integration and OTP functionality.
Provides business logic for check-in/check-out operations and parent verification.
"""
import random
import string
import re
from datetime import timedelta, datetime
from django.utils import timezone
from django.db import transaction
from django.core.exceptions import ValidationError

from users.models import UserRole

from .models import OtpCode, Message, NotificationLog, AttendanceEntry
from .realtime_tracker import get_realtime_tracker
from students.models import Student, ParentGuardian, LaravelParent

from audit.models import log_event
from core.utils import is_school_day


def is_ecd_student(student):
    """FR-ATT-003/§6.1: Late status applies to ECD classes only.
    Returns True if the student belongs to an ECD department class."""
    from academics.models import GradeClass, Department
    gc = GradeClass.objects.filter(name=student.class_name).first()
    return gc is not None and gc.department == Department.ECD


def resolve_late_status(student, proposed_status):
    """If proposed status is 'late' but student is not ECD, downgrade to 'present'.
    FR-ATT-003/§6.1: Late status applies to ECD classes only."""
    if proposed_status == 'late' and not is_ecd_student(student):
        return 'present'
    return proposed_status


class OTPService:
    """
    Service for managing OTP codes for parent verification during student checkout.
    Integrates with SMS notification system for code delivery.
    """
    
    @staticmethod
    def generate_otp(parent_id, expiry_minutes=10):
        """
        Generate a new 6-digit OTP code for parent verification.
        
        Args:
            parent_id: ID of the ParentGuardian
            expiry_minutes: OTP expiry time in minutes (default 10)
            
        Returns:
            OtpCode instance
        """
        try:
            parent = ParentGuardian.objects.get(id=parent_id)
        except ParentGuardian.DoesNotExist:
            raise ValidationError("Parent not found")
        
        # Generate OTP code
        otp = OtpCode.generate_code(parent, expiry_minutes)
        
        # Send SMS notification
        message_text = (
            f"Hello, to confirm an authorized pickup of your child, "
            f"please share this code with school: {otp.code}. "
            f"Code expires in {expiry_minutes} minutes. - Hodari Christian School"
        )
        
        # Queue SMS message
        Message.objects.create(
            phone=parent.phone,
            message=message_text,
            status=0  # Pending
        )
        
        # Log notification
        NotificationLog.objects.create(
            otp_code=otp,
            recipient_phone=parent.phone,
            notification_type='otp',
            message=message_text,
            delivery_status='pending'
        )
        
        return otp
    
    @staticmethod
    def verify_otp(parent_id, code):
        """
        Verify OTP code for parent.
        
        Args:
            parent_id: ID of the ParentGuardian
            code: 6-digit OTP code
            
        Returns:
            dict with verification result
        """
        try:
            parent = ParentGuardian.objects.get(id=parent_id)
        except ParentGuardian.DoesNotExist:
            return {
                'verified': False,
                'message': 'Parent not found'
            }
        
        # Find valid OTP
        otp = OtpCode.objects.filter(
            parent=parent,
            code=code,
            verified=False
        ).first()
        
        if not otp:
            return {
                'verified': False,
                'message': 'Invalid OTP code'
            }
        
        if otp.is_expired():
            return {
                'verified': False,
                'message': 'OTP code has expired'
            }
        
        # Mark as verified
        otp.verified = True
        otp.save()
        
        # Update notification log
        NotificationLog.objects.filter(
            otp_code=otp
        ).update(
            delivery_status='sent',
            sent_at=timezone.now()
        )
        
        return {
            'verified': True,
            'message': 'OTP verified successfully'
        }


class AttendanceService:
    """
    Enhanced service for managing student attendance with Laravel compatibility.
    Handles check-in/check-out operations, status derivation, and early departure detection.
    """
    
    # Constants for attendance processing
    EARLY_DEPARTURE_CUTOFF_HOUR = 15
    EARLY_DEPARTURE_CUTOFF_MINUTE = 30
    
    @staticmethod
    def checkin_student(student_id, user, timestamp=None, laravel_format=False):
        """
        Enhanced check-in processing with comprehensive validation and status derivation.
        
        Args:
            student_id: Student ID (admission_no or laravel_student_id)
            user: User performing the check-in
            timestamp: Optional timestamp (defaults to now)
            laravel_format: Whether student_id is in Laravel format
            
        Returns:
            dict with operation result including detailed status information
        """
        # Input validation
        if not student_id:
            return {
                'success': False,
                'message': 'Student ID is required',
                'error_code': 'MISSING_STUDENT_ID'
            }
        
        if not user:
            return {
                'success': False,
                'message': 'User is required for attendance marking',
                'error_code': 'MISSING_USER'
            }
        
        if timestamp is None:
            timestamp = timezone.now()
        
        # Validate timestamp is not in the future
        if timestamp > timezone.now():
            return {
                'success': False,
                'message': 'Check-in time cannot be in the future',
                'error_code': 'FUTURE_TIMESTAMP'
            }

        # Backdating prevention: only SA/AO can backdate check-in events
        if timestamp < timezone.now() - timedelta(minutes=5):
            allowed_backdate_roles = {UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER}
            if not hasattr(user, 'role') or user.role not in allowed_backdate_roles:
                return {
                    'success': False,
                    'message': 'Backdating check-in is not allowed. Only Admin Officers and Super Admins can backdate.',
                    'error_code': 'BACKDATE_NOT_ALLOWED'
                }

        # FR-CAL-006: Skip attendance marking on non-school days (weekends, holidays)
        today = timezone.localdate(timestamp)
        if not is_school_day(today):
            return {
                'success': False,
                'message': f'No school today ({today.strftime("%A")})',
                'error_code': 'NON_SCHOOL_DAY'
            }

        # Find and validate student
        student = AttendanceService._find_student(student_id, laravel_format)
        if not student:
            return {
                'success': False,
                'message': f'Student with ID {student_id} not found or inactive',
                'error_code': 'STUDENT_NOT_FOUND'
            }
        
        # Check for duplicate check-in
        existing_entry = AttendanceEntry.objects.filter(
            student=student,
            date=today
        ).first()
        
        if existing_entry and existing_entry.check_in_time:
            return {
                'success': False,
                'message': f'Student {student.get_full_name()} already checked in today at {existing_entry.check_in_time.strftime("%H:%M")}',
                'error_code': 'ALREADY_CHECKED_IN',
                'existing_checkin_time': existing_entry.check_in_time.strftime('%H:%M:%S')
            }
        
        # Process check-in with enhanced logic
        try:
            with transaction.atomic():
                entry, created = AttendanceEntry.objects.get_or_create(
                    student=student,
                    date=today,
                    defaults={
                        'status': AttendanceService._derive_checkin_status(timestamp, student=student),
                        'check_in_time': timestamp.time(),
                        'marked_by': user,
                        'class_name': student.class_name or 'Unknown'
                    }
                )
                
                if not created and not entry.check_in_time:
                    # Update existing entry that had no check-in time
                    entry.status = AttendanceService._derive_checkin_status(timestamp, student=student)
                    entry.check_in_time = timestamp.time()
                    entry.marked_by = user
                    entry.marked_at = timestamp
                    entry.save(update_fields=['status', 'check_in_time', 'marked_by', 'marked_at'])
            
            # Send parent notifications asynchronously
            AttendanceService._send_checkin_notifications(student, timestamp)
            
            # Broadcast real-time event
            try:
                tracker = get_realtime_tracker()
                tracker.broadcast_checkin_event(student, timestamp, marked_by=user)
                # Also broadcast updated statistics
                tracker.broadcast_statistics_update(class_id=student.class_name)
            except Exception as e:
                # Log error but don't fail the operation
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Error broadcasting check-in event: {str(e)}")
            
            # Audit log for check-in
            try:
                log_event(
                    actor=user,
                    action_type="CHECKIN_EVENT",
                    model_name="AttendanceEntry",
                    object_id=entry.pk,
                    description=f"Check-in: {student.get_full_name()} at {timestamp.strftime('%H:%M')} — {entry.status}",
                    after={"student": student.pk, "status": entry.status, "check_in_time": entry.check_in_time.strftime('%H:%M:%S')},
                )
            except Exception:
                pass
            
            return {
                'success': True,
                'message': f'Student {student.get_full_name()} checked in successfully',
                'entry_id': entry.id,
                'student_name': student.get_full_name(),
                'check_in_time': entry.check_in_time.strftime('%H:%M:%S'),
                'status': entry.status,
                'class_name': entry.class_name
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'Failed to process check-in: {str(e)}',
                'error_code': 'PROCESSING_ERROR'
            }
    
    @staticmethod
    def checkout_student(student_id, user, timestamp=None, parent_name="", reason="", laravel_format=False):
        """
        Enhanced check-out processing with early departure detection and validation.
        
        Args:
            student_id: Student ID (admission_no or laravel_student_id)
            user: User performing the check-out
            timestamp: Optional timestamp (defaults to now)
            parent_name: Name of person picking up student
            reason: Reason for checkout (optional)
            laravel_format: Whether student_id is in Laravel format
            
        Returns:
            dict with operation result including early departure status
        """
        # Input validation
        if not student_id:
            return {
                'success': False,
                'message': 'Student ID is required',
                'error_code': 'MISSING_STUDENT_ID'
            }
        
        if not user:
            return {
                'success': False,
                'message': 'User is required for attendance marking',
                'error_code': 'MISSING_USER'
            }
        
        if timestamp is None:
            timestamp = timezone.now()
        
        # Validate timestamp is not in the future
        if timestamp > timezone.now():
            return {
                'success': False,
                'message': 'Check-out time cannot be in the future',
                'error_code': 'FUTURE_TIMESTAMP'
            }
        
        # Find and validate student
        student = AttendanceService._find_student(student_id, laravel_format)
        if not student:
            return {
                'success': False,
                'message': f'Student with ID {student_id} not found or inactive',
                'error_code': 'STUDENT_NOT_FOUND'
            }
        
        today = timezone.localdate(timestamp)
        
        # Check for duplicate checkout
        existing_entry = AttendanceEntry.objects.filter(
            student=student,
            date=today
        ).first()
        
        if existing_entry and existing_entry.check_out_time:
            return {
                'success': False,
                'message': f'Student {student.get_full_name()} already checked out today at {existing_entry.check_out_time.strftime("%H:%M")}',
                'error_code': 'ALREADY_CHECKED_OUT',
                'existing_checkout_time': existing_entry.check_out_time.strftime('%H:%M:%S')
            }

        # Check for school day
        if not is_school_day(today):
            return {
                'success': False,
                'message': f'No school today ({today.strftime("%A")})',
                'error_code': 'NON_SCHOOL_DAY'
            }

        # Determine if this is an early departure
        is_early_departure = AttendanceService._is_early_departure(timestamp.time())
        
        # Process check-out with enhanced logic
        try:
            with transaction.atomic():
                entry, created = AttendanceEntry.objects.get_or_create(
                    student=student,
                    date=today,
                    defaults={
                        'status': 'present',  # If no prior entry, assume they were present
                        'check_in_time': timestamp.time(),  # Assume check-in at same time if no prior entry
                        'check_out_time': timestamp.time(),
                        'marked_by': user,
                        'checkout_by': user,
                        'parent_name': parent_name.strip(),
                        'reason': reason.strip(),
                        'class_name': student.class_name or 'Unknown',
                        'is_early_departure': is_early_departure
                    }
                )
                
                if not created:
                    # Update existing entry
                    entry.check_out_time = timestamp.time()
                    entry.checkout_by = user
                    entry.parent_name = parent_name.strip()
                    entry.reason = reason.strip()
                    entry.is_early_departure = is_early_departure
                    
                    # If no check-in time exists, record checkout-only
                    if not entry.check_in_time:
                        entry.check_in_time = timestamp.time()
                        entry.marked_by = user
                        entry.marked_at = timestamp
                        entry.status = 'present'
                    
                    entry.save(update_fields=[
                        'check_out_time', 'checkout_by', 'parent_name', 'reason', 
                        'is_early_departure', 'check_in_time', 'marked_by', 'marked_at', 'status'
                    ])
            
            # Send parent notifications asynchronously
            AttendanceService._send_checkout_notifications(student, timestamp, parent_name)
            
            # Broadcast real-time event
            try:
                tracker = get_realtime_tracker()
                tracker.broadcast_checkout_event(student, timestamp, marked_by=user)
                # Also broadcast updated statistics
                tracker.broadcast_statistics_update(class_id=student.class_name)
            except Exception as e:
                # Log error but don't fail the operation
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Error broadcasting check-out event: {str(e)}")
            
            has_checkin = created and entry.check_in_time is not None or (not created and bool(entry.marked_by))
            checkout_msg = f'Student {student.get_full_name()} checked out successfully'
            if not has_checkin:
                checkout_msg += ' (check-out only: no prior check-in recorded today)'

            # Audit log for check-out
            try:
                log_event(
                    actor=user,
                    action_type="CHECKOUT_EVENT",
                    model_name="AttendanceEntry",
                    object_id=entry.pk,
                    description=f"Check-out: {student.get_full_name()} at {timestamp.strftime('%H:%M')} (early_departure={entry.is_early_departure})",
                    after={"student": student.pk, "check_out_time": entry.check_out_time.strftime('%H:%M:%S'), "is_early_departure": entry.is_early_departure},
                )
            except Exception:
                pass
            
            return {
                'success': True,
                'message': checkout_msg,
                'entry_id': entry.id,
                'student_name': student.get_full_name(),
                'check_out_time': entry.check_out_time.strftime('%H:%M:%S'),
                'is_early_departure': entry.is_early_departure,
                'parent_name': entry.parent_name,
                'reason': entry.reason,
                'class_name': entry.class_name,
                'checkout_only': not has_checkin
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'Failed to process check-out: {str(e)}',
                'error_code': 'PROCESSING_ERROR'
            }
    
    @staticmethod
    def _find_student(student_id, laravel_format=False):
        """
        Find and validate student by ID with proper error handling.
        
        Attempts lookup by laravel_student_id first (when laravel_format=True)
        or admission_no first (when laravel_format=False), then falls back
        to the other field so QR codes generated with admission_no still resolve.
        
        Args:
            student_id: Student ID to search for (may be a signed QR payload)
            laravel_format: Preferred lookup field (True → laravel_student_id)
            
        Returns:
            Student instance or None if not found
        """
        from attendance.qr_utils import unsign_qr_data
        resolved_id = unsign_qr_data(student_id) or student_id

        def _try_lookup(field):
            try:
                return Student.objects.get(**{field: resolved_id, 'status': 'active'})
            except Student.DoesNotExist:
                return None
            except Student.MultipleObjectsReturned:
                return Student.objects.filter(**{field: resolved_id, 'status': 'active'}).first()

        if laravel_format:
            student = _try_lookup('laravel_student_id')
            if student:
                return student
            return _try_lookup('admission_no')
        else:
            student = _try_lookup('admission_no')
            if student:
                return student
            return _try_lookup('laravel_student_id')

    @staticmethod
    def _derive_checkin_status(timestamp, student=None):
        """
        Derive attendance status based on check-in time.

        FR-ATT-006: A student checking in after 8:30 AM is marked Late.
        FR-ATT-003/§6.1: Late status applies to ECD classes only.
        Compares against local timezone time, not UTC.

        Args:
            timestamp: Check-in timestamp
            student: Student instance (optional, used for ECD check)

        Returns:
            str: Attendance status ('present', 'late', etc.)
        """
        checkin_time = timestamp.time()

        # FR-ATT-006: Late threshold is 8:30 AM
        late_hour = 8
        late_minute = 30

        if (checkin_time.hour > late_hour or
            (checkin_time.hour == late_hour and checkin_time.minute >= late_minute)):
            # FR-ATT-003/§6.1: Late only applies to ECD classes
            if student is not None and not is_ecd_student(student):
                return 'present'
            return 'late'

        return 'present'
    
    @staticmethod
    def _is_early_departure(checkout_time):
        """
        Determine if checkout time constitutes an early departure.
        
        Args:
            checkout_time: Time object for checkout
            
        Returns:
            bool: True if early departure, False otherwise
        """
        return (checkout_time.hour < AttendanceService.EARLY_DEPARTURE_CUTOFF_HOUR or 
                (checkout_time.hour == AttendanceService.EARLY_DEPARTURE_CUTOFF_HOUR and 
                 checkout_time.minute < AttendanceService.EARLY_DEPARTURE_CUTOFF_MINUTE))
    
    @staticmethod
    def process_batch_checkin(students_data, user, default_timestamp=None):
        """
        Process multiple student check-ins in a batch with transaction safety.
        
        Args:
            students_data: List of student data dictionaries
            user: User performing the check-ins
            default_timestamp: Default timestamp if not provided per student
            
        Returns:
            dict with batch processing results
        """
        if not students_data:
            return {
                'success': False,
                'message': 'No students provided for batch check-in',
                'results': []
            }
        
        results = []
        successful_count = 0
        failed_count = 0
        
        for student_data in students_data:
            student_id = student_data.get('id') or student_data.get('student_id')
            checkin_time = student_data.get('checkin_time') or student_data.get('timestamp')
            
            # Parse timestamp if provided
            timestamp = default_timestamp
            if checkin_time:
                try:
                    if isinstance(checkin_time, str):
                        timestamp = datetime.fromisoformat(checkin_time.replace('Z', '+00:00'))
                        if timezone.is_naive(timestamp):
                            timestamp = timezone.make_aware(timestamp)
                    else:
                        timestamp = checkin_time
                except (ValueError, TypeError):
                    timestamp = default_timestamp or timezone.now()
            
            # Process individual check-in
            result = AttendanceService.checkin_student(
                student_id=student_id,
                user=user,
                timestamp=timestamp,
                laravel_format=True
            )
            
            # Add student_id to result for tracking
            result['student_id'] = student_id
            results.append(result)
            
            if result['success']:
                successful_count += 1
            else:
                failed_count += 1
        
        return {
            'success': successful_count > 0,
            'message': f'Batch check-in completed: {successful_count} successful, {failed_count} failed',
            'successful_count': successful_count,
            'failed_count': failed_count,
            'total_count': len(students_data),
            'results': results
        }
    
    @staticmethod
    def process_batch_checkout(students_data, user, default_timestamp=None):
        """
        Process multiple student check-outs in a batch with transaction safety.
        
        Args:
            students_data: List of student data dictionaries
            user: User performing the check-outs
            default_timestamp: Default timestamp if not provided per student
            
        Returns:
            dict with batch processing results
        """
        if not students_data:
            return {
                'success': False,
                'message': 'No students provided for batch check-out',
                'results': []
            }
        
        results = []
        successful_count = 0
        failed_count = 0
        
        for student_data in students_data:
            student_id = student_data.get('id') or student_data.get('student_id')
            checkout_time = student_data.get('checkout_time') or student_data.get('timestamp')
            parent_name = student_data.get('parent_name', '')
            reason = student_data.get('reason', '')
            
            # Parse timestamp if provided
            timestamp = default_timestamp
            if checkout_time:
                try:
                    if isinstance(checkout_time, str):
                        timestamp = datetime.fromisoformat(checkout_time.replace('Z', '+00:00'))
                        if timezone.is_naive(timestamp):
                            timestamp = timezone.make_aware(timestamp)
                    else:
                        timestamp = checkout_time
                except (ValueError, TypeError):
                    timestamp = default_timestamp or timezone.now()
            
            # Process individual check-out
            result = AttendanceService.checkout_student(
                student_id=student_id,
                user=user,
                timestamp=timestamp,
                parent_name=parent_name,
                reason=reason,
                laravel_format=True
            )
            
            # Add student_id to result for tracking
            result['student_id'] = student_id
            results.append(result)
            
            if result['success']:
                successful_count += 1
            else:
                failed_count += 1
        
        return {
            'success': successful_count > 0,
            'message': f'Batch check-out completed: {successful_count} successful, {failed_count} failed',
            'successful_count': successful_count,
            'failed_count': failed_count,
            'total_count': len(students_data),
            'results': results
        }

    @staticmethod
    def _send_checkin_notifications(student, timestamp):
        """Send check-in notifications to all student's parents"""
        message_text = (
            f"Your child {student.get_full_name()} has been checked in at "
            f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')} to Hodari Christian School."
        )
        
        # Get all parent contacts
        parents = []
        
        # Get from existing guardian system
        for guardian_rel in student.guardians.all():
            if guardian_rel.phone:
                parents.append({
                    'phone': guardian_rel.phone,
                    'name': guardian_rel.full_name
                })
        
        # Get from Laravel parent system
        for parent_rel in student.laravel_parents.all():
            parents.append({
                'phone': parent_rel.parent.phone,
                'name': parent_rel.parent.full_name
            })
        
        # Send notifications
        for parent in parents:
            # Queue SMS
            Message.objects.create(
                phone=parent['phone'],
                message=message_text,
                status=0  # Pending
            )
            
            # Log notification
            NotificationLog.objects.create(
                attendance_entry_id=student.get_today_attendance().id if student.get_today_attendance() else None,
                recipient_phone=parent['phone'],
                notification_type='checkin',
                message=message_text,
                delivery_status='pending'
            )
    
    @staticmethod
    def _send_checkout_notifications(student, timestamp, parent_name=""):
        """Send check-out notifications to all student's parents"""
        pickup_info = f" by {parent_name}" if parent_name else ""
        message_text = (
            f"Your child {student.get_full_name()} has been picked up{pickup_info} on "
            f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')} from Hodari Christian School."
        )
        
        # Get all parent contacts
        parents = []
        
        # Get from existing guardian system
        for guardian_rel in student.guardians.all():
            if guardian_rel.phone:
                parents.append({
                    'phone': guardian_rel.phone,
                    'name': guardian_rel.full_name
                })
        
        # Get from Laravel parent system
        for parent_rel in student.laravel_parents.all():
            parents.append({
                'phone': parent_rel.parent.phone,
                'name': parent_rel.parent.full_name
            })
        
        # Send notifications
        for parent in parents:
            # Queue SMS
            Message.objects.create(
                phone=parent['phone'],
                message=message_text,
                status=0  # Pending
            )
            
            # Log notification
            NotificationLog.objects.create(
                attendance_entry_id=student.get_today_attendance().id if student.get_today_attendance() else None,
                recipient_phone=parent['phone'],
                notification_type='checkout',
                message=message_text,
                delivery_status='pending'
            )


class StudentSearchService:
    """
    Service for student search functionality with fuzzy matching.
    Supports both Django and Laravel student ID formats.
    """
    
    @staticmethod
    def search_students(query, limit=50):
        """
        Search students by name or ID with fuzzy matching.
        
        Args:
            query: Search query string
            limit: Maximum number of results
            
        Returns:
            QuerySet of matching students
        """
        if not query or len(query.strip()) < 2:
            return Student.objects.none()
        
        return Student.search_students(query)[:limit]
    
    @staticmethod
    def get_student_detail(student_id, laravel_format=False):
        """
        Get detailed student information including parents and attendance.
        
        Args:
            student_id: Student ID
            laravel_format: Whether ID is in Laravel format
            
        Returns:
            dict with student details
        """
        student = AttendanceService._find_student(student_id, laravel_format)
        if not student:
            return None
        
        # Get today's attendance
        today_attendance = student.get_today_attendance()
        
        # Get parents
        parents = []
        
        # From guardian system
        for guardian_rel in student.guardians.all():
            parents.append({
                'id': guardian_rel.id,
                'name': guardian_rel.full_name,
                'phone': guardian_rel.phone,
                'email': guardian_rel.email or '',
                'relationship': guardian_rel.studentguardian_set.filter(student=student).first().get_relationship_display() if guardian_rel.studentguardian_set.filter(student=student).exists() else 'Guardian'
            })
        
        # From Laravel parent system
        for parent_rel in student.laravel_parents.all():
            parents.append({
                'id': parent_rel.parent.id,
                'name': parent_rel.parent.full_name,
                'phone': parent_rel.parent.phone,
                'email': parent_rel.parent.email,
                'relationship': parent_rel.get_relationship_display()
            })
        
        return {
            'id': student.laravel_student_id or student.admission_no,
            'admission_no': student.admission_no,
            'name': student.get_full_name(),
            'class': student.class_name,
            'stream': student.stream_name,
            'photo_url': student.image.url if student.image else (student.photo.url if student.photo else None),
            'status': student.get_status_display(),
            'attendance_rate': student.get_attendance_rate(),
            'today_attendance': {
                'entry_id': today_attendance.id,
                'status': today_attendance.get_status_display() if today_attendance else 'Not marked',
                'check_in_time': today_attendance.check_in_time.strftime('%H:%M') if today_attendance and today_attendance.check_in_time else None,
                'check_out_time': today_attendance.check_out_time.strftime('%H:%M') if today_attendance and today_attendance.check_out_time else None,
            } if today_attendance else None,
            'parents': parents
        }


def calculate_attendance_rate(student, start_date=None, end_date=None):
    """
    Calculate attendance rate for a student over a date range.
    
    Args:
        student: Student instance
        start_date: Start date for calculation (optional)
        end_date: End date for calculation (optional)
        
    Returns:
        float: Attendance rate as percentage
    """
    return student.get_attendance_rate(start_date, end_date)


def correct_attendance(actor, entry, status, reason):
    """
    Correct an attendance entry with authorization and reason.

    FR-ATT-010: Correction audit trail — original status is retained in the
    attendance entry (corrected_by, corrected_at, correction_reason) and a
    full audit log entry is created.

    Args:
        actor: User making the correction
        entry: AttendanceEntry instance (or pk, looked up internally)
        status: New attendance status string
        reason: Reason for correction

    Returns:
        dict with operation result
    """
    if not isinstance(entry, AttendanceEntry):
        try:
            entry = AttendanceEntry.objects.get(pk=entry)
        except AttendanceEntry.DoesNotExist:
            return {
                'success': False,
                'message': 'Attendance entry not found'
            }

    if not reason or not str(reason).strip():
        return {
            'success': False,
            'message': 'Correction reason is required'
        }

    original_status = entry.status

    # FR-ATT-003/§6.1: Late status applies to ECD classes only
    status = resolve_late_status(entry.student, status)

    # Update entry with correction — preserve original_status for audit trail
    entry.status = status
    entry.original_status = original_status
    entry.corrected_by = actor
    entry.corrected_at = timezone.now()
    entry.correction_reason = str(reason).strip()
    entry.save()

    # FR-ATT-010: Log correction to audit trail
    try:
        log_event(
            actor=actor,
            action_type="ATTENDANCE_CORRECTED",
            model_name="AttendanceEntry",
            object_id=entry.pk,
            description=(
                f"Attendance corrected: {original_status} \u2192 {status} for "
                f"{entry.student_id} on {entry.date}. Reason: {reason}"
            ),
        )
    except Exception:
        pass  # Never block correction on audit log failure

    return {
        'success': True,
        'message': 'Attendance corrected successfully',
        'original_status': original_status,
        'new_status': status,
    }


def create_excused_absence(actor, student, date, reason):
    """
    Create or update an attendance entry as Excused.

    FR-ATT-003: Excused absence record. Teachers, Admin Officers, and Super Admins
    can mark a student as Excused when a parent has provided prior notification.

    Args:
        actor: User creating the excused absence (must be AO, Teacher, or SA)
        student: Student instance or pk
        date: Date for the excused absence
        reason: Reason for the excused absence (required)

    Returns:
        dict with operation result
    """
    from students.models import Student

    if not isinstance(student, Student):
        try:
            student = Student.objects.get(pk=student)
        except Student.DoesNotExist:
            return {'success': False, 'message': 'Student not found'}

    if not reason or not str(reason).strip():
        return {'success': False, 'message': 'Excuse reason is required'}

    entry, created = AttendanceEntry.objects.get_or_create(
        student=student,
        date=date,
        defaults={
            'status': AttendanceStatus.EXCUSED,
            'marked_by': actor,
            'class_name': student.class_name or 'Unknown',
            'reason': str(reason).strip(),
        }
    )

    if not created:
        entry.status = AttendanceStatus.EXCUSED
        entry.reason = str(reason).strip()
        entry.marked_by = actor
        entry.save(update_fields=['status', 'reason', 'marked_by', 'updated_at'])

    try:
        log_event(
            actor=actor,
            action_type="EXCUSED_ABSENCE_CREATED",
            model_name="AttendanceEntry",
            object_id=entry.pk,
            description=f"Excused absence for {student.get_full_name()} on {date}. Reason: {reason}",
            after={"student": student.pk, "date": str(date), "reason": str(reason).strip()},
        )
    except Exception:
        pass

    return {
        'success': True,
        'message': f'Excused absence recorded for {student.get_full_name()}',
        'entry_id': entry.id,
        'created': created,
    }


def get_teacher_assigned_classes(user):
    """
    Get classes assigned to a teacher from the timetable/class-assignment data.

    Args:
        user: User instance

    Returns:
        A set of class names for the teacher.
    """
    if getattr(user, "role", None) != UserRole.TEACHER:
        return set()

    from timetable.models import TimetableSlot

    return {
        (name or "").strip()
        for name in TimetableSlot.objects.filter(teacher=user)
        .values_list("class_name", flat=True)
        .distinct()
        if (name or "").strip()
    }


def mark_attendance(actor=None, student=None, date=None, status=None, student_id=None, user=None):
    """
    Mark attendance for a student.

    Supports both the new keyword signature (actor, student, date, status) used
    by views.py and the legacy positional signature (student_id, status, user, date)
    for backwards compatibility.

    Args:
        actor: User marking attendance (new signature)
        student: Student instance (new signature)
        date: Date for attendance (optional, defaults to today)
        status: Attendance status
        student_id: Student admission_no (legacy signature)
        user: User marking attendance (legacy signature)

    Returns:
        AttendanceEntry instance, or dict with operation result
    """
    # Normalise call style: if actor+student provided, use those;
    # otherwise fall back to legacy student_id+user
    if actor is not None:
        marking_user = actor
    else:
        marking_user = user

    if student is None and student_id is not None:
        try:
            student = Student.objects.get(admission_no=student_id, status='active')
        except Student.DoesNotExist:
            return {
                'success': False,
                'message': 'Student not found'
            }

    if student is None:
        return {
            'success': False,
            'message': 'Student not provided'
        }

    if date is None:
        date = timezone.now().date()

    # FR-ATT-003/§6.1: Late status applies to ECD classes only
    if status:
        status = resolve_late_status(student, status)

    entry, created = AttendanceEntry.objects.get_or_create(
        student=student,
        date=date,
        defaults={
            'status': status or 'present',
            'marked_by': marking_user,
            'class_name': student.class_name or 'Unknown',
        }
    )

    if not created:
        entry.status = status or entry.status
        entry.marked_by = marking_user
        entry.save()

    try:
        log_event(
            actor=marking_user,
            action_type="ATTENDANCE_MARKED",
            model_name="AttendanceEntry",
            object_id=entry.pk,
            description=f"Attendance marked: {status} for {student.get_full_name()} on {date}",
            after={"student": student.pk, "date": str(date), "status": status},
        )
    except Exception:
        pass

    return entry



class QRCodeService:
    """
    Service for QR code scanning and validation.
    Handles single and batch QR code processing for attendance marking.
    Supports both IN (check-in) and OUT (check-out) scan types.
    """
    
    # QR code format validation - supports alphanumeric, underscores, hyphens, colons (for signed payloads)
    QR_CODE_PATTERN = re.compile(r'^[a-zA-Z0-9\-_:]+$')
    MAX_QR_CODE_LENGTH = 200
    
    @staticmethod
    def validate_qr_code(qr_code):
        """
        Validate QR code format and extract student ID.
        
        Args:
            qr_code: QR code string from scanner
            
        Returns:
            dict with validation result
        """
        # Handle None and non-string types
        if qr_code is None:
            return {
                'valid': False,
                'message': 'QR code is None',
                'error_code': 'INVALID_FORMAT',
                'original_qr_code': None
            }
        
        if not isinstance(qr_code, str):
            return {
                'valid': False,
                'message': f'QR code must be string, got {type(qr_code).__name__}',
                'error_code': 'INVALID_FORMAT',
                'original_qr_code': str(qr_code)
            }
        
        # Strip whitespace
        qr_code_stripped = qr_code.strip()
        
        # Check if empty after stripping
        if not qr_code_stripped:
            return {
                'valid': False,
                'message': 'QR code is empty',
                'error_code': 'EMPTY_QR_CODE',
                'original_qr_code': qr_code
            }
        
        # Check length
        if len(qr_code_stripped) > QRCodeService.MAX_QR_CODE_LENGTH:
            return {
                'valid': False,
                'message': f'QR code exceeds maximum length of {QRCodeService.MAX_QR_CODE_LENGTH}',
                'error_code': 'QR_CODE_TOO_LONG',
                'original_qr_code': qr_code
            }
        
        # Validate format (alphanumeric, hyphens, underscores)
        if not QRCodeService.QR_CODE_PATTERN.match(qr_code_stripped):
            return {
                'valid': False,
                'message': f'Invalid QR code format: {qr_code_stripped}',
                'error_code': 'INVALID_QR_FORMAT',
                'original_qr_code': qr_code
            }
        
        # Try to unsign (decrypt) the QR payload
        from attendance.qr_utils import unsign_qr_data, is_signed_qr
        unsigned_id = unsign_qr_data(qr_code_stripped)
        if unsigned_id:
            student_id = unsigned_id
        else:
            # Fallback: extract student ID from legacy formats
            student_id = qr_code_stripped
            if student_id.startswith('STU'):
                student_id = student_id[3:]

        # Check if student exists (try laravel_student_id first, then admission_no)
        try:
            student = Student.objects.get(
                laravel_student_id=student_id,
                status='active'
            )
            return {
                'valid': True,
                'student_id': student_id,
                'student': student,
                'original_qr_code': qr_code,
                'message': 'QR code is valid'
            }
        except Student.DoesNotExist:
            # Fallback: try admission_no
            student = Student.objects.filter(
                admission_no=student_id,
                status='active'
            ).first()
            if student:
                return {
                    'valid': True,
                    'student_id': student_id,
                    'student': student,
                    'original_qr_code': qr_code,
                    'message': 'QR code is valid'
                }
            return {
                'valid': False,
                'message': f'Student with ID {student_id} not found or inactive',
                'error_code': 'STUDENT_NOT_FOUND',
                'original_qr_code': qr_code,
                'student_id': student_id
            }
        except Student.MultipleObjectsReturned:
            student = Student.objects.filter(
                laravel_student_id=student_id,
                status='active'
            ).first()
            return {
                'valid': True,
                'student_id': student_id,
                'student': student,
                'original_qr_code': qr_code,
                'message': 'QR code is valid'
            }
    
    @staticmethod
    def find_student_by_qr(qr_code):
        """
        Find student by QR code with multiple lookup strategies.
        
        Args:
            qr_code: QR code string
            
        Returns:
            dict with found status and student or error info
        """
        # Validate QR code format first
        if not qr_code:
            return {
                'found': False,
                'error_code': 'EMPTY_QR_CODE',
                'message': 'QR code is empty'
            }
        
        qr_code_str = str(qr_code).strip()
        
        # Check format
        if not QRCodeService.QR_CODE_PATTERN.match(qr_code_str):
            return {
                'found': False,
                'error_code': 'INVALID_QR_FORMAT',
                'message': f'Invalid QR code format: {qr_code_str}'
            }
        
        # Try to unsign (decrypt) the QR payload
        from attendance.qr_utils import unsign_qr_data, is_signed_qr
        unsigned_id = unsign_qr_data(qr_code_str)
        if unsigned_id:
            student_id = unsigned_id
        else:
            # Fallback: extract student ID from legacy formats
            student_id = qr_code_str
            if student_id.startswith('STU'):
                student_id = student_id[3:]

        # Try multiple lookup strategies
        student = None
        
        # Strategy 1: Direct QR code field match
        student = Student.objects.filter(qr_code=qr_code_str, status='active').first()
        
        # Strategy 2: Laravel student ID match
        if not student:
            student = Student.objects.filter(laravel_student_id=student_id, status='active').first()
        
        # Strategy 3: Admission number match
        if not student:
            student = Student.objects.filter(admission_no=student_id, status='active').first()
        
        if student:
            return {
                'found': True,
                'student': student,
                'student_id': student.laravel_student_id or student.admission_no,
                'message': 'Student found'
            }
        else:
            return {
                'found': False,
                'error_code': 'STUDENT_NOT_FOUND',
                'message': f'Student with ID {qr_code_str} not found or inactive'
            }
    
    @staticmethod
    def validate_scans(scans):
        """
        Validate a list of scan records for batch processing.
        
        Args:
            scans: List of scan dictionaries with qr_code, scan_type, timestamp
            
        Returns:
            dict with validation results
        """
        errors = []
        
        if not isinstance(scans, list):
            return {
                'valid': False,
                'errors': ['Scans must be a list']
            }
        
        for idx, scan in enumerate(scans):
            scan_errors = []
            
            # Validate QR code
            if not scan.get('qr_code'):
                scan_errors.append('QR code is required')
            
            # Validate scan type
            scan_type = scan.get('scan_type', '').upper()
            if scan_type not in ['IN', 'OUT']:
                scan_errors.append(f'Invalid scan_type: {scan_type}. Must be IN or OUT')
            
            # Validate timestamp (optional, defaults to now)
            timestamp = scan.get('timestamp')
            if timestamp:
                try:
                    if isinstance(timestamp, str):
                        # Try parsing ISO format
                        test_ts = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    scan_errors.append(f'Invalid timestamp format: {timestamp}')
            
            if scan_errors:
                errors.append({
                    'scan_index': idx,
                    'qr_code': scan.get('qr_code', ''),
                    'errors': scan_errors
                })
        
        return {
            'valid': len(errors) == 0,
            'errors': errors
        }
    
    @staticmethod
    def process_qr_scan(qr_code=None, scan_type=None, user=None, timestamp=None, parent_name="", reason="", qr_data=None):
        """
        Process a single QR code scan for attendance marking.
        
        Args:
            qr_code: QR code from scanner (or use qr_data parameter)
            qr_data: Alternative parameter name for QR code (for backward compatibility)
            scan_type: 'IN' for check-in, 'OUT' for check-out
            user: User processing the scan
            timestamp: Optional scan timestamp (defaults to now)
            parent_name: Name of pickup person (for OUT scans)
            reason: Reason for early departure (for OUT scans)
            
        Returns:
            dict with scan processing result
        """
        # Handle both qr_code and qr_data parameter names
        if qr_data is not None:
            qr_code = qr_data
        
        if not qr_code:
            return {
                'success': False,
                'qr_code': qr_code,
                'scan_type': scan_type,
                'message': 'QR code is required',
                'error_code': 'EMPTY_QR_CODE',
                'timestamp': timestamp
            }
        
        # Validate QR code
        validation = QRCodeService.validate_qr_code(qr_code)
        if not validation['valid']:
            return {
                'success': False,
                'qr_code': qr_code,
                'scan_type': scan_type,
                'message': validation.get('message', validation.get('error', 'Invalid QR code')),
                'error_code': validation['error_code'],
                'timestamp': timestamp
            }
        
        student = validation['student']
        scan_type = scan_type.upper() if scan_type else 'IN'
        
        # Parse timestamp
        if timestamp is None:
            ts = timezone.now()
        else:
            try:
                if isinstance(timestamp, str):
                    ts = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    if timezone.is_naive(ts):
                        ts = timezone.make_aware(ts)
                else:
                    ts = timestamp
            except (ValueError, TypeError):
                ts = timezone.now()
        
        # Process based on scan type
        if scan_type == 'IN':
            result = AttendanceService.checkin_student(
                student_id=qr_code,
                user=user,
                timestamp=ts,
                laravel_format=True
            )
        elif scan_type == 'OUT':
            result = AttendanceService.checkout_student(
                student_id=qr_code,
                user=user,
                timestamp=ts,
                parent_name=parent_name,
                reason=reason,
                laravel_format=True
            )
        else:
            return {
                'success': False,
                'qr_code': qr_code,
                'scan_type': scan_type,
                'message': f'Invalid scan type: {scan_type}',
                'error_code': 'INVALID_SCAN_TYPE',
                'timestamp': timestamp
            }
        
        # Enrich result with additional fields
        result['qr_code'] = qr_code
        result['scan_type'] = scan_type
        
        return result
    
    @staticmethod
    def process_batch_scans(scans, user):
        """
        Process multiple QR code scans in a batch.
        
        Args:
            scans: List of scan dictionaries
            user: User processing the scans
            
        Returns:
            dict with batch processing results
        """
        results = []
        successful_count = 0
        failed_count = 0
        
        for scan in scans:
            qr_code = scan.get('qr_code', '').strip()
            scan_type = scan.get('scan_type', 'IN').upper()
            timestamp = scan.get('timestamp')
            parent_name = scan.get('parent_name', '')
            reason = scan.get('reason', '')
            
            # Process individual scan
            result = QRCodeService.process_qr_scan(
                qr_code=qr_code,
                scan_type=scan_type,
                user=user,
                timestamp=timestamp,
                parent_name=parent_name,
                reason=reason
            )
            
            results.append(result)
            
            if result.get('success'):
                successful_count += 1
            else:
                failed_count += 1
        
        return {
            'success': successful_count > 0,
            'message': f'Batch scan processing completed: {successful_count} successful, {failed_count} failed',
            'successful_count': successful_count,
            'failed_count': failed_count,
            'total_count': len(scans),
            'results': results
        }
