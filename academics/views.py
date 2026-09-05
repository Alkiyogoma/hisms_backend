from __future__ import annotations

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


def _is_hod_like(role: str) -> bool:
    return role in {
        UserRole.SUPER_ADMIN,
        UserRole.HEAD_OF_SCHOOL,
        UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD,
        UserRole.LOWER_SECONDARY_HOD,
    }


class LessonPlanListView(RoleRequiredMixin, TemplateView):
    template_name = "academics/lesson_plans.html"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD]

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        # FRD: ECD teachers submit Weekly Focus, not lesson plans
        if request.user.role == UserRole.TEACHER and is_ecd_teacher(request.user):
            messages.info(request, "ECD teachers submit a Weekly Focus instead of lesson plans.")
            return redirect("communications:weekly_focus_submit")
        return super().dispatch(request, *args, **kwargs)


    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        role = user.role
        teacher_is_ecd = role == UserRole.TEACHER and is_ecd_teacher(user)

        mine = LessonPlan.objects.filter(teacher=user).select_related("term", "reviewed_by").prefetch_related("attachments").order_by("-created_at")[:100]
        queue = LessonPlan.objects.filter(status=LessonPlanStatus.SUBMITTED).select_related("teacher", "term").prefetch_related("attachments").order_by("submitted_at", "created_at")

        if not _is_hod_like(role):
            queue = LessonPlan.objects.filter(
                teacher=user, status=LessonPlanStatus.SUBMITTED
            ).select_related("teacher", "term").prefetch_related("attachments").order_by("submitted_at", "created_at")

        # FR-ACAD-002: Detect teachers with MISSING plans (HODs/Admin only)
        missing_teachers = _get_missing_plan_teachers() if _is_hod_like(role) else []

        # Subject×class heatmap for HODs
        heatmap = _build_heatmap(role) if _is_hod_like(role) else None

        ctx["mine"] = mine
        ctx["queue"] = queue[:120]
        ctx["can_review"] = user.has_perm("academics.change_lessonplan")
        ctx["can_create"] = user.has_perm("academics.add_lessonplan") and not teacher_is_ecd
        ctx["missing_teachers"] = missing_teachers
        ctx["missing_count"] = len(missing_teachers)
        ctx["heatmap"] = heatmap
        ctx["academics_tab"] = "lesson_plans"
        return ctx


class LessonPlanContextMixin:
    """Shared context for lesson plan create / edit forms."""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user

        # Recent submissions (non-draft) for the sidebar
        ctx["recent_submissions"] = (
            LessonPlan.objects.filter(teacher=user)
            .exclude(status=LessonPlanStatus.DRAFT)
            .select_related("term")
            .order_by("-submitted_at", "-created_at")[:10]
        )

        # Class → subjects mapping from timetable (for JS dynamic filtering)
        from timetable.models import TimetableSlot
        slots = TimetableSlot.objects.filter(teacher=user).select_related("subject")
        class_subject_map: dict[str, list[str]] = {}
        DAY_LABEL = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri"}
        DAY_ORDER = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4}
        timetable_days: dict[str, dict[str, dict]] = {}
        for slot in slots:
            cn = slot.class_name.strip()
            sn = slot.subject_name.strip()
            if cn not in class_subject_map:
                class_subject_map[cn] = []
            if sn and sn not in class_subject_map[cn]:
                class_subject_map[cn].append(sn)

            # Build timetable_days data for subject pill display
            if cn not in timetable_days:
                timetable_days[cn] = {}
            if sn not in timetable_days[cn]:
                subj = slot.subject
                timetable_days[cn][sn] = {
                    "code": subj.code if (subj and subj.code) else sn[:4].upper(),
                    "color": subj.color if (subj and subj.color) else "#023AA5",
                    "days": [],
                }
            day_label = DAY_LABEL.get(slot.day_of_week, slot.day_of_week[:3].title())
            if day_label not in timetable_days[cn][sn]["days"]:
                timetable_days[cn][sn]["days"].append(day_label)

        # Sort days in each subject in Mon-Fri order
        for cn in timetable_days:
            for sn in timetable_days[cn]:
                days = timetable_days[cn][sn]["days"]
                days.sort(key=lambda d: DAY_ORDER.get(d.lower()[:3], 99))

        # For admin roles, show all classes → all subjects (no day info)
        if user.role != UserRole.TEACHER:
            all_subjects = list(
                Subject.objects.filter(is_active=True).values_list("name", flat=True)
            )
            all_classes = list(GradeClass.objects.values_list("name", flat=True))
            class_subject_map = {cn: all_subjects for cn in all_classes}
            # Admin has no personal timetable, so leave timetable_days empty

        ctx["class_subject_map_json"] = json.dumps(class_subject_map)
        ctx["timetable_days_json"] = json.dumps(timetable_days)
        return ctx


class LessonPlanCreateView(LessonPlanContextMixin, RoleRequiredMixin, CreateView):
    template_name = "academics/new_lesson_plan.html"
    form_class = LessonPlanForm
    success_url = "/academics/lesson-plans/"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD]

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
             return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        if request.user.role not in {UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD}:
            raise PermissionDenied()
        # FRD: ECD teachers submit Weekly Focus, not lesson plans
        if request.user.role == UserRole.TEACHER and is_ecd_teacher(request.user):
            messages.info(request, "ECD teachers submit a Weekly Focus instead of lesson plans.")
            return redirect("communications:weekly_focus_submit")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.teacher = self.request.user
        return _lesson_plan_form_processing(self, form)

class LessonPlanUpdateView(LessonPlanContextMixin, RoleRequiredMixin, UpdateView):
    model = LessonPlan
    template_name = "academics/new_lesson_plan.html"
    form_class = LessonPlanForm
    success_url = "/academics/lesson-plans/"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD]

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        plan = self.get_object()
        # FRD: ECD teachers submit Weekly Focus, not lesson plans
        if request.user.role == UserRole.TEACHER and is_ecd_teacher(request.user):
            messages.info(request, "ECD teachers submit a Weekly Focus instead of lesson plans.")
            return redirect("communications:weekly_focus_submit")
        if plan.teacher_id != request.user.id and request.user.role != UserRole.SUPER_ADMIN:
            raise PermissionDenied()
        if plan.status == LessonPlanStatus.APPROVED and request.user.role != UserRole.SUPER_ADMIN:
             messages.error(request, "Approved plans cannot be edited.")
             return redirect("academics:lesson_plans")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        return _lesson_plan_form_processing(self, form)

def _lesson_plan_snapshot(plan):
    """Serialize a LessonPlan's key fields for audit log snapshots."""
    return {
        "class_name": plan.class_name,
        "subject_name": plan.subject_name,
        "week_start_date": str(plan.week_start_date) if plan.week_start_date else None,
        "lesson_title": plan.lesson_title,
        "objectives": plan.objectives,
        "activities": plan.activities,
        "assessment_strategy": plan.assessment_strategy,
        "resources": plan.resources,
        "status": plan.status,
    }


def _lesson_plan_form_processing(view_instance, form):
        # FR-ACAD-010 / LP-003: Deadline and Duplicate enforcement
        from datetime import timedelta, time as time_type, date as date_type
        from django.conf import settings
        from django.utils import timezone
        now = timezone.now()
        week_start = form.cleaned_data["week_start_date"]

        # Block past weeks: only current week (this Monday) and future weeks allowed
        today = date_type.today()
        this_monday = today - timedelta(days=today.weekday())
        if week_start < this_monday:
            messages.error(view_instance.request, "Cannot submit lesson plans for past weeks. Please select the current week or a future week.")
            return view_instance.form_invalid(form)

        # Calculate deadline: Monday (8 AM) of the relevant week.
        # FR-LP-003: Monday 8:00 AM
        deadline_day = getattr(settings, "LESSON_PLAN_SUBMISSION_DEADLINE_DAY", 0) # 0 = Monday
        deadline_time_str = getattr(settings, "LESSON_PLAN_SUBMISSION_DEADLINE_TIME", "08:00")
        h, m = map(int, deadline_time_str.split(":"))
        
        days_to_monday = week_start.weekday()
        last_monday = week_start - timedelta(days=days_to_monday)
        
        # Calculate the Friday before that Monday
        # If Monday is 11th, Friday is 8th (3 days before)
        # 11 - 3 = 8.
        # Logic: last_monday - (7 - deadline_day) if deadline_day < 7
        deadline_date = last_monday - timedelta(days=(7 - deadline_day) % 7)
        
        deadline_dt = timezone.make_aware(timezone.datetime.combine(deadline_date, time_type(h, m)))
        
        # Allow Super Admin to bypass deadline
        is_late = now > deadline_dt
        can_bypass = view_instance.request.user.role == UserRole.SUPER_ADMIN
        
        if view_instance.request.POST.get("action") == "submit":
            if is_late:
                if not can_bypass:
                    messages.warning(view_instance.request, f"Submission deadline was {deadline_dt.strftime('%d %B, %H:%M')}. Plan will be flagged as late.")
                else:
                    messages.info(view_instance.request, "Super Admin: bypassing deadline.")
                
            form.instance.status = LessonPlanStatus.SUBMITTED
            form.instance.submitted_at = now
            view_instance._notify = True
            messages.success(view_instance.request, "Lesson plan submitted for HOD review.")
        else:
            view_instance._notify = False
            messages.success(view_instance.request, "Lesson plan saved as draft.")
            
        # Enforce Teacher-Class Integration from Timetable
        from timetable.models import TimetableSlot
        is_assigned = TimetableSlot.objects.filter(
            teacher=view_instance.request.user,
            class_name__iexact=form.cleaned_data["class_name"].strip(),
            subject_name__iexact=form.cleaned_data["subject_name"].strip()
        ).exists()
        
        if not is_assigned and view_instance.request.user.role == UserRole.TEACHER:
            messages.error(view_instance.request, "You cannot submit a lesson plan for a class/subject you are not assigned to in the timetable.")
            return view_instance.form_invalid(form)

        # Duplicate — redirect to edit the existing plan instead of blocking
        existing = LessonPlan.objects.filter(
            teacher=view_instance.request.user,
            week_start_date=week_start,
            class_name=form.cleaned_data["class_name"],
            subject_name=form.cleaned_data["subject_name"]
        ).exclude(pk=form.instance.pk)
        if existing.exists():
            existing_plan = existing.first()
            messages.info(
                view_instance.request,
                f"A lesson plan for {form.cleaned_data['class_name']} \u2014 {form.cleaned_data['subject_name']} "
                f"already exists this week. Redirecting to edit it.",
            )
            return redirect("academics:lesson_plan_edit", pk=existing_plan.pk)
        
        # Require at least one attachment for submission
        if view_instance.request.POST.get("action") == "submit":
            has_new = view_instance.request.FILES and len(view_instance.request.FILES.getlist('attachments')) > 0
            has_existing = form.instance.pk and form.instance.attachments.exists()
            if not has_new and not has_existing:
                messages.error(view_instance.request, "Please attach at least one file before submitting.")
                return view_instance.form_invalid(form)
        
        response = super(view_instance.__class__, view_instance).form_valid(form)
        
        # Handle Attachments (with batch + duplicate + size validation)
        if view_instance.request.FILES:
            from academics.models import LessonPlanAttachment
            from academics.validators import validate_upload_batch, check_duplicate_file, validate_attachment_file
            files = view_instance.request.FILES.getlist('attachments')

            # Combined size + count limit per MediaSettings
            try:
                validate_upload_batch(files, area="lesson_plans")
            except ValidationError as e:
                msg = e.message if hasattr(e, 'message') else str(e)
                messages.error(view_instance.request, msg)
                from communications.email_service import dispatch_notification
                dispatch_notification(
                    user=view_instance.request.user,
                    title="File upload error",
                    message=msg,
                    link="",
                )
                return view_instance.form_invalid(form)

            # Duplicate detection against existing attachments
            if form.instance.pk:
                existing_att = form.instance.attachments.all()
            else:
                existing_att = LessonPlanAttachment.objects.none()

            for f in files:
                duplicates = check_duplicate_file(f, existing_att, area="lesson_plans")
                if duplicates:
                    msg = f"File '{f.name}' appears to be a duplicate — skipped."
                    messages.warning(view_instance.request, msg)
                    from communications.email_service import dispatch_notification
                    dispatch_notification(
                        user=view_instance.request.user,
                        title="Duplicate file skipped",
                        message=msg,
                        link="",
                    )
                    continue
                try:
                    validate_attachment_file(f, area="lesson_plans")
                except ValidationError as e:
                    msg = f"Attachment '{f.name}' rejected: {e.message}" if f.name else f"File rejected: {e.message}"
                    messages.error(view_instance.request, msg)
                    from communications.email_service import dispatch_notification
                    dispatch_notification(
                        user=view_instance.request.user,
                        title="File validation error",
                        message=msg,
                        link="",
                    )
                    continue
                import os as _os
                raw_name = _os.path.basename(f.name)
                safe_name = re.sub(r'[^\w\.\-\(\)]', '_', raw_name).strip()
                f.name = safe_name
                LessonPlanAttachment.objects.create(
                    lesson_plan=form.instance,
                    file=f,
                    filename=safe_name,
                    uploaded_by=view_instance.request.user
                )

        if view_instance._notify:
            _notify_hod_lesson_plan(form.instance, view_instance.request.user)
            # Generate task for HOD review
            try:
                from tasks.services import generate_lesson_plan_review_tasks
                generate_lesson_plan_review_tasks(form.instance)
            except Exception:
                pass  # Never block lesson plan submission on task generation
        from audit.models import log_event
        action_type = "LESSON_PLAN_SUBMITTED" if view_instance.request.POST.get("action") == "submit" else "LESSON_PLAN_SAVED_DRAFT"
        log_event(
            actor=view_instance.request.user,
            action_type=action_type,
            model_name="LessonPlan",
            object_id=form.instance.pk,
            description=f"Lesson plan {form.instance.class_name} — {form.instance.subject_name} ({form.instance.week_start_date}) saved as {dict(LessonPlanStatus.choices).get(form.instance.status, 'unknown')}",
            before=_lesson_plan_snapshot(form.instance) if form.instance.pk else None,
            after=_lesson_plan_snapshot(form.instance),
            request=view_instance.request,
        )
        return response


class LessonPlanSubmitView(LoginRequiredMixin, View):
    login_url = "/accounts/login/"

    def post(self, request, pk: int):
        # FRD: ECD teachers submit Weekly Focus, not lesson plans
        if request.user.role == UserRole.TEACHER and is_ecd_teacher(request.user):
            messages.info(request, "ECD teachers submit a Weekly Focus instead of lesson plans.")
            return redirect("communications:weekly_focus_submit")
        plan = get_object_or_404(LessonPlan, pk=pk)
        if plan.teacher_id != request.user.id and request.user.role != UserRole.SUPER_ADMIN:
            raise PermissionDenied()
        if plan.status in {LessonPlanStatus.APPROVED}:
            raise ValidationError("Approved lesson plans cannot be resubmitted.")
        # Require at least one attachment
        if not plan.attachments.exists():
            messages.error(request, "Please attach at least one file before submitting.")
            return redirect("academics:lesson_plans")
        before = _lesson_plan_snapshot(plan)
        plan.status = LessonPlanStatus.SUBMITTED
        plan.submitted_at = timezone.now()
        plan.reviewer_feedback = ""
        plan.reviewed_by = None
        plan.reviewed_at = None
        plan.full_clean()
        plan.save(update_fields=["status", "submitted_at", "reviewer_feedback", "reviewed_by_id", "reviewed_at", "updated_at"])
        _notify_hod_lesson_plan(plan, request.user)
        # Generate task for HOD review
        try:
            from tasks.services import generate_lesson_plan_review_tasks
            generate_lesson_plan_review_tasks(plan)
        except Exception:
            pass
        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="LESSON_PLAN_SUBMITTED",
            model_name="LessonPlan",
            object_id=plan.pk,
            description=f"Lesson plan {plan.class_name} — {plan.subject_name} ({plan.week_start_date}) submitted for review",
            before=before,
            after=_lesson_plan_snapshot(plan),
            request=request,
        )
        messages.success(request, "Lesson plan submitted for review.")
        return redirect("academics:lesson_plans")


class LessonPlanWithdrawView(LoginRequiredMixin, View):
    """Allows a teacher to withdraw/pause a submitted plan back to draft."""

    login_url = "/accounts/login/"

    def post(self, request, pk: int):
        if request.user.role == UserRole.TEACHER and is_ecd_teacher(request.user):
            messages.info(request, "ECD teachers submit a Weekly Focus instead of lesson plans.")
            return redirect("communications:weekly_focus_submit")
        plan = get_object_or_404(LessonPlan, pk=pk)
        if plan.teacher_id != request.user.id and request.user.role != UserRole.SUPER_ADMIN:
            raise PermissionDenied()
        if plan.status != LessonPlanStatus.SUBMITTED:
            messages.error(request, "Only submitted plans can be withdrawn.")
            return redirect("academics:lesson_plans")
        before = _lesson_plan_snapshot(plan)
        plan.status = LessonPlanStatus.DRAFT
        plan.submitted_at = None
        plan.reviewer_feedback = ""
        plan.reviewed_by = None
        plan.reviewed_at = None
        plan.save(update_fields=["status", "submitted_at", "reviewer_feedback", "reviewed_by_id", "reviewed_at", "updated_at"])
        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="LESSON_PLAN_WITHDRAWN",
            model_name="LessonPlan",
            object_id=plan.pk,
            description=f"Lesson plan {plan.class_name} — {plan.subject_name} ({plan.week_start_date}) withdrawn to draft",
            before=before,
            after=_lesson_plan_snapshot(plan),
            request=request,
        )
        messages.success(request, "Lesson plan withdrawn. You can now edit and resubmit.")
        return redirect("academics:lesson_plan_edit", pk=plan.pk)


class LessonPlanAttachmentDeleteView(LoginRequiredMixin, View):
    """Delete a single attachment from a lesson plan.

    Only allowed when the plan is in DRAFT or REVISION_REQUESTED status
    and the requesting user is the plan owner (or Super Admin).
    """

    login_url = "/accounts/login/"

    def post(self, request, pk: int):
        att = get_object_or_404(LessonPlanAttachment, pk=pk)
        plan = att.lesson_plan

        if plan.teacher_id != request.user.id and request.user.role != UserRole.SUPER_ADMIN:
            raise PermissionDenied()

        if plan.status not in {LessonPlanStatus.DRAFT, LessonPlanStatus.REVISION_REQUESTED}:
            messages.error(request, "Attachments can only be removed from draft or revision-requested plans.")
            return redirect("academics:lesson_plan_edit", pk=plan.pk)

        file_path = att.file.path
        att.delete()
        # Remove the physical file from disk
        try:
            import os
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass

        messages.success(request, f"Removed {att.filename}.")
        return redirect("academics:lesson_plan_edit", pk=plan.pk)


