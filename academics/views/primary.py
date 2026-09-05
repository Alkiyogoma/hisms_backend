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


from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt


@method_decorator(csrf_exempt, name='dispatch')
class PrimaryCommentEntryView(RoleRequiredMixin, TemplateView):
    """View for Primary teachers to enter narrative comments in bulk."""
    template_name = "academics/primary_comments.html"
    allowed_roles = [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.change_reportcard"

    WORK_HABITS = [
        {"key": "wh_follows_directions", "label": "Follows directions"},
        {"key": "wh_works_independently", "label": "Works well independently"},
        {"key": "wh_not_disturb", "label": "Does not disturb others"},
        {"key": "wh_completes_neatly", "label": "Completes work neatly"},
        {"key": "wh_completes_required", "label": "Completes work required"},
    ]
    PERSONAL_TRAITS = [
        {"key": "pt_creativity", "label": "Displays creativity"},
        {"key": "pt_honest", "label": "Is honest"},
        {"key": "pt_homework", "label": "Successfully completes homework and assignments"},
        {"key": "pt_attention", "label": "Attention span"},
        {"key": "pt_flexibility", "label": "Displays flexibility"},
    ]
    SOCIAL_TRAITS = [
        {"key": "st_respects", "label": "Show respect for authority"},
        {"key": "st_self_control", "label": "Exhibits self-control"},
        {"key": "st_correction", "label": "Responds well to correction"},
        {"key": "st_relates_well", "label": "Relates well with others"},
        {"key": "st_courteous", "label": "Is courteous"},
    ]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        term = get_current_term()
        ctx["current_term"] = term
        ctx["academics_tab"] = "comments"
        from django.utils import timezone
        today = timezone.localdate()
        ctx["today_display"] = today.strftime("%d %B %Y")
        
        # Comment window gating: open after endterm exams, close after grading_deadline
        endterm_end = getattr(term, "endterm_exam_end_date", None) if term else None
        grading_dl = getattr(term, "grading_deadline", None) if term else None
        comment_window_open = False
        comment_window_status = "closed_early"
        if term:
            if grading_dl and today > grading_dl:
                comment_window_status = "closed_late"
            elif endterm_end and today >= endterm_end:
                comment_window_open = True
                comment_window_status = "open"
            else:
                comment_window_status = "closed_early"
        ctx["comment_window_open"] = comment_window_open
        ctx["comment_window_status"] = comment_window_status
        ctx["comment_window_endterm_end"] = endterm_end
        ctx["comment_window_grading_deadline"] = grading_dl
        
        # Filter classes based on role — comments are class-teacher only
        if self.request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            teacher_tca = get_teacher_assigned_classes_from_tca(self.request.user)
            ctx["classes"] = sorted(c for c, info in teacher_tca.items() if info.get("is_class_teacher"))
        else:
            ctx["classes"] = sorted(grade_class_names_for_department(Department.PRIMARY))
            
        selected_class = self.request.GET.get("class_name")
        if selected_class and term:
            students = Student.objects.filter(class_name=selected_class, is_archived=False).order_by("last_name", "first_name")
            
            # Only generate missing report cards once
            if not ReportCard.objects.filter(term=term, student__in=students).exists():
                generate_class_reports(term.id, selected_class, self.request.user)
            
            report_cards = ReportCard.objects.filter(term=term, student__in=students, is_ecd_report=False)
            
            ctx["selected_class"] = selected_class
            ctx["work_habits"] = self.WORK_HABITS
            ctx["personal_traits"] = self.PERSONAL_TRAITS
            ctx["social_traits"] = self.SOCIAL_TRAITS
            ctx["student_reports"] = [
                {
                    "student": rc.student,
                    "report": rc,
                    "comment": rc.teacher_comments or "",
                    "traits": rc.general_traits or {},
                } for rc in report_cards
            ]
            ctx["all_submitted"] = report_cards.exists() and all(rc.comments_submitted for rc in report_cards)
            
        return ctx

    def post(self, request, *args, **kwargs):
        # AJAX single-comment auto-save
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            import json
            try:
                data = json.loads(request.body)
            except (json.JSONDecodeError, ValueError):
                return JsonResponse({"error": "Invalid JSON"}, status=400)
            report_id = data.get("report_id")
            comment = data.get("comment")
            traits = data.get("traits")
            if not report_id:
                return JsonResponse({"error": "Missing report_id"}, status=400)
            if not request.user or request.user.is_anonymous:
                return JsonResponse({"error": "Not authenticated"}, status=401)
            try:
                rc = ReportCard.objects.select_related("student").get(pk=report_id)
            except (ReportCard.DoesNotExist, Exception):
                return JsonResponse({"error": "Report card not found"}, status=404)
            # Enforce class-teacher-only for TEACHER role
            if request.user.role == UserRole.TEACHER:
                from core.teacher_context import get_teacher_assigned_classes_from_tca
                teacher_tca = get_teacher_assigned_classes_from_tca(request.user)
                class_info = teacher_tca.get(rc.student.class_name, {})
                if not class_info or not class_info.get("is_class_teacher"):
                    return JsonResponse({"error": "Only the class teacher can enter narrative comments"}, status=403)
            try:
                update_fields = ["updated_at"]
                if comment is not None:
                    rc.teacher_comments = comment
                    update_fields.append("teacher_comments")
                if traits is not None:
                    rc.general_traits = traits
                    update_fields.append("general_traits")
                rc.save(update_fields=update_fields)
            except Exception as e:
                return JsonResponse({"error": f"Save failed: {e}"}, status=500)
            try:
                log_event(
                    actor=request.user,
                    action_type="TEACHER_COMMENT_UPDATED",
                    model_name="ReportCard",
                    object_id=rc.pk,
                    description=f"Auto-saved comment for {rc.student.admission_no}",
                    request=request
                )
            except Exception:
                pass
            return JsonResponse({"ok": True, "length": len((comment or "").strip())})

        term_id = request.POST.get("term_id") or request.POST.get("term")
        class_name = request.POST.get("class_name")
        action = request.POST.get("action")
        
        # Comment window gate: block form submit if window not open
        from django.utils import timezone as _tz
        today = _tz.localdate()
        term_obj = Term.objects.filter(pk=term_id).first() if term_id else get_current_term()
        endterm_end = getattr(term_obj, "endterm_exam_end_date", None) if term_obj else None
        grading_dl = getattr(term_obj, "grading_deadline", None) if term_obj else None
        if grading_dl and today > grading_dl:
            messages.error(request, "The grading deadline has passed. Comments can no longer be submitted. Contact your admin.")
            return redirect(f"{request.path}?class_name={class_name}")
        if endterm_end and today < endterm_end:
            messages.warning(request, f"Comment entry opens after endterm exams end on {endterm_end.strftime('%d %b %Y')}. You can still draft comments, but submission is blocked until then.")
            return redirect(f"{request.path}?class_name={class_name}")
        
        # RBAC: TEACHER scoped to class-teacher-only assignments
        if request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            teacher_tca = get_teacher_assigned_classes_from_tca(request.user)
            class_info = teacher_tca.get(class_name, {})
            if not class_info or not class_info.get("is_class_teacher"):
                messages.error(request, "Access denied: Only the class teacher can enter narrative comments.")
                return redirect("academics:primary_comments_entry")
            
        students = Student.objects.filter(class_name=class_name, is_archived=False)
        report_cards = ReportCard.objects.filter(term_id=term_id, student__in=students, is_ecd_report=False)
        
        updated = 0
        for rc in report_cards:
            comment = request.POST.get(f"comment_{rc.id}")
            if comment is not None:
                if len(comment.strip()) < 50:
                    messages.warning(request, f"Comment for {rc.student.first_name} {rc.student.last_name} is too short (minimum 50 characters).")
                    continue
                rc.teacher_comments = comment
                rc.save(update_fields=["teacher_comments", "updated_at"])
                updated += 1

                log_event(
                    actor=request.user,
                    action_type="TEACHER_COMMENT_UPDATED",
                    model_name="ReportCard",
                    object_id=rc.pk,
                    description=f"Teacher comment updated for {rc.student.admission_no} in {class_name}",
                    request=request
                )
                
        if action == "submit_for_review":
            rc_ids = []
            for rc in report_cards:
                comment = request.POST.get(f"comment_{rc.id}")
                if comment is None or len(comment.strip()) < 50:
                    continue
                rc.comments_submitted = True
                rc.save(update_fields=["comments_submitted", "updated_at"])
                rc_ids.append(rc.pk)

            if rc_ids:
                # Notify HOD/HOS
                from users.models import User
                from communications.email_service import dispatch_notification
                from core.teacher_context import is_ecd_teacher
                
                hod_roles = [UserRole.PRIMARY_HOD, UserRole.HEAD_OF_SCHOOL]
                recipients = User.objects.filter(role__in=hod_roles, is_active=True).distinct()
                for rec in recipients:
                    dispatch_notification(
                        user=rec,
                        title="Comments Ready for Review",
                        message=f"Teacher {request.user.get_full_name()} has submitted comments for {class_name} — {len(rc_ids)} student(s) ready for HOD/HOS review.",
                        link="/academics/reports/review-queue/",
                        actor=request.user
                    )
                messages.success(request, f"Submitted {len(rc_ids)} report(s) for HOD/HOS review.")
            else:
                messages.warning(request, "No reports met the minimum comment length (50 chars) for submission.")
        elif updated:
            messages.success(request, f"Saved comments for {updated} students.")
            
        return redirect(f"{request.path}?class_name={class_name}")


class ScoreCorrectionHistoryView(RoleRequiredMixin, TemplateView):
    """View for HODs to review audit logs for score corrections."""
    template_name = "academics/correction_history.html"
    allowed_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.view_examscore"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from audit.models import AuditLog
        
        logs = AuditLog.objects.filter(action_type="EXAM_SCORE_CORRECTED").select_related('actor').order_by("-created_at")
        
        # Filter by department if HOD
        if self.request.user.role == UserRole.PRIMARY_HOD:
            # We'd need to filter by student class, but logs only store object_id.
            # For simplicity, we show all for now, or we could join with ExamScore.
            pass
            
        ctx["logs"] = logs
        return ctx


class PrimaryScoreEntryView(RoleRequiredMixin, TemplateView):
    """New focused score entry view for Primary students matching ECD UI."""
    template_name = "academics/primary_score_entry.html"
    allowed_roles = [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.change_examscore"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from academics.models import ExamTypeConfiguration
        
        # Determine available classes
        user = self.request.user
        role = user.role
        teacher_tca = {}
        if role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            teacher_tca = get_teacher_assigned_classes_from_tca(user)
            # Filter for primary classes only
            primary_names = grade_class_names_for_department(Department.PRIMARY)
            classes = sorted(c for c in teacher_tca if c in primary_names)
            ctx["_teacher_tca"] = teacher_tca
            ctx["class_teacher_classes"] = {c for c, info in teacher_tca.items() if info.get("is_class_teacher")}
        elif role == UserRole.PRIMARY_HOD:
            classes = sorted(grade_class_names_for_department(Department.PRIMARY))
        else:
            # HOS/SuperAdmin see all primary classes
            classes = sorted(grade_class_names_for_department(Department.PRIMARY))

        selected_class = self.request.GET.get("class_name")
        if not selected_class and classes:
            selected_class = classes[0]
        
        ctx["classes"] = classes
        ctx["selected_class"] = selected_class
        ctx["current_term"] = get_current_term()
        
        # Get active exam types for the form
        ctx["exam_types"] = ExamTypeConfiguration.objects.filter(is_active=True).order_by('display_order')

        # Pass exam window info so frontend can gray out closed windows
        term = ctx["current_term"]
        if term:
            today = timezone.now().date()
            from academics.utils import _EXAM_WINDOW_MAP, _CODE_TO_TYPE
            active_configs = ExamTypeConfiguration.objects.filter(is_active=True)
            open_codes = set()
            closed_codes = set()
            for et in active_configs:
                canonical = _CODE_TO_TYPE.get(et.code, et.code)
                if canonical == "quiz":
                    quiz_start = getattr(term, "quiz_start_date", None)
                    quiz_end = getattr(term, "quiz_end_date", None)
                    start_ok = (not quiz_start or today >= quiz_start)
                    end_ok = (not quiz_end or today <= quiz_end)
                    if start_ok and end_ok:
                        open_codes.add(et.code)
                    elif quiz_end and today > quiz_end:
                        closed_codes.add(et.code)
                else:
                    mapping = _EXAM_WINDOW_MAP.get(canonical)
                    if not mapping:
                        continue
                    field_start, field_end = mapping
                    exam_start = getattr(term, field_start, None) if field_start else None
                    exam_end = getattr(term, field_end, None) if field_end else None
                    if not exam_start and not exam_end:
                        continue
                    start_ok = (not exam_start or today >= exam_start)
                    end_ok = (not exam_end or today <= exam_end)
                    if start_ok and end_ok:
                        open_codes.add(et.code)
                    elif exam_end and today > exam_end:
                        closed_codes.add(et.code)
            ctx["open_exam_codes"] = open_codes
            ctx["closed_exam_codes"] = closed_codes
        
        # Get subjects for the selected class
        if selected_class:
            if role == UserRole.TEACHER:
                class_info = teacher_tca.get(selected_class, {})
                assigned_subjects = class_info.get("subjects", set())
                # Class teacher sees ALL subjects for their class
                if class_info.get("is_class_teacher"):
                    gc = GradeClass.objects.filter(name=selected_class).first()
                    if gc:
                        all_subs = set(gc.subjects.filter(is_active=True).values_list("name", flat=True))
                        subjects = all_subs
                    else:
                        subjects = assigned_subjects
                else:
                    subjects = assigned_subjects
                ctx["subjects"] = sorted(subjects)
                ctx["editable_subjects"] = assigned_subjects
            else:
                # For HOD/Admin, show all subjects that have scores or are in timetable
                from timetable.models import TimetableSlot
                from academics.models import ExamScore
                subs = set(TimetableSlot.objects.filter(class_name=selected_class).values_list("subject_name", flat=True))
                subs.update(ExamScore.objects.filter(student__class_name=selected_class).values_list("subject_name", flat=True))
                if not subs:
                    from academics.models import Subject
                    subs = set(Subject.objects.filter(department=Department.PRIMARY).values_list("name", flat=True))
                ctx["subjects"] = sorted(list(subs))
                ctx["editable_subjects"] = set(subs)
        
        ctx["academics_tab"] = "primary_assessment"

        if selected_class and ctx.get("current_term"):
            from academics.models import ReportCard, ReportCardStatus, ExamScore, ScoreStatus
            total_students = Student.objects.filter(class_name=selected_class, is_archived=False).count()
            submitted_count = ReportCard.objects.filter(
                student__class_name=selected_class,
                term=ctx["current_term"],
                status=ReportCardStatus.PENDING_SIGN_OFF,
            ).count()
            has_returned = ExamScore.objects.filter(
                student__class_name=selected_class,
                term=ctx["current_term"],
                status=ScoreStatus.RETURNED,
            ).exists()
            returned_student_ids = list(
                ExamScore.objects.filter(
                    student__class_name=selected_class,
                    term=ctx["current_term"],
                    status=ScoreStatus.RETURNED,
                ).values_list("student_id", flat=True).distinct()
            )
            ctx["class_submitted"] = total_students > 0 and submitted_count >= total_students and not has_returned
            ctx["returned_count"] = len(returned_student_ids)
            ctx["returned_student_ids"] = returned_student_ids

            # Check if there are any scores waiting to be submitted (new midterm/quiz entries)
            has_unsubmitted = ExamScore.objects.filter(
                student__class_name=selected_class,
                term=ctx["current_term"],
                status__in=[ScoreStatus.DRAFT, ScoreStatus.SUBMITTED],
            ).exists()
            if has_unsubmitted:
                ctx["class_submitted"] = False

        return ctx

class PrimaryStudentsAPIView(RoleRequiredMixin, View):
    """API to get student list for Primary focused entry."""
    allowed_roles = [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "students.view_student"
    def get(self, request):
        class_name = request.GET.get("class_name")
        if not class_name:
            return JsonResponse({"error": "Class name required"}, status=400)
        
        # RBAC: TEACHER scoped to assigned classes
        if request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            if class_name not in get_teacher_assigned_classes_from_tca(request.user):
                return JsonResponse({"error": "Access denied"}, status=403)
        
        students = Student.objects.filter(class_name=class_name, is_archived=False).order_by("last_name", "first_name")
        term_id = request.GET.get("term")
        student_ids = list(students.values_list("id", flat=True))
        scored_ids = set()
        submitted_ids = set()
        if term_id and student_ids:
            from academics.models import ExamScore
            scored_ids = set(
                ExamScore.objects.filter(student_id__in=student_ids, term_id=term_id)
                .values_list("student_id", flat=True)
                .distinct()
            )
            from academics.models import ReportCard, ReportCardStatus
            submitted_ids = set(
                ReportCard.objects.filter(
                    student_id__in=student_ids, term_id=term_id,
                    status=ReportCardStatus.PENDING_SIGN_OFF,
                )
                .values_list("student_id", flat=True)
                .distinct()
            )
        rc_map = {}
        if term_id and student_ids:
            for rc in ReportCard.objects.filter(student_id__in=student_ids, term_id=term_id):
                rc_map[rc.student_id] = rc.status

        # Per-student score status summary for sidebar chips
        score_status_map = {}
        if term_id and student_ids:
            from academics.models import ScoreStatus
            for sid in student_ids:
                statuses = set(
                    ExamScore.objects.filter(student_id=sid, term_id=term_id)
                    .values_list("status", flat=True)
                )
                if not statuses:
                    score_status_map[sid] = "draft"
                elif ScoreStatus.RETURNED in statuses:
                    score_status_map[sid] = "returned"
                elif ScoreStatus.APPROVED in statuses and ScoreStatus.DRAFT not in statuses and ScoreStatus.SUBMITTED not in statuses:
                    score_status_map[sid] = "approved"
                elif ScoreStatus.SUBMITTED in statuses:
                    score_status_map[sid] = "submitted"
                elif ScoreStatus.DRAFT in statuses:
                    score_status_map[sid] = "draft"

        data = [
            {
                "id": s.id,
                "first_name": s.first_name,
                "last_name": s.last_name,
                "admission_no": s.admission_no,
                "has_scores": s.id in scored_ids,
                "report_card_status": rc_map.get(s.id, ReportCardStatus.DRAFT),
                "score_status": score_status_map.get(s.id, ""),
            }
            for s in students
        ]
        return JsonResponse({"students": data})

class PrimaryScoreAPIView(RoleRequiredMixin, View):
    """API for getting/saving all primary scores for a student in a term."""
    allowed_roles = [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.change_examscore"

    def _student_in_teacher_classes(self, request, student) -> bool:
        if request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            return student.class_name in get_teacher_assigned_classes_from_tca(request.user)
        if request.user.role in [UserRole.PRIMARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]:
            return True
        return False

    def get(self, request, student_id):
        if request.user.role not in [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]:
            return JsonResponse({"error": "Permission denied"}, status=403)

        term_id = request.GET.get("term")
        if not term_id:
            return JsonResponse({"error": "Missing term param"}, status=400)
        
        student = get_object_or_404(Student, id=student_id)
        if not self._student_in_teacher_classes(request, student):
            return JsonResponse({"error": "Access denied"}, status=403)
        
        scores = ExamScore.objects.filter(student_id=student_id, term_id=term_id)
        rc = ReportCard.objects.filter(student_id=student_id, term_id=term_id).first()
        rc_status = rc.status if rc else ReportCardStatus.DRAFT
        
        # structure: { "Subject Name": { "quiz": {...}, "mid_term": {...}, ... } }
        score_data = {}
        for s in scores:
            if s.subject_name not in score_data:
                score_data[s.subject_name] = {}
            score_data[s.subject_name][s.exam_type] = {
                "score": float(s.score),
                "is_locked": s.is_locked,
                "status": s.status,
                "correction_reason": s.correction_reason or "",
                "hod_feedback": s.hod_feedback or "",
            }

        return JsonResponse({
            "scores": score_data,
            "report_card_status": rc_status
        })

    def post(self, request, student_id):
        if request.user.role not in [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]:
            return JsonResponse({"error": "Permission denied"}, status=403)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        term_id = data.get("term")
        all_subject_scores = data.get("scores", {}) # { "Math": {"quiz": "85", ...}, "English": {...} }
        
        if not term_id:
            return JsonResponse({"error": "Missing term_id"}, status=400)
            
        # Check ReportCard status for global lock
        rc = ReportCard.objects.filter(student_id=student_id, term_id=term_id).first()
        if rc and rc.status != ReportCardStatus.DRAFT:
            from academics.models import ScoreStatus as _ScoreStatus
            # Only block exam codes that have already been submitted/approved.
            # Allow new exam types (midterm, endterm) that haven't been submitted yet.
            submitted_codes = set(
                ExamScore.objects.filter(
                    student_id=student_id, term_id=term_id,
                    status__in=[_ScoreStatus.SUBMITTED, _ScoreStatus.APPROVED]
                ).values_list("exam_type", flat=True)
            )
            # Collect all exam codes the teacher is trying to save
            codes_to_save = set()
            for _subj, _codes in all_subject_scores.items():
                if isinstance(_codes, dict):
                    codes_to_save.update(_codes.keys())
            # If all codes being saved are already submitted/approved, block
            if codes_to_save and codes_to_save.issubset(submitted_codes):
                return JsonResponse({"error": "Evaluation is locked (Submitted to HOD)"}, status=403)

        student = get_object_or_404(Student, id=student_id)
        if not self._student_in_teacher_classes(request, student):
            return JsonResponse({"error": "Access denied"}, status=403)
        term = get_object_or_404(Term, id=term_id)

        if term.is_locked:
            return JsonResponse(
                {"error": "This term is locked and cannot accept score entry."},
                status=403,
            )

        from academics.utils import check_score_entry_allowed
        from django.utils import timezone as _tz
        today = _tz.now().date()

        skipped_subjects = set()
        soft_warnings = []

        # TEACHER scoped to only their assigned subjects per class
        if request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            tca = get_teacher_assigned_classes_from_tca(request.user)
            class_info = tca.get(student.class_name, {})
            allowed_subjects = class_info.get("subjects", set())
            before_keys = set(all_subject_scores.keys())
            all_subject_scores = {k: v for k, v in all_subject_scores.items() if k in allowed_subjects}
            skipped_subjects = before_keys - set(all_subject_scores.keys())
            if before_keys and not all_subject_scores:
                return JsonResponse({"error": "You are not assigned to teach any subjects in this class."}, status=403)
        
        from academics.models import ExamTypeConfiguration, ScoreStatus
        active_configs = {c.code: c for c in ExamTypeConfiguration.objects.filter(is_active=True)}
        
        updated = 0
        for subject_name, subjects_scores in all_subject_scores.items():
            for code, val in subjects_scores.items():
                if code not in active_configs: continue
                if val == "" or val is None: continue

                allowed, msg = check_score_entry_allowed(term, code, today)
                if not allowed:
                    from academics.models import ScoreStatus
                    existing_returned = ExamScore.objects.filter(
                        student=student, term=term, subject_name=subject_name,
                        exam_type=code, status=ScoreStatus.RETURNED
                    ).exists()
                    if not existing_returned:
                        soft_warnings.append(f"{subject_name}/{code}: {msg}")
                        continue

                try:
                    score_val = Decimal(str(val))
                    max_score_val = int(active_configs[code].max_score)
                    if score_val < 0 or score_val > max_score_val:
                        return JsonResponse({"error": f"Score {score_val} is out of range (0-{max_score_val})"}, status=400)
                    if score_val != score_val.to_integral_value():
                        return JsonResponse({"error": f"Score {score_val} must be a whole number"}, status=400)
                except (ValueError, DecimalException, KeyError):
                    continue

                # Check for existing individual lock (skip returned scores — teacher must be able to correct them)
                existing = ExamScore.objects.filter(
                    student=student, 
                    term=term, 
                    subject_name=subject_name, 
                    exam_type=code
                ).first()
                if existing and existing.is_locked and existing.status != "returned":
                    continue

                score_obj, created = ExamScore.objects.get_or_create(
                    student=student,
                    term=term,
                    subject_name=subject_name,
                    exam_type=code,
                    defaults={
                        "score": score_val,
                        "max_score": active_configs[code].max_score,
                        "entered_by": request.user,
                        "exam_type_config": active_configs[code]
                    }
                )
                if not created:
                    was_returned = score_obj.status == "returned"
                    score_obj.score = score_val
                    score_obj.max_score = active_configs[code].max_score
                    score_obj.entered_by = request.user
                    score_obj.exam_type_config = active_configs[code]
                    if was_returned:
                        score_obj.status = ScoreStatus.DRAFT
                try:
                    score_obj.full_clean()
                except ValidationError as e:
                    if created:
                        score_obj.delete()
                    return JsonResponse({"error": str(e)}, status=400)
                if not created:
                    save_fields = ["score", "max_score", "entered_by", "exam_type_config", "updated_at"]
                    if was_returned:
                        save_fields.append("status")
                    score_obj.save(update_fields=save_fields)
                else:
                    # Re-save to trigger post-save signals if any; already saved via get_or_create
                    score_obj.save(update_fields=["updated_at"])
                updated += 1

                # Audit logging
                from audit.models import log_event
                if created:
                    log_event(
                        actor=request.user,
                        action_type="EXAM_SCORE_ENTERED",
                        model_name="ExamScore",
                        object_id=score_obj.pk,
                        description=f"Score {score_val} entered for {student.admission_no} in {subject_name} ({code})",
                        request=request,
                    )
                elif existing and existing.score != score_val:
                    # Correction tracking — don't auto-lock; HOD approval handles locking
                    score_obj.previous_score = existing.score
                    score_obj.corrected_by = request.user
                    score_obj.correction_reason = "Corrected via primary assessment"
                    score_obj.save(update_fields=["corrected_by_id", "correction_reason", "previous_score", "updated_at"])
                    log_event(
                        actor=request.user,
                        action_type="EXAM_SCORE_CORRECTED",
                        model_name="ExamScore",
                        object_id=score_obj.pk,
                        description=f"Score corrected from {float(existing.score)} to {float(score_val)} for {student.admission_no} in {subject_name} ({code})",
                        before_value=str(float(existing.score)),
                        after_value=str(float(score_val)),
                        request=request,
                    )
                    # FR-ACAD-003: Auto-recalculate weighted average on correction
                    from academics.services import recalculate_report_card_average
                    rc = ReportCard.objects.filter(student=student, term=term).first()
                    if rc:
                        recalculate_report_card_average(rc)
            
        response_data = {"success": True, "updated": updated}
        if skipped_subjects:
            response_data["warning"] = f"Subjects not in your assignment were skipped: {', '.join(sorted(skipped_subjects))}"
        if soft_warnings:
            response_data["window_warnings"] = soft_warnings
        return JsonResponse(response_data)


class PrimaryBulkSubmissionAPIView(RoleRequiredMixin, View):
    """API for submitting an entire Primary class to HOD for review."""
    allowed_roles = [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.change_reportcard"

    def post(self, request):

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        class_name = data.get("class_name")
        term_id = data.get("term")
        
        if not (class_name and term_id):
            return JsonResponse({"error": "Missing parameters"}, status=400)
        
        # RBAC: TEACHER scoped to assigned classes
        if request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            if class_name not in get_teacher_assigned_classes_from_tca(request.user):
                return JsonResponse({"error": "Access denied"}, status=403)

        term = get_object_or_404(Term, id=term_id)

        if term.is_locked:
            return JsonResponse(
                {"error": "This term is locked and cannot accept score entry."},
                status=403,
            )

        students = Student.objects.filter(class_name=class_name, is_archived=False)

        # Hard-gate: reject submission if any score's exam window hasn't opened.
        # Skip quiz — quiz scores are saved with a warning at save time; the
        # submit should not re-block them.
        from academics.utils import check_score_entry_allowed
        from academics.models import ExamScore, ScoreStatus
        from django.utils import timezone as _tz
        today = _tz.now().date()
        submitted_types = (
            ExamScore.objects
            .filter(student__class_name=class_name, term=term)
            .exclude(exam_type__in=["quiz", "QZ"])
            .values_list("exam_type", flat=True)
            .distinct()
        )
        for et in submitted_types:
            allowed, msg = check_score_entry_allowed(term, et, today)
            if not allowed:
                has_returned = ExamScore.objects.filter(
                    student__class_name=class_name, term=term, exam_type=et,
                    status=ScoreStatus.RETURNED
                ).exists()
                if not has_returned:
                    return JsonResponse(
                        {"error": f"Cannot submit: {msg}"},
                        status=400,
                    )

        from django.db import transaction
        with transaction.atomic():
            for student in students:
                rc, created = ReportCard.objects.get_or_create(
                    student=student,
                    term=term,
                    defaults={
                        "status": ReportCardStatus.PENDING_SIGN_OFF,
                        "updated_at": timezone.now(),
                        "generated_by": request.user,
                    }
                )
                if not created:
                    rc.status = ReportCardStatus.PENDING_SIGN_OFF
                    rc.updated_at = timezone.now()
                    rc.save(update_fields=["status", "updated_at"])
                # Force save individual scores to ensure they follow the ReportCard status
                from academics.models import ExamScore, ScoreStatus
                from audit.models import log_event
                score_qs = ExamScore.objects.filter(
                    student=student, term=term, status__in=[ScoreStatus.DRAFT, ScoreStatus.RETURNED]
                )
                if request.user.role == UserRole.TEACHER:
                    from core.teacher_context import get_teacher_assigned_classes_from_tca
                    tca = get_teacher_assigned_classes_from_tca(request.user)
                    class_info = tca.get(class_name, {})
                    allowed_subjects = class_info.get("subjects", set())
                    if allowed_subjects:
                        score_qs = score_qs.filter(subject_name__in=allowed_subjects)
                score_ids = list(score_qs.values_list('id', flat=True))
                score_qs.update(
                    status=ScoreStatus.SUBMITTED, 
                    is_locked=True, 
                    updated_at=timezone.now()
                )
                # Audit log each score submission
                for sid in score_ids:
                    log_event(
                        actor=request.user,
                        action_type="EXAM_SCORE_SUBMITTED",
                        model_name="ExamScore",
                        object_id=sid,
                        description=f"Score submitted for {student.admission_no} via primary assessment — {class_name}",
                        request=request,
                    )
        
        # ── HOD Notification ──
        from communications.email_service import dispatch_notification
        from users.models import User
        hod_roles = [UserRole.PRIMARY_HOD, UserRole.HEAD_OF_SCHOOL]
        hods = User.objects.filter(role__in=hod_roles, is_active=True)
        for hod in hods:
            dispatch_notification(
                user=hod,
                title="Primary assessment scores submitted for review",
                message=(
                    f"{request.user.get_full_name()} has submitted all scores for {class_name} "
                    f"({term.name}) for HOD review."
                ),
                link="/academics/exam-score-approval-queue/",
                actor=request.user,
            )
        
        return JsonResponse({
            "success": True, 
            "message": f"Successfully submitted all {students.count()} reports for {class_name} for HOD review."
        })


# ═══════════════════════════════════════════════════════════════════════════════
# LOWER SECONDARY ASSESSMENT (Grade 7–9)
# ═══════════════════════════════════════════════════════════════════════════════

class LowerSecondaryScoreEntryView(RoleRequiredMixin, TemplateView):
    """Score entry view for Lower Secondary students (Grade 7–9)."""
    template_name = "academics/lower_secondary_score_entry.html"
    allowed_roles = [UserRole.TEACHER, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.change_examscore"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from academics.models import ExamTypeConfiguration

        user = self.request.user
        role = user.role
        teacher_tca = {}
        if role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            teacher_tca = get_teacher_assigned_classes_from_tca(user)
            ls_names = grade_class_names_for_department(Department.LOWER_SECONDARY)
            classes = sorted(c for c in teacher_tca if c in ls_names)
            ctx["_teacher_tca"] = teacher_tca
            ctx["class_teacher_classes"] = {c for c, info in teacher_tca.items() if info.get("is_class_teacher")}
        elif role == UserRole.LOWER_SECONDARY_HOD:
            classes = sorted(grade_class_names_for_department(Department.LOWER_SECONDARY))
        else:
            classes = sorted(grade_class_names_for_department(Department.LOWER_SECONDARY))

        selected_class = self.request.GET.get("class_name")
        if not selected_class and classes:
            selected_class = classes[0]

        ctx["classes"] = classes
        ctx["selected_class"] = selected_class
        ctx["current_term"] = get_current_term()

        ctx["exam_types"] = ExamTypeConfiguration.objects.filter(is_active=True).order_by("display_order")

        term = ctx["current_term"]
        if term:
            today = timezone.now().date()
            from academics.utils import _EXAM_WINDOW_MAP, _CODE_TO_TYPE
            active_configs = ExamTypeConfiguration.objects.filter(is_active=True)
            open_codes = set()
            closed_codes = set()
            for et in active_configs:
                canonical = _CODE_TO_TYPE.get(et.code, et.code)
                if canonical == "quiz":
                    quiz_start = getattr(term, "quiz_start_date", None)
                    quiz_end = getattr(term, "quiz_end_date", None)
                    start_ok = not quiz_start or today >= quiz_start
                    end_ok = not quiz_end or today <= quiz_end
                    if start_ok and end_ok:
                        open_codes.add(et.code)
                    elif quiz_end and today > quiz_end:
                        closed_codes.add(et.code)
                else:
                    mapping = _EXAM_WINDOW_MAP.get(canonical)
                    if not mapping:
                        continue
                    field_start, field_end = mapping
                    exam_start = getattr(term, field_start, None) if field_start else None
                    exam_end = getattr(term, field_end, None) if field_end else None
                    if not exam_start and not exam_end:
                        continue
                    start_ok = not exam_start or today >= exam_start
                    end_ok = not exam_end or today <= exam_end
                    if start_ok and end_ok:
                        open_codes.add(et.code)
                    elif exam_end and today > exam_end:
                        closed_codes.add(et.code)
            ctx["open_exam_codes"] = open_codes
            ctx["closed_exam_codes"] = closed_codes

        if selected_class:
            if role == UserRole.TEACHER:
                class_info = teacher_tca.get(selected_class, {})
                assigned_subjects = class_info.get("subjects", set())
                if class_info.get("is_class_teacher"):
                    gc = GradeClass.objects.filter(name=selected_class).first()
                    if gc:
                        all_subs = set(gc.subjects.filter(is_active=True).values_list("name", flat=True))
                        subjects = all_subs
                    else:
                        subjects = assigned_subjects
                else:
                    subjects = assigned_subjects
                ctx["subjects"] = sorted(subjects)
                ctx["editable_subjects"] = assigned_subjects
            else:
                from timetable.models import TimetableSlot
                from academics.models import ExamScore
                subs = set(TimetableSlot.objects.filter(class_name=selected_class).values_list("subject_name", flat=True))
                subs.update(ExamScore.objects.filter(student__class_name=selected_class).values_list("subject_name", flat=True))
                if not subs:
                    from academics.models import Subject
                    subs = set(Subject.objects.filter(department=Department.LOWER_SECONDARY).values_list("name", flat=True))
                ctx["subjects"] = sorted(list(subs))
                ctx["editable_subjects"] = set(subs)

        ctx["academics_tab"] = "lower_secondary_assessment"

        if selected_class and ctx.get("current_term"):
            from academics.models import ReportCard, ReportCardStatus, ExamScore, ScoreStatus
            total_students = Student.objects.filter(class_name=selected_class, is_archived=False).count()
            submitted_count = ReportCard.objects.filter(
                student__class_name=selected_class,
                term=ctx["current_term"],
                status=ReportCardStatus.PENDING_SIGN_OFF,
            ).count()
            has_returned = ExamScore.objects.filter(
                student__class_name=selected_class,
                term=ctx["current_term"],
                status=ScoreStatus.RETURNED,
            ).exists()
            returned_student_ids = list(
                ExamScore.objects.filter(
                    student__class_name=selected_class,
                    term=ctx["current_term"],
                    status=ScoreStatus.RETURNED,
                ).values_list("student_id", flat=True).distinct()
            )
            ctx["class_submitted"] = total_students > 0 and submitted_count >= total_students and not has_returned
            ctx["returned_count"] = len(returned_student_ids)
            ctx["returned_student_ids"] = returned_student_ids

            has_unsubmitted = ExamScore.objects.filter(
                student__class_name=selected_class,
                term=ctx["current_term"],
                status__in=[ScoreStatus.DRAFT, ScoreStatus.SUBMITTED],
            ).exists()
            if has_unsubmitted:
                ctx["class_submitted"] = False

        return ctx


class LowerSecondaryStudentsAPIView(RoleRequiredMixin, View):
    """API to get student list for Lower Secondary focused entry."""
    allowed_roles = [UserRole.TEACHER, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "students.view_student"

    def get(self, request):
        class_name = request.GET.get("class_name")
        if not class_name:
            return JsonResponse({"error": "Class name required"}, status=400)

        if request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            if class_name not in get_teacher_assigned_classes_from_tca(request.user):
                return JsonResponse({"error": "Access denied"}, status=403)

        students = Student.objects.filter(class_name=class_name, is_archived=False).order_by("last_name", "first_name")
        term_id = request.GET.get("term")
        student_ids = list(students.values_list("id", flat=True))
        scored_ids = set()
        submitted_ids = set()
        if term_id and student_ids:
            from academics.models import ExamScore
            scored_ids = set(
                ExamScore.objects.filter(student_id__in=student_ids, term_id=term_id)
                .values_list("student_id", flat=True).distinct()
            )
            from academics.models import ReportCard, ReportCardStatus
            submitted_ids = set(
                ReportCard.objects.filter(
                    student_id__in=student_ids, term_id=term_id,
                    status=ReportCardStatus.PENDING_SIGN_OFF,
                ).values_list("student_id", flat=True).distinct()
            )
        rc_map = {}
        if term_id and student_ids:
            for rc in ReportCard.objects.filter(student_id__in=student_ids, term_id=term_id):
                rc_map[rc.student_id] = rc.status

        score_status_map = {}
        if term_id and student_ids:
            from academics.models import ScoreStatus
            for sid in student_ids:
                statuses = set(
                    ExamScore.objects.filter(student_id=sid, term_id=term_id)
                    .values_list("status", flat=True)
                )
                if not statuses:
                    score_status_map[sid] = "draft"
                elif ScoreStatus.RETURNED in statuses:
                    score_status_map[sid] = "returned"
                elif ScoreStatus.APPROVED in statuses and ScoreStatus.DRAFT not in statuses and ScoreStatus.SUBMITTED not in statuses:
                    score_status_map[sid] = "approved"
                elif ScoreStatus.SUBMITTED in statuses:
                    score_status_map[sid] = "submitted"
                elif ScoreStatus.DRAFT in statuses:
                    score_status_map[sid] = "draft"

        data = [
            {
                "id": s.id,
                "first_name": s.first_name,
                "last_name": s.last_name,
                "admission_no": s.admission_no,
                "has_scores": s.id in scored_ids,
                "report_card_status": rc_map.get(s.id, ReportCardStatus.DRAFT),
                "score_status": score_status_map.get(s.id, ""),
            }
            for s in students
        ]
        return JsonResponse({"students": data})


class LowerSecondaryScoreAPIView(RoleRequiredMixin, View):
    """API for getting/saving all Lower Secondary scores for a student in a term."""
    allowed_roles = [UserRole.TEACHER, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.change_examscore"

    def _student_in_teacher_classes(self, request, student) -> bool:
        if request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            return student.class_name in get_teacher_assigned_classes_from_tca(request.user)
        if request.user.role in [UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]:
            return True
        return False

    def get(self, request, student_id):
        if request.user.role not in [UserRole.TEACHER, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]:
            return JsonResponse({"error": "Permission denied"}, status=403)

        term_id = request.GET.get("term")
        if not term_id:
            return JsonResponse({"error": "Missing term param"}, status=400)

        student = get_object_or_404(Student, id=student_id)
        if not self._student_in_teacher_classes(request, student):
            return JsonResponse({"error": "Access denied"}, status=403)

        scores = ExamScore.objects.filter(student_id=student_id, term_id=term_id)
        rc = ReportCard.objects.filter(student_id=student_id, term_id=term_id).first()
        rc_status = rc.status if rc else ReportCardStatus.DRAFT

        score_data = {}
        for s in scores:
            if s.subject_name not in score_data:
                score_data[s.subject_name] = {}
            score_data[s.subject_name][s.exam_type] = {
                "score": float(s.score),
                "is_locked": s.is_locked,
                "status": s.status,
                "correction_reason": s.correction_reason or "",
                "hod_feedback": s.hod_feedback or "",
            }

        return JsonResponse({"scores": score_data, "report_card_status": rc_status})

    def post(self, request, student_id):
        if request.user.role not in [UserRole.TEACHER, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]:
            return JsonResponse({"error": "Permission denied"}, status=403)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        term_id = data.get("term")
        all_subject_scores = data.get("scores", {})

        if not term_id:
            return JsonResponse({"error": "Missing term_id"}, status=400)

        rc = ReportCard.objects.filter(student_id=student_id, term_id=term_id).first()
        if rc and rc.status != ReportCardStatus.DRAFT:
            from academics.models import ScoreStatus as _ScoreStatus
            submitted_codes = set(
                ExamScore.objects.filter(
                    student_id=student_id, term_id=term_id,
                    status__in=[_ScoreStatus.SUBMITTED, _ScoreStatus.APPROVED]
                ).values_list("exam_type", flat=True)
            )
            codes_to_save = set()
            for _subj, _codes in all_subject_scores.items():
                if isinstance(_codes, dict):
                    codes_to_save.update(_codes.keys())
            if codes_to_save and codes_to_save.issubset(submitted_codes):
                return JsonResponse({"error": "Evaluation is locked (Submitted to HOD)"}, status=403)

        student = get_object_or_404(Student, id=student_id)
        if not self._student_in_teacher_classes(request, student):
            return JsonResponse({"error": "Access denied"}, status=403)
        term = get_object_or_404(Term, id=term_id)

        if term.is_locked:
            return JsonResponse({"error": "This term is locked and cannot accept score entry."}, status=403)

        from academics.utils import check_score_entry_allowed
        from django.utils import timezone as _tz
        today = _tz.now().date()

        skipped_subjects = set()
        soft_warnings = []

        if request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            tca = get_teacher_assigned_classes_from_tca(request.user)
            class_info = tca.get(student.class_name, {})
            allowed_subjects = class_info.get("subjects", set())
            before_keys = set(all_subject_scores.keys())
            all_subject_scores = {k: v for k, v in all_subject_scores.items() if k in allowed_subjects}
            skipped_subjects = before_keys - set(all_subject_scores.keys())
            if before_keys and not all_subject_scores:
                return JsonResponse({"error": "You are not assigned to teach any subjects in this class."}, status=403)

        from academics.models import ExamTypeConfiguration, ScoreStatus
        active_configs = {c.code: c for c in ExamTypeConfiguration.objects.filter(is_active=True)}

        updated = 0
        for subject_name, subjects_scores in all_subject_scores.items():
            for code, val in subjects_scores.items():
                if code not in active_configs:
                    continue
                if val == "" or val is None:
                    continue

                allowed, msg = check_score_entry_allowed(term, code, today)
                if not allowed:
                    existing_returned = ExamScore.objects.filter(
                        student=student, term=term, subject_name=subject_name,
                        exam_type=code, status=ScoreStatus.RETURNED
                    ).exists()
                    if not existing_returned:
                        soft_warnings.append(f"{subject_name}/{code}: {msg}")
                        continue

                try:
                    score_val = Decimal(str(val))
                    max_score_val = int(active_configs[code].max_score)
                    if score_val < 0 or score_val > max_score_val:
                        return JsonResponse({"error": f"Score {score_val} is out of range (0-{max_score_val})"}, status=400)
                    if score_val != score_val.to_integral_value():
                        return JsonResponse({"error": f"Score {score_val} must be a whole number"}, status=400)
                except (ValueError, DecimalException, KeyError):
                    continue

                existing = ExamScore.objects.filter(
                    student=student, term=term, subject_name=subject_name, exam_type=code
                ).first()
                if existing and existing.is_locked and existing.status != "returned":
                    continue

                score_obj, created = ExamScore.objects.get_or_create(
                    student=student, term=term, subject_name=subject_name, exam_type=code,
                    defaults={
                        "score": score_val,
                        "max_score": active_configs[code].max_score,
                        "entered_by": request.user,
                        "exam_type_config": active_configs[code],
                    }
                )
                if not created:
                    was_returned = score_obj.status == "returned"
                    score_obj.score = score_val
                    score_obj.max_score = active_configs[code].max_score
                    score_obj.entered_by = request.user
                    score_obj.exam_type_config = active_configs[code]
                    if was_returned:
                        score_obj.status = ScoreStatus.DRAFT
                try:
                    score_obj.full_clean()
                except ValidationError as e:
                    if created:
                        score_obj.delete()
                    return JsonResponse({"error": str(e)}, status=400)
                if not created:
                    save_fields = ["score", "max_score", "entered_by", "exam_type_config", "updated_at"]
                    if was_returned:
                        save_fields.append("status")
                    score_obj.save(update_fields=save_fields)
                else:
                    score_obj.save(update_fields=["updated_at"])
                updated += 1

                from audit.models import log_event
                if created:
                    log_event(
                        actor=request.user, action_type="EXAM_SCORE_ENTERED",
                        model_name="ExamScore", object_id=score_obj.pk,
                        description=f"Score {score_val} entered for {student.admission_no} in {subject_name} ({code})",
                        request=request,
                    )
                elif existing and existing.score != score_val:
                    score_obj.previous_score = existing.score
                    score_obj.corrected_by = request.user
                    score_obj.correction_reason = "Corrected via lower secondary assessment"
                    score_obj.save(update_fields=["corrected_by_id", "correction_reason", "previous_score", "updated_at"])
                    log_event(
                        actor=request.user, action_type="EXAM_SCORE_CORRECTED",
                        model_name="ExamScore", object_id=score_obj.pk,
                        description=f"Score corrected from {float(existing.score)} to {float(score_val)} for {student.admission_no} in {subject_name} ({code})",
                        before_value=str(float(existing.score)), after_value=str(float(score_val)),
                        request=request,
                    )
                    from academics.services import recalculate_report_card_average
                    rc = ReportCard.objects.filter(student=student, term=term).first()
                    if rc:
                        recalculate_report_card_average(rc)

        response_data = {"success": True, "updated": updated}
        if skipped_subjects:
            response_data["warning"] = f"Subjects not in your assignment were skipped: {', '.join(sorted(skipped_subjects))}"
        if soft_warnings:
            response_data["window_warnings"] = soft_warnings
        return JsonResponse(response_data)


class LowerSecondaryBulkSubmissionAPIView(RoleRequiredMixin, View):
    """API for submitting an entire Lower Secondary class to HOD for review."""
    allowed_roles = [UserRole.TEACHER, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.change_reportcard"

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        class_name = data.get("class_name")
        term_id = data.get("term")

        if not (class_name and term_id):
            return JsonResponse({"error": "Missing parameters"}, status=400)

        if request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            if class_name not in get_teacher_assigned_classes_from_tca(request.user):
                return JsonResponse({"error": "Access denied"}, status=403)

        term = get_object_or_404(Term, id=term_id)

        if term.is_locked:
            return JsonResponse({"error": "This term is locked and cannot accept score entry."}, status=403)

        students = Student.objects.filter(class_name=class_name, is_archived=False)

        from academics.utils import check_score_entry_allowed
        from academics.models import ExamScore, ScoreStatus
        from django.utils import timezone as _tz
        today = _tz.now().date()
        submitted_types = (
            ExamScore.objects
            .filter(student__class_name=class_name, term=term)
            .exclude(exam_type__in=["quiz", "QZ"])
            .values_list("exam_type", flat=True).distinct()
        )
        for et in submitted_types:
            allowed, msg = check_score_entry_allowed(term, et, today)
            if not allowed:
                has_returned = ExamScore.objects.filter(
                    student__class_name=class_name, term=term, exam_type=et,
                    status=ScoreStatus.RETURNED
                ).exists()
                if not has_returned:
                    return JsonResponse({"error": f"Cannot submit: {msg}"}, status=400)

        from django.db import transaction
        with transaction.atomic():
            for student in students:
                rc, created = ReportCard.objects.get_or_create(
                    student=student, term=term,
                    defaults={
                        "status": ReportCardStatus.PENDING_SIGN_OFF,
                        "updated_at": timezone.now(),
                        "generated_by": request.user,
                    }
                )
                if not created:
                    rc.status = ReportCardStatus.PENDING_SIGN_OFF
                    rc.updated_at = timezone.now()
                    rc.save(update_fields=["status", "updated_at"])
                from academics.models import ExamScore, ScoreStatus
                from audit.models import log_event
                score_qs = ExamScore.objects.filter(
                    student=student, term=term, status__in=[ScoreStatus.DRAFT, ScoreStatus.RETURNED]
                )
                if request.user.role == UserRole.TEACHER:
                    from core.teacher_context import get_teacher_assigned_classes_from_tca
                    tca = get_teacher_assigned_classes_from_tca(request.user)
                    class_info = tca.get(class_name, {})
                    allowed_subjects = class_info.get("subjects", set())
                    if allowed_subjects:
                        score_qs = score_qs.filter(subject_name__in=allowed_subjects)
                score_ids = list(score_qs.values_list("id", flat=True))
                score_qs.update(
                    status=ScoreStatus.SUBMITTED, is_locked=True, updated_at=timezone.now()
                )
                for sid in score_ids:
                    log_event(
                        actor=request.user, action_type="EXAM_SCORE_SUBMITTED",
                        model_name="ExamScore", object_id=sid,
                        description=f"Score submitted for {student.admission_no} via lower secondary assessment — {class_name}",
                        request=request,
                    )

        from communications.email_service import dispatch_notification
        from users.models import User
        hod_roles = [UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL]
        hods = User.objects.filter(role__in=hod_roles, is_active=True)
        for hod in hods:
            dispatch_notification(
                user=hod,
                title="Lower Secondary assessment scores submitted for review",
                message=(
                    f"{request.user.get_full_name()} has submitted all scores for {class_name} "
                    f"({term.name}) for HOD review."
                ),
                link="/academics/exam-score-approval-queue/",
                actor=request.user,
            )

        return JsonResponse({
            "success": True,
            "message": f"Successfully submitted all {students.count()} reports for {class_name} for HOD review."
        })
