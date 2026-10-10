import re
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone
from django.utils.http import urlencode
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


def _export_students_for_user(user):
    """Scope academic CSV exports: school-wide roles get everyone, section heads
    get every section they head (e.g. Primary + Lower Secondary), others nothing."""
    from core.scoping import hod_class_names
    qs = Student.objects.filter(is_archived=False).order_by("class_name", "last_name")
    if user.is_school_wide:
        return qs
    names = hod_class_names(user)
    return qs.filter(class_name__in=names) if names else Student.objects.none()
from users.models import UserRole
from core.permissions import RoleRequiredMixin
from academics.approval_policy import AllowedRolesEnforcedMixin
from core.teacher_context import get_teacher_assigned_classes, get_teacher_assigned_classes_from_tca, is_ecd_teacher
from academics.grading_utils import (
    compute_grade_with_gaps, get_grade_from_score, get_grade_label, get_full_grade_display,
    is_pass, is_at_risk, is_critical
)


def _action_error(exc):
    """Plain wording for a failed report action. Rule violations (validation,
    permission) are shown as written; anything unexpected is logged and the
    user is told nothing was changed instead of seeing internal error text."""
    import logging
    if isinstance(exc, ValidationError):
        return " ".join(exc.messages)
    if isinstance(exc, PermissionDenied):
        return str(exc) or "You do not have permission to do that."
    logging.getLogger(__name__).exception("Report action failed")
    return "The system could not complete this action, so nothing was changed. Please try again."


def _back_to(request, fallback):
    """Return to the page the action came from (keeping its filters) when it is on this site."""
    from django.utils.http import url_has_allowed_host_and_scheme
    referer = request.META.get("HTTP_REFERER")
    if referer and url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return redirect(referer)
    return redirect(fallback)


class ReportRouterView(RoleRequiredMixin, View):
    """Router to send users to their appropriate reports view based on role."""
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.TEACHER, UserRole.PARENT]
    required_permission = "academics.view_reportcard"

    def get(self, request, *args, **kwargs):
        role = request.user.role
        if role == UserRole.PARENT:
            return redirect("academics:parent_reports")
        elif request.user.has_role(UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD,
                                   UserRole.LOWER_SECONDARY_HOD):
            return redirect("academics:performance_report")
        elif request.user.has_role(UserRole.ECD_HOD):
            # ECD is rated, not marked, so it has no performance figures.
            return redirect("academics:report_review_queue")
        elif role == UserRole.TEACHER:
            return redirect("academics:exam_scores_entry")
        else:
            return redirect("/")


class HOSSignOffListView(AllowedRolesEnforcedMixin, RoleRequiredMixin, TemplateView):
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
            from core.scoping import hod_class_names
            section_classes = hod_class_names(self.request.user)
            if section_classes is not None:
                classes_qs = classes_qs.filter(class_name__in=section_classes)
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
            ctx["pending_signoffs"] = pending_signoffs_by_class(term, classes)
            ctx["pending_signoff_total"] = sum(p["count"] for p in ctx["pending_signoffs"])
            
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
            if self.request.user.is_school_wide:
                available = GradeClass.objects.all().values_list("name", flat=True)
            elif self.request.user.section_departments:
                available = GradeClass.objects.filter(
                    department__in=self.request.user.section_departments
                ).values_list("name", flat=True)
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


def _remark_rows(student, term):
    """[(subject, remark)] for the remark-only subjects of the learner's class,
    plus any other remark recorded for them; remark is "" when not entered."""
    from academics.remarks import remark_subject_names, remarks_for
    from academics.score_progress import expected_subjects_for_class
    remarks = remarks_for(student, term)
    names = remark_subject_names(expected_subjects_for_class(student.class_name) | set(remarks))
    return [(name, remarks.get(name, "")) for name in sorted(names)]


