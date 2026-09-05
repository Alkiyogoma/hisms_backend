"""
Celery Tasks for Admissions module.

Periodic background tasks for:
- Sending 2-day assessment reminders
- Flagging expired admission offers (14-day window)
- E07: Assessment fee reminders
- E11: Report outstanding reminders
- E16: Form submission reminders
- E19: Invoice payment reminders
"""

from celery import shared_task
from django.core.management import call_command
from django.utils import timezone
from datetime import timedelta
import io
import logging

logger = logging.getLogger(__name__)


@shared_task
def send_assessment_reminders_task():
    """
    ADM-023: Send assessment reminders 2 days before scheduled assessments.

    Runs daily at 08:30 EAT via Celery beat.
    Delegates to the ``send_assessment_reminders`` management command
    so the logic is reusable from CLI and from the periodic task.
    """
    out = io.StringIO()
    try:
        call_command("send_assessment_reminders", stdout=out)
        output = out.getvalue().strip()
        logger.info("send_assessment_reminders_task: %s", output)
        return output
    except Exception as exc:
        logger.error("send_assessment_reminders_task failed: %s", exc)
        raise


@shared_task
def flag_expired_offers_task():
    """
    Flag admitted/conditional applicants past the 14-day offer window.

    Runs daily at 07:00 EAT via Celery beat.
    Moves applicants from ADMITTED/CONDITIONAL to FLAGGED_FOR_REVIEW
    if they haven't been enrolled within 14 days of the status change.
    """
    from admissions.models import Applicant, ApplicantStatus
    from admissions.services import transition_applicant_status

    cutoff = timezone.now() - timezone.timedelta(days=14)
    expired = Applicant.objects.filter(
        status__in=[ApplicantStatus.ADMITTED, ApplicantStatus.CONDITIONAL],
        updated_at__lt=cutoff,
    )

    count = 0
    for applicant in expired:
        try:
            transition_applicant_status(
                applicant=applicant,
                to_status=ApplicantStatus.FLAGGED_FOR_REVIEW,
                actor=None,
                reason="Offer expired — 14-day window passed without enrolment.",
            )
            count += 1
            logger.info("Flagged expired offer for applicant %s (%s)", applicant.pk, applicant.child_full_name)

            # S06: Invoice payment reminder SMS to parent
            try:
                from communications.email_service import dispatch_notification
                from core.models import SchoolSettings
                parent_phone = (applicant.parent_phone or "").strip() or None
                if parent_phone:
                    school_name = SchoolSettings.get_settings().school_name or "the school"
                    sms_msg = (
                        f"Dear {applicant.parent_full_name}, this is a reminder that the admission invoice "
                        f"for {applicant.child_full_name} is still outstanding. "
                        f"Ref: {applicant.reference_number}. "
                        f"Please complete payment to finalise enrolment. Call us for assistance."
                    )
                    dispatch_notification(
                        user=None,
                        title="Invoice Payment Reminder",
                        message=sms_msg,
                        link=None,
                        actor=None,
                        external_email=None,
                        phone=parent_phone,
                    )
            except Exception:
                pass

        except Exception as exc:
            logger.error("Failed to flag applicant %s: %s", applicant.pk, exc)

    return f"Flagged {count} expired offers"


@shared_task
def send_admission_fee_reminders_task():
    """E07: Send assessment fee reminders for applicants at assessment_pending status > 3 days."""
    from admissions.models import Applicant, ApplicantStatus
    from core.email_templates import send_dynamic_email
    from core.models import SchoolSettings

    cutoff = timezone.now() - timedelta(days=3)
    applicants = Applicant.objects.filter(
        status=ApplicantStatus.ASSESSMENT_PENDING,
        updated_at__lte=cutoff,
    ).select_related()

    settings = SchoolSettings.get_settings()
    contact = settings.get_admissions_contact()

    for app in applicants:
        if hasattr(app, 'assessment') and app.assessment and app.assessment.assessment_fee_confirmed_paid:
            continue

        send_dynamic_email(
            template_type="admission_assessment_fee_reminder",
            to_email=app.parent_email,
            context={
                "parent_name": app.parent_full_name,
                "learner_name": app.child_full_name,
                "reference_number": app.reference_number,
                "assessment_date": str(app.assessment.scheduled_date) if app.assessment else "TBD",
                "assessment_fee": f"TSh {app.assessment.assessment_fee_amount:,.0f}" if app.assessment else "TSh 50,000",
                "finance_email": contact.get("email", ""),
                "admissions_phone": contact.get("phone", ""),
                "school_name": settings.school_name or "the school",
            }
        )

    return f"Sent fee reminders for {applicants.count()} applicants"


