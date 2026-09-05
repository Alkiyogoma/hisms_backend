"""
Task Generation Service — auto-creates role-based tasks from module events.

Each generate_* function checks for existing pending tasks to avoid duplicates.
All tasks link back to their source object via GenericForeignKey (content_type + object_id).
"""

from datetime import timedelta
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q

from users.models import User, UserRole
from .models import Task, TASK_TYPE_CHOICES


#  Helpers 

def _next_friday_4pm():
    """Return next Friday at 16:00 (lesson plan deadline)."""
    now = timezone.now()
    days_until_friday = (4 - now.weekday()) % 7
    if days_until_friday == 0 and now.hour >= 16:
        days_until_friday = 7
    from datetime import time
    return timezone.make_aware(
        timezone.datetime.combine(now.date() + timedelta(days=days_until_friday), time(16, 0))
    )


def _friday_of_week(week_start_date):
    """Return Friday 4PM of the given week."""
    from datetime import time, date as date_cls
    if isinstance(week_start_date, date_cls):
        fri = week_start_date + timedelta(days=4)
    else:
        fri = week_start_date.date() + timedelta(days=4)
    return timezone.make_aware(timezone.datetime.combine(fri, time(16, 0)))


def _due_tomorrow():
    return timezone.now() + timedelta(days=1, hours=8)


def _due_this_week():
    return timezone.now() + timedelta(days=5)


def _due_end_of_term():
    from academics.utils import get_current_term
    term = get_current_term()
    if term and term.end_date:
        from datetime import time
        return timezone.make_aware(timezone.datetime.combine(term.end_date, time(17, 0)))
    return _due_this_week()


def _task_exists(task_type, assigned_to, content_type=None, object_id=None, status_in=None):
    """Check if a matching pending task already exists to avoid duplicates."""
    if status_in is None:
        status_in = ['pending', 'in_progress']
    qs = Task.objects.filter(
        task_type=task_type,
        assigned_to=assigned_to,
        status__in=status_in,
    )
    if content_type and object_id:
        qs = qs.filter(content_type=content_type, object_id=object_id)
    return qs.exists()


def _create_task(**kwargs):
    """Create a task if it doesn't already exist (dedup check)."""
    task_type = kwargs.get('task_type')
    assigned_to = kwargs.get('assigned_to')
    content_type = kwargs.get('content_type')
    object_id = kwargs.get('object_id')

    if _task_exists(task_type, assigned_to, content_type, object_id):
        return None

    task, created = Task.objects.get_or_create(
        task_type=task_type,
        assigned_to=assigned_to,
        content_type=content_type,
        object_id=object_id,
        defaults={
            k: v for k, v in kwargs.items()
            if k not in ('task_type', 'assigned_to', 'content_type', 'object_id')
        }
    )

    # Notify the assigned user when a new task is created
    if created and assigned_to:
        from communications.email_service import dispatch_notification
        title = f"New Task: {task.get_task_type_display()}"
        priority_tag = f" [{task.priority.upper()}]" if hasattr(task, 'priority') and task.priority else ""
        message = f"You have been assigned a new task: {task.title or task.get_task_type_display()}{priority_tag}."
        if task.due_date:
            message += f" Due: {task.due_date.strftime('%d %b %Y')}."
        dispatch_notification(
            user=assigned_to,
            title=title,
            message=message,
            link="/tasks/dashboard/",
        )

    return task if created else None


#  Academic Tasks 

def generate_lesson_plan_review_tasks(lesson_plan):
    """When a teacher submits a lesson plan, create review tasks for HODs."""
    from academics.models import GradeClass, Department

    content_type = ContentType.objects.get_for_model(lesson_plan.__class__)

    # Determine department from class name
    gc = GradeClass.objects.filter(name=lesson_plan.class_name).first()
    dept = gc.department if gc else Department.PRIMARY

    # Find the right HOD
    hod_role = UserRole.PRIMARY_HOD if dept == Department.PRIMARY else UserRole.ECD_HOD
    hods = User.objects.filter(role=hod_role, is_active=True)

    tasks = []
    for hod in hods:
        task = _create_task(
            task_type='lesson_plan_review',
            title=f"Review: {lesson_plan.class_name} — {lesson_plan.subject_name}",
            description=f"Lesson plan submitted by {lesson_plan.teacher.get_full_name() or lesson_plan.teacher.username} for Week of {lesson_plan.week_start_date}. Please review and approve/reject.",
            assigned_to=hod,
            created_by=lesson_plan.teacher,
            due_date=_friday_of_week(lesson_plan.week_start_date),
            priority='high',
            content_type=content_type,
            object_id=lesson_plan.pk,
            metadata={
                'class_name': lesson_plan.class_name,
                'subject_name': lesson_plan.subject_name,
                'teacher_name': lesson_plan.teacher.get_full_name() or lesson_plan.teacher.username,
                'week_start': str(lesson_plan.week_start_date),
                'module_url': f'/academics/lesson-plans/',
            },
            actions_available=['approve', 'reject', 'request_revision'],
        )
        if task:
            tasks.append(task)
    return tasks


