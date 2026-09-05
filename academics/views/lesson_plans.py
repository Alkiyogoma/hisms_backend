import logging
import re
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
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


def _is_hod_like(role: str) -> bool:
    return role in {
        UserRole.SUPER_ADMIN,
        UserRole.HEAD_OF_SCHOOL,
        UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD,
        UserRole.LOWER_SECONDARY_HOD,
    }


def _is_super_admin(user) -> bool:
    return getattr(user, 'role', '') == UserRole.SUPER_ADMIN


class LessonPlanListView(RoleRequiredMixin, TemplateView):
    template_name = "academics/lesson_plans.html"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.view_lessonplan"

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

        from datetime import date as _date
        date_from = self.request.GET.get("date_from")
        date_to = self.request.GET.get("date_to")

        mine_qs = LessonPlan.objects.filter(teacher=user).select_related("term", "reviewed_by").prefetch_related("attachments").order_by("-created_at")
        queue = LessonPlan.objects.filter(status=LessonPlanStatus.SUBMITTED).select_related("teacher", "term").prefetch_related("attachments").order_by("submitted_at", "created_at")

        if not _is_hod_like(role):
            if date_from:
                try:
                    mine_qs = mine_qs.filter(week_start_date__gte=_date.fromisoformat(date_from))
                    queue = queue.filter(week_start_date__gte=_date.fromisoformat(date_from))
                except (ValueError, TypeError):
                    pass
            if date_to:
                try:
                    mine_qs = mine_qs.filter(week_start_date__lte=_date.fromisoformat(date_to))
                    queue = queue.filter(week_start_date__lte=_date.fromisoformat(date_to))
                except (ValueError, TypeError):
                    pass

        if not _is_hod_like(role):
            mine_total = mine_qs.count()
            queue = LessonPlan.objects.filter(
                teacher=user, status=LessonPlanStatus.SUBMITTED
            ).select_related("teacher", "term").prefetch_related("attachments").order_by("submitted_at", "created_at")
            if date_from:
                try:
                    queue = queue.filter(week_start_date__gte=_date.fromisoformat(date_from))
                except (ValueError, TypeError):
                    pass
            if date_to:
                try:
                    queue = queue.filter(week_start_date__lte=_date.fromisoformat(date_to))
                except (ValueError, TypeError):
                    pass

        from academics.models import Term as TermModel
        ctx["date_from"] = date_from or ""
        ctx["date_to"] = date_to or ""

        # Teacher filter (HODs/Admin only)
        selected_teacher = None
        if _is_hod_like(role):
            from django.contrib.auth import get_user_model
            User = get_user_model()
            all_teachers = User.objects.filter(
                role=UserRole.TEACHER, is_active=True
            ).order_by("first_name", "last_name")
            teacher_id = self.request.GET.get("teacher")
            # HOD/SA sees ALL plans by default, or filtered to a specific teacher
            base_qs = LessonPlan.objects.all().select_related("teacher", "term", "reviewed_by").prefetch_related("attachments").order_by("-created_at")
            # Exclude MISSING records for teachers without timetable slots
            from django.db.models import Q as _Q
            from timetable.models import TimetableSlot
            from academics.utils import get_current_term as _get_term
            _ct = _get_term()
            if _ct:
                _slot_tids = set(TimetableSlot.objects.filter(term=_ct).values_list("teacher_id", flat=True))
                if _slot_tids:
                    base_qs = base_qs.exclude(
                        _Q(status=LessonPlanStatus.MISSING) & ~_Q(teacher_id__in=_slot_tids)
                    )
            if date_from:
                try:
                    base_qs = base_qs.filter(week_start_date__gte=_date.fromisoformat(date_from))
                except (ValueError, TypeError):
                    pass
            if date_to:
                try:
                    base_qs = base_qs.filter(week_start_date__lte=_date.fromisoformat(date_to))
                except (ValueError, TypeError):
                    pass
            if teacher_id:
                selected_teacher = all_teachers.filter(pk=teacher_id).first()
                if selected_teacher:
                    mine_qs = base_qs.filter(teacher=selected_teacher)
                else:
                    mine_qs = base_qs
            else:
                mine_qs = base_qs
                selected_teacher = None
            ctx["all_teachers"] = all_teachers
            ctx["selected_teacher"] = selected_teacher

        # FR-ACAD-002: Detect teachers with MISSING plans (HODs/Admin only)
        missing_teachers = _get_missing_plan_teachers() if _is_hod_like(role) else []

        # Subject×class heatmap for HODs
        heatmap_week_offset = int(self.request.GET.get("week", 0))
        heatmap = _build_heatmap(role, week_offset=heatmap_week_offset) if _is_hod_like(role) else None

        # Stat counts: HODs see all plans (excluding drafts), teachers see only their own
        if _is_hod_like(role):
            approved_count = LessonPlan.objects.filter(status=LessonPlanStatus.APPROVED).count()
            draft_count = 0
            submitted_count = LessonPlan.objects.filter(status=LessonPlanStatus.SUBMITTED).count()
            total_plans_count = LessonPlan.objects.exclude(status=LessonPlanStatus.DRAFT).count()
        else:
            approved_count = LessonPlan.objects.filter(teacher=user, status=LessonPlanStatus.APPROVED).count()
            draft_count = LessonPlan.objects.filter(teacher=user, status=LessonPlanStatus.DRAFT).count()
            submitted_count = LessonPlan.objects.filter(teacher=user, status=LessonPlanStatus.SUBMITTED).count()
            total_plans_count = LessonPlan.objects.filter(teacher=user).count()

        ctx["approved_count"] = approved_count
        ctx["draft_count"] = draft_count
        ctx["submitted_count"] = submitted_count
        ctx["total_plans_count"] = total_plans_count

        # Status filter for My Submissions
        status_filter = self.request.GET.get("status", "")
        if status_filter:
            mine_qs = mine_qs.filter(status=status_filter)
        ctx["status_filter"] = status_filter

        page = int(self.request.GET.get("page", 1))
        per_page = 20
        queue_total = queue.count()
        ctx["queue_count"] = queue_total
        queue_total_pages = max(1, (queue_total + per_page - 1) // per_page)
        page = min(page, queue_total_pages)
        ctx["queue"] = queue[(page - 1) * per_page : page * per_page]
        ctx["page"] = page
        ctx["total_pages"] = queue_total_pages
        ctx["total_count"] = queue_total
        ctx["has_next"] = page < queue_total_pages
        ctx["has_prev"] = page > 1

        page_numbers = []
        if queue_total_pages <= 7:
            page_numbers = list(range(1, queue_total_pages + 1))
        else:
            page_numbers = [1]
            if page > 3:
                page_numbers.append(-1)
            for pg in range(max(2, page - 1), min(queue_total_pages, page + 2) + 1):
                page_numbers.append(pg)
            if page < queue_total_pages - 2:
                page_numbers.append(-1)
            page_numbers.append(queue_total_pages)
        ctx["page_numbers"] = page_numbers

        # My Submissions pagination
        # For HODs viewing submitted status, show queue (all submitted) instead of mine
        if _is_hod_like(role) and status_filter == "submitted":
            display_qs = queue
        elif _is_hod_like(role):
            display_qs = LessonPlan.objects.select_related("teacher", "term").prefetch_related("attachments").order_by("-created_at")
            # HODs should not see unsubmitted (draft) plans from other teachers
            if not status_filter or status_filter != "draft":
                display_qs = display_qs.exclude(status=LessonPlanStatus.DRAFT)
            if date_from:
                try:
                    display_qs = display_qs.filter(week_start_date__gte=_date.fromisoformat(date_from))
                except (ValueError, TypeError):
                    pass
            if date_to:
                try:
                    display_qs = display_qs.filter(week_start_date__lte=_date.fromisoformat(date_to))
                except (ValueError, TypeError):
                    pass
            if status_filter:
                display_qs = display_qs.filter(status=status_filter)
        else:
            display_qs = mine_qs
        display_total = display_qs.count()

        mine_page = int(self.request.GET.get("mine_page", 1))
        mine_per_page = 20
        display_total_pages = max(1, (display_total + mine_per_page - 1) // mine_per_page)
        mine_page = min(mine_page, display_total_pages)
        ctx["mine"] = display_qs[(mine_page - 1) * mine_per_page : mine_page * mine_per_page]
        ctx["mine_page"] = mine_page
        ctx["mine_total_pages"] = display_total_pages
        ctx["mine_total_count"] = display_total
        ctx["mine_has_next"] = mine_page < display_total_pages
        ctx["mine_has_prev"] = mine_page > 1

        mine_page_numbers = []
        if display_total_pages <= 7:
            mine_page_numbers = list(range(1, display_total_pages + 1))
        else:
            mine_page_numbers = [1]
            if mine_page > 3:
                mine_page_numbers.append(-1)
            for pg in range(max(2, mine_page - 1), min(display_total_pages, mine_page + 2) + 1):
                mine_page_numbers.append(pg)
            if mine_page < display_total_pages - 2:
                mine_page_numbers.append(-1)
            mine_page_numbers.append(display_total_pages)
        ctx["mine_page_numbers"] = mine_page_numbers

        # Check if teacher has any timetable slots (for empty state — teachers only)
        from timetable.models import TimetableSlot
        if role == UserRole.TEACHER:
            has_timetable_slots = TimetableSlot.objects.filter(teacher=user).exists()
        else:
            has_timetable_slots = True
        ctx["has_timetable_slots"] = has_timetable_slots

        ctx["can_review"] = user.has_perm("academics.can_review_lessonplan")
        ctx["can_create"] = user.has_perm("academics.add_lessonplan") and not teacher_is_ecd
        ctx["missing_teachers"] = missing_teachers
        ctx["missing_count"] = len(missing_teachers)
        ctx["heatmap"] = heatmap
        ctx["academics_tab"] = "lesson_plans"

        # Recent plans for HOD/HOS review queue
        if _is_hod_like(role):
            recent_plans = LessonPlan.objects.filter(
                status__in=[LessonPlanStatus.SUBMITTED, LessonPlanStatus.APPROVED, LessonPlanStatus.REJECTED, LessonPlanStatus.REVISION_REQUESTED]
            ).select_related("teacher", "term").order_by("-updated_at")[:10]
            ctx["recent_plans"] = recent_plans

        return ctx


class LessonPlanContextMixin:
    """Shared context for lesson plan create / edit forms."""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        role = user.role

        # Recent submissions (non-draft) for the sidebar
        ctx["recent_submissions"] = (
            LessonPlan.objects.filter(teacher=user)
            .exclude(status=LessonPlanStatus.DRAFT)
            .select_related("term")
            .prefetch_related("attachments")
            .order_by("-submitted_at", "-created_at")[:10]
        )

        # Week navigation
        from datetime import date as _date, timedelta as _td
        today = _date.today()
        this_monday = today - _td(days=today.weekday())
        week_offset = int(self.request.GET.get("week", 0))
        current_monday = this_monday + _td(weeks=week_offset)
        # When editing, use the plan's week as default
        obj = getattr(self, 'object', None)
        if obj and obj.pk and obj.week_start_date:
            current_monday = obj.week_start_date
        current_friday = current_monday + _td(days=4)
        ctx["week_offset"] = week_offset
        ctx["current_monday"] = current_monday
        ctx["current_friday"] = current_friday
        ctx["week_dates"] = [current_monday + _td(days=i) for i in range(5)]

        # Class → subjects mapping from timetable (for JS dynamic filtering)
        from timetable.models import TimetableSlot
        from academics.ecd_utils import ecd_template_type_from_class_name
        can_view_all = user.has_perm("academics.view_all_lessonplans")
        if can_view_all:
            all_slots = TimetableSlot.objects.all().select_related("subject", "teacher")
        else:
            all_slots = TimetableSlot.objects.filter(teacher=user).select_related("subject")
        # Filter out ECD classes (ECD uses Weekly Focus, not lesson plans)
        # Build a set of known ECD class names from DB + heuristic
        ecd_names = set(
            n.strip().lower()
            for n in GradeClass.objects.filter(department="ECD").values_list("name", flat=True)
            if n
        )
        def _is_ecd_slot(slot):
            cn = (slot.class_name or "").strip().lower()
            if cn in ecd_names:
                return True
            if ecd_template_type_from_class_name(cn):
                return True
            # Also check subject name — ECD subjects should be excluded
            sn = (slot.subject_name or "").strip().lower()
            if sn == "ecd":
                return True
            return False
        slots = [s for s in all_slots if not _is_ecd_slot(s)]
        class_subject_map: dict[str, list[str]] = {}
        DAY_LABEL = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri"}
        DAY_ORDER = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4}
        timetable_days: dict[str, dict[str, dict]] = {}
        timetable_slots_flat = []

        # Build LP detail map for this week: (class, subject, day) -> plan details
        if can_view_all:
            week_plans_qs = LessonPlan.objects.filter(
                week_start_date=current_monday
            ).select_related("term").prefetch_related("attachments")
        else:
            week_plans_qs = LessonPlan.objects.filter(
                teacher=user, week_start_date=current_monday
            ).select_related("term").prefetch_related("attachments")
        lp_detail_map = {}
        for plan in week_plans_qs:
            key = (plan.class_name.strip(), plan.subject_name.strip(), plan.day_of_week)
            atts = plan.attachments.all()[:5]
            att_data = [{"name": a.filename, "url": a.file.url} for a in atts]
            lp_detail_map[key] = {
                "status": plan.status,
                "title": plan.lesson_title or "",
                "pk": plan.pk,
                "submitted_at": plan.submitted_at.strftime("%d %b %H:%M") if plan.submitted_at else "",
                "created_at": plan.created_at.strftime("%d %b %H:%M") if plan.created_at else "",
                "attachments": att_data,
                "attachment_count": plan.attachments.count(),
            }

        for slot in slots:
            cn = slot.class_name.strip()
            sn = slot.subject_name.strip()
            if cn not in class_subject_map:
                class_subject_map[cn] = []
            if sn and sn not in class_subject_map[cn]:
                class_subject_map[cn].append(sn)

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

            # LP details for this slot this week
            lp_detail = lp_detail_map.get((cn, sn, slot.day_of_week), None)

            timetable_slots_flat.append({
                "class_name": cn,
                "subject_name": sn,
                "subject_code": (slot.subject.code if slot.subject and slot.subject.code else sn[:4].upper()),
                "color": (slot.subject.color if slot.subject and slot.subject.color else "#023AA5"),
                "day": slot.day_of_week,
                "day_label": day_label,
                "start_time": slot.start_time.strftime("%H:%M") if slot.start_time else "",
                "end_time": slot.end_time.strftime("%H:%M") if slot.end_time else "",
                "lp": lp_detail,
                "teacher_id": slot.teacher_id or None,
                "teacher_name": (slot.teacher.get_full_name() if slot.teacher else ""),
            })

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
        ctx["timetable_slots_json"] = json.dumps(timetable_slots_flat)
        ctx["is_super_admin"] = can_view_all

        # Check if teacher has any timetable slots (for empty state on list page — teachers only)
        user_role = getattr(user, 'role', None)
        if user_role == UserRole.TEACHER:
            has_timetable_slots = len(slots) > 0
        else:
            has_timetable_slots = True
        ctx["has_timetable_slots"] = has_timetable_slots

        # Current term for read-only display (not user-selectable)
        from academics.utils import get_current_term
        current_term = get_current_term()
        if hasattr(self, 'object') and self.object and self.object.term_id:
            ctx["term_display"] = str(self.object.term)
        elif current_term:
            ctx["term_display"] = str(current_term)
        else:
            ctx["term_display"] = "No active term"
        ctx["academics_tab"] = "lesson_plans"
        ctx["lp_form_view"] = True
        return ctx


class LessonPlanCreateView(LessonPlanContextMixin, RoleRequiredMixin, CreateView):
    template_name = "academics/new_lesson_plan.html"
    form_class = LessonPlanForm
    def get_success_url(self):
        return reverse("academics:lesson_plans")
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.add_lessonplan"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
             return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        if request.user.role not in {UserRole.TEACHER, UserRole.SUPER_ADMIN}:
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
        if self.request.user.has_perm("academics.view_all_lessonplans"):
            slot_teacher_id = self.request.POST.get("slot_teacher_id")
            if slot_teacher_id:
                from users.models import User as UserModel
                try:
                    form.instance.teacher = UserModel.objects.get(pk=int(slot_teacher_id))
                except (UserModel.DoesNotExist, ValueError, TypeError):
                    form.instance.teacher = self.request.user
            else:
                form.instance.teacher = self.request.user
        else:
            form.instance.teacher = self.request.user
        return _lesson_plan_form_processing(self, form)


class QuickLessonPlanView(RoleRequiredMixin, View):
    """HTMX modal for quick lesson plan creation from timetable.

    GET  → returns a modal partial with pre-filled slot data.
    POST → creates the lesson plan, returns updated timetable cell HTML.
    """
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL,
                     UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.add_lessonplan"

    def get(self, request, *args, **kwargs):
        from datetime import date, timedelta
        from timetable.models import TimetableSlot
        from academics.utils import get_current_term
        from users.models import User as UserModel

        slot_id = request.GET.get("slot_id")
        if not slot_id:
            return HttpResponseBadRequest("slot_id required")

        slot = get_object_or_404(TimetableSlot, pk=slot_id)
        term = get_current_term()
        today = date.today()
        this_monday = today - timedelta(days=today.weekday())
        week_offset = int(request.GET.get("week", 0))
        week_start = this_monday + timedelta(weeks=week_offset)

        # Pre-fill teacher from slot, or allow admin to choose
        selected_teacher = slot.teacher
        teachers = UserModel.objects.filter(
            role=UserRole.TEACHER, is_active=True
        ).order_by("last_name", "first_name")

        context = {
            "slot": slot,
            "term": term,
            "class_name": slot.class_name,
            "subject_name": slot.subject_name,
            "selected_teacher": selected_teacher,
            "teachers": teachers,
            "week_start": week_start,
            "week_offset": week_offset,
            "can_choose_teacher": request.user.role != UserRole.TEACHER,
        }
        return render(request, "academics/_quick_lp_modal.html", context)

    def post(self, request, *args, **kwargs):
        from datetime import date, timedelta
        from timetable.models import TimetableSlot
        from academics.utils import get_current_term

        slot_id = request.POST.get("slot_id")
        slot = get_object_or_404(TimetableSlot, pk=slot_id)
        term = get_current_term()

        user = request.user
        if user.role == UserRole.TEACHER:
            if slot.teacher_id != user.id:
                return HttpResponseBadRequest("You can only create plans for your own timetable slots.")
            teacher = user
        else:
            teacher_id = request.POST.get("teacher_id")
            teacher = get_object_or_404(get_user_model(), pk=teacher_id) if teacher_id else user

        today = date.today()
        this_monday = today - timedelta(days=today.weekday())
        week_offset = int(request.POST.get("week", 0))
        week_start = this_monday + timedelta(weeks=week_offset)

        lesson_title = (request.POST.get("lesson_title") or "").strip()
        action = request.POST.get("action", "draft")

        plan = LessonPlan(
            teacher=teacher,
            term=term,
            class_name=slot.class_name,
            subject_name=slot.subject_name,
            day_of_week=slot.day_of_week,
            week_start_date=week_start,
            lesson_title=lesson_title,
            status=LessonPlanStatus.DRAFT,
        )
        plan.save()

        # Handle file uploads
        files = request.FILES.getlist("attachments")
        for f in files:
            LessonPlanAttachment.objects.create(
                lesson_plan=plan,
                file=f,
                filename=f.name,
                uploaded_by=request.user,
            )

        # If submitting, change status and set submitted_at
        if action == "submit":
            from django.utils import timezone as tz
            plan.status = LessonPlanStatus.SUBMITTED
            plan.submitted_at = tz.now()
            plan.save(update_fields=["status", "submitted_at", "updated_at"])

        # Log it
        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="LESSON_PLAN_QUICK_CREATED",
            model_name="LessonPlan",
            object_id=plan.pk,
            description=f"Quick lesson plan created for {plan.class_name} — {plan.subject_name}",
            after=_lesson_plan_snapshot(plan),
        )

        status_label = "submitted" if action == "submit" else "draft"
        toast_color = "#059669" if action == "submit" else "#023AA5"
        toast_msg = f"Lesson plan {status_label} for {slot.class_name} — {slot.subject_name}"

        # Return JS to close modal, show toast, and refresh timetable
        js = (
            '<script>'
            'document.getElementById("quick-lp-modal")?.remove();'
            'document.body.insertAdjacentHTML("beforeend",'
            '<div id="qlp-toast" style="position:fixed;top:20px;right:20px;z-index:99999;padding:14px 20px;border-radius:12px;background:' + toast_color + ';color:#fff;font-size:13px;font-weight:700;box-shadow:0 8px 24px rgba(0,0,0,0.15);display:flex;align-items:center;gap:8px;transition:opacity 0.3s">'
            '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>'
            + toast_msg +
            '</div>'
            ');'
            'setTimeout(function(){'
            'var t=document.getElementById("qlp-toast");'
            'if(t){t.style.opacity="0";}'
            'setTimeout(function(){if(t)t.remove();location.reload();},400);'
            '},2500);'
            '</script>'
        )
        return HttpResponse(js)


