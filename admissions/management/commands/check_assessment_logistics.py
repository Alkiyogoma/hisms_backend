from django.core.management.base import BaseCommand
from django.utils import timezone
from admissions.models import AssessmentSchedule
from communications.email_service import dispatch_notification
from users.models import User, UserRole

class Command(BaseCommand):
    help = "Check for assessments where logistics have not been sent"

    def handle(self, *args, **options):
        today = timezone.now().date()
        # NOTIF-03: Assessment scheduled but logistics NOT yet sent
        pending = AssessmentSchedule.objects.filter(
            assessment_fee_confirmed_paid=True,
            logistics_sent_at__isnull=True,
            scheduled_date__gte=today
        ).select_related('applicant')
        
        admins = User.objects.filter(role=UserRole.ADMIN_OFFICER, is_active=True)
        
        count = 0
        for assessment in pending:
            applicant = assessment.applicant
            msg = f"Logistics not yet sent for {applicant.child_full_name} assessment on {assessment.scheduled_date}. Please send assessment details to the parent."
            for admin in admins:
                dispatch_notification(
                    user=admin,
                    title="Action Required: Assessment Logistics",
                    message=msg,
                    link=f"/admissions/{applicant.id}/",
                    actor=None
                )
            count += 1
        
        self.stdout.write(self.style.SUCCESS(f"Processed {count} pending logistics alerts."))