def generate_lesson_plan_submission_task(teacher):
    """Remind a teacher to submit lesson plans if none exist this week."""
    from academics.models import LessonPlan, LessonPlanStatus

    now = timezone.now()
    today = now.date()
    # Find current week's Monday
    monday = today - timedelta(days=today.weekday())

    existing = LessonPlan.objects.filter(
        teacher=teacher,
        week_start_date=monday,
        status__in=[LessonPlanStatus.SUBMITTED, LessonPlanStatus.APPROVED, LessonPlanStatus.DRAFT],
    ).exists()

    if existing:
        return None

    return _create_task(
        task_type='lesson_plan_submission',
        title=f"Submit lesson plans for week of {monday.strftime('%d %b')}",
        description="Weekly lesson plans are due by Friday 4:00 PM.",
        assigned_to=teacher,
        due_date=_friday_of_week(monday),
        priority='high',
        metadata={
            'week_start': str(monday),
            'module_url': '/academics/lesson-plans/new/',
        },
        actions_available=['submit', 'mark_in_progress'],
    )


def generate_report_signoff_tasks(report_card):
    """When report cards are pending sign-off, create tasks for HOS."""
    content_type = ContentType.objects.get_for_model(report_card.__class__)

    hos_users = User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True)
    tasks = []
    for hos in hos_users:
        task = _create_task(
            task_type='report_signoff',
            title=f"Sign off: {report_card.student.first_name} {report_card.student.last_name} — {report_card.student.class_name}",
            description=f"Report card for {report_card.term.name} is ready for sign-off. Student: {report_card.student.admission_no}.",
            assigned_to=hos,
            due_date=_due_end_of_term(),
            priority='high',
            content_type=content_type,
            object_id=report_card.pk,
            metadata={
                'student_name': f"{report_card.student.first_name} {report_card.student.last_name}",
                'class_name': report_card.student.class_name,
                'term_name': report_card.term.name,
                'module_url': f'/academics/hos-signoff/',
            },
            actions_available=['sign_off', 'reject', 'request_revision'],
        )
        if task:
            tasks.append(task)
    return tasks


def generate_exam_score_submission_task(teacher):
    """Remind teachers to enter exam scores if pending."""
    from academics.utils import get_current_term
    term = get_current_term()
    if not term:
        return None

    return _create_task(
        task_type='grade_submission',
        title=f"Enter exam scores — {term.name}",
        description=f"Exam scores for {term.name} are pending. Enter scores for your assigned classes.",
        assigned_to=teacher,
        due_date=_due_end_of_term(),
        priority='medium',
        metadata={
            'term_name': term.name,
            'module_url': '/academics/exam-scores/',
        },
        actions_available=['mark_in_progress', 'mark_complete'],
    )


def generate_ecd_evaluation_task(teacher):
    """Remind ECD teachers to complete evaluations."""
    return _create_task(
        task_type='assessment_entry',
        title="Complete ECD evaluation entries",
        description="ECD evaluations for the current term need to be completed for all your assigned classes.",
        assigned_to=teacher,
        due_date=_due_end_of_term(),
        priority='medium',
        metadata={
            'module_url': '/academics/ecd-evaluations/',
        },
        actions_available=['mark_in_progress', 'mark_complete'],
    )


def generate_weekly_focus_task(teacher):
    """Remind ECD teachers to submit Weekly Focus."""
    return _create_task(
        task_type='weekly_focus_submission',
        title=f"Submit Weekly Focus — Week {timezone.now().isocalendar()[1]}",
        description="Weekly focus for your ECD classes is due. Include theme, activities, and items to bring.",
        assigned_to=teacher,
        due_date=_due_tomorrow(),
        priority='medium',
        metadata={
            'module_url': '/communications/weekly-focus/new/',
        },
        actions_available=['submit', 'mark_in_progress'],
    )


#  Admissions Tasks 

