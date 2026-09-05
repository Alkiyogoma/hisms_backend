from django.core.management.base import BaseCommand
from django.utils import timezone
from attendance.models import AttendanceEntry, AttendanceStatus
from communications.email_service import send_parent_notification
from students.models import ParentGuardian

class Command(BaseCommand):
    help = "Send notifications to parents of absent students"

    def handle(self, *args, **options):
        today = timezone.now().date()
        absent_entries = AttendanceEntry.objects.filter(
            date=today, 
            status=AttendanceStatus.ABSENT
        )
        
        count = 0
        for entry in absent_entries:
            guardians = ParentGuardian.objects.filter(studentguardian__student=entry.student, studentguardian__is_primary=True)
            for guardian in guardians:
                send_parent_notification(
                    guardian=guardian,
                    title="Student Absence Notification",
                    message=f"Dear Parent, {entry.student.first_name} has been marked absent today ({today}). Please let us know if this is expected.",
                    link="/attendance/parent/",
                    actor=None
                )
                count += 1
        self.stdout.write(self.style.SUCCESS(f"Sent {count} absentee notifications."))
