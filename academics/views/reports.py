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
    is_pass, is_at_risk, is_critical, get_pass_mark
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


class HOSSignOffListView(AllowedRolesEnforcedMixin, RoleRequiredMixin, View):
    """Retired: the HOS Sign-Off page. Heads of School approve and publish
    from the Review Queue; old links land there, keeping the class."""
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.view_reportcard"

    def get(self, request, *args, **kwargs):
        url = reverse("academics:report_review_queue")
        if request.GET.get("class_name"):
            url += "?" + urlencode({"class_name": request.GET["class_name"]})
        return redirect(url)


from django.core.management import call_command


class ReportCardListView(RoleRequiredMixin, TemplateView):
    template_name = "academics/report_card_list.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.ADMIN_OFFICER, UserRole.TEACHER]
    required_permission = "academics.view_reportcard"

    def get_context_data(self, **kwargs):
        from academics.progress_filters import ProgressFilters
        ctx = super().get_context_data(**kwargs)
        pf = ProgressFilters(self.request)
        term = pf.term
        ctx["current_term"] = get_current_term()
        ctx["term"] = term
        ctx["term_id"] = term.id if term else ""

        # RBAC: teachers restricted to their class-teacher-only classes
        requested = self.request.GET.get("class_name")
        if self.request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            all_teacher_classes = get_teacher_assigned_classes_from_tca(self.request.user)
            available = [cls for cls, info in all_teacher_classes.items() if info.get("is_class_teacher")]
            if requested and requested not in available:
                ctx["class_error"] = "You are not the class teacher for this class."
        else:
            from academics.models import GradeClass, Department
            if self.request.user.is_school_wide:
                classes = GradeClass.objects.all()
            elif self.request.user.section_departments:
                classes = GradeClass.objects.filter(department__in=self.request.user.section_departments)
            else:
                classes = GradeClass.objects.filter(department=Department.PRIMARY)
            available = list(classes.order_by("sort_order", "name").values_list("name", flat=True))
        ctx["available_classes"] = available
        class_name = None if ctx.get("class_error") else (pf.pick_class(available) or None)
        ctx["class_name"] = class_name
        pf.pick_subject([], note="All subjects")
        exam_type = pf.pick_type(all_label="Combined term report")
        ctx["exam_type"] = exam_type
        ctx["exam_type_name"] = next((e.name for e in pf.exam_types if e.code == exam_type), "")
        ctx["pf"] = pf.context()

        if class_name and term:
            reports = ReportCard.objects.filter(
                student__class_name=class_name, 
                term=term
            ).select_related("student").order_by("student__last_name", "student__first_name")
            ctx["reports"] = reports
            ctx["has_pending"] = reports.filter(status=ReportCardStatus.PENDING_SIGN_OFF).exists()
            # Results sheet: every learner's subject marks, total, average and
            # position (same figures as the report card), with their report.
            from academics.class_position import class_results
            results = class_results(class_name, term, exam_type=exam_type or None)
            by_student = {r.student_id: r for r in reports}
            for row in results["rows"]:
                row["report"] = by_student.get(row["student"].pk)
            ctx["results"] = results
            ctx["print_all_report"] = next(iter(reports), None)

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

    def get(self, request, *args, **kwargs):
        if request.GET.get("export") != "csv":
            return super().get(request, *args, **kwargs)
        ctx = self.get_context_data(**kwargs)
        results = ctx.get("results")
        if ctx.get("class_error"):
            raise PermissionDenied(ctx["class_error"])
        if not results:
            return super().get(request, *args, **kwargs)
        import csv
        term = ctx["term"]
        label = ctx["exam_type_name"] or "combined"
        filename = f"{ctx['class_name']}_{term.name}_{label}_results.csv".replace(" ", "_")
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        writer = csv.writer(response)
        writer.writerow(["Admission No", "Student", "Sex"] + [s["name"] for s in results["subjects"]]
                        + ["Total", "Average", "Position", "Grade"])
        for row in results["rows"]:
            st = row["student"]
            writer.writerow(
                [st.admission_no, st.get_full_name(), (st.gender or "")[:1].upper()]
                + ["" if m is None else m for m in row["marks"]]
                + ["" if row[k] is None else row[k] for k in ("total", "average", "position", "grade")]
            )
        return response