@shared_task
def send_report_reminders_task():
    """E11: Remind assessing teacher to submit report."""
    from admissions.models import Applicant, ApplicantStatus
    from core.email_templates import send_dynamic_email
    from users.models import User, UserRole

    cutoff = timezone.now() - timedelta(hours=48)
    applicants = Applicant.objects.filter(
        status=ApplicantStatus.ASSESSMENT_COMPLETED,
        updated_at__lte=cutoff,
    ).select_related()

    for app in applicants:
        assessment = getattr(app, 'assessment', None)
        if not assessment or not assessment.facilitating_teacher_name:
            continue

        teacher_email = ""
        teacher_users = User.objects.filter(
            is_active=True,
            role__in=[UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD],
        )
        for tu in teacher_users:
            if f"{tu.first_name} {tu.last_name}".strip().lower() == assessment.facilitating_teacher_name.strip().lower():
                teacher_email = tu.email
                break

        if not teacher_email:
            continue

        send_dynamic_email(
            template_type="admission_report_reminder",
            to_email=teacher_email,
            context={
                "teacher_name": assessment.facilitating_teacher_name,
                "learner_name": app.child_full_name,
                "intended_grade": app.grade_applying_for,
                "assessment_date": str(assessment.scheduled_date),
                "reference_number": app.reference_number,
                "report_link": f"/admissions/applicant/{app.pk}/",
            }
        )

    return f"Sent report reminders for {applicants.count()} applicants"


@shared_task
def send_form_reminders_task():
    """E16: Remind parent to complete admission form."""
    from admissions.models import Applicant, ApplicantStatus
    from core.email_templates import send_dynamic_email
    from communications.email_service import dispatch_notification
    from core.models import SchoolSettings

    settings = SchoolSettings.get_settings()
    contact = settings.get_admissions_contact()

    for days in [7, 12]:
        cutoff = timezone.now() - timedelta(days=days)
        applicants = Applicant.objects.filter(
            status__in=[ApplicantStatus.ADMITTED, ApplicantStatus.CONDITIONAL],
            updated_at__lte=cutoff,
            updated_at__gt=cutoff - timedelta(days=1),
        )
        for app in applicants:
            send_dynamic_email(
                template_type="admission_form_reminder",
                to_email=app.parent_email,
                context={
                    "parent_name": app.parent_full_name,
                    "learner_name": app.child_full_name,
                    "reference_number": app.reference_number,
                    "offer_expiry_date": "14 days from offer",
                    "portal_link": "/admissions/inquire/",
                    "admissions_phone": contact.get("phone", ""),
                }
            )
            phone = (app.parent_phone or "").strip() or None
            if phone:
                dispatch_notification(
                    user=None, title="Form Reminder",
                    message=f"Hodari: Reminder — please complete the admission form for {app.child_full_name}. Ref {app.reference_number}.",
                    link=None, actor=None, external_email=None, phone=phone,
                )

    return "Sent form reminders"