def generate_assessment_scheduling_task(applicant):
    """Create task for admin to schedule assessment for new applicant."""
    admins = User.objects.filter(role__in=[UserRole.ADMIN_OFFICER, UserRole.SUPER_ADMIN], is_active=True)
    content_type = ContentType.objects.get_for_model(applicant.__class__)

    tasks = []
    for admin in admins:
        task = _create_task(
            task_type='assessment_scheduling',
            title=f"Schedule assessment: {applicant.child_full_name}",
            description=f"New inquiry for {applicant.grade_applying_for}. Parent: {applicant.parent_full_name}. Schedule and confirm assessment.",
            assigned_to=admin,
            due_date=_due_this_week(),
            priority='high',
            content_type=content_type,
            object_id=applicant.pk,
            metadata={
                'child_name': applicant.child_full_name,
                'grade': applicant.grade_applying_for,
                'parent_name': applicant.parent_full_name,
                'module_url': f'/admissions/applicant/{applicant.pk}/',
            },
            actions_available=['mark_in_progress', 'mark_complete'],
        )
        if task:
            tasks.append(task)
    return tasks


def generate_assessment_report_task(applicant, teacher_user):
    """Create task for the assigned teacher to fill the admission assessment report."""
    if not teacher_user:
        return None

    content_type = ContentType.objects.get_for_model(applicant.__class__)

    task = _create_task(
        task_type='assessment_report',
        title=f"Fill assessment report: {applicant.child_full_name}",
        description=(
            f"Complete the admission assessment report for {applicant.child_full_name}.\n\n"
            f"Grade applying for: {applicant.grade_applying_for}\n"
            f"Parent: {applicant.parent_full_name}\n"
            f"Reference: {applicant.reference_number}\n\n"
            f"Open this task to access the full assessment form."
        ),
        assigned_to=teacher_user,
        due_date=_due_this_week(),
        priority='high',
        content_type=content_type,
        object_id=applicant.pk,
        metadata={
            'child_name': applicant.child_full_name,
            'grade': applicant.grade_applying_for,
            'parent_name': applicant.parent_full_name,
            'reference': applicant.reference_number,
            'applicant_id': applicant.pk,
            'module_url': f'/admissions/applicant/{applicant.pk}/',
        },
        actions_available=['submit'],
    )
    return task


def generate_enrolment_processing_task(applicant):
    """Create task for admin to complete enrolment after admission."""
    admins = User.objects.filter(role__in=[UserRole.ADMIN_OFFICER, UserRole.SUPER_ADMIN], is_active=True)
    content_type = ContentType.objects.get_for_model(applicant.__class__)

    tasks = []
    for admin in admins:
        task = _create_task(
            task_type='enrolment_processing',
            title=f"Complete enrolment: {applicant.child_full_name}",
            description=f"Applicant has been admitted. Complete enrolment process including document verification and student ID generation.",
            assigned_to=admin,
            due_date=_due_this_week(),
            priority='critical',
            content_type=content_type,
            object_id=applicant.pk,
            metadata={
                'child_name': applicant.child_full_name,
                'grade': applicant.grade_applying_for,
                'module_url': f'/admissions/applicant/{applicant.pk}/',
            },
            actions_available=['mark_in_progress', 'mark_complete'],
        )
        if task:
            tasks.append(task)
    return tasks


def generate_applicant_followup_task(applicant):
    """Create follow-up task when applicant has been waiting too long."""
    from users.models import UserRole
    admins = User.objects.filter(role__in=[UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL], is_active=True)
    content_type = ContentType.objects.get_for_model(applicant.__class__)

    tasks = []
    for admin in admins:
        task = _create_task(
            task_type='applicant_followup',
            title=f"Follow up: {applicant.child_full_name}",
            description=f"Applicant {applicant.child_full_name} ({applicant.grade_applying_for}) has been in {applicant.get_status_display()} status. Please follow up.",
            assigned_to=admin,
            due_date=_due_tomorrow(),
            priority='high',
            content_type=content_type,
            object_id=applicant.pk,
            metadata={
                'child_name': applicant.child_full_name,
                'status': applicant.status,
                'module_url': f'/admissions/applicant/{applicant.pk}/',
            },
            actions_available=['contact_parent', 'mark_complete'],
        )
        if task:
            tasks.append(task)
    return tasks


#  Finance Tasks 