def _analytics_scope(user):
    """(role_for_analytics, departments) for the analytics and at-risk views.

    A user holding several section roles sees the union of what each role
    sees on its own: Head of Primary -> Primary, ECD Head -> ECD; Head of
    Lower Secondary, Head of School and Super Admin have always seen the
    whole school (departments None). role_for_analytics keeps the existing
    single-section paths (ECD uses ratings, not exam scores).
    """
    if user.is_school_wide or user.has_role(UserRole.LOWER_SECONDARY_HOD):
        return UserRole.HEAD_OF_SCHOOL, None
    depts = []
    if user.has_role(UserRole.PRIMARY_HOD):
        depts.append(Department.PRIMARY)
    if user.has_role(UserRole.ECD_HOD):
        depts.append(Department.ECD)
    if depts == [Department.PRIMARY]:
        return UserRole.PRIMARY_HOD, depts
    if depts == [Department.ECD]:
        return UserRole.ECD_HOD, depts
    return (UserRole.HEAD_OF_SCHOOL, depts) if depts else (user.role, None)


def _reopens_on(term):
    """Start date of the term after ``term`` (None when not yet set up)."""
    from academics.models import Term
    if not term or not (term.end_date or term.start_date):
        return None
    after = term.end_date or term.start_date
    nxt = Term.objects.filter(start_date__gt=after).order_by("start_date").first()
    return nxt.start_date if nxt else None


def _class_teacher_name(report):
    """The class teacher assigned to the learner's class for the report's term,
    else the latest class teacher of that class, else whoever generated it."""
    from hr.models import TeacherClassAssignment
    class_name = report.student.class_name
    a = (TeacherClassAssignment.objects.filter(
            is_class_teacher=True, grade_class__name=class_name, term=report.term)
         .select_related("teacher").first())
    if a:
        return a.teacher.full_name or str(a.teacher)
    from attendance.analytics import class_teachers
    name = class_teachers().get(class_name)
    if name:
        return name
    by = report.generated_by
    return (by.get_full_name() or by.username) if by else ""


def _photo_data_uri(student):
    """The learner's photo inlined as a data: URI, so the PDF engine never
    has to fetch it over HTTP (media may sit behind login). None if absent."""
    import base64
    import mimetypes
    f = student.photo_file()
    if not f:
        return None
    mime = mimetypes.guess_type(f.name)[0] or ""
    if not mime.startswith("image/"):
        return None
    try:
        f.open("rb")
        try:
            data = f.read()
        finally:
            f.close()
    except (OSError, ValueError):
        return None
    if not data or len(data) > 5 * 1024 * 1024:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


TRAIT_GROUPS = [
    ("Work Habits", [
        ("Works well independently", "wh_works_independently"),
        ("Completes work neatly", "wh_completes_neatly"),
        ("Completes work required", "wh_completes_required"),
        ("Does not disturb others", "wh_not_disturb"),
        ("Follows directions", "wh_follows_directions"),
    ]),
    ("Personal Traits", [
        ("Displays creativity", "pt_creativity"),
        ("Is honest", "pt_honest"),
        ("Successfully completes homework and assignments", "pt_homework"),
        ("Attention span", "pt_attention"),
        ("Displays flexibility", "pt_flexibility"),
    ]),
    ("Social Traits", [
        ("Show respect for authority", "st_respects"),
        ("Exhibits self-control", "st_self_control"),
        ("Responds well to correction", "st_correction"),
        ("Relates well with others", "st_relates_well"),
        ("Is courteous", "st_courteous"),
    ]),
]


def _trait_groups(traits):
    """[(title, rows)] with two traits per row: [(label, grade), (label, grade) | None]."""
    groups = []
    for title, items in TRAIT_GROUPS:
        cells = [(label, traits.get(key) or "-") for label, key in items]
        rows = [(cells[i], cells[i + 1] if i + 1 < len(cells) else None) for i in range(0, len(cells), 2)]
        groups.append((title, rows))
    return groups


