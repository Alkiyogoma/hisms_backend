import logging
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

logger = logging.getLogger(__name__)
from academics.grading_utils import (
    compute_grade_with_gaps, get_grade_from_score, get_grade_label, get_full_grade_display,
    is_pass, is_at_risk, is_critical
)


class ExamScoreEntryView(RoleRequiredMixin, TemplateView):
    template_name = "academics/exam_scores.html"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.change_examscore"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        # GRD-001/002: Only teachers, HODs, HOS, and Super Admin can access exam scores
        if not request.user.has_role(UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN):
            raise PermissionDenied()
        if request.user.role == UserRole.TEACHER:
            # ECD teachers -> ECD evaluations
            if is_ecd_teacher(request.user):
                return redirect("academics:ecd_evaluations_entry")
            # Primary / Lower Secondary teachers -> department-specific assessment
            from timetable.models import TimetableSlot
            primary_names = grade_class_names_for_department(Department.PRIMARY)
            ls_names = grade_class_names_for_department(Department.LOWER_SECONDARY)
            teacher_classes = list(set(TimetableSlot.objects.filter(teacher=request.user).values_list("class_name", flat=True)))
            has_primary = any(c in primary_names for c in teacher_classes)
            has_ls = any(c in ls_names for c in teacher_classes)
            if has_primary and not has_ls:
                return redirect("academics:primary_assessment")
            elif has_ls and not has_primary:
                return redirect("academics:lower_secondary_assessment")
            elif has_primary and has_ls:
                # Teacher spans both departments — send to primary by default;
                # they can navigate to LS via the sidebar or direct URL.
                return redirect("academics:primary_assessment")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = ExamScoreFilterForm(self.request.GET or None)
        students = []
        scores_by_student = {}

        if form.is_valid():
            term = form.cleaned_data["term"]
            class_name = form.cleaned_data["class_name"].strip()
            subject_name = form.cleaned_data["subject_name"].strip()
            exam_type = form.cleaned_data["exam_type"]

            students_qs = Student.objects.filter(is_archived=False, class_name=class_name)
            
            # FR-ACAD-011: HOD Scoping
            from core.scoping import hod_class_names
            section_classes = hod_class_names(self.request.user)
            if section_classes is not None:
                students_qs = students_qs.filter(class_name__in=section_classes)

            # FR-ACAD-001: Teacher scoping on GET — restrict to assigned class+subject
            if self.request.user.role == UserRole.TEACHER:
                tca = get_teacher_assigned_classes_from_tca(self.request.user)
                class_info = tca.get(class_name, {})
                allowed_subjects = class_info.get("subjects", set())
                if not allowed_subjects or subject_name not in allowed_subjects:
                    messages.warning(self.request, f"You are not assigned to teach {subject_name} in {class_name}.")
                    students = []
                    scores_by_student = {}
                    ctx["filter_form"] = form
                    ctx["students"] = students
                    ctx["scores_by_student"] = scores_by_student
                    ctx["academics_tab"] = "exam_scores"
                    return ctx

            students = students_qs.order_by("last_name", "first_name")
            scores = ExamScore.objects.filter(
                term=term,
                subject_name=subject_name,
                exam_type=exam_type,
                student__in=students
            )
            scores_by_student = {s.student_id: s for s in scores}

        ctx["filter_form"] = form
        ctx["students"] = students
        ctx["scores_by_student"] = scores_by_student
        ctx["academics_tab"] = "exam_scores"
        return ctx

    def post(self, request, *args, **kwargs):
        if not request.user.has_role(UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD):
            raise PermissionDenied()
        
        form = ExamScoreFilterForm(request.POST)
        if not form.is_valid():
            missing = ", ".join(
                (form.fields[name].label or name.replace("_", " ").title())
                for name in form.errors if name in form.fields
            )
            messages.error(
                request,
                f"Scores were not saved: {missing or 'the selection'} is missing or invalid. "
                "Choose the term, class, subject and exam type again.",
            )
            ctx = self.get_context_data(**kwargs)
            ctx["filter_form"] = form
            return self.render_to_response(ctx)

        term = form.cleaned_data["term"]
        class_name = form.cleaned_data["class_name"].strip()
        subject_name = form.cleaned_data["subject_name"].strip()
        exam_type = form.cleaned_data["exam_type"]

        def refuse(message):
            """Explain why nothing was saved, keeping the filter and every typed score."""
            messages.error(request, f"Scores were not saved: {message}")
            students = Student.objects.filter(is_archived=False, class_name=class_name).order_by("last_name", "first_name")
            ctx = self.get_context_data(**kwargs)
            ctx["filter_form"] = form
            ctx["students"] = students
            ctx["scores_by_student"] = {
                sc.student_id: sc for sc in ExamScore.objects.filter(
                    term=term, subject_name=subject_name, exam_type=exam_type, student__in=students,
                )
            }
            ctx["score_errors"] = {}
            ctx["submitted_scores"] = {
                str(st.id): request.POST.get(f"score_{st.id}", "").strip()
                for st in students if request.POST.get(f"score_{st.id}", "").strip()
            }
            return self.render_to_response(ctx)

        if term.is_locked:
            return refuse("this term is locked and cannot accept score entry.")

        from academics.utils import check_score_entry_allowed
        allowed, msg = check_score_entry_allowed(term, exam_type)
        if not allowed:
            return refuse(msg or "score entry is not allowed for this exam type at this time.")

        # FR-ACAD-001: Teacher must be assigned to this class+subject before entering scores
        if request.user.role == UserRole.TEACHER:
            tca = get_teacher_assigned_classes_from_tca(request.user)
            class_info = tca.get(class_name, {})
            allowed_subjects = class_info.get("subjects", set())
            if not allowed_subjects or subject_name not in allowed_subjects:
                return refuse(f"you are not assigned to teach {subject_name} in {class_name}.")

        students = Student.objects.filter(is_archived=False, class_name=class_name).order_by("last_name", "first_name")
        scores = ExamScore.objects.filter(
            term=term,
            subject_name=subject_name,
            exam_type=exam_type,
            student__in=students
        )
        existing_scores = {s.student_id: s for s in scores}

        # Resolve max_score from ExamTypeConfiguration for accurate validation
        from academics.models import ExamTypeConfiguration
        try:
            exam_config = ExamTypeConfiguration.objects.get(code=exam_type, is_active=True)
            max_score = exam_config.max_score
        except ExamTypeConfiguration.DoesNotExist:
            max_score = 100  # fallback

        updates_made = 0
        # {student_id: error_message} — collected to display inline
        score_errors = {}
        submitted_scores = {}  # {student_id: raw_value} — preserve typed values

        from decimal import Decimal, InvalidOperation

        for student in students:
            raw = request.POST.get(f"score_{student.id}")
            if raw is None or raw.strip() == "":
                continue

            raw = raw.strip()
            submitted_scores[student.id] = raw

            try:
                score_val = Decimal(raw)
                if not score_val.is_finite():
                    raise InvalidOperation
            except InvalidOperation:
                score_errors[student.id] = f"'{raw}' is not a number. Enter a whole number from 0 to {max_score}."
                continue

            # FR-ACAD-001: explicit 0–max_score range check
            if score_val < 0 or score_val > max_score:
                score_errors[student.id] = f"{raw} is out of range. Enter a whole number from 0 to {max_score}."
                continue
            # Same rule as the Primary / Lower Secondary entry pages.
            if score_val != score_val.to_integral_value():
                score_errors[student.id] = f"{raw} must be a whole number (0 to {max_score})."
                continue

            existing = existing_scores.get(student.id)
            if existing:
                if existing.is_locked:
                    score_errors[student.id] = "This score is locked and cannot be edited."
                    continue
                if existing.score != score_val:
                    old_score = existing.score
                    existing.score = score_val
                    try:
                        existing.full_clean()
                    except ValidationError as exc:
                        existing.score = old_score
                        score_errors[student.id] = " ".join(exc.messages)
                        continue
                    existing.save(update_fields=["score", "updated_at"])
                    updates_made += 1

                    from audit.models import log_event
                    log_event(
                        actor=request.user,
                        action_type="EXAM_SCORE_UPDATED",
                        model_name="ExamScore",
                        object_id=existing.pk,
                        description=f"Score updated from {old_score} to {score_val} for {student.admission_no} in {subject_name} ({exam_type})",
                        before_value=str(old_score),
                        after_value=str(score_val),
                        request=request
                    )
            else:
                new_score = ExamScore(
                    student=student,
                    term=term,
                    subject_name=subject_name,
                    exam_type=exam_type,
                    score=score_val,
                    entered_by=request.user
                )
                try:
                    new_score.full_clean()
                except ValidationError as exc:
                    score_errors[student.id] = " ".join(exc.messages)
                    continue
                new_score.save()
                updates_made += 1
                
                # Log event
                from audit.models import log_event
                log_event(
                    actor=request.user,
                    action_type="EXAM_SCORE_ENTERED",
                    model_name="ExamScore",
                    object_id=new_score.pk,
                    description=f"Score {score_val} entered for {student.admission_no} in {subject_name} ({exam_type})",
                    request=request
                )

        if score_errors:
            # Re-fetch updated scores so the table shows freshly saved values
            updated_scores = ExamScore.objects.filter(
                term=term, subject_name=subject_name, exam_type=exam_type,
                student__in=students
            )
            scores_by_student = {s.student_id: s for s in updated_scores}
            ctx = self.get_context_data(**kwargs)
            ctx["filter_form"] = form
            ctx["students"] = students
            ctx["scores_by_student"] = scores_by_student
            ctx["score_errors"] = {str(k): v for k, v in score_errors.items()}
            ctx["submitted_scores"] = {str(k): v for k, v in submitted_scores.items()}
            ctx["exam_max_score"] = int(max_score)
            names = {st.id: f"{st.first_name} {st.last_name}" for st in students if st.id in score_errors}
            problem_list = "; ".join(f"{names.get(sid, sid)}: {err}" for sid, err in score_errors.items())
            if updates_made > 0:
                messages.warning(
                    request,
                    f"Saved {updates_made} score(s). These were not saved — {problem_list}"
                )
            else:
                messages.error(
                    request,
                    f"No scores were saved. Fix the highlighted rows — {problem_list}"
                )
            return self.render_to_response(ctx)

        # ── HOD notification: grades ready for review ────────────────────────
        if updates_made > 0:
            try:
                from communications.email_service import dispatch_notification
                from users.models import User
                hod_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL]
                hods = User.objects.filter(role__in=hod_roles, is_active=True)
                for hod in hods:
                    dispatch_notification(
                        user=hod,
                        title="Exam scores submitted for review",
                        message=(
                            f"{request.user.get_full_name()} has submitted exam scores for "
                            f"{class_name} — {subject_name} ({exam_type}) in {term.name}. "
                            f"{updates_made} score(s) saved. Please review and approve."
                        ),
                        link="/academics/exam-scores/",
                        actor=request.user,
                    )
            except Exception:
                pass  # Never block score saving on notification failure

        # ── FR-ACAD-006: Handle Submit for Review ──────────────────────
        if request.POST.get("action") == "submit_for_review" and updates_made >= 0:
            return self._handle_submit_for_review(request, term, class_name, subject_name, exam_type, students)

        messages.success(request, f"Saved scores for {updates_made} students.")
        
        # redirect back with clean GET params to preserve filter
        import urllib.parse
        filter_params = {
            "term": term.id,
            "class_name": class_name,
            "subject_name": subject_name,
            "exam_type": exam_type,
        }
        query_string = urllib.parse.urlencode(filter_params)
        return redirect(f"{request.path}?{query_string}")

    def _handle_submit_for_review(self, request, term, class_name, subject_name, exam_type, students):
        """
        FR-ACAD-006 / GRD-004: Submit all DRAFT scores for this filter to HOD for review.
        Changes status from DRAFT → SUBMITTED and locks scores so teacher cannot edit.
        Notifies the relevant HOD(s) that scores are ready for review.
        """
        if term.is_locked:
            messages.error(request, "This term is locked and cannot accept score entry.")
            return

        from academics.models import ScoreStatus

        scores = ExamScore.objects.filter(
            term=term,
            subject_name=subject_name,
            exam_type=exam_type,
            student__in=students,
            status=ScoreStatus.DRAFT,
        )

        submitted_count = 0
        for score in scores:
            score.status = ScoreStatus.SUBMITTED
            score.is_locked = True
            score.save(update_fields=["status", "is_locked", "updated_at"])
            submitted_count += 1

            # Report moves to pending only once every subject is submitted.
            from academics.score_progress import sync_report_status
            sync_report_status(score.student, term)

            from audit.models import log_event
            log_event(
                actor=request.user,
                action_type="EXAM_SCORE_SUBMITTED",
                model_name="ExamScore",
                object_id=score.pk,
                description=f"Score {score.score} submitted for review — {score.student.admission_no} in {subject_name} ({exam_type})",
                request=request,
            )

        if submitted_count == 0:
            # Check if all were already submitted
            already_submitted = ExamScore.objects.filter(
                term=term, subject_name=subject_name, exam_type=exam_type,
                student__in=students, status=ScoreStatus.SUBMITTED
            ).count()
            if already_submitted > 0:
                messages.info(request, "All scores for this filter are already submitted for review.")
            else:
                messages.warning(request, "No draft scores found to submit. Save scores first, then submit.")
        else:
            # Notify HODs
            try:
                from communications.email_service import dispatch_notification
                from users.models import User
                hod_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL]
                hods = User.objects.filter(role__in=hod_roles, is_active=True)
                for hod in hods:
                    dispatch_notification(
                        user=hod,
                        title="Exam scores submitted for HOD review",
                        message=(
                            f"{request.user.get_full_name()} has submitted {submitted_count} exam score(s) "
                            f"for {class_name} — {subject_name} ({exam_type}) in {term.name}. "
                            f"Please review and approve."
                        ),
                        link="/academics/exam-score-approval-queue/",
                        actor=request.user,
                    )
            except Exception:
                logger.exception("Failed to dispatch HOD approval notification for exam scores")

            messages.success(
                request,
                f"{submitted_count} score(s) submitted for HOD review. Scores are now locked."
            )

        import urllib.parse
        filter_params = {
            "term": term.id,
            "class_name": class_name,
            "subject_name": subject_name,
            "exam_type": exam_type,
        }
        query_string = urllib.parse.urlencode(filter_params)
        return redirect(f"{request.path}?{query_string}")