class LessonPlanWithdrawModalView(RoleRequiredMixin, View):
    """HTMX modal for confirming lesson plan withdrawal from timetable."""
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL,
                     UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.change_lessonplan"

    def get(self, request, pk):
        plan = get_object_or_404(LessonPlan, pk=pk)
        if plan.teacher_id != request.user.id and request.user.role != UserRole.SUPER_ADMIN:
            raise PermissionDenied()
        return render(request, "academics/_lp_withdraw_modal.html", {"plan": plan})

    def post(self, request, pk):
        plan = get_object_or_404(LessonPlan, pk=pk)
        if plan.teacher_id != request.user.id and request.user.role != UserRole.SUPER_ADMIN:
            raise PermissionDenied()
        if plan.status != LessonPlanStatus.SUBMITTED:
            if request.headers.get("HX-Request"):
                return HttpResponse('<script>document.getElementById("lp-withdraw-modal")?.remove()</script>')
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
            description=f"Lesson plan {plan.class_name} - {plan.subject_name} ({plan.week_start_date}) withdrawn to draft",
            before=before,
            after=_lesson_plan_snapshot(plan),
            request=request,
        )
        _notify_hod_lesson_plan(plan, request.user, event="withdrawn")

        js = (
            '<script>'
            'document.getElementById("lp-withdraw-modal")?.remove();'
            'document.body.insertAdjacentHTML("beforeend",'
            '<div id="qlp-toast" style="position:fixed;top:20px;right:20px;z-index:99999;padding:14px 20px;border-radius:12px;background:#F59E0B;color:#fff;font-size:13px;font-weight:700;box-shadow:0 8px 24px rgba(0,0,0,0.15);display:flex;align-items:center;gap:8px;transition:opacity 0.3s">'
            '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>'
            'Lesson plan withdrawn to draft'
            '</div>'
            ');'
            'setTimeout(function(){'
            'var t=document.getElementById("qlp-toast");'
            'if(t){t.style.opacity="0";}'
            'setTimeout(function(){if(t)t.remove();location.reload();},400);'
            '},2500);'
            '</script>'
        )
        return HttpResponse(js)