def _remark_rows(student, term, expected=None, remark_names=None):
    """[(subject, remark)] for the remark-only subjects of the learner's class,
    plus any other remark recorded for them; remark is "" when not entered.
    ``expected`` (the class's subjects) and ``remark_names`` (every
    remark-only subject) save their lookups when the caller has them."""
    from academics.remarks import remark_subject_names, remarks_for
    from academics.score_progress import expected_subjects_for_class
    remarks = remarks_for(student, term)
    if expected is None:
        expected = expected_subjects_for_class(student.class_name)
    wanted = set(expected) | set(remarks)
    names = (remark_names & wanted) if remark_names is not None else remark_subject_names(wanted)
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


def _class_teacher_name(report, fallback_to_generator=True):
    """The class teacher assigned to the learner's class for the report's term,
    else the latest class teacher of that class, else whoever generated it
    (unless ``fallback_to_generator`` is False: then "")."""
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
    by = report.generated_by if fallback_to_generator else None
    return (by.get_full_name() or by.username) if by else ""


def report_signatures(report, shared=None):
    """Approved signature images (data: URIs) for a report card, each only
    once that person has done their step:

    * class teacher: the term's assigned class teacher, once submitted;
    * HOD: whoever approved it (``ecd_hod_approved_by``);
    * Head of School: whoever signed it off, once published.

    ``hod_name`` is set once the HOD has approved, so the report can show
    who approved even when that person has no approved signature yet."""
    from hr.models import TeacherClassAssignment, approved_signature_uri
    if shared is None:
        shared = {}

    def sig(user):
        if user is None:
            return None
        return _shared(shared, ("signature", user.pk), lambda: approved_signature_uri(user))

    out = {"class_teacher": None, "hod": None, "hod_name": "", "hos": None}
    if report.status in (ReportCardStatus.PENDING_SIGN_OFF, ReportCardStatus.PUBLISHED):
        def teacher_user():
            a = (TeacherClassAssignment.objects.filter(
                    is_class_teacher=True, grade_class__name=report.student.class_name, term=report.term)
                 .select_related("teacher__user").first())
            return a.teacher.user if a else None
        out["class_teacher"] = sig(_shared(
            shared, ("class_teacher_user", report.student.class_name, report.term_id), teacher_user))
    hod = report.ecd_hod_approved_by
    if hod is not None:
        out["hod"] = sig(hod)
        out["hod_name"] = hod.get_full_name() or hod.username
    if report.status == ReportCardStatus.PUBLISHED:
        out["hos"] = sig(report.signed_off_by)
    return out


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


def _shared(shared, key, compute):
    """``compute()`` once per key when a class is printed together (``shared``
    is a dict then); every time for a single report (``shared`` is None)."""
    if shared is None:
        return compute()
    if key not in shared:
        shared[key] = compute()
    return shared[key]


def _class_attendance(reports, term):
    """(period, {learner id: attendance row}) for every learner of a class
    print, from one pass."""
    from attendance import analytics
    start, end = analytics.term_range(term)
    period = analytics.build_period(start, end)
    rows = analytics.build_learners(period, None, student_ids=[r.student_id for r in reports])
    return period, {row["student"].pk: row for row in rows}


