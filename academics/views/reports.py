import re
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone
from django.views.generic import CreateView, UpdateView, TemplateView, View
import json

from academics.ecd_utils import build_ecd_report_context, ecd_template_type_from_class_name, grade_class_names_for_department
from academics.views.ecd import ECDEvaluationEntryView
from academics.forms import LessonPlanForm, LessonPlanReviewForm, ExamScoreFilterForm, SubjectForm, TermForm
from academics.utils import get_current_term
from academics.models import (
    AcademicYear,
    Department,
    ECDEvaluation,
    ExamScore,
    ExamType,
    LessonPlan,
    LessonPlanAttachment,
    LessonPlanStatus,
    GradeClass,
    ProgressionConfig,
    ProgressionCase,
    ProgressionStatus,
    ProgressionOutcome,
    PromotionRun,
    ReportCard,
    ReportCardStatus,
    ScoreStatus,
    Term,
    GradeClass,
    get_exam_weights,
    get_active_exam_types,
    get_active_ecd_templates,
    ABCPaceProgress,
    ABCScripture,
    ABCReadingProgramme,
    ABCGeneralAssignment,
    ABCInternalExam,
    Subject,
)
from academics.services import generate_class_reports, sign_off_report, calculate_progression_cases
from audit.models import log_event
from students.models import Student, StudentStatus, EnrollmentHistory
from users.models import UserRole
from core.permissions import RoleRequiredMixin
from core.teacher_context import get_teacher_assigned_classes, get_teacher_assigned_classes_from_tca, is_ecd_teacher
from academics.grading_utils import (
    compute_grade_with_gaps, get_grade_from_score, get_grade_label, get_full_grade_display,
    is_pass, is_at_risk, is_critical
)


class ReportRouterView(RoleRequiredMixin, View):
    """Router to send users to their appropriate reports view based on role."""
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.TEACHER, UserRole.PARENT]
    required_permission = "academics.view_reportcard"

    def get(self, request, *args, **kwargs):
        role = request.user.role
        if role == UserRole.PARENT:
            return redirect("academics:parent_reports")
        elif role in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD}:
            return redirect("academics:analytics")
        elif role == UserRole.TEACHER:
            return redirect("academics:exam_scores_entry")
        else:
            return redirect("/")


class HOSSignOffListView(RoleRequiredMixin, TemplateView):
    template_name = "academics/hos_signoff_list.html"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.view_reportcard"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.request.user.role not in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN}:
            raise PermissionDenied()
            
        term = get_current_term()
        ctx["current_term"] = term
        
        # Group report cards by class
        if term:
            classes_qs = Student.objects.filter(is_archived=False).values_list("class_name", flat=True).distinct()
            
            # FR-ACAD-011: HOD Scoping
            if self.request.user.role == UserRole.PRIMARY_HOD:
                from academics.models import GradeClass, Department
                primary_classes = GradeClass.objects.filter(department=Department.PRIMARY).values_list('name', flat=True)
                classes_qs = classes_qs.filter(class_name__in=primary_classes)
            elif self.request.user.role == UserRole.ECD_HOD:
                from academics.models import GradeClass, Department
                ecd_classes = GradeClass.objects.filter(department=Department.ECD).values_list('name', flat=True)
                classes_qs = classes_qs.filter(class_name__in=ecd_classes)
            classes = list(classes_qs)
            # Batch fetch all report cards in one query instead of N*4 queries
            from collections import defaultdict
            all_reports = ReportCard.objects.filter(
                term=term, student__class_name__in=classes
            ).select_related("student")
            reports_by_class = defaultdict(list)
            for r in all_reports:
                reports_by_class[r.student.class_name].append(r)

            class_stats = []
            for c in classes:
                class_reports = reports_by_class.get(c, [])
                total = len(class_reports)
                if total > 0:
                    published = sum(1 for r in class_reports if r.status == ReportCardStatus.PUBLISHED)
                    pending = total - published
                    blocked = sum(
                        1 for r in class_reports
                        if r.status != ReportCardStatus.PUBLISHED
                        and r.is_ecd_report
                        and r.ecd_template_type in ["kindergarten", "pre_school", "abc"]
                        and len((r.teacher_comments or "").strip()) < 50
                    )
                    class_stats.append({
                        "class_name": c,
                        "total": total,
                        "published": published,
                        "pending": pending,
                        "blocked": blocked,
                        "can_sign_off_all": pending > 0 and blocked == 0,
                    })
            ctx["class_stats"] = class_stats
            
            # Action: generate missing reports
            if "generate" in self.request.GET:
                cls_to_gen = self.request.GET.get("generate")
                generate_class_reports(term.id, cls_to_gen, self.request.user)
                
        # Sign-off window gating
        from django.utils import timezone as _tz
        today = _tz.localdate()
        if term:
            grading_dl = getattr(term, "grading_deadline", None)
            endterm_end = getattr(term, "endterm_exam_end_date", None)
            if grading_dl and today < grading_dl:
                ctx["signoff_blocked"] = True
                ctx["signoff_block_reason"] = f"Grading deadline not reached ({grading_dl.strftime('%d %b %Y')})"
            elif endterm_end and today < endterm_end:
                ctx["signoff_blocked"] = True
                ctx["signoff_block_reason"] = f"Endterm exams not yet ended ({endterm_end.strftime('%d %b %Y')})"
            else:
                ctx["signoff_blocked"] = False
                ctx["signoff_block_reason"] = ""
        else:
            ctx["signoff_blocked"] = False
            ctx["signoff_block_reason"] = ""
                
        ctx["academics_tab"] = "hos_signoff"
                
        return ctx


from django.core.management import call_command