def generate_payment_matching_task(unmatched_payment):
    """Create task for finance officer to match a bank statement payment."""
    from finance.models import Payment
    finance_users = User.objects.filter(role__in=[UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN], is_active=True)
    content_type = ContentType.objects.get_for_model(unmatched_payment.__class__)

    tasks = []
    for user in finance_users:
        task = _create_task(
            task_type='payment_matching',
            title=f"Match payment: TZS {unmatched_payment.amount:,.0f} — {unmatched_payment.reference or 'No ref'}",
            description=f"Bank statement entry of TZS {unmatched_payment.amount:,.0f} needs to be matched to an invoice.",
            assigned_to=user,
            due_date=_due_tomorrow(),
            priority='high',
            content_type=content_type,
            object_id=unmatched_payment.pk,
            metadata={
                'amount': float(unmatched_payment.amount),
                'reference': unmatched_payment.reference or '',
                'module_url': '/finance/unmatched-payments/',
            },
            actions_available=['mark_in_progress', 'mark_complete'],
        )
        if task:
            tasks.append(task)
    return tasks


def generate_overdue_invoice_task(invoice):
    """Create task to follow up on overdue invoices."""
    from finance.models import PaymentMethod
    finance_users = User.objects.filter(role__in=[UserRole.FINANCE_OFFICER, UserRole.HEAD_OF_SCHOOL], is_active=True)
    content_type = ContentType.objects.get_for_model(invoice.__class__)

    tasks = []
    for user in finance_users:
        task = _create_task(
            task_type='overdue_collection',
            title=f"Follow up: {invoice.student.first_name} {invoice.student.last_name} — overdue fees",
            description=f"Invoice {invoice.invoice_number} is overdue. Balance: TZS {invoice.total_due:,.0f}. Contact parent/guardian.",
            assigned_to=user,
            due_date=_due_tomorrow(),
            priority='high',
            content_type=content_type,
            object_id=invoice.pk,
            metadata={
                'student_name': f"{invoice.student.first_name} {invoice.student.last_name}",
                'student_id': invoice.student.pk,
                'amount': float(invoice.total_due),
                'invoice_number': invoice.invoice_number,
                'module_url': f'/finance/invoices/{invoice.pk}/',
            },
            actions_available=['contact_parent', 'send_reminder', 'mark_complete'],
        )
        if task:
            tasks.append(task)
    return tasks


def generate_fee_structure_task():
    """Remind finance to set up fee structures for new term."""
    from academics.utils import get_current_term
    from finance.models import FeeStructure

    current_term = get_current_term()
    if not current_term:
        return None

    existing = FeeStructure.objects.filter(term=current_term).count()
    if existing > 0:
        return None  # Already configured

    finance_users = User.objects.filter(role__in=[UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN], is_active=True)
    tasks = []
    for user in finance_users:
        task = _create_task(
            task_type='fee_verification',
            title=f"Set up fee structures — {current_term.name}",
            description=f"No fee structures configured for {current_term.name}. Set up tuition, activity, and uniform fees for all classes.",
            assigned_to=user,
            due_date=_due_this_week(),
            priority='critical',
            metadata={
                'term_name': current_term.name,
                'module_url': '/finance/fee-structure/',
            },
            actions_available=['mark_in_progress', 'mark_complete'],
        )
        if task:
            tasks.append(task)
    return tasks


#  Welfare Tasks 

def generate_welfare_alert_task(observation):
    """Create task for HOD to review high/critical welfare observations."""
    from welfare.models import WelfareSeverity
    if observation.severity not in [WelfareSeverity.HIGH, WelfareSeverity.CRITICAL]:
        return None

    from academics.models import GradeClass, Department
    gc = GradeClass.objects.filter(name=observation.student.class_name).first()
    dept = gc.department if gc else Department.ECD

    hod_role = UserRole.ECD_HOD if dept == Department.ECD else UserRole.PRIMARY_HOD
    hods = User.objects.filter(role=hod_role, is_active=True)

    content_type = ContentType.objects.get_for_model(observation.__class__)
    tasks = []
    for hod in hods:
        task = _create_task(
            task_type='welfare_alert',
            title=f"Welfare alert: {observation.student.first_name} — {observation.get_severity_display()}",
            description=f"{'CRITICAL' if observation.severity == 'critical' else 'HIGH'} welfare observation for {observation.student.first_name} {observation.student.last_name} ({observation.student.class_name}). Requires immediate review.",
            assigned_to=hod,
            created_by=observation.submitted_by,
            due_date=_due_tomorrow(),
            priority='critical' if observation.severity == 'critical' else 'high',
            content_type=content_type,
            object_id=observation.pk,
            metadata={
                'student_name': f"{observation.student.first_name} {observation.student.last_name}",
                'class_name': observation.student.class_name,
                'severity': observation.severity,
                'module_url': f'/welfare/{observation.pk}/',
            },
            actions_available=['review', 'contact_parent', 'mark_complete'],
        )
        if task:
            tasks.append(task)
    return tasks