class ExamScoreCorrectionView(RoleRequiredMixin, View):
    """Change a grade after HOD approval (Head of School / Super Admin only).
    A reason is mandatory; each change is kept as an ExamScoreAmendment."""
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    # The role check below is the real gate (Head of School holds view only).
    required_permission = "academics.view_examscore"

    def post(self, request, pk):
        from academics.approval_policy import can_amend_approved_grades
        from academics.services import amend_approved_score

        if not can_amend_approved_grades(request.user):
            return JsonResponse(
                {"error": "Only the Head of School or Super Admin can change an approved grade."}, status=403
            )
        score = get_object_or_404(ExamScore, pk=pk)
        try:
            data = json.loads(request.body) if request.content_type == "application/json" else request.POST
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        try:
            amendment = amend_approved_score(
                score, data.get("score", ""), data.get("reason", ""), request.user, request=request,
            )
        except ValidationError as e:
            return JsonResponse({"error": " ".join(e.messages)}, status=400)
        return JsonResponse({"ok": True, "amendment": amendment_payload(amendment)})


def amendment_payload(a):
    """JSON shape of one approved-grade change, shown as a note on the score."""
    by = a.changed_by
    return {
        "previous_score": float(a.previous_score),
        "new_score": float(a.new_score),
        "reason": a.reason,
        "changed_by": by.get_full_name() or by.username,
        "changed_at": timezone.localtime(a.created_at).strftime("%d %b %Y %H:%M"),
    }


