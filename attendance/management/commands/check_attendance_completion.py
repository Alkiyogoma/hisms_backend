from django.core.management.base import BaseCommand
from django.utils import timezone
from academics.models import GradeClass
from attendance.models import AttendanceEntry
from users.models import User, UserRole
from communications.email_service import dispatch_notification

class Command(BaseCommand):
    help = "Alert HODs if attendance is not taken by 9:00 AM"

    def handle(self, *args, **options):
        today = timezone.now().date()
        
        # In production, this would be triggered by a cron job at exactly 9:00 AM.
        # For this audit, we check if attendance is missing for any class today.
        
        classes = GradeClass.objects.all()
        missing_classes = []
        for gc in classes:
            if not AttendanceEntry.objects.filter(date=today, student__class_name=gc.name).exists():
                missing_classes.append(gc.name)
        
        if missing_classes:
            hods = User.objects.filter(role__in=[UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL], is_active=True)
            for hod in hods:
                dispatch_notification(
                    user=hod,
                    title="Missing Attendance Alert",
                    message=f"Attendance has not been recorded for the following classes: {', '.join(missing_classes)}",
                    link="/attendance/dashboard/",
                    actor=None
                )
            self.stdout.write(self.style.WARNING(f"Alerted HODs about missing attendance for {len(missing_classes)} classes."))