def generate_welfare_followup_task(observation):
    """Create follow-up task for welfare observations with pending follow-ups."""
    if not observation.follow_up_required or not observation.follow_up_date:
        return None

    from django.utils import timezone as tz
    if observation.follow_up_date > tz.now().date():
        return None  # Not yet due

    assigned = observation.reviewed_by or observation.submitted_by
    if not assigned:
        return None

    content_type = ContentType.objects.get_for_model(observation.__class__)
    return _create_task(
        task_type='welfare_followup',
        title=f"Welfare follow-up: {observation.student.first_name}",
        description=f"Follow-up required for welfare observation of {observation.student.first_name} {observation.student.last_name}.",
        assigned_to=assigned,
        due_date=tz.make_aware(timezone.datetime.combine(observation.follow_up_date, timezone.datetime.min.time())) + timedelta(hours=9),
        priority='medium',
        content_type=content_type,
        object_id=observation.pk,
        metadata={
            'student_name': f"{observation.student.first_name} {observation.student.last_name}",
            'module_url': f'/welfare/{observation.pk}/',
        },
        actions_available=['mark_complete', 'defer'],
    )


#  Attendance Tasks 

def generate_attendance_alert_task(student, attendance_rate):
    """Create task when student attendance drops below threshold (85%)."""
    from finance.models import PaymentMethod

    # Assign to class teacher
    from hr.models import TeacherClassAssignment
    from academics.utils import get_current_term

    term = get_current_term()
    if not term:
        return None

    class_teacher_assignment = TeacherClassAssignment.objects.filter(
        grade_class__name=student.class_name,
        term=term,
        is_class_teacher=True,
    ).select_related('teacher__user').first()

    if not class_teacher_assignment:
        return None

    teacher_user = class_teacher_assignment.teacher.user
    content_type = ContentType.objects.get_for_model(student.__class__)

    task = _create_task(
        task_type='attendance_alert',
        title=f"Attendance alert: {student.first_name} — {attendance_rate:.0f}%",
        description=f"{student.first_name} {student.last_name}'s attendance has dropped to {attendance_rate:.0f}%, below the 85% threshold. Follow up with parent.",
        assigned_to=teacher_user,
        due_date=_due_tomorrow(),
        priority='high',
        content_type=content_type,
        object_id=student.pk,
        metadata={
            'student_name': f"{student.first_name} {student.last_name}",
            'class_name': student.class_name,
            'attendance_rate': attendance_rate,
            'module_url': f'/students/{student.pk}/',
        },
        actions_available=['contact_parent', 'mark_complete'],
    )
    return task


#  HR Tasks 

def generate_leave_approval_task(leave_request):
    """Create task for HOS to approve/reject leave requests."""
    hos_users = User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True)
    content_type = ContentType.objects.get_for_model(leave_request.__class__)

    tasks = []
    for hos in hos_users:
        task = _create_task(
            task_type='compliance_check',
            title=f"Leave request: {leave_request.staff.full_name} ({leave_request.total_days} days)",
            description=f"{leave_request.staff.full_name} has submitted a leave request from {leave_request.start_date} to {leave_request.end_date}. Please review.",
            assigned_to=hos,
            due_date=_due_tomorrow(),
            priority='high',
            content_type=content_type,
            object_id=leave_request.pk,
            metadata={
                'staff_name': leave_request.staff.full_name,
                'start_date': str(leave_request.start_date),
                'end_date': str(leave_request.end_date),
                'days': leave_request.total_days,
                'module_url': '/hr/leave/',
            },
            actions_available=['approve', 'reject'],
        )
        if task:
            tasks.append(task)
    return tasks


def generate_payroll_approval_task(payroll_run):
    """Create task for HOS to approve payroll."""
    hos_users = User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True)
    content_type = ContentType.objects.get_for_model(payroll_run.__class__)

    tasks = []
    for hos in hos_users:
        task = _create_task(
            task_type='compliance_check',
            title=f"Approve payroll: {payroll_run.period_name}",
            description=f"Payroll for {payroll_run.period_name} has been submitted for approval. Total net pay: TZS {payroll_run.total_net_pay:,.0f}.",
            assigned_to=hos,
            due_date=_due_tomorrow(),
            priority='critical',
            content_type=content_type,
            object_id=payroll_run.pk,
            metadata={
                'period_name': payroll_run.period_name,
                'total_net_pay': float(payroll_run.total_net_pay or 0),
                'module_url': f'/hr/payroll/{payroll_run.pk}/',
            },
            actions_available=['approve', 'reject'],
        )
        if task:
            tasks.append(task)
    return tasks