class ReportCardListView(RoleRequiredMixin, TemplateView):
    template_name = "academics/report_card_list.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.ADMIN_OFFICER, UserRole.TEACHER]
    required_permission = "academics.view_reportcard"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        class_name = self.request.GET.get("class_name")
        current_term = get_current_term()
        term_id = self.request.GET.get("term_id")
        if term_id:
            try:
                term = Term.objects.get(id=term_id)
            except (Term.DoesNotExist, ValueError):
                term = current_term
        else:
            term = current_term
        ctx["class_name"] = class_name
        ctx["current_term"] = current_term
        ctx["term"] = term
        ctx["term_id"] = term.id if term else ""
        ctx["all_terms"] = Term.objects.all().order_by("-start_date")

        # RBAC: teachers restricted to their class-teacher-only classes
        if self.request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            all_teacher_classes = get_teacher_assigned_classes_from_tca(self.request.user)
            class_teacher_classes = [cls for cls, info in all_teacher_classes.items() if info.get("is_class_teacher")]
            if class_name and class_name not in class_teacher_classes:
                class_name = None
                ctx["class_name"] = None
                ctx["class_error"] = "You are not the class teacher for this class."
            ctx["available_classes"] = class_teacher_classes
        else:
            from academics.models import GradeClass, Department
            dept_ctx = Department.PRIMARY
            if self.request.user.role in [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL]:
                available = GradeClass.objects.all().values_list("name", flat=True)
            elif self.request.user.role == UserRole.PRIMARY_HOD:
                available = GradeClass.objects.filter(department=Department.PRIMARY).values_list("name", flat=True)
            elif self.request.user.role == UserRole.ECD_HOD:
                available = GradeClass.objects.filter(department=Department.ECD).values_list("name", flat=True)
            else:
                available = GradeClass.objects.filter(department=Department.PRIMARY).values_list("name", flat=True)
            ctx["available_classes"] = list(available)

        if class_name and term:
            reports = ReportCard.objects.filter(
                student__class_name=class_name, 
                term=term
            ).select_related("student").order_by("student__last_name", "student__first_name")
            ctx["reports"] = reports
            ctx["has_pending"] = reports.filter(status=ReportCardStatus.PENDING_SIGN_OFF).exists()

        # Sign-off window gating
        from django.utils import timezone as _tz
        today = _tz.localdate()
        if term:
            grading_dl = getattr(term, "grading_deadline", None)
            endterm_end = getattr(term, "endterm_exam_end_date", None)
            if grading_dl and today < grading_dl:
                ctx["signoff_blocked"] = True
                ctx["signoff_block_reason"] = f"Grading deadline not reached ({grading_dl.strftime('%d %b %Y')})"
            elif endterm_end and today < endterm_end:
                ctx["signoff_blocked"] = True
                ctx["signoff_block_reason"] = f"Endterm exams not yet ended ({endterm_end.strftime('%d %b %Y')})"
            else:
                ctx["signoff_blocked"] = False
                ctx["signoff_block_reason"] = ""
        else:
            ctx["signoff_blocked"] = False
            ctx["signoff_block_reason"] = ""

        ctx["academics_tab"] = "reports"
        return ctx


def _build_report_card_context(report):
    """
    Build the shared context dict for report card rendering (both preview and PDF).

    Returns a dict with keys: report, student, attendance, subjects,
    checkpoint_scores (if applicable), and ecd (if ECD report).
    """
    ctx = {"report": report, "student": report.student}

    from attendance.models import AttendanceEntry, AttendanceStatus
    term = report.term
    att_filter = {"student": report.student}
    if term and term.start_date and term.end_date:
        att_filter["date__range"] = (term.start_date, term.end_date)
    entries = AttendanceEntry.objects.filter(**att_filter)
    ctx["attendance"] = {
        "present": entries.filter(status=AttendanceStatus.PRESENT).count(),
        "absent": entries.filter(status=AttendanceStatus.ABSENT).count(),
        "late": entries.filter(status=AttendanceStatus.LATE).count(),
    }

    if report.is_ecd_report:
        ctx["ecd"] = build_ecd_report_context(report)
        return ctx

    # Primary report: attendance rate
    a = ctx["attendance"]
    total = a["present"] + a["absent"] + a["late"]
    a["rate"] = int((a["present"] / total * 100)) if total > 0 else 100

    # Subject scores with weighted averages and gap detection
    scores = ExamScore.objects.filter(
        student=report.student, term=report.term, status=ScoreStatus.APPROVED
    )
    subjects = {}
    for s in scores:
        if s.subject_name not in subjects:
            subjects[s.subject_name] = {}
        subjects[s.subject_name][s.exam_type] = float(s.score)
    exam_weights = get_exam_weights()
    ctx["exam_weights"] = exam_weights
    for subj, exams in subjects.items():
        grade_result = compute_grade_with_gaps(
            scores=exams,
            weights=exam_weights,
        )
        subjects[subj]["quiz"] = exams.get("quiz")
        subjects[subj]["mid"] = exams.get("mid_term")
        subjects[subj]["end"] = exams.get("end_of_term")
        subjects[subj]["avg"] = grade_result["average"]
        subjects[subj]["grade"] = grade_result["full_grade"]
        subjects[subj]["grade_letter"] = grade_result["full_grade"].split()[-1].strip("()") if grade_result["average"] is not None else "N/A"
        subjects[subj]["gap_case"] = grade_result["gap"]["case"]
        subjects[subj]["gap_label"] = grade_result["gap"]["label"]
        subjects[subj]["redistributed_weights"] = grade_result["redistributed_weights"]
        subjects[subj]["makeup_required"] = grade_result["makeup_required"]
    ctx["subjects"] = subjects

    # Compute overall_average if not set on the report
    if report.overall_average is None and subjects:
        total = Decimal(0)
        count = 0
        for subj, data in subjects.items():
            if data.get("avg") is not None:
                total += Decimal(str(data["avg"]))
                count += 1
        if count > 0:
            ctx["computed_average"] = total / count

    # Cambridge Checkpoint (FR-ACAD-005) -- Grades 6, 7, 8, and 9
    if report.student.class_name in ["Grade 6", "Grade 7", "Grade 8", "Grade 9"]:
        from academics.models import CambridgeCheckpointScore
        ctx["checkpoint_scores"] = CambridgeCheckpointScore.objects.filter(
            student=report.student,
            academic_year=report.term.academic_year,
        )

    return ctx