def _build_report_card_context(report, shared=None):
    """
    Build the shared context dict for report card rendering (both preview and PDF).

    Returns a dict with keys: report, student, attendance, subjects,
    checkpoint_scores (if applicable), and ecd (if ECD report).
    ``shared`` (a dict) lets a class print look up class-wide data once.
    """
    ctx = {"report": report, "student": report.student}

    # Attendance over the term's school days so far: weekends, holidays and
    # days before enrolment are left out, and unmarked days are never counted
    # as absent. Present includes late; absent includes excused.
    from attendance import analytics
    term = report.term
    if shared is None:
        # One report: still look attendance up once for this card and the
        # progress report built into it.
        shared = {}
    if "attendance_rows" not in shared:
        start, end = analytics.term_range(term)
        att = analytics.learner_attendance(report.student, start, end)
        shared["attendance_period"] = att["period"]
        shared["attendance_rows"] = {report.student_id: att["row"]}
    row = shared["attendance_rows"].get(report.student_id) or {}
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
    ctx["reopens_on"] = _shared(shared, ("reopens", term.pk), lambda: _reopens_on(term))
    ctx["class_teacher"] = _shared(
        shared, ("class_teacher", report.student.class_name, term.pk, report.generated_by_id),
        lambda: _class_teacher_name(report),
    )
    ctx["signatures"] = report_signatures(report, shared)

    if report.is_ecd_report:
        ctx["ecd"] = build_ecd_report_context(report, shared=shared)
        return ctx

    # Official report card = the Student Progress Report for the term, plus
    # the report's general traits, class teacher remark and sign-off.
    from academics.views.learner_report import build_learner_report
    learner = build_learner_report(report.student, term, shared=shared)
    learner.pop("class_teacher")
    ctx.update(learner)
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
    exam_weights = _shared(shared, ("exam_weights",), get_exam_weights)
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

        # The learner's class this term, for "Print all" and the class-wide approve buttons.
        if user.role != UserRole.PARENT:
            class_reports = self.class_report_queryset(report)
            ctx["class_name"] = report.student.class_name
            ctx["class_total"] = (
                self.ecd_class_learners(report).count() if report.is_ecd_report else class_reports.count()
            )
            unpublished = class_reports.exclude(status=ReportCardStatus.PUBLISHED)
            ctx["class_unpublished"] = unpublished.count()
            # HOD step: submitted reports not yet approved. HOS step: approved ones.
            ctx["class_awaiting_hod"] = unpublished.filter(
                status=ReportCardStatus.PENDING_SIGN_OFF, ecd_hod_approved_by__isnull=True).count()
            ctx["class_ready"] = unpublished.filter(ecd_hod_approved_by__isnull=False).count()
            from academics.approval_policy import approver_class_names, can_approve_grades
            scope = approver_class_names(user) if can_approve_grades(user) else set()
            ctx["can_hod_approve"] = scope is None or report.student.class_name in scope

        return ctx

    @staticmethod
    def ecd_class_learners(report):
        """The children in the learner's ECD class that term, as the teacher's list shows them."""
        from academics.views.ecd import ecd_class_roster
        return ecd_class_roster(report.student.class_name, report.term)

    @staticmethod
    def class_report_queryset(report):
        # Same reports the class-wide sign-off acts on (class name and term).
        return ReportCard.objects.filter(
            term=report.term, student__class_name=report.student.class_name,
            is_ecd_report=report.is_ecd_report,
        )


class ClassReportPrintView(HOSReportPreviewView):
    """Every report in the learner's class for that term on one page, one learner per printed page."""

    def get_context_data(self, **kwargs):
        if self.request.user.role == UserRole.PARENT:
            raise PermissionDenied()
        ctx = super().get_context_data(**kwargs)
        from django.template.loader import render_to_string
        report = ctx["report"]
        partial = "academics/_ecd_report_body.html" if report.is_ecd_report else "academics/_progress_report_body.html"
        related = ("student", "student__from_applicant", "term__academic_year", "signed_off_by", "generated_by",
                   "ecd_hod_approved_by")
        if report.is_ecd_report:
            # One sheet per child in the class: a child whose report has not
            # been started still gets a blank sheet, and learners who left the
            # class are not printed.
            learners = list(self.ecd_class_learners(report).order_by("last_name", "first_name"))
            by_student = {
                r.student_id: r for r in ReportCard.objects.filter(
                    term=report.term, student__in=learners, is_ecd_report=True,
                ).select_related(*related)
            }
            reports = [
                by_student.get(s.pk) or ReportCard(
                    student=s, term=report.term, is_ecd_report=True,
                    ecd_template_type=report.ecd_template_type, status=ReportCardStatus.DRAFT,
                    generated_by=report.generated_by,
                )
                for s in learners
            ]
            ctx["class_not_started"] = sum(1 for r in reports if r.pk is None)
        else:
            reports = list(
                self.class_report_queryset(report)
                .select_related(*related)
                .order_by("student__last_name", "student__first_name")
            )
        ctx["class_total"] = len(reports)
        # Class-wide lookups (attendance pass, class teacher, reopening date,
        # ECD report lines) are made once for the whole class.
        period, rows = _class_attendance(reports, report.term)
        shared = {"attendance_period": period, "attendance_rows": rows}
        ctx["class_view"] = True
        # The report partials use nothing from the request, so they render
        # without it: the context processors (notifications, task counts,
        # branding) would otherwise run again for every learner.
        ctx["class_reports"] = [
            render_to_string(partial, _build_report_card_context(r, shared)) for r in reports
        ]
        return ctx