def generate_contract_expiry_tasks():
    """Create tasks for staff contracts expiring within 60 days."""
    from datetime import date as date_cls
    from hr.models import StaffProfile

    today = date_cls.today()
    threshold = today + timedelta(days=60)

    expiring = StaffProfile.objects.filter(
        is_active=True,
        contract_end_date__isnull=False,
        contract_end_date__gte=today,
        contract_end_date__lte=threshold,
    )

    hos_users = User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True)
    tasks = []
    for staff in expiring:
        for hos in hos_users:
            if not _task_exists('compliance_check', hos):
                days_left = (staff.contract_end_date - today).days
                task = _create_task(
                    task_type='compliance_check',
                    title=f"Contract expiring: {staff.full_name} ({days_left} days)",
                    description=f"{staff.full_name}'s contract expires on {staff.contract_end_date}. Consider renewal or offboarding.",
                    assigned_to=hos,
                    due_date=timezone.make_aware(timezone.datetime.combine(staff.contract_end_date - timedelta(days=14), timezone.datetime.min.time())),
                    priority='medium' if days_left > 30 else 'high',
                    metadata={
                        'staff_name': staff.full_name,
                        'contract_end': str(staff.contract_end_date),
                        'days_left': days_left,
                        'module_url': f'/hr/staff/{staff.pk}/',
                    },
                    actions_available=['mark_in_progress', 'mark_complete'],
                )
                if task:
                    tasks.append(task)
    return tasks#  Welfare Periodic Follow-up Scanning 

def generate_overdue_welfare_followup_tasks():
    """FR-WEL-003: Scan for welfare observations with overdue follow-up dates.

    Severity-based follow-up reminders:
    - Low: 5-school-day follow-up reminder (no external notification)
    - Medium: 3-school-day reminder
    - High/Critical: Already handled by generate_welfare_alert_task
    - Critical: Both HOD + HOS must acknowledge

    Returns list of tasks created.
    """
    from welfare.models import WelfareObservation, WelfareSeverity
    from academics.models import Term

    today = timezone.now().date()

    # Find observations where follow_up_required=True and follow_up_date <= today
    # and the observation is still open (not resolved)
    overdue_observations = WelfareObservation.objects.filter(
        follow_up_required=True,
        follow_up_date__lte=today,
    ).exclude(
        hod_status="resolved",
    ).select_related("student", "submitted_by", "reviewed_by")

    tasks = []
    for obs in overdue_observations:
        # Determine assigned user
        assigned = obs.reviewed_by or obs.submitted_by
        if not assigned:
            continue

        # Skip if overdue follow-up task already exists for this observation
        content_type = ContentType.objects.get_for_model(obs.__class__)
        if _task_exists("welfare_followup", assigned, content_type, obs.pk):
            continue

        # Determine priority and message based on severity
        if obs.severity == WelfareSeverity.CRITICAL:
            priority = "critical"
            reminder_msg = (f"CRITICAL welfare observation for {obs.student.first_name} "
                          f"{obs.student.last_name} requires follow-up and acknowledgement.")
            # Also notify HOS for critical observations
            hos_users = User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True)
            for hos in hos_users:
                if not _task_exists("welfare_followup", hos, content_type, obs.pk):
                    task = _create_task(
                        task_type="welfare_followup",
                        title=f"Welfare follow-up (Critical): {obs.student.first_name}",
                        description=reminder_msg,
                        assigned_to=hos,
                        due_date=timezone.now() + timedelta(hours=8),
                        priority="critical",
                        content_type=content_type,
                        object_id=obs.pk,
                        metadata={
                            "student_name": f"{obs.student.first_name} {obs.student.last_name}",
                            "student_id": obs.student.pk,
                            "severity": obs.severity,
                            "module_url": f"/welfare/{obs.pk}/",
                        },
                        actions_available=["review", "mark_complete"],
                    )
                    if task:
                        tasks.append(task)
        elif obs.severity == WelfareSeverity.HIGH:
            priority = "high"
            reminder_msg = (f"HIGH severity welfare follow-up overdue for "
                          f"{obs.student.first_name} {obs.student.last_name}.")
        elif obs.severity == WelfareSeverity.MEDIUM:
            priority = "medium"
            reminder_msg = (f"Welfare follow-up reminder (3-day) for "
                          f"{obs.student.first_name} {obs.student.last_name}.")
        else:  # LOW
            priority = "low"
            reminder_msg = (f"Welfare follow-up reminder (5-day) for "
                          f"{obs.student.first_name} {obs.student.last_name}.")

        task = _create_task(
            task_type="welfare_followup",
            title=f"Welfare follow-up: {obs.student.first_name} ({obs.get_severity_display()})",
            description=reminder_msg,
            assigned_to=assigned,
            due_date=timezone.now() + timedelta(hours=8),
            priority=priority,
            content_type=content_type,
            object_id=obs.pk,
            metadata={
                "student_name": f"{obs.student.first_name} {obs.student.last_name}",
                "student_id": obs.student.pk,
                "severity": obs.severity,
                "module_url": f"/welfare/{obs.pk}/",
            },
            actions_available=["mark_complete", "defer"],
        )
        if task:
            tasks.append(task)

    return tasks