class HOSReportPreviewView(RoleRequiredMixin, TemplateView):
    template_name = "academics/report_card_print.html"
    login_url = "/accounts/login/"
    required_permission = "academics.view_reportcard"

    def get_template_names(self):
        report = get_object_or_404(ReportCard, pk=self.kwargs["pk"])
        if report.is_ecd_report:
            return ["academics/ecd_report_preview.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        if not user.has_perm("academics.view_reportcard"):
            raise PermissionDenied()

        report = get_object_or_404(ReportCard, pk=kwargs["pk"])

        # Teachers: only their class-teacher classes
        if user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            all_classes = get_teacher_assigned_classes_from_tca(user)
            class_teacher_classes = [cls for cls, info in all_classes.items() if info.get("is_class_teacher")]
            if report.student.class_name not in class_teacher_classes:
                raise PermissionDenied()

        # Parents: only their own children's published reports
        if user.role == UserRole.PARENT:
            if report.status != ReportCardStatus.PUBLISHED:
                raise PermissionDenied()
            from students.models import ParentGuardian, StudentGuardian
            guardian = ParentGuardian.objects.filter(user=user).first()
            if not guardian:
                raise PermissionDenied()
            child_ids = StudentGuardian.objects.filter(
                guardian=guardian
            ).values_list("student_id", flat=True)
            if report.student_id not in child_ids:
                raise PermissionDenied()

        ctx.update(_build_report_card_context(report))
        
        # Sign-off window gating for preview
        from django.utils import timezone as _tz
        today = _tz.localdate()
        term = report.term
        if term:
            grading_dl = getattr(term, "grading_deadline", None)
            endterm_end = getattr(term, "endterm_exam_end_date", None)
            if grading_dl and today < grading_dl:
                ctx["signoff_blocked"] = True
                ctx["signoff_block_reason"] = f"Grading deadline not reached ({grading_dl.strftime('%d %b %Y')})"
            elif endterm_end and today < endterm_end:
                ctx["signoff_blocked"] = True
                ctx["signoff_block_reason"] = f"Endterm exams not yet ended ({endterm_end.strftime('%d %b %Y')})"
            else:
                ctx["signoff_blocked"] = False
                ctx["signoff_block_reason"] = ""
        else:
            ctx["signoff_blocked"] = False
            ctx["signoff_block_reason"] = ""
        
        return ctx


class HOSSignOffActionView(RoleRequiredMixin, View):
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.change_reportcard"
    def post(self, request, *args, **kwargs):
        if request.user.role not in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD}:
            raise PermissionDenied()
            
        report_id = request.POST.get("report_id")
        class_name = request.POST.get("class_name")
        term_id = request.POST.get("term_id")
        action = request.POST.get("action", "signoff") # Default to signoff for backward compatibility
        reason = request.POST.get("reason", "")
        
        from academics.services import sign_off_report, reject_report_for_edit
        
        if report_id:
            try:
                report = ReportCard.objects.get(pk=report_id)
                if action == "reject":
                    reject_report_for_edit(report, request.user, reason=reason)
                    messages.success(request, f"Report for {report.student.first_name} returned to teacher for correction.")
                else:
                    sign_off_report(report, request.user)
                    messages.success(request, f"Report for {report.student.first_name} signed off and published.")
            except Exception as e:
                messages.error(request, str(e))
        elif class_name and term_id:
            reports = ReportCard.objects.filter(student__class_name=class_name, term_id=term_id).exclude(status=ReportCardStatus.PUBLISHED)
            processed_count = 0
            err_count = 0
            for r in reports:
                try:
                    if action == "reject":
                        reject_report_for_edit(r, request.user, reason=reason)
                    else:
                        sign_off_report(r, request.user)
                    processed_count += 1
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Bulk %s failed for report %s: %s", action, r.pk, exc,
                    )
                    err_count += 1
            
            msg = f"Bulk {action}: {processed_count} successful, {err_count} blocked."
            messages.success(request, msg)
            
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect("academics:analytics")


class ReportReviewQueueView(RoleRequiredMixin, TemplateView):
    """HOD/HOS review queue for reports with submitted comments + approved scores."""
    template_name = "academics/report_review_queue.html"
    allowed_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.view_reportcard"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from django.db.models import Q

        term = get_current_term()
        ctx["term"] = term

        if not term:
            return ctx

        user = self.request.user
        dept_filter = self.request.GET.get("dept", "")

        # Determine allowed departments
        if user.role == UserRole.PRIMARY_HOD:
            allowed_depts = [Department.PRIMARY]
        elif user.role == UserRole.ECD_HOD:
            allowed_depts = [Department.ECD]
        elif user.role == UserRole.LOWER_SECONDARY_HOD:
            allowed_depts = [Department.LOWER_SECONDARY]
        else:
            allowed_depts = [Department.PRIMARY, Department.ECD, Department.LOWER_SECONDARY]

        if dept_filter == "primary":
            allowed_depts = [Department.PRIMARY]
        elif dept_filter == "ecd":
            allowed_depts = [Department.ECD]
        elif dept_filter == "lower_secondary":
            allowed_depts = [Department.LOWER_SECONDARY]

        dept_class_names = list(
            GradeClass.objects.filter(department__in=allowed_depts).values_list("name", flat=True)
        )

        # Action: generate missing reports for a class
        if "generate" in self.request.GET:
            cls_to_gen = self.request.GET.get("generate")
            if cls_to_gen in dept_class_names:
                from academics.services import generate_class_reports
                generate_class_reports(term.id, cls_to_gen, self.request.user)
                messages.success(self.request, f"Generated missing report cards for {cls_to_gen}.")

        # Reports with comments submitted, not yet published -- includes ECD + Primary
        status_filter = self.request.GET.get("status", "")
        reports_qs = ReportCard.objects.filter(
            term=term,
            student__class_name__in=dept_class_names,
        )
        if status_filter == "published":
            reports_qs = reports_qs.filter(status=ReportCardStatus.PUBLISHED)
        elif status_filter == "pending":
            reports_qs = reports_qs.filter(comments_submitted=True).exclude(status=ReportCardStatus.PUBLISHED)
        else:
            reports_qs = reports_qs.filter(comments_submitted=True).exclude(status=ReportCardStatus.PUBLISHED)

        reports = reports_qs.select_related("student", "generated_by").order_by("student__class_name", "student__last_name")

        # Separate ECD vs Primary for score-status checks
        from academics.models import ScoreStatus, ExamScore, ECDEvaluation
        from collections import defaultdict

        primary_student_ids = [rc.student_id for rc in reports if not rc.is_ecd_report]
        ecd_student_ids = [rc.student_id for rc in reports if rc.is_ecd_report]

        # Batch load ExamScore statuses for Primary students
        student_statuses = defaultdict(set)
        if primary_student_ids:
            score_batch = ExamScore.objects.filter(
                student_id__in=primary_student_ids, term=term
            ).values("student_id", "status").distinct()
            for row in score_batch:
                student_statuses[row["student_id"]].add(row["status"])

        # Batch load ECDEvaluation ratings for ECD students
        ecd_complete = set()
        if ecd_student_ids:
            from academics.ecd_utils import ecd_template_type_from_class_name
            for rc in reports:
                if rc.is_ecd_report:
                    student_id = rc.student_id
                    tpl = rc.ecd_template_type or ecd_template_type_from_class_name(rc.student.class_name)
                    if not tpl:
                        continue
                    required = set(ECDEvaluationEntryView.get_flat_domains(tpl))
                    existing = set(
                        ECDEvaluation.objects.filter(
                            report_card__student_id=student_id,
                            report_card__term=term,
                            rating__in=["E", "G", "S", "N"],
                        ).values_list("domain", flat=True)
                    )
                    if required and required.issubset(existing):
                        ecd_complete.add(student_id)

        rows = []
        for rc in reports:
            comment_ok = len((rc.teacher_comments or "").strip()) >= 50

            if rc.is_ecd_report:
                all_approved = rc.student_id in ecd_complete
                pending_count = 0
            else:
                statuses = student_statuses.get(rc.student_id, set())
                all_approved = bool(statuses) and all(s == ScoreStatus.APPROVED for s in statuses)
                pending_count = sum(1 for s in statuses if s in {ScoreStatus.SUBMITTED, ScoreStatus.RETURNED})

            rows.append({
                "report": rc,
                "student": rc.student,
                "all_scores_approved": all_approved,
                "pending_score_count": pending_count,
                "comment_ok": comment_ok,
                "overall_average": rc.overall_average,
                "class_name": rc.student.class_name,
                "teacher": rc.generated_by,
                "is_ecd": rc.is_ecd_report,
            })

        ctx["reports"] = rows
        ctx["status_filter"] = status_filter

        # Always compute pending counts regardless of current filter
        pending_reports = ReportCard.objects.filter(
            term=term,
            student__class_name__in=dept_class_names,
            comments_submitted=True,
        ).exclude(status=ReportCardStatus.PUBLISHED).select_related("student", "generated_by")

        pending_rows = []
        for rc in pending_reports:
            if rc.is_ecd_report:
                all_a = rc.student_id in ecd_complete
                p_count = 0
            else:
                sts = student_statuses.get(rc.student_id, set())
                all_a = bool(sts) and all(s == ScoreStatus.APPROVED for s in sts)
                p_count = sum(1 for s in sts if s in {ScoreStatus.SUBMITTED, ScoreStatus.RETURNED})
            comment_ok = len((rc.teacher_comments or "").strip()) >= 50
            pending_rows.append({"all_scores_approved": all_a, "comment_ok": comment_ok})

        ctx["total_pending"] = len(pending_rows)
        ctx["total_ready"] = len([r for r in pending_rows if r["all_scores_approved"] and r["comment_ok"]])

        # Count published reports for this term+department
        published_count = ReportCard.objects.filter(
            term=term,
            student__class_name__in=dept_class_names,
            status=ReportCardStatus.PUBLISHED,
        ).count()
        ctx["total_published"] = published_count
        ctx["academics_tab"] = "review_queue"
        return ctx

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action", "approve")
        report_id = request.POST.get("report_id")
        reason = request.POST.get("reason", "")

        from academics.services import sign_off_report, reject_report_for_edit, revoke_report_signoff

        if not report_id:
            messages.error(request, "No report specified.")
            return redirect("academics:report_review_queue")

        try:
            report = ReportCard.objects.get(pk=report_id)
        except ReportCard.DoesNotExist:
            messages.error(request, "Report not found.")
            return redirect("academics:report_review_queue")

        try:
            if action == "ecd_hod_approve":
                # ECD HOD approves ECD report before HOS sign-off
                if request.user.role not in {UserRole.ECD_HOD, UserRole.SUPER_ADMIN}:
                    raise PermissionDenied("Only ECD HOD or Super Admin can approve ECD reports.")
                if not report.is_ecd_report:
                    messages.error(request, "This action is only for ECD reports.")
                    return redirect("academics:report_review_queue")
                report.ecd_hod_approved_by = request.user
                report.ecd_hod_approved_at = timezone.now()
                report.save(update_fields=["ecd_hod_approved_by", "ecd_hod_approved_at", "updated_at"])
                from audit.models import log_event
                log_event(
                    actor=request.user,
                    action_type="ECD_REPORT_APPROVED",
                    model_name="ReportCard",
                    object_id=report.pk,
                    description=f"ECD report approved by HOD: {report.student.admission_no}",
                )
                messages.success(request, f"ECD report for {report.student.first_name} approved by HOD.")
            elif action == "reject":
                # GRD-009: Only HOS, Super Admin, or HOD can reject reports.
                if request.user.role not in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD}:
                    raise PermissionDenied("Only HOS, Super Admin, or HOD can reject reports.")
                reject_report_for_edit(report, request.user, reason=reason)
                messages.success(request, f"Report for {report.student.first_name} returned for correction.")
            elif action == "revoke":
                # GRD-016: Only HOS or Super Admin can revoke.
                if request.user.role not in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN}:
                    raise PermissionDenied("Only HOS or Super Admin can revoke a signed-off report.")
                if not reason:
                    messages.error(request, "A reason is required to revoke a report sign-off.")
                    return redirect("academics:report_review_queue")
                revoke_report_signoff(report, request.user, reason=reason)
                messages.success(request, f"Report sign-off revoked for {report.student.first_name}.")
            else:
                # GRD-009: Only HOS or Super Admin can sign off.
                if request.user.role not in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN}:
                    raise PermissionDenied("Only the Head of School or Super Admin can sign off reports.")
                sign_off_report(report, request.user)
                messages.success(request, f"Report for {report.student.first_name} signed off and published.")
        except Exception as e:
            messages.error(request, str(e))

        return redirect("academics:report_review_queue")


