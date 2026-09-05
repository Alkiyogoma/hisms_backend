"""
One-time task seeder — scans ALL existing module data and creates
tasks for pending items that were never wired up via signals.

Run via: /tasks/seed/  (browser) or `python manage.py seed_tasks` (CLI)
"""

from datetime import timedelta
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType

from users.models import User, UserRole
from .models import Task


def _task_exists(task_type, assigned_to, content_type=None, object_id=None):
    """Dedup check — skip if a pending task already exists."""
    qs = Task.objects.filter(
        task_type=task_type,
        assigned_to=assigned_to,
        status__in=['pending', 'in_progress'],
    )
    if content_type and object_id:
        qs = qs.filter(content_type=content_type, object_id=object_id)
    return qs.exists()


def _due_tomorrow():
    return timezone.now() + timedelta(days=1, hours=8)


def _due_this_week():
    return timezone.now() + timedelta(days=5)


def _due_end_of_term():
    from academics.models import Term
    term = Term.objects.filter(is_locked=False).first()
    if term and term.end_date:
        from datetime import time
        return timezone.make_aware(timezone.datetime.combine(term.end_date, time(17, 0)))
    return _due_this_week()


def seed_all():
    """
    Master seeder — calls each module seeder and returns a summary dict.
    Safe to run multiple times (dedup prevents duplicates).
    """
    results = {
        'admissions': 0,
        'lesson_plans': 0,
        'exam_scores': 0,
        'report_cards': 0,
        'overdue_invoices': 0,
        'unmatched_payments': 0,
        'welfare_alerts': 0,
        'leave_requests': 0,
        'payroll_approvals': 0,
        'fee_structures': 0,
        'attendance_alerts': 0,
        'total': 0,
    }

    results['admissions'] = seed_admissions_tasks()
    results['lesson_plans'] = seed_lesson_plan_tasks()
    results['exam_scores'] = seed_exam_score_tasks()
    results['report_cards'] = seed_report_card_tasks()
    results['overdue_invoices'] = seed_overdue_invoice_tasks()
    results['unmatched_payments'] = seed_unmatched_payment_tasks()
    results['welfare_alerts'] = seed_welfare_tasks()
    results['leave_requests'] = seed_leave_request_tasks()
    results['payroll_approvals'] = seed_payroll_tasks()
    results['fee_structures'] = seed_fee_structure_tasks()
    results['attendance_alerts'] = seed_attendance_tasks()

    results['total'] = sum(v for k, v in results.items() if k != 'total')
    return results