#  Bulk / Scheduled Task Generation 
def generate_daily_tasks():
    """
    Run daily (via management command or cron) to generate
    recurring tasks for all active users based on their role.
    """
    today = timezone.now().date()
    tasks_created = []

    # Teachers: lesson plan reminders (if week started and no plans yet)
    from datetime import date as date_cls
    if today.weekday() == 0:  # Monday
        teachers = User.objects.filter(role=UserRole.TEACHER, is_active=True)
        for teacher in teachers:
            from core.teacher_context import is_ecd_teacher
            task = generate_lesson_plan_submission_task(teacher)
            if task:
                tasks_created.append(task)

            # ECD teachers: weekly focus
            if is_ecd_teacher(teacher):
                task = generate_weekly_focus_task(teacher)
                if task:
                    tasks_created.append(task)

    # Finance: overdue invoices
    if today.weekday() in [0, 2, 4]:  # Mon, Wed, Fri
        from finance.models import Invoice, InvoiceStatus
        overdue = Invoice.objects.filter(status=InvoiceStatus.OVERDUE)[:10]
        for inv in overdue:
            tasks = generate_overdue_invoice_task(inv)
            tasks_created.extend(tasks)

    # Contract expiry checks (weekly on Monday)
    if today.weekday() == 0:
        tasks = generate_contract_expiry_tasks()
        tasks_created.extend(tasks)

    # Fee structure check (once per term start)
    tasks = generate_fee_structure_task()
    if tasks:
        tasks_created.extend(tasks)

    # FR-WEL-003: Welfare severity follow-up reminders
    # Scan for observations with overdue follow-up dates
    welfare_tasks = generate_overdue_welfare_followup_tasks()
    tasks_created.extend(welfare_tasks)

    return tasks_created


# ============================================================================
# PARENT PORTAL TASK GENERATORS
# ============================================================================

def generate_discipline_contact_task(incident):
    """FR-PTC: Create a parent contact task when a discipline incident requires parent notification."""
    from discipline.models import DisciplineIncident
    from students.models import Student, StudentGuardian, ParentGuardian

    if not incident.parent_contacted and incident.severity in ('medium', 'high', 'critical'):
        student = incident.student
        guardians = ParentGuardian.objects.filter(studentguardian__student=student)
        tasks_created = []
        for guardian in guardians:
            if not guardian.user_id:
                continue
            existing = Task.objects.filter(
                task_type='discipline_contact',
                content_type=ContentType.objects.get_for_model(DisciplineIncident),
                object_id=incident.pk,
                assigned_to=guardian.user,
                status__in=['pending', 'in_progress'],
            ).exists()
            if not existing:
                priority_map = {'critical': 'urgent', 'high': 'high', 'medium': 'medium'}
                task = Task.objects.create(
                    title=f"Contact regarding {student.get_full_name()} discipline incident",
                    description=(
                        f"A {incident.severity} discipline incident was recorded for {student.get_full_name()} "
                        f"on {incident.incident_date.strftime('%d %b %Y') if incident.incident_date else 'N/A'}.\n"
                        f"Summary: {incident.summary}\n"
                        f"Action taken: {incident.action_taken or 'None yet'}\n\n"
                        f"Please contact the school to discuss this matter."
                    ),
                    task_type='discipline_contact',
                    priority=priority_map.get(incident.severity, 'medium'),
                    status='pending',
                    assigned_to=guardian.user,
                    created_by=incident.reported_by,
                    content_type=ContentType.objects.get_for_model(DisciplineIncident),
                    object_id=incident.pk,
                    metadata={
                        'student_id': student.pk,
                        'student_name': student.get_full_name(),
                        'class_name': student.class_name,
                        'module_url': f'/discipline/incident/{incident.pk}/',
                        'incident_severity': incident.severity,
                    },
                    due_date=(incident.incident_date + timedelta(days=3)) if incident.incident_date else None,
                )
                tasks_created.append(task)
        return tasks_created
    return []