class AcademicAnalyticsView(RoleRequiredMixin, TemplateView):
    template_name = "academics/analytics.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.view_examscore"

    def _compute_term_analytics(self, term, role, dept_filter, selected_class, weights):
        """Compute all analytics metrics for a given term. Returns dict of computed values."""
        from django.db.models import Avg, Count, Q, Sum, F, Case, When, Value, FloatField, ExpressionWrapper
        from academics.models import Department, GradeClass, ExamType, ReportCard, ReportCardStatus

        result = {}
        if not term:
            return result

        # -- ECD Analytics Path (uses ECDEvaluation ratings, not ExamScore) --
        if role == UserRole.ECD_HOD:
            ecd_classes = GradeClass.objects.filter(department=Department.ECD).values_list('name', flat=True)
            rating_map = {"E": 4, "G": 3, "S": 2, "N": 1}

            rc_filter = Q(student__class_name__in=ecd_classes, is_ecd_report=True, term=term)
            report_cards = ReportCard.objects.filter(rc_filter).exclude(
                status=ReportCardStatus.DRAFT
            ).prefetch_related("ecd_evaluations", "student")

            # Per-student averages
            student_avgs = []
            student_avg_map = {}
            for rc in report_cards:
                evals = rc.ecd_evaluations.all()
                if not evals:
                    continue
                total_val = 0
                count = 0
                for ev in evals:
                    val = rating_map.get(ev.rating, 0)
                    if val > 0:
                        total_val += val
                        count += 1
                if count == 0:
                    continue
                avg_rating = total_val / count  # 1.0 - 4.0
                avg_pct = avg_rating / 4.0 * 100.0  # Scale to 0-100%
                student_avgs.append({"student": rc.student_id, "avg": avg_pct})
                student_avg_map[rc.student_id] = avg_pct

            valid_avgs = [s["avg"] for s in student_avgs]
            result["overall_avg"] = round(sum(valid_avgs) / len(valid_avgs), 1) if valid_avgs else 0
            total_students = len(valid_avgs)
            passed_students = sum(1 for a in valid_avgs if a >= 60)
            result["pass_rate_pct"] = round((passed_students / total_students * 100), 1) if total_students > 0 else 0
            result["passed_count"] = passed_students
            result["total_students_count"] = total_students
            result["student_avg_map"] = student_avg_map

            result["grade_dist"] = {
                "ap": sum(1 for a in valid_avgs if a >= 90),
                "a":  sum(1 for a in valid_avgs if a >= 80 and a < 90),
                "b":  sum(1 for a in valid_avgs if a >= 70 and a < 80),
                "c":  sum(1 for a in valid_avgs if a >= 60 and a < 70),
                "d":  sum(1 for a in valid_avgs if a >= 50 and a < 60),
                "e":  sum(1 for a in valid_avgs if a < 50),
            }

            # Per-class at-risk counts
            class_at_risk_counts = {}
            if student_avg_map:
                class_student_qs = Student.objects.filter(
                    id__in=list(student_avg_map.keys())
                ).values('id', 'class_name')
                for entry in class_student_qs:
                    sid = entry['id']
                    cn = entry['class_name']
                    avg = student_avg_map.get(sid)
                    if avg is not None and avg < 60:
                        class_at_risk_counts[cn] = class_at_risk_counts.get(cn, 0) + 1
            result["class_at_risk_counts"] = class_at_risk_counts

            # Completion stats
            completion_stats = []
            for cls in GradeClass.objects.filter(department=Department.ECD):
                cls_total = Student.objects.filter(class_name=cls.name, is_archived=False).count()
                if cls_total == 0:
                    continue
                reports = ReportCard.objects.filter(term=term, student__class_name=cls.name, is_ecd_report=True)
                assessed = reports.exclude(status=ReportCardStatus.DRAFT).count()
                pending = reports.filter(status=ReportCardStatus.PENDING_SIGN_OFF).count()
                cls_at_risk = class_at_risk_counts.get(cls.name, 0)
                completion_stats.append({
                    "class_name": cls.name, "total": cls_total,
                    "assessed": assessed, "pending": pending,
                    "remaining": cls_total - assessed,
                    "progress_pct": round((assessed / cls_total * 100), 1) if cls_total > 0 else 0,
                    "can_sign_off": pending > 0,
                    "student_count": cls_total, "at_risk_count": cls_at_risk,
                })
            result["completion_stats"] = sorted(completion_stats, key=lambda x: x["class_name"])

            # At-risk students
            at_risk_ids = [s["student"] for s in student_avgs if s["avg"] < 60]
            at_risk_students = Student.objects.filter(id__in=at_risk_ids).only(
                "first_name", "last_name", "class_name", "admission_no"
            )
            at_risk_map = {s["student"]: s["avg"] for s in student_avgs if s["avg"] < 60}
            for s in at_risk_students:
                s.avg = round(at_risk_map[s.id], 1)
                s.grade = get_grade_from_score(s.avg)
            result["at_risk_students"] = at_risk_students[:10]
            result["at_risk_count"] = len(at_risk_ids)

            # Class performance -- single batch query
            student_ids = [s["student"] for s in student_avgs]
            students_map = {s_obj.id: s_obj for s_obj in Student.objects.filter(id__in=student_ids).only("id", "class_name")}
            class_perf_data = {}
            for s in student_avgs:
                student_obj = students_map.get(s["student"])
                if student_obj:
                    class_perf_data.setdefault(student_obj.class_name, []).append(s["avg"])
            class_stats = []
            for cn, avgs in class_perf_data.items():
                class_stats.append({"student__class_name": cn, "avg": round(sum(avgs) / len(avgs), 1)})
            class_stats.sort(key=lambda x: x["avg"], reverse=True)
            for c in class_stats:
                c["is_flagged"] = is_at_risk(c["avg"])
            result["class_stats"] = class_stats

            # Domain performance (instead of subject performance)
            all_evals = ECDEvaluation.objects.filter(report_card__in=report_cards)
            domain_data = {}
            for ev in all_evals:
                val = rating_map.get(ev.rating, 0)
                if val > 0:
                    domain_data.setdefault(ev.domain, []).append(val)
            subject_stats = []
            for domain, vals in domain_data.items():
                avg = (sum(vals) / len(vals)) / 4.0 * 100.0
                subject_stats.append({"subject_name": domain, "avg": round(avg, 1)})
            subject_stats.sort(key=lambda x: x["avg"], reverse=True)
            result["subject_stats"] = subject_stats

            # Pending sign-offs
            reports_pending = ReportCard.objects.filter(
                term=term, student__class_name__in=ecd_classes, is_ecd_report=True
            ).exclude(status=ReportCardStatus.PUBLISHED)
            result["pending_signoffs"] = reports_pending.values(
                "student__class_name"
            ).annotate(count=Count("id")).order_by("student__class_name")

            # Per-class student data
            if selected_class:
                class_students = Student.objects.filter(
                    class_name=selected_class, is_archived=False
                ).order_by("last_name", "first_name")
                class_student_data = []
                for s_obj in class_students:
                    avg_val = student_avg_map.get(s_obj.id)
                    data = {
                        "student": s_obj,
                        "avg": round(avg_val, 1) if avg_val else None,
                        "grade": get_grade_from_score(avg_val) if avg_val else "N/A",
                        "is_at_risk": is_at_risk(avg_val) if avg_val else False,
                        "is_critical": is_critical(avg_val) if avg_val else False,
                    }
                    class_student_data.append(data)
                result["class_student_data"] = sorted(
                    class_student_data,
                    key=lambda x: (0 if x["avg"] is not None else 1, x["avg"] if x["avg"] is not None else 0),
                )
                result["class_selected_name"] = selected_class
                result["class_student_total"] = len(class_students)
                result["class_with_scores"] = sum(1 for d in class_student_data if d["avg"] is not None)
                result["class_at_risk_count"] = sum(1 for d in class_student_data if d["is_at_risk"])

            return result

        # -- ExamScore Analytics Path (Primary / Secondary) --
        scores = ExamScore.objects.filter(term=term).filter(dept_filter)

        # Apply weights to scores
        w_scores = scores.annotate(
            weight_val=Case(
                *[When(exam_type=code, then=Value(w/100.0)) for code, w in weights.items()],
                default=Value(0.0),
                output_field=FloatField()
            )
        ).annotate(
            weighted_val=ExpressionWrapper(F('score') * F('weight_val'), output_field=FloatField())
        )

        # 1. Overall stats (Weighted)
        student_totals = w_scores.values("student").annotate(
            total_w=Sum('weighted_val'),
            sum_w=Sum('weight_val')
        )
        student_avgs = [
            {"student": s["student"], "avg": float(s["total_w"] / s["sum_w"])}
            for s in student_totals if s["sum_w"] > 0
        ]
        student_avg_map = {s["student"]: s["avg"] for s in student_avgs}
        valid_avgs = [s["avg"] for s in student_avgs]
        
        result["overall_avg"] = round(sum(valid_avgs) / len(valid_avgs), 1) if valid_avgs else 0
        
        total_students = len(valid_avgs)
        passed_students = sum(1 for a in valid_avgs if a >= 60)
        result["pass_rate_pct"] = round((passed_students / total_students * 100), 1) if total_students > 0 else 0
        result["passed_count"] = passed_students
        result["total_students_count"] = total_students
        result["student_avg_map"] = student_avg_map
        
        # Grade distribution
        result["grade_dist"] = {
            "ap": sum(1 for a in valid_avgs if a >= 90),
            "a":  sum(1 for a in valid_avgs if a >= 80 and a < 90),
            "b":  sum(1 for a in valid_avgs if a >= 70 and a < 80),
            "c":  sum(1 for a in valid_avgs if a >= 60 and a < 70),
            "d":  sum(1 for a in valid_avgs if a >= 50 and a < 60),
            "e":  sum(1 for a in valid_avgs if a < 50),
        }

        # Per-class at-risk counts
        class_at_risk_counts = {}
        if student_avg_map:
            class_student_qs = Student.objects.filter(
                id__in=list(student_avg_map.keys())
            ).values('id', 'class_name')
            for entry in class_student_qs:
                sid = entry['id']
                cn = entry['class_name']
                avg = student_avg_map.get(sid)
                if avg is not None and avg < 60:
                    class_at_risk_counts[cn] = class_at_risk_counts.get(cn, 0) + 1
        result["class_at_risk_counts"] = class_at_risk_counts

        # Completion stats
        completion_stats = []
        tracking_classes = GradeClass.objects.all()
        if role == UserRole.PRIMARY_HOD:
            tracking_classes = GradeClass.objects.filter(department=Department.PRIMARY)
        elif role == UserRole.ECD_HOD:
            tracking_classes = GradeClass.objects.filter(department=Department.ECD)
            
        for cls in tracking_classes:
            cls_total = Student.objects.filter(class_name=cls.name, is_archived=False).count()
            if cls_total == 0: continue
            
            if cls.department == Department.ECD:
                reports = ReportCard.objects.filter(term=term, student__class_name=cls.name, is_ecd_report=True)
                assessed = reports.exclude(status=ReportCardStatus.DRAFT).count()
                pending = reports.filter(status=ReportCardStatus.PENDING_SIGN_OFF).count()
            else:
                assessed = Student.objects.filter(
                    class_name=cls.name, is_archived=False, exam_scores__term=term
                ).distinct().count()
                pending = ReportCard.objects.filter(
                    term=term, student__class_name=cls.name, status=ReportCardStatus.PENDING_SIGN_OFF
                ).count()
            
            cls_at_risk = class_at_risk_counts.get(cls.name, 0)
            completion_stats.append({
                "class_name": cls.name,
                "total": cls_total,
                "assessed": assessed,
                "pending": pending,
                "remaining": cls_total - assessed,
                "progress_pct": round((assessed / cls_total * 100), 1) if cls_total > 0 else 0,
                "can_sign_off": pending > 0,
                "student_count": cls_total,
                "at_risk_count": cls_at_risk,
            })
        result["completion_stats"] = sorted(completion_stats, key=lambda x: x["class_name"])

        # At-risk students
        at_risk_ids = [s["student"] for s in student_avgs if s["avg"] < 60]
        at_risk_students = Student.objects.filter(id__in=at_risk_ids).only("first_name", "last_name", "class_name", "admission_no")
        at_risk_map = {s["student"]: s["avg"] for s in student_avgs if s["avg"] < 60}
        for s in at_risk_students:
            s.avg = round(at_risk_map[s.id], 1)
            s.grade = get_grade_from_score(s.avg)
        result["at_risk_students"] = at_risk_students[:10]
        result["at_risk_count"] = len(at_risk_ids)

        # Class performance
        class_perf = scores.values("student__class_name").annotate(avg=Avg("score")).order_by("-avg")
        for c in class_perf:
            c["avg"] = round(c["avg"], 1)
            c["is_flagged"] = is_at_risk(c["avg"])
        result["class_stats"] = class_perf

        # Subject performance
        subject_stats = scores.values("subject_name").annotate(
            avg=Avg("score"),
            total_count=Count("id")
        ).order_by("-avg")
        for s in subject_stats:
            s["avg"] = round(s["avg"], 1)
        result["subject_stats"] = subject_stats

        # Pending sign-offs
        reports_pending = ReportCard.objects.filter(term=term).filter(dept_filter).exclude(status=ReportCardStatus.PUBLISHED)
        result["pending_signoffs"] = reports_pending.values("student__class_name").annotate(count=Count("id")).order_by("student__class_name")

        # Per-class student data
        if selected_class:
            class_students = Student.objects.filter(class_name=selected_class, is_archived=False).order_by("last_name", "first_name")
            class_student_data = []
            for s in class_students:
                avg_val = student_avg_map.get(s.id)
                data = {
                    "student": s,
                    "avg": round(avg_val, 1) if avg_val else None,
                    "grade": get_grade_from_score(avg_val) if avg_val else "N/A",
                    "is_at_risk": is_at_risk(avg_val) if avg_val else False,
                    "is_critical": is_critical(avg_val) if avg_val else False,
                }
                class_student_data.append(data)
            
            result["class_student_data"] = sorted(
                class_student_data,
                key=lambda x: (0 if x["avg"] is not None else 1, x["avg"] if x["avg"] is not None else 0)
            )
            result["class_selected_name"] = selected_class
            result["class_student_total"] = len(class_students)
            result["class_with_scores"] = sum(1 for d in class_student_data if d["avg"] is not None)
            result["class_at_risk_count"] = sum(1 for d in class_student_data if d["is_at_risk"])

        return result

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from django.db.models import Avg, Count, Q, Sum, F, Case, When, Value, FloatField, ExpressionWrapper
        from academics.models import Department, GradeClass, ExamType, ReportCard, ReportCardStatus, get_exam_weights

        # Current (default) term — resolved from system date (FR-CAL-003)
        from academics.utils import get_current_term
        current_term = get_current_term() or Term.objects.order_by("-start_date").first()
        ctx["current_term"] = current_term

        # Available terms for the selector
        available_terms = Term.objects.all().order_by("-start_date")
        ctx["available_terms"] = available_terms

        # Selected term from query param (default to most recent term with data)
        selected_term_id = self.request.GET.get("term_id")
        if selected_term_id:
            selected_term = Term.objects.filter(id=selected_term_id).first()
        else:
            # Find the most recent term that has ExamScore data
            terms_with_scores = (
                ExamScore.objects.values("term")
                .annotate(score_count=Count("id"))
                .filter(score_count__gt=0)
                .order_by("-term")
                .values_list("term", flat=True)
            )
            data_term = Term.objects.filter(id__in=terms_with_scores).order_by("-start_date").first()
            selected_term = current_term or data_term
        ctx["selected_term"] = selected_term
        ctx["selected_term_id"] = selected_term.id if selected_term else None

        # Defaults for template variables (used when no term/data exists)
        ctx.setdefault("overall_avg", 0)
        ctx.setdefault("pass_rate_pct", 0)
        ctx.setdefault("passed_count", 0)
        ctx.setdefault("total_students_count", 0)
        ctx.setdefault("grade_dist", {"ap": 0, "a": 0, "b": 0, "c": 0, "d": 0, "e": 0})
        ctx.setdefault("subject_stats", [])
        ctx.setdefault("class_stats", [])
        ctx.setdefault("pending_signoffs", [])
        ctx.setdefault("at_risk_students", [])
        ctx.setdefault("at_risk_count", 0)
        ctx.setdefault("completion_stats", [])
        ctx.setdefault("class_student_data", [])
        ctx.setdefault("class_student_total", 0)
        ctx.setdefault("class_with_scores", 0)
        ctx.setdefault("class_at_risk_count", 0)
        ctx.setdefault("total_enrolled_count", 0)
        ctx.setdefault("student_avg_map", {})
        ctx.setdefault("class_at_risk_counts", {})
        ctx.setdefault("is_ecd_analytics", False)

        # Comparison mode
        compare_mode = self.request.GET.get("compare", "") == "1"
        ctx["compare_mode"] = compare_mode
        
        # 0. Department Scoping for HODs
        role = self.request.user.role
        dept_filter = Q()
        
        if role == UserRole.PRIMARY_HOD:
            primary_classes = GradeClass.objects.filter(department=Department.PRIMARY).values_list('name', flat=True)
            dept_filter = Q(student__class_name__in=primary_classes)
            ctx["dept_name"] = "Primary"
        elif role == UserRole.ECD_HOD:
            ecd_classes = GradeClass.objects.filter(department=Department.ECD).values_list('name', flat=True)
            dept_filter = Q(student__class_name__in=ecd_classes)
            ctx["dept_name"] = "ECD"
            ctx["is_ecd_analytics"] = True
        else:
            ctx["dept_name"] = "Whole School"

        # Determine available classes for tabs
        if role == UserRole.PRIMARY_HOD:
            available_classes = GradeClass.objects.filter(department=Department.PRIMARY).values_list('name', flat=True).order_by('name')
        elif role == UserRole.ECD_HOD:
            available_classes = GradeClass.objects.filter(department=Department.ECD).values_list('name', flat=True).order_by('name')
        else:
            available_classes = GradeClass.objects.all().values_list('name', flat=True).order_by('name')
        all_available = list(available_classes)
        
        # Department filter for class tabs
        selected_dept = self.request.GET.get("dept", "")
        ctx["selected_dept"] = selected_dept
        ctx["dept_choices"] = Department.choices
        
        if selected_dept:
            dept_class_names = GradeClass.objects.filter(department=selected_dept).values_list('name', flat=True)
            available_classes = [c for c in all_available if c in dept_class_names]
        else:
            available_classes = all_available
        
        ctx["available_classes"] = list(available_classes)
        selected_class = self.request.GET.get("class_name", "")
        ctx["selected_class"] = selected_class

        weights = get_exam_weights()

        # Compute analytics for the selected term
        if selected_term:
            term_data = self._compute_term_analytics(selected_term, role, dept_filter, selected_class, weights)
            ctx.update(term_data)
            
            # Total enrolled students across scoped classes
            if role == UserRole.PRIMARY_HOD:
                enrolled_qs = Student.objects.filter(is_archived=False, class_name__in=primary_classes)
            elif role == UserRole.ECD_HOD:
                enrolled_qs = Student.objects.filter(is_archived=False, class_name__in=ecd_classes)
            else:
                enrolled_qs = Student.objects.filter(is_archived=False)
            ctx["total_enrolled_count"] = enrolled_qs.count()

            # Comparison: compute analytics for the current term too
            if compare_mode and current_term and selected_term.id != current_term.id:
                compare_data = self._compute_term_analytics(current_term, role, dept_filter, selected_class, weights)
                ctx["compare_data"] = compare_data
                ctx["compare_term"] = current_term
        
        ctx["academics_tab"] = "analytics"
        
        return ctx


