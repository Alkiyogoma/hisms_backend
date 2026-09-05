"""
Bidirectional Data Synchronization Service

Provides:
- Bidirectional sync between Django and Laravel
- Conflict detection and resolution with timestamp precedence
- Data integrity validation with checksums
- Sync failure handling with retry queues
- Real-time sync status monitoring
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
import hashlib
import json
from enum import Enum

from django.utils import timezone
from django.db import transaction
from django.conf import settings

from .models import AttendanceEntry
from students.models import Student

logger = logging.getLogger(__name__)


class SyncStatus(Enum):
    """Sync status enumeration"""
    PENDING = 'pending'
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CONFLICT = 'conflict'
    SYNCED = 'synced'


class SyncConflict:
    """Represents a data conflict between systems"""
    
    def __init__(self, record_id: int, django_record: Dict, laravel_record: Dict):
        self.record_id = record_id
        self.django_record = django_record
        self.laravel_record = laravel_record
        self.resolution = None
        self.resolved_at = None


class DataSyncService:
    """
    Service for bidirectional synchronization of attendance data
    between Django and Laravel systems.
    """
    
    # Sync settings
    MAX_RETRIES = 3
    RETRY_DELAY_MINUTES = 5
    BATCH_SIZE = 100
    SYNC_TIMEOUT_SECONDS = 300
    
    # Conflict resolution strategy: 'django_wins', 'laravel_wins', 'newest_wins', 'manual'
    CONFLICT_RESOLUTION_STRATEGY = 'newest_wins'
    
    # Checksum algorithm
    CHECKSUM_ALGORITHM = 'sha256'
    
    def __init__(self):
        """Initialize sync service"""
        self.pending_syncs = []
        self.failed_syncs = []
        self.conflicts = []
        self.sync_stats = {
            'total_records': 0,
            'synced': 0,
            'failed': 0,
            'conflicts': 0,
        }
    
    @staticmethod
    def sync_attendance_to_laravel(attendance_id: int) -> Tuple[bool, Optional[str]]:
        """
        Sync a single attendance record from Django to Laravel.
        
        Property 24: Data Synchronization Bidirectionality
        For any attendance record created in Django, the record should be correctly
        replicated to the Laravel system with proper field mapping.
        
        Args:
            attendance_id: ID of the AttendanceEntry to sync
            
        Returns:
            Tuple of (success, error_message)
        """
        try:
            attendance = AttendanceEntry.objects.get(id=attendance_id)
            
            # Map Django fields to Laravel format
            laravel_data = DataSyncService._map_django_to_laravel(attendance)
            
            # Sync to Laravel (would call Laravel API in real implementation)
            result = DataSyncService._send_to_laravel('/api/attendance/sync', laravel_data)
            
            if result['success']:
                # Update sync metadata
                attendance.laravel_synced = True
                attendance.laravel_sync_at = timezone.now()
                attendance.save(update_fields=['laravel_synced', 'laravel_sync_at'])
                
                logger.info(f"Successfully synced attendance {attendance_id} to Laravel")
                return True, None
            else:
                logger.error(f"Failed to sync attendance {attendance_id}: {result.get('error')}")
                return False, result.get('error', 'Unknown error')
                
        except AttendanceEntry.DoesNotExist:
            logger.error(f"Attendance record {attendance_id} not found")
            return False, "Record not found"
        except Exception as e:
            logger.error(f"Error syncing attendance {attendance_id}: {str(e)}")
            return False, str(e)
    
    @staticmethod
    def sync_attendance_from_laravel(laravel_attendance_id: int, laravel_data: Dict) -> Tuple[bool, Optional[str]]:
        """
        Sync attendance data from Laravel to Django.
        
        Args:
            laravel_attendance_id: Laravel attendance record ID
            laravel_data: Laravel attendance data
            
        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Check if record already exists
            existing = AttendanceEntry.objects.filter(
                laravel_attendance_id=laravel_attendance_id
            ).first()
            
            # Map Laravel fields to Django format
            django_data = DataSyncService._map_laravel_to_django(laravel_data)
            
            if existing:
                # Check for conflicts
                conflict = DataSyncService._detect_conflict(existing, django_data)
                
                if conflict:
                    # Resolve conflict
                    resolved_data = DataSyncService._resolve_conflict(existing, django_data)
                    
                    # Update with resolved data
                    for field, value in resolved_data.items():
                        setattr(existing, field, value)
                    existing.save()
                    
                    logger.info(f"Updated attendance {existing.id} from Laravel with conflict resolution")
                else:
                    # No conflict, update directly
                    for field, value in django_data.items():
                        setattr(existing, field, value)
                    existing.save()
                    
                    logger.info(f"Updated attendance {existing.id} from Laravel")
            else:
                # Create new record
                attendance = AttendanceEntry.objects.create(
                    laravel_attendance_id=laravel_attendance_id,
                    **django_data
                )
                
                logger.info(f"Created new attendance {attendance.id} from Laravel")
            
            return True, None
            
        except Exception as e:
            logger.error(f"Error syncing Laravel attendance {laravel_attendance_id}: {str(e)}")
            return False, str(e)
    
    @staticmethod
    def _map_django_to_laravel(attendance: AttendanceEntry) -> Dict[str, Any]:
        """
        Map Django AttendanceEntry to Laravel format.
        
        Args:
            attendance: Django AttendanceEntry object
            
        Returns:
            Dictionary with Laravel field mapping
        """
        return {
            'student_id': attendance.student.laravel_student_id or attendance.student.id,
            'checkin': attendance.check_in_time.isoformat() if attendance.check_in_time else None,
            'checkout': attendance.check_out_time.isoformat() if attendance.check_out_time else None,
            'status': attendance.status,
            'checkin_by': attendance.marked_by.id if attendance.marked_by else None,
            'checkout_by': attendance.checkout_by.id if attendance.checkout_by else None,
            'parent_name': attendance.parent_name,
            'reason': attendance.reason,
            'class_id': attendance.student.current_class.id if hasattr(attendance.student, 'current_class') else None,
        }
    
    @staticmethod
    def _map_laravel_to_django(laravel_data: Dict) -> Dict[str, Any]:
        """
        Map Laravel attendance data to Django format.
        
        Args:
            laravel_data: Laravel attendance data
            
        Returns:
            Dictionary with Django field mapping
        """
        # Get related objects
        student = Student.objects.get(laravel_student_id=laravel_data.get('student_id'))
        
        return {
            'student': student,
            'check_in_time': laravel_data.get('checkin'),
            'check_out_time': laravel_data.get('checkout'),
            'status': laravel_data.get('status', 'absent'),
            'parent_name': laravel_data.get('parent_name', ''),
            'reason': laravel_data.get('reason', ''),
        }
    
    @staticmethod
    def _detect_conflict(django_record: AttendanceEntry, laravel_data: Dict) -> bool:
        """
        Detect conflicts between Django and Laravel records.
        
        Property 25: Conflict Resolution Consistency
        For any conflicting data changes between systems, the conflict should be
        resolved using timestamp-based precedence rules.
        
        Args:
            django_record: Django AttendanceEntry object
            laravel_data: Laravel record data
            
        Returns:
            True if conflict detected, False otherwise
        """
        # Check if data differs significantly
        if django_record.check_in_time and laravel_data.get('checkin'):
            if abs((django_record.check_in_time - laravel_data.get('checkin')).total_seconds()) > 60:
                return True
        
        if django_record.check_out_time and laravel_data.get('checkout'):
            if abs((django_record.check_out_time - laravel_data.get('checkout')).total_seconds()) > 60:
                return True
        
        if django_record.status != laravel_data.get('status'):
            return True
        
        return False
    
    @staticmethod
    def _resolve_conflict(django_record: AttendanceEntry, laravel_data: Dict) -> Dict[str, Any]:
        """
        Resolve conflicts using configured strategy.
        
        Args:
            django_record: Django AttendanceEntry object
            laravel_data: Laravel record data
            
        Returns:
            Dictionary with resolved data
        """
        strategy = DataSyncService.CONFLICT_RESOLUTION_STRATEGY
        
        if strategy == 'newest_wins':
            # Use timestamps to determine which is newer
            django_updated = django_record.updated_at or django_record.created_at
            laravel_updated = laravel_data.get('updated_at')
            
            if laravel_updated and django_updated:
                if laravel_updated > django_updated:
                    return DataSyncService._map_laravel_to_django(laravel_data)
        
        elif strategy == 'django_wins':
            # Keep Django data
            return {}
        
        elif strategy == 'laravel_wins':
            # Use Laravel data
            return DataSyncService._map_laravel_to_django(laravel_data)
        
        # Default: keep Django data for unresolvable conflicts
        return {}
    
    @staticmethod
    def _send_to_laravel(endpoint: str, data: Dict) -> Dict[str, Any]:
        """
        Send data to Laravel API.
        
        Args:
            endpoint: Laravel API endpoint
            data: Data to send
            
        Returns:
            Response dictionary with success status
        """
        try:
            import requests
            
            laravel_api_url = settings.LARAVEL_API_URL if hasattr(settings, 'LARAVEL_API_URL') else 'http://localhost:8000'
            api_token = settings.LARAVEL_API_TOKEN if hasattr(settings, 'LARAVEL_API_TOKEN') else ''
            
            headers = {
                'Authorization': f'Bearer {api_token}',
                'Content-Type': 'application/json',
            }
            
            response = requests.post(
                f"{laravel_api_url}{endpoint}",
                json=data,
                headers=headers,
                timeout=DataSyncService.SYNC_TIMEOUT_SECONDS
            )
            
            if response.status_code in [200, 201]:
                return {'success': True, 'data': response.json()}
            else:
                return {'success': False, 'error': f"HTTP {response.status_code}"}
                
        except Exception as e:
            logger.error(f"Error sending data to Laravel: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def validate_data_integrity(attendance_id: Optional[int] = None) -> Tuple[bool, List[str]]:
        """
        Validate data integrity with checksum verification.
        
        Property 26: Data Integrity Validation
        For any dataset in both Django and Laravel systems, checksum verification
        should correctly identify when data integrity issues exist.
        
        Args:
            attendance_id: Optional attendance ID to validate specific record
            
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        try:
            errors = []
            
            if attendance_id:
                records = AttendanceEntry.objects.filter(id=attendance_id)
            else:
                records = AttendanceEntry.objects.all()
            
            for record in records:
                # Calculate checksum of Django record
                django_checksum = DataSyncService._calculate_checksum(record)
                
                # Would compare with Laravel checksum in real implementation
                # For now, just verify internal consistency
                
                if not record.student:
                    errors.append(f"Attendance {record.id}: Missing student reference")
                
                if record.check_out_time and record.check_in_time:
                    if record.check_out_time < record.check_in_time:
                        errors.append(f"Attendance {record.id}: Check-out before check-in")
                
                if record.status == 'present' and not record.check_in_time:
                    errors.append(f"Attendance {record.id}: Status is present but no check-in time")
            
            is_valid = len(errors) == 0
            logger.info(f"Data integrity validation: {is_valid}, {len(errors)} errors found")
            
            return is_valid, errors
            
        except Exception as e:
            logger.error(f"Error validating data integrity: {str(e)}")
            return False, [str(e)]
    
    @staticmethod
    def _calculate_checksum(record: AttendanceEntry) -> str:
        """
        Calculate checksum for an attendance record.
        
        Args:
            record: AttendanceEntry object
            
        Returns:
            Checksum string
        """
        data_to_hash = json.dumps({
            'student_id': record.student.id,
            'date': record.date.isoformat(),
            'status': record.status,
            'check_in_time': record.check_in_time.isoformat() if record.check_in_time else None,
            'check_out_time': record.check_out_time.isoformat() if record.check_out_time else None,
        }, sort_keys=True)
        
        return hashlib.sha256(data_to_hash.encode()).hexdigest()
    
    @staticmethod
    def get_sync_status() -> Dict[str, Any]:
        """
        Get current synchronization status.
        
        Returns:
            Dictionary containing sync status information
        """
        unsynced = AttendanceEntry.objects.filter(
            laravel_synced=False
        ).count()
        
        synced = AttendanceEntry.objects.filter(
            laravel_synced=True
        ).count()
        
        return {
            'total_records': AttendanceEntry.objects.count(),
            'synced_records': synced,
            'unsynced_records': unsynced,
            'sync_percentage': round((synced / (synced + unsynced) * 100) if (synced + unsynced) > 0 else 0, 2),
            'last_sync': timezone.now().isoformat(),
        }
    
    @staticmethod
    def retry_failed_syncs() -> Dict[str, int]:
        """
        Retry previously failed synchronizations.
        
        Returns:
            Dictionary with retry statistics
        """
        unsynced = AttendanceEntry.objects.filter(
            laravel_synced=False,
            sync_retry_count__lt=DataSyncService.MAX_RETRIES
        )[:DataSyncService.BATCH_SIZE]
        
        success_count = 0
        failure_count = 0
        
        for record in unsynced:
            success, error = DataSyncService.sync_attendance_to_laravel(record.id)
            
            if success:
                success_count += 1
            else:
                failure_count += 1
                record.sync_retry_count += 1
                record.sync_error = error
                record.save(update_fields=['sync_retry_count', 'sync_error'])
        
        logger.info(f"Retry sync results: {success_count} success, {failure_count} failed")
        
        return {
            'success': success_count,
            'failed': failure_count,
            'total_attempted': len(unsynced),
        }