def generate_report_card_ready_task(report_card):
    """Create a parent task when a report card is published."""
    from academics.models import ReportCard
    from students.models import Student, ParentGuardian

    if report_card.status != 'published':
        return []

    student = report_card.student
    guardians = ParentGuardian.objects.filter(studentguardian__student=student)
    tasks_created = []
    for guardian in guardians:
        if not guardian.user_id:
            continue
        existing = Task.objects.filter(
            task_type='report_card_ready',
            content_type=ContentType.objects.get_for_model(ReportCard),
            object_id=report_card.pk,
            assigned_to=guardian.user,
            status__in=['pending', 'in_progress'],
        ).exists()
        if not existing:
            task = Task.objects.create(
                title=f"{student.get_full_name()} -- {report_card.term.name} report card ready",
                description=(
                    f"The {report_card.term.name} report card for {student.get_full_name()} "
                    f"has been published. Overall average: {report_card.overall_average}%.\n\n"
                    f"View the full report card in the Reports section."
                ),
                task_type='report_card_ready',
                priority='medium',
                status='pending',
                assigned_to=guardian.user,
                created_by=report_card.generated_by,
                content_type=ContentType.objects.get_for_model(ReportCard),
                object_id=report_card.pk,
                metadata={
                    'student_id': student.pk,
                    'student_name': student.get_full_name(),
                    'class_name': student.class_name,
                    'term_name': report_card.term.name,
                    'overall_average': str(report_card.overall_average),
                    'module_url': f'/reports/report-card/{report_card.pk}/',
                },
            )
            tasks_created.append(task)
    return tasks_created


def generate_open_invoice_task(invoice):
    """Create a parent task for unpaid/partial invoices."""
    from finance.models import Invoice
    from students.models import Student, ParentGuardian

    if invoice.status in ('paid',):
        return []
    if not invoice.student_id:
        return []

    student = invoice.student
    guardians = ParentGuardian.objects.filter(studentguardian__student=student)
    tasks_created = []
    for guardian in guardians:
        if not guardian.user_id:
            continue
        existing = Task.objects.filter(
            task_type='open_invoice',
            content_type=ContentType.objects.get_for_model(Invoice),
            object_id=invoice.pk,
            assigned_to=guardian.user,
            status__in=['pending', 'in_progress'],
        ).exists()
        if not existing:
            priority = 'high' if invoice.status == 'overdue' else 'medium'
            task = Task.objects.create(
                title=f"Fee payment due -- {student.get_full_name()} -- TZS {invoice.total_due:,.0f}",
                description=(
                    f"An invoice for {student.get_full_name()} ({invoice.term.name if invoice.term else 'N/A'}) "
                    f"is {invoice.status}. Amount: TZS {invoice.total_due:,.0f}.\n"
                    f"Due date: {invoice.due_date.strftime('%d %b %Y') if invoice.due_date else 'N/A'}.\n\n"
                    f"Please make payment to avoid disruption to your child's education."
                ),
                task_type='open_invoice',
                priority=priority,
                status='pending',
                assigned_to=guardian.user,
                created_by=None,
                content_type=ContentType.objects.get_for_model(Invoice),
                object_id=invoice.pk,
                metadata={
                    'student_id': student.pk,
                    'student_name': student.get_full_name(),
                    'class_name': student.class_name,
                    'invoice_number': invoice.invoice_number or '',
                    'amount_due': str(invoice.total_due),
                    'invoice_status': invoice.status,
                    'module_url': f'/finance/invoice/{invoice.pk}/',
                },
                due_date=invoice.due_date,
            )
            tasks_created.append(task)
    return tasks_created


def generate_parent_tasks_for_student(student):
    """Scan and create all pending parent tasks for a given student (used by seeder/daily)."""
    tasks = []
    from discipline.models import DisciplineIncident
    from finance.models import Invoice
    from academics.models import ReportCard

    for incident in DisciplineIncident.objects.filter(
        student=student, parent_contacted=False, severity__in=['medium', 'high', 'critical']
    ):
        tasks.extend(generate_discipline_contact_task(incident))

    for inv in Invoice.objects.filter(
        student=student, status__in=['unpaid', 'partial', 'overdue']
    ):
        tasks.extend(generate_open_invoice_task(inv))

    for rc in ReportCard.objects.filter(student=student, status='published'):
        tasks.extend(generate_report_card_ready_task(rc))

    return tasks


def cleanup_completed_tasks(days_old=30):
    """Archive completed tasks older than N days."""
    cutoff = timezone.now() - timedelta(days=days_old)
    deleted = Task.objects.filter(
        status='completed',
        completed_at__lt=cutoff,
    ).delete()
    return deleted