def _build_report_card_context(report):
    """
    Build the shared context dict for report card rendering (both preview and PDF).

    Returns a dict with keys: report, student, attendance, subjects,
    checkpoint_scores (if applicable), and ecd (if ECD report).
    """
    ctx = {"report": report, "student": report.student}

    # Attendance over the term's school days so far: weekends, holidays and
    # days before enrolment are left out, and unmarked days are never counted
    # as absent. Present includes late; absent includes excused.
    from attendance import analytics
    term = report.term
    start, end = analytics.term_range(term)
    row = analytics.learner_attendance(report.student, start, end)["row"] or {}
    ctx["attendance"] = {
        "present": row.get("in_school", 0),
        "late": row.get("late", 0),
        "absent": row.get("absent", 0) + row.get("excused", 0),
        "excused": row.get("excused", 0),
        "marked": row.get("marked", 0),
        "rate": row.get("rate"),
    }
    ctx["photo_src"] = _photo_data_uri(report.student)
    ctx["photo_checked"] = True
    ctx["reopens_on"] = _reopens_on(term)
    ctx["class_teacher"] = _class_teacher_name(report)

    if report.is_ecd_report:
        ctx["ecd"] = build_ecd_report_context(report)
        return ctx

    ctx["trait_groups"] = _trait_groups(report.general_traits or {})

    # Subject scores with weighted averages and gap detection
    scores = ExamScore.objects.filter(
        student=report.student, term=report.term, status=ScoreStatus.APPROVED
    ).mark_bearing()
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
    ctx["remark_rows"] = _remark_rows(report.student, report.term)

    # ACADEMIC PROGRESS table: approved, mark-bearing subjects only. The exam
    # average and grade appear only once the term is fully assessed — never
    # the stored average, which may predate later scores.
    from academics.score_progress import exam_summary
    ctx["exam"] = exam_summary(report.student, report.term)

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


class HOSSignOffActionView(AllowedRolesEnforcedMixin, RoleRequiredMixin, View):
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.change_reportcard"
    def post(self, request, *args, **kwargs):
        if not request.user.has_role(UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD):
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
                    reject_report_for_edit(
                        report, request.user, reason=reason, subjects=request.POST.getlist("subjects"),
                    )
                    messages.success(request, f"Report for {report.student.first_name} returned to teacher for correction.")
                else:
                    sign_off_report(report, request.user)
                    messages.success(request, f"Report for {report.student.first_name} signed off and published.")
            except ReportCard.DoesNotExist:
                messages.error(request, "That report no longer exists — it may have been deleted. Refresh the page.")
            except Exception as e:
                messages.error(request, f"Nothing was changed: {_action_error(e)}")
        elif class_name and term_id:
            reports = ReportCard.objects.filter(student__class_name=class_name, term_id=term_id).exclude(status=ReportCardStatus.PUBLISHED)
            processed_count = 0
            blocked = []
            for r in reports.select_related("student"):
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
                    blocked.append(f"{r.student.first_name} {r.student.last_name} ({_action_error(exc)})")

            verb = "returned" if action == "reject" else "signed off"
            if not processed_count and not blocked:
                messages.info(request, f"There were no unpublished reports in {class_name} to process.")
            elif not blocked:
                messages.success(request, f"{processed_count} report(s) in {class_name} {verb}.")
            else:
                level = messages.warning if processed_count else messages.error
                level(
                    request,
                    f"{processed_count} report(s) {verb}; {len(blocked)} not {verb}: " + "; ".join(blocked),
                )
        else:
            messages.error(request, "Nothing was changed: no report or class was selected.")

        return _back_to(request, "academics:hos_signoff_list")