class HOSSignOffActionView(AllowedRolesEnforcedMixin, RoleRequiredMixin, View):
    """Report actions posted from the preview and the Reports page: HOD
    approve (``hod_approve``), HOS approve and publish (``approve``, the
    default) or return (``reject``), for one report (``report_id``) or a
    whole class (``class_name`` + ``term_id``)."""
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.change_reportcard"

    VERBS = {"hod_approve": "approved by the HOD", "reject": "returned", "approve": "approved and published"}

    def post(self, request, *args, **kwargs):
        if not request.user.has_role(UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD):
            raise PermissionDenied()

        report_id = request.POST.get("report_id")
        class_name = request.POST.get("class_name")
        term_id = request.POST.get("term_id")
        action = request.POST.get("action", "approve")
        if action in {"signoff", "ecd_hod_approve"}:  # older forms
            action = "approve" if action == "signoff" else "hod_approve"
        if action not in self.VERBS:
            action = "approve"
        reason = request.POST.get("reason", "")

        from academics.services import hod_approve_report, sign_off_report, reject_report_for_edit
        from core.scoping import hod_class_names
        section_classes = hod_class_names(request.user)

        def act(report):
            if action == "reject":
                reject_report_for_edit(report, request.user, reason=reason, subjects=request.POST.getlist("subjects"))
            elif action == "hod_approve":
                hod_approve_report(report, request.user)
            else:
                sign_off_report(report, request.user)

        if report_id:
            try:
                report = ReportCard.objects.select_related("student").get(pk=report_id)
                if section_classes is not None and report.student.class_name not in section_classes:
                    raise PermissionDenied(f"{report.student.class_name} is outside your section.")
                act(report)
                if action == "hod_approve":
                    _notify_reports_hod_approved(request.user, [report])
                messages.success(request, f"Report for {report.student.first_name} {self.VERBS[action]}.")
            except ReportCard.DoesNotExist:
                messages.error(request, "That report no longer exists: it may have been deleted. Refresh the page.")
            except Exception as e:
                messages.error(request, f"Nothing was changed: {_action_error(e)}")
        elif class_name and term_id:
            if section_classes is not None and class_name not in section_classes:
                messages.error(request, f"Nothing was changed: {class_name} is outside your section.")
                return _back_to(request, "academics:report_review_queue")
            reports = ReportCard.objects.filter(student__class_name=class_name, term_id=term_id).exclude(status=ReportCardStatus.PUBLISHED)
            skipped = 0
            if action == "approve":
                # Publishing a class: reports still waiting for the HOD are
                # left as they are rather than listed as failures.
                skipped = reports.filter(ecd_hod_approved_by__isnull=True).count()
                reports = reports.exclude(ecd_hod_approved_by__isnull=True)
            elif action == "hod_approve":
                # Only submitted reports not yet approved are the HOD's to approve.
                reports = reports.filter(status=ReportCardStatus.PENDING_SIGN_OFF, ecd_hod_approved_by__isnull=True)
            done, blocked = [], []
            for r in reports.select_related("student"):
                try:
                    act(r)
                    done.append(r)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning("Bulk %s failed for report %s: %s", action, r.pk, exc)
                    blocked.append(f"{r.student.first_name} {r.student.last_name} ({_action_error(exc)})")

            if action == "hod_approve" and done:
                _notify_reports_hod_approved(request.user, done)
            verb = self.VERBS[action]
            if skipped:
                messages.info(request, f"{skipped} report(s) in {class_name} are not yet approved by the HOD and were not published.")
            if not done and not blocked:
                if not skipped:
                    messages.info(request, f"There were no reports in {class_name} to process.")
            elif not blocked:
                messages.success(request, f"{len(done)} report(s) in {class_name} {verb}.")
            else:
                level = messages.warning if done else messages.error
                level(request, f"{len(done)} report(s) {verb}; {len(blocked)} not: " + "; ".join(blocked))
        else:
            messages.error(request, "Nothing was changed: no report or class was selected.")

        return _back_to(request, "academics:report_review_queue")