class AtRiskStudentsListView(RoleRequiredMixin, TemplateView):
    template_name = "academics/at_risk_list.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.view_examscore"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from django.db.models import Sum, F, Case, When, Value, FloatField, ExpressionWrapper, Q
        from academics.models import Department, GradeClass, get_exam_weights

        term = get_current_term()
        ctx["current_term"] = term
        
        role = self.request.user.role
        dept_filter = Q()
        if role == UserRole.PRIMARY_HOD:
            primary_classes = GradeClass.objects.filter(department=Department.PRIMARY).values_list('name', flat=True)
            dept_filter = Q(student__class_name__in=primary_classes)
        elif role == UserRole.ECD_HOD:
            ecd_classes = GradeClass.objects.filter(department=Department.ECD).values_list('name', flat=True)
            dept_filter = Q(student__class_name__in=ecd_classes)

        if term:
            # -- ECD At-Risk Path (uses ECDEvaluation ratings) --
            if role == UserRole.ECD_HOD:
                rating_map = {"E": 4, "G": 3, "S": 2, "N": 1}
                ecd_classes = GradeClass.objects.filter(department=Department.ECD).values_list('name', flat=True)

                rc_filter = Q(student__class_name__in=ecd_classes, is_ecd_report=True, term=term)
                report_cards = ReportCard.objects.filter(rc_filter).exclude(
                    status=ReportCardStatus.DRAFT
                ).select_related("student").prefetch_related("ecd_evaluations")

                at_risk_students = []
                for rc in report_cards:
                    evals = rc.ecd_evaluations.all()
                    if not evals:
                        continue
                    total_val = 0
                    count = 0
                    for ev in evals:
                        val = rating_map.get(ev.rating, 0)
                        if val > 0:
                            total_val += val
                            count += 1
                    if count == 0:
                        continue
                    avg_rating = total_val / count
                    avg_pct = avg_rating / 4.0 * 100.0
                    if avg_pct < 60:  # At-risk threshold
                        student = rc.student  # Already fetched via select_related
                        student.avg = round(avg_pct, 1)
                        student.grade = get_grade_from_score(student.avg)
                        at_risk_students.append(student)

                ctx["at_risk_students"] = sorted(at_risk_students, key=lambda x: x.avg)
                return ctx

            # -- ExamScore At-Risk Path (Primary / Secondary) --
            weights = get_exam_weights()
            scores = ExamScore.objects.filter(term=term).filter(dept_filter)
            
            # Weighted calculation
            w_scores = scores.annotate(
                weight_val=Case(
                    *[When(exam_type=code, then=Value(w/100.0)) for code, w in weights.items()],
                    default=Value(0.0),
                    output_field=FloatField()
                )
            ).annotate(
                weighted_val=ExpressionWrapper(F('score') * F('weight_val'), output_field=FloatField())
            )

            student_totals = w_scores.values("student").annotate(
                total_w=Sum('weighted_val'),
                sum_w=Sum('weight_val')
            )
            
            at_risk_map = {s["student"]: float(s["total_w"] / s["sum_w"]) for s in student_totals if s["sum_w"] > 0 and (s["total_w"] / s["sum_w"]) < 60}
            
            at_risk_students = Student.objects.filter(id__in=at_risk_map.keys()).only("first_name", "last_name", "class_name", "admission_no")
            
            for s in at_risk_students:
                s.avg = round(at_risk_map[s.id], 1)
                s.grade = get_grade_from_score(s.avg)
                
            ctx["at_risk_students"] = sorted(at_risk_students, key=lambda x: x.avg)
            
        return ctx