def seed_admissions_tasks():
    """Generate tasks for all pending applicants."""
    from admissions.models import Applicant, ApplicantStatus

    ACTIVE_STATUSES = [
        ApplicantStatus.INQUIRY_RECEIVED,
        ApplicantStatus.MEETING_SCHEDULED,
        ApplicantStatus.ASSESSMENT_PENDING,
        ApplicantStatus.ASSESSMENT_FEE_PAID,
        ApplicantStatus.ASSESSMENT_CONFIRMED,
        ApplicantStatus.ASSESSMENT_COMPLETED,
        ApplicantStatus.HOD_REVIEW,
        ApplicantStatus.HOS_DECISION,
        ApplicantStatus.ADMITTED,
        ApplicantStatus.WAITLISTED,
        ApplicantStatus.MEETING_COMPLETED,
        ApplicantStatus.DECLINED_AT_MEETING,
        ApplicantStatus.REPORT_PENDING,
        ApplicantStatus.ASSESSMENT_FAILED,
        ApplicantStatus.FORM_SUBMITTED,
        ApplicantStatus.INVOICE_GENERATED,
        ApplicantStatus.INVOICE_PAID,
        ApplicantStatus.FLAGGED_FOR_REVIEW,
    ]

    applicants = Applicant.objects.filter(status__in=ACTIVE_STATUSES)
    admins = User.objects.filter(
        role__in=[UserRole.ADMIN_OFFICER, UserRole.SUPER_ADMIN], is_active=True
    )
    hos_users = User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True)

    ct = ContentType.objects.get_for_model(Applicant)
    created = 0

    for app in applicants:
        # Assessment scheduling tasks
        if app.status in [
            ApplicantStatus.INQUIRY_RECEIVED,
            ApplicantStatus.MEETING_SCHEDULED,
        ]:
            for admin in admins:
                if not _task_exists('assessment_scheduling', admin, ct, app.pk):
                    Task.objects.create(
                        task_type='assessment_scheduling',
                        title=f'Schedule assessment: {app.child_full_name}',
                        description=f'New inquiry for {app.grade_applying_for}. '
                                    f'Parent: {app.parent_full_name}. Schedule assessment.',
                        assigned_to=admin,
                        due_date=_due_this_week(),
                        priority='high',
                        content_type=ct,
                        object_id=app.pk,
                        metadata={
                            'child_name': app.child_full_name,
                            'grade': app.grade_applying_for,
                            'parent_name': app.parent_full_name,
                            'module_url': f'/admissions/applicant/{app.pk}/',
                        },
                        actions_available=['mark_in_progress', 'mark_complete'],
                    )
                    created += 1

        # Assessment fee verification
        if app.status == ApplicantStatus.ASSESSMENT_FEE_PAID:
            for admin in admins:
                if not _task_exists('assessment_scheduling', admin, ct, app.pk):
                    Task.objects.create(
                        task_type='assessment_scheduling',
                        title=f'Verify assessment fee: {app.child_full_name}',
                        description=f'Assessment fee paid for {app.child_full_name}. '
                                    f'Confirm payment and proceed.',
                        assigned_to=admin,
                        due_date=_due_tomorrow(),
                        priority='high',
                        content_type=ct,
                        object_id=app.pk,
                        metadata={
                            'child_name': app.child_full_name,
                            'grade': app.grade_applying_for,
                            'module_url': f'/admissions/applicant/{app.pk}/',
                        },
                        actions_available=['mark_in_progress', 'mark_complete'],
                    )
                    created += 1

        # HOD review tasks
        if app.status == ApplicantStatus.HOD_REVIEW:
            for admin in admins:
                if not _task_exists('assessment_scheduling', admin, ct, app.pk):
                    Task.objects.create(
                        task_type='assessment_scheduling',
                        title=f'HOD review: {app.child_full_name}',
                        description=f'Assessment completed for {app.child_full_name}. '
                                    f'HOD needs to review results.',
                        assigned_to=admin,
                        due_date=_due_tomorrow(),
                        priority='high',
                        content_type=ct,
                        object_id=app.pk,
                        metadata={
                            'child_name': app.child_full_name,
                            'grade': app.grade_applying_for,
                            'module_url': f'/admissions/applicant/{app.pk}/',
                        },
                        actions_available=['mark_in_progress', 'mark_complete'],
                    )
                    created += 1

        # HOS decision tasks
        if app.status == ApplicantStatus.HOS_DECISION:
            for hos in hos_users:
                if not _task_exists('assessment_scheduling', hos, ct, app.pk):
                    Task.objects.create(
                        task_type='assessment_scheduling',
                        title=f'Admission decision: {app.child_full_name}',
                        description=f'HOS decision needed for {app.child_full_name} '
                                    f'({app.grade_applying_for}).',
                        assigned_to=hos,
                        due_date=_due_tomorrow(),
                        priority='critical',
                        content_type=ct,
                        object_id=app.pk,
                        metadata={
                            'child_name': app.child_full_name,
                            'grade': app.grade_applying_for,
                            'module_url': f'/admissions/applicant/{app.pk}/',
                        },
                        actions_available=['mark_in_progress', 'mark_complete'],
                    )
                    created += 1

        # Enrolment processing for admitted applicants
        if app.status == ApplicantStatus.ADMITTED:
            for admin in admins:
                if not _task_exists('enrolment_processing', admin, ct, app.pk):
                    Task.objects.create(
                        task_type='enrolment_processing',
                        title=f'Complete enrolment: {app.child_full_name}',
                        description=f'{app.child_full_name} has been admitted to '
                                    f'{app.grade_applying_for}. Complete enrolment.',
                        assigned_to=admin,
                        due_date=_due_this_week(),
                        priority='critical',
                        content_type=ct,
                        object_id=app.pk,
                        metadata={
                            'child_name': app.child_full_name,
                            'grade': app.grade_applying_for,
                            'module_url': f'/admissions/applicant/{app.pk}/',
                        },
                        actions_available=['mark_in_progress', 'mark_complete'],
                    )
                    created += 1

        # Follow-up for waitlisted
        if app.status == ApplicantStatus.WAITLISTED:
            for admin in admins:
                if not _task_exists('applicant_followup', admin, ct, app.pk):
                    Task.objects.create(
                        task_type='applicant_followup',
                        title=f'Follow up: {app.child_full_name} (waitlisted)',
                        description=f'{app.child_full_name} is waitlisted for '
                                    f'{app.grade_applying_for}. Follow up with parent.',
                        assigned_to=admin,
                        due_date=_due_this_week(),
                        priority='medium',
                        content_type=ct,
                        object_id=app.pk,
                        metadata={
                            'child_name': app.child_full_name,
                            'grade': app.grade_applying_for,
                            'module_url': f'/admissions/applicant/{app.pk}/',
                        },
                        actions_available=['contact_parent', 'mark_complete'],
                    )
                    created += 1

    return created