def _get_missing_plan_teachers():
    """FR-ACAD-002: Return teachers who have NOT submitted a lesson plan for
    the current week after the Friday 4 PM deadline.

    Returns a list of dicts: [{teacher, department_label}]
    Only includes Primary and Secondary teachers (ECD excluded per FRD).
    """
    from datetime import timedelta, time as time_type
    from django.conf import settings as dj_settings
    from users.models import User

    now = timezone.now()
    today = now.date()

    # Determine the current week's Monday
    days_since_monday = today.weekday()
    this_monday = today - timedelta(days=days_since_monday)

    # Calculate the deadline: Monday 8:00 AM of the relevant week
    deadline_day = getattr(dj_settings, "LESSON_PLAN_SUBMISSION_DEADLINE_DAY", 0)  # Monday = 0
    deadline_time_str = getattr(dj_settings, "LESSON_PLAN_SUBMISSION_DEADLINE_TIME", "08:00")  # 8 AM EAT
    h, m = map(int, deadline_time_str.split(":"))
    deadline_date = this_monday - timedelta(days=(7 - deadline_day) % 7)
    deadline_dt = timezone.make_aware(timezone.datetime.combine(deadline_date, time_type(h, m)))

    # Only flag missing plans after the deadline has passed
    if now <= deadline_dt:
        return []

    # Get all active teachers (excluding ECD — use is_ecd_teacher for consistency)
    all_teachers = User.objects.filter(
        role=UserRole.TEACHER, is_active=True
    ).select_related("staff_profile")
    all_teachers = [t for t in all_teachers if not is_ecd_teacher(t)]

    # Find teachers who HAVE submitted a plan for this week
    # (exclude MISSING — those are persisted placeholders for non-submissions)
    submitted_teacher_ids = set(
        LessonPlan.objects.filter(
            week_start_date=this_monday,
            status__in=[LessonPlanStatus.SUBMITTED, LessonPlanStatus.APPROVED],
        ).values_list("teacher_id", flat=True)
    )
    # Teachers with a MISSING marker are still considered non-compliant
    missing_teacher_ids = set(
        LessonPlan.objects.filter(
            week_start_date=this_monday,
            status=LessonPlanStatus.MISSING,
        ).values_list("teacher_id", flat=True)
    )

    missing = []
    for teacher in all_teachers:
        if teacher.pk not in submitted_teacher_ids and teacher.pk not in missing_teacher_ids:
            dept_label = "Primary"
            if hasattr(teacher, "staff_profile") and teacher.staff_profile:
                dept_label = teacher.staff_profile.department or "Primary"
            missing.append({
                "teacher": teacher,
                "department_label": dept_label,
            })

    return missing


def _notify_hod_lesson_plan(plan, actor):
    """Notify the department-appropriate HOD for the submitted lesson plan.

    FRD: The relevant HOD receives an in-app notification. We resolve the
    department from the lesson's class_name via GradeClass and notify the
    corresponding HOD role (PRIMARY_HOD or ECD_HOD). HOS is always notified.
    """
    from communications.email_service import dispatch_notification
    from users.models import User, UserRole
    from academics.models import GradeClass, Department

    # Determine department from class name
    dept = GradeClass.objects.filter(name=plan.class_name).values_list("department", flat=True).first()

    if dept == Department.ECD:
        hod_roles = [UserRole.ECD_HOD]
    elif dept == Department.PRIMARY:
        hod_roles = [UserRole.PRIMARY_HOD]
    else:
        # Secondary or unknown — notify both HOD types as fallback
        hod_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD]

    # Always include HOS so they have school-wide visibility
    hod_roles.append(UserRole.HEAD_OF_SCHOOL)

    hods = User.objects.filter(role__in=hod_roles, is_active=True).distinct()
    for hod in hods:
        dispatch_notification(
            user=hod,
            title="New lesson plan submitted",
            message=f"New lesson plan submitted — {plan.teacher.get_full_name()}, {plan.class_name} — {plan.subject_name}",
            link="/academics/lesson-plans/",
            actor=actor
        )


def _build_heatmap(role):
    """Build a subject×class coverage heatmap for the given role's department(s).

    Returns a dict with:
      subjects  — list of Subject objects (row labels)
      classes   — list of GradeClass objects (column labels)
      grid      — list of dicts, each {subject, cells: [{class, status, teacher, exists}]}
      week_start — the Monday of the current week
    """
    from datetime import date, timedelta

    if role == UserRole.ECD_HOD:
        departments = [Department.ECD]
    elif role == UserRole.PRIMARY_HOD:
        departments = [Department.PRIMARY]
    else:
        departments = [Department.PRIMARY, Department.LOWER_SECONDARY]

    classes = list(
        GradeClass.objects.filter(department__in=departments).order_by("sort_order", "name")
    )
    subjects = list(
        Subject.objects.filter(department__in=departments, is_active=True)
        .order_by("name")
        .prefetch_related("classes")
    )

    # Which (subject, class) pairs actually make sense per the timetable
    valid_pairs: set[tuple[str, str]] = set()
    for subj in subjects:
        for gc in subj.classes.all():
            valid_pairs.add((subj.name, gc.name))

    today = date.today()
    week_start = today - timedelta(days=today.weekday())

    plans = (
        LessonPlan.objects.filter(
            week_start_date=week_start,
            class_name__in=[c.name for c in classes],
            subject_name__in=[s.name for s in subjects],
        )
        .select_related("teacher")
        .order_by("-created_at")
    )

    plan_lookup: dict[tuple[str, str], LessonPlan] = {}
    for p in plans:
        key = (p.class_name, p.subject_name)
        if key not in plan_lookup:
            plan_lookup[key] = p

    grid = []
    for subj in subjects:
        cells = []
        for gc in classes:
            plan = plan_lookup.get((gc.name, subj.name))
            applicable = (subj.name, gc.name) in valid_pairs
            if plan:
                cells.append(
                    {
                        "class": gc.name,
                        "status": plan.status,
                        "teacher": plan.teacher.get_full_name() or plan.teacher.username,
                        "exists": True,
                    }
                )
            elif applicable:
                cells.append(
                    {
                        "class": gc.name,
                        "status": None,
                        "teacher": None,
                        "exists": False,
                    }
                )
            else:
                cells.append(
                    {
                        "class": gc.name,
                        "status": None,
                        "teacher": None,
                        "exists": None,  # N/A
                    }
                )
        grid.append({"subject": subj.name, "cells": cells})

    return {
        "subjects": subjects,
        "classes": classes,
        "grid": grid,
        "week_start": week_start,
    }


class LessonPlanReviewView(LoginRequiredMixin, View):
    login_url = "/accounts/login/"

    def post(self, request, pk: int):
        if not _is_hod_like(request.user.role):
            raise PermissionDenied()
        plan = get_object_or_404(LessonPlan, pk=pk)
        form = LessonPlanReviewForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Invalid review form.")
            return redirect("academics:lesson_plans")
        decision = form.cleaned_data["decision"]
        feedback = (form.cleaned_data.get("feedback") or "").strip()
        if decision in (LessonPlanStatus.REJECTED, LessonPlanStatus.REVISION_REQUESTED) and not feedback:
            messages.error(request, "Feedback is required when rejecting or requesting revision.")
            return redirect("academics:lesson_plans")
        before = _lesson_plan_snapshot(plan)
        plan.status = decision
        plan.reviewed_by = request.user
        plan.reviewed_at = timezone.now()
        plan.reviewer_feedback = feedback
        plan.full_clean()
        plan.save(update_fields=["status", "reviewed_by_id", "reviewed_at", "reviewer_feedback", "updated_at"])
        
        from audit.models import log_event
        action_type = "LESSON_PLAN_" + ("APPROVED" if decision == LessonPlanStatus.APPROVED else "REJECTED" if decision == LessonPlanStatus.REJECTED else "REVISION_REQUESTED")
        log_event(
            actor=request.user,
            action_type=action_type,
            model_name="LessonPlan",
            object_id=plan.pk,
            description=f"Lesson plan {plan.class_name} — {plan.subject_name} ({plan.term.name}) {plan.get_status_display().lower()}",
            before=before,
            after=_lesson_plan_snapshot(plan),
            request=request,
        )
        
        # NOTIF-05 / NOTIF-06
        from communications.email_service import dispatch_notification
        status_text = plan.get_status_display().lower()
        title = f"Lesson Plan {status_text.capitalize()}"
        msg = f"Your lesson plan for {plan.class_name} — {plan.subject_name} ({plan.term.name}) was {status_text} by {request.user.get_full_name()}."
        if feedback:
            msg += f"\n\nFeedback: {feedback}"
        
        dispatch_notification(
            user=plan.teacher,
            title=title,
            message=msg,
            link="/academics/lesson-plans/",
            actor=request.user
        )
        
        messages.success(request, f"Lesson plan {status_text}.")
        return redirect("academics:lesson_plans")


class LessonPlanComplianceView(LoginRequiredMixin, TemplateView):
    """Dedicated lesson plan compliance tracking view.

    Shows per-teacher submission rates, overdue plans, and historical trend
    for the current term. Filters by department (Primary / ECD).
    """
    template_name = "academics/lesson_plan_compliance.html"
    login_url = "/accounts/login/"

    def dispatch(self, request, *args, **kwargs):
        if not _is_hod_like(request.user.role):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from users.models import User
        from django.db.models import Count, Q
        from collections import defaultdict

        user = self.request.user
        today = timezone.localdate()
        dept_filter = self.request.GET.get("dept", "")

        term = Term.get_current()
        if not term:
            ctx["no_term"] = True
            return ctx

        ctx["term"] = term
        ctx["today"] = today
        ctx["academics_tab"] = "compliance"

        # Compute expected weeks so far this term
        if term.start_date and term.end_date:
            weeks_elapsed = max(1, ((today - term.start_date).days // 7) + 1)
        else:
            weeks_elapsed = 8
        ctx["weeks_elapsed"] = weeks_elapsed

        # Determine which teachers to show based on user role
        # ECD teachers only do Weekly Focus — never show them in lesson plan compliance
        if user.role == UserRole.PRIMARY_HOD:
            allowed_depts = [Department.PRIMARY]
        elif user.role == UserRole.ECD_HOD:
            # ECD has no lesson plans — redirect or show empty
            allowed_depts = []
        else:
            # HOS / Super admin — Primary only (ECD excluded from lesson plans)
            allowed_depts = [Department.PRIMARY]

        if dept_filter in ("primary", "ecd"):
            ctx["filter_dept"] = dept_filter
            if dept_filter == "primary":
                allowed_depts = [Department.PRIMARY]
            else:
                # ECD filter — no lesson plans exist for ECD
                allowed_depts = []

        dept_class_names = list(
            GradeClass.objects.filter(department__in=allowed_depts).values_list("name", flat=True)
        )

        # Teachers with a staff_profile in the relevant departments
        # NOTE: staff_profile.department may be "Primary" (capitalized) vs "PRIMARY" (enum value)
        # Use case-insensitive match for robustness
        from django.db.models import Q
        dept_q = Q()
        for d in allowed_depts:
            dept_q |= Q(staff_profile__department__iexact=d)
        teachers = (
            User.objects.filter(
                role=UserRole.TEACHER,
                is_active=True,
            )
            .filter(dept_q)
            .select_related("staff_profile")
            .order_by("last_name", "first_name")
        )

        # All lesson plans for these teachers this term — single query
        plans = LessonPlan.objects.filter(
            teacher__in=teachers,
            term=term,
            class_name__in=dept_class_names,
        ).values("teacher_id", "status", "week_start_date")

        # Per-teacher aggregation
        teacher_plans = defaultdict(lambda: {"submitted": set(), "approved": set(), "revision": set(), "rejected": set()})

        for p in plans:
            t = teacher_plans[p["teacher_id"]]
            if p["status"] == LessonPlanStatus.APPROVED:
                t["approved"].add(p["week_start_date"])
            elif p["status"] == LessonPlanStatus.SUBMITTED:
                t["submitted"].add(p["week_start_date"])
            elif p["status"] == LessonPlanStatus.REVISION_REQUESTED:
                t["revision"].add(p["week_start_date"])
            elif p["status"] == LessonPlanStatus.REJECTED:
                t["rejected"].add(p["week_start_date"])

        # Batch fetch latest submission date per teacher — single query instead of N
        from django.db.models import Max
        latest_dates = LessonPlan.objects.filter(
            teacher__in=teachers, term=term
        ).values("teacher_id").annotate(
            latest_submitted_at=Max("submitted_at")
        ).values_list("teacher_id", "latest_submitted_at")
        latest_date_map = dict(latest_dates)

        # Build compliance rows
        compliance_rows = []
        overall_approved = 0
        overall_expected = 0

        for teacher in teachers:
            tp = teacher_plans[teacher.pk]
            approved_weeks = len(tp["approved"])
            submitted_weeks = len(tp["submitted"])
            revision_weeks = len(tp["revision"])
            rejected_weeks = len(tp["rejected"])

            # Any plan in any status counts as "covered"
            covered = len({
                *tp["approved"], *tp["submitted"], *tp["revision"], *tp["rejected"]
            })
            missing = max(0, weeks_elapsed - covered)

            pct = round((approved_weeks / weeks_elapsed) * 100) if weeks_elapsed else 0
            overall_approved += approved_weeks
            overall_expected += weeks_elapsed

            compliance_rows.append({
                "teacher": teacher,
                "dept": teacher.staff_profile.department if hasattr(teacher, "staff_profile") else "",
                "submitted": submitted_weeks,
                "approved": approved_weeks,
                "revision": revision_weeks,
                "rejected": rejected_weeks,
                "missing": missing,
                "pct": pct,
                "grade": "A" if pct >= 90 else ("B" if pct >= 75 else ("C" if pct >= 50 else "D")),
                "last_submission": latest_date_map.get(teacher.pk),
            })

        ctx["compliance_rows"] = compliance_rows
        ctx["overall_pct"] = round((overall_approved / overall_expected) * 100) if overall_expected else 0
        ctx["total_teachers"] = len(compliance_rows)
        ctx["total_compliant"] = len([r for r in compliance_rows if r["pct"] >= 75])
        ctx["total_missing"] = sum(r["missing"] for r in compliance_rows)

        # Trend: weekly approved counts
        weekly_data = defaultdict(int)
        for p in plans:
            if p["status"] == LessonPlanStatus.APPROVED:
                weekly_data[p["week_start_date"]] += 1

        ctx["weekly_trend"] = sorted(
            [{"week": w, "count": c} for w, c in weekly_data.items()],
            key=lambda x: x["week"]
        )

        return ctx


class ExamScoreEntryView(LoginRequiredMixin, TemplateView):
    template_name = "academics/exam_scores.html"
    login_url = "/accounts/login/"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role == UserRole.TEACHER:
            # ECD teachers -> ECD evaluations
            if is_ecd_teacher(request.user):
                return redirect("academics:ecd_evaluations_entry")
            # Primary teachers -> primary assessment
            from timetable.models import TimetableSlot
            primary_names = grade_class_names_for_department(Department.PRIMARY)
            teacher_classes = list(set(TimetableSlot.objects.filter(teacher=request.user).values_list("class_name", flat=True)))
            has_primary = any(c in primary_names for c in teacher_classes)
            if has_primary:
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
            if self.request.user.role == UserRole.PRIMARY_HOD:
                from academics.models import GradeClass, Department
                primary_classes = GradeClass.objects.filter(department=Department.PRIMARY).values_list('name', flat=True)
                students_qs = students_qs.filter(class_name__in=primary_classes)
            elif self.request.user.role == UserRole.ECD_HOD:
                from academics.models import GradeClass, Department
                ecd_classes = GradeClass.objects.filter(department=Department.ECD).values_list('name', flat=True)
                students_qs = students_qs.filter(class_name__in=ecd_classes)

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
        if request.user.role not in {UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD}:
            raise PermissionDenied()
        
        form = ExamScoreFilterForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Please provide term, class, subject, and exam type.")
            return redirect(request.path)

        term = form.cleaned_data["term"]
        class_name = form.cleaned_data["class_name"].strip()
        subject_name = form.cleaned_data["subject_name"].strip()
        exam_type = form.cleaned_data["exam_type"]

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
            except InvalidOperation:
                score_errors[student.id] = f"Score must be a number between 0 and {max_score}."
                continue

            # FR-ACAD-001: explicit 0–max_score range check
            if score_val < 0 or score_val > max_score:
                score_errors[student.id] = f"Score must be a number between 0 and {max_score}."
                continue

            existing = existing_scores.get(student.id)
            if existing:
                if existing.is_locked:
                    score_errors[student.id] = "This score is locked and cannot be edited."
                    continue
                if existing.score != score_val:
                    existing.score = score_val
                    existing.full_clean()
                    existing.save(update_fields=["score", "updated_at"])
                    updates_made += 1
            else:
                new_score = ExamScore(
                    student=student,
                    term=term,
                    subject_name=subject_name,
                    exam_type=exam_type,
                    score=score_val,
                    entered_by=request.user
                )
                new_score.full_clean()
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
            if updates_made > 0:
                messages.warning(
                    request,
                    f"Saved {updates_made} score(s), but {len(score_errors)} had errors."
                )
            else:
                messages.error(
                    request,
                    f"{len(score_errors)} score(s) could not be saved."
                )
            return self.render_to_response(ctx)

        # ── HOD notification: grades ready for review ────────────────────────
        if updates_made > 0:
            try:
                from communications.email_service import dispatch_notification
                from users.models import User
                hod_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL]
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
                hod_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL]
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
                pass

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

class ReportRouterView(LoginRequiredMixin, View):
    """Router to send users to their appropriate reports view based on role."""
    login_url = "/accounts/login/"

    def get(self, request, *args, **kwargs):
        role = request.user.role
        if role == UserRole.PARENT:
            return redirect("academics:parent_reports")
        elif role in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD}:
            return redirect("academics:analytics")
        elif role == UserRole.TEACHER:
            return redirect("academics:report_card_list")
        else:
            return redirect("/")

class HOSSignOffListView(LoginRequiredMixin, TemplateView):
    template_name = "academics/hos_signoff_list.html"
    login_url = "/accounts/login/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.request.user.role not in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD}:
            raise PermissionDenied()
            
        term = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
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
                
        ctx["academics_tab"] = "hos_signoff"
                
        return ctx

from django.core.management import call_command
from django.http import HttpResponse

class MigrationTriggerView(LoginRequiredMixin, View):
    """Temporary view to trigger migrations when terminal is restricted."""
    def get(self, request):
        if not request.user.is_superuser:
            return HttpResponse("Unauthorized", status=403)
        try:
            print("Running makemigrations...")
            call_command('makemigrations', 'hr', '--noinput')
            call_command('makemigrations', 'attendance', '--noinput')
            call_command('makemigrations', 'academics', '--noinput')
            print("Running migrate...")
            call_command('migrate', '--noinput')
            return HttpResponse("Migrations successfully executed.")
        except Exception as e:
            return HttpResponse(f"Error: {e}")

class ReportCardListView(RoleRequiredMixin, TemplateView):
    template_name = "academics/report_card_list.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.TEACHER]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        class_name = self.request.GET.get("class_name")
        current_term = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
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
        subjects[subj]["avg"] = grade_result["average"]
        subjects[subj]["grade"] = grade_result["full_grade"]
        subjects[subj]["gap_case"] = grade_result["gap"]["case"]
        subjects[subj]["gap_label"] = grade_result["gap"]["label"]
        subjects[subj]["redistributed_weights"] = grade_result["redistributed_weights"]
        subjects[subj]["makeup_required"] = grade_result["makeup_required"]
    ctx["subjects"] = subjects

    # Cambridge Checkpoint (FR-ACAD-005) — Grades 6, 7, 8, and 9
    if report.student.class_name in ["Grade 6", "Grade 7", "Grade 8", "Grade 9"]:
        from academics.models import CambridgeCheckpointScore
        ctx["checkpoint_scores"] = CambridgeCheckpointScore.objects.filter(
            student=report.student,
            academic_year=report.term.academic_year,
        )

    return ctx