class LessonPlanEditModalView(RoleRequiredMixin, View):
    """HTMX modal for editing a lesson plan from timetable (same UI as quick-create)."""
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL,
                     UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.change_lessonplan"

    def get(self, request, pk):
        plan = get_object_or_404(LessonPlan, pk=pk)
        if plan.teacher_id != request.user.id and request.user.role != UserRole.SUPER_ADMIN:
            raise PermissionDenied()
        attachments = plan.attachments.all()
        context = {
            "plan": plan,
            "attachments": attachments,
        }
        return render(request, "academics/_lp_edit_modal.html", context)

    def post(self, request, pk):
        plan = get_object_or_404(LessonPlan, pk=pk)
        if plan.teacher_id != request.user.id and request.user.role != UserRole.SUPER_ADMIN:
            raise PermissionDenied()

        lesson_title = (request.POST.get("lesson_title") or "").strip()
        action = request.POST.get("action", plan.status)

        before = _lesson_plan_snapshot(plan)

        plan.lesson_title = lesson_title

        if action == "submit" and plan.status != LessonPlanStatus.SUBMITTED:
            from django.utils import timezone as tz
            plan.status = LessonPlanStatus.SUBMITTED
            plan.submitted_at = tz.now()
            plan.save()
            _notify_hod_lesson_plan(plan, request.user)
            toast_msg_text = f"Lesson plan submitted for {plan.class_name} - {plan.subject_name}"
            toast_color = "#059669"
        elif action == "draft" and before.get("status") == LessonPlanStatus.SUBMITTED:
            plan.status = LessonPlanStatus.DRAFT
            plan.submitted_at = None
            plan.reviewer_feedback = ""
            plan.reviewed_by = None
            plan.reviewed_at = None
            plan.save(update_fields=["status", "submitted_at", "reviewer_feedback", "reviewed_by_id", "reviewed_at", "updated_at"])
            _notify_hod_lesson_plan(plan, request.user, event="withdrawn")
            toast_msg_text = "Lesson plan withdrawn to draft"
            toast_color = "#F59E0B"
        elif action == "draft":
            plan.status = LessonPlanStatus.DRAFT
            plan.save()
            toast_msg_text = f"Draft saved for {plan.class_name} - {plan.subject_name}"
            toast_color = "#023AA5"
        else:
            plan.save()
            toast_msg_text = f"Lesson plan updated for {plan.class_name} - {plan.subject_name}"
            toast_color = "#023AA5"

        files = request.FILES.getlist("attachments")
        for f in files:
            LessonPlanAttachment.objects.create(
                lesson_plan=plan,
                file=f,
                filename=f.name,
                uploaded_by=request.user,
            )

        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="lesson_plan.modal_edit",
            model_name="LessonPlan",
            object_id=str(plan.pk),
            description=f"Modal edit: {plan.lesson_title}",
            before=before,
            request=request,
        )

        js = (
            '<script>'
            'document.getElementById("quick-lp-modal")?.remove();'
            'document.body.insertAdjacentHTML("beforeend",'
            '<div id="qlp-toast" style="position:fixed;top:20px;right:20px;z-index:99999;padding:14px 20px;border-radius:12px;background:' + toast_color + ';color:#fff;font-size:13px;font-weight:700;box-shadow:0 8px 24px rgba(0,0,0,0.15);display:flex;align-items:center;gap:8px;transition:opacity 0.3s">'
            '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>'
            + toast_msg_text +
            '</div>'
            ');'
            'setTimeout(function(){'
            'var t=document.getElementById("qlp-toast");'
            'if(t){t.style.opacity="0";}'
            'setTimeout(function(){if(t)t.remove();location.reload();},400);'
            '},2500);'
            '</script>'
        )
        return HttpResponse(js)