def seed_lesson_plan_tasks():
    """Generate tasks for submitted lesson plans needing HOD review."""
    from academics.models import LessonPlan, LessonPlanStatus, GradeClass, Department

    submitted = LessonPlan.objects.filter(
        status__in=[LessonPlanStatus.SUBMITTED, LessonPlanStatus.REVISION_REQUESTED]
    ).select_related('teacher')

    ct = ContentType.objects.get_for_model(LessonPlan)
    created = 0

    for lp in submitted:
        # Find HOD for review
        gc = GradeClass.objects.filter(name=lp.class_name).first()
        dept = gc.department if gc else Department.PRIMARY
        hod_role = UserRole.PRIMARY_HOD if dept == Department.PRIMARY else UserRole.ECD_HOD
        hods = User.objects.filter(role=hod_role, is_active=True)

        for hod in hods:
            if not _task_exists('lesson_plan_review', hod, ct, lp.pk):
                from datetime import time
                from datetime import timedelta as td
                fri = lp.week_start_date + td(days=4)
                due = timezone.make_aware(
                    timezone.datetime.combine(fri, time(16, 0))
                )
                Task.objects.create(
                    task_type='lesson_plan_review',
                    title=f'Review: {lp.class_name} — {lp.subject_name}',
                    description=f'Lesson plan submitted by '
                                f'{lp.teacher.get_full_name() or lp.teacher.username} '
                                f'for week of {lp.week_start_date}.',
                    assigned_to=hod,
                    created_by=lp.teacher,
                    due_date=due,
                    priority='high',
                    content_type=ct,
                    object_id=lp.pk,
                    metadata={
                        'class_name': lp.class_name,
                        'subject_name': lp.subject_name,
                        'teacher_name': lp.teacher.get_full_name() or lp.teacher.username,
                        'week_start': str(lp.week_start_date),
                        'module_url': '/academics/lesson-plans/',
                    },
                    actions_available=['approve', 'reject', 'request_revision'],
                )
                created += 1

        # Revision requested → task for teacher to resubmit
        if lp.status == LessonPlanStatus.REVISION_REQUESTED:
            if not _task_exists('lesson_plan_submission', lp.teacher, ct, lp.pk):
                from datetime import time
                from datetime import timedelta as td
                fri = lp.week_start_date + td(days=4)
                due = timezone.make_aware(
                    timezone.datetime.combine(fri, time(16, 0))
                )
                Task.objects.create(
                    task_type='lesson_plan_submission',
                    title=f'Revise lesson plan: {lp.class_name} — {lp.subject_name}',
                    description=f'Your lesson plan needs revision. '
                                f'Review feedback and resubmit.',
                    assigned_to=lp.teacher,
                    due_date=due,
                    priority='high',
                    content_type=ct,
                    object_id=lp.pk,
                    metadata={
                        'class_name': lp.class_name,
                        'subject_name': lp.subject_name,
                        'module_url': '/academics/lesson-plans/',
                    },
                    actions_available=['submit', 'mark_in_progress'],
                )
                created += 1

    # Also generate submission reminders for teachers who haven't submitted
    from datetime import date as date_cls
    today = timezone.now().date()
    monday = today - timedelta(days=today.weekday())

    teachers = User.objects.filter(role=UserRole.TEACHER, is_active=True)
    for teacher in teachers:
        has_plan = LessonPlan.objects.filter(
            teacher=teacher,
            week_start_date=monday,
            status__in=[
                LessonPlanStatus.SUBMITTED,
                LessonPlanStatus.APPROVED,
                LessonPlanStatus.DRAFT,
            ],
        ).exists()
        if not has_plan:
            if not _task_exists('lesson_plan_submission', teacher):
                from datetime import time
                fri = monday + timedelta(days=4)
                due = timezone.make_aware(
                    timezone.datetime.combine(fri, time(16, 0))
                )
                Task.objects.create(
                    task_type='lesson_plan_submission',
                    title=f'Submit lesson plans for week of {monday.strftime("%d %b")}',
                    description='Weekly lesson plans are due by Friday 4:00 PM.',
                    assigned_to=teacher,
                    due_date=due,
                    priority='high',
                    metadata={
                        'week_start': str(monday),
                        'module_url': '/academics/lesson-plans/new/',
                    },
                    actions_available=['submit', 'mark_in_progress'],
                )
                created += 1

    return created