class HOSReportPreviewView(LoginRequiredMixin, TemplateView):
    template_name = "academics/report_card_print.html"
    login_url = "/accounts/login/"

    def get_template_names(self):
        report = get_object_or_404(ReportCard, pk=self.kwargs["pk"])
        if report.is_ecd_report:
            return ["academics/ecd_report_preview.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.request.user.role not in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.ECD_HOD}:
            raise PermissionDenied()

        report = get_object_or_404(ReportCard, pk=kwargs["pk"])
        user = self.request.user
        if user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            all_classes = get_teacher_assigned_classes_from_tca(user)
            class_teacher_classes = [cls for cls, info in all_classes.items() if info.get("is_class_teacher")]
            if report.student.class_name not in class_teacher_classes:
                raise PermissionDenied()

        ctx.update(_build_report_card_context(report))
        return ctx

class HOSSignOffActionView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        if request.user.role not in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD}:
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
                except:
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
    allowed_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from django.db.models import Q

        term = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
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
        else:
            allowed_depts = [Department.PRIMARY, Department.ECD]

        if dept_filter == "primary":
            allowed_depts = [Department.PRIMARY]
        elif dept_filter == "ecd":
            allowed_depts = [Department.ECD]

        dept_class_names = list(
            GradeClass.objects.filter(department__in=allowed_depts).values_list("name", flat=True)
        )

        # Reports with comments submitted, not yet published — includes ECD + Primary
        reports = ReportCard.objects.filter(
            term=term,
            student__class_name__in=dept_class_names,
            comments_submitted=True,
        ).exclude(
            status=ReportCardStatus.PUBLISHED
        ).select_related("student", "generated_by").order_by("student__class_name", "student__last_name")

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
        ctx["total_pending"] = len(rows)
        ctx["total_ready"] = len([r for r in rows if r["all_scores_approved"] and r["comment_ok"]])
        ctx["academics_tab"] = "review_queue"
        return ctx

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action", "approve")
        report_id = request.POST.get("report_id")
        reason = request.POST.get("reason", "")

        from academics.services import sign_off_report, reject_report_for_edit

        if not report_id:
            messages.error(request, "No report specified.")
            return redirect("academics:report_review_queue")

        try:
            report = ReportCard.objects.get(pk=report_id)
        except ReportCard.DoesNotExist:
            messages.error(request, "Report not found.")
            return redirect("academics:report_review_queue")

        try:
            if action == "reject":
                reject_report_for_edit(report, request.user, reason=reason)
                messages.success(request, f"Report for {report.student.first_name} returned for correction.")
            else:
                sign_off_report(report, request.user)
                messages.success(request, f"Report for {report.student.first_name} signed off and published.")
        except Exception as e:
            messages.error(request, str(e))

        return redirect("academics:report_review_queue")


class AcademicAnalyticsView(RoleRequiredMixin, TemplateView):
    template_name = "academics/analytics.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD]

    def _compute_term_analytics(self, term, role, dept_filter, selected_class, weights):
        """Compute all analytics metrics for a given term. Returns dict of computed values."""
        from django.db.models import Avg, Count, Q, Sum, F, Case, When, Value, FloatField, ExpressionWrapper
        from academics.models import Department, GradeClass, ExamType, ReportCard, ReportCardStatus

        result = {}
        if not term:
            return result

        # ── ECD Analytics Path (uses ECDEvaluation ratings, not ExamScore) ──
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
                avg_rating = total_val / count  # 1.0 – 4.0
                avg_pct = avg_rating / 4.0 * 100.0  # Scale to 0–100%
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

            # Class performance — single batch query
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

        # ── ExamScore Analytics Path (Primary / Secondary) ──
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

        # Current (default) term
        current_term = Term.objects.filter(is_locked=False).order_by("-start_date").first()
        if not current_term:
            current_term = Term.objects.order_by("-start_date").first()
        ctx["current_term"] = current_term
        
        # Available terms for the selector
        available_terms = Term.objects.all().order_by("-start_date")
        ctx["available_terms"] = available_terms
        
        # Selected term from query param (default to current term)
        selected_term_id = self.request.GET.get("term_id")
        if selected_term_id:
            selected_term = Term.objects.filter(id=selected_term_id).first()
        else:
            selected_term = current_term
        ctx["selected_term"] = selected_term
        ctx["selected_term_id"] = selected_term.id if selected_term else None
        
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