class LessonPlanUpdateView(LessonPlanContextMixin, RoleRequiredMixin, UpdateView):
    model = LessonPlan
    template_name = "academics/new_lesson_plan.html"
    form_class = LessonPlanForm
    def get_success_url(self):
        return reverse("academics:lesson_plans")
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        plan = self.get_object()
        if request.user.role == UserRole.TEACHER and is_ecd_teacher(request.user):
            messages.info(request, "ECD teachers submit a Weekly Focus instead of lesson plans.")
            return redirect("communications:weekly_focus_submit")
        is_owner = plan.teacher_id == request.user.id
        is_privileged = request.user.role in {
            UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD,
            UserRole.LOWER_SECONDARY_HOD, UserRole.ECD_HOD,
            UserRole.HEAD_OF_SCHOOL,
        }
        if not is_owner and not is_privileged:
            raise PermissionDenied()
        # Teachers can edit DRAFT, REVISION_REQUESTED, or REJECTED. Privileged roles can edit any.
        if request.user.role == UserRole.TEACHER:
            if plan.status not in {LessonPlanStatus.REVISION_REQUESTED, LessonPlanStatus.DRAFT, LessonPlanStatus.REJECTED}:
                messages.error(request, "You can only edit lesson plans that are draft, rejected, or returned for revision. Withdraw a submitted plan first.")
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

        # Block past weeks only for NEW plans, not when editing existing drafts
        today = date_type.today()
        this_monday = today - timedelta(days=today.weekday())
        if week_start < this_monday and not view_instance.object.pk:
            messages.error(view_instance.request, "Cannot submit lesson plans for past weeks. Please select the current week or a future week.")
            return view_instance.form_invalid(form)

        # Dynamic deadline from LessonPlanDeadline config
        from core.models import LessonPlanDeadline
        deadline_config = LessonPlanDeadline.get_active()
        deadline_day = deadline_config.deadline_day
        deadline_time = deadline_config.deadline_time
        h, m = deadline_time.hour, deadline_time.minute
        
        days_to_monday = week_start.weekday()
        last_monday = week_start - timedelta(days=days_to_monday)
        
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
            
        # Enforce Teacher-Class Integration from Timetable (skip for existing plans being edited)
        from timetable.models import TimetableSlot
        is_assigned = TimetableSlot.objects.filter(
            teacher=view_instance.request.user,
            class_name__iexact=form.cleaned_data["class_name"].strip(),
            subject_name__iexact=form.cleaned_data["subject_name"].strip()
        ).exists()
        
        if not is_assigned and view_instance.request.user.role == UserRole.TEACHER and not view_instance.object.pk:
            messages.error(view_instance.request, "You cannot submit a lesson plan for a class/subject you are not assigned to in the timetable.")
            return view_instance.form_invalid(form)

        # Duplicate — redirect to edit the existing plan instead of blocking
        day_of_week = form.cleaned_data.get("day_of_week", "")
        existing_qs = LessonPlan.objects.filter(
            teacher=form.instance.teacher,
            week_start_date=week_start,
            class_name=form.cleaned_data["class_name"],
            subject_name=form.cleaned_data["subject_name"]
        ).exclude(pk=form.instance.pk)
        if day_of_week:
            existing_qs = existing_qs.filter(day_of_week=day_of_week)
        if existing_qs.exists():
            existing_plan = existing_qs.first()
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
        
        # Capture BEFORE snapshot BEFORE saving (audit fix)
        before_snapshot = _lesson_plan_snapshot(form.instance) if form.instance.pk else None

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
            before=before_snapshot,
            after=_lesson_plan_snapshot(form.instance),
            request=view_instance.request,
        )
        return response