def seed_exam_score_tasks():
    """Generate tasks for exam scores submitted for HOD approval."""
    from academics.models import ExamScore, ScoreStatus

    submitted = ExamScore.objects.filter(status=ScoreStatus.SUBMITTED)
    ct = ContentType.objects.get_for_model(ExamScore)
    created = 0

    for score in submitted.select_related('student', 'teacher'):
        if not _task_exists('grade_submission', score.entered_by, ct, score.pk):
            Task.objects.create(
                task_type='grade_submission',
                title=f'Review scores: {score.student.first_name} — {score.subject_name}',
                description=f'Exam scores for {score.exam_type} submitted by '
                            f'{score.entered_by.get_full_name() or score.entered_by.username}. '
                            f'Score: {score.score}/{score.max_score}.',
                assigned_to=score.entered_by,
                due_date=_due_tomorrow(),
                priority='medium',
                content_type=ct,
                object_id=score.pk,
                metadata={
                    'student_name': f'{score.student.first_name} {score.student.last_name}',
                    'subject': score.subject_name,
                    'module_url': '/academics/exam-scores/',
                },
                actions_available=['mark_in_progress', 'mark_complete'],
            )
            created += 1

    return created


def seed_report_card_tasks():
    """Generate tasks for report cards pending HOS sign-off."""
    from academics.models import ReportCard, ReportCardStatus

    pending = ReportCard.objects.filter(status=ReportCardStatus.PENDING_SIGN_OFF)
    ct = ContentType.objects.get_for_model(ReportCard)
    hos_users = User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True)
    created = 0

    for rc in pending.select_related('student', 'term'):
        for hos in hos_users:
            if not _task_exists('report_signoff', hos, ct, rc.pk):
                Task.objects.create(
                    task_type='report_signoff',
                    title=f'Sign off: {rc.student.first_name} {rc.student.last_name} — {rc.student.class_name}',
                    description=f'Report card for {rc.term.name} ready for sign-off.',
                    assigned_to=hos,
                    due_date=_due_end_of_term(),
                    priority='high',
                    content_type=ct,
                    object_id=rc.pk,
                    metadata={
                        'student_name': f'{rc.student.first_name} {rc.student.last_name}',
                        'class_name': rc.student.class_name,
                        'term_name': rc.term.name,
                        'module_url': '/academics/hos-signoff/',
                    },
                    actions_available=['sign_off', 'reject', 'request_revision'],
                )
                created += 1

    return created


def seed_overdue_invoice_tasks():
    """Generate tasks for overdue invoices."""
    from finance.models import Invoice, InvoiceStatus

    overdue = Invoice.objects.filter(status=InvoiceStatus.OVERDUE).select_related('student')
    finance_users = User.objects.filter(
        role__in=[UserRole.FINANCE_OFFICER, UserRole.HEAD_OF_SCHOOL], is_active=True
    )
    ct = ContentType.objects.get_for_model(Invoice)
    created = 0

    for inv in overdue:
        for user in finance_users:
            if not _task_exists('overdue_collection', user, ct, inv.pk):
                Task.objects.create(
                    task_type='overdue_collection',
                    title=f'Follow up: {inv.student.first_name} {inv.student.last_name} — overdue fees',
                    description=f'Invoice {inv.invoice_number} is overdue. '
                                f'Balance: TZS {inv.total_due:,.0f}. Contact parent.',
                    assigned_to=user,
                    due_date=_due_tomorrow(),
                    priority='high',
                    content_type=ct,
                    object_id=inv.pk,
                    metadata={
                        'student_name': f'{inv.student.first_name} {inv.student.last_name}',
                        'student_id': inv.student.pk,
                        'amount': float(inv.total_due),
                        'invoice_number': inv.invoice_number,
                        'module_url': f'/finance/invoices/{inv.pk}/',
                    },
                    actions_available=['contact_parent', 'send_reminder', 'mark_complete'],
                )
                created += 1

    return created