class ReportReviewQueueView(AllowedRolesEnforcedMixin, RoleRequiredMixin, TemplateView):
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
        if user.section_departments and not user.is_school_wide:
            allowed_depts = list(user.section_departments)
        else:
            allowed_depts = [Department.PRIMARY, Department.ECD, Department.LOWER_SECONDARY]

        # The filter narrows within what the user may see; it never widens it.
        if dept_filter == "primary":
            allowed_depts = [d for d in allowed_depts if d == Department.PRIMARY]
        elif dept_filter == "ecd":
            allowed_depts = [d for d in allowed_depts if d == Department.ECD]
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

        # Per-subject progress for non-ECD students: a report is approved only
        # when every class subject is approved, not just the ones with scores.
        from academics.score_progress import build_progress, expected_subjects_for_class
        progress_by_student = {}
        if primary_student_ids:
            rows_by_student = defaultdict(list)
            for sc in ExamScore.objects.filter(student_id__in=primary_student_ids, term=term):
                rows_by_student[sc.student_id].append(sc)
            from academics.models import SubjectTermRemark
            from academics.remarks import remark_subject_names
            remark_subjects = remark_subject_names()
            remarks_by_student = defaultdict(dict)
            for sid, subject, remark in SubjectTermRemark.objects.filter(
                    student_id__in=primary_student_ids, term=term).values_list("student_id", "subject_name", "remark"):
                remarks_by_student[sid][subject] = remark
            expected_by_class = {}
            weights = get_exam_weights()
            for rc in reports:
                if rc.is_ecd_report:
                    continue
                cls = rc.student.class_name
                if cls not in expected_by_class:
                    expected_by_class[cls] = expected_subjects_for_class(cls)
                progress_by_student[rc.student_id] = build_progress(
                    rows_by_student.get(rc.student_id, []), expected_by_class[cls], weights,
                    remark_subjects, remarks_by_student.get(rc.student_id),
                )

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

            progress = None
            if rc.is_ecd_report:
                all_approved = rc.student_id in ecd_complete
                pending_count = 0
            else:
                progress = progress_by_student.get(rc.student_id)
                all_approved = bool(progress and progress["is_complete"])
                pending_count = sum(
                    1 for v in (progress["subjects"].values() if progress else [])
                    if v["status"] in {ScoreStatus.SUBMITTED, ScoreStatus.RETURNED}
                )

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
                "approved_subjects": progress["approved_subjects"] if progress else 0,
                "total_subjects": progress["total_subjects"] if progress else 0,
                "subject_names": sorted(progress["subjects"]) if progress else [],
            })

        ctx["reports"] = rows
        ctx["status_filter"] = status_filter

        # Always compute pending counts regardless of current filter
        pending_reports = ReportCard.objects.filter(
            term=term,
            student__class_name__in=dept_class_names,
            comments_submitted=True,
        ).exclude(status=ReportCardStatus.PUBLISHED).select_related("student", "generated_by")

        # Progress for pending reports not already in the (filtered) list above.
        missing_ids = [
            rc.student_id for rc in pending_reports
            if not rc.is_ecd_report and rc.student_id not in progress_by_student
        ]
        if missing_ids:
            from academics.score_progress import class_progress
            by_class = defaultdict(list)
            for rc in pending_reports:
                if rc.student_id in missing_ids:
                    by_class[rc.student.class_name].append(rc.student_id)
            for cls, ids in by_class.items():
                progress_by_student.update(class_progress(ids, term.id, cls))

        pending_rows = []
        for rc in pending_reports:
            if rc.is_ecd_report:
                all_a = rc.student_id in ecd_complete
            else:
                p = progress_by_student.get(rc.student_id)
                all_a = bool(p and p["is_complete"])
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
        ctx["pending_signoffs"] = pending_signoffs_by_class(term, dept_class_names)
        ctx["pending_signoff_total"] = sum(p["count"] for p in ctx["pending_signoffs"])
        ctx["academics_tab"] = "review_queue"
        return ctx

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action", "approve")
        report_id = request.POST.get("report_id")
        reason = request.POST.get("reason", "")

        from academics.services import sign_off_report, reject_report_for_edit, revoke_report_signoff

        if not report_id:
            messages.error(request, "No report specified.")
            return _back_to(request, "academics:report_review_queue")

        try:
            report = ReportCard.objects.get(pk=report_id)
        except ReportCard.DoesNotExist:
            messages.error(request, "Report not found.")
            return _back_to(request, "academics:report_review_queue")

        try:
            if action == "ecd_hod_approve":
                # ECD HOD approves ECD report before HOS sign-off
                if not request.user.has_role(UserRole.ECD_HOD, UserRole.SUPER_ADMIN):
                    raise PermissionDenied("Only ECD HOD or Super Admin can approve ECD reports.")
                if not report.is_ecd_report:
                    messages.error(request, "This action is only for ECD reports.")
                    return _back_to(request, "academics:report_review_queue")
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
                if not request.user.has_role(UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD):
                    raise PermissionDenied("Only HOS, Super Admin, or HOD can reject reports.")
                reject_report_for_edit(
                    report, request.user, reason=reason, subjects=request.POST.getlist("subjects"),
                )
                messages.success(request, f"Report for {report.student.first_name} returned for correction.")
            elif action == "revoke":
                # GRD-016: Only HOS or Super Admin can revoke.
                if request.user.role not in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN}:
                    raise PermissionDenied("Only HOS or Super Admin can revoke a signed-off report.")
                if not reason:
                    messages.error(request, "A reason is required to revoke a report sign-off.")
                    return _back_to(request, "academics:report_review_queue")
                revoke_report_signoff(report, request.user, reason=reason)
                messages.success(request, f"Report sign-off revoked for {report.student.first_name}.")
            else:
                # GRD-009: Only HOS or Super Admin can sign off.
                if request.user.role not in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN}:
                    raise PermissionDenied("Only the Head of School or Super Admin can sign off reports.")
                sign_off_report(report, request.user)
                messages.success(request, f"Report for {report.student.first_name} signed off and published.")
        except Exception as e:
            messages.error(request, f"Nothing was changed: {_action_error(e)}")

        return _back_to(request, "academics:report_review_queue")