class LessonPlanSubmitView(RoleRequiredMixin, LoginRequiredMixin, View):
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.SUPER_ADMIN]
    required_permission = "academics.add_lessonplan"

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
            logger.exception("Failed to generate review task for lesson plan %s", plan.pk)
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
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": True, "message": "Lesson plan submitted for review."})
        return redirect("academics:lesson_plans")


class LessonPlanWithdrawView(RoleRequiredMixin, LoginRequiredMixin, View):
    """Allows a teacher to withdraw/pause a submitted plan back to draft."""

    login_url = "/accounts/login/"
    allowed_roles = [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.SUPER_ADMIN]

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

        # Notify HODs that the plan was withdrawn
        _notify_hod_lesson_plan(plan, request.user, event="withdrawn")

        messages.success(request, "Lesson plan withdrawn. You can now edit and resubmit.")

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": True, "message": "Lesson plan withdrawn."})
        if request.headers.get("HX-Request"):
            js = (
                '<script>'
            'htmx.ajax("GET",window.location.href,{target:"#timetable-content",swap:"outerHTML",select:"#timetable-content"});'
                '</script>'
            )
            return HttpResponse(js)
        return redirect("academics:lesson_plan_edit", pk=plan.pk)


class LessonPlanAttachmentDeleteView(RoleRequiredMixin, LoginRequiredMixin, View):
    """Delete a single attachment from a lesson plan.

    Only allowed when the plan is in DRAFT or REVISION_REQUESTED status
    and the requesting user is the plan owner (or Super Admin).
    """

    login_url = "/accounts/login/"
    allowed_roles = [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.SUPER_ADMIN]

    def post(self, request, pk: int):
        att = get_object_or_404(LessonPlanAttachment, pk=pk)
        plan = att.lesson_plan

        is_owner = plan.teacher_id == request.user.id
        is_privileged = request.user.role in {
            UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD,
            UserRole.LOWER_SECONDARY_HOD, UserRole.ECD_HOD,
            UserRole.HEAD_OF_SCHOOL,
        }
        if not is_owner and not is_privileged:
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
            logger.warning("Failed to remove orphaned file from disk: %s", file_path)

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": True, "message": f"Removed {att.filename}."})
        messages.success(request, f"Removed {att.filename}.")
        return redirect("academics:lesson_plan_edit", pk=plan.pk)