def seed_unmatched_payment_tasks():
    """Generate tasks for unmatched bank payments."""
    from finance.models import UnmatchedPayment

    unmatched = UnmatchedPayment.objects.filter(is_resolved=False)
    finance_users = User.objects.filter(
        role__in=[UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN], is_active=True
    )
    ct = ContentType.objects.get_for_model(UnmatchedPayment)
    created = 0

    for payment in unmatched:
        for user in finance_users:
            if not _task_exists('payment_matching', user, ct, payment.pk):
                Task.objects.create(
                    task_type='payment_matching',
                    title=f'Match payment: TZS {payment.amount:,.0f} — {payment.reference or "No ref"}',
                    description=f'Bank statement entry of TZS {payment.amount:,.0f} '
                                f'needs to be matched to an invoice.',
                    assigned_to=user,
                    due_date=_due_tomorrow(),
                    priority='high',
                    content_type=ct,
                    object_id=payment.pk,
                    metadata={
                        'amount': float(payment.amount),
                        'reference': payment.reference or '',
                        'module_url': '/finance/unmatched-payments/',
                    },
                    actions_available=['mark_in_progress', 'mark_complete'],
                )
                created += 1

    return created


def seed_welfare_tasks():
    """Generate tasks for high/critical welfare observations pending HOD review."""
    from welfare.models import WelfareObservation, WelfareSeverity

    pending = WelfareObservation.objects.filter(
        severity__in=[WelfareSeverity.HIGH, WelfareSeverity.CRITICAL],
        hod_status__in=['pending', 'in_progress'],
    ).select_related('student', 'submitted_by')

    ct = ContentType.objects.get_for_model(WelfareObservation)
    created = 0

    for obs in pending:
        from academics.models import GradeClass, Department
        gc = GradeClass.objects.filter(name=obs.student.class_name).first()
        dept = gc.department if gc else Department.ECD
        hod_role = UserRole.ECD_HOD if dept == Department.ECD else UserRole.PRIMARY_HOD
        hods = User.objects.filter(role=hod_role, is_active=True)

        for hod in hods:
            if not _task_exists('welfare_alert', hod, ct, obs.pk):
                Task.objects.create(
                    task_type='welfare_alert',
                    title=f'Welfare alert: {obs.student.first_name} — {obs.get_severity_display()}',
                    description=f'{"CRITICAL" if obs.severity == "critical" else "HIGH"} welfare '
                                f'observation for {obs.student.first_name} {obs.student.last_name} '
                                f'({obs.student.class_name}).',
                    assigned_to=hod,
                    created_by=obs.submitted_by,
                    due_date=_due_tomorrow(),
                    priority='critical' if obs.severity == 'critical' else 'high',
                    content_type=ct,
                    object_id=obs.pk,
                    metadata={
                        'student_name': f'{obs.student.first_name} {obs.student.last_name}',
                        'student_id': obs.student.pk,
                        'class_name': obs.student.class_name,
                        'severity': obs.severity,
                        'module_url': f'/welfare/{obs.pk}/',
                    },
                    actions_available=['review', 'contact_parent', 'mark_complete'],
                )
                created += 1

    return created


def seed_leave_request_tasks():
    """Generate tasks for pending leave requests."""
    from hr.models import LeaveRequest, LeaveStatus

    pending = LeaveRequest.objects.filter(status=LeaveStatus.PENDING)
    hos_users = User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True)
    ct = ContentType.objects.get_for_model(LeaveRequest)
    created = 0

    for lr in pending.select_related('staff'):
        for hos in hos_users:
            if not _task_exists('compliance_check', hos, ct, lr.pk):
                Task.objects.create(
                    task_type='compliance_check',
                    title=f'Leave request: {lr.staff.full_name} ({lr.total_days} days)',
                    description=f'{lr.staff.full_name} requests {lr.get_leave_type_display()} '
                                f'from {lr.start_date} to {lr.end_date}.',
                    assigned_to=hos,
                    due_date=_due_tomorrow(),
                    priority='high',
                    content_type=ct,
                    object_id=lr.pk,
                    metadata={
                        'staff_name': lr.staff.full_name,
                        'start_date': str(lr.start_date),
                        'end_date': str(lr.end_date),
                        'days': lr.total_days,
                        'module_url': '/hr/leave/',
                    },
                    actions_available=['approve', 'reject'],
                )
                created += 1

    return created


