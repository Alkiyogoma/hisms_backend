"""
Management command: send_assessment_reminders
FRD FR-ADM-015 — Send a reminder to the parent 2 days before the scheduled assessment date.
Run daily via cron/Celery beat.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta


class Command(BaseCommand):
    help = "Send 2-day assessment reminders to parents (FR-ADM-015)"

    def handle(self, *args, **options):
        from admissions.models import Applicant, ApplicantStatus, AssessmentSchedule
        from communications.email_service import dispatch_notification
        from audit.models import log_event

        target_date = timezone.localdate() + timedelta(days=2)

        # Find all assessments scheduled in exactly 2 days
        # FR-ADM-015: Send reminder regardless of logistics sent status
        schedules = AssessmentSchedule.objects.filter(
            scheduled_date=target_date,
        ).select_related("applicant")

        sent = 0
        for schedule in schedules:
            applicant = schedule.applicant

            # Skip if already completed/enrolled/denied
            if applicant.status in [
                ApplicantStatus.ASSESSMENT_COMPLETED,
                ApplicantStatus.HOS_REVIEW,
                ApplicantStatus.HOS_DECISION,
                ApplicantStatus.ADMITTED,
                ApplicantStatus.ENROLLED,
                ApplicantStatus.DENIED,
                ApplicantStatus.WITHDRAWN,
                ApplicantStatus.MEETING_COMPLETED,
                ApplicantStatus.DECLINED_AT_MEETING,
                ApplicantStatus.REPORT_PENDING,
                ApplicantStatus.ASSESSMENT_FAILED,
                ApplicantStatus.FORM_SUBMITTED,
                ApplicantStatus.INVOICE_GENERATED,
                ApplicantStatus.INVOICE_PAID,
                ApplicantStatus.FLAGGED_FOR_REVIEW,
            ]:
                continue

            parent_phone = (applicant.parent_phone or "").strip()
            parent_email = (applicant.parent_email or "").strip()
            if not parent_email and not parent_phone:
                continue

            from admissions.services import ASSESSMENT_BRING_CHECKLIST
            from core.models import SchoolSettings
            _school_name = SchoolSettings.get_settings().school_name or "the school"
            msg = (
                f"Dear {applicant.parent_full_name},\n\n"
                f"This is a reminder that the assessment for {applicant.child_full_name} "
                f"is scheduled in 2 days.\n\n"
                f" Date: {schedule.scheduled_date.strftime('%A, %d %B %Y')}\n"
                f" Time: {schedule.scheduled_time.strftime('%I:%M %p')}\n"
                f" Location: {schedule.location}\n"
                f" Teacher: {schedule.facilitating_teacher_name}"
                f"{ASSESSMENT_BRING_CHECKLIST}\n"
                "Please ensure your child is rested and ready. "
                "If you need to reschedule, contact the school immediately.\n\n"
                f"Warm regards,\n{_school_name}"
            )

            dispatch_notification(
                user=None,
                title=f"Assessment Reminder — {applicant.child_full_name} in 2 Days",
                message=msg,
                actor=None,
                external_email=parent_email or None,
                phone=parent_phone or None,
            )

            sent += 1
            contact = parent_email or parent_phone
            log_event(
                actor=None,
                action_type="ASSESSMENT_REMINDER_SENT",
                model_name="AssessmentSchedule",
                object_id=schedule.pk,
                description=f"2-day assessment reminder sent to {contact} for {applicant.child_full_name}",
            )

        self.stdout.write(self.style.SUCCESS(f"Assessment reminders sent: {sent}"))