class ParentReportListView(RoleRequiredMixin, TemplateView):
    template_name = "academics/parent_report_list.html"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.PARENT]
    required_permission = "academics.view_reportcard"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.request.user.role != UserRole.PARENT:
            raise PermissionDenied()
            
        from academics.models import AcademicYear
        from students.models import ParentGuardian, Student
        guardian = ParentGuardian.objects.filter(user=self.request.user).first()
        if guardian:
            students = Student.objects.filter(studentguardian__guardian=guardian).distinct()
            reports_qs = ReportCard.objects.filter(student__in=students, status=ReportCardStatus.PUBLISHED).select_related("student", "term", "term__academic_year")

            # Year/term filters
            academic_years = AcademicYear.objects.order_by("-name")
            terms = Term.objects.order_by("-start_date")

            year_id = self.request.GET.get("year")
            term_id = self.request.GET.get("term")
            student_id = self.request.GET.get("student")

            if year_id:
                reports_qs = reports_qs.filter(term__academic_year_id=year_id)
            if term_id:
                reports_qs = reports_qs.filter(term_id=term_id)
            if student_id:
                reports_qs = reports_qs.filter(student_id=student_id)

            reports = reports_qs.order_by("-term__start_date")
            ctx["reports"] = reports
            ctx["academic_years"] = academic_years
            ctx["terms"] = terms
            ctx["selected_year"] = year_id
            ctx["selected_term"] = term_id
            ctx["selected_student"] = student_id
            ctx["students"] = students
        else:
            ctx["reports"] = []
            ctx["academic_years"] = Term.objects.none()
            ctx["terms"] = Term.objects.none()
            ctx["students"] = Student.objects.none()
            
        return ctx