@shared_task
def send_admission_invoice_reminders_task():
    """E19: Remind parent about outstanding admission invoice."""
    from admissions.models import Applicant, ApplicantStatus
    from finance.models import Invoice, InvoiceStatus
    from core.email_templates import send_dynamic_email
    from communications.email_service import dispatch_notification
    from core.models import SchoolSettings

    settings = SchoolSettings.get_settings()
    contact = settings.get_admissions_contact()

    for days in [7, 14, 21]:
        cutoff = timezone.now() - timedelta(days=days)
        applicants = Applicant.objects.filter(
            status=ApplicantStatus.INVOICE_GENERATED,
            updated_at__lte=cutoff,
            updated_at__gt=cutoff - timedelta(days=1),
        )
        for app in applicants:
            invoice = Invoice.objects.filter(applicant=app, invoice_number__startswith="ADM-").first()
            if not invoice:
                continue

            send_dynamic_email(
                template_type="admission_invoice_reminder",
                to_email=app.parent_email,
                context={
                    "parent_name": app.parent_full_name,
                    "learner_name": app.child_full_name,
                    "reference_number": app.reference_number,
                    "invoice_number": invoice.invoice_number,
                    "invoice_amount": f"TSh {invoice.amount_due:,.0f}",
                    "portal_link": "/admissions/inquire/",
                    "finance_phone": contact.get("phone", ""),
                }
            )
            phone = (app.parent_phone or "").strip() or None
            if phone:
                dispatch_notification(
                    user=None, title="Invoice Reminder",
                    message=f"Hodari: Admission invoice {invoice.invoice_number} for {app.child_full_name} is outstanding. View it in the parent portal. Ref {app.reference_number}.",
                    link=None, actor=None, external_email=None, phone=phone,
                )

    return "Sent invoice reminders"


@shared_task
def send_meeting_reminders_task():
    """Meeting reminders: 48h and 24h before scheduled meetings."""
    from admissions.models import Applicant, ApplicantStatus, MeetingSchedule
    from core.email_templates import send_dynamic_email
    from communications.email_service import dispatch_notification
    from core.models import SchoolSettings

    settings = SchoolSettings.get_settings()
    contact = settings.get_admissions_contact()
    now = timezone.now()

    for hours, template_type in [(48, "admission_meeting_reminder_48h"), (24, "admission_meeting_reminder_24h")]:
        window_start = now + timedelta(hours=hours - 1)
        window_end = now + timedelta(hours=hours + 1)
        meetings = MeetingSchedule.objects.filter(
            meeting_date__gte=window_start.date(),
            meeting_date__lte=window_end.date(),
            applicant__status=ApplicantStatus.MEETING_SCHEDULED,
        ).select_related("applicant")

        for meeting in meetings:
            app = meeting.applicant
            parent_email = (app.parent_email or "").strip() or None
            parent_phone = (app.parent_phone or "").strip() or None
            if not parent_email and not parent_phone:
                continue
            day_str = meeting.meeting_date.strftime("%A, %d %B %Y")
            time_str = meeting.meeting_time.strftime("%I:%M %p").lstrip("0")
            ctx = {
                "parent_name": app.parent_full_name,
                "child_name": app.child_full_name,
                "meeting_date": day_str,
                "meeting_time": time_str,
                "reference_number": app.reference_number,
                "admissions_phone": contact.get("phone", ""),
                "school_name": settings.school_name or "the school",
            }
            if parent_email:
                send_dynamic_email(template_type=template_type, to_email=parent_email, context=ctx)
            if parent_phone:
                if hours == 24:
                    # S03: Meeting reminder 24h (spec copy)
                    sms_msg = f"Hodari: Reminder — your meeting with the Head of School is tomorrow at {time_str}. Ref {app.reference_number}."
                else:
                    # 48h reminder
                    sms_msg = f"Hodari: Reminder — your meeting with the Head of School is in 2 days on {day_str} at {time_str}. Ref {app.reference_number}."
                dispatch_notification(
                    user=None, title="Meeting Reminder",
                    message=sms_msg,
                    link=None, actor=None, external_email=None, phone=parent_phone,
                )

    return "Sent meeting reminders"