class ExamScoreApprovalQueueView(RoleRequiredMixin, TemplateView):
    """FR-ACAD-006: Dedicated HOD approval queue for submitted exam scores.
    Shows scores with status=SUBMITTED, allows approve/return/bulk actions.

    Only HODs, the Head of School and Super Admin may approve or return.
    Teachers get a read-only view of the status of their own subjects' scores.
    """
    template_name = "academics/exam_score_approval_queue.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.TEACHER]
    required_permission = "academics.view_examscore"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from academics.models import ScoreStatus
        from academics.approval_policy import (
            approval_block_reason, approver_class_names, can_approve_grades, taught_class_names,
        )

        term = get_current_term()
        ctx["current_term"] = term
        user = self.request.user
        can_approve = can_approve_grades(user)
        ctx["can_approve"] = can_approve

        # Filters
        selected_class = self.request.GET.get("class_name", "").strip()
        selected_subject = self.request.GET.get("subject_name", "").strip()
        ctx["selected_class"] = selected_class
        ctx["selected_subject"] = selected_subject

        if can_approve:
            # Base queryset: submitted scores for review
            qs = ExamScore.objects.filter(
                status=ScoreStatus.SUBMITTED
            ).select_related("student", "entered_by")
            # FR-ACAD-011: HOD Scoping
            scope = approver_class_names(user)
            if scope is not None:
                qs = qs.filter(student__class_name__in=scope)
        else:
            # Teachers: read-only sign-off status of their own subjects' scores.
            qs = ExamScore.objects.filter(
                status__in=[ScoreStatus.SUBMITTED, ScoreStatus.APPROVED, ScoreStatus.RETURNED]
            ).select_related("student", "entered_by")
            from django.db.models import Q
            own = Q(entered_by=user)
            for class_name, info in get_teacher_assigned_classes_from_tca(user).items():
                if info["subjects"]:
                    own |= Q(student__class_name=class_name, subject_name__in=info["subjects"])
            qs = qs.filter(own)

        if term:
            qs = qs.filter(term=term)

        if selected_class:
            qs = qs.filter(student__class_name=selected_class)
        if selected_subject:
            qs = qs.filter(subject_name=selected_subject)

        # Group by class + subject for the queue
        from django.db.models import Count
        queue_items = (
            qs.values("student__class_name", "subject_name", "exam_type")
            .annotate(score_count=Count("id"))
            .order_by("student__class_name", "subject_name", "exam_type")
        )

        # Build detailed entries per group
        taught = taught_class_names(user, term) if can_approve else set()
        queue_details = []
        for item in queue_items:
            scores = list(qs.filter(
                student__class_name=item["student__class_name"],
                subject_name=item["subject_name"],
                exam_type=item["exam_type"],
            ).order_by("student__last_name", "student__first_name"))
            block_reason = ""
            if can_approve:
                for sc in scores:
                    sc.block_reason = approval_block_reason(user, sc, taught)
                reasons = {sc.block_reason for sc in scores}
                if all(reasons):
                    block_reason = scores[0].block_reason if len(reasons) == 1 else "You cannot approve these scores."
            queue_details.append({
                "class_name": item["student__class_name"],
                "subject_name": item["subject_name"],
                "exam_type": item["exam_type"],
                "score_count": item["score_count"],
                "scores": scores,
                "block_reason": block_reason,
            })

        ctx["queue_details"] = queue_details
        ctx["total_pending"] = qs.filter(status=ScoreStatus.SUBMITTED).count()

        # Group queue_details by exam_type for tab panels
        from collections import OrderedDict
        by_exam = OrderedDict()
        for item in queue_details:
            et = item["exam_type"]
            if et not in by_exam:
                by_exam[et] = []
            by_exam[et].append(item)
        ctx["queue_by_exam_type"] = by_exam

        # Exam type summary for metric cards
        exam_type_summary = (
            qs.values("exam_type")
            .annotate(score_count=Count("id"))
            .order_by("exam_type")
        )
        ctx["exam_types_count"] = exam_type_summary.count()
        ctx["subjects_count"] = qs.values("subject_name").distinct().count()

        # Available filter options
        ctx["available_classes"] = (
            qs.values_list("student__class_name", flat=True).distinct().order_by("student__class_name")
        )
        ctx["available_subjects"] = (
            qs.values_list("subject_name", flat=True).distinct().order_by("subject_name")
        )

        ctx["academics_tab"] = "approval_queue"

        return ctx

    def post(self, request, *args, **kwargs):
        from academics.models import ScoreStatus
        from academics.approval_policy import (
            approval_block_reason, approver_class_names, can_approve_grades, taught_class_names,
        )
        from communications.email_service import dispatch_notification
        from audit.models import log_event

        if not can_approve_grades(request.user):
            raise PermissionDenied("Only a Head of Department or the Head of School can approve grades.")

        action = request.POST.get("action", "")
        score_ids = request.POST.getlist("score_ids")
        reason = request.POST.get("reason", "").strip()

        if not score_ids:
            messages.error(request, "No scores selected.")
            return redirect("academics:exam_score_approval_queue")

        scores = ExamScore.objects.filter(
            pk__in=score_ids, status=ScoreStatus.SUBMITTED
        ).select_related("student", "term")
        scope = approver_class_names(request.user)
        if scope is not None:
            scores = scores.filter(student__class_name__in=scope)
        updated = 0
        blocked = {}
        taught_by_term = {}

        for score in scores:
            if score.term_id not in taught_by_term:
                taught_by_term[score.term_id] = taught_class_names(request.user, score.term)
            # Nobody approves or returns grades for a class they teach or scores they entered.
            reason_blocked = approval_block_reason(request.user, score, taught_by_term[score.term_id])
            if reason_blocked:
                blocked[reason_blocked] = blocked.get(reason_blocked, 0) + 1
                continue
            if action == "approve":
                score.status = ScoreStatus.APPROVED
                score.approved_by = request.user
                score.approved_at = timezone.now()
                score.save(update_fields=["status", "approved_by_id", "approved_at", "updated_at"])

                # Notify the teacher who entered the score
                if score.entered_by:
                    dispatch_notification(
                        user=score.entered_by,
                        title="Exam scores approved",
                        message=(
                            f"Your {score.subject_name} ({score.exam_type}) score for "
                            f"{score.student.first_name} {score.student.last_name} has been approved."
                        ),
                        link="/academics/exam-scores/",
                        actor=request.user,
                    )

                log_event(
                    actor=request.user,
                    action_type="EXAM_SCORE_APPROVED",
                    model_name="ExamScore",
                    object_id=score.pk,
                    description=f"Score {score.score} approved for {score.student.admission_no} in {score.subject_name}",
                    request=request,
                )
                updated += 1

                # FR-ACAD-003: Auto-recalculate weighted average when score moves to APPROVED
                from academics.services import recalculate_report_card_average
                rc = ReportCard.objects.filter(student=score.student, term=score.term).first()
                if rc:
                    recalculate_report_card_average(rc)

            elif action == "return":
                if not reason:
                    messages.error(request, "Reason is required when returning scores for correction.")
                    return redirect("academics:exam_score_approval_queue")
                score.status = ScoreStatus.RETURNED
                score.correction_reason = reason
                score.is_locked = False
                score.save(update_fields=["status", "correction_reason", "is_locked", "updated_at"])

                # A returned subject means the report is no longer complete.
                from academics.score_progress import sync_report_status
                sync_report_status(score.student, score.term)

                # Notify the teacher who entered the score
                if score.entered_by:
                    dispatch_notification(
                        user=score.entered_by,
                        title="Exam scores returned for correction",
                        message=(
                            f"Your scores for {score.student.class_name} — {score.subject_name} "
                            f"({score.exam_type}) were returned by {request.user.get_full_name()}.\n\n"
                            f"Reason: {reason}"
                        ),
                        link="/academics/exam-scores/",
                        actor=request.user,
                    )

                log_event(
                    actor=request.user,
                    action_type="EXAM_SCORE_RETURNED",
                    model_name="ExamScore",
                    object_id=score.pk,
                    description=f"Score {score.score} returned for {score.student.admission_no}: {reason[:100]}",
                    request=request,
                )
                updated += 1

        action_label = "approved" if action == "approve" else "returned"
        if updated or not blocked:
            messages.success(request, f"{updated} score(s) {action_label}.")
        for msg, count in blocked.items():
            messages.error(request, f"{count} score(s) not {action_label}: {msg}")
        return redirect("academics:exam_score_approval_queue")


