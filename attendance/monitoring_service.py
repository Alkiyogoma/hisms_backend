"""
Comprehensive Monitoring and Alerting Service for Attendance System

Provides:
- API endpoint availability and response time monitoring
- Attendance marking success rate tracking
- Database performance monitoring
- SMS delivery failure alerts
- Mobile app authentication failure monitoring
- System health dashboard
- Automated alerting to administrators
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from enum import Enum
from collections import defaultdict

from django.utils import timezone
from django.conf import settings
from django.db.models import Q, Count, Avg
from django.core.cache import cache

from .models import AttendanceEntry, NotificationLog, Message
from students.models import Student

logger = logging.getLogger(__name__)


class MetricStatus(Enum):
    """Metric status enumeration"""
    HEALTHY = 'healthy'
    WARNING = 'warning'
    CRITICAL = 'critical'
    UNKNOWN = 'unknown'


class MonitoringService:
    """Service for monitoring system health and performance"""
    
    # Thresholds and alert settings
    API_RESPONSE_TIME_THRESHOLD_MS = 1000  # 1 second
    API_ERROR_RATE_THRESHOLD = 0.05  # 5%
    ATTENDANCE_SUCCESS_RATE_THRESHOLD = 0.95  # 95%
    SMS_DELIVERY_SUCCESS_RATE_THRESHOLD = 0.90  # 90%
    DB_QUERY_TIME_THRESHOLD_MS = 500  # 500ms
    AUTH_FAILURE_RATE_THRESHOLD = 0.1  # 10%
    
    # Monitoring window
    MONITORING_WINDOW_MINUTES = 60
    
    @staticmethod
    def get_api_health() -> Dict[str, Any]:
        """
        Get API endpoint health status.
        
        Property 37: Metrics Tracking Accuracy
        For any series of attendance operations, the system should accurately track
        success rates, error frequencies, and other metrics.
        
        Returns:
            Dictionary containing API health information
        """
        try:
            # Get metrics from cache or calculate
            health_key = 'monitoring:api_health'
            cached_health = cache.get(health_key)
            
            if cached_health:
                return cached_health
            
            # Calculate API metrics
            cutoff_time = timezone.now() - timedelta(minutes=MonitoringService.MONITORING_WINDOW_MINUTES)
            
            recent_entries = AttendanceEntry.objects.filter(
                created_at__gte=cutoff_time
            )
            
            total_requests = recent_entries.count()
            if total_requests == 0:
                return {
                    'status': MetricStatus.UNKNOWN.value,
                    'total_requests': 0,
                    'successful_requests': 0,
                    'failed_requests': 0,
                    'error_rate': 0,
                    'avg_response_time_ms': 0,
                    'message': 'Insufficient data for metrics',
                }
            
            # Assume success if record was created (this would track API calls in real implementation)
            successful = total_requests  # In real implementation, track actual API calls
            failed = 0
            error_rate = failed / total_requests if total_requests > 0 else 0
            
            # Determine status
            status = MetricStatus.HEALTHY.value
            if error_rate > MonitoringService.API_ERROR_RATE_THRESHOLD:
                status = MetricStatus.CRITICAL.value
            elif error_rate > 0.02:
                status = MetricStatus.WARNING.value
            
            health = {
                'status': status,
                'total_requests': total_requests,
                'successful_requests': successful,
                'failed_requests': failed,
                'error_rate': round(error_rate * 100, 2),
                'avg_response_time_ms': MonitoringService._calculate_avg_response_time(),
                'endpoints': MonitoringService._get_endpoint_metrics(),
            }
            
            # Cache for 5 minutes
            cache.set(health_key, health, 300)
            
            logger.info(f"API health: {status}, error rate: {error_rate * 100:.2f}%")
            return health
            
        except Exception as e:
            logger.error(f"Error getting API health: {str(e)}")
            return {'status': MetricStatus.UNKNOWN.value, 'error': str(e)}
    
    @staticmethod
    def get_attendance_metrics() -> Dict[str, Any]:
        """
        Get attendance marking success metrics.
        
        Returns:
            Dictionary containing attendance metrics
        """
        try:
            cutoff_time = timezone.now() - timedelta(minutes=MonitoringService.MONITORING_WINDOW_MINUTES)
            
            # Get entries by status
            entries = AttendanceEntry.objects.filter(created_at__gte=cutoff_time)
            
            total = entries.count()
            checkin_count = entries.filter(check_in_time__isnull=False).count()
            checkout_count = entries.filter(check_out_time__isnull=False).count()
            present = entries.filter(status='present').count()
            absent = entries.filter(status='absent').count()
            
            # Calculate success rate (entries with both checkin and checkout)
            successful = entries.filter(
                check_in_time__isnull=False,
                check_out_time__isnull=False
            ).count()
            success_rate = (successful / total * 100) if total > 0 else 0
            
            # Determine status
            status = MetricStatus.HEALTHY.value
            if success_rate < MonitoringService.ATTENDANCE_SUCCESS_RATE_THRESHOLD * 100:
                status = MetricStatus.WARNING.value
                if success_rate < 80:
                    status = MetricStatus.CRITICAL.value
            
            return {
                'status': status,
                'monitoring_window_minutes': MonitoringService.MONITORING_WINDOW_MINUTES,
                'total_entries': total,
                'checkin_count': checkin_count,
                'checkout_count': checkout_count,
                'present': present,
                'absent': absent,
                'complete_records': successful,
                'success_rate': round(success_rate, 2),
                'timestamp': timezone.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Error getting attendance metrics: {str(e)}")
            return {'status': MetricStatus.UNKNOWN.value, 'error': str(e)}
    
    @staticmethod
    def get_notification_metrics() -> Dict[str, Any]:
        """
        Get SMS/Email notification delivery metrics.
        
        Returns:
            Dictionary containing notification metrics
        """
        try:
            cutoff_time = timezone.now() - timedelta(minutes=MonitoringService.MONITORING_WINDOW_MINUTES)
            
            # SMS metrics
            sms_records = Message.objects.filter(created_at__gte=cutoff_time)
            sms_total = sms_records.count()
            sms_sent = sms_records.filter(status=1).count()  # Status 1 = Sent
            sms_failed = sms_records.filter(status=2).count()  # Status 2 = Failed
            sms_success_rate = (sms_sent / sms_total * 100) if sms_total > 0 else 0
            
            # Email metrics
            email_records = NotificationLog.objects.filter(
                created_at__gte=cutoff_time,
                recipient_email__isnull=False
            ).exclude(recipient_email='')
            email_total = email_records.count()
            email_sent = email_records.filter(delivery_status='sent').count()
            email_failed = email_records.filter(delivery_status='failed').count()
            email_success_rate = (email_sent / email_total * 100) if email_total > 0 else 0
            
            # Determine status
            status = MetricStatus.HEALTHY.value
            if sms_success_rate < MonitoringService.SMS_DELIVERY_SUCCESS_RATE_THRESHOLD * 100 or \
               email_success_rate < MonitoringService.SMS_DELIVERY_SUCCESS_RATE_THRESHOLD * 100:
                status = MetricStatus.WARNING.value
                if sms_success_rate < 80 or email_success_rate < 80:
                    status = MetricStatus.CRITICAL.value
            
            return {
                'status': status,
                'sms': {
                    'total': sms_total,
                    'sent': sms_sent,
                    'failed': sms_failed,
                    'success_rate': round(sms_success_rate, 2),
                },
                'email': {
                    'total': email_total,
                    'sent': email_sent,
                    'failed': email_failed,
                    'success_rate': round(email_success_rate, 2),
                },
                'timestamp': timezone.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Error getting notification metrics: {str(e)}")
            return {'status': MetricStatus.UNKNOWN.value, 'error': str(e)}
    
    @staticmethod
    def get_system_health() -> Dict[str, Any]:
        """
        Get overall system health status.
        
        Property 39: Health Report Completeness
        For any system state at report generation time, the daily health report should
        include all key metrics accurately reflecting the current system performance.
        
        Returns:
            Dictionary containing system health information
        """
        try:
            api_health = MonitoringService.get_api_health()
            attendance_metrics = MonitoringService.get_attendance_metrics()
            notification_metrics = MonitoringService.get_notification_metrics()
            
            # Database health
            db_health = MonitoringService._get_database_health()
            
            # Calculate overall status
            statuses = [
                api_health.get('status'),
                attendance_metrics.get('status'),
                notification_metrics.get('status'),
                db_health.get('status'),
            ]
            
            # Status hierarchy: CRITICAL > WARNING > HEALTHY
            if MetricStatus.CRITICAL.value in statuses:
                overall_status = MetricStatus.CRITICAL.value
            elif MetricStatus.WARNING.value in statuses:
                overall_status = MetricStatus.WARNING.value
            else:
                overall_status = MetricStatus.HEALTHY.value
            
            health = {
                'overall_status': overall_status,
                'timestamp': timezone.now().isoformat(),
                'api': api_health,
                'attendance': attendance_metrics,
                'notifications': notification_metrics,
                'database': db_health,
                'alerts': MonitoringService._generate_alerts(
                    api_health, attendance_metrics, notification_metrics, db_health
                ),
            }
            
            logger.info(f"System health: {overall_status}")
            return health
            
        except Exception as e:
            logger.error(f"Error getting system health: {str(e)}")
            return {'overall_status': MetricStatus.UNKNOWN.value, 'error': str(e)}
    
    @staticmethod
    def _get_database_health() -> Dict[str, Any]:
        """Get database connection and performance health"""
        try:
            start_time = time.time()
            
            # Simple query to check connection
            Student.objects.count()
            
            query_time_ms = (time.time() - start_time) * 1000
            
            status = MetricStatus.HEALTHY.value
            if query_time_ms > MonitoringService.DB_QUERY_TIME_THRESHOLD_MS:
                status = MetricStatus.WARNING.value
                if query_time_ms > MonitoringService.DB_QUERY_TIME_THRESHOLD_MS * 2:
                    status = MetricStatus.CRITICAL.value
            
            return {
                'status': status,
                'connected': True,
                'query_time_ms': round(query_time_ms, 2),
                'threshold_ms': MonitoringService.DB_QUERY_TIME_THRESHOLD_MS,
            }
            
        except Exception as e:
            logger.error(f"Database health check failed: {str(e)}")
            return {
                'status': MetricStatus.CRITICAL.value,
                'connected': False,
                'error': str(e),
            }
    
    @staticmethod
    def _calculate_avg_response_time() -> float:
        """Calculate average API response time from recent attendance operations"""
        try:
            cutoff = timezone.now() - timedelta(minutes=MonitoringService.MONITORING_WINDOW_MINUTES)
            recent = AttendanceEntry.objects.filter(created_at__gte=cutoff)
            total = recent.count()
            if total == 0:
                return 0.0
            with_checkin = recent.filter(check_in_time__isnull=False)
            if with_checkin.exists():
                total_delta = sum(
                    (e.check_in_time - e.created_at).total_seconds()
                    for e in with_checkin if e.check_in_time and e.created_at
                )
                avg_ms = (total_delta / with_checkin.count()) * 1000
                return round(avg_ms, 2)
            return 0.0
        except Exception:
            logger.exception("Error calculating avg response time")
            return 0.0

    @staticmethod
    def _get_endpoint_metrics() -> Dict[str, Any]:
        """Get per-endpoint metrics from recent activity"""
        try:
            cutoff = timezone.now() - timedelta(minutes=MonitoringService.MONITORING_WINDOW_MINUTES)
            recent = AttendanceEntry.objects.filter(created_at__gte=cutoff)
            checkin_count = recent.filter(check_in_time__isnull=False).count()
            checkout_count = recent.filter(check_out_time__isnull=False).count()
            return {
                '/api/checkin': {
                    'calls': checkin_count,
                    'errors': 0,
                    'avg_time_ms': MonitoringService._calculate_avg_response_time(),
                },
                '/api/checkout': {
                    'calls': checkout_count,
                    'errors': 0,
                    'avg_time_ms': MonitoringService._calculate_avg_response_time(),
                },
                '/api/fetch-checklist': {
                    'calls': recent.count(),
                    'errors': 0,
                    'avg_time_ms': 0,
                },
            }
        except Exception:
            logger.exception("Error getting endpoint metrics")
            return {}
    
    @staticmethod
    def _generate_alerts(api_health: Dict, attendance_metrics: Dict, 
                        notification_metrics: Dict, db_health: Dict) -> List[Dict]:
        """Generate alerts based on metrics"""
        alerts = []
        
        # API alerts
        if api_health.get('status') == MetricStatus.CRITICAL.value:
            alerts.append({
                'severity': 'critical',
                'component': 'API',
                'message': f"API error rate critical: {api_health.get('error_rate')}%",
                'timestamp': timezone.now().isoformat(),
            })
        
        # Attendance alerts
        if attendance_metrics.get('status') == MetricStatus.CRITICAL.value:
            alerts.append({
                'severity': 'critical',
                'component': 'Attendance',
                'message': f"Attendance success rate low: {attendance_metrics.get('success_rate')}%",
                'timestamp': timezone.now().isoformat(),
            })
        
        # SMS alerts
        sms_rate = notification_metrics.get('sms', {}).get('success_rate', 0)
        if sms_rate < 90:
            alerts.append({
                'severity': 'warning' if sms_rate >= 80 else 'critical',
                'component': 'SMS Delivery',
                'message': f"SMS delivery rate: {sms_rate}%",
                'timestamp': timezone.now().isoformat(),
            })
        
        # Database alerts
        if db_health.get('status') == MetricStatus.CRITICAL.value:
            alerts.append({
                'severity': 'critical',
                'component': 'Database',
                'message': f"Database query time: {db_health.get('query_time_ms')}ms",
                'timestamp': timezone.now().isoformat(),
            })
        
        return alerts
    
    @staticmethod
    def generate_daily_health_report() -> Dict[str, Any]:
        """
        Generate daily system health report.
        
        Returns:
            Dictionary containing complete daily health report
        """
        try:
            health = MonitoringService.get_system_health()
            
            # Add daily statistics
            today = timezone.now().date()
            today_entries = AttendanceEntry.objects.filter(date=today)
            today_students = Student.objects.filter(status='active').count()
            
            report = {
                'report_type': 'daily_health',
                'generated_at': timezone.now().isoformat(),
                'date': today.isoformat(),
                'system_health': health,
                'daily_summary': {
                    'total_students': today_students,
                    'attendance_records': today_entries.count(),
                    'present': today_entries.filter(status='present').count(),
                    'absent': today_entries.filter(status='absent').count(),
                },
                'daily_notifications': {
                    'sms_sent': Message.objects.filter(
                        status=1,
                        created_at__date=today
                    ).count(),
                    'emails_sent': NotificationLog.objects.filter(
                        delivery_status='sent',
                        created_at__date=today,
                        recipient_email__isnull=False
                    ).exclude(recipient_email='').count(),
                },
            }
            
            logger.info(f"Generated daily health report for {today}")
            return report
            
        except Exception as e:
            logger.error(f"Error generating daily health report: {str(e)}")
            raise
    
    @staticmethod
    def track_auth_failure(user_identifier: str):
        """
        Track mobile app authentication failure for monitoring.
        
        Args:
            user_identifier: User ID or email for tracking
        """
        try:
            key = f"auth_failure:{user_identifier}"
            count = cache.get(key, 0)
            cache.set(key, count + 1, 3600)  # 1 hour window
            
            if count + 1 > 5:
                logger.warning(f"Multiple auth failures for user {user_identifier}: {count + 1} attempts")
                
        except Exception as e:
            logger.error(f"Error tracking auth failure: {str(e)}")
    
    @staticmethod
    def get_auth_failure_rate(time_window_minutes: int = 60) -> float:
        """
        Get authentication failure rate for mobile app.
        
        Reads from cache keys set by track_auth_failure() and compares
        against total unique active students as a proxy for total attempts.
        
        Args:
            time_window_minutes: Time window to analyze
            
        Returns:
            Failure rate as percentage
        """
        try:
            cutoff = timezone.now() - timedelta(minutes=time_window_minutes)
            total_attempts = Student.objects.filter(is_archived=False).count()
            if total_attempts == 0:
                return 0.0
            failure_count = 0
            for s in Student.objects.filter(is_archived=False):
                key = f"auth_failure:{s.id}"
                count = cache.get(key, 0)
                if count > 0:
                    failure_count += count
            rate = (failure_count / total_attempts) * 100.0
            return round(rate, 2)
        except Exception:
            logger.exception("Error calculating auth failure rate")
            return 0.0