class LessonPlanDeleteView(RoleRequiredMixin, LoginRequiredMixin, View):
    """Delete a lesson plan. Only allowed for draft, rejected, or revision-requested plans.

    Teachers can only delete their own plans.
    HODs and Super Admin can delete any plan in those statuses.
    """
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.SUPER_ADMIN]

    def post(self, request, pk: int):
        plan = get_object_or_404(LessonPlan, pk=pk)
        is_owner = plan.teacher_id == request.user.id
        is_privileged = request.user.role in {
            UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD,
            UserRole.LOWER_SECONDARY_HOD, UserRole.ECD_HOD,
            UserRole.HEAD_OF_SCHOOL,
        }
        if not is_owner and not is_privileged:
            raise PermissionDenied()
        if plan.status not in {LessonPlanStatus.DRAFT, LessonPlanStatus.REJECTED, LessonPlanStatus.REVISION_REQUESTED}:
            msg = "Only draft, rejected, or revision-requested plans can be deleted."
            if request.headers.get("HX-Request") or request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.content_type == "application/x-www-form-urlencoded" and request.POST.get("action"):
                return HttpResponseBadRequest(msg)
            messages.error(request, msg)
            return redirect("academics:lesson_plans")
        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="DELETE",
            model_name="LessonPlan",
            object_id=str(plan.pk),
            description=f"Deleted lesson plan: {plan.class_name} — {plan.subject_name} ({plan.week_start_date})",
            before=_lesson_plan_snapshot(plan),
            request=request,
        )
        plan.delete()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": True, "message": "Lesson plan deleted."})
        messages.success(request, "Lesson plan deleted.")
        return redirect("academics:lesson_plans")


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

    from core.models import LessonPlanDeadline
    deadline_config = LessonPlanDeadline.get_active()
    deadline_day = deadline_config.deadline_day
    deadline_time = deadline_config.deadline_time
    h, m = deadline_time.hour, deadline_time.minute
    deadline_date = this_monday - timedelta(days=(7 - deadline_day) % 7)
    deadline_dt = timezone.make_aware(timezone.datetime.combine(deadline_date, time_type(h, m)))

    # Only flag missing plans after the deadline has passed
    if now <= deadline_dt:
        return []

    # Get all active teachers (excluding ECD — use is_ecd_teacher for consistency)
    # Only include teachers who have timetable slots this term (no slots = no LP expected)
    from timetable.models import TimetableSlot
    from academics.utils import get_current_term
    current_term = get_current_term()
    slot_teacher_ids = set()
    if current_term:
        slot_teacher_ids = set(
            TimetableSlot.objects.filter(term=current_term).values_list("teacher_id", flat=True)
        )

    all_teachers = User.objects.filter(
        role=UserRole.TEACHER, is_active=True,
        pk__in=slot_teacher_ids,
    ).select_related("staff_profile") if slot_teacher_ids else User.objects.none()
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