class AcademicAnalyticsView(RoleRequiredMixin, View):
    """Retired: the Analytics tab duplicated the Performance Report with
    different counting rules, so the two disagreed. Old links and bookmarks
    land on the Performance Report for the same term."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.view_examscore"

    def get(self, request, *args, **kwargs):
        url = reverse("academics:performance_report")
        term = Term.objects.filter(pk=request.GET.get("term_id")).first() if request.GET.get("term_id", "").isdigit() else None
        if term:
            url += "?" + urlencode({"year": term.academic_year_id, "term": term.pk})
        return redirect(url)


def pending_signoffs_by_class(term, class_names=None):
    """Reports not yet published this term, counted per class (reports, not
    learners). ``class_names`` limits it to a section; None means every class."""
    from django.db.models import Count
    qs = ReportCard.objects.filter(term=term).exclude(status=ReportCardStatus.PUBLISHED)
    if class_names is not None:
        qs = qs.filter(student__class_name__in=list(class_names))
    return [
        {"class_name": r["student__class_name"], "count": r["count"]}
        for r in qs.values("student__class_name").annotate(count=Count("id")).order_by("student__class_name")
    ]


class AtRiskStudentsListView(RoleRequiredMixin, TemplateView):
    template_name = "academics/at_risk_list.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.view_examscore"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from django.db.models import Q
        from academics.models import Department, GradeClass

        term = get_current_term()
        ctx["current_term"] = term
        
        # Union across every section role held (see _analytics_scope).
        role, scope_depts = _analytics_scope(self.request.user)

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
            # Learners whose average is below the pass mark, from the same
            # approved, weighted marks as the Performance Report's By student
            # list (single weak subjects are listed per learner).
            from academics import performance
            classes = None
            if scope_depts:
                classes = list(GradeClass.objects.filter(department__in=scope_depts).values_list("name", flat=True))
            ctx["at_risk_students"] = performance.at_risk_learners(performance.classes_scope(classes), term)

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


