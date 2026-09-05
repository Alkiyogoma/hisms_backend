"""
Core data migration logic for migrating Laravel attendance data to Django HISMS.
Implements batch processing, conflict detection, and comprehensive reporting.
"""
import logging
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, date
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from students.models import Student, ParentGuardian, StudentGuardian, LaravelParent, StudentLaravelParent
from users.models import User
from .models import AttendanceEntry, OtpCode, Message, NotificationLog
from .laravel_extractor import LaravelDatabaseExtractor, LaravelFieldMapper

logger = logging.getLogger(__name__)
User = get_user_model()


class MigrationResult:
    """Container for migration operation results"""
    
    def __init__(self, operation_type: str):
        self.operation_type = operation_type
        self.total_processed = 0
        self.successful = 0
        self.failed = 0
        self.skipped = 0
        self.errors = []
        self.warnings = []
        self.start_time = timezone.now()
        self.end_time = None
        
    def add_success(self):
        self.successful += 1
        self.total_processed += 1
        
    def add_failure(self, error_msg: str):
        self.failed += 1
        self.total_processed += 1
        self.errors.append(error_msg)
        
    def add_skip(self, reason: str):
        self.skipped += 1
        self.total_processed += 1
        self.warnings.append(reason)
        
    def add_warning(self, warning_msg: str):
        self.warnings.append(warning_msg)
        
    def finish(self):
        self.end_time = timezone.now()
        
    def get_duration(self):
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0
        
    def to_dict(self):
        return {
            'operation_type': self.operation_type,
            'total_processed': self.total_processed,
            'successful': self.successful,
            'failed': self.failed,
            'skipped': self.skipped,
            'success_rate': (self.successful / self.total_processed * 100) if self.total_processed > 0 else 0,
            'duration_seconds': self.get_duration(),
            'errors': self.errors,
            'warnings': self.warnings,
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat() if self.end_time else None
        }


class MigrationReport:
    """Comprehensive migration report with statistics and details"""
    
    def __init__(self):
        self.migration_id = f"migration_{timezone.now().strftime('%Y%m%d_%H%M%S')}"
        self.start_time = timezone.now()
        self.end_time = None
        self.results = {}
        self.overall_stats = {
            'total_records_processed': 0,
            'total_successful': 0,
            'total_failed': 0,
            'total_skipped': 0
        }
        
    def add_result(self, operation: str, result: MigrationResult):
        self.results[operation] = result.to_dict()
        self.overall_stats['total_records_processed'] += result.total_processed
        self.overall_stats['total_successful'] += result.successful
        self.overall_stats['total_failed'] += result.failed
        self.overall_stats['total_skipped'] += result.skipped
        
    def finish(self):
        self.end_time = timezone.now()
        
    def get_duration(self):
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0
        
    def to_dict(self):
        return {
            'migration_id': self.migration_id,
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'duration_seconds': self.get_duration(),
            'overall_stats': self.overall_stats,
            'operation_results': self.results,
            'success_rate': (
                self.overall_stats['total_successful'] / 
                self.overall_stats['total_records_processed'] * 100
            ) if self.overall_stats['total_records_processed'] > 0 else 0
        }