def _notify_hod_lesson_plan(plan, actor, event="submitted"):
    """Notify the department-appropriate HOD for lesson plan events.

    Resolves the department from the lesson's class_name via GradeClass and notifies
    the corresponding HOD role. HOS is always notified.

    Args:
        plan: LessonPlan instance
        actor: User who performed the action
        event: "submitted" or "withdrawn"
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
    elif dept == Department.LOWER_SECONDARY:
        hod_roles = [UserRole.LOWER_SECONDARY_HOD]
    else:
        # Unknown department — notify all HOD types as fallback
        hod_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]

    # Always include HOS so they have school-wide visibility
    hod_roles.append(UserRole.HEAD_OF_SCHOOL)

    hods = User.objects.filter(role__in=hod_roles, is_active=True).distinct()
    for hod in hods:
        if event == "withdrawn":
            title = "Lesson plan withdrawn"
            message = f"Lesson plan withdrawn — {plan.teacher.get_full_name()}, {plan.class_name} — {plan.subject_name}"
        else:
            title = "New lesson plan submitted"
            message = f"New lesson plan submitted — {plan.teacher.get_full_name()}, {plan.class_name} — {plan.subject_name}"
        dispatch_notification(
            user=hod,
            title=title,
            message=message,
            link="/academics/lesson-plans/",
            actor=actor
        )


def _build_heatmap(role, week_offset=0):
    """Build a subject×class coverage heatmap for the given role's department(s).

    Returns a dict with:
      subjects  — list of Subject objects (row labels)
      classes   — list of GradeClass objects (column labels)
      grid      — list of dicts, each {subject, cells: [{class, status, teacher, exists}]}
      week_start — the Monday of the selected week
      week_offset — current offset for navigation
      week_end — Sunday of the selected week
    """
    from datetime import date, timedelta

    if role == UserRole.ECD_HOD:
        departments = [Department.ECD]
    elif role == UserRole.PRIMARY_HOD:
        departments = [Department.PRIMARY]
    elif role == UserRole.LOWER_SECONDARY_HOD:
        departments = [Department.LOWER_SECONDARY]
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
    this_monday = today - timedelta(days=today.weekday())
    week_start = this_monday + timedelta(weeks=week_offset)
    week_end = week_start + timedelta(days=6)

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
        "week_end": week_end,
        "week_offset": week_offset,
    }


class LessonPlanReviewView(RoleRequiredMixin, View):
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.can_review_lessonplan"

    def post(self, request, pk: int):
        if not request.user.has_perm("academics.can_review_lessonplan"):
            raise PermissionDenied()
        plan = get_object_or_404(LessonPlan, pk=pk)
        
        # LP-REVIEW: Only SUBMITTED plans can be reviewed.
        if plan.status != LessonPlanStatus.SUBMITTED:
            messages.error(request, "Only submitted lesson plans can be reviewed.")
            return redirect("academics:lesson_plans")
        
        # LP-REVIEW: HOD can only review plans from teachers in their department.
        if request.user.role not in {UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL}:
            from timetable.models import TimetableSlot
            from academics.models import Department, GradeClass
            if request.user.role == UserRole.PRIMARY_HOD:
                allowed_depts = [Department.PRIMARY]
            elif request.user.role == UserRole.ECD_HOD:
                allowed_depts = [Department.ECD]
            elif request.user.role == UserRole.LOWER_SECONDARY_HOD:
                allowed_depts = [Department.LOWER_SECONDARY]
            else:
                raise PermissionDenied()
            allowed_class_names = list(
                GradeClass.objects.filter(department__in=allowed_depts).values_list("name", flat=True)
            )
            if plan.class_name not in allowed_class_names:
                raise PermissionDenied("You can only review lesson plans from your department.")
        
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


class LessonPlanComplianceView(RoleRequiredMixin, TemplateView):
    """Dedicated lesson plan compliance tracking view.

    Shows per-teacher submission rates, overdue plans, and historical trend
    for the current term. Filters by department (Primary / ECD).
    """
    template_name = "academics/lesson_plan_compliance.html"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.view_lessonplan"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{self.login_url}?next={request.path}")
        if not _is_hod_like(request.user.role):
            raise PermissionDenied()
        return TemplateView.dispatch(self, request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from users.models import User
        from django.db.models import Count, Q
        from collections import defaultdict

        user = self.request.user
        today = timezone.localdate()
        dept_filter = self.request.GET.get("dept", "")

        from academics.utils import get_current_term
        term = get_current_term()
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

        # Compliance rows pagination
        comp_page = int(self.request.GET.get("comp_page", 1))
        comp_per_page = 20
        comp_total = len(compliance_rows)
        comp_total_pages = max(1, (comp_total + comp_per_page - 1) // comp_per_page)
        comp_page = min(comp_page, comp_total_pages)
        ctx["compliance_rows"] = compliance_rows[(comp_page - 1) * comp_per_page : comp_page * comp_per_page]
        ctx["comp_page"] = comp_page
        ctx["comp_total_pages"] = comp_total_pages
        ctx["comp_total_count"] = comp_total
        ctx["comp_has_next"] = comp_page < comp_total_pages
        ctx["comp_has_prev"] = comp_page > 1
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

        # ── Weekly Coverage Heatmap ──
        from datetime import timedelta
        week_offset = int(self.request.GET.get("week", 0))
        # Monday of the selected week (0 = this week relative to today)
        today_weekday = today.weekday()  # 0=Mon
        this_monday = today - timedelta(days=today_weekday)
        selected_monday = this_monday + timedelta(weeks=week_offset)
        selected_sunday = selected_monday + timedelta(days=6)

        ctx["week_offset"] = week_offset
        ctx["selected_monday"] = selected_monday
        ctx["selected_sunday"] = selected_sunday

        # Build heatmap: for each teacher, status per day (Mon-Fri) for selected week
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        day_dates = [selected_monday + timedelta(days=i) for i in range(5)]

        # Get plans for selected week
        selected_week_plans = LessonPlan.objects.filter(
            teacher__in=teachers,
            term=term,
            week_start_date=selected_monday,
        ).select_related("teacher").values("teacher_id", "status", "subject_name", "class_name")

        # Build teacher -> day -> status map
        heatmap_data = []
        teacher_plan_map = defaultdict(lambda: defaultdict(lambda: {"status": None, "subject": "", "cls": ""}))
        for p in selected_week_plans:
            t = teacher_plan_map[p["teacher_id"]]
            # All plans for this week go to Monday since week_start_date is Monday
            # But we show coverage status for the whole week
            for i in range(5):
                if t[day_dates[i]]["status"] is None:
                    t[day_dates[i]]["status"] = p["status"]
                    t[day_dates[i]]["subject"] = p["subject_name"]
                    t[day_dates[i]]["cls"] = p["class_name"]
                elif p["status"] == LessonPlanStatus.APPROVED:
                    t[day_dates[i]]["status"] = p["status"]

        for teacher in teachers:
            days = []
            for i, d in enumerate(day_dates):
                info = teacher_plan_map[teacher.pk][d]
                status = info["status"]
                days.append({
                    "date": d,
                    "day_name": day_names[i],
                    "status": status or "none",
                    "subject": info["subject"],
                    "class": info["cls"],
                })
            heatmap_data.append({
                "teacher": teacher,
                "days": days,
            })

        ctx["heatmap_data"] = heatmap_data
        ctx["day_names"] = day_names
        ctx["day_dates"] = day_dates

        return ctx


class LessonPlanReviewQueueView(RoleRequiredMixin, TemplateView):
    """Full-page review queue for HODs to review submitted lesson plans."""
    template_name = "academics/lesson_plan_review_queue.html"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.can_review_lessonplan"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        from academics.utils import get_current_term
        
        term = get_current_term()
        status_filter = self.request.GET.get("status", "submitted")
        ctx["term"] = term
        ctx["academics_tab"] = "review_queue"
        ctx["status_filter"] = status_filter
        
        if not term:
            ctx["plans"] = []
            return ctx
        
        from academics.models import GradeClass, Department
        if user.role == UserRole.PRIMARY_HOD:
            dept_classes = list(GradeClass.objects.filter(department=Department.PRIMARY).values_list("name", flat=True))
        elif user.role == UserRole.ECD_HOD:
            dept_classes = list(GradeClass.objects.filter(department=Department.ECD).values_list("name", flat=True))
        elif user.role == UserRole.LOWER_SECONDARY_HOD:
            dept_classes = list(GradeClass.objects.filter(department=Department.LOWER_SECONDARY).values_list("name", flat=True))
        else:
            dept_classes = None

        plans_qs = LessonPlan.objects.filter(term=term).select_related("teacher", "term", "reviewed_by").prefetch_related("attachments")
        
        if dept_classes is not None:
            plans_qs = plans_qs.filter(class_name__in=dept_classes)
        
        if status_filter == "submitted":
            plans_qs = plans_qs.filter(status=LessonPlanStatus.SUBMITTED)
        elif status_filter == "all_pending":
            plans_qs = plans_qs.filter(status__in=[LessonPlanStatus.SUBMITTED, LessonPlanStatus.REVISION_REQUESTED])
        elif status_filter == "reviewed":
            plans_qs = plans_qs.filter(status__in=[LessonPlanStatus.APPROVED, LessonPlanStatus.REJECTED])
        elif status_filter == "all":
            pass
        
        plans = plans_qs.order_by("-submitted_at", "-created_at")
        
        all_dept_plans = LessonPlan.objects.filter(term=term)
        if dept_classes is not None:
            all_dept_plans = all_dept_plans.filter(class_name__in=dept_classes)
        
        from django.db.models import Count, Q
        stats = all_dept_plans.aggregate(
            submitted=Count("id", filter=Q(status=LessonPlanStatus.SUBMITTED)),
            approved=Count("id", filter=Q(status=LessonPlanStatus.APPROVED)),
            rejected=Count("id", filter=Q(status=LessonPlanStatus.REJECTED)),
            revision=Count("id", filter=Q(status=LessonPlanStatus.REVISION_REQUESTED)),
        )
        ctx["stats"] = stats

        page = int(self.request.GET.get("page", 1))
        per_page = 15
        total = plans.count()
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        ctx["plans"] = plans[(page - 1) * per_page : page * per_page]
        ctx["page"] = page
        ctx["total_pages"] = total_pages
        ctx["total_count"] = total
        ctx["has_next"] = page < total_pages
        ctx["has_prev"] = page > 1

        page_numbers = []
        if total_pages <= 7:
            page_numbers = list(range(1, total_pages + 1))
        else:
            page_numbers = [1]
            if page > 3:
                page_numbers.append(-1)
            for pg in range(max(2, page - 1), min(total_pages, page + 2) + 1):
                page_numbers.append(pg)
            if page < total_pages - 2:
                page_numbers.append(-1)
            page_numbers.append(total_pages)
        ctx["page_numbers"] = page_numbers

        ctx["can_review"] = True
        return ctx


class LessonPlanProgressView(RoleRequiredMixin, TemplateView):
    """Lesson plan progress/tracking view for teachers and HODs."""
    template_name = "academics/lesson_plan_progress.html"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.view_lessonplan"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        from django.db.models import Count, Q
        from academics.utils import get_current_term
        
        term = get_current_term()
        ctx["term"] = term
        ctx["academics_tab"] = "progress"
        
        if term:
            if _is_hod_like(user.role):
                # HOD sees department-wide stats
                plans = LessonPlan.objects.filter(term=term)
            else:
                plans = LessonPlan.objects.filter(teacher=user, term=term)
            
            stats = plans.aggregate(
                total=Count("id"),
                approved=Count("id", filter=Q(status=LessonPlanStatus.APPROVED)),
                submitted=Count("id", filter=Q(status=LessonPlanStatus.SUBMITTED)),
                draft=Count("id", filter=Q(status=LessonPlanStatus.DRAFT)),
                revision=Count("id", filter=Q(status=LessonPlanStatus.REVISION_REQUESTED)),
                rejected=Count("id", filter=Q(status=LessonPlanStatus.REJECTED)),
                missing=Count("id", filter=Q(status=LessonPlanStatus.MISSING)),
            )
            ctx["stats"] = stats
            
            # Weekly breakdown
            from django.db.models.functions import TruncWeek
            weekly = (
                plans.filter(status=LessonPlanStatus.APPROVED)
                .annotate(week=TruncWeek("week_start_date"))
                .values("week")
                .annotate(count=Count("id"))
                .order_by("-week")[:12]
            )
            ctx["weekly_approved"] = list(weekly)
            
            # Recent plans list with pagination
            recent_qs = plans.order_by("-week_start_date")
            recent_total = recent_qs.count()
            rp_page = int(self.request.GET.get("rp_page", 1))
            rp_per_page = 20
            rp_total_pages = max(1, (recent_total + rp_per_page - 1) // rp_per_page)
            rp_page = min(rp_page, rp_total_pages)
            ctx["recent_plans"] = recent_qs[(rp_page - 1) * rp_per_page : rp_page * rp_per_page]
            ctx["rp_page"] = rp_page
            ctx["rp_total_pages"] = rp_total_pages
            ctx["rp_total_count"] = recent_total
            ctx["rp_has_next"] = rp_page < rp_total_pages
            ctx["rp_has_prev"] = rp_page > 1
            
            # Status breakdown for chart
            status_counts = {}
            for status, label in LessonPlanStatus.choices:
                status_counts[status] = plans.filter(status=status).count()
            ctx["status_counts"] = status_counts
        
        return ctx


class LessonPlanReportsView(RoleRequiredMixin, TemplateView):
    """Lesson plan reporting view."""
    template_name = "academics/lesson_plan_reports.html"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.view_lessonplan"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        from django.db.models import Count, Q, Avg
        from academics.utils import get_current_term
        
        term = get_current_term()
        ctx["term"] = term
        ctx["academics_tab"] = "lp_reports"
        
        if term:
            plans = LessonPlan.objects.filter(term=term)
            
            # Per-teacher stats with pagination
            teacher_stats_qs = (
                plans.values("teacher__first_name", "teacher__last_name", "teacher__username")
                .annotate(
                    total=Count("id"),
                    approved=Count("id", filter=Q(status=LessonPlanStatus.APPROVED)),
                    submitted=Count("id", filter=Q(status=LessonPlanStatus.SUBMITTED)),
                    missing=Count("id", filter=Q(status=LessonPlanStatus.MISSING)),
                )
                .order_by("-approved")
            )
            ts_page = int(self.request.GET.get("ts_page", 1))
            ts_per_page = 20
            ts_total = teacher_stats_qs.count()
            ts_total_pages = max(1, (ts_total + ts_per_page - 1) // ts_per_page)
            ts_page = min(ts_page, ts_total_pages)
            ctx["teacher_stats"] = list(teacher_stats_qs[(ts_page - 1) * ts_per_page : ts_page * ts_per_page])
            ctx["ts_page"] = ts_page
            ctx["ts_total_pages"] = ts_total_pages
            ctx["ts_total_count"] = ts_total
            ctx["ts_has_next"] = ts_page < ts_total_pages
            ctx["ts_has_prev"] = ts_page > 1
            
            # Per-class stats
            class_stats = (
                plans.values("class_name")
                .annotate(
                    total=Count("id"),
                    approved=Count("id", filter=Q(status=LessonPlanStatus.APPROVED)),
                )
                .order_by("-approved")
            )
            ctx["class_stats"] = class_stats
            
            # Subject stats with pagination
            subject_stats_qs = (
                plans.values("subject_name")
                .annotate(
                    total=Count("id"),
                    approved=Count("id", filter=Q(status=LessonPlanStatus.APPROVED)),
                    submitted=Count("id", filter=Q(status=LessonPlanStatus.SUBMITTED)),
                    missing=Count("id", filter=Q(status=LessonPlanStatus.MISSING)),
                )
                .order_by("-approved")
            )
            ss_page = int(self.request.GET.get("ss_page", 1))
            ss_per_page = 15
            ss_total = subject_stats_qs.count()
            ss_total_pages = max(1, (ss_total + ss_per_page - 1) // ss_per_page)
            ss_page = min(ss_page, ss_total_pages)
            ctx["subject_stats"] = list(subject_stats_qs[(ss_page - 1) * ss_per_page : ss_page * ss_per_page])
            ctx["ss_page"] = ss_page
            ctx["ss_total_pages"] = ss_total_pages
            ctx["ss_total_count"] = ss_total
            ctx["ss_has_next"] = ss_page < ss_total_pages
            ctx["ss_has_prev"] = ss_page > 1
            
            # Weekly trend
            from django.db.models.functions import TruncWeek
            weekly_trend = (
                plans.filter(status=LessonPlanStatus.APPROVED)
                .annotate(week=TruncWeek("week_start_date"))
                .values("week")
                .annotate(count=Count("id"))
                .order_by("week")
            )
            ctx["weekly_trend"] = list(weekly_trend)
            
            # Overall summary
            from django.db.models import Sum
            total_plans = plans.count()
            approved_plans = plans.filter(status=LessonPlanStatus.APPROVED).count()
            ctx["total_plans"] = total_plans
            ctx["approved_plans"] = approved_plans
            ctx["pending_rejected"] = total_plans - approved_plans
            ctx["approval_rate"] = round((approved_plans / total_plans * 100), 1) if total_plans > 0 else 0
        
        return ctx