class AcademicReportExportView(RoleRequiredMixin, View):
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.view_examscore"

    def get(self, request, *args, **kwargs):
        import csv
        from django.db.models import Avg
        from django.http import HttpResponse

        term = get_current_term()
        if not term:
            return HttpResponse("No active term found.", status=404)

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="Hodari_Full_Report_{term.name.replace(" ", "_")}.csv"'
        )

        writer = csv.writer(response)
        writer.writerow(["Admission No", "First Name", "Last Name", "Class", "Weighted Average (%)", "Status"])

        students = _export_students_for_user(request.user)

        from django.db.models import Sum, F, Case, When, Value, FloatField
        from academics.models import get_exam_weights
        
        exam_weights = get_exam_weights()
        students = _export_students_for_user(request.user)

        for s in students:
            scores = ExamScore.objects.filter(student=s, term=term)
            if not scores.exists():
                continue

            # Calculate weighted average
            subj_totals = scores.values("subject_name").annotate(
                total_w=Sum(
                    Case(
                        *[When(exam_type=code, then=F('score') * (w/100.0)) for code, w in exam_weights.items()],
                        default=Value(0.0),
                        output_field=FloatField()
                    )
                ),
                sum_w=Sum(
                    Case(
                        *[When(exam_type=code, then=Value(w/100.0)) for code, w in exam_weights.items()],
                        default=Value(0.0),
                        output_field=FloatField()
                    )
                )
            )

            # Average of weighted subject scores
            valid_subjs = [float(st['total_w'] / st['sum_w']) for st in subj_totals if st['sum_w'] > 0]
            if valid_subjs:
                avg_val = round(sum(valid_subjs) / len(valid_subjs), 1)
                grade = get_grade_from_score(avg_val) # Using grading utility
                writer.writerow(
                    [s.admission_no, s.first_name, s.last_name, s.class_name, avg_val, grade]
                )

        return response