class ReportReviewQueueView(AllowedRolesEnforcedMixin, RoleRequiredMixin, TemplateView):
    """Every section's reports for a term, filtered by year, term, class and
    assessment type. Two gates: the HOD approves a submitted, complete
    report; the Head of School then approves and publishes it."""
    template_name = "academics/report_review_queue.html"
    allowed_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.view_reportcard"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from collections import defaultdict
        from academics.progress_filters import ProgressFilters

        user = self.request.user
        ctx["academics_tab"] = "review_queue"
        pf = ProgressFilters(self.request)
        term = pf.term
        ctx["term"] = term

        # Departments the user may see; ``dept`` (old links) narrows, never widens.
        if user.section_departments and not user.is_school_wide:
            allowed_depts = list(user.section_departments)
        else:
            allowed_depts = [Department.ECD, Department.PRIMARY, Department.LOWER_SECONDARY]
        dept_filter = self.request.GET.get("dept", "")
        dept_map = {"primary": Department.PRIMARY, "ecd": Department.ECD, "lower_secondary": Department.LOWER_SECONDARY}
        if dept_filter in dept_map:
            allowed_depts = [d for d in allowed_depts if d == dept_map[dept_filter]]

        class_options = list(
            GradeClass.objects.filter(department__in=allowed_depts)
            .order_by("sort_order", "name").values_list("name", flat=True)
        )
        class_name = pf.pick_class(class_options, allow_all=True)
        pf.pick_subject([], note="All subjects")
        exam_type = pf.pick_type(all_label="Combined term report")
        ctx["exam_type_name"] = next((e.name for e in pf.exam_types if e.code == exam_type), "")
        status_filter = self.request.GET.get("status", "")
        if status_filter not in {"published", "all"}:
            status_filter = ""
        ctx["status_filter"] = status_filter
        ctx["pf"] = pf.context(hidden={k: v for k, v in (("dept", dept_filter), ("status", status_filter)) if v})
        # Links that switch the status keep the other filters.
        keep = {k: v for k, v in (("year", pf.year.pk if pf.year else ""), ("term", term.pk if term else ""),
                                  ("class_name", class_name), ("type", exam_type), ("dept", dept_filter)) if v}
        ctx["status_links"] = [
            (value, label, "?" + urlencode({**keep, **({"status": value} if value else {})}))
            for value, label in (("", "Pending"), ("published", "Published"), ("all", "All"))
        ]

        if not term:
            return ctx

        shown_classes = [class_name] if class_name else class_options
        order = {c: i for i, c in enumerate(class_options)}
        base_qs = ReportCard.objects.filter(term=term, student__class_name__in=shown_classes)

        if status_filter == "published":
            reports_qs = base_qs.filter(status=ReportCardStatus.PUBLISHED)
        elif status_filter == "all":
            reports_qs = base_qs
        else:
            reports_qs = base_qs.filter(comments_submitted=True).exclude(status=ReportCardStatus.PUBLISHED)
        reports = sorted(
            reports_qs.select_related("student", "generated_by", "ecd_hod_approved_by"),
            key=lambda rc: (order.get(rc.student.class_name, 999), rc.student.last_name, rc.student.first_name),
        )
        pending_reports = list(
            base_qs.filter(comments_submitted=True).exclude(status=ReportCardStatus.PUBLISHED)
            .select_related("student")
        )

        from academics.models import ScoreStatus
        from academics.score_progress import class_progress
        from academics import ecd_report

        everyone = {rc.pk: rc for rc in list(reports) + pending_reports}.values()
        # Per-subject progress for marked reports: complete only when every
        # class subject is approved, not just the ones with scores.
        progress_by_student = {}
        by_class = defaultdict(list)
        for rc in everyone:
            if not rc.is_ecd_report:
                by_class[rc.student.class_name].append(rc.student_id)
        for cls, ids in by_class.items():
            progress_by_student.update(class_progress(ids, term.id, cls))

        # ECD reports are complete when every line of the term's report is rated.
        ecd_complete, structures = set(), {}
        for rc in everyone:
            if not rc.is_ecd_report:
                continue
            class_type = rc.ecd_template_type or ecd_report.class_type_of(rc.student.class_name)
            if not class_type:
                continue
            if class_type not in structures:
                structures[class_type] = ecd_report.ReportStructure(class_type, ecd_report.term_number_of(term))
            structure = structures[class_type]
            if structure.items and not ecd_report.missing_lines(structure, ecd_report.values_for(rc)):
                ecd_complete.add(rc.student_id)

        # Average and position for the selected assessment (or the combined term).
        from academics.class_position import class_results
        results = {}
        shared = {}
        for cls in {rc.student.class_name for rc in reports if not rc.is_ecd_report}:
            for row in class_results(cls, term, shared=shared, exam_type=exam_type or None)["rows"]:
                results[row["student"].pk] = row

        def gates(rc):
            if rc.is_ecd_report:
                complete = rc.student_id in ecd_complete
            else:
                p = progress_by_student.get(rc.student_id)
                complete = bool(p and p["is_complete"])
            comment_ok = len((rc.teacher_comments or "").strip()) >= 50
            approved = bool(rc.ecd_hod_approved_by_id)
            unpublished = rc.status != ReportCardStatus.PUBLISHED
            return {
                "complete": complete,
                "comment_ok": comment_ok,
                "hod_approved": approved,
                # Gate 1: submitted, complete and waiting for the HOD.
                "awaiting_hod": (unpublished and rc.status == ReportCardStatus.PENDING_SIGN_OFF
                                 and complete and comment_ok and not approved),
                # Gate 2: what the Head of School may approve and publish now.
                "ready_to_publish": unpublished and complete and comment_ok and approved,
            }

        # Class teacher as the printed report names it, looked up once per class.
        teacher_by_class = {}
        for rc in reports:
            if rc.student.class_name not in teacher_by_class:
                teacher_by_class[rc.student.class_name] = _class_teacher_name(rc, fallback_to_generator=False)

        rows = []
        for rc in reports:
            g = gates(rc)
            progress = None if rc.is_ecd_report else progress_by_student.get(rc.student_id)
            result = results.get(rc.student_id) or {}
            rows.append({
                "report": rc,
                "student": rc.student,
                **g,
                "all_scores_approved": g["complete"],
                "pending_score_count": sum(
                    1 for v in (progress["subjects"].values() if progress else [])
                    if v["status"] in {ScoreStatus.SUBMITTED, ScoreStatus.RETURNED}
                ),
                "average": result.get("average"),
                "position": result.get("position"),
                "class_name": rc.student.class_name,
                "teacher": teacher_by_class.get(rc.student.class_name, ""),
                "is_ecd": rc.is_ecd_report,
                "approved_subjects": progress["approved_subjects"] if progress else 0,
                "total_subjects": progress["total_subjects"] if progress else 0,
                "subject_names": sorted(progress["subjects"]) if progress else [],
            })
        ctx["reports"] = rows

        pending_gates = [gates(rc) for rc in pending_reports]
        ctx["total_pending"] = len(pending_gates)
        ctx["total_awaiting_hod"] = sum(1 for g in pending_gates if g["awaiting_hod"])
        ctx["total_ready"] = sum(1 for g in pending_gates if g["ready_to_publish"])
        ctx["total_blocked"] = ctx["total_pending"] - ctx["total_ready"] - ctx["total_awaiting_hod"]
        ctx["total_published"] = base_qs.filter(status=ReportCardStatus.PUBLISHED).count()
        from academics.approval_policy import can_approve_grades
        ctx["can_hod_approve"] = can_approve_grades(user)

        # Publishing waits for the grading deadline and the end of end-term exams.
        today = timezone.localdate()
        grading_dl = getattr(term, "grading_deadline", None)
        endterm_end = getattr(term, "endterm_exam_end_date", None)
        ctx["signoff_block_reason"] = (
            f"Grading deadline not reached ({grading_dl.strftime('%d %b %Y')})" if grading_dl and today < grading_dl
            else f"End-term exams not yet ended ({endterm_end.strftime('%d %b %Y')})" if endterm_end and today < endterm_end
            else ""
        )
        return ctx

    # Past-tense wording for the result message, per action.
    ACTION_VERBS = {
        "hod_approve": "approved by the HOD",
        "reject": "returned for correction",
        "revoke": "had the sign-off revoked",
        "approve": "approved and published",
    }

    def post(self, request, *args, **kwargs):
        """One report (``report_id``) or a selection (``report_ids``)."""
        action = request.POST.get("action", "approve")
        if action == "ecd_hod_approve":  # older forms
            action = "hod_approve"
        if action not in self.ACTION_VERBS:
            action = "approve"
        reason = request.POST.get("reason", "").strip()
        ids = [i for i in request.POST.getlist("report_ids") + [request.POST.get("report_id", "")] if i.isdigit()]

        from academics.approval_policy import approver_class_names
        from academics.services import (
            hod_approve_report, reject_report_for_edit, revoke_report_signoff, sign_off_report,
        )

        if not ids:
            messages.error(request, "Nothing was changed: no report was selected.")
            return _back_to(request, "academics:report_review_queue")
        if action in {"reject", "revoke"} and not reason:
            messages.error(request, "Nothing was changed: a reason is required.")
            return _back_to(request, "academics:report_review_queue")

        user = request.user
        try:
            # GRD-009 / GRD-016: who may do what.
            if action == "approve" and user.role not in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN}:
                raise PermissionDenied("Only the Head of School or Super Admin can approve and publish reports.")
            if action == "revoke" and user.role not in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN}:
                raise PermissionDenied("Only HOS or Super Admin can revoke a signed-off report.")
        except PermissionDenied as e:
            messages.error(request, f"Nothing was changed: {_action_error(e)}")
            return _back_to(request, "academics:report_review_queue")

        scope = approver_class_names(user)
        reports = ReportCard.objects.filter(pk__in=ids).select_related("student", "generated_by")
        done, failed = [], []
        for report in reports:
            name = f"{report.student.first_name} {report.student.last_name}"
            try:
                if scope is not None and report.student.class_name not in scope:
                    raise PermissionDenied(f"{report.student.class_name} is outside your section.")
                if action == "hod_approve":
                    hod_approve_report(report, user)
                elif action == "reject":
                    reject_report_for_edit(report, user, reason=reason, subjects=request.POST.getlist("subjects"))
                elif action == "revoke":
                    revoke_report_signoff(report, user, reason=reason)
                else:
                    sign_off_report(report, user)
                done.append(report)
            except Exception as e:
                failed.append(f"{name}: {_action_error(e)}")
        missing = len(ids) - len(done) - len(failed)

        if action == "hod_approve" and done:
            _notify_reports_hod_approved(user, done)

        verb = self.ACTION_VERBS[action]
        if len(done) == 1 and not failed:
            messages.success(request, f"{done[0].student.first_name}'s report {verb}.")
        elif done and not failed:
            messages.success(request, f"{len(done)} reports {verb}.")
        elif done:
            messages.warning(request, f"{len(done)} report(s) {verb}; {len(failed)} not changed. " + " ".join(failed))
        elif failed:
            messages.error(request, "Nothing was changed. " + " ".join(failed))
        if missing > 0:
            messages.error(request, f"{missing} selected report(s) no longer exist. Refresh the page.")
        return _back_to(request, "academics:report_review_queue")


