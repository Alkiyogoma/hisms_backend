"""
Comprehensive Reporting Service for Attendance System

Provides:
- Daily attendance reports with check-in/check-out times
- Monthly summaries with presence rates
- Class-wise attendance reports
- Parent pickup reports with authorized person tracking
- Excel export with configurable filters
- Attendance alerts for low attendance rates
- Attendance trend analysis
- Regulatory compliance reports
"""

from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional, Tuple
from django.utils import timezone
from django.db.models import Q, Count, Avg, F
from django.conf import settings
import logging
import json

from .models import AttendanceEntry, NotificationLog
from students.models import Student

logger = logging.getLogger(__name__)


class ReportingService:
    """Service for generating comprehensive attendance reports"""
    
    # Alert thresholds and settings
    ATTENDANCE_ALERT_THRESHOLD = float(settings.ATTENDANCE_ALERT_THRESHOLD) if hasattr(settings, 'ATTENDANCE_ALERT_THRESHOLD') else 0.85
    CRITICAL_ALERT_THRESHOLD = 0.70  # 70% - critical alert
    WARNING_ALERT_THRESHOLD = 0.85  # 85% - warning alert
    
    # Report filters
    REPORT_FORMATS = ['json', 'csv', 'excel', 'pdf']
    
    @staticmethod
    def generate_daily_report(report_date: date, class_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate daily attendance report for specified date.
        
        Property 22: Report Generation Completeness
        For any attendance dataset within a specified date range, generated reports
        should include all required information without omissions.
        
        Args:
            report_date: Date for the report
            class_name: Optional class filter
            
        Returns:
            Dictionary containing daily report data
        """
        try:
            # Get all attendance entries for the day
            entries = AttendanceEntry.objects.filter(date=report_date)
            
            if class_name:
                entries = entries.filter(class_name=class_name)
            
            # Calculate statistics
            total_entries = entries.count()
            present = entries.filter(status='present').count()
            absent = entries.filter(status='absent').count()
            checked_in = entries.filter(check_in_time__isnull=False).count()
            checked_out = entries.filter(check_out_time__isnull=False).count()
            early_departures = entries.filter(is_early_departure=True).count()
            
            # Calculate percentages
            present_percentage = (present / total_entries * 100) if total_entries > 0 else 0
            checked_in_percentage = (checked_in / total_entries * 100) if total_entries > 0 else 0
            checked_out_percentage = (checked_out / total_entries * 100) if total_entries > 0 else 0
            
            # Get active students count for context
            total_active = Student.objects.filter(status='active')
            if class_name:
                total_active = total_active.filter(class_name=class_name)
            total_active_count = total_active.count()
            
            # Compile detailed entries
            detailed_entries = []
            for entry in entries.order_by('student__last_name', 'student__first_name'):
                detailed_entries.append({
                    'student_id': entry.student.id,
                    'student_name': entry.student.get_full_name(),
                    'admission_no': entry.student.admission_no,
                    'class': entry.student.class_name,
                    'status': entry.status,
                    'check_in_time': entry.check_in_time.isoformat() if entry.check_in_time else None,
                    'check_out_time': entry.check_out_time.isoformat() if entry.check_out_time else None,
                    'marked_by': entry.marked_by.username if entry.marked_by else None,
                    'parent_name': entry.parent_name,
                    'reason': entry.reason,
                })
            
            report = {
                'report_type': 'daily',
                'report_date': report_date.isoformat(),
                'generated_at': timezone.now().isoformat(),
                'class_filter': class_name,
                'summary': {
                    'total_entries': total_entries,
                    'total_active_students': total_active_count,
                    'present': present,
                    'absent': absent,
                    'checked_in': checked_in,
                    'checked_out': checked_out,
                    'early_departures': early_departures,
                },
                'percentages': {
                    'present_percentage': round(present_percentage, 2),
                    'checked_in_percentage': round(checked_in_percentage, 2),
                    'checked_out_percentage': round(checked_out_percentage, 2),
                },
                'details': detailed_entries,
            }
            
            logger.info(f"Generated daily report for {report_date}: {total_entries} entries")
            return report
            
        except Exception as e:
            logger.error(f"Error generating daily report: {str(e)}")
            raise
    
    @staticmethod
    def generate_monthly_report(year: int, month: int, class_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate monthly attendance summary with presence rates.
        
        Args:
            year: Year for the report
            month: Month for the report (1-12)
            class_name: Optional class filter
            
        Returns:
            Dictionary containing monthly report data
        """
        try:
            from datetime import datetime as dt
            
            # Get date range for month
            start_date = date(year, month, 1)
            if month == 12:
                end_date = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                end_date = date(year, month + 1, 1) - timedelta(days=1)
            
            # Get attendance entries for month
            entries = AttendanceEntry.objects.filter(
                date__gte=start_date,
                date__lte=end_date
            )
            
            if class_name:
                entries = entries.filter(class_name=class_name)
            
            # Group by student
            student_stats = {}
            for entry in entries:
                student_id = entry.student.id
                if student_id not in student_stats:
                    student_stats[student_id] = {
                        'student_name': entry.student.get_full_name(),
                        'admission_no': entry.student.admission_no,
                        'class': entry.student.class_name,
                        'total': 0,
                        'present': 0,
                        'absent': 0,
                        'daily_entries': []
                    }
                
                student_stats[student_id]['total'] += 1
                if entry.status == 'present':
                    student_stats[student_id]['present'] += 1
                else:
                    student_stats[student_id]['absent'] += 1
                
                student_stats[student_id]['daily_entries'].append({
                    'date': entry.date.isoformat(),
                    'status': entry.status,
                })
            
            # Calculate percentages
            summary_list = []
            total_present = 0
            total_entries = 0
            
            for student_id, stats in student_stats.items():
                presence_rate = (stats['present'] / stats['total'] * 100) if stats['total'] > 0 else 0
                total_present += stats['present']
                total_entries += stats['total']
                
                summary_list.append({
                    'student_id': student_id,
                    'student_name': stats['student_name'],
                    'admission_no': stats['admission_no'],
                    'class': stats['class'],
                    'total_days': stats['total'],
                    'present_days': stats['present'],
                    'absent_days': stats['absent'],
                    'presence_rate': round(presence_rate, 2),
                })
            
            # Overall statistics
            overall_presence_rate = (total_present / total_entries * 100) if total_entries > 0 else 0
            
            report = {
                'report_type': 'monthly',
                'year': year,
                'month': month,
                'generated_at': timezone.now().isoformat(),
                'class_filter': class_name,
                'date_range': {
                    'start': start_date.isoformat(),
                    'end': end_date.isoformat(),
                },
                'summary': {
                    'total_records': total_entries,
                    'total_present': total_present,
                    'overall_presence_rate': round(overall_presence_rate, 2),
                },
                'student_summaries': summary_list,
            }
            
            logger.info(f"Generated monthly report for {year}-{month:02d}: {total_entries} entries")
            return report
            
        except Exception as e:
            logger.error(f"Error generating monthly report: {str(e)}")
            raise
    
    @staticmethod
    def generate_class_report(class_name: str, start_date: Optional[date] = None, 
                            end_date: Optional[date] = None) -> Dict[str, Any]:
        """
        Generate class-wise attendance report with detailed statistics.
        
        Args:
            class_name: Class name for the report
            start_date: Optional start date (defaults to today)
            end_date: Optional end date (defaults to today)
            
        Returns:
            Dictionary containing class report data
        """
        try:
            if not start_date:
                start_date = timezone.now().date()
            if not end_date:
                end_date = timezone.now().date()
            
            # Get entries for class
            entries = AttendanceEntry.objects.filter(
                class_name=class_name,
                date__gte=start_date,
                date__lte=end_date
            )
            
            # Get all active students in class
            class_students = Student.objects.filter(
                class_name=class_name,
                status='active'
            )
            
            # Calculate class statistics
            total_students = class_students.count()
            total_records = entries.count()
            present_count = entries.filter(status='present').count()
            absent_count = entries.filter(status='absent').count()
            checked_in_count = entries.filter(check_in_time__isnull=False).count()
            checked_out_count = entries.filter(check_out_time__isnull=False).count()
            
            # Calculate percentages
            present_percentage = (present_count / total_records * 100) if total_records > 0 else 0
            checked_in_percentage = (checked_in_count / total_records * 100) if total_records > 0 else 0
            
            # Get average check-in/out times
            avg_checkin_hour = entries.filter(check_in_time__isnull=False).aggregate(
                avg_hour=Avg('check_in_time__hour')
            )['avg_hour'] or 0
            
            avg_checkout_hour = entries.filter(check_out_time__isnull=False).aggregate(
                avg_hour=Avg('check_out_time__hour')
            )['avg_hour'] or 0
            
            # Student breakdown
            student_details = []
            for student in class_students:
                student_entries = entries.filter(student=student)
                student_present = student_entries.filter(status='present').count()
                student_attendance_rate = (student_present / student_entries.count() * 100) if student_entries.count() > 0 else 0
                
                student_details.append({
                    'student_id': student.id,
                    'student_name': student.get_full_name(),
                    'admission_no': student.admission_no,
                    'total_records': student_entries.count(),
                    'present': student_present,
                    'absent': student_entries.filter(status='absent').count(),
                    'attendance_rate': round(student_attendance_rate, 2),
                })
            
            report = {
                'report_type': 'class_wise',
                'class_name': class_name,
                'generated_at': timezone.now().isoformat(),
                'date_range': {
                    'start': start_date.isoformat(),
                    'end': end_date.isoformat(),
                },
                'summary': {
                    'total_active_students': total_students,
                    'total_records': total_records,
                    'present': present_count,
                    'absent': absent_count,
                    'checked_in': checked_in_count,
                    'checked_out': checked_out_count,
                },
                'statistics': {
                    'present_percentage': round(present_percentage, 2),
                    'checked_in_percentage': round(checked_in_percentage, 2),
                    'avg_checkin_hour': round(avg_checkin_hour, 2),
                    'avg_checkout_hour': round(avg_checkout_hour, 2),
                },
                'student_details': sorted(student_details, key=lambda x: x['student_name']),
            }
            
            logger.info(f"Generated class report for {class_name}: {total_students} students")
            return report
            
        except Exception as e:
            logger.error(f"Error generating class report: {str(e)}")
            raise
    
    @staticmethod
    def generate_parent_pickup_report(start_date: Optional[date] = None, 
                                     end_date: Optional[date] = None) -> Dict[str, Any]:
        """
        Generate report of parent pickups with authorized person tracking.
        
        Args:
            start_date: Optional start date (defaults to today)
            end_date: Optional end date (defaults to today)
            
        Returns:
            Dictionary containing pickup report data
        """
        try:
            if not start_date:
                start_date = timezone.now().date()
            if not end_date:
                end_date = timezone.now().date()
            
            # Get checkout entries with parent information
            entries = AttendanceEntry.objects.filter(
                check_out_time__isnull=False,
                date__gte=start_date,
                date__lte=end_date
            ).select_related('student', 'checkout_by')
            
            # Group by parent/authorized person
            pickup_records = []
            for entry in entries:
                pickup_records.append({
                    'student_id': entry.student.id,
                    'student_name': entry.student.get_full_name(),
                    'admission_no': entry.student.admission_no,
                    'class': entry.student.class_name,
                    'checkout_time': entry.check_out_time.isoformat() if entry.check_out_time else None,
                    'parent_name': entry.parent_name or 'Unknown',
                    'authorized_person': entry.checkout_by.username if entry.checkout_by else 'System',
                    'is_early_departure': entry.is_early_departure,
                })
            
            # Summary statistics
            total_pickups = len(pickup_records)
            early_departures = sum(1 for r in pickup_records if r['is_early_departure'])
            unique_parents = len(set(r['parent_name'] for r in pickup_records))
            unique_students = len(set(r['student_id'] for r in pickup_records))
            
            # Parent summary
            parent_summary = {}
            for record in pickup_records:
                parent = record['parent_name']
                if parent not in parent_summary:
                    parent_summary[parent] = {'count': 0, 'students': set()}
                parent_summary[parent]['count'] += 1
                parent_summary[parent]['students'].add(record['student_name'])
            
            parent_list = [
                {
                    'parent_name': parent,
                    'total_pickups': stats['count'],
                    'unique_students': len(stats['students']),
                }
                for parent, stats in parent_summary.items()
            ]
            
            report = {
                'report_type': 'parent_pickups',
                'generated_at': timezone.now().isoformat(),
                'date_range': {
                    'start': start_date.isoformat(),
                    'end': end_date.isoformat(),
                },
                'summary': {
                    'total_pickups': total_pickups,
                    'unique_parents': unique_parents,
                    'unique_students': unique_students,
                    'early_departures': early_departures,
                    'early_departure_percentage': round((early_departures / total_pickups * 100) if total_pickups > 0 else 0, 2),
                },
                'parent_summary': parent_list,
                'details': pickup_records,
            }
            
            logger.info(f"Generated parent pickup report: {total_pickups} pickups")
            return report
            
        except Exception as e:
            logger.error(f"Error generating parent pickup report: {str(e)}")
            raise
    
    @staticmethod
    def generate_alerts(start_date: Optional[date] = None, 
                       end_date: Optional[date] = None) -> Dict[str, Any]:
        """
        Generate attendance alerts for students below threshold.
        
        Property 23: Alert Generation Accuracy
        For any student with calculated attendance rate below 85%, an attendance alert
        should be generated, while students above 85% should not trigger alerts.
        
        Args:
            start_date: Optional start date for calculation
            end_date: Optional end date for calculation
            
        Returns:
            Dictionary containing alert data
        """
        try:
            if not start_date:
                start_date = timezone.now().date() - timedelta(days=30)
            if not end_date:
                end_date = timezone.now().date()
            
            # Get all active students
            students = Student.objects.filter(status='active')
            
            alerts = {
                'critical': [],
                'warning': [],
                'generated_at': timezone.now().isoformat(),
            }
            
            for student in students:
                # Get attendance entries for date range
                entries = AttendanceEntry.objects.filter(
                    student=student,
                    date__gte=start_date,
                    date__lte=end_date
                )
                
                if entries.count() == 0:
                    continue
                
                # Calculate attendance rate
                present_count = entries.filter(status='present').count()
                attendance_rate = present_count / entries.count()
                
                # Generate alerts based on threshold
                if attendance_rate < ReportingService.CRITICAL_ALERT_THRESHOLD:
                    alerts['critical'].append({
                        'student_id': student.id,
                        'student_name': student.get_full_name(),
                        'admission_no': student.admission_no,
                        'class': student.class_name,
                        'attendance_rate': round(attendance_rate * 100, 2),
                        'threshold': ReportingService.CRITICAL_ALERT_THRESHOLD * 100,
                        'severity': 'critical',
                        'message': f"Critical: {student.get_full_name()} has {round(attendance_rate * 100, 2)}% attendance",
                    })
                elif attendance_rate < ReportingService.WARNING_ALERT_THRESHOLD:
                    alerts['warning'].append({
                        'student_id': student.id,
                        'student_name': student.get_full_name(),
                        'admission_no': student.admission_no,
                        'class': student.class_name,
                        'attendance_rate': round(attendance_rate * 100, 2),
                        'threshold': ReportingService.WARNING_ALERT_THRESHOLD * 100,
                        'severity': 'warning',
                        'message': f"Warning: {student.get_full_name()} has {round(attendance_rate * 100, 2)}% attendance",
                    })
            
            logger.info(f"Generated alerts: {len(alerts['critical'])} critical, {len(alerts['warning'])} warnings")
            return alerts
            
        except Exception as e:
            logger.error(f"Error generating alerts: {str(e)}")
            raise
    
    @staticmethod
    def get_alerts_for_student(student: Student, days: int = 30) -> List[Dict[str, Any]]:
        """
        Get specific alerts for a student.
        
        Args:
            student: Student instance
            days: Number of days to look back
            
        Returns:
            List of alert dictionaries
        """
        try:
            start_date = timezone.now().date() - timedelta(days=days)
            entries = AttendanceEntry.objects.filter(
                student=student,
                date__gte=start_date
            )
            
            if entries.count() == 0:
                return []
            
            present_count = entries.filter(status='present').count()
            attendance_rate = present_count / entries.count()
            
            alerts = []
            
            if attendance_rate < ReportingService.CRITICAL_ALERT_THRESHOLD:
                alerts.append({
                    'type': 'critical',
                    'message': f"Critical attendance alert for {student.get_full_name()}",
                    'attendance_rate': round(attendance_rate * 100, 2),
                })
            elif attendance_rate < ReportingService.WARNING_ALERT_THRESHOLD:
                alerts.append({
                    'type': 'warning',
                    'message': f"Attendance warning for {student.get_full_name()}",
                    'attendance_rate': round(attendance_rate * 100, 2),
                })
            
            return alerts
            
        except Exception as e:
            logger.error(f"Error getting alerts for student {student.id}: {str(e)}")
            return []
    
    @staticmethod
    def analyze_attendance_trends(student: Student, days: int = 90) -> Dict[str, Any]:
        """
        Analyze attendance trends over time period.
        
        Args:
            student: Student instance
            days: Number of days to analyze
            
        Returns:
            Dictionary containing trend analysis
        """
        try:
            start_date = timezone.now().date() - timedelta(days=days)
            entries = AttendanceEntry.objects.filter(
                student=student,
                date__gte=start_date
            ).order_by('date')
            
            if not entries:
                return {'error': 'No attendance data found'}
            
            # Analyze by week
            weekly_stats = {}
            for entry in entries:
                week_start = entry.date - timedelta(days=entry.date.weekday())
                week_key = week_start.isoformat()
                
                if week_key not in weekly_stats:
                    weekly_stats[week_key] = {'present': 0, 'absent': 0, 'total': 0}
                
                weekly_stats[week_key]['total'] += 1
                if entry.status == 'present':
                    weekly_stats[week_key]['present'] += 1
                else:
                    weekly_stats[week_key]['absent'] += 1
            
            # Calculate trends
            weekly_trends = []
            for week_key, stats in sorted(weekly_stats.items()):
                rate = (stats['present'] / stats['total'] * 100) if stats['total'] > 0 else 0
                weekly_trends.append({
                    'week_start': week_key,
                    'present': stats['present'],
                    'absent': stats['absent'],
                    'rate': round(rate, 2),
                })
            
            # Calculate overall trend (improving, declining, stable)
            if len(weekly_trends) > 1:
                first_week_rate = weekly_trends[0]['rate']
                last_week_rate = weekly_trends[-1]['rate']
                trend = 'improving' if last_week_rate > first_week_rate else ('declining' if last_week_rate < first_week_rate else 'stable')
            else:
                trend = 'insufficient_data'
            
            return {
                'student_id': student.id,
                'student_name': student.get_full_name(),
                'period_days': days,
                'weekly_trends': weekly_trends,
                'overall_trend': trend,
                'first_week_rate': weekly_trends[0]['rate'] if weekly_trends else 0,
                'last_week_rate': weekly_trends[-1]['rate'] if weekly_trends else 0,
            }
            
        except Exception as e:
            logger.error(f"Error analyzing attendance trends: {str(e)}")
            return {'error': str(e)}
    
    @staticmethod
    def generate_compliance_report(year: int, month: Optional[int] = None) -> Dict[str, Any]:
        """
        Generate regulatory compliance report.
        
        Args:
            year: Year for the report
            month: Optional specific month
            
        Returns:
            Dictionary containing compliance data
        """
        try:
            # Get attendance data for period
            if month:
                start_date = date(year, month, 1)
                if month == 12:
                    end_date = date(year + 1, 1, 1) - timedelta(days=1)
                else:
                    end_date = date(year, month + 1, 1) - timedelta(days=1)
            else:
                start_date = date(year, 1, 1)
                end_date = date(year, 12, 31)
            
            entries = AttendanceEntry.objects.filter(
                date__gte=start_date,
                date__lte=end_date
            )
            
            # Calculate compliance metrics
            total_records = entries.count()
            fully_recorded = entries.filter(
                check_in_time__isnull=False,
                check_out_time__isnull=False
            ).count()
            recorded_percentage = (fully_recorded / total_records * 100) if total_records > 0 else 0
            
            # Get class compliance
            class_stats = entries.values('class_name').annotate(
                total=Count('id'),
                recorded=Count('id', filter=Q(check_in_time__isnull=False, check_out_time__isnull=False))
            )
            
            report = {
                'report_type': 'compliance',
                'year': year,
                'month': month,
                'date_range': {
                    'start': start_date.isoformat(),
                    'end': end_date.isoformat(),
                },
                'compliance_metrics': {
                    'total_records': total_records,
                    'fully_recorded': fully_recorded,
                    'recorded_percentage': round(recorded_percentage, 2),
                    'compliant': recorded_percentage >= 95,  # 95% compliance target
                },
                'class_compliance': [
                    {
                        'class_name': stat['class_name'],
                        'total_records': stat['total'],
                        'recorded_count': stat['recorded'],
                        'compliance_percentage': round((stat['recorded'] / stat['total'] * 100) if stat['total'] > 0 else 0, 2),
                    }
                    for stat in class_stats
                ],
            }
            
            return report
            
        except Exception as e:
            logger.error(f"Error generating compliance report: {str(e)}")
            raise

    # ── Methods called by reporting_api.py that were missing ──────────────

    @staticmethod
    def generate_attendance_trend_analysis(
        student_id: Optional[int] = None,
        class_name: Optional[str] = None,
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Generate attendance trend analysis over the past N days.

        If *student_id* is given, return weekly trends for that student.
        If *class_name* is given, filter to that class.
        Otherwise, return school-wide daily trends.
        """
        try:
            start_date = timezone.now().date() - timedelta(days=days)
            end_date = timezone.now().date()

            entries = AttendanceEntry.objects.filter(
                date__gte=start_date, date__lte=end_date
            )

            if student_id:
                try:
                    student = Student.objects.get(pk=student_id)
                except Student.DoesNotExist:
                    return {'error': f'Student {student_id} not found'}
                entries = entries.filter(student=student)

            if class_name:
                entries = entries.filter(class_name=class_name)

            # Build daily buckets
            daily_stats: Dict[str, Dict[str, int]] = {}
            for d in range(days + 1):
                day = (start_date + timedelta(days=d)).isoformat()
                daily_stats[day] = {'present': 0, 'absent': 0, 'total': 0}

            for entry in entries:
                day_key = entry.date.isoformat()
                if day_key in daily_stats:
                    daily_stats[day_key]['total'] += 1
                    if entry.status == 'present':
                        daily_stats[day_key]['present'] += 1
                    else:
                        daily_stats[day_key]['absent'] += 1

            daily_trends = []
            for day_key in sorted(daily_stats.keys()):
                stats = daily_stats[day_key]
                rate = (stats['present'] / stats['total'] * 100) if stats['total'] > 0 else None
                daily_trends.append({
                    'date': day_key,
                    'present': stats['present'],
                    'absent': stats['absent'],
                    'total': stats['total'],
                    'rate': round(rate, 2) if rate is not None else None,
                })

            # Overall summary
            total_entries = sum(d['total'] for d in daily_trends)
            total_present = sum(d['present'] for d in daily_trends)
            overall_rate = (total_present / total_entries * 100) if total_entries > 0 else 0

            # Trend direction
            rates = [d['rate'] for d in daily_trends if d['rate'] is not None]
            if len(rates) >= 2:
                mid = len(rates) // 2
                first_half = sum(rates[:mid]) / max(mid, 1)
                second_half = sum(rates[mid:]) / max(len(rates) - mid, 1)
                if second_half > first_half + 2:
                    trend = 'improving'
                elif second_half < first_half - 2:
                    trend = 'declining'
                else:
                    trend = 'stable'
            else:
                trend = 'insufficient_data'

            result = {
                'report_type': 'trend_analysis',
                'period_days': days,
                'student_id': student_id,
                'class_name': class_name,
                'date_range': {
                    'start': start_date.isoformat(),
                    'end': end_date.isoformat(),
                },
                'summary': {
                    'total_records': total_entries,
                    'total_present': total_present,
                    'overall_rate': round(overall_rate, 2),
                    'trend': trend,
                },
                'daily_trends': daily_trends,
            }

            logger.info(f"Generated trend analysis ({days}d, student={student_id}, class={class_name}): {total_entries} records")
            return result

        except Exception as e:
            logger.error(f"Error generating trend analysis: {str(e)}")
            raise

    @staticmethod
    def generate_attendance_alerts(
        threshold: int = 85,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> Dict[str, Any]:
        """
        Generate attendance alerts for students below threshold.
        API-facing method that delegates to generate_alerts().
        """
        try:
            start = date_from or (timezone.now().date() - timedelta(days=30))
            end = date_to or timezone.now().date()

            students = Student.objects.filter(status='active')
            threshold_decimal = threshold / 100.0
            warning_list = []
            critical_list = []

            for student in students:
                entries = AttendanceEntry.objects.filter(
                    student=student, date__gte=start, date__lte=end
                )
                if not entries.exists():
                    continue

                present_count = entries.filter(status='present').count()
                rate = present_count / entries.count()

                alert = {
                    'student_id': student.id,
                    'student_name': student.get_full_name(),
                    'admission_no': student.admission_no,
                    'class': student.class_name,
                    'attendance_rate': round(rate * 100, 2),
                    'threshold': threshold,
                }

                if rate < ReportingService.CRITICAL_ALERT_THRESHOLD:
                    alert['severity'] = 'critical'
                    alert['message'] = f"Critical: {student.get_full_name()} has {round(rate * 100, 2)}% attendance"
                    critical_list.append(alert)
                elif rate < threshold_decimal:
                    alert['severity'] = 'warning'
                    alert['message'] = f"Warning: {student.get_full_name()} has {round(rate * 100, 2)}% attendance"
                    warning_list.append(alert)

            return {
                'report_type': 'attendance_alerts',
                'threshold': threshold,
                'total_alerts': len(warning_list) + len(critical_list),
                'by_alert_level': {
                    'warning': warning_list,
                    'critical': critical_list,
                    'severe': [],
                },
                'generated_at': timezone.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"Error generating attendance alerts: {str(e)}")
            raise

    @staticmethod
    def generate_classwise_report(
        class_name: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> Dict[str, Any]:
        """
        API-facing method that delegates to generate_class_report() or generates
        a multi-class summary when class_name is None.
        """
        if class_name:
            return ReportingService.generate_class_report(
                class_name=class_name,
                start_date=date_from,
                end_date=date_to,
            )

        # No specific class — build a class-level summary
        try:
            if not date_from:
                date_from = timezone.now().date()
            if not date_to:
                date_to = timezone.now().date()

            entries = AttendanceEntry.objects.filter(
                date__gte=date_from, date__lte=date_to
            )
            class_stats = (
                entries.values('class_name')
                .annotate(
                    total=Count('id'),
                    present=Count('id', filter=Q(status='present')),
                    absent=Count('id', filter=Q(status='absent')),
                )
                .order_by('class_name')
            )

            classes = []
            total_records = 0
            total_present = 0
            for stat in class_stats:
                t = stat['total']
                p = stat['present']
                total_records += t
                total_present += p
                classes.append({
                    'class_name': stat['class_name'],
                    'total_records': t,
                    'present': p,
                    'absent': stat['absent'],
                    'present_percentage': round((p / t * 100) if t > 0 else 0, 2),
                })

            overall = (total_present / total_records * 100) if total_records > 0 else 0

            return {
                'report_type': 'class_wise',
                'generated_at': timezone.now().isoformat(),
                'date_range': {'start': date_from.isoformat(), 'end': date_to.isoformat()},
                'summary': {
                    'total_classes': len(classes),
                    'total_records': total_records,
                    'overall_present_percentage': round(overall, 2),
                },
                'classes': classes,
            }
        except Exception as e:
            logger.error(f"Error generating classwise report: {str(e)}")
            raise

    @staticmethod
    def generate_regulatory_compliance_report(
        year: int, month: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        API-facing alias wrapping generate_compliance_report().
        """
        return ReportingService.generate_compliance_report(year=year, month=month)