class ReportPDFDownloadView(RoleRequiredMixin, View):
    """Server-side PDF download for report cards.
    Filename pattern: StudentName_Class_Term_AcademicYear_Report.pdf
    Uses WeasyPrint for PDF generation, falling back to HTML if unavailable.
    """
    login_url = "/accounts/login/"
    required_permission = "academics.view_reportcard"

    def get(self, request, pk):
        # GRD-014: anyone holding view_reportcard may download; linked parent
        # guardians are additionally gated to their own children's published reports.
        if not request.user.has_perm("academics.view_reportcard"):
            raise PermissionDenied()

        report = get_object_or_404(ReportCard, pk=pk)
        student = report.student

        # GRD-014: Parent can only download after HOS sign-off
        from students.models import ParentGuardian, Student
        guardian = ParentGuardian.objects.filter(user=request.user).first()
        if guardian is not None:
            if report.status != ReportCardStatus.PUBLISHED:
                raise PermissionDenied("Report not yet signed off by HOS.")
            if not Student.objects.filter(studentguardian__guardian=guardian, pk=report.student_id).exists():
                raise PermissionDenied()

        # Build filename: StudentName_Class_Term_AcademicYear_Report.pdf
        safe_name = f"{student.first_name}_{student.last_name}".replace(" ", "_")
        class_name = student.class_name.replace(" ", "_")
        term_name = report.term.name.replace(" ", "_") if report.term else "NoTerm"
        ac_year = report.term.academic_year.name.replace(" ", "_") if report.term and report.term.academic_year else ""
        filename = f"{safe_name}_{class_name}_{term_name}_{ac_year}_Report.pdf"

        # Pick the right template
        if report.is_ecd_report:
            template_name = "academics/ecd_report_preview.html"
        else:
            template_name = "academics/report_card_print.html"

        ctx = _build_report_card_context(report)

        rendered = render(request, template_name, ctx)
        html = rendered.content.decode("utf-8")

        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="REPORT_PDF_DOWNLOADED",
            model_name="ReportCard",
            object_id=report.pk,
            description=f"Report PDF downloaded for {student.admission_no} ({report.term.name if report.term else 'N/A'})",
            request=request
        )

        try:
            from weasyprint import HTML
            import time
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
            start = time.monotonic()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    lambda: HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
                )
                pdf_bytes = future.result(timeout=10)
            elapsed = round(time.monotonic() - start, 2)
            if elapsed > 5:
                import logging
                logging.getLogger(__name__).warning("Report PDF slow: %.2fs for %s", filename, elapsed)
            response = HttpResponse(content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            response.write(pdf_bytes)
            response["X-PDF-Gen-Time"] = str(elapsed)
            return response
        except FuturesTimeout:
            import logging
            logging.getLogger(__name__).error("Report PDF timed out: %s", filename)
            return rendered
        except (ImportError, OSError):
            # Fallback: WeasyPrint not available (missing package or native deps like GTK)
            # Renders the report card page normally in the browser
            return rendered


