from datetime import date, datetime, timedelta
import json
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Avg, Count, ProtectedError, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import TemplateView
from django.utils import timezone

from core.permissions import RoleRequiredMixin
from users.models import UserRole

from academics.models import LessonPlan, LessonPlanStatus, Term, GradeClass, Department, ReportCard, ReportCardStatus, ExamScore
from admissions.models import Applicant, ApplicantStatus
from attendance.models import AttendanceEntry, AttendanceStatus
from audit.models import AuditLog
from discipline.models import DisciplineIncident
from finance.models import Invoice, Payment
from hr.models import PayrollRun, TeacherClassAssignment
from students.models import Student, StudentStatus, StudentGuardian
from users.models import User, UserRole
from timetable.models import TimetableSlot, Weekday
from welfare.models import WelfareObservation, WelfareSeverity
from events.models import CalendarEvent
from core.teacher_context import is_ecd_teacher


# Privilege escalation prevention for bulk import (FRD NFR-SEC-004)
ROLE_RANK = {
    UserRole.SUPER_ADMIN: 6,
    UserRole.HEAD_OF_SCHOOL: 5,
    UserRole.PRIMARY_HOD: 4,
    UserRole.ECD_HOD: 4,
    UserRole.LOWER_SECONDARY_HOD: 4,
    UserRole.ADMIN_OFFICER: 3,
    UserRole.FINANCE_OFFICER: 3,
    UserRole.TEACHER: 2,
    UserRole.PARENT: 1,
}


def _user_initials(user):
    if user and user.is_authenticated:
        full = user.get_full_name()
        if full:
            parts = full.split()
            return (parts[0][0] + parts[-1][0]).upper() if len(parts) >= 2 else parts[0][:2].upper()
        return user.username[:2].upper()
    return "HX"


def _pipeline_stages(max_h=80):
    """Build pipeline stage counts for all 22 individual statuses.
    Optimized: single query with annotation instead of N separate counts."""
    from django.db.models import Count
    stage_groups = [
        {"label": "Inquiry",    "keys": [ApplicantStatus.INQUIRY_RECEIVED],              "color": "#BFCFE9"},
        {"label": "Meeting Set","keys": [ApplicantStatus.MEETING_SCHEDULED],            "color": "#BFCFE9"},
        {"label": "Held",       "keys": [ApplicantStatus.MEETING_COMPLETED],            "color": "#BFCFE9"},
        {"label": "Declined",   "keys": [ApplicantStatus.DECLINED_AT_MEETING],          "color": "#EF4444"},
        {"label": "Assessment", "keys": [ApplicantStatus.ASSESSMENT_PENDING],           "color": "#1A56C4"},
        {"label": "Fee Paid",   "keys": [ApplicantStatus.ASSESSMENT_FEE_PAID],          "color": "#1A56C4"},
        {"label": "Logistics",  "keys": [ApplicantStatus.ASSESSMENT_CONFIRMED],         "color": "#1A56C4"},
        {"label": "Complete",   "keys": [ApplicantStatus.ASSESSMENT_COMPLETED],         "color": "#1A56C4"},
        {"label": "Report",     "keys": [ApplicantStatus.REPORT_PENDING],               "color": "#1A56C4"},
        {"label": "Failed",     "keys": [ApplicantStatus.ASSESSMENT_FAILED],            "color": "#EF4444"},
        {"label": "HOS Review", "keys": [ApplicantStatus.HOS_REVIEW],                   "color": "#1A56C4"},
        {"label": "HOS Review", "keys": [ApplicantStatus.HOS_DECISION],                 "color": "#1A56C4"},
        {"label": "Admitted",   "keys": [ApplicantStatus.ADMITTED],                     "color": "#FBBC05"},
        {"label": "Conditional","keys": [ApplicantStatus.CONDITIONAL],                  "color": "#FBBC05"},
        {"label": "Form",       "keys": [ApplicantStatus.FORM_SUBMITTED],               "color": "#FBBC05"},
        {"label": "Invoice",    "keys": [ApplicantStatus.INVOICE_GENERATED],            "color": "#FBBC05"},
        {"label": "Paid",       "keys": [ApplicantStatus.INVOICE_PAID],                 "color": "#FBBC05"},
        {"label": "Enrolled",   "keys": [ApplicantStatus.ENROLLED],                     "color": "#22C55E"},
        {"label": "Flagged",    "keys": [ApplicantStatus.FLAGGED_FOR_REVIEW],           "color": "#22C55E"},
        {"label": "Waitlisted", "keys": [ApplicantStatus.WAITLISTED],                   "color": "#2563EB"},
        {"label": "Denied",     "keys": [ApplicantStatus.DENIED],                       "color": "#EF4444"},
        {"label": "Withdrawn",  "keys": [ApplicantStatus.WITHDRAWN],                    "color": "#EF4444"},
    ]
    all_keys = []
    key_to_group = {}
    for i, group in enumerate(stage_groups):
        for k in group["keys"]:
            all_keys.append(k)
            key_to_group[k] = i

    status_counts = (
        Applicant.objects.filter(status__in=all_keys)
        .values("status")
        .annotate(cnt=Count("id"))
    )
    counts = [0] * len(stage_groups)
    for row in status_counts:
        idx = key_to_group.get(row["status"])
        if idx is not None:
            counts[idx] += row["cnt"]

    results = []
    for i, group in enumerate(stage_groups):
        count = counts[i]
        results.append({
            "label": group["label"],
            "count": count,
            "color": group["color"],
            "bar_height": min(max_h, 5 + (count * 10)),
        })
    return results


def _recent_audit():
    return AuditLog.objects.all().select_related("actor")[:6]


from core.utils import is_school_day  # noqa: F401 — re-exported for callers within this module


def _unconfirmed_students_qs(today, limit=50):
    """FR-ATT-006: Return active students with no confirmed attendance today.
    Used by Admin and HOS dashboards (school-wide scope).
    Returns an empty list on non-school days."""
    if not is_school_day(today):
        return []

    return [
        {"student": s, "class_name": s.class_name}
        for s in Student.objects.filter(
            status=StudentStatus.ACTIVE,
            is_archived=False,
        ).exclude(
            attendance_entries__date=today,
            attendance_entries__status__in=[
                AttendanceStatus.PRESENT,
                AttendanceStatus.LATE,
                AttendanceStatus.ABSENT,
                AttendanceStatus.EXCUSED,
            ],
        ).order_by("class_name", "last_name", "first_name")[:limit]
    ]


def _base_ctx(user, now=None, today=None):
    if not now: now = datetime.now()
    if not today: today = timezone.now().date()

    from academics.utils import get_current_term
    term = get_current_term()
    term_label = term.name if term else "No Active Term"

    return {
        "user_initials": _user_initials(user),
        "today_display": today.strftime("%A, %d %B %Y"),
        "now": now,
        "hour": now.hour,
        "term_label": term_label,
    }


class DashboardRouterView(RoleRequiredMixin, TemplateView):
    allowed_roles = [
        UserRole.HEAD_OF_SCHOOL,
        UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD,
        UserRole.LOWER_SECONDARY_HOD,
        UserRole.ADMIN_OFFICER,
        UserRole.FINANCE_OFFICER,
        UserRole.TEACHER,
        UserRole.SUPER_ADMIN,
        UserRole.PARENT,
    ]
    """
    Smart dashboard router — shows role-specific view.
    Acceptance criterion #1: 8 roles → correct dashboards.
    """
    login_url = "/accounts/login/"

    def get(self, request, *args, **kwargs):
        role = request.user.role
        if role == UserRole.PARENT:
            from django.shortcuts import redirect
            return redirect("parent_portal:dashboard")
        role_to_view = {
            UserRole.HEAD_OF_SCHOOL: HOSDashboardView,
            UserRole.PRIMARY_HOD: PrimaryHODDashboardView,
            UserRole.ECD_HOD: ECDHODDashboardView,
            UserRole.LOWER_SECONDARY_HOD: PrimaryHODDashboardView,
            UserRole.ADMIN_OFFICER: AdminDashboardView,
            UserRole.FINANCE_OFFICER: FinanceDashboardProxyView,
            UserRole.TEACHER: TeacherDashboardView,
            UserRole.SUPER_ADMIN: SuperAdminDashboardView,
        }
        view_cls = role_to_view.get(role, SuperAdminDashboardView)
        return view_cls.as_view()(request, *args, **kwargs)

class SeedDatabaseView(RoleRequiredMixin, TemplateView):
    """One-click view to seed the database if it's empty."""
    template_name = "dashboards/seed_db.html"
    allowed_roles = [UserRole.SUPER_ADMIN]

    def post(self, request, *args, **kwargs):
        import sys
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        sys.path.append(base_dir)
        try:
            from seed_comprehensive_2026 import seed
            seed()
            from django.contrib import messages
            messages.success(request, "Database seeded successfully! All classes and templates are now available.")
        except Exception as e:
            from django.contrib import messages
            messages.error(request, f"Failed to seed database: {str(e)}")
            
        return redirect("/")


# --------------------------------------------------------------------------- #
# Role Dashboards                                                               #
# --------------------------------------------------------------------------- #

class SuperAdminDashboardView(RoleRequiredMixin, TemplateView):
    """System Administration Panel — FRD Section 2."""
    template_name = "dashboards/superadmin.html"
    allowed_roles = [UserRole.SUPER_ADMIN]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        
        # 1. View-as diagnostic users
        view_as_users = []
        for role_val, role_label in UserRole.choices:
            if role_val == UserRole.SUPER_ADMIN: continue
            u = User.objects.filter(role=role_val).first()
            if u:
                view_as_users.append({
                    "id": u.id,
                    "role_label": role_label,
                    "name": u.get_full_name() or u.username,
                    "role": role_val,
                    "initials": (u.first_name[0] + u.last_name[0]).upper() if u.first_name and u.last_name else u.username[:2].upper()
                })
        ctx["view_as_users"] = view_as_users

        # 2. System Health Metrics
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        total_logins = AuditLog.objects.filter(action_type="LOGIN", created_at__gte=now - timedelta(days=30)).count()
        failed_logins = AuditLog.objects.filter(action_type="LOGIN_FAILED", created_at__gte=now - timedelta(days=30)).count()
        ctx["uptime"] = str(round((total_logins / max(total_logins + failed_logins, 1)) * 100, 1))
        ctx["incidents"] = AuditLog.objects.filter(action_type__icontains="error", created_at__gte=now - timedelta(days=7)).count()
        ctx["pending_audit"] = AuditLog.objects.filter(action_type__icontains="error").count()

        # 3. Recent Audit Log
        ctx["recent_audit"] = AuditLog.objects.all().select_related("actor")[:10]

        # 4. Action Queue items
        ctx["action_queue"] = [
            {"label": "Manage user accounts & roles", "sub": "Add, deactivate, or reassign system users", "color": "blue", "url": "/accounts/list/"},
            {"label": "Academic year configuration", "sub": "Set up terms, weeks, and grade structure", "color": "blue", "url": "/settings/?tab=academic_year"},
            {"label": "Attendance module", "sub": "Review and corrections (Admin)", "color": "amber", "url": "/attendance/"},
            {"label": "System settings & permissions", "sub": "Thresholds, notifications, API keys", "color": "blue", "url": "/admin/"},
        ]

        ctx.update(_base_ctx(user))
        return ctx