class ExamScoreCorrectionView(RoleRequiredMixin, View):
    """Authorised correction of locked scores."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD]

    def post(self, request, pk):
        score = get_object_or_404(ExamScore, pk=pk)
        new_val = request.POST.get("score")
        reason = request.POST.get("reason", "").strip()
        
        if not reason:
            messages.error(request, "Reason is required for correction.")
            return redirect("academics:exam_scores_entry")
            
        try:
            old_val = score.score
            score.score = float(new_val)
            score.corrected_by = request.user
            score.correction_reason = reason
            score.is_locked = True # Re-lock
            score.full_clean()
            score.save()
            
            from audit.models import log_event
            log_event(
                actor=request.user,
                action_type="EXAM_SCORE_CORRECTED",
                model_name="ExamScore",
                object_id=score.pk,
                description=f"Score corrected from {old_val} to {score.score} for {score.student.admission_no}",
                before_value=str(old_val),
                after_value=str(score.score),
                request=request
            )
            messages.success(request, "Score corrected successfully.")
        except Exception as e:
            messages.error(request, str(e))
            
        return redirect("academics:exam_scores_entry")


class ExamScoreApprovalQueueView(RoleRequiredMixin, TemplateView):
    """FR-ACAD-006: Dedicated HOD approval queue for submitted exam scores.
    Shows scores with status=SUBMITTED, allows approve/return/bulk actions.
    """
    template_name = "academics/exam_score_approval_queue.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from academics.models import ScoreStatus, GradeClass, Department

        term = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
        ctx["current_term"] = term

        # Filters
        selected_class = self.request.GET.get("class_name", "").strip()
        selected_subject = self.request.GET.get("subject_name", "").strip()
        ctx["selected_class"] = selected_class
        ctx["selected_subject"] = selected_subject

        # Base queryset: submitted scores for review
        qs = ExamScore.objects.filter(
            status=ScoreStatus.SUBMITTED
        ).select_related("student", "entered_by")

        if term:
            qs = qs.filter(term=term)

        # FR-ACAD-011: HOD Scoping
        role = self.request.user.role
        if role == UserRole.PRIMARY_HOD:
            primary_classes = GradeClass.objects.filter(department=Department.PRIMARY).values_list("name", flat=True)
            qs = qs.filter(student__class_name__in=primary_classes)
        elif role == UserRole.ECD_HOD:
            ecd_classes = GradeClass.objects.filter(department=Department.ECD).values_list("name", flat=True)
            qs = qs.filter(student__class_name__in=ecd_classes)

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
        queue_details = []
        for item in queue_items:
            scores = qs.filter(
                student__class_name=item["student__class_name"],
                subject_name=item["subject_name"],
                exam_type=item["exam_type"],
            ).order_by("student__last_name", "student__first_name")
            queue_details.append({
                "class_name": item["student__class_name"],
                "subject_name": item["subject_name"],
                "exam_type": item["exam_type"],
                "score_count": item["score_count"],
                "scores": list(scores),
            })

        ctx["queue_details"] = queue_details
        ctx["total_pending"] = qs.count()

        # Group queue_details by exam_type for tab panels
        from collections import OrderedDict
        by_exam = OrderedDict()
        for item in queue_details:
            et = item["exam_type"]
            if et not in by_exam:
                by_exam[et] = []
            by_exam[et].append(item)
        ctx["queue_by_exam_type"] = by_exam

        # Available filter options
        ctx["available_classes"] = (
            qs.values_list("student__class_name", flat=True).distinct().order_by("student__class_name")
        )
        ctx["available_subjects"] = (
            qs.values_list("subject_name", flat=True).distinct().order_by("subject_name")
        )

        # Exam type summary for tabs
        exam_type_summary = (
            qs.values("exam_type")
            .annotate(score_count=Count("id"))
            .order_by("exam_type")
        )
        ctx["exam_types"] = [r["exam_type"] for r in exam_type_summary]
        ctx["exam_type_counts"] = {r["exam_type"]: r["score_count"] for r in exam_type_summary}
        ctx["exam_types_count"] = len(exam_type_summary)
        ctx["subjects_count"] = qs.values("subject_name").distinct().count()

        ctx["academics_tab"] = "approval_queue"

        return ctx

    def post(self, request, *args, **kwargs):
        from academics.models import ScoreStatus
        from communications.email_service import dispatch_notification
        from audit.models import log_event

        action = request.POST.get("action", "")
        score_ids = request.POST.getlist("score_ids")
        reason = request.POST.get("reason", "").strip()

        if not score_ids:
            messages.error(request, "No scores selected.")
            return redirect("academics:exam_score_approval_queue")

        scores = ExamScore.objects.filter(pk__in=score_ids, status=ScoreStatus.SUBMITTED)
        updated = 0

        for score in scores:
            if action == "approve":
                score.status = ScoreStatus.APPROVED
                score.approved_by = request.user
                score.approved_at = timezone.now()
                score.save(update_fields=["status", "approved_by_id", "approved_at", "updated_at"])

                log_event(
                    actor=request.user,
                    action_type="EXAM_SCORE_APPROVED",
                    model_name="ExamScore",
                    object_id=score.pk,
                    description=f"Score {score.score} approved for {score.student.admission_no} in {score.subject_name}",
                    request=request,
                )
                updated += 1

            elif action == "return":
                if not reason:
                    messages.error(request, "Reason is required when returning scores for correction.")
                    return redirect("academics:exam_score_approval_queue")
                score.status = ScoreStatus.RETURNED
                score.correction_reason = reason
                score.is_locked = False
                score.save(update_fields=["status", "correction_reason", "is_locked", "updated_at"])

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
        messages.success(request, f"{updated} score(s) {action_label}.")
        return redirect("academics:exam_score_approval_queue")


class AtRiskStudentsListView(RoleRequiredMixin, TemplateView):
    template_name = "academics/at_risk_list.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from django.db.models import Sum, F, Case, When, Value, FloatField, ExpressionWrapper, Q
        from academics.models import Department, GradeClass, get_exam_weights

        term = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
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
            # ── ECD At-Risk Path (uses ECDEvaluation ratings) ──
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

            # ── ExamScore At-Risk Path (Primary / Secondary) ──
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


from django.views.generic import ListView, CreateView
from django.urls import reverse_lazy


class AcademicYearListView(RoleRequiredMixin, ListView):
    """FR-CAL-001: Super Admin views and manages academic years."""
    model = AcademicYear
    template_name = "academics/academic_year_list.html"
    context_object_name = "academic_years"
    allowed_roles = [UserRole.SUPER_ADMIN]

    def get_queryset(self):
        return AcademicYear.objects.all().order_by("-is_current", "name")


class AcademicYearCreateView(RoleRequiredMixin, CreateView):
    """FR-CAL-001: Super Admin configures academic year structure."""
    model = AcademicYear
    template_name = "academics/academic_year_form.html"
    fields = ["name", "is_current", "number_of_terms", "start_date", "end_date"]
    success_url = reverse_lazy("academics:academic_year_list")
    allowed_roles = [UserRole.SUPER_ADMIN]

class TermListView(RoleRequiredMixin, ListView):
    model = Term
    template_name = "academics/term_list.html"
    context_object_name = "terms"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER]

class TermCreateView(RoleRequiredMixin, CreateView):
    """FR-CAL-001: Super Admin configures academic year structure."""
    model = Term
    form_class = TermForm
    template_name = "academics/term_form.html"
    success_url = reverse_lazy("academics:terms")
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER]


class ParentReportListView(LoginRequiredMixin, TemplateView):
    template_name = "academics/parent_report_list.html"
    login_url = "/accounts/login/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.request.user.role != UserRole.PARENT:
            raise PermissionDenied()
            
        # find students linked to parent
        from students.models import ParentGuardian, Student
        guardian = ParentGuardian.objects.filter(user=self.request.user).first()
        if guardian:
            students = Student.objects.filter(studentguardian__guardian=guardian).distinct()
            reports = ReportCard.objects.filter(student__in=students, status=ReportCardStatus.PUBLISHED).select_related("student", "term", "term__academic_year").order_by("-term__start_date")
            ctx["reports"] = reports
        else:
            ctx["reports"] = []
            
        return ctx




class ECDEvaluationEntryView(RoleRequiredMixin, TemplateView):
    template_name = "academics/ecd_evaluation_entry.html"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ECD_HOD]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["academics_tab"] = "ecd_evaluations"
        return ctx

    # FRD FR-ACAD-ECD: Granular domain competencies per class type
    # Each entry: (section_header, [list of competency strings])
    DOMAIN_GROUPS = {
        "pre_k": [
            ("Numeracy & Cognitive", [
                "Recognises numbers 1–10", "Recites numbers 1–10",
                "Understands size (big / small)", "Identifies colours learned this term",
                "Identifies shapes learned this term", "Sorting shapes & colours",
            ]),
            ("Communication Skills", [
                "Knows first name", "Responds to direct questions",
                "Expresses needs clearly", "Responds well within a group",
                "Repeats sentences",
            ]),
            ("Motor Skills", [
                "Can tear paper", "Holds & uses paint brush, pencil, crayon",
                "Can thread beads", "Holds spoon and eats without help",
                "Can jump up and down", "Can throw a ball",
                "Can kick a ball", "Participates in music and movement",
            ]),
            ("Social / Emotional Skills", [
                "Enjoys school activities", "Plays and shares well with others",
                "Listens and follows instructions",
            ]),
            ("Swimming", [
                "Getting in the water", "Hand & leg movement",
                "Attitude", "Swimming attendance",
            ]),
        ],
        "kindergarten": [
            ("Numeracy", [
                "Number counting 1–100", "Number formation 0–9",
                "Number sequence 1–40", "Number recognition 1–40",
                "Number value", "Number tracing",
                "Basic shapes", "Problem solving",
                "Can put together puzzles (critical thinking)",
                "Ability to do a maze (critical thinking)",
                "Basic addition", "Basic subtraction",
            ]),
            ("Reading", [
                "Recognise, compare & distinguish sounds",
                "Listening to stories",
                "Vocabulary, grammar & pronunciation",
                "Songs and rhymes", "Comprehension",
                "Ability to sit still and listen to recall",
                "Reading / blending two to three sounds",
            ]),
            ("Writing", [
                "Sound and number formation",
                "Handwriting neatness and pencil grip",
                "Writing first name", "Writing last name",
            ]),
            ("Bible Memory", [
                "Scripture memorisation",
            ]),
            ("Personal", [
                "Completes work timely", "Shows initiative and creativity",
                "Attentive to direction", "Works well independently",
                "Exhibits self-confidence", "Portrays independence",
                "Exhibits self-control", "Considerate of others",
                "Responds well to correction", "Cleanliness",
                "Food appetite",
            ]),
            ("Physical Development", [
                "Swimming", "Catch and throw",
                "Balancing", "Running",
                "Kicking", "Jumping jacks",
            ]),
            ("Artistry", [
                "Good hand and eye coordination to perform a task",
                "Participates in music and dance",
                "Fine motor grip", "Shows creativity in crafts",
            ]),
        ],
        "pre_school": [
            ("Numeracy", [
                "Counting 1–10: counting", "Counting 1–10: recognition", "Counting 1–10: matching",
                "Shapes recognition", "Shapes association", "Colour recognition", "Colour association",
            ]),
            ("Pre-Writing", [
                "colouring, painting, moulding", "eye-hand coordination",
            ]),
            ("School Readiness", [
                "Readiness to participate in class activities", "Attention span", "Grade level maturity",
                "Able to eat on their own", "Able to comprehend and follow instructions",
                "Able to communicate in comprehensible speech", "Potty trained",
            ]),
            ("Social Skills", [
                "Willing to share", "Plays well and safely with others", "Attitude towards discipline and correction",
            ]),
        ],
        "abc": [
            ("Academic Progress", [
                "Mathematics", "English", "Science",
                "Social Studies", "Word Building", "Scripture",
            ]),
            ("General Assignments", [
                "Completes assignments on time",
                "Quality of homework",
                "Independent work habits",
            ]),
            ("Character", [
                "Honesty & integrity", "Respect for authority",
                "Kindness to peers", "Self-discipline",
                "Attitude toward learning",
            ]),
        ],
    }

    # Flat domain list derived from DOMAIN_GROUPS (used for DB keys)
    @classmethod
    def get_flat_domains(cls, template_type):
        groups = cls.DOMAIN_GROUPS.get(template_type, [])
        return [item for _, items in groups for item in items]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        class_name = (self.request.GET.get("class_name") or "").strip()
        template_type = (self.request.GET.get("template_type") or "").strip()

        #  Fixed ECD class tabs matching the mockup 
        ECD_TABS = [
            {"label": "Pre-Kindergarten", "class_name": "Pre-Kindergarten", "template_type": "pre_k"},
            {"label": "Kindergarten",     "class_name": "Kindergarten",     "template_type": "kindergarten"},
            {"label": "Pre-School (RR)",  "class_name": "Pre-School",       "template_type": "pre_school"},
            {"label": "ABC Class",        "class_name": "ABC Class",        "template_type": "abc"},
        ]
        if self.request.user.role == UserRole.TEACHER:
            assigned_classes = get_teacher_assigned_classes(self.request.user)
            ctx["ecd_tabs"] = [t for t in ECD_TABS if t["class_name"] in assigned_classes]
        else:
            ctx["ecd_tabs"] = ECD_TABS

        # Auto-detect current term (unlocked only)
        auto_term = Term.objects.filter(is_locked=False).order_by("-start_date").first()
        ctx["current_term"] = auto_term

        # If no class_name in URL, default to first available tab
        if not class_name and ctx["ecd_tabs"]:
            class_name = ctx["ecd_tabs"][0]["class_name"]
            template_type = ctx["ecd_tabs"][0]["template_type"]

        # Auto-resolve template_type from class name if missing
        if class_name and not template_type:
            _tmap = {t["class_name"]: t["template_type"] for t in ECD_TABS}
            template_type = _tmap.get(class_name, "pre_k")

        ctx["selected_class"] = class_name
        ctx["selected_template"] = template_type
        term = auto_term
        ctx["selected_term"] = term

        if not term or not class_name or not template_type:
            return ctx

        # Security: Ensure teacher is allowed to see this class
        if self.request.user.role == UserRole.TEACHER:
            from timetable.models import TimetableSlot
            if not TimetableSlot.objects.filter(teacher=self.request.user, class_name=class_name).exists():
                messages.error(self.request, "You are not authorized to evaluate this class.")
                return ctx

        students = Student.objects.filter(class_name=class_name, is_archived=False).order_by("last_name", "first_name")

        # Only generate missing report cards once
        if not ReportCard.objects.filter(term=term, student__in=students).exists():
            from academics.services import generate_class_reports
            generate_class_reports(term.id, class_name, self.request.user)

        report_cards = ReportCard.objects.filter(term=term, student__in=students)
        for rc in report_cards:
            if not rc.is_ecd_report or rc.ecd_template_type != template_type:
                rc.is_ecd_report = True
                rc.ecd_template_type = template_type
                rc.save(update_fields=["is_ecd_report", "ecd_template_type"])

        domains = self.get_flat_domains(template_type)
        domain_groups = self.DOMAIN_GROUPS.get(template_type, [])

        # Fetch existing evaluations
        evaluations = ECDEvaluation.objects.filter(report_card__in=report_cards)
        eval_map = {}  # (report_card_id, domain) -> rating
        for ev in evaluations:
            eval_map[(ev.report_card_id, ev.domain)] = ev.rating

        ctx["students_data"] = []
        for student in students:
            rc = report_cards.filter(student=student).first()
            if not rc:
                continue

            # Build grouped evaluation structure for template
            student_groups = []
            all_rated = True
            for section_title, section_items in domain_groups:
                section_evals = []
                for domain in section_items:
                    rating = eval_map.get((rc.id, domain), "")
                    if not rating:
                        all_rated = False
                    section_evals.append({"domain": domain, "rating": rating})
                student_groups.append({"section": section_title, "items": section_evals})

            # Also flat list for compatibility
            flat_evals = [{"domain": d, "rating": eval_map.get((rc.id, d), "")} for d in domains]

            # ABC Specific Data
            abc_data = {}
            if template_type == "abc":
                abc_data = {
                    "pace_progress": rc.abc_pace_progress.all().order_by("subject", "pace_no"),
                    "scripture": {s.quarter: s.verse for s in rc.abc_scripture.all()},
                    "reading": {r.quarter: r for r in rc.abc_reading.all()},
                    "assignments": {a.quarter: a for a in rc.abc_assignments.all()},
                }

            ctx["students_data"].append({
                "student": student,
                "report_card": rc,
                "evaluations": flat_evals,
                "domain_groups": student_groups,
                "abc": abc_data,
                "is_complete": all_rated and len(rc.teacher_comments or "") >= 50,
            })

        ctx["domains"] = domains
        ctx["domain_groups"] = domain_groups

        return ctx

    def post(self, request, *args, **kwargs):
        term_id = request.POST.get("term")
        class_name = request.POST.get("class_name")
        template_type = request.POST.get("template_type")
        
        if not (term_id and class_name and template_type):
            messages.error(request, "Missing selection parameters.")
            return redirect("academics:ecd_evaluations_entry")

        # Security: Enforce own classes only for teachers
        if request.user.role == UserRole.TEACHER:
            from timetable.models import TimetableSlot
            if not TimetableSlot.objects.filter(teacher=request.user, class_name=class_name).exists():
                messages.error(request, "Access denied: This class is not assigned to you.")
                return redirect("academics:ecd_evaluations_entry")
        # UAT ECD-008: Pre-School formal assessment only in Term 1 and Term 3
        if template_type == "pre_school":
            term_obj = Term.objects.get(pk=term_id) if term_id else None
            if term_obj and "Term 2" in term_obj.name:
                messages.error(request, "Pre-School assessment is only conducted in Term 1 and Term 3 per school policy.")
                return redirect("academics:ecd_evaluations_entry")

        domains = self.get_flat_domains(template_type)
        students = Student.objects.filter(class_name=class_name, is_archived=False)
        report_cards = ReportCard.objects.filter(term_id=term_id, student__in=students)

        updated_count = 0
        for rc in report_cards:
            if rc.status != ReportCardStatus.DRAFT:
                continue

            # Teacher comments
            comment = request.POST.get(f"comment_{rc.id}")
            if comment is not None:
                rc.teacher_comments = comment
                rc.save(update_fields=["teacher_comments"])

            for domain in domains:
                rating = request.POST.get(f"eval_{rc.id}_{domain}")
                if rating:
                    if rating not in ["E", "G", "S", "N"]:
                        messages.error(request, f"Invalid rating '{rating}' for domain '{domain}'. Allowed: E, G, S, N.")
                        continue
                    ECDEvaluation.objects.update_or_create(
                        report_card=rc,
                        domain=domain,
                        defaults={"rating": rating}
                    )

            # ABC Extra Data Saving
            if template_type == "abc":
                from academics.models import ABCPaceProgress, ABCScripture, ABCReadingProgramme, ABCGeneralAssignment
                
                # 1. PACE Progress
                # Expected format: pace_subject_rcid_idx, pace_no_rcid_idx, etc.
                # Simplified for this specific implementation: we'll handle subjects Math, English, Word Building, Science, Social Studies
                subjects = ["Math", "English", "Word Building", "Science", "Social Studies"]
                for sub in subjects:
                    for i in range(1, 5): # Up to 4 PACEs per subject
                        pace_no = request.POST.get(f"pace_no_{rc.id}_{sub}_{i}")
                        if pace_no:
                            ABCPaceProgress.objects.update_or_create(
                                report_card=rc, subject=sub, pace_no=pace_no,
                                defaults={
                                    "sticker_no": request.POST.get(f"pace_stk_{rc.id}_{sub}_{i}", ""),
                                    "status": request.POST.get(f"pace_status_{rc.id}_{sub}_{i}", "in_progress"),
                                    "supervisor_score": request.POST.get(f"pace_s_{rc.id}_{sub}_{i}") or None,
                                    "moderator_score": request.POST.get(f"pace_m_{rc.id}_{sub}_{i}") or None,
                                    "date_completed": request.POST.get(f"pace_date_{rc.id}_{sub}_{i}", ""),
                                }
                            )

                # 2. Scripture, Reading, Assignments (Terms 1-3 — FR-CAL-002)
                for q in range(1, 4):
                    verse = request.POST.get(f"abc_verse_{rc.id}_{q}")
                    if verse is not None:
                        ABCScripture.objects.update_or_create(report_card=rc, quarter=q, defaults={"verse": verse})
                    
                    item_name = request.POST.get(f"abc_assign_item_{rc.id}_{q}")
                    if item_name is not None:
                        ABCGeneralAssignment.objects.update_or_create(
                            report_card=rc, quarter=q, 
                            defaults={
                                "item_name": item_name,
                                "score": request.POST.get(f"abc_assign_score_{rc.id}_{q}") or None
                            }
                        )
                    
                    wpm = request.POST.get(f"abc_read_wpm_{rc.id}_{q}")
                    if wpm is not None:
                        ABCReadingProgramme.objects.update_or_create(
                            report_card=rc, quarter=q,
                            defaults={
                                "wpm": wpm or None,
                                "percentage": request.POST.get(f"abc_read_pct_{rc.id}_{q}") or None,
                                "comprehension_score": request.POST.get(f"abc_read_comp_{rc.id}_{q}") or None,
                            }
                        )

            updated_count += 1

        messages.success(request, f"Saved evaluations for {updated_count} students.")
        return redirect(f"{request.path}?term={term_id}&class_name={class_name}&template_type={template_type}")


# 
# Room / Venue Management
# 

class RoomListView(RoleRequiredMixin, View):
    """List all rooms and handle the Add-new-room form."""
    template_name = "academics/rooms.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER]

    def get(self, request, *args, **kwargs):
        from academics.models import Room
        return render(request, self.template_name, {
            "rooms": Room.objects.all(),
            "edit_room": None,
        })

    def post(self, request, *args, **kwargs):
        from academics.models import Room
        name = request.POST.get("name", "").strip()
        building = request.POST.get("building", "").strip()
        capacity = request.POST.get("capacity") or None
        is_active = request.POST.get("is_active", "1") == "1"

        if not name:
            messages.error(request, "Room name is required.")
            return redirect("academics:rooms")

        _, created = Room.objects.get_or_create(
            name=name,
            defaults={"building": building, "capacity": capacity, "is_active": is_active}
        )
        if created:
            messages.success(request, f'Room "{name}" added successfully.')
        else:
            messages.warning(request, f'A room named "{name}" already exists.')
        return redirect("academics:rooms")


class RoomEditView(RoleRequiredMixin, View):
    """Edit an existing room."""
    template_name = "academics/rooms.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER]

    def get(self, request, pk):
        from academics.models import Room
        room = get_object_or_404(Room, pk=pk)
        return render(request, self.template_name, {
            "rooms": Room.objects.all(),
            "edit_room": room,
        })

    def post(self, request, pk):
        from academics.models import Room
        room = get_object_or_404(Room, pk=pk)
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Room name is required.")
            return redirect("academics:room_edit", pk=pk)

        room.name = name
        room.building = request.POST.get("building", "").strip()
        room.capacity = request.POST.get("capacity") or None
        room.is_active = request.POST.get("is_active", "1") == "1"
        room.save()
        messages.success(request, f'Room "{room.name}" updated.')
        return redirect("academics:rooms")


class RoomDeleteView(RoleRequiredMixin, View):
    """Delete a room (POST only)."""
    allowed_roles = [UserRole.SUPER_ADMIN]

    def post(self, request, pk):
        from academics.models import Room
        room = get_object_or_404(Room, pk=pk)
        name = room.name
        # Check it's not in use by any timetable slot
        if room.timetable_slots.exists():
            messages.error(request, f'"{name}" is assigned to timetable slots and cannot be deleted. Remove those slots first.')
        else:
            room.delete()
            messages.success(request, f'Room "{name}" deleted.')
        return redirect("academics:rooms")

def _export_students_for_user(user):
    """Scope academic CSV export by role (Primary HOD / ECD HOD cannot export other departments)."""
    from students.models import Student

    qs = Student.objects.filter(is_archived=False).order_by("class_name", "last_name")
    role = getattr(user, "role", None)
    if role in (UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL):
        return qs
    if role == UserRole.PRIMARY_HOD:
        names = grade_class_names_for_department(Department.PRIMARY)
        return qs.filter(class_name__in=names)
    if role == UserRole.ECD_HOD:
        names = grade_class_names_for_department(Department.ECD)
        return qs.filter(class_name__in=names)
    return Student.objects.none()


def _ecd_api_class_allowed(request, class_name: str) -> bool:
    if not (class_name or "").strip():
        return False
    role = request.user.role
    if role in (UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN):
        return True
    if role == UserRole.TEACHER:
        from timetable.models import TimetableSlot

        if TimetableSlot.objects.filter(teacher=request.user, class_name=class_name).exists():
            return True
        from core.teacher_context import get_teacher_assigned_classes_from_tca
        return class_name in get_teacher_assigned_classes_from_tca(request.user)
    if role == UserRole.ECD_HOD:
        return class_name in grade_class_names_for_department(Department.ECD)
    return False


def _ecd_api_student_allowed(request, student: Student) -> bool:
    return _ecd_api_class_allowed(request, student.class_name)


class DuplicateCheckView(LoginRequiredMixin, View):
    """AJAX endpoint: check uploaded file hashes against existing attachments.
    
    POST with JSON body: {"hashes": [{"hash": "abc123...", "filename": "file.pdf"}, ...]}
    Returns: {"results": [{"hash": "abc123...", "filename": "file.pdf", "duplicate": true, "existing_name": "file.pdf"}, ...]}
    """
    login_url = "/accounts/login/"

    def post(self, request):
        import json
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        raw_hashes = data.get("hashes", [])
        hashes = [h for h in raw_hashes if isinstance(h, dict) and "hash" in h and "filename" in h]
        if not hashes:
            return JsonResponse({"results": []})

        # Get all existing attachment hashes for the current lesson plan
        # or all attachments if no lesson_plan_id provided
        lesson_plan_id = data.get("lesson_plan_id")
        if lesson_plan_id:
            try:
                lesson_plan_id = int(lesson_plan_id)
            except (TypeError, ValueError):
                lesson_plan_id = None
        from academics.models import LessonPlanAttachment
        
        if lesson_plan_id:
            existing = LessonPlanAttachment.objects.filter(lesson_plan_id=lesson_plan_id)
        else:
            existing = LessonPlanAttachment.objects.all()

        # Compute hashes of existing files
        from academics.validators import _compute_file_hash
        existing_hashes = {}
        for att in existing:
            try:
                h = _compute_file_hash(att.file.path)
                existing_hashes[h] = att.filename
            except (AttributeError, ValueError, OSError):
                # Can't compute hash — skip
                continue

        results = []
        incoming_map = {item["hash"]: item["filename"] for item in hashes if "hash" in item}
        
        for h, fname in incoming_map.items():
            is_dup = h in existing_hashes
            results.append({
                "hash": h,
                "filename": fname,
                "duplicate": is_dup,
                "existing_name": existing_hashes.get(h, ""),
            })

        return JsonResponse({"results": results})


class CambridgeCheckpointEntryView(RoleRequiredMixin, TemplateView):
    """FR-ACAD-005: Teacher/Admin entry form for Cambridge Checkpoint results.
    Grades 6 (Primary Checkpoint) and 7, 8, 9 (Lower Secondary Checkpoint).
    """
    template_name = "academics/cambridge_checkpoint_entry.html"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.ADMIN_OFFICER]

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
        term = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
        academic_year = term.academic_year if term else None
        ctx["current_term"] = term
        ctx["academic_year"] = academic_year

        # Available classes: Grades 6-9 only
        eligible_classes = ["Grade 6", "Grade 7", "Grade 8", "Grade 9"]
        if user.role == UserRole.PRIMARY_HOD:
            from academics.models import Department
            primary_classes = GradeClass.objects.filter(department=Department.PRIMARY).values_list("name", flat=True)
            eligible_classes = [c for c in eligible_classes if c in primary_classes]
        elif user.role == UserRole.TEACHER:
            assigned = get_teacher_assigned_classes(user)
            eligible_classes = [c for c in eligible_classes if c in assigned]

        ctx["eligible_classes"] = eligible_classes

        selected_class = self.request.GET.get("class_name", "").strip()
        ctx["selected_class"] = selected_class

        if selected_class and selected_class in eligible_classes and academic_year:
            subjects = self.CHECKPOINT_SUBJECTS.get(selected_class, ["English", "Mathematics", "Science"])
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
        term = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
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

                if score_val < 0 or score_val > 6:
                    messages.warning(request, f"Score must be 0.0–6.0 for {student.first_name} {student.last_name} in {subj}.")
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


class AcademicReportExportView(RoleRequiredMixin, View):
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD]

    def get(self, request, *args, **kwargs):
        import csv
        from django.db.models import Avg
        from django.http import HttpResponse

        term = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
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


# 
# ECD Assessment API Endpoints (CSRF-protected; send X-CSRFToken from JS)
# 


class ECDStudentsAPIView(LoginRequiredMixin, View):
    """API endpoint to get students for a specific ECD class"""

    def get(self, request):
        if request.user.role not in [
            UserRole.TEACHER,
            UserRole.ECD_HOD,
            UserRole.HEAD_OF_SCHOOL,
            UserRole.SUPER_ADMIN,
        ]:
            return JsonResponse({"error": "Permission denied"}, status=403)

        class_name = request.GET.get("class_name")
        if not class_name:
            return JsonResponse({"error": "Class name required"}, status=400)

        if not _ecd_api_class_allowed(request, class_name):
            return JsonResponse({"error": "Access denied for this class"}, status=403)

        students = (
            Student.objects.filter(class_name=class_name, is_archived=False)
            .order_by("last_name", "first_name")
            .values("id", "first_name", "last_name", "admission_no", "date_of_birth")
        )

        return JsonResponse({"students": list(students)})


class ECDEvaluationAPIView(LoginRequiredMixin, View):
    """API endpoint for ECD evaluation CRUD operations"""

    def get(self, request, student_id=None):
        if request.user.role not in [
            UserRole.TEACHER,
            UserRole.ECD_HOD,
            UserRole.HEAD_OF_SCHOOL,
            UserRole.SUPER_ADMIN,
        ]:
            return JsonResponse({"error": "Permission denied"}, status=403)

        if not student_id:
            return JsonResponse({"error": "Student ID required"}, status=400)

        student = get_object_or_404(Student, id=student_id)

        if not _ecd_api_student_allowed(request, student):
            return JsonResponse({"error": "Access denied for this student"}, status=403)

        tpl = ecd_template_type_from_class_name(student.class_name)
        if not tpl:
            return JsonResponse({"error": "Student is not in a recognised ECD class"}, status=400)
        # UAT ECD-008: Pre-School formal assessment only in Term 1 and Term 3
        if tpl == "pre_school":
            current_term_chk = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
            if current_term_chk and "Term 2" in current_term_chk.name:
                return JsonResponse(
                    {"error": "Pre-School formal assessment is only conducted in Term 1 and Term 3 per school policy."},
                    status=403,
                )

        term = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
        if not term:
            return JsonResponse({"error": "No active term found"}, status=400)

        report_card, created = ReportCard.objects.get_or_create(
            student=student,
            term=term,
            defaults={
                "is_ecd_report": True,
                "ecd_template_type": tpl,
                "generated_by": request.user,
                "status": ReportCardStatus.DRAFT,
            },
        )
        if report_card.ecd_template_type != tpl:
            report_card.is_ecd_report = True
            report_card.ecd_template_type = tpl
            report_card.save(update_fields=["is_ecd_report", "ecd_template_type", "updated_at"])

        report_card.populate_attendance_summary()

        evaluations = ECDEvaluation.objects.filter(report_card=report_card)
        eval_data = {ev.domain: ev.rating for ev in evaluations}

        domains = ECDEvaluationEntryView.get_flat_domains(tpl)

        response_data = {
            "student": {
                "id": student.id,
                "name": f"{student.first_name} {student.last_name}",
                "class_name": student.class_name,
                "admission_no": student.admission_no,
            },
            "report_card_id": report_card.id,
            "ecd_template_type": tpl,
            "evaluations": eval_data,
            "domains": domains,
            "teacher_comments": report_card.teacher_comments or "",
            "ecd_remarks": report_card.ecd_remarks or "",
            "status": report_card.status,            "is_locked": report_card.status != ReportCardStatus.DRAFT,
        }

        # Add ABC specific data
        if tpl == 'abc':
            paces = ABCPaceProgress.objects.filter(report_card=report_card)
            response_data['abc_pace_progress'] = [
                {
                    "subject": p.subject,
                    "pace_no": p.pace_no,
                    "sticker_no": p.sticker_no,
                    "status": p.status,
                    "supervisor_score": float(p.supervisor_score) if p.supervisor_score else None,
                    "moderator_score": float(p.moderator_score) if p.moderator_score else None,
                    "date_completed": p.date_completed
                } for p in paces
            ]
            
            scripture = ABCScripture.objects.filter(report_card=report_card)
            response_data['abc_scripture'] = {s.quarter: s.verse for s in scripture}
            
            reading = ABCReadingProgramme.objects.filter(report_card=report_card)
            response_data['abc_reading'] = {
                r.quarter: {
                    "wpm": r.wpm,
                    "percentage": float(r.percentage) if r.percentage else None,
                    "comprehension_score": float(r.comprehension_score) if r.comprehension_score else None
                } for r in reading
            }
            
            assignments = ABCGeneralAssignment.objects.filter(report_card=report_card)
            response_data['abc_assignments'] = {
                a.quarter: {
                    "item_name": a.item_name,
                    "score": float(a.score) if a.score else None
                } for a in assignments
            }

            # FR-ACAD-013: ABC Class Term 3 internal exam
            internal_exams = ABCInternalExam.objects.filter(report_card=report_card)
            response_data['abc_internal_exams'] = {
                e.subject: e.rating for e in internal_exams
            }

        return JsonResponse(response_data)

    def post(self, request, student_id=None):
        if request.user.role not in [
            UserRole.TEACHER,
            UserRole.ECD_HOD,
            UserRole.HEAD_OF_SCHOOL,
            UserRole.SUPER_ADMIN,
        ]:
            return JsonResponse({"error": "Permission denied"}, status=403)

        if not student_id:
            return JsonResponse({"error": "Student ID required"}, status=400)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        student = get_object_or_404(Student, id=student_id)

        if not _ecd_api_student_allowed(request, student):
            return JsonResponse({"error": "Access denied for this student"}, status=403)

        tpl = ecd_template_type_from_class_name(student.class_name)
        if not tpl:
            return JsonResponse({"error": "Student is not in a recognised ECD class"}, status=400)
        # UAT ECD-008: Pre-School formal assessment only in Term 1 and Term 3
        if tpl == "pre_school":
            current_term_chk = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
            if current_term_chk and "Term 2" in current_term_chk.name:
                return JsonResponse(
                    {"error": "Pre-School formal assessment is only conducted in Term 1 and Term 3 per school policy."},
                    status=403,
                )

        term = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
        if not term:
            return JsonResponse({"error": "No active term found"}, status=400)

        report_card, created = ReportCard.objects.get_or_create(
            student=student,
            term=term,
            defaults={
                "is_ecd_report": True,
                "ecd_template_type": tpl,
                "generated_by": request.user,
                "status": ReportCardStatus.DRAFT,
            },
        )
        if report_card.ecd_template_type != tpl:
            report_card.is_ecd_report = True
            report_card.ecd_template_type = tpl
            report_card.save(update_fields=["is_ecd_report", "ecd_template_type", "updated_at"])

        report_card.populate_attendance_summary()

        # UAT ECD-009: Lock evaluations when report is not in DRAFT status
        if report_card.status != ReportCardStatus.DRAFT:
            return JsonResponse(
                {"error": "This report has already been submitted and is locked for editing."},
                status=403,
            )

        from django.db import transaction
        with transaction.atomic():
            evaluations = data.get("evaluations", {})
        for domain, rating in evaluations.items():
            if rating not in ["E", "G", "S", "N"]:
                return JsonResponse(
                    {"error": f"Invalid rating '{rating}' for domain '{domain}'. Allowed: E, G, S, N."},
                    status=400,
                )
            ECDEvaluation.objects.update_or_create(
                    report_card=report_card,
                    domain=domain,
                    defaults={"rating": rating},
                )

        # Handle ABC specific data in POST
        if tpl == 'abc':
            pace_data = data.get("abc_pace_progress", [])
            for p in pace_data:
                ABCPaceProgress.objects.update_or_create(
                    report_card=report_card,
                    subject=p.get("subject"),
                    pace_no=p.get("pace_no"),
                    defaults={
                        "sticker_no": p.get("sticker_no", ""),
                        "status": p.get("status", "in_progress"),
                        "supervisor_score": p.get("supervisor_score"),
                        "moderator_score": p.get("moderator_score"),
                        "date_completed": p.get("date_completed", "")
                    }
                )
            
            scripture_data = data.get("abc_scripture", {})
            for q, v in scripture_data.items():
                if v:
                    ABCScripture.objects.update_or_create(
                        report_card=report_card,
                        quarter=int(q),
                        defaults={"verse": v}
                    )
            
            reading_data = data.get("abc_reading", {})
            for q, rd in reading_data.items():
                ABCReadingProgramme.objects.update_or_create(
                    report_card=report_card,
                    quarter=int(q),
                    defaults={
                        "wpm": rd.get("wpm"),
                        "percentage": rd.get("percentage"),
                        "comprehension_score": rd.get("comprehension_score")
                    }
                )
            
            assignment_data = data.get("abc_assignments", {})
            for q, ad in assignment_data.items():
                ABCGeneralAssignment.objects.update_or_create(
                    report_card=report_card,
                    quarter=int(q),
                    defaults={
                        "item_name": ad.get("item_name", ""),
                        "score": ad.get("score")
                    }
                )

            # FR-ACAD-013: ABC Class Term 3 internal exam — only save in Term 3
            internal_exam_data = data.get("abc_internal_exams", {})
            if internal_exam_data and term and "Term 3" in (term.name or ""):
                for subject, rating in internal_exam_data.items():
                    if rating in ["E", "G", "S", "N"]:
                        ABCInternalExam.objects.update_or_create(
                            report_card=report_card,
                            subject=subject,
                            defaults={"rating": rating},
                        )

        teacher_comments = data.get("teacher_comments", "")
        if teacher_comments.strip() and len(teacher_comments.strip()) < 50:
            return JsonResponse(
                {"error": "Teacher comment must be at least 50 characters."},
                status=400,
            )
        if len(teacher_comments.strip()) >= 50:
            report_card.teacher_comments = teacher_comments

        ecd_remarks = (data.get("overall_remarks") or "").strip()
        if ecd_remarks:
            report_card.ecd_remarks = ecd_remarks[:50]

        if teacher_comments or ecd_remarks:
            report_card.save(update_fields=["teacher_comments", "ecd_remarks"])

        return JsonResponse({"success": True, "message": "Evaluations saved successfully"})


class ECDSubmissionAPIView(LoginRequiredMixin, View):
    """API endpoint for submitting ECD evaluations for HOD review"""

    def post(self, request, student_id=None):
        if request.user.role not in [
            UserRole.TEACHER,
            UserRole.ECD_HOD,
            UserRole.HEAD_OF_SCHOOL,
            UserRole.SUPER_ADMIN,
        ]:
            return JsonResponse({"error": "Permission denied"}, status=403)

        if not student_id:
            return JsonResponse({"error": "Student ID required"}, status=400)

        student = get_object_or_404(Student, id=student_id)

        if not _ecd_api_student_allowed(request, student):
            return JsonResponse({"error": "Access denied for this student"}, status=403)
        # UAT ECD-008: Pre-School formal assessment only in Term 1 and Term 3
        tpl = ecd_template_type_from_class_name(student.class_name)
        if tpl == "pre_school":
            current_term_chk = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
            if current_term_chk and "Term 2" in current_term_chk.name:
                return JsonResponse(
                    {"error": "Pre-School formal assessment is only conducted in Term 1 and Term 3 per school policy."},
                    status=403,
                )

        term = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
        if not term:
            return JsonResponse({"error": "No active term found"}, status=400)

        report_card = get_object_or_404(ReportCard, student=student, term=term)
        report_card.populate_attendance_summary()

        if report_card.status == ReportCardStatus.PENDING_SIGN_OFF:
            return JsonResponse(
                {"error": "This report has already been submitted for review."},
                status=400,
            )
        if report_card.status == ReportCardStatus.PUBLISHED:
            return JsonResponse(
                {"error": "This report has already been published and cannot be re-submitted."},
                status=400,
            )

        tpl = ecd_template_type_from_class_name(student.class_name) or report_card.ecd_template_type
        if not tpl:
            return JsonResponse({"error": "Student is not in a recognised ECD class"}, status=400)

        if not report_card.teacher_comments or len(report_card.teacher_comments.strip()) < 50:
            return JsonResponse({"error": "Teacher comments must be at least 50 characters long"}, status=400)

        required_domains = ECDEvaluationEntryView.get_flat_domains(tpl)
        existing_evaluations = ECDEvaluation.objects.filter(report_card=report_card).values_list(
            "domain", flat=True
        )

        missing_domains = set(required_domains) - set(existing_evaluations)
        if missing_domains:
            return JsonResponse(
                {"error": f'Missing evaluations for domains: {", ".join(missing_domains)}'},
                status=400,
            )

        report_card.status = ReportCardStatus.PENDING_SIGN_OFF
        report_card.comments_submitted = True
        report_card.save(update_fields=["status", "comments_submitted", "updated_at"])

        # FRD: Notify ECD HOD when evaluations are submitted for review
        try:
            from communications.email_service import dispatch_notification
            from users.models import User
            hod_roles = [UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL]
            hods = User.objects.filter(role__in=hod_roles, is_active=True).distinct()
            for hod in hods:
                dispatch_notification(
                    user=hod,
                    title="ECD evaluations submitted for review",
                    message=(
                        f"{request.user.get_full_name()} has submitted ECD evaluations for "
                        f"{student.first_name} {student.last_name} ({student.class_name}). "
                        f"Please review and approve."
                    ),
                    link="/academics/ecd-assessment/?class_name=" + student.class_name,
                    actor=request.user,
                )
        except Exception:
            pass  # Never block submission on notification failure

        return JsonResponse({"success": True, "message": "Report submitted for HOD review"})


class ECDAssessmentView(RoleRequiredMixin, TemplateView):
    """View for ECD assessment entry page (class from query string; default Pre-Kindergarten)."""

    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]

    def get_template_names(self):
        class_name = (self.request.GET.get("class_name") or "Pre-Kindergarten").strip()
        tpl = ecd_template_type_from_class_name(class_name) or "pre_k"
        return [f"academics/ecd_assessment_{tpl}.html"]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        class_name = (self.request.GET.get("class_name") or "Pre-Kindergarten").strip()
        tpl = ecd_template_type_from_class_name(class_name) or "pre_k"

        # FRD Section 1.4 & 2.3: Defined ECD classes
        ECD_TABS = [
            {"display_name": "Pre-Kindergarten", "class_name": "Pre-Kindergarten"},
            {"display_name": "Kindergarten",     "class_name": "Kindergarten"},
            {"display_name": "Pre-School",       "class_name": "Pre-School"},
            {"display_name": "ABC Class",        "class_name": "ABC Class"},
        ]

        if self.request.user.role == UserRole.TEACHER:
            assigned_tca = get_teacher_assigned_classes_from_tca(self.request.user)
            assigned_class_names = set(assigned_tca.keys())
            available_tabs = [tab for tab in ECD_TABS if tab["class_name"] in assigned_class_names]
            ctx["ecd_tabs"] = available_tabs

            if not available_tabs:
                ctx["no_class_assigned"] = True
            elif class_name not in {tab["class_name"] for tab in available_tabs}:
                class_name = available_tabs[0]["class_name"]
                tpl = ecd_template_type_from_class_name(class_name) or "pre_k"
        else:
            ctx["ecd_tabs"] = ECD_TABS

        # Current term for display and term gating (FR-CAL-002: 3 terms)
        current_term = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
        ctx["current_term"] = current_term
        if current_term:
            try:
                term_num = int(current_term.name.replace("Term ", ""))
            except (ValueError, AttributeError):
                term_num = 1
        else:
            term_num = 1
        ctx["current_term_num"] = term_num

        ctx["ecd_class_name"] = class_name
        ctx["ecd_template_type"] = tpl
        ctx["ecd_class_label"] = class_name
        ctx["page_title"] = f"ECD Reports — {class_name}"
        ctx["domain_groups"] = ECDEvaluationEntryView.DOMAIN_GROUPS.get(tpl, [])

        # Pre-K field groups (label, field_id) matching Hodari_09a mockup
        ctx["pre_k_fields_nc"] = [
            ("Recognises numbers 1–10", "n1"), ("Recites numbers 1–10", "n2"),
            ("Understands size (big / small)", "n3"), ("Identifies colours learned this term", "n4"),
            ("Identifies shapes learned this term", "n5"), ("Sorting shapes & colours", "n6"),
        ]
        ctx["pre_k_fields_cs"] = [
            ("Knows first name", "c1"), ("Responds to direct questions", "c2"),
            ("Expresses needs clearly", "c3"), ("Responds well within a group", "c4"),
            ("Repeats sentences", "c5"),
        ]
        ctx["pre_k_fields_ms"] = [
            ("Can tear paper", "m1"), ("Holds & uses paint brush, pencil, crayon", "m2"),
            ("Can thread beads", "m3"), ("Holds spoon and eats without help", "m4"),
            ("Can jump up and down", "m5"), ("Can throw a ball", "m6"),
            ("Can kick a ball", "m7"), ("Participates in music and movement", "m8"),
        ]
        ctx["pre_k_fields_se"] = [
            ("Enjoys school activities", "s1"), ("Plays and shares well with others", "s2"),
            ("Listens and follows instructions", "s3"),
        ]
        ctx["pre_k_fields_sw"] = [
            ("Getting in the water", "sw1"), ("Hand & leg movement", "sw2"),
            ("Attitude", "sw3"), ("Swimming attendance", "sw4"),
        ]

        # KG sections (Hodari_09b)
        ctx["kg_sections"] = [
            ("Numeracy", [
                ("Number counting 1–100", "kn1"), ("Number formation 0–9", "kn2"),
                ("Number sequence 1–40", "kn3"), ("Number recognition 1–40", "kn4"),
                ("Number value", "kn5"), ("Number tracing", "kn6"),
                ("Basic shapes", "kn7"), ("Problem solving", "kn8"),
                ("Can put together puzzles (critical thinking)", "kn9"),
                ("Ability to do a maze (critical thinking)", "kn10"),
                ("Basic addition", "kn11"), ("Basic subtraction", "kn12"),
            ]),
            ("Reading", [
                ("Recognise, compare & distinguish sounds", "kr1"),
                ("Listening to stories", "kr2"),
                ("Vocabulary, grammar & pronunciation", "kr3"),
                ("Songs and rhymes", "kr4"), ("Comprehension", "kr5"),
                ("Ability to sit still and listen to recall", "kr6"),
                ("Reading / blending two to three sounds", "kr7"),
            ]),
            ("Writing", [
                ("Sound and number formation", "kw1"),
                ("Handwriting neatness and pencil grip", "kw2"),
                ("Writing first name", "kw3"), ("Writing last name", "kw4"),
            ]),
        ]
        ctx["kg_general_sections"] = [
            ("Personal", [
                ("Completes work timely", "gp1"), ("Shows initiative and creativity", "gp2"),
                ("Attentive to direction", "gp3"), ("Works well independently", "gp4"),
                ("Exhibits self-confidence", "gp5"), ("Portrays independence", "gp6"),
                ("Exhibits self-control", "gp7"), ("Considerate of others", "gp8"),
                ("Responds well to correction", "gp9"), ("Cleanliness", "gp10"),
                ("Food appetite", "gp11"),
            ]),
            ("Physical Development", [
                ("Swimming", "ph1"), ("Catch and throw", "ph2"),
                ("Balancing", "ph3"), ("Running", "ph4"),
                ("Kicking", "ph5"), ("Jumping jacks", "ph6"),
            ]),
            ("Artistry", [
                ("Good hand and eye coordination to perform a task", "ar1"),
                ("Participates in music and dance", "ar2"),
                ("Fine motor grip", "ar3"), ("Shows creativity in crafts", "ar4"),
            ]),
        ]

        # Pre-School sections (Hodari_09c mockup labels) — 4-point scale
        ctx["pre_school_sections"] = [
            ("Numeracy", [
                ("Counting 1–10: counting", "ps_n1"),
                ("Counting 1–10: recognition", "ps_n2"),
                ("Counting 1–10: matching", "ps_n3"),
                ("Shapes recognition", "ps_sc1"),
                ("Shapes association", "ps_sc2"),
                ("Colour recognition", "ps_cl1"),
                ("Colour association", "ps_cl2"),
            ]),
            ("Pre-Writing", [
                ("colouring, painting, moulding", "ps_pw1"),
                ("eye-hand coordination", "ps_pw2"),
            ]),
            ("School Readiness", [
                ("Readiness to participate in class activities", "ps_sr1"),
                ("Attention span", "ps_sr2"),
                ("Grade level maturity", "ps_sr3"),
                ("Able to eat on their own", "ps_sr4"),
                ("Able to comprehend and follow instructions", "ps_sr5"),
                ("Able to communicate in comprehensible speech", "ps_sr6"),
                ("Potty trained", "ps_sr7"),
            ]),
            ("Social Skills", [
                ("Willing to share", "ps_ss1"),
                ("Plays well and safely with others", "ps_ss2"),
                ("Attitude towards discipline and correction", "ps_ss3"),
            ]),
        ]

        # ABC sections (FR-ACAD-011)
        ctx["abc_pace_subjects"] = ["Mathematics", "English", "Science", "Social Studies", "Word Building", "Scripture"]
        ctx["abc_fields_assignments"] = [
            ("Completes assignments on time", "abc_a1"),
            ("Quality of homework", "abc_a2"),
            ("Independent work habits", "abc_a3"),
        ]
        ctx["abc_fields_character"] = [
            ("Honesty & integrity", "abc_c1"),
            ("Respect for authority", "abc_c2"),
            ("Kindness to peers", "abc_c3"),
            ("Self-discipline", "abc_c4"),
            ("Attitude toward learning", "abc_c5"),
        ]
        ctx["current_term"] = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
        from django.utils.timezone import localdate
        ctx["today_display"] = localdate().strftime("%b %d, %Y").lstrip("0").replace(" 0", " ")
        ctx["academics_tab"] = "ecd_evaluations"
        return ctx



class ECDProgressAPIView(LoginRequiredMixin, View):
    """API endpoint to get ECD evaluation progress for a class"""

    def get(self, request):
        if request.user.role not in [
            UserRole.TEACHER,
            UserRole.ECD_HOD,
            UserRole.HEAD_OF_SCHOOL,
            UserRole.SUPER_ADMIN,
        ]:
            return JsonResponse({"error": "Permission denied"}, status=403)

        class_name = request.GET.get("class_name")
        if not class_name:
            return JsonResponse({"error": "Class name required"}, status=400)

        if not _ecd_api_class_allowed(request, class_name):
            return JsonResponse({"error": "Access denied for this class"}, status=403)

        tpl = ecd_template_type_from_class_name(class_name)
        if not tpl:
            return JsonResponse({"error": "Not an ECD class"}, status=400)

        term = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
        if not term:
            return JsonResponse({"error": "No active term found"}, status=400)

        students = Student.objects.filter(class_name=class_name, is_archived=False)
        total_students = students.count()

        if total_students == 0:
            return JsonResponse({"progress": {"total": 0, "completed": 0, "percentage": 0}})

        completed_reports = ReportCard.objects.filter(
            student__in=students,
            term=term,
            is_ecd_report=True,
            ecd_template_type=tpl,
            status__in=[ReportCardStatus.PENDING_SIGN_OFF, ReportCardStatus.PUBLISHED],
        ).count()

        percentage = round((completed_reports / total_students) * 100, 1) if total_students > 0 else 0

        return JsonResponse(
            {
                "progress": {
                    "total": total_students,
                    "completed": completed_reports,
                    "percentage": percentage,
                }
            }
        )


class PrimaryCommentEntryView(RoleRequiredMixin, TemplateView):
    """View for Primary teachers to enter narrative comments in bulk."""
    template_name = "academics/primary_comments.html"
    allowed_roles = [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        term = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
        ctx["current_term"] = term
        
        # Filter classes based on role
        if self.request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            ctx["classes"] = sorted(get_teacher_assigned_classes_from_tca(self.request.user))
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
            ctx["student_reports"] = [
                {
                    "student": rc.student,
                    "report": rc,
                    "comment": rc.teacher_comments or ""
                } for rc in report_cards
            ]
            
        return ctx

    def post(self, request, *args, **kwargs):
        term_id = request.POST.get("term_id")
        class_name = request.POST.get("class_name")
        action = request.POST.get("action")
        if not (term_id and class_name):
            messages.error(request, "Missing parameters.")
            return redirect("academics:primary_comments_entry")
        
        # RBAC: TEACHER scoped to assigned classes
        if request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            if class_name not in get_teacher_assigned_classes_from_tca(request.user):
                messages.error(request, "Access denied: This class is not assigned to you.")
                return redirect("academics:primary_comments_entry")
            
        students = Student.objects.filter(class_name=class_name, is_archived=False)
        report_cards = ReportCard.objects.filter(term_id=term_id, student__in=students)
        
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
                from users.models import User, UserRole
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
    allowed_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]

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
        ctx["current_term"] = Term.get_current() or Term.objects.filter(is_locked=False).order_by("-start_date").first()
        
        # Get active exam types for the form
        ctx["exam_types"] = ExamTypeConfiguration.objects.filter(is_active=True).order_by('display_order')
        
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
                subs = set(TimetableSlot.objects.filter(class_name=selected_class).values_list("subject_name", flat=True))
                subs.update(ExamScore.objects.filter(student__class_name=selected_class).values_list("subject_name", flat=True))
                if not subs:
                    from academics.models import Subject
                    subs = set(Subject.objects.filter(department=Department.PRIMARY).values_list("name", flat=True))
                ctx["subjects"] = sorted(list(subs))
                ctx["editable_subjects"] = set(subs)
        
        ctx["academics_tab"] = "primary_assessment"
        return ctx

class PrimaryStudentsAPIView(LoginRequiredMixin, View):
    """API to get student list for Primary focused entry."""
    def get(self, request):
        if request.user.role not in [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]:
            return JsonResponse({"error": "Permission denied"}, status=403)

        class_name = request.GET.get("class_name")
        if not class_name:
            return JsonResponse({"error": "Class name required"}, status=400)
        
        # RBAC: TEACHER scoped to assigned classes
        if request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            if class_name not in get_teacher_assigned_classes_from_tca(request.user):
                return JsonResponse({"error": "Access denied"}, status=403)
        
        students = Student.objects.filter(class_name=class_name, is_archived=False).order_by("last_name", "first_name").values("id", "first_name", "last_name", "admission_no")
        return JsonResponse({"students": list(students)})

class PrimaryScoreAPIView(LoginRequiredMixin, View):
    """API for getting/saving all primary scores for a student in a term."""

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
        
        # structure: { "Subject Name": { "quiz": 85, "mid_term": 70, ... } }
        score_data = {}
        for s in scores:
            if s.subject_name not in score_data:
                score_data[s.subject_name] = {}
            score_data[s.subject_name][s.exam_type] = {
                "score": float(s.score), 
                "is_locked": s.is_locked or (rc_status != ReportCardStatus.DRAFT)
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
            return JsonResponse({"error": "Evaluation is locked (Submitted to HOD)"}, status=403)

        student = get_object_or_404(Student, id=student_id)
        if not self._student_in_teacher_classes(request, student):
            return JsonResponse({"error": "Access denied"}, status=403)
        term = get_object_or_404(Term, id=term_id)

        skipped_subjects = set()

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
        
        from academics.models import ExamTypeConfiguration
        active_configs = {c.code: c for c in ExamTypeConfiguration.objects.filter(is_active=True)}
        
        updated = 0
        for subject_name, subjects_scores in all_subject_scores.items():
            for code, val in subjects_scores.items():
                if code not in active_configs: continue
                if val == "" or val is None: continue
                
                try:
                    score_val = Decimal(str(val))
                    max_score_val = int(active_configs[code].max_score)
                    if score_val < 0 or score_val > max_score_val:
                        return JsonResponse({"error": f"Score {score_val} is out of range (0-{max_score_val})"}, status=400)
                    if score_val != score_val.to_integral_value():
                        return JsonResponse({"error": f"Score {score_val} must be a whole number"}, status=400)
                except (ValueError, DecimalException, KeyError):
                    continue

                # Check for existing individual lock
                existing = ExamScore.objects.filter(
                    student=student, 
                    term=term, 
                    subject_name=subject_name, 
                    exam_type=code
                ).first()
                if existing and existing.is_locked:
                    continue

                score_obj, created = ExamScore.objects.get_or_create(
                    student=student,
                    term=term,
                    subject_name=subject_name,
                    exam_type=code,
                    defaults={
                        "score": score_val,
                        "entered_by": request.user,
                        "exam_type_config": active_configs[code]
                    }
                )
                if not created:
                    score_obj.score = score_val
                    score_obj.entered_by = request.user
                    score_obj.exam_type_config = active_configs[code]
                try:
                    score_obj.full_clean()
                except ValidationError as e:
                    if created:
                        score_obj.delete()
                    return JsonResponse({"error": str(e)}, status=400)
                if not created:
                    score_obj.save(update_fields=["score", "entered_by", "exam_type_config", "updated_at"])
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
                    score_obj.corrected_by = request.user
                    score_obj.correction_reason = "Corrected via primary assessment"
                    score_obj.save(update_fields=["corrected_by_id", "correction_reason", "updated_at"])
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
            
        response_data = {"success": True, "updated": updated}
        if skipped_subjects:
            response_data["warning"] = f"Subjects not in your assignment were skipped: {', '.join(sorted(skipped_subjects))}"
        return JsonResponse(response_data)


class PrimaryBulkSubmissionAPIView(LoginRequiredMixin, View):
    """API for submitting an entire Primary class to HOD for review."""
    def post(self, request):
        if request.user.role not in [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]:
            return JsonResponse({"error": "Permission denied"}, status=403)

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
        students = Student.objects.filter(class_name=class_name, is_archived=False)
        
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
                score_qs = ExamScore.objects.filter(student=student, term=term)
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
        from users.models import User, UserRole
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



# ──────────────────────────────────────────────────────────────
# GradeClass (Class) Management CRUD
# ──────────────────────────────────────────────────────────────

class GradeClassListView(RoleRequiredMixin, View):
    template_name = "academics/classes.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.ADMIN_OFFICER]

    def get(self, request, *args, **kwargs):
        from academics.models import GradeClass, Department
        classes = GradeClass.objects.all().order_by("department", "name")
        return render(request, self.template_name, {"classes": classes, "edit_class": None, "departments": Department.choices})

    def post(self, request, *args, **kwargs):
        from academics.models import GradeClass, Department
        name = request.POST.get("name", "").strip()
        department = request.POST.get("department", "").strip()
        max_capacity_raw = request.POST.get("max_capacity", "").strip()
        if not max_capacity_raw:
            messages.error(request, "Class capacity is required. Enter a positive whole number.")
            return redirect("academics:class_management")
        try:
            max_capacity = int(max_capacity_raw)
            if max_capacity <= 0:
                raise ValueError
        except (TypeError, ValueError):
            messages.error(request, "Class capacity must be a positive whole number.")
            return redirect("academics:class_management")
        _, created = GradeClass.objects.get_or_create(name=name, defaults={"department": department, "max_capacity": max_capacity})
        if created:
            messages.success(request, f'Class "{name}" added successfully.')
        else:
            messages.warning(request, f'A class named "{name}" already exists.')
        return redirect("academics:class_management")


class GradeClassEditView(RoleRequiredMixin, View):
    template_name = "academics/classes.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER]

    def get(self, request, pk):
        from academics.models import GradeClass, Department
        gc = get_object_or_404(GradeClass, pk=pk)
        return render(request, self.template_name, {"classes": GradeClass.objects.all().order_by("department", "name"), "edit_class": gc, "departments": Department.choices})

    def post(self, request, pk):
        from academics.models import GradeClass
        gc = get_object_or_404(GradeClass, pk=pk)
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Class name is required.")
            return redirect("academics:class_edit", pk=pk)
        gc.name = name
        gc.department = request.POST.get("department", gc.department).strip()
        max_capacity_raw = request.POST.get("max_capacity", "").strip()
        if not max_capacity_raw:
            messages.error(request, "Class capacity is required. Enter a positive whole number.")
            return redirect("academics:class_edit", pk=pk)
        try:
            gc.max_capacity = int(max_capacity_raw)
            if gc.max_capacity <= 0:
                raise ValueError
        except (TypeError, ValueError):
            messages.error(request, "Class capacity must be a positive whole number.")
            return redirect("academics:class_edit", pk=pk)
        gc.save()
        messages.success(request, f'Class "{gc.name}" updated.')
        return redirect("academics:class_management")


class GradeClassDeleteView(RoleRequiredMixin, View):
    allowed_roles = [UserRole.SUPER_ADMIN]

    def post(self, request, pk):
        from academics.models import GradeClass
        gc = get_object_or_404(GradeClass, pk=pk)
        name = gc.name
        from students.models import Student
        student_count = Student.objects.filter(class_name=name, is_archived=False).count()
        if student_count > 0:
            messages.error(request, f'"{name}" has {student_count} active student(s) and cannot be deleted. Reassign students first.')
        else:
            gc.delete()
            messages.success(request, f'Class "{name}" deleted.')
        return redirect("academics:class_management")

class SubjectListView(RoleRequiredMixin, TemplateView):
    template_name = "academics/subjects.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # Department filter (optional GET param)
        selected_dept = self.request.GET.get("department", "").strip()
        subjects_qs = Subject.objects.all().order_by("name")
        if selected_dept:
            subjects_qs = subjects_qs.filter(departments__contains=selected_dept)

        ctx["subjects"] = subjects_qs
        ctx["form"] = SubjectForm()
        # Mapping for JS filtering on the form
        ctx["class_depts"] = {str(gc.id): gc.department for gc in GradeClass.objects.all()}
        ctx["selected_dept"] = selected_dept
        ctx["dept_choices"] = Department.choices
        return ctx

    def post(self, request):
        if request.user.role not in self.allowed_roles:
            raise PermissionDenied()
        form = SubjectForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Subject created successfully.")
            return redirect("academics:subjects")
        
        ctx = self.get_context_data()
        ctx["form"] = form
        return render(request, self.template_name, ctx)


class SubjectEditView(RoleRequiredMixin, UpdateView):
    model = Subject
    form_class = SubjectForm
    template_name = "academics/subjects.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD]
    success_url = "/academics/subjects/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["subjects"] = Subject.objects.all().order_by("name")
        ctx["edit_subject"] = self.get_object()
        # Mapping for JS filtering
        ctx["class_depts"] = {str(gc.id): gc.department for gc in GradeClass.objects.all()}
        ctx["dept_choices"] = Department.choices
        ctx["selected_dept"] = self.request.GET.get("department", "").strip()
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Subject updated successfully.")
        return super().form_valid(form)


class SubjectDeleteView(RoleRequiredMixin, View):
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL]

    def post(self, request, pk):
        if request.user.role not in self.allowed_roles:
            raise PermissionDenied()
        subject = get_object_or_404(Subject, pk=pk)
        name = subject.name
        subject.delete()
        messages.success(request, f"Subject '{name}' deleted.")
        return redirect("academics:subjects")


class ReportPDFDownloadView(LoginRequiredMixin, View):
    """Server-side PDF download for report cards.
    Filename pattern: StudentName_Class_Term_AcademicYear_Report.pdf
    Uses WeasyPrint for PDF generation, falling back to HTML if unavailable.
    """
    login_url = "/accounts/login/"

    def get(self, request, pk):
        if request.user.role not in {
            UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN,
            UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.TEACHER,
        }:
            raise PermissionDenied()

        report = get_object_or_404(ReportCard, pk=pk)
        student = report.student

        # Teacher scope check
        if request.user.role == UserRole.TEACHER:
            if student.class_name not in get_teacher_assigned_classes(request.user):
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

        try:
            from weasyprint import HTML
            response = HttpResponse(content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf(response)
            return response
        except (ImportError, OSError):
            # Fallback: WeasyPrint not available (missing package or native deps like GTK)
            # Renders the report card page normally in the browser
            return rendered


class StudentAcademicRecordView(LoginRequiredMixin, TemplateView):
    """Full grade history across all terms for a single student."""
    template_name = "academics/student_academic_record.html"
    login_url = "/accounts/login/"

    def dispatch(self, request, *args, **kwargs):
        if request.user.role not in {
            UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL,
            UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.TEACHER, UserRole.ADMIN_OFFICER,
        }:
            raise PermissionDenied()
        if request.user.role == UserRole.TEACHER:
            from hr.models import TeacherClassAssignment
            from academics.models import Term
            pk = kwargs.get("pk")
            student = get_object_or_404(Student, pk=pk)
            current_term = Term.objects.filter(is_locked=False).order_by("-start_date").first()
            if current_term:
                assigned_names = set(
                    TeacherClassAssignment.objects.filter(
                        teacher__user=request.user,
                        term=current_term,
                    ).values_list("grade_class__name", flat=True)
                )
                if student.class_name not in assigned_names:
                    messages.error(request, "You can only view records for students in your assigned classes.")
                    return redirect("academics:lesson_plans")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        student = get_object_or_404(Student, pk=pk)
        ctx["student"] = student

        import json
        from collections import OrderedDict, defaultdict
        from academics.models import ScoreStatus, ExamTypeConfiguration

        # All report cards for this student, oldest first (for trends)
        reports = list(ReportCard.objects.filter(student=student).select_related(
            "term__academic_year", "signed_off_by"
        ).order_by("term__start_date"))

        ctx["report_count"] = len(reports)

        # Build per-term score breakdowns
        term_data = OrderedDict()
        prev_overall = None

        for rc in reports:
            ecd_rating_map = {"E": 4, "G": 3, "S": 2, "N": 1}
            ecd_rating_rev = {4: "E", 3: "G", 2: "S", 1: "N"}

            if rc.is_ecd_report:
                evaluations = rc.ecd_evaluations.all()
                if not evaluations.exists():
                    continue

                subject_rows = []
                overall_wsum = 0
                overall_tw = 0
                for ev in evaluations:
                    num = ecd_rating_map.get(ev.rating, 0)
                    if num > 0:
                        overall_wsum += num
                        overall_tw += 1
                    subject_rows.append({
                        "name": ev.domain,
                        "weighted_avg": num if num else None,
                        "exam_details": [{"code": "rating", "exam_type": "Rating", "score": ev.rating}],
                    })

                overall_avg = round(overall_wsum / overall_tw, 1) if overall_tw else None
                exam_columns = [("rating", "Rating")]
            else:
                scores_qs = ExamScore.objects.filter(
                    student=student, term=rc.term, status=ScoreStatus.APPROVED
                ).select_related("exam_type_config")

                if not scores_qs.exists():
                    continue

                subjects = OrderedDict()
                for s in scores_qs:
                    key = s.subject_name
                    if key not in subjects:
                        subjects[key] = {}
                    subjects[key][s.exam_type] = s.score

                exam_configs = {
                    et.code: et for et in ExamTypeConfiguration.objects.filter(is_active=True)
                }
                subject_rows = []
                overall_wsum = 0
                overall_tw = 0
                for subj_name, exam_scores in subjects.items():
                    sw = 0
                    stw = 0
                    exam_details = []
                    for et_code, et_obj in exam_configs.items():
                        score_val = exam_scores.get(et_code)
                        if score_val is not None:
                            w = float(et_obj.weight_percentage)
                            sw += float(score_val) * w
                            stw += w
                            exam_details.append({
                                "exam_type": et_obj.name,
                                "code": et_code,
                                "score": score_val,
                            })
                    subj_avg = round(sw / stw, 1) if stw > 0 else None
                    subject_rows.append({
                        "name": subj_name,
                        "weighted_avg": subj_avg,
                        "exam_details": exam_details,
                    })
                    if subj_avg:
                        overall_wsum += subj_avg
                        overall_tw += 1

                overall_avg = round(overall_wsum / overall_tw, 1) if overall_tw > 0 else None

                exam_columns_set = set()
                for srow in subject_rows:
                    for d in srow["exam_details"]:
                        exam_columns_set.add((d["code"], d["exam_type"]))
                exam_columns = sorted(exam_columns_set, key=lambda x: x[0])

            comparison = None
            if prev_overall is not None and overall_avg is not None:
                diff = round(overall_avg - prev_overall, 1)
                comparison = {
                    "diff": diff,
                    "direction": "up" if diff > 1 else ("down" if diff < -1 else "flat"),
                }

            grade = _grade_from_pct(overall_avg) if overall_avg and not rc.is_ecd_report else (ecd_rating_rev.get(round(overall_avg)) if overall_avg else "--")

            term_data[rc.term] = {
                "report": rc,
                "subjects": subject_rows,
                "overall_average": overall_avg,
                "grade": grade,
                "exam_columns": exam_columns,
                "comparison": comparison,
                "is_ecd": rc.is_ecd_report,
            }
            if overall_avg is not None:
                prev_overall = overall_avg

        ctx["term_data"] = term_data

        # --- Analysis & Insights ---
        term_list = list(term_data.values())
        ctx["has_data"] = bool(term_list)

        if term_list:
            first = term_list[0]
            last = term_list[-1]

            first_avg = first["overall_average"]
            last_avg = last["overall_average"]
            trajectory = "stable"
            trajectory_diff = 0
            if first_avg is not None and last_avg is not None:
                trajectory_diff = round(last_avg - first_avg, 1)
                if trajectory_diff > 0.3:
                    trajectory = "improving"
                elif trajectory_diff < -0.3:
                    trajectory = "declining"

            ctx["trajectory"] = trajectory
            ctx["trajectory_diff"] = trajectory_diff
            ctx["first_term_name"] = first["report"].term.name if term_list else ""
            ctx["last_term_name"] = last["report"].term.name if term_list else ""

            current = last
            ctx["current_avg"] = current["overall_average"]
            ctx["current_grade"] = current["grade"]
            ctx["is_ecd"] = current["is_ecd"]

            if current["subjects"]:
                sorted_subjects = sorted(
                    [s for s in current["subjects"] if s["weighted_avg"] is not None],
                    key=lambda x: x["weighted_avg"], reverse=True
                )
                if sorted_subjects:
                    ctx["best_subject"] = sorted_subjects[0]
                    ctx["worst_subject"] = sorted_subjects[-1]

            if len(term_list) >= 2:
                subject_trends = {}
                for td in term_list:
                    for s in td["subjects"]:
                        if s["name"] not in subject_trends:
                            subject_trends[s["name"]] = []
                        subject_trends[s["name"]].append({
                            "term": td["report"].term,
                            "avg": s["weighted_avg"],
                        })

                improving_subjects = []
                declining_subjects = []
                for subj_name, points in subject_trends.items():
                    if len(points) >= 2:
                        first_s = points[0]["avg"]
                        last_s = points[-1]["avg"]
                        if first_s is not None and last_s is not None:
                            sd = round(last_s - first_s, 1)
                            if sd > 0.5:
                                improving_subjects.append({"name": subj_name, "diff": sd})
                            elif sd < -0.5:
                                declining_subjects.append({"name": subj_name, "diff": sd})

                ctx["improving_subjects"] = sorted(improving_subjects, key=lambda x: -x["diff"])
                ctx["declining_subjects"] = sorted(declining_subjects, key=lambda x: x["diff"])

            total_present = sum(
                td["report"].attendance_days_present for td in term_list if td["report"].attendance_days_present
            )
            total_absent = sum(
                td["report"].attendance_days_absent for td in term_list if td["report"].attendance_days_absent
            )
            total_late = sum(
                td["report"].attendance_days_late for td in term_list if td["report"].attendance_days_late
            )
            att_total = total_present + total_absent + total_late
            ctx["att_summary"] = {
                "present": total_present,
                "absent": total_absent,
                "late": total_late,
                "rate": round((total_present + total_late) / att_total * 100, 1) if att_total else None,
            }

            grade_counts = {}
            for td in term_list:
                g = td["grade"]
                grade_counts[g] = grade_counts.get(g, 0) + 1
            ctx["grade_distribution"] = grade_counts

            all_avgs = [td["overall_average"] for td in term_list if td["overall_average"] is not None]
            ctx["lifetime_avg"] = round(sum(all_avgs) / len(all_avgs), 1) if all_avgs else None

            # Standard deviation (grade stability)
            if len(all_avgs) >= 2:
                mean = sum(all_avgs) / len(all_avgs)
                variance = sum((x - mean) ** 2 for x in all_avgs) / len(all_avgs)
                ctx["std_dev"] = round(variance ** 0.5, 1)
            else:
                ctx["std_dev"] = None

            # Subject count
            ctx["subject_count"] = len(current["subjects"]) if term_list else 0

        # Group by academic year for tabs
        year_groups = defaultdict(list)
        for term_obj, td in term_data.items():
            year_groups[term_obj.academic_year].append({
                "data": td,
                "term": term_obj,
            })
        ctx["year_groups"] = [
            {
                "year": year,
                "terms": sorted(terms, key=lambda x: x["term"].start_date or ""),
            }
            for year, terms in sorted(year_groups.items(), key=lambda x: x[0].name)
        ]

        # Chart data as JSON
        chart_points = []
        for term_obj, td in term_data.items():
            if td["overall_average"] is not None:
                chart_points.append({
                    "label": f"{term_obj.academic_year.name[:4]} {term_obj.name}",
                    "value": td["overall_average"],
                    "year": term_obj.academic_year.name,
                })
        ctx["chart_data_json"] = json.dumps(chart_points)

        return ctx


def _grade_from_pct(pct):
    if pct is None:
        return "--"
    if pct >= 80:
        return "A"
    if pct >= 70:
        return "B"
    if pct >= 60:
        return "C"
    if pct >= 50:
        return "D"
    return "E"


class ProgressionConfigListView(RoleRequiredMixin, View):
    """List/create progression configs for a year-end cycle."""
    template_name = "academics/progression_config_list.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER]

    def get(self, request, *args, **kwargs):
        configs = ProgressionConfig.objects.select_related(
            "academic_year_from", "academic_year_to", "created_by"
        ).all().order_by("-created_at")
        years = AcademicYear.objects.all().order_by("name")

        # Detect cases stuck because no HOD is assigned for their department
        from users.models import User
        dept_role_map = {
            "PRIMARY": UserRole.PRIMARY_HOD,
            "ECD": UserRole.ECD_HOD,
            "LOWER_SECONDARY": UserRole.LOWER_SECONDARY_HOD,
        }
        unreachable_warnings = []
        for dept_name, hod_role in dept_role_map.items():
            has_hod = User.objects.filter(role=hod_role, is_active=True).exists()
            if not has_hod:
                grade_names = GradeClass.objects.filter(
                    department=dept_name,
                ).values_list("name", flat=True)
                stuck_count = ProgressionCase.objects.filter(
                    student__class_name__in=list(grade_names),
                    status__in=[ProgressionStatus.CALCULATED, ProgressionStatus.PENDING_HOD_REVIEW],
                ).count()
                if stuck_count > 0:
                    unreachable_warnings.append({
                        "department_label": Department(dept_name).label,
                        "department": dept_name,
                        "count": stuck_count,
                    })

        return render(request, self.template_name, {
            "configs": configs,
            "years": years,
            "unreachable_warnings": unreachable_warnings,
        })

    def post(self, request, *args, **kwargs):
        year_from_id = request.POST.get("academic_year_from")
        year_to_id = request.POST.get("academic_year_to")
        if not year_from_id or not year_to_id:
            messages.error(request, "Both source and target academic years are required.")
            return redirect("academics:progression_config_list")
        try:
            min_avg = float(request.POST.get("minimum_average", 50))
            min_att = float(request.POST.get("minimum_attendance", 80))
            ret_thresh = float(request.POST.get("retention_threshold", 50))
        except (TypeError, ValueError):
            messages.error(request, "Numeric values are required for thresholds.")
            return redirect("academics:progression_config_list")
        year_from = get_object_or_404(AcademicYear, pk=year_from_id)
        year_to = get_object_or_404(AcademicYear, pk=year_to_id)
        if ProgressionConfig.objects.filter(
            academic_year_from=year_from, academic_year_to=year_to
        ).exists():
            messages.error(request, "A configuration for this year pair already exists.")
            return redirect("academics:progression_config_list")
        config = ProgressionConfig.objects.create(
            academic_year_from=year_from,
            academic_year_to=year_to,
            minimum_average=min_avg,
            minimum_attendance=min_att,
            retention_threshold=ret_thresh,
            created_by=request.user,
        )
        log_event(
            actor=request.user,
            action_type="PROGRESSION_CONFIG_CREATED",
            model_name="ProgressionConfig",
            object_id=config.pk,
            description=f"Created progression config: {year_from} → {year_to} (avg≥{min_avg}%, att≥{min_att}%, ret<{ret_thresh}%)",
            request=request,
        )
        messages.success(request, f"Progression config created: {year_from} → {year_to}")
        return redirect("academics:progression_config_list")


class ProgressionConfigEditView(RoleRequiredMixin, View):
    """Edit an existing progression config. Warns if cases exist under old thresholds."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER]

    def post(self, request, pk):
        config = get_object_or_404(ProgressionConfig, pk=pk)
        old_avg = config.minimum_average
        old_att = config.minimum_attendance
        old_ret = config.retention_threshold
        try:
            config.minimum_average = float(request.POST.get("minimum_average", config.minimum_average))
            config.minimum_attendance = float(request.POST.get("minimum_attendance", config.minimum_attendance))
            config.retention_threshold = float(request.POST.get("retention_threshold", config.retention_threshold))
        except (TypeError, ValueError):
            messages.error(request, "Numeric values are required for thresholds.")
            return redirect("academics:progression_config_list")
        config.save()

        thresholds_changed = (
            config.minimum_average != old_avg
            or config.minimum_attendance != old_att
            or config.retention_threshold != old_ret
        )

        if thresholds_changed and config.cases.exists():
            calculated_count = config.cases.filter(status=ProgressionStatus.CALCULATED).count()
            if calculated_count:
                messages.warning(
                    request,
                    f"Thresholds updated. {calculated_count} case(s) still in 'calculated' status "
                    f"were calculated under the old thresholds. Click 'Recalculate' to re-run "
                    f"only those cases. Cases past calculated status are not affected.",
                )
            else:
                messages.info(request, "Thresholds updated. All cases are already past calculated status.")
        else:
            messages.success(request, "Progression config updated.")
        return redirect("academics:progression_config_list")