def _notify_reports_hod_approved(approver, reports):
    """One message per Head of School for a batch of HOD approvals (skipped
    when the Head of School approved them themself)."""
    if approver.has_role(UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN):
        return
    try:
        from communications.email_service import dispatch_notification
        from users.models import User
        classes = sorted({r.student.class_name for r in reports})
        count = len(reports)
        term = reports[0].term
        link = reverse("academics:report_review_queue") + "?" + urlencode(
            {"year": term.academic_year_id, "term": term.pk, **({"class_name": classes[0]} if len(classes) == 1 else {})}
        )
        for hos in User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True):
            dispatch_notification(
                user=hos,
                title="Reports ready to publish",
                message=(
                    f"{approver.get_full_name()} approved {count} report{'s' if count != 1 else ''} "
                    f"({', '.join(classes)}). They are ready for you to approve and publish."
                ),
                link=link,
                actor=approver,
            )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("HOD approval notification failed")


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


class AtRiskStudentsListView(RoleRequiredMixin, TemplateView):
    template_name = "academics/at_risk_list.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.view_examscore"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["pass_mark"] = int(get_pass_mark())
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
                    if is_at_risk(avg_pct):  # below the pass mark
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

        # GRD-014: Parent can only download after HOS sign-off, and only their
        # own child's. Applies to the parent role even without a guardian
        # record, and to anyone else who is linked as a guardian.
        from students.models import ParentGuardian, Student
        guardian_ids = list(ParentGuardian.objects.filter(user=request.user).values_list("pk", flat=True))
        if request.user.role == UserRole.PARENT or guardian_ids:
            if report.status != ReportCardStatus.PUBLISHED:
                raise PermissionDenied("Report not yet signed off by HOS.")
            if not Student.objects.filter(studentguardian__guardian_id__in=guardian_ids,
                                          pk=report.student_id).exists():
                raise PermissionDenied()
        return render_report_pdf_response(request, report)


def render_report_pdf_response(request, report):
    """The report card as a PDF download (HTML page if WeasyPrint is missing).

    Callers do the access checks first.
    """
    student = report.student

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

    # Imported before the try: its except clause names FuturesTimeout, which must
    # exist even when the WeasyPrint import fails.
    import time
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
    try:
        from weasyprint import HTML
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