@shared_task
def send_assessment_24h_reminders_task():
    """Assessment reminders 48h and 24h before scheduled assessments."""
    from admissions.models import Applicant, ApplicantStatus, AssessmentSchedule
    from core.email_templates import send_dynamic_email
    from communications.email_service import dispatch_notification
    from core.models import SchoolSettings

    settings = SchoolSettings.get_settings()
    contact = settings.get_admissions_contact()
    now = timezone.now()

    for hours, template_type in [(48, "admission_assessment_reminder"), (24, "admission_assessment_reminder")]:
        window_start = now + timedelta(hours=hours - 1)
        window_end = now + timedelta(hours=hours + 1)

        assessments = AssessmentSchedule.objects.filter(
            scheduled_date__gte=window_start.date(),
            scheduled_date__lte=window_end.date(),
            applicant__status__in=[
                ApplicantStatus.ASSESSMENT_FEE_PAID,
                ApplicantStatus.ASSESSMENT_CONFIRMED,
            ],
        ).select_related("applicant")

        for assessment in assessments:
            app = assessment.applicant
            parent_email = (app.parent_email or "").strip() or None
            parent_phone = (app.parent_phone or "").strip() or None
            if not parent_email and not parent_phone:
                continue
            ctx = {
                "parent_name": app.parent_full_name,
                "child_name": app.child_full_name,
                "assessment_date": str(assessment.scheduled_date),
                "assessment_time": str(assessment.scheduled_time),
                "assessment_location": assessment.location,
                "ref": app.reference_number,
                "reference_number": app.reference_number,
                "school_name": settings.school_name or "the school",
            }
            if parent_email:
                send_dynamic_email(
                    template_type=template_type,
                    to_email=parent_email,
                    context=ctx,
                )
            if parent_phone:
                if hours == 24:
                    # S04: Assessment reminder 24h (spec copy)
                    sms_msg = f"Hodari: Reminder — {app.child_full_name}'s assessment is tomorrow, arrive {assessment.scheduled_time}. Ref {app.reference_number}."
                else:
                    # 48h reminder
                    sms_msg = f"Hodari: Reminder — {app.child_full_name}'s assessment is in 2 days on {assessment.scheduled_date}, arrive {assessment.scheduled_time}. Ref {app.reference_number}."
                dispatch_notification(
                    user=None, title="Assessment Reminder",
                    message=sms_msg,
                    link=None, actor=None, external_email=None, phone=parent_phone,
                )

    return "Sent 24h assessment reminders"


@shared_task
def send_hos_review_reminders_task():
    """E13: HOS review reminders every 72h for applicants at HOS_REVIEW status."""
    from admissions.models import Applicant, ApplicantStatus
    from core.email_templates import send_dynamic_email
    from users.models import User, UserRole
    from core.models import SchoolSettings

    settings = SchoolSettings.get_settings()
    cutoff = timezone.now() - timedelta(hours=72)
    applicants = Applicant.objects.filter(
        status=ApplicantStatus.HOS_REVIEW,
        updated_at__lte=cutoff,
    ).select_related()

    hos_users = User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True)
    review_link_base = "/admissions/applicant/"

    for app in applicants:
        for hos in hos_users:
            if hos.email:
                send_dynamic_email(
                    template_type="admission_hos_review_reminder",
                    to_email=hos.email,
                    context={
                        "hos_name": hos.get_full_name() or "Head of School",
                        "child_name": app.child_full_name,
                        "grade": app.grade_applying_for,
                        "reference_number": app.reference_number,
                        "review_link": f"{review_link_base}{app.pk}/",
                        "school_name": settings.school_name or "the school",
                    },
                )

    return f"Sent HOS review reminders for {applicants.count()} applicants"