def seed_payroll_tasks():
    """Generate tasks for payroll runs pending HOS approval."""
    from hr.models import PayrollRun, PayrollStatus

    pending = PayrollRun.objects.filter(status=PayrollStatus.PENDING_APPROVAL)
    hos_users = User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True)
    ct = ContentType.objects.get_for_model(PayrollRun)
    created = 0

    for pr in pending:
        for hos in hos_users:
            if not _task_exists('compliance_check', hos, ct, pr.pk):
                Task.objects.create(
                    task_type='compliance_check',
                    title=f'Approve payroll: {pr.period_name}',
                    description=f'Payroll for {pr.period_name} submitted for approval. '
                                f'Net pay: TZS {pr.total_net_pay:,.0f}.',
                    assigned_to=hos,
                    due_date=_due_tomorrow(),
                    priority='critical',
                    content_type=ct,
                    object_id=pr.pk,
                    metadata={
                        'period_name': pr.period_name,
                        'total_net_pay': float(pr.total_net_pay or 0),
                        'module_url': f'/hr/payroll/{pr.pk}/',
                    },
                    actions_available=['approve', 'reject'],
                )
                created += 1

    return created


def seed_fee_structure_tasks():
    """Generate task if no fee structures exist for current term."""
    from academics.models import Term
    from finance.models import FeeStructure

    current_term = Term.objects.filter(is_locked=False).first()
    if not current_term:
        return 0

    existing = FeeStructure.objects.filter(term=current_term).count()
    if existing > 0:
        return 0

    finance_users = User.objects.filter(
        role__in=[UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN], is_active=True
    )
    created = 0

    for user in finance_users:
        if not _task_exists('fee_verification', user):
            Task.objects.create(
                task_type='fee_verification',
                title=f'Set up fee structures — {current_term.name}',
                description=f'No fee structures configured for {current_term.name}. '
                            f'Set up fees for all classes.',
                assigned_to=user,
                due_date=_due_this_week(),
                priority='critical',
                metadata={
                    'term_name': current_term.name,
                    'module_url': '/finance/fee-structure/',
                },
                actions_available=['mark_in_progress', 'mark_complete'],
            )
            created += 1

    return created


def seed_attendance_tasks():
    """
    Generate tasks for students with low attendance (< 85%).
    Checks attendance for the current term.
    """
    from attendance.models import AttendanceEntry, AttendanceStatus
    from academics.models import Term
    from students.models import Student

    term = Term.objects.filter(is_locked=False).first()
    if not term or not term.start_date:
        return 0

    today = timezone.now().date()
    start = term.start_date
    end = today

    students = Student.objects.filter(is_archived=False, status='active')
    created = 0

    for student in students:
        entries = AttendanceEntry.objects.filter(
            student=student, date__range=[start, end]
        )
        total = entries.count()
        if total < 5:
            continue  # Not enough data

        present = entries.filter(
            status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE]
        ).count()
        rate = (present / total) * 100

        if rate >= 85:
            continue

        # Find class teacher
        from hr.models import TeacherClassAssignment
        assignment = TeacherClassAssignment.objects.filter(
            grade_class__name=student.class_name,
            term=term,
            is_class_teacher=True,
        ).select_related('teacher__user').first()

        if not assignment:
            continue

        teacher_user = assignment.teacher.user
        ct = ContentType.objects.get_for_model(Student)

        if not _task_exists('attendance_alert', teacher_user, ct, student.pk):
            Task.objects.create(
                task_type='attendance_alert',
                title=f'Attendance alert: {student.first_name} — {rate:.0f}%',
                description=f'{student.first_name} {student.last_name}\'s attendance '
                            f'is {rate:.0f}%, below the 85% threshold.',
                assigned_to=teacher_user,
                due_date=_due_tomorrow(),
                priority='high',
                content_type=ct,
                object_id=student.pk,
                metadata={
                    'student_name': f'{student.first_name} {student.last_name}',
                    'student_id': student.pk,
                    'class_name': student.class_name,
                    'attendance_rate': round(rate, 1),
                    'module_url': f'/students/{student.pk}/',
                },
                actions_available=['contact_parent', 'mark_complete'],
            )
            created += 1

    return created