class DataMigrator:
    """
    Core data migration system for transferring Laravel attendance data to Django.
    Implements batch processing, conflict detection, and comprehensive reporting.
    """
    
    def __init__(self, extractor: LaravelDatabaseExtractor, batch_size: int = 500):
        self.extractor = extractor
        self.batch_size = batch_size
        self.report = MigrationReport()
        self.dry_run = False
        
        # Caches for lookups
        self._student_cache = {}
        self._class_cache = {}
        self._user_cache = {}
        self._parent_cache = {}
        
    def set_dry_run(self, dry_run: bool = True):
        """Enable/disable dry run mode (no actual database changes)"""
        self.dry_run = dry_run
        
    def _get_or_create_user(self, name: str, email: str = None) -> Optional[User]:
        """Get or create a Django user for Laravel user mapping"""
        if not name:
            return None
            
        # Check cache first
        cache_key = f"{name}_{email or 'no_email'}"
        if cache_key in self._user_cache:
            return self._user_cache[cache_key]
            
        # Try to find existing user
        if email:
            try:
                user = User.objects.get(email=email)
                self._user_cache[cache_key] = user
                return user
            except User.DoesNotExist:
                pass
                
        # Try to find by username (name)
        try:
            user = User.objects.get(username=name)
            self._user_cache[cache_key] = user
            return user
        except User.DoesNotExist:
            pass
            
        # Create new user if not in dry run mode
        if not self.dry_run:
            try:
                user = User.objects.create_user(
                    username=name,
                    email=email or f"{name}@hodari.ac.tz",
                    first_name=name.split()[0] if name else "",
                    last_name=" ".join(name.split()[1:]) if len(name.split()) > 1 else ""
                )
                self._user_cache[cache_key] = user
                return user
            except IntegrityError:
                # Username might already exist, try with suffix
                try:
                    user = User.objects.create_user(
                        username=f"{name}_laravel",
                        email=email or f"{name}_laravel@hodari.ac.tz",
                        first_name=name.split()[0] if name else "",
                        last_name=" ".join(name.split()[1:]) if len(name.split()) > 1 else ""
                    )
                    self._user_cache[cache_key] = user
                    return user
                except IntegrityError:
                    logger.error(f"Failed to create user for {name}")
                    return None
        
        return None
        
    def _get_or_create_class(self, class_name: str) -> Optional[str]:
        """Get or create a Django Class for Laravel class mapping"""
        if not class_name:
            return None
            
        # For now, just return the class name since we're using class_name field
        # In the future, this could be enhanced to work with a proper Class model
        return class_name
        
    def migrate_students(self) -> MigrationResult:
        """
        Migrate student records from Laravel to Django.
        Preserves student_id mapping and handles class relationships.
        """
        result = MigrationResult("students")
        logger.info("Starting student migration...")
        
        try:
            # Get Laravel classes first for mapping
            laravel_classes = self.extractor.extract_classes()
            class_lookup = {cls['id']: cls['name'] for cls in laravel_classes}
            
            offset = 0
            while True:
                # Extract batch of students
                laravel_students = self.extractor.extract_students(self.batch_size, offset)
                if not laravel_students:
                    break
                    
                for laravel_student in laravel_students:
                    try:
                        # Map Laravel data to Django format
                        mapped_data = LaravelFieldMapper.map_student_data(laravel_student, class_lookup)
                        
                        # Check for existing student by laravel_student_id
                        laravel_id = laravel_student.get('student_id')
                        if not laravel_id:
                            result.add_failure(f"Student missing student_id: {laravel_student}")
                            continue
                            
                        # Check if student already exists
                        existing_student = Student.objects.filter(
                            laravel_student_id=laravel_id
                        ).first()
                        
                        if existing_student:
                            result.add_skip(f"Student {laravel_id} already exists")
                            continue
                            
                        # Get or create class
                        class_name = mapped_data.get('class_name')
                        django_class_name = None
                        if class_name:
                            django_class_name = self._get_or_create_class(class_name)
                            
                        if not self.dry_run:
                            # Create new student
                            student_data = {
                                'admission_no': mapped_data.get('admission_no', f"L{laravel_id}"),
                                'first_name': mapped_data.get('first_name', ''),
                                'last_name': mapped_data.get('last_name', ''),
                                'phone': mapped_data.get('phone') or '',  # Ensure phone is never None
                                'laravel_student_id': laravel_id,
                                'qr_code': laravel_student.get('student_id'),  # Use student_id as QR code
                                'status': 'active' if laravel_student.get('status') == 'active' else 'withdrawn',
                                'class_name': django_class_name or 'Unknown'
                            }
                            
                            student = Student.objects.create(**student_data)
                            
                            # Cache for later use
                            self._student_cache[laravel_id] = student
                            
                        result.add_success()
                        
                    except Exception as e:
                        error_msg = f"Failed to migrate student {laravel_student.get('student_id', 'unknown')}: {str(e)}"
                        logger.error(error_msg)
                        result.add_failure(error_msg)
                        
                offset += self.batch_size
                logger.info(f"Processed {result.total_processed} students...")
                
        except Exception as e:
            error_msg = f"Student migration failed: {str(e)}"
            logger.error(error_msg)
            result.add_failure(error_msg)
            
        result.finish()
        logger.info(f"Student migration completed: {result.successful} successful, {result.failed} failed, {result.skipped} skipped")
        return result
        
    def migrate_classes(self) -> MigrationResult:
        """
        Migrate class records from Laravel to Django.
        Since Django uses class_name field, this just validates class names exist.
        """
        result = MigrationResult("classes")
        logger.info("Starting class migration...")
        
        try:
            laravel_classes = self.extractor.extract_classes()
            
            for laravel_class in laravel_classes:
                try:
                    class_name = laravel_class.get('name')
                    if not class_name:
                        result.add_failure(f"Class missing name: {laravel_class}")
                        continue
                        
                    # Cache class name for later use
                    self._class_cache[laravel_class.get('id')] = class_name
                    result.add_success()
                    
                except Exception as e:
                    error_msg = f"Failed to process class {laravel_class.get('name', 'unknown')}: {str(e)}"
                    logger.error(error_msg)
                    result.add_failure(error_msg)
                    
        except Exception as e:
            error_msg = f"Class migration failed: {str(e)}"
            logger.error(error_msg)
            result.add_failure(error_msg)
            
        result.finish()
        logger.info(f"Class migration completed: {result.successful} successful, {result.failed} failed, {result.skipped} skipped")
        return result
        
    def migrate_attendance_records(self) -> MigrationResult:
        """
        Migrate attendance records from Laravel to Django.
        Maps checkin/checkout timestamps and derives status.
        """
        result = MigrationResult("attendance_records")
        logger.info("Starting attendance record migration...")
        
        try:
            offset = 0
            while True:
                # Extract batch of attendance records
                laravel_attendance = self.extractor.extract_attendance_records(self.batch_size, offset)
                if not laravel_attendance:
                    break
                    
                for laravel_record in laravel_attendance:
                    try:
                        # Map Laravel data to Django format
                        mapped_data = LaravelFieldMapper.map_attendance_data(laravel_record)
                        
                        # Get Laravel attendance ID for duplicate checking
                        laravel_attendance_id = laravel_record.get('attendance_id')
                        if not laravel_attendance_id:
                            result.add_failure(f"Attendance record missing attendance_id: {laravel_record}")
                            continue
                            
                        # Check for existing record
                        existing_record = AttendanceEntry.objects.filter(
                            laravel_attendance_id=laravel_attendance_id
                        ).first()
                        
                        if existing_record:
                            result.add_skip(f"Attendance record {laravel_attendance_id} already exists")
                            continue
                            
                        # Find corresponding Django student
                        laravel_student_id = laravel_record.get('student_id')
                        if not laravel_student_id:
                            result.add_failure(f"Attendance record missing student_id: {laravel_record}")
                            continue
                            
                        student = Student.objects.filter(
                            laravel_student_id=laravel_student_id
                        ).first()
                        
                        if not student:
                            result.add_failure(f"Student {laravel_student_id} not found for attendance record {laravel_attendance_id}")
                            continue
                            
                        # Get marked_by user
                        marked_by_name = laravel_record.get('checkin_by')
                        marked_by_user = None
                        if marked_by_name:
                            marked_by_user = self._get_or_create_user(marked_by_name)
                            
                        # Get checkout_by user
                        checkout_by_name = laravel_record.get('checkout_by')
                        checkout_by_user = None
                        if checkout_by_name:
                            checkout_by_user = self._get_or_create_user(checkout_by_name)
                            
                        # Determine date from checkin or created_at
                        record_date = None
                        if laravel_record.get('checkin'):
                            record_date = laravel_record['checkin'].date()
                        elif laravel_record.get('created_at'):
                            record_date = laravel_record['created_at'].date()
                        else:
                            record_date = timezone.now().date()
                            
                        # Get class name
                        class_name = "Unknown"
                        if student.class_name:
                            class_name = student.class_name
                            
                        if not self.dry_run:
                            # Create attendance entry
                            attendance_data = {
                                'student': student,
                                'date': record_date,
                                'status': mapped_data.get('status', 'absent'),
                                'class_name': class_name,
                                'laravel_attendance_id': laravel_attendance_id,
                                'parent_name': laravel_record.get('parent_name') or '',
                                'reason': laravel_record.get('reason') or '',
                                'is_early_departure': mapped_data.get('is_early_departure', False)
                            }
                            
                            # Add times if available
                            if laravel_record.get('checkin'):
                                attendance_data['check_in_time'] = laravel_record['checkin'].time()
                            if laravel_record.get('checkout'):
                                attendance_data['check_out_time'] = laravel_record['checkout'].time()
                                
                            # Add users if available
                            if marked_by_user:
                                attendance_data['marked_by'] = marked_by_user
                            else:
                                # Use the test user as fallback for marked_by (required field)
                                from users.models import User
                                test_user = User.objects.filter(username='test_migrator').first()
                                if test_user:
                                    attendance_data['marked_by'] = test_user
                                else:
                                    # Create a default user if none exists
                                    test_user = User.objects.create_user(
                                        username='migration_default',
                                        email='migration@hodari.ac.tz',
                                        first_name='Migration',
                                        last_name='Default'
                                    )
                                    attendance_data['marked_by'] = test_user
                                    
                            if checkout_by_user:
                                attendance_data['checkout_by'] = checkout_by_user
                                
                            # Check for existing record on same date for same student
                            existing_date_record = AttendanceEntry.objects.filter(
                                student=student,
                                date=record_date
                            ).first()
                            
                            if existing_date_record:
                                result.add_skip(f"Attendance for student {laravel_student_id} on {record_date} already exists")
                                continue
                                
                            AttendanceEntry.objects.create(**attendance_data)
                            
                        result.add_success()
                        
                    except Exception as e:
                        error_msg = f"Failed to migrate attendance record {laravel_record.get('attendance_id', 'unknown')}: {str(e)}"
                        logger.error(error_msg)
                        result.add_failure(error_msg)
                        
                offset += self.batch_size
                logger.info(f"Processed {result.total_processed} attendance records...")
                
        except Exception as e:
            error_msg = f"Attendance record migration failed: {str(e)}"
            logger.error(error_msg)
            result.add_failure(error_msg)
            
        result.finish()
        logger.info(f"Attendance migration completed: {result.successful} successful, {result.failed} failed, {result.skipped} skipped")
        return result
        
    def migrate_parent_relationships(self) -> MigrationResult:
        """
        Migrate parent records and student-parent relationships from Laravel to Django.
        Creates ParentGuardian objects and StudentParentRelationship links.
        """
        result = MigrationResult("parent_relationships")
        logger.info("Starting parent relationship migration...")
        
        try:
            # First migrate parents
            offset = 0
            while True:
                laravel_parents = self.extractor.extract_parents(self.batch_size, offset)
                if not laravel_parents:
                    break
                    
                for laravel_parent in laravel_parents:
                    try:
                        laravel_parent_id = laravel_parent.get('id')
                        if not laravel_parent_id:
                            result.add_failure(f"Parent missing id: {laravel_parent}")
                            continue
                            
                        # Check if parent already exists
                        existing_parent = LaravelParent.objects.filter(
                            laravel_parent_id=laravel_parent_id
                        ).first()
                        
                        if existing_parent:
                            result.add_skip(f"Parent {laravel_parent_id} already exists")
                            self._parent_cache[laravel_parent_id] = existing_parent
                            continue
                            
                        if not self.dry_run:
                            # Map parent data
                            mapped_data = LaravelFieldMapper.map_parent_data(laravel_parent)
                            
                            parent_data = {
                                'first_name': mapped_data.get('first_name', ''),
                                'last_name': mapped_data.get('last_name', ''),
                                'email': mapped_data.get('email') or '',
                                'phone': mapped_data.get('phone') or '',
                                'secondary_phone': mapped_data.get('secondary_phone') or '',
                                'address': mapped_data.get('address') or '',
                                'city': mapped_data.get('city') or '',
                                'state': mapped_data.get('state') or '',
                                'postal_code': mapped_data.get('postal_code') or '',
                                'occupation': mapped_data.get('occupation') or '',
                                'laravel_parent_id': laravel_parent_id
                            }
                            
                            parent = LaravelParent.objects.create(**parent_data)
                            self._parent_cache[laravel_parent_id] = parent
                            
                        result.add_success()
                        
                    except Exception as e:
                        error_msg = f"Failed to migrate parent {laravel_parent.get('id', 'unknown')}: {str(e)}"
                        logger.error(error_msg)
                        result.add_failure(error_msg)
                        
                offset += self.batch_size
                
            # Now migrate relationships
            relationships = self.extractor.extract_student_parent_relationships()
            for relationship in relationships:
                try:
                    laravel_student_id = relationship.get('student_id')
                    laravel_parent_id = relationship.get('parent_id')
                    
                    if not laravel_student_id or not laravel_parent_id:
                        result.add_failure(f"Relationship missing student_id or parent_id: {relationship}")
                        continue
                        
                    # Find Django objects
                    student = Student.objects.filter(laravel_student_id=laravel_student_id).first()
                    parent = LaravelParent.objects.filter(laravel_parent_id=laravel_parent_id).first()
                    
                    if not student:
                        result.add_failure(f"Student {laravel_student_id} not found for relationship")
                        continue
                        
                    if not parent:
                        result.add_failure(f"Parent {laravel_parent_id} not found for relationship")
                        continue
                        
                    # Check if relationship already exists
                    existing_rel = StudentLaravelParent.objects.filter(
                        student=student,
                        parent=parent
                    ).first()
                    
                    if existing_rel:
                        result.add_skip(f"Relationship between student {laravel_student_id} and parent {laravel_parent_id} already exists")
                        continue
                        
                    if not self.dry_run:
                        StudentLaravelParent.objects.create(
                            student=student,
                            parent=parent,
                            relationship=relationship.get('relationship', 'guardian'),
                            is_primary_contact=relationship.get('is_primary_contact', False),
                            can_pickup=relationship.get('can_pickup', True)
                        )
                        
                    result.add_success()
                    
                except Exception as e:
                    error_msg = f"Failed to migrate relationship {relationship}: {str(e)}"
                    logger.error(error_msg)
                    result.add_failure(error_msg)
                    
        except Exception as e:
            error_msg = f"Parent relationship migration failed: {str(e)}"
            logger.error(error_msg)
            result.add_failure(error_msg)
            
        result.finish()
        logger.info(f"Parent relationship migration completed: {result.successful} successful, {result.failed} failed, {result.skipped} skipped")
        return result
        
    def migrate_otp_codes(self) -> MigrationResult:
        """
        Migrate OTP codes from Laravel to Django.
        Preserves OTP codes with expiry and verification status.
        """
        result = MigrationResult("otp_codes")
        logger.info("Starting OTP code migration...")
        
        try:
            offset = 0
            while True:
                # Extract batch of OTP codes
                laravel_otp_codes = self.extractor.extract_otp_codes(self.batch_size, offset, active_only=False)
                if not laravel_otp_codes:
                    break
                    
                for laravel_otp in laravel_otp_codes:
                    try:
                        laravel_otp_id = laravel_otp.get('id')
                        if not laravel_otp_id:
                            result.add_failure(f"OTP code missing id: {laravel_otp}")
                            continue
                            
                        # Check if OTP already exists
                        existing_otp = OtpCode.objects.filter(
                            laravel_parent_id=laravel_otp.get('parent_id'),
                            code=laravel_otp.get('code'),
                            expires_at=laravel_otp.get('expires_at')
                        ).first()
                        
                        if existing_otp:
                            result.add_skip(f"OTP code {laravel_otp_id} already exists")
                            continue
                            
                        # Find corresponding Django parent
                        laravel_parent_id = laravel_otp.get('parent_id')
                        if not laravel_parent_id:
                            result.add_failure(f"OTP code missing parent_id: {laravel_otp}")
                            continue
                            
                        # Try to find parent in LaravelParent model
                        parent = LaravelParent.objects.filter(
                            laravel_parent_id=laravel_parent_id
                        ).first()
                        
                        # If not found in LaravelParent, try to find in ParentGuardian
                        django_parent = None
                        if parent and parent.guardian:
                            django_parent = parent.guardian
                        else:
                            # Try to find by phone number in ParentGuardian
                            if parent and parent.phone:
                                django_parent = ParentGuardian.objects.filter(
                                    phone=parent.phone
                                ).first()
                        
                        if not django_parent:
                            result.add_failure(f"Parent {laravel_parent_id} not found for OTP code {laravel_otp_id}")
                            continue
                            
                        if not self.dry_run:
                            # Create OTP code
                            otp_data = {
                                'parent': django_parent,
                                'code': laravel_otp.get('code', ''),
                                'expires_at': laravel_otp.get('expires_at'),
                                'verified': laravel_otp.get('verified', False),
                                'laravel_parent_id': laravel_parent_id
                            }
                            
                            OtpCode.objects.create(**otp_data)
                            
                        result.add_success()
                        
                    except Exception as e:
                        error_msg = f"Failed to migrate OTP code {laravel_otp.get('id', 'unknown')}: {str(e)}"
                        logger.error(error_msg)
                        result.add_failure(error_msg)
                        
                offset += self.batch_size
                logger.info(f"Processed {result.total_processed} OTP codes...")
                
        except Exception as e:
            error_msg = f"OTP code migration failed: {str(e)}"
            logger.error(error_msg)
            result.add_failure(error_msg)
            
        result.finish()
        logger.info(f"OTP code migration completed: {result.successful} successful, {result.failed} failed, {result.skipped} skipped")
        return result
        
    def migrate_messages(self) -> MigrationResult:
        """
        Migrate SMS message queue from Laravel to Django.
        Preserves message status and delivery tracking.
        """
        result = MigrationResult("messages")
        logger.info("Starting message migration...")
        
        try:
            offset = 0
            while True:
                # Extract batch of messages
                laravel_messages = self.extractor.extract_messages(self.batch_size, offset)
                if not laravel_messages:
                    break
                    
                for laravel_message in laravel_messages:
                    try:
                        laravel_message_id = laravel_message.get('id')
                        if not laravel_message_id:
                            result.add_failure(f"Message missing id: {laravel_message}")
                            continue
                            
                        # Check if message already exists
                        existing_message = Message.objects.filter(
                            laravel_message_id=laravel_message_id
                        ).first()
                        
                        if existing_message:
                            result.add_skip(f"Message {laravel_message_id} already exists")
                            continue
                            
                        if not self.dry_run:
                            # Create message
                            message_data = {
                                'phone': laravel_message.get('phone', ''),
                                'message': laravel_message.get('message', ''),
                                'status': laravel_message.get('status', 0),  # Laravel status: 0=pending, 1=sent, 2=failed
                                'laravel_message_id': laravel_message_id
                            }
                            
                            # Set sent_at if message was sent
                            if laravel_message.get('status') == 1 and laravel_message.get('updated_at'):
                                message_data['sent_at'] = laravel_message['updated_at']
                                
                            Message.objects.create(**message_data)
                            
                        result.add_success()
                        
                    except Exception as e:
                        error_msg = f"Failed to migrate message {laravel_message.get('id', 'unknown')}: {str(e)}"
                        logger.error(error_msg)
                        result.add_failure(error_msg)
                        
                offset += self.batch_size
                logger.info(f"Processed {result.total_processed} messages...")
                
        except Exception as e:
            error_msg = f"Message migration failed: {str(e)}"
            logger.error(error_msg)
            result.add_failure(error_msg)
            
        result.finish()
        logger.info(f"Message migration completed: {result.successful} successful, {result.failed} failed, {result.skipped} skipped")
        return result
        
    def validate_migration(self) -> Dict:
        """
        Validate migration results and data integrity.
        Returns validation report with statistics and issues.
        """
        validation_report = {
            'valid': True,
            'issues': [],
            'statistics': {},
            'validation_time': timezone.now().isoformat()
        }
        
        try:
            # Count migrated records
            migrated_students = Student.objects.filter(laravel_student_id__isnull=False).count()
            migrated_attendance = AttendanceEntry.objects.filter(laravel_attendance_id__isnull=False).count()
            migrated_parents = LaravelParent.objects.filter(laravel_parent_id__isnull=False).count()
            migrated_otp_codes = OtpCode.objects.filter(laravel_parent_id__isnull=False).count()
            migrated_messages = Message.objects.filter(laravel_message_id__isnull=False).count()
            
            validation_report['statistics'] = {
                'migrated_students': migrated_students,
                'migrated_attendance': migrated_attendance,
                'migrated_parents': migrated_parents,
                'migrated_otp_codes': migrated_otp_codes,
                'migrated_messages': migrated_messages,
                'total_students': Student.objects.count(),
                'total_attendance': AttendanceEntry.objects.count(),
                'total_parents': LaravelParent.objects.count(),
                'total_otp_codes': OtpCode.objects.count(),
                'total_messages': Message.objects.count()
            }
            
            # Check for orphaned attendance records
            orphaned_attendance = AttendanceEntry.objects.filter(
                laravel_attendance_id__isnull=False,
                student__laravel_student_id__isnull=True
            ).count()
            
            if orphaned_attendance > 0:
                validation_report['valid'] = False
                validation_report['issues'].append(f"Found {orphaned_attendance} orphaned attendance records")
                
            # Check for students without attendance
            students_no_attendance = Student.objects.filter(
                laravel_student_id__isnull=False,
                attendance_entries__isnull=True
            ).count()
            
            if students_no_attendance > 0:
                validation_report['issues'].append(f"Found {students_no_attendance} migrated students without attendance records")
                
            # Check for parents without relationships
            parents_no_relationships = LaravelParent.objects.filter(
                laravel_parent_id__isnull=False,
                students__isnull=True
            ).count()
            
            if parents_no_relationships > 0:
                validation_report['issues'].append(f"Found {parents_no_relationships} migrated parents without student relationships")
                
            validation_report['statistics'].update({
                'orphaned_attendance': orphaned_attendance,
                'students_no_attendance': students_no_attendance,
                'parents_no_relationships': parents_no_relationships
            })
            
        except Exception as e:
            validation_report['valid'] = False
            validation_report['issues'].append(f"Validation error: {str(e)}")
            
        return validation_report
        
    def generate_migration_report(self) -> MigrationReport:
        """
        Generate comprehensive migration report with all operation results.
        """
        self.report.finish()
        return self.report
        
    def run_full_migration(self) -> MigrationReport:
        """
        Run complete migration process in correct order.
        Returns comprehensive migration report.
        """
        logger.info("Starting full data migration from Laravel to Django...")
        
        try:
            # Step 1: Migrate classes first (needed for student references)
            class_result = self.migrate_classes()
            self.report.add_result('classes', class_result)
            
            # Step 2: Migrate students
            student_result = self.migrate_students()
            self.report.add_result('students', student_result)
            
            # Step 3: Migrate parent relationships
            parent_result = self.migrate_parent_relationships()
            self.report.add_result('parent_relationships', parent_result)
            
            # Step 4: Migrate attendance records
            attendance_result = self.migrate_attendance_records()
            self.report.add_result('attendance_records', attendance_result)
            
            # Step 5: Migrate OTP codes
            otp_result = self.migrate_otp_codes()
            self.report.add_result('otp_codes', otp_result)
            
            # Step 6: Migrate messages
            message_result = self.migrate_messages()
            self.report.add_result('messages', message_result)
            
            # Step 7: Validate migration
            validation_report = self.validate_migration()
            self.report.results['validation'] = validation_report
            
            logger.info("Full migration completed successfully")
            
        except Exception as e:
            error_msg = f"Full migration failed: {str(e)}"
            logger.error(error_msg)
            # Add error to report
            if 'errors' not in self.report.results:
                self.report.results['errors'] = []
            self.report.results['errors'].append(error_msg)
            
        return self.generate_migration_report()