@shared_task
def send_form_outstanding_reminder_task():
    """E17: Day 14 form outstanding → admissions notification (in-app + email)."""
    from admissions.models import Applicant, ApplicantStatus
    from communications.email_service import dispatch_notification, send_email_safe
    from users.models import User, UserRole
    from core.models import SchoolSettings

    settings = SchoolSettings.get_settings()
    contact = settings.get_admissions_contact()

    cutoff = timezone.now() - timedelta(days=14)
    applicants = Applicant.objects.filter(
        status__in=[ApplicantStatus.ADMITTED, ApplicantStatus.CONDITIONAL],
        updated_at__lte=cutoff,
        updated_at__gt=cutoff - timedelta(days=1),
    ).select_related()

    ao_users = User.objects.filter(role=UserRole.ADMIN_OFFICER, is_active=True)
    for app in applicants:
        e17_context = {
            "child_name": app.child_full_name,
            "grade": app.grade_applying_for,
            "parent_name": app.parent_full_name,
            "parent_phone": app.parent_phone or "not provided",
            "reference_number": app.reference_number,
            "application_link": f"/admissions/applicant/{app.pk}/",
            "school_name": settings.school_name or "Hodari Christian School",
        }
        from core.email_templates import send_dynamic_email
        for ao in ao_users:
            dispatch_notification(
                user=ao,
                title="Admission Form Outstanding (14 days)",
                message=(
                    f"The admission form for {app.child_full_name} ({app.reference_number}) "
                    f"has not been completed 14 days after the offer was sent."
                ),
                link=f"/admissions/applicant/{app.pk}/",
                actor=None,
            )
            if ao.email:
                db_sent = send_dynamic_email(
                    template_type="admission_form_outstanding",
                    to_email=ao.email,
                    context=e17_context,
                )
                if not db_sent:
                    send_email_safe(
                        to_email=ao.email,
                        subject=f"Form outstanding: {app.child_full_name} — {app.reference_number}",
                        body=(
                            f"The admission form for {app.child_full_name} ({app.reference_number}) "
                            f"has not been completed 14 days after the offer was sent.\n\n"
                            f"Learner: {app.child_full_name}\n"
                            f"Grade: {app.grade_applying_for}\n"
                            f"Parent: {app.parent_full_name}\n"
                            f"Phone: {app.parent_phone or 'not provided'}\n\n"
                            f"View the application: /admissions/applicant/{app.pk}/"
                        ),
                    )

    return f"Sent form outstanding notifications for {applicants.count()} applicants"


@shared_task
def send_invoice_30day_reminder_task():
    """E20: Day 30 invoice → admissions + finance notification."""
    from admissions.models import Applicant, ApplicantStatus
    from finance.models import Invoice
    from communications.email_service import dispatch_notification
    from users.models import User, UserRole

    cutoff = timezone.now() - timedelta(days=30)
    applicants = Applicant.objects.filter(
        status=ApplicantStatus.INVOICE_GENERATED,
        updated_at__lte=cutoff,
    ).select_related()

    ao_users = User.objects.filter(role=UserRole.ADMIN_OFFICER, is_active=True)
    fo_users = User.objects.filter(role=UserRole.FINANCE_OFFICER, is_active=True)
    all_staff = list(ao_users) + list(fo_users)

    for app in applicants:
        invoice = Invoice.objects.filter(applicant=app, invoice_number__startswith="ADM-").first()
        inv_str = f" ({invoice.invoice_number})" if invoice else ""
        e20_context = {
            "child_name": app.child_full_name,
            "grade": app.grade_applying_for,
            "parent_name": app.parent_full_name,
            "parent_phone": app.parent_phone or "not provided",
            "invoice_number": invoice.invoice_number if invoice else "N/A",
            "reference_number": app.reference_number,
            "application_link": f"/admissions/applicant/{app.pk}/",
            "school_name": "Hodari Christian School",
        }
        from core.email_templates import send_dynamic_email
        for staff in all_staff:
            dispatch_notification(
                user=staff,
                title="Invoice Outstanding (30 days)",
                message=(
                    f"The admission invoice{inv_str} for {app.child_full_name} "
                    f"({app.reference_number}) has been outstanding for 30 days."
                ),
                link=f"/admissions/applicant/{app.pk}/",
                actor=None,
            )
            if staff.email:
                db_sent = send_dynamic_email(
                    template_type="admission_invoice_outstanding_30d",
                    to_email=staff.email,
                    context=e20_context,
                )
                if not db_sent:
                    send_email_safe(
                        to_email=staff.email,
                        subject=f"Invoice still outstanding: {app.child_full_name} — {app.reference_number}",
                        body=(
                            f"This is an automatic reminder that the following admission invoice is now 30 days overdue.\n\n"
                            f"Learner: {app.child_full_name}\n"
                            f"Grade: {app.grade_applying_for}\n"
                            f"Parent: {app.parent_full_name}\n"
                            f"Invoice: {invoice.invoice_number if invoice else 'N/A'}\n"
                            f"Reference: {app.reference_number}\n\n"
                            f"Call parent: {app.parent_phone or 'not provided'}\n"
                            f"View the application: /admissions/applicant/{app.pk}/"
                        ),
                    )

    return f"Sent 30-day invoice reminders for {applicants.count()} applicants"