class CambridgeCheckpointEntryView(RoleRequiredMixin, TemplateView):
    """FR-ACAD-005: Teacher/Admin entry form for Cambridge Checkpoint results.
    Grades 6 (Primary Checkpoint) and 7, 8, 9 (Lower Secondary Checkpoint).
    """
    template_name = "academics/cambridge_checkpoint_entry.html"
    allowed_roles = [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.change_examscore"

    # Cambridge Checkpoint subjects per grade level
    CHECKPOINT_SUBJECTS = {
        "Grade 6": ["English", "Mathematics", "Science"],
        "Grade 7": ["English", "Mathematics", "Science"],
        "Grade 8": ["English", "Mathematics", "Science"],
        "Grade 9": ["English", "Mathematics", "Science"],
    }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        term = get_current_term()
        academic_year = term.academic_year if term else None
        ctx["current_term"] = term
        ctx["academic_year"] = academic_year

        # Available classes: Grades 6-9 only
        eligible_classes = ["Grade 6", "Grade 7", "Grade 8", "Grade 9"]
        from core.scoping import hod_class_names
        section_classes = hod_class_names(user)
        if section_classes is not None:
            eligible_classes = [c for c in eligible_classes if c in section_classes]
        elif user.role == UserRole.TEACHER:
            assigned = get_teacher_assigned_classes(user)
            eligible_classes = [c for c in eligible_classes if c in assigned]

        ctx["eligible_classes"] = eligible_classes

        selected_class = self.request.GET.get("class_name", "").strip()
        ctx["selected_class"] = selected_class

        if selected_class and selected_class in eligible_classes and academic_year:
            subjects = self.CHECKPOINT_SUBJECTS.get(selected_class, ["English", "Mathematics", "Science"])

            # Per-subject teacher restriction: teachers only see subjects they teach
            if user.role == UserRole.TEACHER:
                from hr.models import TeacherClassAssignment
                tca = TeacherClassAssignment.objects.filter(
                    teacher_profile__user=user,
                    grade_class__name=selected_class,
                ).values_list("subjects_taught", flat=True)
                teacher_subjects = set()
                for subj_list in tca:
                    if subj_list:
                        teacher_subjects.update(subj_list)
                if teacher_subjects:
                    subjects = [s for s in subjects if s in teacher_subjects]

            ctx["subjects"] = subjects

            students = Student.objects.filter(
                class_name=selected_class, is_archived=False
            ).order_by("last_name", "first_name")

            # Fetch existing scores
            from academics.models import CambridgeCheckpointScore
            existing = CambridgeCheckpointScore.objects.filter(
                academic_year=academic_year, student__in=students
            )
            score_map = {}  # (student_id, subject) -> score object
            for cs in existing:
                score_map[(cs.student_id, cs.subject_name)] = cs

            students_data = []
            for s in students:
                subj_scores = {}
                for subj in subjects:
                    cs = score_map.get((s.id, subj))
                    subj_scores[subj] = cs.score if cs else None
                students_data.append({"student": s, "scores": subj_scores})

            ctx["students_data"] = students_data

        ctx["academics_tab"] = "cambridge"

        return ctx

    def post(self, request, *args, **kwargs):
        user = request.user
        term = get_current_term()
        if not term:
            messages.error(request, "No active term found.")
            return redirect("academics:cambridge_checkpoint_entry")

        academic_year = term.academic_year
        class_name = request.POST.get("class_name", "").strip()
        if class_name not in self.CHECKPOINT_SUBJECTS:
            messages.error(request, "Invalid class for Cambridge Checkpoint.")
            return redirect("academics:cambridge_checkpoint_entry")

        students = Student.objects.filter(class_name=class_name, is_archived=False)
        subjects = self.CHECKPOINT_SUBJECTS[class_name]

        # Per-subject teacher restriction: teachers can only submit scores for subjects they teach
        if user.role == UserRole.TEACHER:
            from hr.models import TeacherClassAssignment
            tca = TeacherClassAssignment.objects.filter(
                teacher_profile__user=user,
                grade_class__name=class_name,
            ).values_list("subjects_taught", flat=True)
            teacher_subjects = set()
            for subj_list in tca:
                if subj_list:
                    teacher_subjects.update(subj_list)
            if teacher_subjects:
                subjects = [s for s in subjects if s in teacher_subjects]

        updated = 0
        from decimal import Decimal, InvalidOperation

        for student in students:
            for subj in subjects:
                raw = request.POST.get(f"score_{student.id}_{subj}", "").strip()
                if not raw:
                    continue

                try:
                    score_val = Decimal(raw)
                except InvalidOperation:
                    messages.warning(request, f"Invalid score for {student.first_name} {student.last_name} in {subj}: {raw}")
                    continue

                if score_val < 1 or score_val > 6:
                    messages.warning(request, f"Score must be 1.0–6.0 for {student.first_name} {student.last_name} in {subj}.")
                    continue

                from academics.models import CambridgeCheckpointScore
                cs, created = CambridgeCheckpointScore.objects.update_or_create(
                    student=student,
                    academic_year=academic_year,
                    subject_name=subj,
                    defaults={
                        "score": score_val,
                        "entered_by": user,
                    },
                )
                updated += 1

                from audit.models import log_event
                action = "CREATED" if created else "UPDATED"
                log_event(
                    actor=user,
                    action_type=f"CHECKPOINT_SCORE_{action}",
                    model_name="CambridgeCheckpointScore",
                    object_id=cs.pk,
                    description=f"Checkpoint score {score_val} for {student.admission_no} in {subj} ({class_name})",
                    request=request,
                )

        messages.success(request, f"Saved {updated} Cambridge Checkpoint score(s) for {class_name}.")
        return redirect(f"{request.path}?class_name={class_name}")