class ProgressionConfigDeleteView(RoleRequiredMixin, View):
    """Delete a progression config (only if no cases exist)."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL]

    def post(self, request, pk):
        config = get_object_or_404(ProgressionConfig, pk=pk)
        if config.cases.exists():
            messages.error(request, "Cannot delete — cases have already been calculated. Archive the config instead.")
            return redirect("academics:progression_config_list")
        log_event(
            actor=request.user,
            action_type="PROGRESSION_CONFIG_DELETED",
            model_name="ProgressionConfig",
            object_id=pk,
            description=f"Deleted progression config: {config.academic_year_from} → {config.academic_year_to}",
            request=request,
        )
        config.delete()
        messages.success(request, "Progression config deleted.")
        return redirect("academics:progression_config_list")


class ProgressionCaseCalculateView(RoleRequiredMixin, View):
    """Triggers Phase Two recalculation for a given config asynchronously.

    Pre-computes scope counts before queuing so the admin sees how many cases
    will be affected. Actual calculation runs via Celery to prevent HTTP timeouts
    at scale (see Item 9). Only cases still in 'calculated' status are touched.
    """
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER]

    def post(self, request, config_id):
        config = get_object_or_404(ProgressionConfig, pk=config_id)
        from academics.models import RecalcStatus
        config.recalc_status = RecalcStatus.QUEUED
        config.recalc_completed_count = None
        config.recalc_failed_details = []
        config.save(update_fields=["recalc_status", "recalc_completed_count", "recalc_failed_details"])

        existing_calculated = config.cases.filter(status=ProgressionStatus.CALCULATED).count()
        existing_non_calc = config.cases.exclude(status=ProgressionStatus.CALCULATED).count()

        from academics.tasks import calculate_progression_cases_task
        task = calculate_progression_cases_task.delay(
            config_id=config.pk,
            triggered_by_id=request.user.pk,
            recalculate=True,
        )

        log_event(
            actor=request.user,
            action_type="PROGRESSION_CALCULATION_QUEUED",
            model_name="ProgressionConfig",
            object_id=config_id,
            description=f"Async recalculation queued (task {task.id}) — "
                        f"{existing_calculated} calculated case(s) will be updated, "
                        f"{existing_non_calc} case(s) at other statuses left untouched "
                        f"({config.academic_year_from} → {config.academic_year_to})",
            request=request,
        )

        if existing_non_calc:
            messages.success(
                request,
                f"Recalculation queued (task {task.id}). When complete, "
                f"{existing_calculated} calculated case(s) will be updated; "
                f"{existing_non_calc} case(s) at other statuses left untouched.",
            )
        else:
            messages.success(
                request,
                f"Recalculation queued (task {task.id}). "
                f"All {existing_calculated} calculated case(s) will be updated.",
            )

        return redirect("academics:progression_config_list")


# ---------------------------------------------------------------------------
# Phase Three — HOD Review & HOS Decision screens
# ---------------------------------------------------------------------------

class ProgressionHODReviewListView(RoleRequiredMixin, TemplateView):
    """HOD reviews retention candidates for their department."""
    template_name = "academics/progression_hod_review.html"
    allowed_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    page_size = 50

    def _hod_department(self, user):
        if user.role == UserRole.PRIMARY_HOD:
            return "PRIMARY"
        if user.role == UserRole.ECD_HOD:
            return "ECD"
        if user.role == UserRole.LOWER_SECONDARY_HOD:
            return "LOWER_SECONDARY"
        return None  # HOS/Super Admin see all

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        config_id = self.request.GET.get("config_id")
        search_q = self.request.GET.get("q", "").strip()
        filter_class = self.request.GET.get("class", "").strip()
        page = int(self.request.GET.get("page", 1))
        config = None
        cases_page = None
        departments = []
        class_list = []

        if config_id:
            config = get_object_or_404(ProgressionConfig, pk=config_id)
            dept = self._hod_department(self.request.user)
            base = ProgressionCase.objects.filter(progression_config=config).select_related(
                "student", "hod_recommended_by", "hos_decided_by",
            )
            if dept:
                grade_names = GradeClass.objects.filter(
                    department__iexact=dept
                ).values_list("name", flat=True)
                class_list = sorted(grade_names)
                base = base.filter(student__class_name__in=list(grade_names))
            else:
                class_list = sorted(GradeClass.objects.values_list("name", flat=True))

            if search_q and len(search_q) >= 2:
                from django.db.models import Q
                base = base.filter(
                    Q(student__first_name__icontains=search_q) |
                    Q(student__last_name__icontains=search_q) |
                    Q(student__admission_no__icontains=search_q)
                )
            if filter_class:
                base = base.filter(student__class_name=filter_class)

            total_count = base.count()
            base = base.order_by("-created_at")

            from django.core.paginator import Paginator
            paginator = Paginator(base, self.page_size)
            cases_page = paginator.get_page(page)
        else:
            departments = [Department.ECD, Department.PRIMARY, Department.LOWER_SECONDARY]

        ctx.update({
            "config": config,
            "cases_page": cases_page,
            "total_count": cases_page.paginator.count if cases_page else 0,
            "configs": ProgressionConfig.objects.all().order_by("-created_at"),
            "selected_config_id": config_id,
            "departments": departments,
            "class_list": class_list,
            "search_q": search_q,
            "filter_class": filter_class,
            "academics_tab": "progression",
            "ProgressionOutcome": ProgressionOutcome,
        })
        return ctx


class ProgressionHODReviewActionView(RoleRequiredMixin, View):
    """POST endpoint for HOD to recommend an outcome."""
    allowed_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]

    def post(self, request, case_id):
        case = get_object_or_404(ProgressionCase, pk=case_id)

        # HODs may only act on cases within their own department
        hod_dept = None
        if request.user.role == UserRole.PRIMARY_HOD:
            hod_dept = "PRIMARY"
        elif request.user.role == UserRole.ECD_HOD:
            hod_dept = "ECD"
        elif request.user.role == UserRole.LOWER_SECONDARY_HOD:
            hod_dept = "LOWER_SECONDARY"
        if hod_dept:
            grade_names = GradeClass.objects.filter(
                department=hod_dept
            ).values_list("name", flat=True)
            if case.student.class_name not in list(grade_names):
                log_event(
                    actor=request.user,
                    action_type="HOD_CROSS_DEPT_DENIED",
                    model_name="ProgressionCase",
                    object_id=case.pk,
                    description=(
                        f"HOD {request.user} (role={request.user.role}) denied access to "
                        f"case {case.pk} for {case.student} — student class "
                        f"'{case.student.class_name}' is not in department {hod_dept}"
                    ),
                    request=request,
                )
                raise PermissionDenied("You do not have access to cases outside your department.")

        if case.status not in (ProgressionStatus.CALCULATED, ProgressionStatus.PENDING_HOD_REVIEW):
            messages.error(request, "This case is not awaiting HOD review.")
            return redirect(self._redirect_url(case))

        recommendation = request.POST.get("recommendation")
        if recommendation not in ProgressionOutcome.values:
            messages.error(request, "Invalid recommendation.")
            return redirect(self._redirect_url(case))

        override_reason = request.POST.get("override_reason", "")
        if recommendation != case.system_suggested_outcome and not override_reason.strip():
            messages.error(request, "Override reason is required when outcome differs from system suggestion.")
            return redirect(self._redirect_url(case))

        old_recommendation = case.hod_recommendation
        old_recommended_by = case.hod_recommended_by

        case.hod_recommendation = recommendation
        case.hod_recommended_by = request.user
        case.hod_recommended_at = timezone.now()

        # Item 6: When HOD resubmits after a return, preserve previous recommendation history
        if case.hos_return_comment and old_recommendation and old_recommendation != recommendation:
            prev_line = f"Previous: {ProgressionOutcome(old_recommendation).label}"
            if old_recommended_by:
                prev_line += f" (by {old_recommended_by.get_full_name()})"
            now_line = f"Now: {ProgressionOutcome(recommendation).label}"
            if override_reason.strip():
                case.override_reason = f"{prev_line}\n{now_line}\nReason: {override_reason.strip()}"
            else:
                case.override_reason = f"{prev_line}\n{now_line}"
        else:
            case.override_reason = override_reason.strip()

        case.status = ProgressionStatus.PENDING_HOS_DECISION
        case.save()

        log_event(
            actor=request.user,
            action_type="HOD_RECOMMENDATION",
            model_name="ProgressionCase",
            object_id=case.pk,
            description=f"HOD recommended {ProgressionOutcome(recommendation).label} for {case.student} "
                        f"(system suggested: {case.get_system_suggested_outcome_display() or 'none'})",
            request=request,
        )
        messages.success(request, f"HOD recommendation recorded: {case.student} → {ProgressionOutcome(recommendation).label}")
        return redirect(self._redirect_url(case))

    def _redirect_url(self, case):
        return reverse("academics:progression_hod_review") + f"?config_id={case.progression_config_id}"


class ProgressionHODBulkApproveView(RoleRequiredMixin, View):
    """Bulk approve promote for selected eligible cases (system_suggested_outcome is promote)."""
    allowed_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]

    def post(self, request):
        config_id = request.POST.get("config_id")
        if not config_id:
            messages.error(request, "No configuration selected.")
            return redirect("academics:progression_hod_review")
        config = get_object_or_404(ProgressionConfig, pk=config_id)

        case_ids = request.POST.getlist("case_ids")
        if not case_ids:
            messages.warning(request, "No cases selected for bulk approval.")
            return redirect(reverse("academics:progression_hod_review") + f"?config_id={config_id}")

        dept = None
        if request.user.role == UserRole.PRIMARY_HOD:
            dept = "PRIMARY"
        elif request.user.role == UserRole.ECD_HOD:
            dept = "ECD"
        elif request.user.role == UserRole.LOWER_SECONDARY_HOD:
            dept = "LOWER_SECONDARY"

        selected = ProgressionCase.objects.filter(pk__in=case_ids, progression_config=config)
        if dept:
            grade_names = GradeClass.objects.filter(department__iexact=dept).values_list("name", flat=True)
            selected = selected.filter(student__class_name__in=list(grade_names))

        rejected = []
        count = 0
        for case in selected.iterator():
            if case.system_suggested_outcome != ProgressionOutcome.PROMOTE or \
               case.status != ProgressionStatus.PENDING_HOD_REVIEW:
                rejected.append(str(case.pk))
                continue
            case.hod_recommendation = ProgressionOutcome.PROMOTE
            case.hod_recommended_by = request.user
            case.hod_recommended_at = timezone.now()
            case.status = ProgressionStatus.PENDING_HOS_DECISION
            case.save()
            log_event(
                actor=request.user,
                action_type="HOD_BULK_APPROVE",
                model_name="ProgressionCase",
                object_id=case.pk,
                description=f"HOD bulk-approved promote for {case.student}",
                request=request,
            )
            count += 1

        msg = f"Bulk approved promote for {count} student(s)."
        if rejected:
            msg += f" {len(rejected)} ineligible case(s) skipped (not promote-suggested or wrong status)."
        messages.success(request, msg)
        return redirect(reverse("academics:progression_hod_review") + f"?config_id={config_id}")


class ProgressionHOSDecisionListView(RoleRequiredMixin, TemplateView):
    """HOS reviews and makes final decisions on progression cases."""
    template_name = "academics/progression_hos_decision.html"
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    page_size = 50

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        config_id = self.request.GET.get("config_id")
        search_q = self.request.GET.get("q", "").strip()
        filter_class = self.request.GET.get("class", "").strip()
        page = int(self.request.GET.get("page", 1))
        config = None
        cases_page = None
        class_list = []

        if config_id:
            config = get_object_or_404(ProgressionConfig, pk=config_id)
            base = ProgressionCase.objects.filter(
                progression_config=config,
                status=ProgressionStatus.PENDING_HOS_DECISION,
            ).select_related(
                "student", "hod_recommended_by",
            )
            class_list = sorted(
                base.values_list("student__class_name", flat=True).distinct()
            )

            if search_q and len(search_q) >= 2:
                from django.db.models import Q
                base = base.filter(
                    Q(student__first_name__icontains=search_q) |
                    Q(student__last_name__icontains=search_q) |
                    Q(student__admission_no__icontains=search_q)
                )
            if filter_class:
                base = base.filter(student__class_name=filter_class)

            total_count = base.count()
            base = base.order_by("-created_at")

            from django.core.paginator import Paginator
            paginator = Paginator(base, self.page_size)
            cases_page = paginator.get_page(page)
        else:
            configs_with_pending = ProgressionConfig.objects.filter(
                cases__status=ProgressionStatus.PENDING_HOS_DECISION
            ).distinct().order_by("-created_at")
            ctx["configs_with_pending"] = configs_with_pending

        ctx.update({
            "config": config,
            "cases_page": cases_page,
            "total_count": cases_page.paginator.count if cases_page else 0,
            "configs": ProgressionConfig.objects.all().order_by("-created_at"),
            "selected_config_id": config_id,
            "class_list": class_list,
            "search_q": search_q,
            "filter_class": filter_class,
            "academics_tab": "progression",
            "ProgressionOutcome": ProgressionOutcome,
        })
        return ctx


class ProgressionHOSDecisionActionView(RoleRequiredMixin, View):
    """POST endpoint for HOS to make a final decision or return to HOD."""
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]

    def post(self, request, case_id):
        case = get_object_or_404(ProgressionCase, pk=case_id)
        if case.status != ProgressionStatus.PENDING_HOS_DECISION:
            messages.error(request, "This case is not awaiting HOS decision.")
            return redirect(self._redirect_url(case))

        action = request.POST.get("action", "decide")

        if action == "return_to_hod":
            comment = request.POST.get("return_comment", "").strip()
            if not comment:
                messages.error(request, "A written comment is required when returning a case to HOD.")
                return redirect(self._redirect_url(case))
            old_recommendation = case.hod_recommendation
            case.hos_return_comment = comment
            # Retain hod_recommendation so HOD can see their previous recommendation
            case.status = ProgressionStatus.PENDING_HOD_REVIEW
            case.save()

            log_event(
                actor=request.user,
                action_type="HOS_RETURNED_TO_HOD",
                model_name="ProgressionCase",
                object_id=case.pk,
                description=f"HOS returned case for {case.student} to HOD. Comment: {comment}",
                request=request,
            )
            messages.success(request, f"Case returned to HOD for {case.student}.")
            return redirect(self._redirect_url(case))

        # Normal final decision path
        decision = request.POST.get("decision")
        if decision not in ProgressionOutcome.values:
            messages.error(request, "Invalid decision.")
            return redirect(self._redirect_url(case))

        override_reason = request.POST.get("override_reason", "")
        ref_field = case.hod_recommendation if case.hod_recommendation else case.system_suggested_outcome
        if decision != ref_field and not override_reason.strip():
            messages.error(request, "Override reason is required when decision differs from HOD recommendation.")
            return redirect(self._redirect_url(case))

        case.hos_decision = decision
        case.hos_decided_by = request.user
        case.hos_decided_at = timezone.now()
        if override_reason.strip():
            existing = case.override_reason or ""
            if existing.strip():
                case.override_reason = f"{existing}\nHOS override: {override_reason.strip()}"
            else:
                case.override_reason = override_reason.strip()
        case.status = ProgressionStatus.FINALIZED
        case.save()

        log_event(
            actor=request.user,
            action_type="HOS_DECISION",
            model_name="ProgressionCase",
            object_id=case.pk,
            description=f"HOS decided {ProgressionOutcome(decision).label} for {case.student} "
                        f"(HOD recommended: {case.get_hod_recommendation_display() or 'none'})",
            request=request,
        )
        messages.success(request, f"HOS decision recorded: {case.student} → {ProgressionOutcome(decision).label}")
        return redirect(self._redirect_url(case))

    def _redirect_url(self, case):
        return reverse("academics:progression_hos_decision") + f"?config_id={case.progression_config_id}"


class ProgressionHOSBulkDecideView(RoleRequiredMixin, View):
    """HOS bulk-decide promote for selected PENDING_HOS_DECISION cases with hod_recommendation=promote."""
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]

    def post(self, request):
        config_id = request.POST.get("config_id")
        if not config_id:
            messages.error(request, "No configuration selected.")
            return redirect("academics:progression_hos_decision")
        config = get_object_or_404(ProgressionConfig, pk=config_id)

        case_ids = request.POST.getlist("case_ids")
        if not case_ids:
            messages.warning(request, "No cases selected for bulk decision.")
            return redirect(reverse("academics:progression_hos_decision") + f"?config_id={config_id}")

        selected = ProgressionCase.objects.filter(pk__in=case_ids, progression_config=config)

        rejected = []
        count = 0
        for case in selected.iterator():
            if case.status != ProgressionStatus.PENDING_HOS_DECISION or \
               case.hod_recommendation != ProgressionOutcome.PROMOTE:
                rejected.append(str(case.pk))
                continue
            case.hos_decision = ProgressionOutcome.PROMOTE
            case.hos_decided_by = request.user
            case.hos_decided_at = timezone.now()
            case.status = ProgressionStatus.FINALIZED
            case.save()
            log_event(
                actor=request.user,
                action_type="HOS_BULK_DECISION",
                model_name="ProgressionCase",
                object_id=case.pk,
                description=f"HOS bulk-decided promote for {case.student}",
                request=request,
            )
            _notify_finalized_case(case, config, request.user)
            count += 1

        msg = f"HOS bulk-decided promote for {count} student(s)."
        if rejected:
            msg += f" {len(rejected)} ineligible case(s) skipped (not awaiting HOS decision or not promote-recommended)."
        messages.success(request, msg)
        return redirect(reverse("academics:progression_hos_decision") + f"?config_id={config_id}")


# ---------------------------------------------------------------------------
# Item 2 — Teacher read-only progression view
# ---------------------------------------------------------------------------

class ProgressionTeacherView(RoleRequiredMixin, TemplateView):
    """Read-only view for teachers to see their own class progression cases.

    Only exposes: calculated_average, system_suggested_outcome, status.
    Never exposes: hod_recommendation, override_reason, hos_decision, calculation_basis.
    """
    template_name = "academics/progression_teacher_view.html"
    allowed_roles = [UserRole.TEACHER, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        config_id = self.request.GET.get("config_id")

        if user.role in (UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN):
            assigned_class_names = list(GradeClass.objects.values_list("name", flat=True))
        else:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            assigned = get_teacher_assigned_classes_from_tca(user)
            assigned_class_names = list(assigned.keys())

        config = None
        cases_data = []

        if config_id:
            config = get_object_or_404(ProgressionConfig, pk=config_id)
            cases = ProgressionCase.objects.filter(
                progression_config=config,
                student__class_name__in=assigned_class_names,
            ).select_related("student").order_by("student__last_name", "student__first_name")

            for case in cases:
                cases_data.append({
                    "student_name": case.student.get_full_name(),
                    "admission_no": case.student.admission_no,
                    "class_name": case.student.class_name,
                    "calculated_average": case.calculated_average,
                    "calculated_attendance_rate": case.calculated_attendance_rate,
                    "system_suggested_outcome": case.system_suggested_outcome,
                    "system_suggested_outcome_label": case.get_system_suggested_outcome_display(),
                    "status": case.status,
                    "status_label": case.get_status_display(),
                })

        ctx.update({
            "config": config,
            "cases_data": cases_data,
            "configs": ProgressionConfig.objects.all().order_by("-created_at"),
            "selected_config_id": config_id,
            "academics_tab": "progression_teacher",
        })
        return ctx


# ---------------------------------------------------------------------------
# Item 3 — Parent finalized-outcome view
# ---------------------------------------------------------------------------

class ProgressionParentView(RoleRequiredMixin, TemplateView):
    """Parent sees finalized outcome for their children only.

    Multi-child support: ?student=N to select a specific child.
    Only exposes: outcome_label, new_class, student basics.
    Never exposes: hod_recommendation, override_reason, calculation_basis.
    """
    template_name = "academics/progression_parent_view.html"
    allowed_roles = [UserRole.PARENT, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        config_id = self.request.GET.get("config_id")
        selected_student_id = self.request.GET.get("student")

        if user.role in (UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN):
            children = Student.objects.filter(is_archived=False)
        else:
            from students.models import ParentGuardian, StudentGuardian
            guardian = ParentGuardian.objects.filter(email=user.email).first()
            if guardian:
                child_ids = StudentGuardian.objects.filter(
                    guardian=guardian
                ).values_list("student_id", flat=True)
                children = Student.objects.filter(pk__in=child_ids, is_archived=False)
            else:
                children = Student.objects.none()

        selected_student = None
        if selected_student_id:
            selected_student = children.filter(id=selected_student_id).first()
        if not selected_student and children:
            selected_student = children.first()

        config = None
        results = []

        if config_id and selected_student:
            config = get_object_or_404(ProgressionConfig, pk=config_id)
            cases = ProgressionCase.objects.filter(
                progression_config=config,
                student=selected_student,
                status=ProgressionStatus.FINALIZED,
            ).select_related("student")

            for case in cases:
                new_class = ""
                if case.hos_decision == ProgressionOutcome.PROMOTE:
                    grade_order = list(
                        GradeClass.objects.filter().order_by("sort_order", "name").values_list("name", flat=True)
                    )
                    grade_index = {name: i for i, name in enumerate(grade_order)}
                    idx = grade_index.get(case.student.class_name)
                    if idx is not None and idx + 1 < len(grade_order):
                        new_class = grade_order[idx + 1]
                elif case.hos_decision == ProgressionOutcome.RETAIN:
                    new_class = case.student.class_name + " (repeat)"
                elif case.hos_decision == ProgressionOutcome.GRADUATE:
                    new_class = "Graduated"

                results.append({
                    "student_name": case.student.get_full_name(),
                    "admission_no": case.student.admission_no,
                    "class_name": case.student.class_name,
                    "outcome_label": case.get_hos_decision_display(),
                    "new_class": new_class,
                })

        ctx.update({
            "config": config,
            "results": results,
            "children": children,
            "selected_student": selected_student,
            "configs": ProgressionConfig.objects.all().order_by("-created_at"),
            "selected_config_id": config_id,
            "config_id": config_id,
            "academics_tab": "progression_parent",
        })
        return ctx


# ---------------------------------------------------------------------------
# Item 12 — Notification helper for finalized cases
# ---------------------------------------------------------------------------

def _notify_finalized_case(case, config, actor):
    """Send parent (and HOD for retain) notifications for a single finalized case.

    Promoted/graduated → parent notification with new placement.
    Retained → parent notification + HOD notification with support conditions.
    """
    from communications.email_service import send_parent_notification, dispatch_notification
    from students.models import ParentGuardian

    student = case.student
    decision = case.hos_decision
    if not decision:
        return

    grade_order = list(
        GradeClass.objects.filter().order_by("sort_order", "name").values_list("name", flat=True)
    )
    grade_index = {name: i for i, name in enumerate(grade_order)}

    new_class = ""
    idx = grade_index.get(student.class_name)
    if decision == ProgressionOutcome.PROMOTE and idx is not None and idx + 1 < len(grade_order):
        new_class = grade_order[idx + 1]
    elif decision == ProgressionOutcome.RETAIN:
        new_class = student.class_name
    elif decision == ProgressionOutcome.GRADUATE:
        new_class = "Graduated"

    outcome_label = ProgressionOutcome(decision).label if decision else "pending"
    guardians = ParentGuardian.objects.filter(
        studentguardian__student=student,
        studentguardian__is_primary=True,
    )

    for guardian in guardians:
        send_parent_notification(
            guardian=guardian,
            title=f"Student Progression: {student.first_name}",
            message=(
                f"Dear {guardian.full_name},\n\n"
                f"{student.first_name} {student.last_name} ({student.admission_no}) "
                f"has been {outcome_label} for the {config.academic_year_to} academic year.\n"
                f"New placement: {new_class}\n\n"
                f"If you have any questions, please contact the school office."
            ),
            link="/academics/progression/parent/",
            actor=actor,
        )

    if decision == ProgressionOutcome.RETAIN:
        dept = GradeClass.objects.filter(name=student.class_name).values_list("department", flat=True).first()
        if dept:
            hod_role_map = {
                "PRIMARY": UserRole.PRIMARY_HOD,
                "ECD": UserRole.ECD_HOD,
                "LOWER_SECONDARY": UserRole.LOWER_SECONDARY_HOD,
            }
            hod_role = hod_role_map.get(dept)
            if hod_role:
                from users.models import User as UserModel
                hod = UserModel.objects.filter(role=hod_role).first()
                if hod:
                    support_conditions = ""
                    if case.override_reason:
                        support_conditions = f"\nSupport conditions: {case.override_reason}"
                    dispatch_notification(
                        user=hod,
                        title=f"Student Retained: {student.first_name}",
                        message=(
                            f"{student.first_name} {student.last_name} ({student.admission_no}) "
                            f"has been retained in {student.class_name} for {config.academic_year_to}. "
                            f"Please review any support conditions needed.{support_conditions}"
                        ),
                        link="/academics/progression/hod-review/",
                        actor=actor,
                    )


# ---------------------------------------------------------------------------
# Phase Four — Bulk Promotion Execution
# ---------------------------------------------------------------------------

class ProgressionBulkPromotionView(RoleRequiredMixin, TemplateView):
    """Admin Officer or HOS reviews finalized cases and triggers promotion."""
    template_name = "academics/progression_bulk_promotion.html"
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        config_id = self.request.GET.get("config_id")
        config = None
        promote_cases = ProgressionCase.objects.none()
        retain_cases = ProgressionCase.objects.none()
        graduate_cases = ProgressionCase.objects.none()
        resume_run = None
        failed_students = []
        successful_students = []
        not_attempted_students = []

        if config_id:
            config = get_object_or_404(ProgressionConfig, pk=config_id)
            base = ProgressionCase.objects.filter(
                progression_config=config,
                status=ProgressionStatus.FINALIZED,
            ).select_related("student").order_by("student__last_name")
            promote_cases = base.filter(hos_decision=ProgressionOutcome.PROMOTE)
            retain_cases = base.filter(hos_decision=ProgressionOutcome.RETAIN)
            graduate_cases = base.filter(hos_decision=ProgressionOutcome.GRADUATE)

            # Resume support: identify three groups
            resume_run = PromotionRun.objects.filter(
                academic_year_from=config.academic_year_from,
                academic_year_to=config.academic_year_to,
                status__in=["in_progress", "paused", "interrupted"],
            ).order_by("-created_at").first()

            if resume_run:
                processed_set = set(resume_run.processed_student_ids)
                failed_set = set(resume_run.failed_student_ids)

                for case in base:
                    student = case.student
                    if student.pk in processed_set:
                        successful_students.append(case)
                    elif student.pk in failed_set:
                        # Attach failure reason from detail_json
                        reason = ""
                        for entry in resume_run.failed_detail_json:
                            if entry.get("student_id") == student.pk:
                                reason = entry.get("reason", "")
                                break
                        failed_students.append({"case": case, "reason": reason})
                    else:
                        not_attempted_students.append(case)

        ctx.update({
            "config": config,
            "configs": ProgressionConfig.objects.all().order_by("-created_at"),
            "selected_config_id": config_id,
            "promote_cases": promote_cases,
            "retain_cases": retain_cases,
            "graduate_cases": graduate_cases,
            "academics_tab": "progression",
            "runs": PromotionRun.objects.filter(academic_year_from=config.academic_year_from if config else None).order_by("-created_at") if config else PromotionRun.objects.none(),
            "resume_run": resume_run,
            "successful_students": successful_students,
            "failed_students": failed_students,
            "not_attempted_students": not_attempted_students,
        })
        return ctx


class ProgressionExecutePromotionView(RoleRequiredMixin, View):
    """Executes the promotion for finalized cases in a config.

    Each student is processed and committed individually so that a single
    failure does not roll back prior successes, preserving resume capability.
    """
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]

    def post(self, request, config_id):
        config = get_object_or_404(ProgressionConfig, pk=config_id)

        # Verify all cases are finalized
        non_finalized = ProgressionCase.objects.filter(
            progression_config=config,
        ).exclude(status=ProgressionStatus.FINALIZED)
        if non_finalized.exists():
            names = list(non_finalized.values_list("student__admission_no", flat=True)[:10])
            messages.error(
                request,
                f"Cannot execute — {non_finalized.count()} case(s) not finalized: {', '.join(names)}. "
                "All cases must be finalized before promotion can run.",
            )
            return redirect(reverse("academics:progression_bulk_promotion") + f"?config_id={config_id}")

        # Determine which cases still need processing (resume support)
        existing_run = PromotionRun.objects.filter(
            academic_year_from=config.academic_year_from,
            academic_year_to=config.academic_year_to,
            status__in=["in_progress", "paused", "interrupted"],
        ).order_by("-created_at").first()

        already_processed = set(existing_run.processed_student_ids) if existing_run else set()
        already_failed = set(existing_run.failed_student_ids) if existing_run else set()

        finalized = ProgressionCase.objects.filter(
            progression_config=config,
            status=ProgressionStatus.FINALIZED,
        ).select_related("student")

        grade_order = list(
            GradeClass.objects.filter().order_by("sort_order", "name").values_list("name", flat=True)
        )
        grade_index = {name: i for i, name in enumerate(grade_order)}

        if existing_run and existing_run.status == "in_progress":
            run = existing_run
        else:
            run = PromotionRun.objects.create(
                academic_year_from=config.academic_year_from,
                academic_year_to=config.academic_year_to,
                executed_by=request.user,
                status="in_progress",
            )

        promoted_count = run.promoted_count
        retained_count = run.retained_count
        graduated_count = run.graduated_count
        failed_count = run.failed_count
        failed_detail = list(run.failed_detail_json)
        errors = []

        for case in finalized:
            student = case.student
            if student.pk in already_processed:
                continue

            decision = case.hos_decision
            current_class = student.class_name
            current_idx = grade_index.get(current_class)

            if current_idx is None:
                if student.pk not in already_failed:
                    failed_detail.append({"student_id": student.pk, "reason": f"Class '{current_class}' not in GradeClass registry"})
                    failed_count += 1
                    run.failed_student_ids.append(student.pk)
                errors.append(f"Class '{current_class}' not in GradeClass registry")
                continue

            from django.db import transaction

            try:
                with transaction.atomic():
                    if decision == ProgressionOutcome.GRADUATE:
                        student.status = StudentStatus.GRADUATED
                        student.academic_year = config.academic_year_to
                        student.save(update_fields=["status", "academic_year", "updated_at"])
                        EnrollmentHistory.objects.create(
                            student=student,
                            academic_year=config.academic_year_to,
                            class_name=current_class,
                            stream_name=student.stream_name,
                            action="graduated",
                            progression_case=case,
                        )
                        graduated_count += 1

                    elif decision == ProgressionOutcome.PROMOTE:
                        if current_idx + 1 >= len(grade_order):
                            raise ValueError(f"Already in final grade '{current_class}', cannot promote")
                        next_class = grade_order[current_idx + 1]
                        next_grade = GradeClass.objects.filter(name=next_class).first()
                        if next_grade and next_grade.max_capacity:
                            enrolled = Student.objects.filter(
                                class_name=next_class, is_archived=False,
                                status=StudentStatus.ACTIVE,
                            ).count()
                            if enrolled >= next_grade.max_capacity:
                                raise ValueError(
                                    f"Destination '{next_class}' at capacity ({enrolled}/{next_grade.max_capacity})"
                                )
                        EnrollmentHistory.objects.create(
                            student=student,
                            academic_year=config.academic_year_from,
                            class_name=current_class,
                            stream_name=student.stream_name,
                            action="promoted",
                            notes=f"Promoted from {current_class} to {next_class}",
                            progression_case=case,
                        )
                        student.class_name = next_class
                        student.academic_year = config.academic_year_to
                        student.save(update_fields=["class_name", "academic_year", "updated_at"])
                        promoted_count += 1

                    elif decision == ProgressionOutcome.RETAIN:
                        EnrollmentHistory.objects.create(
                            student=student,
                            academic_year=config.academic_year_from,
                            class_name=current_class,
                            stream_name=student.stream_name,
                            action="retained",
                            notes=f"Retained in {current_class}",
                            progression_case=case,
                        )
                        student.academic_year = config.academic_year_to
                        student.save(update_fields=["academic_year", "updated_at"])
                        retained_count += 1

                    run.processed_student_ids.append(student.pk)
                    if student.pk in already_failed:
                        run.failed_student_ids = [pk for pk in run.failed_student_ids if pk != student.pk]
                        failed_detail = [d for d in failed_detail if d.get("student_id") != student.pk]
                        run.failed_count = max(0, run.failed_count - 1)
                        failed_count = run.failed_count
                    run.promoted_count = promoted_count
                    run.retained_count = retained_count
                    run.graduated_count = graduated_count
                    run.failed_count = failed_count
                    run.failed_detail_json = failed_detail
                    run.save()

                    # Item 12: Per-student notification fires immediately after commit
                    _notify_finalized_case(case, config, request.user)

            except Exception as e:
                if student.pk not in already_failed:
                    failed_detail.append({"student_id": student.pk, "reason": str(e)})
                    failed_count += 1
                    run.failed_student_ids.append(student.pk)
                else:
                    for entry in failed_detail:
                        if entry.get("student_id") == student.pk:
                            entry["reason"] = str(e)
                            break
                run.failed_count = failed_count
                run.failed_detail_json = failed_detail
                run.processed_student_ids = [pk for pk in run.processed_student_ids if pk != student.pk]
                run.save()
                errors.append(str(e))

        had_failures = bool(errors)
        run.status = "completed" if not had_failures else "paused" if failed_count < finalized.count() else "interrupted"
        run.completed_at = timezone.now()
        run.save()

        log_event(
            actor=request.user,
            action_type="PROMOTION_EXECUTED",
            model_name="PromotionRun",
            object_id=run.pk,
            description=f"Promotion run: {promoted_count} promoted, {retained_count} retained, "
                        f"{graduated_count} graduated, {failed_count} failed "
                        f"({config.academic_year_from} → {config.academic_year_to})",
            request=request,
        )

        msg = f"Promotion run complete: {promoted_count} promoted, {retained_count} retained, {graduated_count} graduated."
        if failed_detail:
            msg += f" {failed_count} failed: {'; '.join(d['reason'] for d in failed_detail[:5])}"
        messages.success(request, msg)

        return redirect(reverse("academics:progression_bulk_promotion") + f"?config_id={config_id}")