@shared_task
def send_payment_receipt_task(invoice_id):
    """E20-RECEIPT: Send payment receipt email after fee is cleared."""
    from finance.models import Invoice
    from core.email_templates import send_dynamic_email
    from core.models import SchoolSettings

    try:
        invoice = Invoice.objects.select_related("applicant").get(pk=invoice_id)
    except Invoice.DoesNotExist:
        return "Invoice not found"

    app = invoice.applicant
    if not app:
        return "No applicant linked"

    parent_email = (app.parent_email or "").strip() or None
    if not parent_email:
        return "No parent email"

    settings = SchoolSettings.get_settings()
    send_dynamic_email(
        template_type="admission_payment_receipt",
        to_email=parent_email,
        context={
            "parent_name": app.parent_full_name,
            "child_name": app.child_full_name,
            "reference_number": app.reference_number,
            "amount_paid": f"TZS {invoice.amount_due:,.0f}",
            "payment_method": "Bank Transfer / Mobile Money",
            "school_name": settings.school_name or "the school",
        },
    )

    return f"Receipt sent for invoice {invoice.invoice_number}"


@shared_task
def send_target_enrollment_reminders_task():
    """
    Send reminders to staff when an applicant's target enrollment month is approaching.

    Runs monthly on the 1st at 09:00 EAT. Flags applicants whose target enrollment
    is within the next 3 months but still at an early pipeline stage (inquiry_received,
    meeting_scheduled, meeting_completed).
    """
    from admissions.models import Applicant, ApplicantStatus
    from core.email_templates import send_dynamic_email
    from core.models import SchoolSettings
    from users.models import User, UserRole

    now = timezone.now()
    current_year = now.year
    current_month = now.month

    upcoming_months = []
    for offset in range(1, 4):
        m = current_month + offset
        y = current_year
        if m > 12:
            m -= 12
            y += 1
        upcoming_months.append((y, m))

    early_stages = [
        ApplicantStatus.INQUIRY_RECEIVED,
        ApplicantStatus.MEETING_SCHEDULED,
        ApplicantStatus.MEETING_COMPLETED,
    ]

    applicants = Applicant.objects.filter(
        target_enrollment_year__isnull=False,
        target_enrollment_month__isnull=False,
        status__in=early_stages,
    )

    reminded = 0
    settings = SchoolSettings.get_settings()
    admin_officers = User.objects.filter(role=UserRole.ADMIN_OFFICER, is_active=True)

    for app in applicants:
        target = (app.target_enrollment_year, app.target_enrollment_month)
        if target not in upcoming_months:
            continue

        months_left = (target[0] - current_year) * 12 + (target[1] - current_month)
        month_label = dict(Applicant._meta.get_field("target_enrollment_month").choices).get(app.target_enrollment_month, str(app.target_enrollment_month))

        for ao in admin_officers:
            send_dynamic_email(
                template_type="admission_form_reminder",
                to_email=ao.email,
                context={
                    "parent_name": app.parent_full_name,
                    "child_name": app.child_full_name,
                    "reference_number": app.reference_number,
                    "grade": app.grade_applying_for,
                    "target_date": f"{month_label} {app.target_enrollment_year}",
                    "months_left": months_left,
                    "current_status": app.get_status_display(),
                    "school_name": settings.school_name or "Hodari Christian School",
                    "portal_link": f"/admissions/applicant/{app.pk}/",
                },
            )
        reminded += 1

    return f"Sent enrollment reminders for {reminded} applicants with upcoming target dates"