class HOSDashboardView(RoleRequiredMixin, TemplateView):
    """Head of School Dashboard — FRD Section 14."""
    template_name = "dashboards/hos.html"
    allowed_roles = [UserRole.HEAD_OF_SCHOOL]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = date.today()
        user = self.request.user
        
        # 1. School overview metrics — batch into fewer queries
        from django.db.models import Count, Q, Case, When, Value, IntegerField
        active_count = Student.objects.filter(status=StudentStatus.ACTIVE).count()
        present_today = AttendanceEntry.objects.filter(date=today, status=AttendanceStatus.PRESENT).count()
        att_pct = round(present_today / active_count * 100) if active_count else 0

        # Lesson plan counts in one query
        plan_stats = LessonPlan.objects.aggregate(
            total=Count('id'),
            approved=Count('id', filter=Q(status=LessonPlanStatus.APPROVED)),
            pending=Count('id', filter=Q(status=LessonPlanStatus.SUBMITTED)),
        )
        total_plans = plan_stats['total']
        plans_approved = plan_stats['approved']
        plans_pending = plan_stats['pending']

        reports_awaiting = ReportCard.objects.filter(status=ReportCardStatus.PENDING_SIGN_OFF).count()

        # Simple fee calculation for dashboard - FR-HOS-FIN
        try:
            today = timezone.now().date()
            from academics.utils import get_current_term
            current_term = get_current_term()
                
            if current_term:
                term_invoices = Invoice.objects.filter(term=current_term)
                term_billed = term_invoices.aggregate(t=Sum("total_due"))["t"] or 0
                term_paid = Payment.objects.filter(invoice__in=term_invoices, is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
                fee_collection_pct = round((term_paid / term_billed * 100)) if term_billed > 0 else 0
            else:
                fee_collection_pct = 0
                
            overdue_accounts = Invoice.objects.filter(status="overdue").count()
        except Exception:
            fee_collection_pct = 0
            overdue_accounts = 0

        # 2. Upcoming events
        try:
            from events.models import CalendarEvent
            upcoming_events = CalendarEvent.objects.filter(
                start_date__gte=today, is_published=True
            ).order_by("start_date")[:4]
        except Exception:
            upcoming_events = []

        # 3. Staff presence — real StaffAttendanceEntry queries
        from attendance.models import StaffAttendanceEntry
        all_staff = User.objects.exclude(role__in=[UserRole.PARENT, UserRole.SUPER_ADMIN])
        expected_staff = all_staff.count()
        staff_entries_today = StaffAttendanceEntry.objects.filter(
            date=today,
            staff__user__in=all_staff,
        ).select_related("staff__user")
        staff_status_map = {}
        for entry in staff_entries_today:
            staff_status_map[entry.staff_id] = entry.status
        present_staff = sum(
            1 for s in staff_status_map.values()
            if s in (AttendanceStatus.PRESENT, AttendanceStatus.LATE)
        )
        absent_staff = sum(
            1 for s in staff_status_map.values()
            if s == AttendanceStatus.ABSENT
        )
        unconfirmed_staff = expected_staff - present_staff - absent_staff
        if unconfirmed_staff < 0:
            unconfirmed_staff = 0
        
        # 4. Attendance by class — single query with annotation (eliminates N+1)
        class_active_counts = dict(
            Student.objects.filter(status=StudentStatus.ACTIVE)
            .values('class_name')
            .annotate(cnt=Count('id'))
            .values_list('class_name', 'cnt')
        )
        class_present_counts = dict(
            AttendanceEntry.objects.filter(date=today, status=AttendanceStatus.PRESENT)
            .values('student__class_name')
            .annotate(cnt=Count('id'))
            .values_list('student__class_name', 'cnt')
        )
        class_late_counts = dict(
            AttendanceEntry.objects.filter(date=today, status=AttendanceStatus.LATE)
            .values('student__class_name')
            .annotate(cnt=Count('id'))
            .values_list('student__class_name', 'cnt')
        )
        class_attendance = []
        for cname, c_total in class_active_counts.items():
            if c_total > 0:
                c_checked_in = class_present_counts.get(cname, 0) + class_late_counts.get(cname, 0)
                c_pct = round((c_checked_in / c_total) * 100)
                class_attendance.append({"name": cname, "pct": c_pct})

        # 5. Lesson plan compliance by department — single annotated query
        dept_plan_stats = (
            LessonPlan.objects.filter(
                class_name__in=GradeClass.objects.values_list('name', flat=True)
            )
            .values('class_name')
            .annotate(
                approved=Count('id', filter=Q(status=LessonPlanStatus.APPROVED)),
                total=Count('id'),
            )
        )
        # Map classes to departments
        class_to_dept = dict(
            GradeClass.objects.values_list('name', 'department')
        )
        dept_compliance_data = {}
        for dept_key, dept_label in [
            (Department.PRIMARY, "Primary"),
            (Department.LOWER_SECONDARY, "Lower Secondary"),
        ]:
            dept_compliance_data[dept_key] = {"name": dept_label, "approved": 0, "total": 0}
        for row in dept_plan_stats:
            dept = class_to_dept.get(row['class_name'])
            if dept in dept_compliance_data:
                dept_compliance_data[dept]['approved'] += row['approved']
                dept_compliance_data[dept]['total'] += row['total']
        dept_compliance = []
        for dept_key, dept_label in [
            (Department.PRIMARY, "Primary"),
            (Department.LOWER_SECONDARY, "Lower Secondary"),
        ]:
            d = dept_compliance_data[dept_key]
            pct = round((d['approved'] / d['total'] * 100)) if d['total'] else 0
            dept_compliance.append({"name": d['name'], "pct": pct, "note": None})

        # 6. Pipeline stages
        pipeline_stages = _pipeline_stages()

        # 7. Recent audit (formatted for template)
        recent_audit = [
            {
                "label": entry.description or entry.action_type,
                "actor": entry.actor.get_username() if entry.actor else "System",
                "when": entry.created_at.strftime("%d %b, %H:%M") if entry.created_at else "",
                "severity": "warn" if "error" in (entry.action_type or "").lower() or "fail" in (entry.action_type or "").lower() else "info",
            }
            for entry in AuditLog.objects.select_related("actor").order_by("-created_at")[:6]
        ]

        # FR-ADM-018: Admission actions for HOS
        from admissions.services import get_critical_actions
        admission_actions = get_critical_actions()
        critical_admission_actions = [a for a in admission_actions if a["severity"] == "high"]

        # FR-ADM-018: HOS decision queue — applicants awaiting HOS decision
        hos_decision_qs = Applicant.objects.filter(
            status=ApplicantStatus.HOS_DECISION,
        ).select_related("assessment")[:10]
        hos_decision_queue = []
        for app in hos_decision_qs:
            assessment = getattr(app, "assessment", None)
            hos_decision_queue.append({
                "applicant": app,
                "assessment": assessment,
                "result_display": assessment.get_result_display() if assessment and assessment.result else "—",
                "hod_comments": (assessment.hod_comments or "")[:120] if assessment else "",
            })

        # Critical welfare cases needing HOS attention
        critical_welfare_count = WelfareObservation.objects.filter(
            severity=WelfareSeverity.CRITICAL,
        ).exclude(
            hod_status="resolved",
        ).count()

        # FR-WEL-008: Aggregate welfare summary per ECD class for current term
        from collections import Counter
        ecd_welfare_by_class = {}
        ecd_class_names = list(GradeClass.objects.filter(
            department=Department.ECD
        ).values_list("name", flat=True))
        if current_term:
            term_start = current_term.start_date
            term_end = current_term.end_date
        else:
            term_start = today.replace(month=1, day=1)
            term_end = today
        for cname in ecd_class_names:
            class_obs = WelfareObservation.objects.filter(
                student__class_name=cname,
                observation_date__gte=term_start,
                observation_date__lte=term_end,
            )
            total = class_obs.count()
            if total == 0:
                continue
            severities = dict(Counter(class_obs.values_list("severity", flat=True)))
            concern_types = dict(Counter(class_obs.values_list("concern_type", flat=True)))
            open_count = class_obs.filter(hod_status__in=["pending", "in_progress"]).count()
            ecd_welfare_by_class[cname] = {
                "total": total,
                "severities": severities,
                "concern_types": concern_types,
                "open": open_count,
            }

        # School-wide attendance breakdown for HOS cards (reuses existing variables)
        total_active = active_count
        present_today_count = present_today
        late_today_count = AttendanceEntry.objects.filter(date=today, status=AttendanceStatus.LATE).count()
        absent_today_count = AttendanceEntry.objects.filter(date=today, status=AttendanceStatus.ABSENT).count()
        checked_in_today = present_today_count + late_today_count
        unconfirmed_today = total_active - checked_in_today - absent_today_count

        # FR-ATT-006: School-wide unconfirmed student name-list for HOS
        unconfirmed_students = _unconfirmed_students_qs(today)

        # Behaviour incidents requiring HOS visibility
        behaviour_base = DisciplineIncident.objects.filter(
            escalated=True,
            status__in=['pending_review', 'under_investigation'],
        )
        behaviour_pending_count = behaviour_base.count()
        high_critical_count = behaviour_base.filter(severity__in=['high', 'critical']).count()
        escalated_behaviour = behaviour_base.select_related('student', 'reported_by', 'reviewed_by').order_by('-created_at')[:6]

        # FR-ACAD-010b: Term comparison indicators (up/down/flat)
        comparison = {}
        if current_term:
            prev_term = Term.objects.filter(
                academic_year=current_term.academic_year,
                start_date__lt=current_term.start_date,
                is_locked=True,
            ).order_by('-start_date').first()
            if prev_term:
                # Previous term attendance rate
                prev_term_students = Student.objects.filter(status=StudentStatus.ACTIVE)
                prev_att_entries = AttendanceEntry.objects.filter(
                    date__range=[prev_term.start_date, prev_term.end_date],
                    status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE],
                ) if prev_term.start_date and prev_term.end_date else AttendanceEntry.objects.none()
                prev_total_entries = AttendanceEntry.objects.filter(
                    date__range=[prev_term.start_date, prev_term.end_date],
                ) if prev_term.start_date and prev_term.end_date else AttendanceEntry.objects.none()
                prev_present = prev_att_entries.count() if prev_term.start_date else 0
                prev_total = prev_total_entries.count() if prev_term.start_date else 0
                prev_att_pct = round(prev_present / prev_total * 100) if prev_total else 0
                
                att_diff = att_pct - prev_att_pct
                comparison["attendance"] = {
                    "current": att_pct, "previous": prev_att_pct,
                    "direction": "up" if att_diff > 1 else "down" if att_diff < -1 else "flat",
                    "diff": round(att_diff, 1),
                }
                
                # Previous term fee collection
                prev_invoices = Invoice.objects.filter(term=prev_term)
                prev_billed = prev_invoices.aggregate(t=Sum("total_due"))["t"] or 0
                prev_paid = Payment.objects.filter(invoice__in=prev_invoices, is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
                prev_fee_pct = round(prev_paid / prev_billed * 100) if prev_billed > 0 else 0
                fee_diff = fee_collection_pct - prev_fee_pct
                comparison["fees"] = {
                    "current": fee_collection_pct, "previous": prev_fee_pct,
                    "direction": "up" if fee_diff > 1 else "down" if fee_diff < -1 else "flat",
                    "diff": round(fee_diff, 1),
                }
                
                # Previous term lesson plan compliance
                prev_dept_plan_stats = (
                    LessonPlan.objects.filter(
                        class_name__in=GradeClass.objects.values_list('name', flat=True)
                    )
                    .values('class_name')
                    .annotate(
                        approved=Count('id', filter=Q(status=LessonPlanStatus.APPROVED)),
                        total=Count('id'),
                    )
                )
                # We only have current term data for plans; use total_approved ratio
                plan_pct = round(plans_approved / total_plans * 100) if total_plans else 0
                # For comparison, use a simplified previous term estimate
                comparison["lesson_plans"] = {
                    "current": plan_pct, "previous": None,
                    "direction": "flat",
                    "diff": 0,
                }

        # ── Academic Performance (FRD Section 14.1, FR-ACAD-018) ──────────
        from academics.models import ScoreStatus, ExamTypeConfiguration
        from academics.utils import get_current_term
        perf_term = get_current_term()

        school_avg = 0
        school_grade = "-"
        pass_rate = 0
        d_count = 0
        e_count = 0
        class_perf_list = []
        dept_perf_list = []
        flagged_class_count = 0
        at_risk_students = []
        perf_trend = None

        if perf_term:
            active_students_qs = Student.objects.filter(status=StudentStatus.ACTIVE, is_archived=False)

            all_scores_qs = ExamScore.objects.filter(
                term=perf_term,
                status=ScoreStatus.APPROVED,
            ).select_related('student').order_by('student_id', 'subject_name', 'exam_type')

            exam_types = list(ExamTypeConfiguration.objects.filter(is_active=True))
            exam_weights = {et.code: float(et.weight_percentage) for et in exam_types}

            # ── Student-level weighted averages ──
            student_groups = {}
            for sc in all_scores_qs:
                student_groups.setdefault(sc.student_id, {}).setdefault(sc.subject_name, []).append(sc)

            student_overall_avgs = {}
            for sid, subj_dict in student_groups.items():
                subj_avgs = []
                for subj, scores in subj_dict.items():
                    wsum = tw = 0
                    for sc in scores:
                        w = exam_weights.get(sc.exam_type, 0)
                        wsum += float(sc.score) * w
                        tw += w
                    if tw > 0:
                        subj_avgs.append(wsum / tw)
                if subj_avgs:
                    student_overall_avgs[sid] = sum(subj_avgs) / len(subj_avgs)

            total_with_scores = len(student_overall_avgs)
            if total_with_scores:
                d_count = sum(1 for a in student_overall_avgs.values() if 50 <= a < 60)
                e_count = sum(1 for a in student_overall_avgs.values() if a < 50)
                passed_count = sum(1 for a in student_overall_avgs.values() if a >= 60)
                pass_rate = round(passed_count / total_with_scores * 100)
                school_avg = round(sum(student_overall_avgs.values()) / total_with_scores, 1)
                school_grade = "B" if school_avg >= 70 else "C" if school_avg >= 60 else "D" if school_avg >= 50 else "E"

            # ── Per-class averages ──
            class_agg = (
                ExamScore.objects.filter(term=perf_term, status=ScoreStatus.APPROVED)
                .values('student__class_name')
                .annotate(avg=Avg('score'))
            )
            for row in class_agg:
                avg = round(float(row['avg']), 1)
                class_perf_list.append({
                    "name": row['student__class_name'],
                    "avg": avg,
                    "grade": "B" if avg >= 70 else "C" if avg >= 60 else "D" if avg >= 50 else "E",
                    "flagged": avg < 60,
                })
            class_perf_list.sort(key=lambda x: x['avg'])
            flagged_class_count = sum(1 for c in class_perf_list if c['flagged'])

            # ── Department performance ──
            class_names_by_dept = {}
            for gc in GradeClass.objects.all():
                class_names_by_dept.setdefault(gc.department, []).append(gc.name)
            for dept_key, dept_label in [
                (Department.PRIMARY, "Primary"),
                (Department.ECD, "ECD"),
                (Department.LOWER_SECONDARY, "Lower Secondary"),
            ]:
                cnames = class_names_by_dept.get(dept_key, [])
                if cnames:
                    agg = ExamScore.objects.filter(
                        student__class_name__in=cnames, term=perf_term, status=ScoreStatus.APPROVED,
                    ).aggregate(avg=Avg('score'))
                    davg = round(float(agg['avg'] or 0), 1)
                else:
                    davg = 0
                dept_perf_list.append({
                    "name": dept_label,
                    "avg": davg,
                    "grade": "B" if davg >= 70 else "C" if davg >= 60 else "D" if davg >= 50 else "E" if davg > 0 else "-",
                })

            # ── At-risk students (per-subject D or below) ──
            students_map = {s.pk: s for s in active_students_qs}
            for sid, subj_dict in student_groups.items():
                for subj_name, subj_scores in subj_dict.items():
                    wsum = tw = 0
                    for sc in subj_scores:
                        w = exam_weights.get(sc.exam_type, 0)
                        wsum += float(sc.score) * w
                        tw += w
                    if tw > 0:
                        avg = wsum / tw
                        if avg < 60:
                            at_risk_students.append({
                                "student": students_map.get(sid),
                                "subject": subj_name,
                                "average": round(avg, 1),
                                "flag_color": "red" if avg < 50 else "amber",
                            })

            # ── Trend vs previous term ──
            prev_term = (
                Term.objects.filter(
                    academic_year=perf_term.academic_year,
                    start_date__lt=perf_term.start_date,
                    is_locked=True,
                )
                .order_by('-start_date')
                .first()
            )
            if prev_term:
                prev_avg = (
                    ExamScore.objects.filter(term=prev_term, status=ScoreStatus.APPROVED)
                    .aggregate(Avg('score'))['score__avg']
                )
                if prev_avg:
                    p = round(float(prev_avg), 1)
                    diff = school_avg - p
                    perf_trend = {
                        "direction": "up" if diff > 0.5 else "down" if diff < -0.5 else "flat",
                        "diff": round(diff, 1),
                        "previous": p,
                    }

        ctx.update(_base_ctx(user))
        ctx.update({
            "active_students": active_count,
            "att_pct": att_pct,
            "present_today": present_today,
            "unconfirmed_students": unconfirmed_students,
            "unconfirmed_count": len(unconfirmed_students),
            "checked_in_today": checked_in_today,
            "absent_today": absent_today_count,
            "late_today": late_today_count,
            "unconfirmed_today": unconfirmed_today,
            "plans_approved": plans_approved,
            "plans_pending": plans_pending,
            "plans_pending_count": plans_pending,
            "plans_ratio": f"{plans_approved}/{total_plans}" if total_plans else "0/0",
            "reports_awaiting": reports_awaiting,
            "fee_collection_pct": fee_collection_pct,
            "overdue_accounts": overdue_accounts,
            "upcoming_events": upcoming_events,
            "expected_staff": expected_staff,
            "present_staff": present_staff,
            "absent_staff": absent_staff,
            "unconfirmed_staff": unconfirmed_staff,
            "class_attendance": class_attendance[:5],
            "alert_count": plans_pending + reports_awaiting,
            "dept_compliance": dept_compliance,
            "pipeline_stages": pipeline_stages,
            "critical_welfare_count": critical_welfare_count,
            "ecd_welfare_by_class": ecd_welfare_by_class,
            "escalated_behaviour": escalated_behaviour,
            "behaviour_pending_count": behaviour_pending_count,
            "high_critical_count": high_critical_count,
            "recent_audit": recent_audit,
            "comparison": comparison,
            "admission_actions": admission_actions[:5],
            "critical_admission_actions": critical_admission_actions,
            "hos_decision_queue": hos_decision_queue,
            "hos_decision_count": len(hos_decision_queue),
            # Academic performance
            "school_avg": school_avg,
            "school_grade": school_grade,
            "pass_rate": pass_rate,
            "d_count": d_count,
            "e_count": e_count,
            "total_with_scores": total_with_scores,
            "class_perf_list": class_perf_list,
            "flagged_class_count": flagged_class_count,
            "dept_perf_list": dept_perf_list,
            "at_risk_students": at_risk_students,
            "perf_trend": perf_trend,
        })
        return ctx


class PrimaryHODDashboardView(RoleRequiredMixin, TemplateView):
    allowed_roles = [UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD]
    """Primary Head of Department Dashboard — High Fidelity."""
    template_name = "dashboards/primary_hod.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        today = timezone.localdate()
        
        # 1. Base Context
        selected_date_str = self.request.GET.get("date")
        if selected_date_str:
            try:
                today = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
            except:
                today = timezone.localdate()
        else:
            today = timezone.localdate()

        ctx.update(_base_ctx(user, today=today))
        ctx["today"] = today
        ctx["soon_date"] = (today + timedelta(days=3)).strftime("%Y-%m-%d")
        from academics.utils import get_current_term
        term = get_current_term()
        
        # 2. Scope: Department classes based on HOD role
        if user.role == UserRole.LOWER_SECONDARY_HOD:
            dept_classes = GradeClass.objects.filter(department=Department.LOWER_SECONDARY).values_list('name', flat=True)
        elif user.role == UserRole.ECD_HOD:
            dept_classes = GradeClass.objects.filter(department=Department.ECD).values_list('name', flat=True)
        else:
            dept_classes = GradeClass.objects.filter(department=Department.PRIMARY).values_list('name', flat=True)
        
        # 3. Metrics
        plans_to_review = LessonPlan.objects.filter(class_name__in=dept_classes, status=LessonPlanStatus.SUBMITTED)
        ctx["plans_to_review_count"] = plans_to_review.count()
        
        behaviour_pending = DisciplineIncident.objects.filter(student__class_name__in=dept_classes, escalated=True)
        ctx["behaviour_pending_count"] = behaviour_pending.count()
        
        if term and term.end_date:
            days_to_deadline = (term.end_date - today).days
            ctx["report_deadline_days"] = max(0, days_to_deadline)
        else:
            ctx["report_deadline_days"] = 0

        # Week number from term start
        if term and term.start_date:
            week_num = ((today - term.start_date).days // 7) + 1
            ctx["current_week"] = week_num
        else:
            ctx["current_week"] = None

        # 4. Performance Pulse (real stats)
        dept_avg = ExamScore.objects.filter(student__class_name__in=dept_classes, term=term).aggregate(Avg('score'))['score__avg'] or 0

        # Compute trend vs previous term
        prev_term = Term.objects.filter(
            academic_year=term.academic_year if term else None,
            end_date__lt=term.start_date if term else None,
        ).order_by('-end_date').first() if term else None
        prev_avg = ExamScore.objects.filter(
            student__class_name__in=dept_classes, term=prev_term
        ).aggregate(Avg('score'))['score__avg'] if prev_term else None
        if dept_avg and prev_avg:
            diff = round(dept_avg - prev_avg, 1)
            trend = f"+{diff}%" if diff >= 0 else f"{diff}%"
        else:
            trend = "—"

        ctx["dept_performance"] = {
            "avg": round(dept_avg, 1) if dept_avg else 0,
            "grade": "B" if dept_avg >= 70 else ("C" if dept_avg >= 60 else "D"),
            "trend": f"{trend} vs previous term",
            "student_count": Student.objects.filter(class_name__in=dept_classes, is_archived=False).count()
        }

        # Flagged Class — find class with lowest average in current term
        class_avgs = []
        for gc_name in dept_classes:
            avg = ExamScore.objects.filter(
                student__class_name=gc_name, term=term
            ).aggregate(Avg('score'))['score__avg']
            if avg is not None:
                critical_count = ExamScore.objects.filter(
                    student__class_name=gc_name, term=term, score__lt=40
                ).count()
                class_avgs.append({"name": gc_name, "avg": round(avg, 1), "critical_count": critical_count})
        if class_avgs:
            worst = min(class_avgs, key=lambda x: x["avg"])
            ctx["flagged_class"] = worst
        else:
            ctx["flagged_class"] = None
        
        # 5. Staff Presence — from StaffAttendanceEntry
        from attendance.models import StaffAttendanceEntry
        from timetable.models import TimetableSlot
        from hr.models import StaffProfile
        if user.role == UserRole.LOWER_SECONDARY_HOD:
            dept_staff = User.objects.filter(
                staff_profile__in=StaffProfile.objects.filter(department=Department.LOWER_SECONDARY)
            )
        else:
            dept_staff = User.objects.filter(
                staff_profile__in=StaffProfile.objects.filter(department=Department.PRIMARY)
            )

        today_weekday = today.strftime("%a").lower()[:3]

        # Find which dept staff have any timetable slots (class assignments)
        all_class_teachers_primary = set(
            TimetableSlot.objects.filter(
                teacher__in=dept_staff,
                term=term,
                is_published=True,
            ).values_list("teacher_id", flat=True)
        ) if term else set()

        staff_with_classes_today = set(
            TimetableSlot.objects.filter(
                teacher__in=dept_staff,
                day_of_week=today_weekday,
                is_published=True,
                term=term,
            ).values_list("teacher_id", flat=True)
        ) if term else set()

        staff_entries = {
            e.staff_id: e
            for e in StaffAttendanceEntry.objects.filter(
                date=today,
                staff__user__in=dept_staff,
            ).select_related("staff__user")
        }
        staff_list = []
        absent_count = 0
        for s in dept_staff:
            entry = staff_entries.get(s.staff_profile.id) if hasattr(s, 'staff_profile') else None
            is_teacher = s.id in all_class_teachers_primary
            has_classes_today = s.id in staff_with_classes_today
            if entry:
                status = entry.get_status_display()
                check_in = entry.check_in_time.strftime("%I:%M %p").lstrip("0") if entry.check_in_time else None
            else:
                status = "Unconfirmed"
                check_in = None
                absent_count += 1
            staff_list.append({
                "name": s.get_full_name() or s.username,
                "role": s.staff_profile.job_title if hasattr(s, 'staff_profile') else "Teacher",
                "status": status,
                "check_in": check_in,
                "is_teacher": is_teacher,
                "has_classes_today": has_classes_today,
            })
        ctx["staff_attendance"] = staff_list[:6]
        ctx["staff_absent_count"] = absent_count
        
        # 6. Lesson Plan Queue
        ctx["plans_queue"] = plans_to_review.order_by('-updated_at')[:3]
        
        # 7. Compliance List (Staff Submission Status) — batched query
        teacher_pks = list(dept_staff.values_list('pk', flat=True))
        approved_teachers = set(
            LessonPlan.objects.filter(
                teacher_id__in=teacher_pks, status=LessonPlanStatus.APPROVED
            ).values_list('teacher_id', flat=True)
        )
        submitted_teachers = set(
            LessonPlan.objects.filter(
                teacher_id__in=teacher_pks, status=LessonPlanStatus.SUBMITTED
            ).values_list('teacher_id', flat=True)
        )
        returned_teachers = set(
            LessonPlan.objects.filter(
                teacher_id__in=teacher_pks, status=LessonPlanStatus.REVISION_REQUESTED
            ).values_list('teacher_id', flat=True)
        )
        compliance = []
        for s in dept_staff:
            if s.pk in approved_teachers:
                status = "Approved"
            elif s.pk in returned_teachers:
                status = "Returned"
            elif s.pk in submitted_teachers:
                status = "Submitted"
            else:
                status = "Missing"
            compliance.append({
                "name": s.get_full_name() or s.username,
                "status": status,
            })
        ctx["compliance_list"] = compliance[:5]

        # FR-ADM-018: Admission actions relevant to Primary HOD
        from admissions.services import get_critical_actions
        admission_actions = get_critical_actions()
        dept_grade_names = set(GradeClass.objects.filter(department=Department.LOWER_SECONDARY if user.role == UserRole.LOWER_SECONDARY_HOD else Department.PRIMARY).values_list("name", flat=True))
        relevant_actions = []
        for a in admission_actions:
            app_obj = a.get("applicant")
            grade = getattr(app_obj, "grade_applying_for", None) if app_obj else None
            if grade in dept_grade_names or a.get("type") in ("academic", "admin"):
                relevant_actions.append(a)
        ctx["admission_actions"] = relevant_actions[:5]

        # FR-ADM-018: HOS review queue — applicants awaiting HOS review in this department
        hos_review_qs = Applicant.objects.filter(
            status=ApplicantStatus.HOS_REVIEW,
            grade_applying_for__in=list(dept_grade_names),
        ).select_related("assessment")[:10]
        ctx["hos_review_queue"] = []
        for app in hos_review_qs:
            assessment = getattr(app, "assessment", None)
            ctx["hos_review_queue"].append({
                "applicant": app,
                "assessment": assessment,
                "result_display": assessment.get_result_display() if assessment and assessment.result else "—",
                "teacher_comments": (assessment.teacher_comments or "")[:120] if assessment else "",
            })
        ctx["hos_review_count"] = len(ctx["hos_review_queue"])

        # FR-ACAD-002: Aggregated compliance counts for widget
        ctx["compliance_counts"] = {
            "total": len(compliance),
            "approved": len([c for c in compliance if c["status"] == "Approved"]),
            "submitted": len([c for c in compliance if c["status"] == "Submitted"]),
            "returned": len([c for c in compliance if c["status"] == "Returned"]),
            "missing": len([c for c in compliance if c["status"] == "Missing"]),
        }

        # 8. Class Attendance Today — real data from AttendanceEntry
        class_att = []
        class_active_counts = dict(
            Student.objects.filter(class_name__in=dept_classes, status=StudentStatus.ACTIVE)
            .values('class_name')
            .annotate(cnt=Count('id'))
            .values_list('class_name', 'cnt')
        )
        class_present_counts = dict(
            AttendanceEntry.objects.filter(date=today, student__class_name__in=dept_classes, status=AttendanceStatus.PRESENT)
            .values('student__class_name')
            .annotate(cnt=Count('id'))
            .values_list('student__class_name', 'cnt')
        )
        class_late_counts = dict(
            AttendanceEntry.objects.filter(date=today, student__class_name__in=dept_classes, status=AttendanceStatus.LATE)
            .values('student__class_name')
            .annotate(cnt=Count('id'))
            .values_list('student__class_name', 'cnt')
        )
        for c in dept_classes:
            total = class_active_counts.get(c, 0)
            if total > 0:
                present = class_present_counts.get(c, 0)
                late = class_late_counts.get(c, 0)
                checked_in = present + late
                pct = round((checked_in / total) * 100)
                status = "green" if pct >= 90 else ("amber" if pct >= 80 else "red")
                class_att.append({"name": c, "pct": pct, "status": status})
        ctx["class_attendance"] = class_att[:5]
        
        # 9. Behaviour entries to review
        ctx["behaviour_review"] = behaviour_pending.order_by('-created_at')[:4]
        
        # 10. Upcoming Events
        try:
            from events.models import CalendarEvent
            ctx["upcoming_events"] = CalendarEvent.objects.filter(start_date__gte=today, is_published=True).order_by('start_date')[:4]
        except:
            ctx["upcoming_events"] = []

        # FR-ACAD-010: Subject × Class Heatmap (HOD Reports)
        # Build heatmap grid: subjects as rows, classes as columns, cells = avg score
        # Uses aggregated queries to avoid N+1 per-subject/per-class loops.
        from academics.models import ExamTypeConfiguration, ScoreStatus
        from django.db.models import Avg as DjangoAvg
        heatmap_data = []
        at_risk_students = []
        if term:
            # Get active exam types for weighted average
            exam_types = list(ExamTypeConfiguration.objects.filter(is_active=True))
            exam_weights = {et.code: float(et.weight_percentage) for et in exam_types}
            
            # Get all primary classes sorted
            primary_class_names = sorted(dept_classes)
            
            # Single aggregated query: avg score per subject × class
            heatmap_agg = (
                ExamScore.objects.filter(
                    student__class_name__in=primary_class_names,
                    term=term,
                    status=ScoreStatus.APPROVED,
                )
                .values('subject_name', 'student__class_name')
                .annotate(avg_score=DjangoAvg('score'))
                .order_by('subject_name', 'student__class_name')
            )
            # Build lookup: {(subject, class): avg_score}
            heatmap_lookup = {}
            for row in heatmap_agg:
                heatmap_lookup[(row['subject_name'], row['student__class_name'])] = round(float(row['avg_score']), 1)
            
            # Get unique subjects that have scores in primary classes this term
            subjects_with_scores = (
                ExamScore.objects.filter(
                    student__class_name__in=primary_class_names,
                    term=term,
                    status=ScoreStatus.APPROVED,
                )
                .values_list('subject_name', flat=True)
                .distinct()
                .order_by('subject_name')
            )
            
            for subject_name in subjects_with_scores:
                row = {"subject": subject_name, "classes": {}}
                for cls_name in primary_class_names:
                    avg = heatmap_lookup.get((subject_name, cls_name))
                    row["classes"][cls_name] = avg if avg is not None else None
                heatmap_data.append(row)
            
            # At-risk students: weighted avg below 60 (D or below) in ANY subject
            # UAT: "at-risk students averaging D or below in any subject"
            all_scores_qs = (
                ExamScore.objects.filter(
                    student__class_name__in=primary_class_names,
                    term=term,
                    status=ScoreStatus.APPROVED,
                )
                .select_related('student')
                .order_by('student_id', 'subject_name', 'exam_type')
            )
            _score_groups = {}
            for sc in all_scores_qs:
                key = (sc.student_id, sc.subject_name)
                _score_groups.setdefault(key, []).append(sc)

            students_map = {
                s.pk: s for s in Student.objects.filter(
                    class_name__in=primary_class_names, is_archived=False, status='active'
                )
            }

            for (student_id, subj), scores in _score_groups.items():
                wsum = 0
                tw = 0
                for sc in scores:
                    w = exam_weights.get(sc.exam_type, 0)
                    wsum += float(sc.score) * w
                    tw += w
                if tw > 0:
                    avg = wsum / tw
                    if avg < 60:  # D or below
                        flag_color = "red" if avg < 50 else "amber"
                        at_risk_students.append({
                            "student": students_map.get(student_id),
                            "subject": subj,
                            "average": round(avg, 1),
                            "flag_color": flag_color,
                        })
        
        ctx["heatmap_data"] = heatmap_data
        ctx["heatmap_classes"] = sorted(dept_classes)
        ctx["at_risk_students"] = at_risk_students

        return ctx


class ECDHODDashboardView(RoleRequiredMixin, TemplateView):
    allowed_roles = [UserRole.ECD_HOD]
    """ECD Head of Department Dashboard — High Fidelity."""
    template_name = "dashboards/ecd_hod.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        
        # Date Filter
        selected_date_str = self.request.GET.get("date")
        if selected_date_str:
            try:
                today = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
            except:
                today = timezone.localdate()
        else:
            today = timezone.localdate()

        ctx.update(_base_ctx(user, today=today))
        ctx["today"] = today
        ctx["soon_date"] = (today + timedelta(days=3)).strftime("%Y-%m-%d")

        # Scope: ECD Classes
        ecd_classes = GradeClass.objects.filter(department=Department.ECD).values_list('name', flat=True)
        
        # 1. Welfare Metrics
        welfare_pending = WelfareObservation.objects.filter(student__class_name__in=ecd_classes, hod_status="pending")
        ctx["welfare_to_review_count"] = welfare_pending.count()
        ctx["flagged_count"] = WelfareObservation.objects.filter(student__class_name__in=ecd_classes, severity__in=[WelfareSeverity.HIGH, WelfareSeverity.CRITICAL], hod_status="pending").count()

        # Children flagged for follow-up (high/critical, pending HOD review)
        flagged_children = WelfareObservation.objects.filter(
            student__class_name__in=ecd_classes,
            severity__in=[WelfareSeverity.HIGH, WelfareSeverity.CRITICAL],
            hod_status="pending",
        ).select_related("student").order_by("-created_at")[:5]
        ctx["flagged_children"] = flagged_children

        # Parent comms needed — high/critical observations where parent not yet contacted
        parent_comms = WelfareObservation.objects.filter(
            student__class_name__in=ecd_classes,
            severity__in=[WelfareSeverity.HIGH, WelfareSeverity.CRITICAL],
            parent_contacted=False,
            hod_status="pending",
        ).select_related("student").order_by("-created_at")[:5]
        ctx["parent_comms_needed"] = parent_comms
        ctx["comms_needed_count"] = parent_comms.count()
        
        # 2. Staff Presence — from StaffAttendanceEntry
        from attendance.models import StaffAttendanceEntry
        from timetable.models import TimetableSlot
        from hr.models import StaffProfile
        ecd_staff = User.objects.filter(
            staff_profile__in=StaffProfile.objects.filter(department=Department.ECD)
        )

        today_weekday = today.strftime("%a").lower()[:3]  # "mon", "tue", etc.
        current_term = Term.objects.filter(academic_year__is_current=True).order_by('-start_date').first()

        # Find which ECD staff have any timetable slots (class assignments)
        all_class_teachers = set(
            TimetableSlot.objects.filter(
                teacher__in=ecd_staff,
                term=current_term,
                is_published=True,
            ).values_list("teacher_id", flat=True)
        ) if current_term else set()

        # Find which ECD staff have timetable slots TODAY specifically
        staff_with_classes_today = set(
            TimetableSlot.objects.filter(
                teacher__in=ecd_staff,
                day_of_week=today_weekday,
                is_published=True,
                term=current_term,
            ).values_list("teacher_id", flat=True)
        ) if current_term else set()

        staff_entries = {
            e.staff_id: e
            for e in StaffAttendanceEntry.objects.filter(
                date=today,
                staff__user__in=ecd_staff,
            ).select_related("staff__user")
        }
        staff_list = []
        ecd_absent_count = 0
        for s in ecd_staff:
            entry = staff_entries.get(s.staff_profile.id) if hasattr(s, 'staff_profile') else None
            is_teacher = s.id in all_class_teachers
            has_classes_today = s.id in staff_with_classes_today
            if entry:
                status = entry.get_status_display()
                check_in = entry.check_in_time.strftime("%I:%M %p").lstrip("0") if entry.check_in_time else None
            else:
                status = "Unconfirmed"
                check_in = None
                ecd_absent_count += 1
            staff_list.append({
                "name": s.get_full_name() or s.username,
                "role": s.staff_profile.job_title if hasattr(s, 'staff_profile') else "Teacher",
                "status": status,
                "check_in": check_in,
                "is_teacher": is_teacher,
                "has_classes_today": has_classes_today,
            })
        ctx["staff_attendance"] = staff_list[:5]
        ctx["staff_absent_count"] = ecd_absent_count
        
        # 3. Welfare List
        ctx["welfare_review_list"] = welfare_pending.order_by("-created_at")[:4]
        
        # 4. Attendance — real data from AttendanceEntry
        class_att = []
        class_active_counts = dict(
            Student.objects.filter(class_name__in=ecd_classes, status=StudentStatus.ACTIVE)
            .values('class_name')
            .annotate(cnt=Count('id'))
            .values_list('class_name', 'cnt')
        )
        class_present_counts = dict(
            AttendanceEntry.objects.filter(date=today, student__class_name__in=ecd_classes, status=AttendanceStatus.PRESENT)
            .values('student__class_name')
            .annotate(cnt=Count('id'))
            .values_list('student__class_name', 'cnt')
        )
        class_late_counts = dict(
            AttendanceEntry.objects.filter(date=today, student__class_name__in=ecd_classes, status=AttendanceStatus.LATE)
            .values('student__class_name')
            .annotate(cnt=Count('id'))
            .values_list('student__class_name', 'cnt')
        )
        for c in ecd_classes:
            total = class_active_counts.get(c, 0)
            if total > 0:
                present = class_present_counts.get(c, 0)
                late = class_late_counts.get(c, 0)
                checked_in = present + late
                pct = round((checked_in / total) * 100)
                status = "green" if pct >= 90 else ("amber" if pct >= 80 else "red")
                class_att.append({"name": c, "pct": pct, "status": status})
        ctx["class_attendance"] = class_att[:5]
        
        # 5. Progression Status — computed from ECD evaluations (E/G/S/N ratings) for current term
        from academics.utils import get_current_term
        current_term = get_current_term()
        rating_map = {"E": 4, "G": 3, "S": 2, "N": 1}
        meets = towards = not_meet = 0
        try:
            rc_filter = Q(
                student__class_name__in=ecd_classes,
                is_ecd_report=True,
            )
            if current_term:
                rc_filter &= Q(term=current_term)
            ecd_report_cards = ReportCard.objects.filter(
                rc_filter
            ).exclude(
                status=ReportCardStatus.DRAFT
            ).prefetch_related("ecd_evaluations")
            for rc in ecd_report_cards:
                evals = rc.ecd_evaluations.all()
                if not evals:
                    continue
                total = 0
                count = 0
                for ev in evals:
                    val = rating_map.get(ev.rating, 0)
                    if val > 0:
                        total += val
                        count += 1
                if count == 0:
                    continue
                avg = total / count
                if avg >= 3.0:
                    meets += 1
                elif avg >= 2.0:
                    towards += 1
                else:
                    not_meet += 1
        except Exception:
            pass
        ctx["progression"] = {
            "meets": meets,
            "towards": towards,
            "not_meet": not_meet,
        }
        
        # 6. Upcoming Events
        try:
            from events.models import CalendarEvent
            ctx["upcoming_events"] = CalendarEvent.objects.filter(start_date__gte=today, is_published=True).order_by('start_date')[:4]
        except:
            ctx["upcoming_events"] = []

        # FR-ADM-018: Admission actions relevant to ECD HOD
        from admissions.services import get_critical_actions
        admission_actions = get_critical_actions()
        ecd_grade_names = set(GradeClass.objects.filter(department=Department.ECD).values_list("name", flat=True))
        relevant_actions = []
        for a in admission_actions:
            app_obj = a.get("applicant")
            grade = getattr(app_obj, "grade_applying_for", None) if app_obj else None
            if grade in ecd_grade_names or a.get("type") in ("academic", "admin"):
                relevant_actions.append(a)
        ctx["admission_actions"] = relevant_actions[:5]

        # FR-ADM-018: ECD HOS review queue — applicants awaiting HOS review in ECD department
        hos_review_qs = Applicant.objects.filter(
            status=ApplicantStatus.HOS_REVIEW,
            grade_applying_for__in=list(ecd_grade_names),
        ).select_related("assessment")[:10]
        ctx["hos_review_queue"] = []
        for app in hos_review_qs:
            assessment = getattr(app, "assessment", None)
            ctx["hos_review_queue"].append({
                "applicant": app,
                "assessment": assessment,
                "result_display": assessment.get_result_display() if assessment and assessment.result else "—",
                "teacher_comments": (assessment.teacher_comments or "")[:120] if assessment else "",
            })
        ctx["hos_review_count"] = len(ctx["hos_review_queue"])

        return ctx


class AdminDashboardView(RoleRequiredMixin, TemplateView):
    allowed_roles = [UserRole.ADMIN_OFFICER]
    """Administrative Officer Dashboard."""
    template_name = "dashboards/admin.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        
        new_inquiries = Applicant.objects.filter(status=ApplicantStatus.INQUIRY_RECEIVED).count()
        pending_enrolment = Applicant.objects.filter(status=ApplicantStatus.ADMITTED).count()
        
        # FR-ADM-024: Upcoming assessments next 7 days
        today = date.today()
        upcoming_assessments = (
            Applicant.objects.filter(
                status__in=[
                    ApplicantStatus.ASSESSMENT_PENDING,
                    ApplicantStatus.ASSESSMENT_FEE_PAID,
                    ApplicantStatus.ASSESSMENT_CONFIRMED,
                ],
                assessment__scheduled_date__gte=today,
                assessment__scheduled_date__lte=today + timedelta(days=7),
            ).order_by("assessment__scheduled_date", "assessment__scheduled_time")[:10]
        )

        # FR-ADM-025: Pending actions queue for admin
        blocked_assessments = Applicant.objects.filter(
            status=ApplicantStatus.ASSESSMENT_PENDING,
            assessment__assessment_fee_confirmed_paid=False,
        ).count()
        # Applicants with no activity in 7+ days (overdue follow-ups)
        stale_cutoff = today - timedelta(days=7)
        overdue_followups = (
            Applicant.objects.filter(
                updated_at__date__lt=stale_cutoff,
            ).exclude(
                status__in=[
                    ApplicantStatus.ENROLLED,
                    ApplicantStatus.DENIED,
                    ApplicantStatus.WITHDRAWN,
                    ApplicantStatus.DECLINED_AT_MEETING,
                    ApplicantStatus.ASSESSMENT_FAILED,
                    ApplicantStatus.FLAGGED_FOR_REVIEW,
                ],
            ).count()
        )

        # FR-ATT-006: School-wide unconfirmed students by 9:00 AM
        unconfirmed_students = _unconfirmed_students_qs(today)

        # Fee reminder count for admin awareness
        from finance.models import Invoice, InvoiceStatus
        overdue_invoices = Invoice.objects.filter(status=InvoiceStatus.OVERDUE).count()

        # School capacity
        from academics.models import get_class_capacity
        grade_classes = GradeClass.objects.all().order_by("sort_order", "name")
        total_capacity = sum(get_class_capacity(gc) for gc in grade_classes)
        total_enrolled = Student.objects.filter(status=StudentStatus.ACTIVE).count()
        active_by_class = dict(
            Student.objects.filter(status=StudentStatus.ACTIVE)
            .values("class_name").annotate(cnt=Count("id"))
            .values_list("class_name", "cnt")
        )
        class_capacity = []
        for gc in grade_classes:
            enrolled = active_by_class.get(gc.name, 0)
            cap = get_class_capacity(gc)
            pct = round(enrolled / cap * 100, 1) if cap else 0
            class_capacity.append({
                "name": gc.name,
                "enrolled": enrolled,
                "capacity": cap,
                "pct": min(pct, 100),
                "available": max(cap - enrolled, 0),
                "over_capacity": enrolled > cap,
            })

        ctx.update(_base_ctx(user))
        ctx.update({
            "new_inquiries": new_inquiries,
            "pending_enrolment": pending_enrolment,
            "pipeline_stages": _pipeline_stages(),
            "upcoming_assessments": upcoming_assessments,
            "blocked_assessments": blocked_assessments,
            "overdue_followups": overdue_followups,
            "overdue_invoices": overdue_invoices,
            "unconfirmed_students": unconfirmed_students,
            "unconfirmed_count": len(unconfirmed_students),
            "school_capacity_pct": round(total_enrolled / total_capacity * 100, 1) if total_capacity else 0,
            "total_capacity": total_capacity,
            "total_enrolled": total_enrolled,
            "available_slots": max(total_capacity - total_enrolled, 0),
            "class_capacity": class_capacity,
        })
        return ctx


class FinanceDashboardProxyView(RoleRequiredMixin, TemplateView):
    allowed_roles = [UserRole.FINANCE_OFFICER]
    """Redirects or proxies to the main finance dashboard."""
    def get(self, request, *args, **kwargs):
        return redirect("finance:dashboard")


class TeacherDashboardView(RoleRequiredMixin, TemplateView):
    allowed_roles = [UserRole.TEACHER]
    """Teacher Dashboard — Academic focus."""
    template_name = "dashboards/teacher.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        today = date.today()
        now = datetime.now()
        
        # 3. Daily Data Metrics
        from academics.utils import get_current_term
        current_term = get_current_term()

        # 1. Timetable (Filtered by term)
        day_map = {1: Weekday.MON, 2: Weekday.TUE, 3: Weekday.WED, 4: Weekday.THU, 5: Weekday.FRI}
        current_day = day_map.get(today.isoweekday(), Weekday.MON)
        
        today_slots = TimetableSlot.objects.filter(
            teacher=user, day_of_week=current_day, term=current_term
        ).select_related("subject").order_by("start_time")
        
        # 2. Lesson Plans
        base_plans = LessonPlan.objects.filter(teacher=user)
        my_plans = base_plans.select_related("term", "reviewed_by").order_by("-updated_at")
        
        # Define "Assigned Classes"
        # 1. Explicit Class assignments for this term
        assignment_classes = list(TeacherClassAssignment.objects.filter(
            teacher__user=user, 
            term=current_term
        ).values_list("grade_class__name", flat=True).distinct())
        
        # 2. Classes taught in the timetable (all days, not just today, for comprehensive list)
        timetable_classes = list(TimetableSlot.objects.filter(
            teacher=user,
            term=current_term
        ).values_list("class_name", flat=True).distinct())
        
        # Combined assigned classes
        all_assigned = list(set(assignment_classes + timetable_classes))
        
        # For Attendance to Mark TODAY:
        # We only want classes where the teacher is actually teaching TODAY or is the Class Teacher
        timetable_classes_today = list(set(today_slots.values_list("class_name", flat=True)))
        
        # If the teacher is a Class Teacher (Homeroom), they should always see their class
        homeroom_classes = list(TeacherClassAssignment.objects.filter(
            teacher__user=user, 
            term=current_term,
            is_class_teacher=True
        ).values_list("grade_class__name", flat=True).distinct())
        
        # att_classes: The list of classes they should mark attendance for today
        # CRITICAL FIX: If homeroom_classes exist, they take precedence for daily attendance.
        # Otherwise, we fallback to today's timetable classes.
        if homeroom_classes:
            att_classes = homeroom_classes
        else:
            att_classes = timetable_classes_today
        
        # Attendance Today Summary (for the top metrics)
        if att_classes:
            att_present = AttendanceEntry.objects.filter(date=today, student__class_name__in=att_classes, status=AttendanceStatus.PRESENT).count()
            att_total = Student.objects.filter(class_name__in=att_classes, status=StudentStatus.ACTIVE).count()
        else:
            att_present = 0
            att_total = 0
        
        # Welfare Recorded Today by this teacher
        welfare_today = WelfareObservation.objects.filter(submitted_by=user, created_at__date=today).count()
        
        # Behavior/Discipline Recorded Today by this teacher
        discipline_today = DisciplineIncident.objects.filter(reported_by=user, created_at__date=today).count()

        # 4. Teacher Context (ECD vs Primary/other)
        teacher_is_ecd = is_ecd_teacher(user)
        ecd_eval_url = reverse("academics:ecd_evaluations_entry") if teacher_is_ecd else None

        # 5. Attendance Alert - per-class status for reminders (batched queries)
        # FR-ATT-006: Name-list of unconfirmed students by 9:00 AM
        unconfirmed_alert = 0
        unconfirmed_students = []  # [{student, class_name}] — shown as name list on dashboard
        class_attendance_status = []
        if att_classes:
            # Batch: get all active student counts per class in one query
            cls_totals = dict(
                Student.objects.filter(class_name__in=att_classes, status=StudentStatus.ACTIVE)
                .values('class_name')
                .annotate(cnt=Count('id'))
                .values_list('class_name', 'cnt')
            )
            # Batch: get all attendance counts per class in one query
            cls_marked_counts = dict(
                AttendanceEntry.objects.filter(date=today, student__class_name__in=att_classes)
                .values('student__class_name')
                .annotate(cnt=Count('id'))
                .values_list('student__class_name', 'cnt')
            )
            cls_present_counts = dict(
                AttendanceEntry.objects.filter(date=today, student__class_name__in=att_classes, status=AttendanceStatus.PRESENT)
                .values('student__class_name')
                .annotate(cnt=Count('id'))
                .values_list('student__class_name', 'cnt')
            )
            for cls_name in att_classes:
                cls_total = cls_totals.get(cls_name, 0)
                cls_marked = cls_marked_counts.get(cls_name, 0)
                cls_present = cls_present_counts.get(cls_name, 0)
                is_marked = cls_marked > 0
                cls_pct = round((cls_present / cls_total * 100)) if cls_total > 0 else 0
                class_attendance_status.append({
                    "class_name": cls_name,
                    "total": cls_total,
                    "marked": is_marked,
                    "present": cls_present,
                    "pct": cls_pct,
                })
                if not is_marked:
                    unconfirmed_alert += cls_total

        # FR-ATT-006: Build name-list of unconfirmed students for teacher's classes
        # Skip on non-school days (weekends + holidays)
        if att_classes and is_school_day(today):
            unconfirmed_qs = (
                Student.objects.filter(
                    class_name__in=att_classes,
                    status=StudentStatus.ACTIVE,
                )
                .exclude(
                    attendance_entries__date=today,
                    attendance_entries__status__in=[
                        AttendanceStatus.PRESENT,
                        AttendanceStatus.LATE,
                        AttendanceStatus.ABSENT,
                        AttendanceStatus.EXCUSED,
                    ],
                )
                .order_by('class_name', 'last_name', 'first_name')
            )
            unconfirmed_students = [
                {"student": s, "class_name": s.class_name}
                for s in unconfirmed_qs
            ]

        # 6. Events
        upcoming_events = []
        try:
            upcoming_events = CalendarEvent.objects.filter(start_date__gte=today, is_published=True).order_by('start_date')[:4]
        except Exception:
            pass

        ctx.update(_base_ctx(user))
        ctx.update({
            "today_slots": today_slots,
            "my_plans": my_plans,
            "att_present": att_present,
            "att_total": att_total,
            "welfare_today": welfare_today,
            "discipline_today": discipline_today,
            "rejected_plans": base_plans.filter(status=LessonPlanStatus.REJECTED),
            "is_ecd_teacher": teacher_is_ecd,
            "is_primary_teacher": not teacher_is_ecd,
            "ecd_eval_url": ecd_eval_url,
            "unconfirmed_alert": unconfirmed_alert,
            "unconfirmed_students": unconfirmed_students,
            "class_attendance_status": class_attendance_status,
            "upcoming_events": upcoming_events,
        })
        return ctx


from django.views.generic import UpdateView
from django.contrib import messages
from core.models import SchoolSettings
from core.forms import SchoolSettingsForm, SchoolSettingsSystemForm, SchoolSettingsMessagingForm
from core.permissions import RoleRequiredMixin
class SchoolSettingsUpdateView(RoleRequiredMixin, View):
    template_name = "core/settings.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    # Permission-driven access: anyone holding view_schoolsettings can open the
    # Settings page (matches nav visibility via MODULE_PERMISSIONS). Role-based
    # tab restrictions below still apply for what each tab exposes.
    required_permission = "core.view_schoolsettings"

    def _get_settings(self):
        return SchoolSettings.get_settings()
    def _is_admin_officer(self, request):
        return getattr(request.user, 'role', None) == UserRole.ADMIN_OFFICER
    def _is_super_admin(self, request):
        return getattr(request.user, 'role', None) == UserRole.SUPER_ADMIN
    def _is_head_of_school(self, request):
        return getattr(request.user, 'role', None) == UserRole.HEAD_OF_SCHOOL
    def _is_hod(self, request):
        return getattr(request.user, 'role', None) in {UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD}

    RESTRICTED_TABS_SUPER_ADMIN_ONLY = {'system', 'groups', 'email_templates'}
    RESTRICTED_TABS_ADMIN_OFFICER = {'system', 'academic_year', 'groups', 'email_templates'}
    RESTRICTED_TABS_HOD = {'system', 'groups', 'induction_checklist', 'email_templates'}

    def get(self, request, *args, **kwargs):
        tab = request.GET.get("tab", "system")
        if self._is_admin_officer(request) and tab in self.RESTRICTED_TABS_ADMIN_OFFICER:
            tab = 'classes'
        elif self._is_head_of_school(request) and tab in self.RESTRICTED_TABS_SUPER_ADMIN_ONLY:
            tab = 'academic_year'
        elif self._is_hod(request) and tab in self.RESTRICTED_TABS_HOD:
            tab = 'classes'
        if tab == "messaging":
            form = SchoolSettingsMessagingForm(instance=self._get_settings())
        else:
            form = SchoolSettingsSystemForm(instance=self._get_settings())
        ctx = {"active_tab": tab, "form": form, "is_admin_officer": self._is_admin_officer(request), "is_head_of_school": self._is_head_of_school(request), "is_hod": self._is_hod(request)}
        # Always load classes count for the tab badge
        if request.user.has_perm("academics.view_gradeclass"):
            from academics.models import GradeClass as _GC
            ctx["classes_count"] = _GC.objects.count()
        if tab == "classes":
            from academics.models import GradeClass, Department
            if not request.user.has_perm("academics.view_gradeclass"):
                # Permission-driven: only roles granted view_gradeclass see class data
                tab = "system"
                ctx["active_tab"] = tab
                ctx["form"] = SchoolSettingsSystemForm(instance=self._get_settings())
            else:
                classes_qs = GradeClass.objects.all()
                if self._is_hod(request):
                    role_dept = {
                        UserRole.PRIMARY_HOD: Department.PRIMARY,
                        UserRole.ECD_HOD: Department.ECD,
                        UserRole.LOWER_SECONDARY_HOD: Department.LOWER_SECONDARY,
                    }.get(request.user.role)
                    if role_dept:
                        classes_qs = classes_qs.filter(department=role_dept)
                ctx["classes"] = classes_qs.order_by("department", "name")
                ctx["departments"] = Department.choices
                edit_pk = request.GET.get("edit")
                if edit_pk:
                    ctx["edit_class"] = get_object_or_404(GradeClass, pk=edit_pk)
        if tab == "academic_year":
            from academics.models import AcademicYear, Term
            from django.utils import timezone
            ctx["academic_years"] = AcademicYear.objects.prefetch_related("terms").order_by("-is_current", "-name")
            ctx["is_super_admin"] = self._is_super_admin(request)
            ctx["today_iso"] = timezone.now().date().isoformat()
            current_year_obj = AcademicYear.objects.filter(is_current=True).first()
            ctx["current_year_name"] = current_year_obj.name if current_year_obj else ""
            current = Term.get_current()
            ctx["current_term"] = current
            if current and current.end_date:
                ctx["days_remaining"] = max((current.end_date - timezone.now().date()).days, 0)
            else:
                ctx["days_remaining"] = 0
        if tab == "media":
            from core.models import MediaSettings
            edit_area = request.GET.get("edit_area")
            if edit_area:
                ctx["edit_setting"] = get_object_or_404(MediaSettings, area=edit_area)
            # Seed defaults only on first visit (when no rows exist yet)
            if not MediaSettings.objects.exists():
                for area_code, _ in MediaSettings.AREA_CHOICES:
                    MediaSettings.get_for_area(area_code)
            ctx["media_settings"] = MediaSettings.objects.all()
        if tab == "dynamic_pages":
            pass  # Branding context is already available via the branding context processor
        from hr.models import OnboardingChecklistItem
        ctx["induction_items"] = OnboardingChecklistItem.objects.all().order_by("step", "order", "item_name")
        if tab == "induction_checklist":
            from hr.views import ONBOARDING_STEP_LABELS
            ctx["onboarding_step_choices"] = list(ONBOARDING_STEP_LABELS.items())
            edit_pk = request.GET.get("edit")
            if edit_pk:
                ctx["edit_item"] = get_object_or_404(OnboardingChecklistItem, pk=edit_pk)
        from django.contrib.auth.models import Group, Permission
        ctx["auth_groups"] = Group.objects.prefetch_related("permissions", "user_set").order_by("name")
        if tab == "groups":
            edit_pk = request.GET.get("edit")
            if edit_pk:
                edit_group = get_object_or_404(Group, pk=edit_pk)
                ctx["edit_group"] = edit_group
                ctx["edit_group_perms"] = set(edit_group.permissions.values_list("id", flat=True))
            ctx["all_permissions"] = Permission.objects.select_related("content_type").order_by("content_type__app_label", "content_type__model", "codename")
        if tab == "email_templates":
            from core.models import EmailTemplate
            from core.email_templates import seed_default_templates
            seed_default_templates()
            # AJAX: return single template as JSON for the edit modal
            if request.GET.get("action") == "get_template":
                from django.http import JsonResponse
                import html as html_mod
                tpl_type = request.GET.get("template_type")
                try:
                    tpl = EmailTemplate.objects.get(template_type=tpl_type)
                    html_body = tpl.html_body
                    if not html_body and tpl.plain_body:
                        html_body = "<p>" + html_mod.escape(tpl.plain_body).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
                    return JsonResponse({
                        "subject": tpl.subject,
                        "html_body": html_body or "",
                        "plain_body": tpl.plain_body,
                        "is_enabled": tpl.is_enabled,
                    })
                except EmailTemplate.DoesNotExist:
                    return JsonResponse({"error": "not found"}, status=404)
            ctx["email_templates"] = EmailTemplate.objects.all()
            ctx["can_edit_email_templates"] = request.user.has_perm("core.change_emailtemplate")
        return render(request, self.template_name, ctx)

    def post(self, request, *args, **kwargs):
        tab = request.GET.get("tab", "system")
        if self._is_admin_officer(request) and tab in self.RESTRICTED_TABS_ADMIN_OFFICER:
            messages.error(request, "You do not have permission to modify these settings.")
            return redirect(f"{reverse_lazy('core:school_settings')}?tab=classes")
        if self._is_head_of_school(request) and tab in self.RESTRICTED_TABS_SUPER_ADMIN_ONLY:
            messages.error(request, "You do not have permission to modify these settings.")
            return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
        if self._is_hod(request) and tab in self.RESTRICTED_TABS_HOD:
            messages.error(request, "You do not have permission to modify these settings.")
            return redirect(f"{reverse_lazy('core:school_settings')}?tab=classes")
        action = request.POST.get("action", "save_settings")

        if tab == "classes" and action in ("add_class", "edit_class", "delete_class"):
            from academics.models import AcademicYear, GradeClass
            from django.core.exceptions import ValidationError
            from django.db import IntegrityError

            # Permission-driven: actions reflect the role's granted gradeclass
            # permissions (mirrors /academics/classes/ enforcement).
            if action == "delete_class":
                if not request.user.has_perm("academics.delete_gradeclass"):
                    messages.error(request, "You do not have permission to delete classes.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=classes")
                pk = request.POST.get("pk")
                gc = get_object_or_404(GradeClass, pk=pk)
                name = gc.name
                gc.delete()
                messages.success(request, f"Class '{name}' deleted.")
            elif action == "add_class":
                if not request.user.has_perm("academics.add_gradeclass"):
                    messages.error(request, "You do not have permission to add classes.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=classes")
                name = request.POST.get("name", "").strip()
                dept = request.POST.get("department", "")
                raw_cap = (request.POST.get("max_capacity") or "").strip()
                try:
                    if not raw_cap:
                        raise ValueError("required")
                    cap = int(raw_cap)
                    if cap <= 0:
                        raise ValueError
                except ValueError as e:
                    if str(e) == "required":
                        messages.error(request, "Class capacity is required. Enter a positive whole number.")
                    else:
                        messages.error(request, "Class capacity must be a positive whole number.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=classes")
                if name and dept:
                    is_exit_grade = request.POST.get("is_exit_grade") == "on"
                    try:
                        from django.db.models import Max
                        next_sort = (GradeClass.objects.aggregate(m=Max("sort_order"))["m"] or 0) + 1
                        gc = GradeClass(
                            name=name, department=dept, max_capacity=cap,
                            sort_order=next_sort, is_exit_grade=is_exit_grade,
                        )
                        gc.full_clean()
                        gc.save()
                        created = True
                    except (ValidationError, IntegrityError) as e:
                        if hasattr(e, 'message_dict'):
                            msgs = []
                            for field, errs in e.message_dict.items():
                                for err in (errs if isinstance(errs, list) else [errs]):
                                    msgs.append(str(err))
                            detail = "; ".join(msgs)
                        else:
                            detail = str(e.message) if hasattr(e, 'message') else str(e)
                        messages.error(request, f"Cannot create class: {detail}")
                        return redirect(f"{reverse_lazy('core:school_settings')}?tab=classes")
                    if created:
                        # FRD OP 8.1: Seed ClassCapacity for current academic year
                        from academics.models import ClassCapacity
                        current_year = AcademicYear.objects.filter(is_current=True).first()
                        if current_year:
                            ClassCapacity.objects.get_or_create(
                                grade_class=gc, academic_year=current_year,
                                defaults={"max_capacity": cap}
                            )
                        messages.success(request, f"Class '{name}' created.")
                    else:
                        messages.warning(request, f"Class '{name}' already exists.")
                else:
                    messages.error(request, "Name and department are required.")
            elif action == "edit_class":
                if not request.user.has_perm("academics.change_gradeclass"):
                    messages.error(request, "You do not have permission to edit classes.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=classes")
                from academics.models import ClassCapacity
                pk = request.POST.get("pk")
                gc = get_object_or_404(GradeClass, pk=pk)
                new_name = request.POST.get("name", gc.name).strip()
                new_dept = request.POST.get("department", gc.department)
                gc.name = new_name
                gc.department = new_dept
                gc.is_exit_grade = request.POST.get("is_exit_grade") == "on"
                raw_cap = (request.POST.get("max_capacity") or "").strip()
                try:
                    if not raw_cap:
                        raise ValueError("required")
                    new_cap = int(raw_cap)
                    if new_cap <= 0:
                        raise ValueError
                except ValueError as e:
                    if str(e) == "required":
                        messages.error(request, "Class capacity is required. Enter a positive whole number.")
                    else:
                        messages.error(request, "Class capacity must be a positive whole number.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=classes")
                gc.max_capacity = new_cap
                try:
                    gc.full_clean()
                    gc.save()
                except ValidationError as e:
                    if hasattr(e, 'message_dict'):
                        msgs = []
                        for field, errs in e.message_dict.items():
                            for err in (errs if isinstance(errs, list) else [errs]):
                                msgs.append(str(err))
                        detail = "; ".join(msgs)
                    else:
                        detail = str(e.message) if hasattr(e, 'message') else str(e)
                    messages.error(request, f"Cannot save class: {detail}")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=classes")
                # FRD OP 8.1: Also update ClassCapacity for current academic year
                current_year = AcademicYear.objects.filter(is_current=True).first()
                if current_year:
                    cc, created = ClassCapacity.objects.get_or_create(
                        grade_class=gc, academic_year=current_year,
                        defaults={"max_capacity": new_cap}
                    )
                    if not created:
                        cc.max_capacity = new_cap
                        cc.save(update_fields=["max_capacity"])
                messages.success(request, f"Class '{gc.name}' updated.")
            return redirect(f"{reverse_lazy('core:school_settings')}?tab=classes")

        # Handle branding save from dynamic_pages tab
        if action == "save_branding":
            settings_obj = self._get_settings()
            login_hero = request.FILES.get("login_hero_image")
            login_staff_hero = request.FILES.get("login_staff_hero_image")
            login_parent_hero = request.FILES.get("login_parent_hero_image")
            login_logo = request.FILES.get("login_logo")
            if login_hero:
                settings_obj.login_hero_image = login_hero
            if login_staff_hero:
                settings_obj.login_staff_hero_image = login_staff_hero
            if login_parent_hero:
                settings_obj.login_parent_hero_image = login_parent_hero
            if login_logo:
                settings_obj.login_logo = login_logo
            if request.POST.get("remove_staff_hero"):
                settings_obj.login_staff_hero_image = None
            if request.POST.get("remove_parent_hero"):
                settings_obj.login_parent_hero_image = None
            if request.POST.get("remove_logo"):
                settings_obj.login_logo = None
            for field in [
                "login_heading", "login_description", "login_tagline",
                "login_feature1", "login_feature2", "login_feature3",
                "login_right_heading", "login_right_subtitle",
            ]:
                val = request.POST.get(field, "").strip()
                setattr(settings_obj, field, val)
            settings_obj.save()
            messages.success(request, "Login page branding updated.")
            return redirect(f"{reverse_lazy('core:school_settings')}?tab=dynamic_pages")

        # Handle media settings tab
        if tab == "media":
            from core.models import MediaSettings

            if action == "save_media_setting":
                area = request.POST.get("area")
                if area:
                    setting, _ = MediaSettings.objects.get_or_create(area=area)
                    setting.allowed_extensions = request.POST.get("allowed_extensions", setting.allowed_extensions)
                    setting.max_file_size_mb = int(request.POST.get("max_file_size_mb", setting.max_file_size_mb))
                    setting.max_total_size_mb = int(request.POST.get("max_total_size_mb", setting.max_total_size_mb))
                    setting.max_files = int(request.POST.get("max_files", setting.max_files))
                    setting.duplicate_check = request.POST.get("duplicate_check") == "on"
                    setting.is_active = request.POST.get("is_active") == "on"
                    setting.min_width = int(request.POST.get("min_width", setting.min_width))
                    setting.min_height = int(request.POST.get("min_height", setting.min_height))
                    setting.save()
                    messages.success(request, f"{setting.get_area_display()} settings saved.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=media")

            if action == "reset_media_defaults":
                area = request.POST.get("area")
                if area:
                    MediaSettings.objects.filter(area=area).delete()
                    MediaSettings.get_for_area(area)
                    messages.success(request, "Settings reset to defaults.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=media")

            return redirect(f"{reverse_lazy('core:school_settings')}?tab=media")

        # Handle messaging tab (email & WhatsApp settings)
        if tab == "messaging":
            form = SchoolSettingsMessagingForm(request.POST, instance=self._get_settings())
            if form.is_valid():
                settings_obj = form.save()
                # Apply email settings to Django runtime so they take effect immediately
                from django.conf import settings as django_settings
                django_settings.EMAIL_BACKEND = settings_obj.email_backend
                django_settings.EMAIL_HOST = settings_obj.email_host
                django_settings.EMAIL_PORT = settings_obj.email_port
                django_settings.EMAIL_USE_TLS = settings_obj.email_use_tls
                django_settings.EMAIL_HOST_USER = settings_obj.email_host_user
                django_settings.EMAIL_HOST_PASSWORD = settings_obj.email_host_password
                django_settings.DEFAULT_FROM_EMAIL = settings_obj.default_from_email
                messages.success(request, "Email settings updated successfully.")
            else:
                messages.error(request, "Please correct the errors below.")
            return redirect(f"{reverse_lazy('core:school_settings')}?tab=messaging")

        # Handle academic year & terms tab
        if tab == "academic_year":
            from academics.models import AcademicYear, Term
            action = request.POST.get("action", "")

            if action == "add_academic_year":
                name = request.POST.get("name", "").strip()
                num_terms = int(request.POST.get("number_of_terms", 3))
                is_current = request.POST.get("is_current") == "on"
                start_date_str = request.POST.get("start_date", "").strip()
                end_date_str = request.POST.get("end_date", "").strip()
                if not name:
                    messages.error(request, "Year name is required.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                if not start_date_str or not end_date_str:
                    messages.error(request, "Start date and end date are required.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                from datetime import date as date_type
                try:
                    start_date = date_type.fromisoformat(start_date_str)
                    end_date = date_type.fromisoformat(end_date_str)
                except (ValueError, TypeError):
                    messages.error(request, "Invalid date format.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                ay = AcademicYear(name=name, number_of_terms=num_terms,
                                  start_date=start_date, end_date=end_date)
                try:
                    from django.core.exceptions import ValidationError as DjangoValidationError
                    ay.full_clean(exclude=["is_current"])
                except DjangoValidationError as e:
                    for field, errors in e.message_dict.items():
                        for err in errors:
                            messages.error(request, err)
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                # MUST-PREVENT (tracker 1.1): a second active/overlapping year must be hard-blocked.
                # Creating another is_current year silently re-points the Current flag via save();
                # creating dates overlapping a seed year with NULL dates bypasses the model overlap
                # check. Both cases are prevented here, server-side.
                other_current = AcademicYear.objects.filter(is_current=True).first()
                if is_current and other_current:
                    messages.error(
                        request,
                        f"Cannot create '{name}' as the current academic year — "
                        f"'{other_current.name}' is already the current year. "
                        f"Clear its current flag first."
                    )
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                overlapping = AcademicYear.objects.filter(
                    start_date__lt=end_date,
                    end_date__gt=start_date,
                ).exclude(name=name).first()
                if overlapping:
                    messages.error(
                        request,
                        f"Date range overlaps with '{overlapping.name}' "
                        f"({overlapping.start_date} to {overlapping.end_date}). "
                        f"Adjust the dates or clear the conflicting year's dates."
                    )
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                existing = AcademicYear.objects.filter(name=name).first()
                if existing:
                    messages.warning(request, f"Academic year '{name}' already exists.")
                else:
                    ay.is_current = is_current
                    ay.save()
                    from datetime import timedelta
                    total_days = (end_date - start_date).days
                    term_days = total_days // num_terms
                    term_names = {1: "Term 1", 2: "Term 2", 3: "Term 3", 4: "Term 4"}
                    for i in range(num_terms):
                        t_start = start_date + timedelta(days=i * term_days)
                        t_end = start_date + timedelta(days=(i + 1) * term_days - 1) if i < num_terms - 1 else end_date
                        Term.objects.create(
                            academic_year=ay,
                            name=term_names.get(i + 1, f"Term {i + 1}"),
                            start_date=t_start,
                            end_date=t_end,
                        )
                    messages.success(request, f"Academic year '{name}' created with {num_terms} terms.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")

            if action == "edit_academic_year":
                pk = request.POST.get("pk")
                ay = get_object_or_404(AcademicYear, pk=pk)
                from django.utils import timezone
                today = timezone.now().date()
                if ay.end_date and ay.end_date < today:
                    messages.error(request, f"Cannot edit '{ay.name}' — it ended on {ay.end_date}. Past academic years are read-only.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                name = request.POST.get("name", "").strip()
                start_date_str = request.POST.get("start_date", "").strip()
                end_date_str = request.POST.get("end_date", "").strip()
                num_terms = int(request.POST.get("number_of_terms", ay.number_of_terms))
                is_current = request.POST.get("is_current") == "on"
                if not name:
                    messages.error(request, "Year name is required.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                from datetime import date as date_type
                if start_date_str:
                    try:
                        ay.start_date = date_type.fromisoformat(start_date_str)
                    except (ValueError, TypeError):
                        pass
                if end_date_str:
                    try:
                        ay.end_date = date_type.fromisoformat(end_date_str)
                    except (ValueError, TypeError):
                        pass
                ay.name = name
                ay.number_of_terms = num_terms
                ay.is_current = is_current
                # MUST-PREVENT (tracker 1.1): setting a second year as current (while another
                # year is already current) must be hard-blocked, not silently re-pointed.
                if is_current:
                    other_current = AcademicYear.objects.filter(is_current=True).exclude(pk=ay.pk).first()
                    if other_current:
                        messages.error(
                            request,
                            f"Cannot set '{name}' as the current academic year — "
                            f"'{other_current.name}' is already the current year. "
                            f"Clear its current flag first."
                        )
                        return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                try:
                    from django.core.exceptions import ValidationError as DjangoValidationError
                    ay.full_clean()
                except DjangoValidationError as e:
                    for field, errors in e.message_dict.items():
                        for err in errors:
                            messages.error(request, err)
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                ay.save()
                messages.success(request, f"Academic year '{name}' updated.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")

            if action == "delete_academic_year":
                pk = request.POST.get("pk")
                ay = get_object_or_404(AcademicYear, pk=pk)
                name = ay.name
                term_count = ay.terms.count()
                if term_count > 0:
                    messages.error(request, f"Cannot delete '{name}' — it has {term_count} term(s). Delete all terms first.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                try:
                    ay.delete()
                    messages.success(request, f"Academic year '{name}' deleted.")
                except ProtectedError as e:
                    blocked = str(e)
                    if "CambridgeCheckpointScore" in blocked:
                        messages.error(request, f"Cannot delete '{name}' — it has Cambridge Checkpoint scores. Remove those first.")
                    elif "ReportCard" in blocked:
                        messages.error(request, f"Cannot delete '{name}' — it has report cards. Remove those first.")
                    elif "ExamScore" in blocked:
                        messages.error(request, f"Cannot delete '{name}' — it has exam scores. Remove those first.")
                    else:
                        messages.error(request, f"Cannot delete '{name}' — it has associated records that must be removed first.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")

            if action == "add_term":
                ay_pk = request.POST.get("academic_year")
                term_name = request.POST.get("term_name", "").strip()
                start_date_str = request.POST.get("start_date")
                end_date_str = request.POST.get("end_date")
                grading_deadline = request.POST.get("grading_deadline") or None
                midterm_start = request.POST.get("midterm_exam_start_date") or None
                midterm_end = request.POST.get("midterm_exam_end_date") or None
                endterm_start = request.POST.get("endterm_exam_start_date") or None
                endterm_end = request.POST.get("endterm_exam_end_date") or None
                quiz_start = request.POST.get("quiz_start_date") or None
                quiz_end = request.POST.get("quiz_end_date") or None
                gm_mt_days = request.POST.get("midterm_grade_marking_days") or 10
                gm_et_days = request.POST.get("endterm_grade_marking_days") or 10
                gm_q_days = request.POST.get("quiz_grade_marking_days") or 5
                if not (ay_pk and term_name and start_date_str and end_date_str):
                    messages.error(request, "Academic year, term name, and dates are required.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                ay = get_object_or_404(AcademicYear, pk=ay_pk)
                if ay.is_published and not self._is_super_admin(request):
                    messages.error(request, f"Cannot add a term to published year '{ay.name}'. Only Super Admin can modify terms after publish.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                from datetime import date as date_type
                try:
                    sd = date_type.fromisoformat(start_date_str)
                    ed = date_type.fromisoformat(end_date_str)
                except (ValueError, TypeError):
                    messages.error(request, "Invalid date format.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                existing = Term.objects.filter(academic_year=ay, name=term_name).first()
                if existing:
                    messages.warning(request, f"Term '{term_name}' already exists for {ay.name}.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")

                def _parse_date(val):
                    if not val:
                        return None
                    try:
                        return date_type.fromisoformat(val)
                    except (ValueError, TypeError):
                        return None

                et_end_parsed = _parse_date(endterm_end)
                try:
                    gm_mt_days = int(gm_mt_days)
                except (ValueError, TypeError):
                    gm_mt_days = 10
                try:
                    gm_et_days = int(gm_et_days)
                except (ValueError, TypeError):
                    gm_et_days = 10
                try:
                    gm_q_days = int(gm_q_days)
                except (ValueError, TypeError):
                    gm_q_days = 5

                term = Term(
                    academic_year=ay, name=term_name,
                    start_date=sd, end_date=ed, grading_deadline=grading_deadline,
                    midterm_exam_start_date=_parse_date(midterm_start),
                    midterm_exam_end_date=_parse_date(midterm_end),
                    endterm_exam_start_date=_parse_date(endterm_start),
                    endterm_exam_end_date=et_end_parsed,
                    quiz_start_date=_parse_date(quiz_start),
                    quiz_end_date=_parse_date(quiz_end),
                    midterm_grade_marking_days=gm_mt_days,
                    endterm_grade_marking_days=gm_et_days,
                    quiz_grade_marking_days=gm_q_days,
                )
                try:
                    from django.core.exceptions import ValidationError as DjangoValidationError
                    term.full_clean()
                except DjangoValidationError as e:
                    for field, errors in e.message_dict.items():
                        for err in errors:
                            messages.error(request, err)
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                term.save()
                messages.success(request, f"Term '{term_name}' added to {ay.name} ({sd} to {ed}).")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")

            if action == "delete_term":
                pk = request.POST.get("pk")
                term = get_object_or_404(Term, pk=pk)
                if term.academic_year.is_published and not self._is_super_admin(request):
                    messages.error(request, f"Cannot delete a term from published year '{term.academic_year.name}'. Only Super Admin can modify terms after publish.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                name = str(term)
                try:
                    term.delete()
                    messages.success(request, f"Term '{name}' deleted.")
                except ProtectedError:
                    messages.error(request, f"Cannot delete '{name}' (it has associated records: lessons, exams, reports). Remove those first.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")

            if action == "edit_term":
                pk = request.POST.get("pk")
                term = get_object_or_404(Term, pk=pk)
                if term.academic_year.is_published and not self._is_super_admin(request):
                    messages.error(request, f"Term dates for published year '{term.academic_year.name}' can only be changed by Super Admin.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")

                def _parse_date(val):
                    if not val:
                        return None
                    try:
                        from datetime import date as date_type
                        return date_type.fromisoformat(val)
                    except (ValueError, TypeError):
                        return None

                term.start_date = _parse_date(request.POST.get("start_date")) or term.start_date
                term.end_date = _parse_date(request.POST.get("end_date")) or term.end_date
                term_name_new = request.POST.get("term_name", "").strip()
                if term_name_new:
                    term.name = term_name_new
                term.grading_deadline = _parse_date(request.POST.get("grading_deadline")) if request.POST.get("grading_deadline") else term.grading_deadline
                term.midterm_exam_start_date = _parse_date(request.POST.get("midterm_exam_start_date"))
                term.midterm_exam_end_date = _parse_date(request.POST.get("midterm_exam_end_date"))
                term.endterm_exam_start_date = _parse_date(request.POST.get("endterm_exam_start_date"))
                term.endterm_exam_end_date = _parse_date(request.POST.get("endterm_exam_end_date"))
                term.quiz_start_date = _parse_date(request.POST.get("quiz_start_date"))
                term.quiz_end_date = _parse_date(request.POST.get("quiz_end_date"))
                gm_mt_days_new = request.POST.get("midterm_grade_marking_days")
                gm_et_days_new = request.POST.get("endterm_grade_marking_days")
                gm_q_days_new = request.POST.get("quiz_grade_marking_days")
                if gm_mt_days_new is not None:
                    try:
                        term.midterm_grade_marking_days = int(gm_mt_days_new)
                    except (ValueError, TypeError):
                        pass
                if gm_et_days_new is not None:
                    try:
                        term.endterm_grade_marking_days = int(gm_et_days_new)
                    except (ValueError, TypeError):
                        pass
                if gm_q_days_new is not None:
                    try:
                        term.quiz_grade_marking_days = int(gm_q_days_new)
                    except (ValueError, TypeError):
                        pass
                try:
                    from django.core.exceptions import ValidationError as DjangoValidationError
                    term.full_clean()
                except DjangoValidationError as e:
                    for field, errors in e.message_dict.items():
                        for err in errors:
                            messages.error(request, err)
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                term.save()
                messages.success(request, f"Term '{term.name}' updated.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")

            if action == "publish_academic_year":
                if not self._is_super_admin(request):
                    messages.error(request, "Only Super Admin can publish an academic year.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                pk = request.POST.get("pk")
                ay = get_object_or_404(AcademicYear, pk=pk)
                ay.is_published = True
                try:
                    from django.core.exceptions import ValidationError as DjangoValidationError
                    ay.full_clean()
                except DjangoValidationError as e:
                    for field, errors in e.message_dict.items():
                        for err in errors:
                            messages.error(request, err)
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                ay.save()
                messages.success(request, f"Academic year '{ay.name}' published.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")

            if action == "unpublish_academic_year":
                if not self._is_super_admin(request):
                    messages.error(request, "Only Super Admin can unpublish an academic year.")
                    return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")
                pk = request.POST.get("pk")
                ay = get_object_or_404(AcademicYear, pk=pk)
                ay.is_published = False
                ay.save()
                messages.success(request, f"Academic year '{ay.name}' unpublished.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")

            # Fallback for unrecognized action on academic_year tab
            return redirect(f"{reverse_lazy('core:school_settings')}?tab=academic_year")

        # Handle groups tab
        if tab == "groups":
            from django.contrib.auth.models import Group, Permission
            if action == "add_group":
                name = request.POST.get("name", "").strip()
                if name:
                    group, created = Group.objects.get_or_create(name=name)
                    if created:
                        messages.success(request, f"Group '{name}' created.")
                    else:
                        messages.warning(request, f"Group '{name}' already exists.")
                else:
                    messages.error(request, "Group name is required.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=groups")

            if action == "edit_group":
                pk = request.POST.get("pk")
                group = get_object_or_404(Group, pk=pk)
                new_name = request.POST.get("name", "").strip()
                if new_name:
                    group.name = new_name
                    group.save()
                # Update permissions
                perm_ids = request.POST.getlist("permissions")
                group.permissions.set(Permission.objects.filter(pk__in=perm_ids))
                messages.success(request, f"Group '{group.name}' updated.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=groups")

            if action == "delete_group":
                pk = request.POST.get("pk")
                group = get_object_or_404(Group, pk=pk)
                name = group.name
                group.delete()
                messages.success(request, f"Group '{name}' deleted.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=groups")

            return redirect(f"{reverse_lazy('core:school_settings')}?tab=groups")

        # Handle induction checklist tab
        if tab == "induction_checklist":
            from hr.models import OnboardingChecklistItem
            from django.db.models import Max

            if action == "add_item":
                step = request.POST.get("step")
                item_name = request.POST.get("item_name", "").strip()
                description = request.POST.get("description", "").strip()
                is_required = request.POST.get("is_required") == "on"
                order_val = int(request.POST.get("order", 0))

                if step and item_name:
                    # Auto-assign order if 0: place at end of that step
                    if order_val == 0:
                        max_order = OnboardingChecklistItem.objects.filter(
                            step=step
                        ).aggregate(m=Max("order"))["m"]
                        order_val = (max_order or 0) + 10
                    OnboardingChecklistItem.objects.create(
                        step=int(step),
                        item_name=item_name,
                        description=description,
                        is_required=is_required,
                        order=order_val,
                    )
                    messages.success(request, f"Checklist item '{item_name}' added.")
                else:
                    messages.error(request, "Step and item name are required.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=induction_checklist")

            if action == "edit_item":
                pk = request.POST.get("pk")
                item = get_object_or_404(OnboardingChecklistItem, pk=pk)
                item.step = int(request.POST.get("step", item.step))
                item.item_name = request.POST.get("item_name", item.item_name).strip()
                item.description = request.POST.get("description", item.description).strip()
                item.is_required = request.POST.get("is_required") == "on"
                item.order = int(request.POST.get("order", item.order))
                item.save()
                messages.success(request, f"Item '{item.item_name}' updated.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=induction_checklist")

            if action == "delete_item":
                pk = request.POST.get("pk")
                item = get_object_or_404(OnboardingChecklistItem, pk=pk)
                name = item.item_name
                item.delete()
                messages.success(request, f"Item '{name}' deleted.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=induction_checklist")

            if action in ("move_up", "move_down"):
                pk = request.POST.get("pk")
                item = get_object_or_404(OnboardingChecklistItem, pk=pk)
                # Get sibling items in same step
                siblings = list(
                    OnboardingChecklistItem.objects.filter(step=item.step)
                    .order_by("order", "item_name")
                )
                idx = next((i for i, s in enumerate(siblings) if s.pk == item.pk), None)
                if idx is not None:
                    if action == "move_up" and idx > 0:
                        # Swap order with previous item
                        prev = siblings[idx - 1]
                        prev.order, item.order = item.order, prev.order
                        prev.save(update_fields=["order"])
                        item.save(update_fields=["order"])
                        messages.success(request, f"'{item.item_name}' moved up.")
                    elif action == "move_down" and idx < len(siblings) - 1:
                        nxt = siblings[idx + 1]
                        nxt.order, item.order = item.order, nxt.order
                        nxt.save(update_fields=["order"])
                        item.save(update_fields=["order"])
                        messages.success(request, f"'{item.item_name}' moved down.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=induction_checklist")

            return redirect(f"{reverse_lazy('core:school_settings')}?tab=induction_checklist")

        # Handle email templates tab
        if tab == "email_templates":
            from core.models import EmailTemplate

            if not request.user.has_perm("core.change_emailtemplate"):
                messages.error(request, "You do not have permission to modify email templates.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=email_templates")

            if action == "save_email_template":
                tpl_type = request.POST.get("template_type")
                if tpl_type:
                    tpl, _ = EmailTemplate.objects.get_or_create(
                        template_type=tpl_type,
                        defaults={"name": dict(EmailTemplate.TEMPLATE_TYPES).get(tpl_type, tpl_type)},
                    )
                    tpl.name = request.POST.get("name", tpl.name).strip()
                    tpl.subject = request.POST.get("subject", tpl.subject)
                    tpl.html_body = request.POST.get("html_body", tpl.html_body)
                    tpl.plain_body = request.POST.get("plain_body", tpl.plain_body)
                    tpl.is_enabled = request.POST.get("is_enabled") == "on"
                    tpl.is_default = False
                    tpl.save()
                    messages.success(request, f"Template '{tpl.name}' saved.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=email_templates")

            if action == "reset_email_template":
                tpl_type = request.POST.get("template_type")
                if tpl_type:
                    EmailTemplate.objects.filter(template_type=tpl_type).delete()
                    from core.email_templates import seed_default_templates
                    seed_default_templates()
                    messages.success(request, "Template reset to defaults.")
                return redirect(f"{reverse_lazy('core:school_settings')}?tab=email_templates")

            return redirect(f"{reverse_lazy('core:school_settings')}?tab=email_templates")

        # Default: save system settings
        form = SchoolSettingsSystemForm(request.POST, instance=self._get_settings())
        if form.is_valid():
            form.save()
            messages.success(request, "School settings updated successfully.")
        else:
            messages.error(request, "Please correct the errors below.")
        return redirect(f"{reverse_lazy('core:school_settings')}?tab=system")


# ═══════════════════════════════════════════════════════════════
# BULK IMPORT VIEWS
# ═══════════════════════════════════════════════════════════════

import csv
import io
from django.http import HttpResponse


class BulkImportView(RoleRequiredMixin, TemplateView):
    """Bulk import page for students and staff."""
    template_name = "core/bulk_import.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "core.view_schoolsettings"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["import_type"] = self.request.GET.get("type", "students")
        ctx["recent_imports"] = self._get_recent_imports(ctx["import_type"])
        role = getattr(self.request.user, 'role', None)
        ctx["is_admin_officer"] = role == UserRole.ADMIN_OFFICER
        ctx["is_head_of_school"] = role == UserRole.HEAD_OF_SCHOOL
        ctx["is_hod"] = role in {UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD}
        if self.request.user.has_perm("academics.view_gradeclass"):
            from academics.models import GradeClass as _GC
            ctx["classes_count"] = _GC.objects.count()
        # Check for preview data in session
        preview_key = f"bulk_preview_{ctx['import_type']}"
        if preview_key in self.request.session:
            preview_data = self.request.session.pop(preview_key)
            ctx["preview_rows"] = preview_data["rows"]
            ctx["csv_headers"] = preview_data["headers"]
            ctx["error_count"] = preview_data["error_count"]
            ctx["duplicate_count"] = preview_data["duplicate_count"]
            ctx["valid_count"] = preview_data["valid_count"]
        return ctx

    def _get_recent_imports(self, import_type):
        from audit.models import AuditLog
        return AuditLog.objects.filter(
            action_type="BULK_IMPORT",
            description__icontains=import_type,
        ).select_related("actor").order_by("-created_at")[:10]


class BulkImportTemplateView(RoleRequiredMixin, View):
    """Download CSV template for students or staff."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "core.view_schoolsettings"

    def get(self, request):
        import_type = request.GET.get("type", "students")

        if import_type == "students":
            headers = [
                "admission_no", "first_name", "last_name", "date_of_birth",
                "gender", "class_name", "stream_name", "phone",
                "nationality", "religion", "blood_type", "allergies_medical",
                "enrolment_date",
            ]
            filename = "hodari_student_import_template.csv"
        else:
            headers = [
                "username", "first_name", "last_name", "email",
                "role", "job_title", "department", "contact_phone",
                "employee_id", "employment_start_date", "gender",
                "nationality", "date_of_birth",
            ]
            filename = "hodari_staff_import_template.csv"

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)

        response = HttpResponse(output.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class BulkImportUploadView(RoleRequiredMixin, View):
    """Upload CSV and show preview with validation."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "core.change_schoolsettings"

    def post(self, request):
        import_type = request.GET.get("type", "students")
        csv_file = request.FILES.get("csv_file")

        if not csv_file:
            messages.error(request, "Please select a CSV file to upload.")
            return redirect(f"{reverse('core:bulk_import')}?type={import_type}")

        if not csv_file.name.endswith(".csv"):
            messages.error(request, "File must be a CSV.")
            return redirect(f"{reverse('core:bulk_import')}?type={import_type}")

        try:
            decoded = csv_file.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(decoded))
            rows = list(reader)
        except Exception as e:
            messages.error(request, f"Error reading CSV: {e}")
            return redirect(f"{reverse('core:bulk_import')}?type={import_type}")

        if not rows:
            messages.error(request, "CSV file is empty.")
            return redirect(f"{reverse('core:bulk_import')}?type={import_type}")

        headers = reader.fieldnames
        preview_rows = []
        error_count = 0
        duplicate_count = 0
        valid_count = 0

        for i, row in enumerate(rows):
            cells = [row.get(h, "") for h in headers]
            errors = []
            is_duplicate = False

            if import_type == "students":
                errors, is_duplicate = self._validate_student_row(row, i)
            else:
                errors, is_duplicate = self._validate_staff_row(row, i, request=request)

            if errors:
                error_count += 1
            elif is_duplicate:
                duplicate_count += 1
            else:
                valid_count += 1

            preview_rows.append({
                "cells": cells,
                "errors": errors,
                "is_duplicate": is_duplicate,
                "raw": row,
            })

        # Store preview in session for confirmation step
        request.session[f"bulk_preview_{import_type}"] = {
            "rows": preview_rows,
            "headers": headers,
            "error_count": error_count,
            "duplicate_count": duplicate_count,
            "valid_count": valid_count,
        }

        return redirect(f"{reverse('core:bulk_import')}?type={import_type}")

    def _validate_student_row(self, row, index):
        from students.models import Student
        errors = []
        is_duplicate = False

        if not row.get("first_name", "").strip():
            errors.append("First name required")
        if not row.get("last_name", "").strip():
            errors.append("Last name required")

        admission_no = row.get("admission_no", "").strip()
        if not admission_no:
            errors.append("Admission no required")
        elif Student.objects.filter(admission_no=admission_no).exists():
            is_duplicate = True

        dob = row.get("date_of_birth", "").strip()
        if dob:
            from datetime import datetime
            try:
                datetime.strptime(dob, "%Y-%m-%d")
            except ValueError:
                errors.append("Date format: YYYY-MM-DD")

        gender = row.get("gender", "").strip().lower()
        if gender and gender not in ("male", "female", "other"):
            errors.append("Gender: male, female, or other")

        return errors, is_duplicate

    def _validate_staff_row(self, row, index, request=None):
        from users.models import User
        errors = []
        is_duplicate = False

        if not row.get("first_name", "").strip():
            errors.append("First name required")
        if not row.get("last_name", "").strip():
            errors.append("Last name required")

        username = row.get("username", "").strip()
        if not username:
            errors.append("Username required")
        elif User.objects.filter(username=username).exists():
            is_duplicate = True

        role = row.get("role", "").strip()
        valid_roles = [c[0] for c in UserRole.choices]
        if role and role not in valid_roles:
            errors.append(f"Invalid role. Options: {', '.join(valid_roles[:5])}...")

        # Privilege escalation prevention: only Super Admin can assign high-level roles
        if role and request:
            importing_user_rank = ROLE_RANK.get(request.user.role, 0)
            target_role_rank = ROLE_RANK.get(role, 0)
            if target_role_rank >= importing_user_rank and request.user.role != UserRole.SUPER_ADMIN:
                errors.append(
                    f"You are not authorized to assign the '{role}' role. "
                    f"You can only assign roles below your own privilege level."
                )

        return errors, is_duplicate


class BulkImportConfirmView(RoleRequiredMixin, View):
    """Confirm and execute the bulk import."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "core.change_schoolsettings"

    def post(self, request):
        import_type = request.GET.get("type", "students")
        preview_key = f"bulk_preview_{import_type}"
        preview_data = request.session.get(preview_key)

        if not preview_data:
            messages.error(request, "No preview data found. Please upload again.")
            return redirect(f"{reverse('core:bulk_import')}?type={import_type}")

        rows = preview_data["rows"]
        imported = 0
        skipped = 0
        errors = 0

        for i, row_data in enumerate(rows):
            row = row_data["raw"]

            # Check skip/update for duplicates
            if row_data["is_duplicate"]:
                if request.POST.get(f"update_{i}") == "1":
                    # Update existing
                    try:
                        if import_type == "students":
                            self._update_student(row)
                        else:
                            self._update_staff(row, request=request)
                        imported += 1
                    except Exception as e:
                        errors += 1
                else:
                    skipped += 1
                continue

            # Skip rows with errors
            if row_data["errors"]:
                errors += 1
                continue

            # Import valid rows
            try:
                if import_type == "students":
                    self._create_student(row)
                else:
                    self._create_staff(row, request=request)
                imported += 1
            except Exception as e:
                errors += 1

        # Log to audit trail
        from audit.models import AuditLog
        AuditLog.objects.create(
            actor=request.user,
            action_type="BULK_IMPORT",
            model_name=import_type.title(),
            description=(
                f"Bulk {import_type} import: {imported} imported, "
                f"{skipped} skipped, {errors} errors."
            ),
        )

        # Clear preview data
        request.session.pop(preview_key, None)

        if imported > 0:
            messages.success(request, f"Imported {imported} {import_type}. {skipped} skipped, {errors} errors.")
        elif skipped > 0:
            messages.warning(request, f"All {skipped} rows were skipped (duplicates). {errors} errors.")
        else:
            messages.error(request, f"Import failed. {errors} errors found.")

        return redirect(f"{reverse('core:bulk_import')}?type={import_type}")

    def _create_student(self, row):
        from students.models import Student
        from datetime import datetime

        dob = None
        if row.get("date_of_birth"):
            try:
                dob = datetime.strptime(row["date_of_birth"].strip(), "%Y-%m-%d").date()
            except ValueError:
                pass

        enrolment_date = None
        if row.get("enrolment_date"):
            try:
                enrolment_date = datetime.strptime(row["enrolment_date"].strip(), "%Y-%m-%d").date()
            except ValueError:
                pass

        Student.objects.create(
            admission_no=row["admission_no"].strip(),
            first_name=row["first_name"].strip(),
            last_name=row["last_name"].strip(),
            date_of_birth=dob,
            gender=row.get("gender", "").strip().lower(),
            class_name=row.get("class_name", "").strip(),
            stream_name=row.get("stream_name", "").strip(),
            phone=row.get("phone", "").strip(),
            nationality=row.get("nationality", "").strip(),
            religion=row.get("religion", "").strip(),
            blood_type=row.get("blood_type", "").strip().lower(),
            allergies_medical=row.get("allergies_medical", "").strip(),
            enrolment_date=enrolment_date,
        )

    def _update_student(self, row):
        from students.models import Student
        from datetime import datetime

        student = Student.objects.get(admission_no=row["admission_no"].strip())
        if row.get("first_name"):
            student.first_name = row["first_name"].strip()
        if row.get("last_name"):
            student.last_name = row["last_name"].strip()
        if row.get("class_name"):
            student.class_name = row["class_name"].strip()
        if row.get("gender"):
            student.gender = row["gender"].strip().lower()
        if row.get("phone"):
            student.phone = row["phone"].strip()
        student.save()

    def _create_staff(self, row, request=None):
        from users.models import User
        from django.utils.crypto import get_random_string

        raw_password = get_random_string(length=10)
        name_parts = f"{row.get('first_name', '').strip()} {row.get('last_name', '').strip()}".strip()

        # Privilege escalation prevention: gate role assignment
        requested_role = row.get("role", "teacher").strip()
        if request and request.user.role != UserRole.SUPER_ADMIN:
            importing_user_rank = ROLE_RANK.get(request.user.role, 0)
            target_role_rank = ROLE_RANK.get(requested_role, 0)
            if target_role_rank >= importing_user_rank:
                requested_role = "teacher"

        user = User.objects.create(
            username=row["username"].strip(),
            first_name=row.get("first_name", "").strip(),
            last_name=row.get("last_name", "").strip(),
            email=row.get("email", "").strip(),
            role=requested_role,
            must_change_password=True,
        )
        user.set_password(raw_password)
        user.save()

        # Send activation email if email provided
        email = (user.email or "").strip()
        if email:
            from communications.email_service import send_email_safe
            from django.template.loader import render_to_string
            from core.models import SchoolSettings

            school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"
            login_url = getattr(settings, 'SITE_URL', 'http://localhost:8000') + '/accounts/login/'

            tpl_context = {
                "user": user,
                "temp_password": raw_password,
                "login_url": login_url,
                "school_name": school_name,
                "site_url": getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000'),
                "static_url": getattr(settings, 'STATIC_URL', '/static/'),
            }

            # Try dynamic DB template first
            from core.email_templates import send_dynamic_email
            db_sent = send_dynamic_email(
                template_type="activation",
                to_email=email,
                context=tpl_context,
                actor=request.user if request else None,
            )

            if not db_sent:
                plain_message = (
                    f"Hello {user.get_full_name() or user.username},\n\n"
                    f"A staff account has been created for you at {school_name}.\n\n"
                    f"Login URL: {login_url}\n"
                    f"Username: {user.username}\n"
                    f"Temporary Password: {raw_password}\n\n"
                    f"Please log in and change your password immediately.\n"
                    f"You will be prompted to set a new password on your first login.\n\n"
                    f"{school_name}"
                )
                try:
                    html_message = render_to_string("registration/activation_email.html", tpl_context)
                except Exception:
                    html_message = None

                send_email_safe(
                    to_email=email,
                    subject=f"Your Staff Account Has Been Created - {school_name}",
                    body=plain_message,
                    html_body=html_message or plain_message,
                    actor=request.user if request else None,
                    action_type="ACTIVATION",
                )

        # Create StaffProfile if job_title provided
        if row.get("job_title"):
            from hr.models import StaffProfile
            from datetime import datetime

            start_date = None
            if row.get("employment_start_date"):
                try:
                    start_date = datetime.strptime(row["employment_start_date"].strip(), "%Y-%m-%d").date()
                except ValueError:
                    start_date = timezone.now().date()

            StaffProfile.objects.create(
                user=user,
                full_name=name_parts,
                job_title=row["job_title"].strip(),
                department=row.get("department", "").strip(),
                contact_phone=row.get("contact_phone", "").strip(),
                contact_email=row.get("email", "").strip(),
                employee_id=row.get("employee_id", "").strip() or None,
                employment_start_date=start_date or timezone.now().date(),
                gender=row.get("gender", "").strip().lower(),
                nationality=row.get("nationality", "").strip(),
            )

    def _update_staff(self, row, request=None):
        from users.models import User

        user = User.objects.get(username=row["username"].strip())
        if row.get("first_name"):
            user.first_name = row["first_name"].strip()
        if row.get("last_name"):
            user.last_name = row["last_name"].strip()
        if row.get("email"):
            user.email = row["email"].strip()
        if row.get("role"):
            # Privilege escalation prevention: gate role assignment
            requested_role = row["role"].strip()
            if request and request.user.role != UserRole.SUPER_ADMIN:
                importing_user_rank = ROLE_RANK.get(request.user.role, 0)
                target_role_rank = ROLE_RANK.get(requested_role, 0)
                if target_role_rank >= importing_user_rank:
                    requested_role = "teacher"
            user.role = requested_role
        user.save()

        # Update StaffProfile if it exists
        if hasattr(user, "staff_profile"):
            profile = user.staff_profile
            if row.get("job_title"):
                profile.job_title = row["job_title"].strip()
            if row.get("department"):
                profile.department = row["department"].strip()
            if row.get("contact_phone"):
                profile.contact_phone = row["contact_phone"].strip()
            profile.save()
class SessionCheckView(RoleRequiredMixin, View):
    """API endpoint: returns seconds remaining before session expires."""
    allowed_roles = [
        UserRole.HEAD_OF_SCHOOL,
        UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD,
        UserRole.ADMIN_OFFICER,
        UserRole.FINANCE_OFFICER,
        UserRole.TEACHER,
        UserRole.SUPER_ADMIN,
        UserRole.PARENT,
    ]

    def get(self, request):
        from django.conf import settings as django_settings
        idle_timeout = getattr(django_settings, "SESSION_IDLE_TIMEOUT", 1800)
        last_activity = request.session.get("_last_activity")
        now_ts = timezone.now().timestamp()
        if not last_activity:
            last_activity = now_ts
        idle_seconds = now_ts - last_activity
        remaining = max(0, int(idle_timeout - idle_seconds))
        # Touch the session so the cache backend resets its TTL (prevents Redis
        # from evicting the session data while the user is still active), but
        # do NOT update _last_activity — that would defeat the idle timer.
        request.session.modified = True
        return JsonResponse({"seconds_remaining": remaining, "idle_timeout": idle_timeout})


class SessionExtendView(RoleRequiredMixin, View):
    """API endpoint: refreshes the session activity timestamp."""
    allowed_roles = [
        UserRole.HEAD_OF_SCHOOL,
        UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD,
        UserRole.ADMIN_OFFICER,
        UserRole.FINANCE_OFFICER,
        UserRole.TEACHER,
        UserRole.SUPER_ADMIN,
        UserRole.PARENT,
    ]

    def post(self, request):
        request.session["_last_activity"] = timezone.now().timestamp()
        return JsonResponse({"status": "extended"})
