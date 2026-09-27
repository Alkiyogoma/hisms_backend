"""
Welfare views — FRD Section 13.
"""
from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import DetailView, ListView, TemplateView, View

from core.permissions import RoleRequiredMixin, assert_role
from core.scoping import DepartmentScopedMixin
from core.teacher_context import get_teacher_assigned_classes, is_ecd_teacher
from users.models import UserRole

from .models import (
    CONCERN_SIGNAL_TAGS, POSITIVE_ONLY_TAGS, TAGS_BY_TYPE,
    WelfareConcernType, WelfareNoteStatus, WelfareNoteType, WelfareObservation,
    WelfareSeverity, clean_tags, concern_type_for_tags, type_tag_mismatch,
)
from .visibility import can_view_observation, can_view_safeguarding, visible_observations


def _assert_observation_department_match(user, observation):
    """FR-WEL-003/FR-WEL-007: Block cross-department access to welfare observations.

    HOS and Super Admin may access any department; department-scoped roles
    (ECD_HOD, PRIMARY_HOD, LOWER_SECONDARY_HOD, TEACHER) are restricted
    to their own department.
    """
    if user.role in (UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN):
        return  # unrestricted
    from academics.models import GradeClass, Department
    student_class = GradeClass.objects.filter(
        name=observation.student.class_name
    ).values_list("department", flat=True).first()
    _ROLE_DEPT_MAP = {
        UserRole.ECD_HOD: Department.ECD,
        UserRole.PRIMARY_HOD: Department.PRIMARY,
        UserRole.LOWER_SECONDARY_HOD: Department.LOWER_SECONDARY,
    }
    required_dept = _ROLE_DEPT_MAP.get(user.role)
    if required_dept and student_class != required_dept:
        raise PermissionDenied("You do not have access to welfare observations outside your department.")
    if user.role == UserRole.TEACHER and observation.submitted_by != user:
        raise PermissionDenied("You can only view your own welfare observations.")


class WelfareStudentIncidentsView(DepartmentScopedMixin, RoleRequiredMixin, TemplateView):
    """Student incidents dashboard.
    Shows recent incidents, trends, severity breakdown, and key insights
    with search filters and charts.  Supports unified mode (all departments)
    when department="all" is passed via as_view()."""
    template_name = "welfare/student_incidents.html"
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "welfare.view_welfareobservation"

    def dispatch(self, request, *args, **kwargs):
        # Let LoginRequiredMixin redirect anonymous users first
        auth_resp = super().dispatch(request, *args, **kwargs)
        if hasattr(auth_resp, 'status_code') and auth_resp.status_code in (302, 403):
            return auth_resp
        # Legacy department-specific routes: redirect if no explicit department set
        if self.department is None and not self._is_unified():
            from academics.models import Department
            dept = self._get_department()
            if dept == Department.ECD:
                return redirect("welfare:student_incidents_ecd")
            return redirect("welfare:student_incidents_primary")
        self._check_teacher_department()
        return auth_resp

    def get_context_data(self, **kwargs):
        from collections import Counter
        from datetime import timedelta
        from django.db.models import Count
        from academics.models import GradeClass, Department

        ctx = super().get_context_data(**kwargs)
        request = self.request
        role = request.user.role
        # NFR-PDPA-006: Log access to sensitive welfare data
        from audit.view_audit import log_sensitive_access
        log_sensitive_access(
            request, "WelfareObservation", None, "welfare",
            description="Welfare student incidents dashboard viewed"
        )
        # Get department context
        department = self._get_department()  # None in unified mode
        is_unified = self._is_unified()
        is_ecd = (department == Department.ECD) if department else False
        ctx["department"] = department
        ctx["is_unified_welfare"] = is_unified
        ctx["is_ecd_welfare"] = is_ecd
        ctx["page_title"] = "Welfare overview"

        # Base queryset scoped by department and role
        qs = WelfareObservation.objects.select_related(
            'student', 'submitted_by', 'reviewed_by'
        ).order_by('-observation_date', '-created_at')

        # Scope to department classes (or all ECD+Primary in unified mode)
        if is_unified:
            dept_class_names = self._get_all_welfare_classes()
        else:
            dept_class_names = list(GradeClass.objects.filter(
                department=department
            ).values_list('name', flat=True))
        qs = qs.filter(student__class_name__in=dept_class_names)
        # Same visibility rules as the student profile (class teacher / HOD /
        # leadership for concerns and health; safeguarding lead + HOS only).
        qs = visible_observations(request.user, qs)

        # Apply search filters
        q = request.GET.get('q', '').strip()
        note_type = request.GET.get('note_type', '')
        status = request.GET.get('status', '')
        class_name = request.GET.get('class_name', '')
        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')
        dept_filter = request.GET.get('department', '')

        # Department filter (for unified mode)
        if dept_filter and is_unified:
            from academics.models import Department as Dept
            dept_val = {"ecd": Dept.ECD, "primary": Dept.PRIMARY, "lower_secondary": Dept.LOWER_SECONDARY}.get(dept_filter)
            if dept_val:
                dept_cls = list(GradeClass.objects.filter(department=dept_val).values_list("name", flat=True))
                qs = qs.filter(student__class_name__in=dept_cls)

        if q:
            from django.db.models import Q
            qs = qs.filter(
                Q(student__first_name__icontains=q) |
                Q(student__last_name__icontains=q) |
                Q(student__admission_no__icontains=q) |
                Q(observation_text__icontains=q)
            )
        if note_type:
            if note_type in WelfareNoteType.values:
                qs = qs.filter(note_type=note_type)
        if status:
            qs = qs.filter(hod_status=status)
        if class_name:
            qs = qs.filter(student__class_name=class_name)
        if date_from:
            qs = qs.filter(observation_date__gte=date_from)
        if date_to:
            qs = qs.filter(observation_date__lte=date_to)

        # Pagination
        page = int(request.GET.get('page', 1))
        per_page = 25
        total_filtered = qs.count()
        total_pages = max(1, (total_filtered + per_page - 1) // per_page)
        page = min(page, total_pages)
        offset = (page - 1) * per_page
        recent_incidents = qs[offset:offset + per_page]

        # Stats from filtered queryset so cards/charts respond to filters
        today = timezone.now().date()
        this_month_start = today.replace(day=1)
        total_count = qs.count()
        this_month_count = qs.filter(observation_date__gte=this_month_start).count()
        last_month_end = this_month_start - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        last_month_count = qs.filter(
            observation_date__gte=last_month_start,
            observation_date__lte=last_month_end,
        ).count()
        open_count = qs.exclude(hod_status='resolved').count()
        resolved_count = qs.filter(hod_status='resolved').count()
        parent_contacted_count = qs.filter(parent_contacted=True).count()

        # Severity breakdown
        severity_data = dict(Counter(qs.values_list('severity', flat=True)))

        # Concern type breakdown
        concern_data = dict(Counter(qs.values_list('concern_type', flat=True)))

        # Monthly trend (last 6 months) — from full department scope for context
        base_qs = visible_observations(
            request.user, WelfareObservation.objects.filter(student__class_name__in=dept_class_names)
        )
        current_month_start = today.replace(day=1)
        monthly_trend = []
        for i in range(5, -1, -1):
            m = current_month_start.month - i
            y = current_month_start.year
            while m <= 0:
                m += 12
                y -= 1
            month_start = current_month_start.replace(year=y, month=m)
            if month_start.month == 12:
                month_end = month_start.replace(year=month_start.year + 1, month=1)
            else:
                month_end = month_start.replace(month=month_start.month + 1)
            count = base_qs.filter(
                observation_date__gte=month_start,
                observation_date__lt=month_end,
            ).count()
            monthly_trend.append({
                'month': month_start.strftime('%b'),
                'month_full': month_start.strftime('%b %Y'),
                'count': count,
            })

        # Top students with most incidents
        from django.db.models import F
        top_students = (
            qs.values('student__first_name', 'student__last_name', 'student__class_name', 'student_id')
            .annotate(incident_count=Count('id'), student_pk=F('student_id'))
            .order_by('-incident_count')[:8]
        )

        # Available classes for filter dropdown
        if role == UserRole.TEACHER:
            available_classes = sorted(get_teacher_assigned_classes(request.user))
        else:
            available_classes = sorted(
                GradeClass.objects.values_list('name', flat=True)
            )

        # Key insights
        contact_pct = round((parent_contacted_count / total_count * 100) if total_count else 0)
        resolved_pct = round((resolved_count / total_count * 100) if total_count else 0)
        month_change = this_month_count - last_month_count

        # Pagination range
        page_range = list(range(max(1, page - 2), min(total_pages, page + 2) + 1))
        show_first_ellipsis = page_range[0] > 2
        show_last_ellipsis = page_range[-1] < total_pages - 1

        # Department context for _top_tabs.html include
        ctx['department'] = department
        ctx['is_unified_welfare'] = is_unified
        ctx['is_ecd_welfare'] = is_ecd
        ctx['can_see_hod_dashboard'] = self.request.user.has_perm("welfare.can_review_observation")

        ctx.update({
            'recent_incidents': recent_incidents,
            'total_filtered': total_filtered,
            'page': page,
            'total_pages': total_pages,
            'total_count': total_count,
            'this_month_count': this_month_count,
            'last_month_count': last_month_count,
            'month_change': month_change,
            'open_count': open_count,
            'resolved_count': resolved_count,
            'resolved_pct': resolved_pct,
            'parent_contacted_count': parent_contacted_count,
            'contact_pct': contact_pct,
            'severity_data': severity_data,
            'concern_data': concern_data,
            'monthly_trend': monthly_trend,
            'top_students': top_students,
            'available_classes': available_classes,
            'note_type_choices': WelfareNoteType.choices,
            'status_choices': [('pending', 'Pending'), ('in_progress', 'In progress'), ('resolved', 'Resolved')],
            # Pass current filter values back to template
            'filter_q': q,
            'filter_note_type': note_type,
            'filter_status': status,
            'filter_class': class_name,
            'filter_department': dept_filter,
            'filter_date_from': date_from,
            'filter_date_to': date_to,
            'page_range': page_range,
            'show_first_ellipsis': show_first_ellipsis,
            'show_last_ellipsis': show_last_ellipsis,
        })
        return ctx


class WelfareListView(DepartmentScopedMixin, RoleRequiredMixin, ListView):
    template_name = "welfare/list.html"
    context_object_name = "observations"
    paginate_by = 20
    allowed_roles = [
        UserRole.ECD_HOD, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD,
        UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.TEACHER
    ]
    required_permission = "welfare.view_welfareobservation"

    def dispatch(self, request, *args, **kwargs):
        auth_resp = super().dispatch(request, *args, **kwargs)
        if hasattr(auth_resp, 'status_code') and auth_resp.status_code in (302, 403):
            return auth_resp
        self._check_teacher_department()
        return auth_resp

    def get_queryset(self):
        from academics.models import Department, GradeClass
        qs = WelfareObservation.objects.select_related("student", "submitted_by").order_by("-observation_date")

        # Scope by department (or all in unified mode)
        department = self._get_department()
        is_unified = self._is_unified()
        if is_unified:
            dept_class_names = self._get_all_welfare_classes()
        else:
            dept_class_names = GradeClass.objects.filter(
                department=department
            ).values_list("name", flat=True)
        qs = qs.filter(student__class_name__in=dept_class_names)

        role = self.request.user.role
        if role == UserRole.TEACHER:
            qs = qs.filter(submitted_by=self.request.user)
        qs = visible_observations(self.request.user, qs)
        severity = self.request.GET.get("severity")
        if severity:
            qs = qs.filter(severity=severity)
        # Department filter (for unified mode)
        dept_filter = self.request.GET.get("department", "")
        if dept_filter and is_unified:
            from academics.models import Department as Dept
            dept_val = {"ecd": Dept.ECD, "primary": Dept.PRIMARY, "lower_secondary": Dept.LOWER_SECONDARY}.get(dept_filter)
            if dept_val:
                dept_cls = list(GradeClass.objects.filter(department=dept_val).values_list("name", flat=True))
                qs = qs.filter(student__class_name__in=dept_cls)
        return qs

    def get_context_data(self, **kwargs):
        from academics.models import Department
        from django.core.paginator import Paginator
        ctx = super().get_context_data(**kwargs)
        department = self._get_department()
        is_unified = self._is_unified()
        is_ecd = (department == Department.ECD) if department else False
        # NFR-PDPA-006: Log access to welfare list (sensitive data)
        from audit.view_audit import log_sensitive_access
        log_sensitive_access(
            self.request, "WelfareObservation", None, "welfare",
            description=f"Welfare list viewed ({'Unified' if is_unified else 'ECD' if is_ecd else 'Primary'} mode)"
        )
        ctx["welfare_tab"] = "list"
        ctx["department"] = department
        ctx["is_unified_welfare"] = is_unified
        ctx["is_ecd_welfare"] = is_ecd
        ctx["page_title"] = "Welfare notes"
        user = self.request.user
        # Drafts are private to their author and never appear in the queue.
        ctx["drafts"] = WelfareObservation.all_objects.filter(
            status=WelfareNoteStatus.DRAFT, submitted_by=user
        ).select_related("student").order_by("-updated_at")
        # Authors can't reopen their safeguarding notes, but can see that
        # they were sent and add a follow-up.
        if not can_view_safeguarding(user):
            ctx["sent_safeguarding"] = WelfareObservation.objects.filter(
                submitted_by=user, note_type=WelfareNoteType.SAFEGUARDING
            ).select_related("student").order_by("-created_at")[:10]
        ctx["can_see_hod_dashboard"] = self.request.user.has_perm("welfare.can_review_observation")
        qs = self.get_queryset()

        # Paginate active observations
        active_qs = qs.exclude(hod_status="resolved")
        paginator = Paginator(active_qs, 20)
        page_number = self.request.GET.get("page")
        page_obj = paginator.get_page(page_number)
        ctx["active_observations"] = page_obj
        ctx["page_obj"] = page_obj
        ctx["paginator"] = paginator

        ctx["resolved_observations"] = qs.filter(hod_status="resolved")[:15]
        ctx["severity_choices"] = WelfareSeverity.choices
        ctx["today_date"] = date.today().strftime("%d %B %Y")
        return ctx


# ── Note form: shared by new notes, drafts and corrections ─────────────────

# Fields whose changes are recorded in a WelfareObservationRevision, with
# the label shown in the note's correction history.
_TRACKED_FIELDS = [
    ("student_id", "Child"),
    ("note_type", "Note type"),
    ("tags", "Tags"),
    ("observation_date", "Date"),
    ("observation_text", "What happened"),
    ("action_taken", "Action taken"),
    ("severity", "Level"),
    ("parent_contacted", "Parent contacted"),
    ("follow_up_required", "Follow-up required"),
    ("follow_up_date", "Follow-up date"),
]

_SEVERITY_FOR_TYPE = {
    WelfareNoteType.POSITIVE: WelfareSeverity.LOW,
    WelfareNoteType.OBSERVATION: WelfareSeverity.LOW,
    WelfareNoteType.SAFEGUARDING: WelfareSeverity.CRITICAL,
}


def _students_for_user(user, class_names=None):
    """Learners this user may write welfare notes about."""
    from students.models import Student
    qs = Student.objects.filter(is_archived=False)
    if class_names is not None:
        qs = qs.filter(class_name__in=class_names)
    if user.role == UserRole.TEACHER:
        qs = qs.filter(class_name__in=get_teacher_assigned_classes(user))
    return qs.order_by("class_name", "last_name")


def _read_note_form(request):
    """Pull the note fields out of POST without validating them."""
    post = request.POST
    note_type = post.get("note_type", "").strip()
    if note_type not in WelfareNoteType.values:
        note_type = ""
    student_ids = []
    for raw in post.getlist("students") or [post.get("student", "")]:
        if str(raw).isdigit() and int(raw) not in student_ids:
            student_ids.append(int(raw))
    severity = post.get("severity", "")
    if note_type in _SEVERITY_FOR_TYPE:
        severity = _SEVERITY_FOR_TYPE[note_type]
    elif severity not in WelfareSeverity.values:
        severity = WelfareSeverity.MEDIUM if note_type == WelfareNoteType.CONCERN else WelfareSeverity.LOW
    return {
        "note_type": note_type,
        "student_ids": student_ids,
        "tags": clean_tags(post.getlist("tags")) if note_type != WelfareNoteType.SAFEGUARDING else [],
        "observation_date": post.get("observation_date", "").strip(),
        "observation_text": post.get("observation_text", "").strip(),
        "child_words": post.get("child_words", "").strip(),
        "immediate_action": post.get("immediate_action", "").strip(),
        "action_taken": post.get("action_taken", "").strip(),
        "severity": severity,
        "parent_contacted": post.get("parent_contacted") == "on",
        "follow_up_required": post.get("follow_up_required") == "on" or bool(post.get("review_date")),
        "follow_up_date": post.get("follow_up_date", "").strip() or post.get("review_date", "").strip(),
        "edit_reason": post.get("edit_reason", "").strip(),
        "confirm_mismatch": post.get("confirm_mismatch") == "1",
    }


def _validate_note_form(data, *, allowed_students, earliest_date, final, mode):
    """Validate form data. Returns (errors, cleaned).

    ``final`` is False only when saving a draft, which needs just a child.
    ``mode`` is "new", "draft" or "correct".
    """
    from datetime import timedelta
    errors = []
    cleaned = {}
    note_type = data["note_type"]

    allowed_ids = set(allowed_students.values_list("pk", flat=True))
    students = [pk for pk in data["student_ids"] if pk in allowed_ids]
    if data["student_ids"] and len(students) != len(data["student_ids"]):
        errors.append("You can only write welfare notes for learners in your class(es).")
    if not students:
        errors.append("Choose the child this note is about.")
    if mode != "new" and len(students) > 1:
        errors.append("A saved note can only be about one child.")
    cleaned["student_ids"] = students

    try:
        obs_date = date.fromisoformat(data["observation_date"]) if data["observation_date"] else date.today()
    except ValueError:
        errors.append("Invalid observation date format.")
        obs_date = date.today()
    if obs_date > date.today():
        errors.append("Observation date cannot be in the future. Please correct the date.")
    elif final and obs_date < earliest_date - timedelta(days=1):
        errors.append("Observation date cannot be more than 1 day before the note was started. Please correct the date.")
    cleaned["observation_date"] = obs_date

    text = data["observation_text"]
    if note_type == WelfareNoteType.SAFEGUARDING and data["child_words"]:
        text = f"{text}\n\nChild's own words: \"{data['child_words']}\"".strip()
    cleaned["observation_text"] = text
    cleaned["action_taken"] = data["action_taken"] or data["immediate_action"]

    if note_type == WelfareNoteType.SAFEGUARDING:
        if mode == "correct":
            errors.append("A note cannot be changed into a safeguarding note. Write a new safeguarding note instead.")
        elif not final:
            errors.append("Safeguarding notes cannot be saved as drafts. Send them to the Safeguarding Lead straight away.")
        if len(students) > 1:
            errors.append("A safeguarding note can only be about one child.")

    if final:
        if not note_type:
            errors.append("Choose what kind of note this is: Positive, Observation, Concern or Safeguarding.")
        if not text:
            errors.append("Please describe what happened.")
        if not cleaned["action_taken"]:
            errors.append("Please describe the action taken today.")
        # Parents are not contacted by the teacher about safeguarding concerns.
        if note_type != WelfareNoteType.SAFEGUARDING and not data["parent_contacted"]:
            errors.append("Please confirm whether the parent was contacted (toggle required).")
        if mode == "correct" and not data["edit_reason"]:
            errors.append("Say briefly why you are correcting this note.")

    cleaned["tags"] = data["tags"]
    cleaned["note_type"] = note_type
    cleaned["severity"] = data["severity"]
    cleaned["concern_type"] = concern_type_for_tags(data["tags"])
    cleaned["parent_contacted"] = data["parent_contacted"]
    cleaned["follow_up_required"] = data["follow_up_required"]
    try:
        cleaned["follow_up_date"] = date.fromisoformat(data["follow_up_date"]) if data["follow_up_date"] else None
    except ValueError:
        errors.append("Invalid follow-up date format.")
        cleaned["follow_up_date"] = None

    # Type/tag disagreement needs an explicit "yes, save it" from the user.
    cleaned["mismatch"] = type_tag_mismatch(note_type, data["tags"])
    if final and cleaned["mismatch"] and not data["confirm_mismatch"] and not errors:
        errors.append(cleaned["mismatch"])
    return errors, cleaned


def _note_initial(obs=None, data=None, student_id=None):
    """Initial values for the note form's JavaScript state."""
    if data is not None:
        return {
            "note_type": data["note_type"],
            "students": data["student_ids"],
            "tags": data["tags"],
            "observation_date": data["observation_date"],
            "observation_text": data["observation_text"],
            "child_words": data["child_words"],
            "action_taken": data["action_taken"],
            "severity": data["severity"],
            "parent_contacted": data["parent_contacted"],
            "follow_up_required": data["follow_up_required"],
            "follow_up_date": data["follow_up_date"],
            "edit_reason": data["edit_reason"],
        }
    if obs is not None:
        return {
            "note_type": obs.note_type,
            "students": [obs.student_id],
            "tags": obs.tags or [],
            "observation_date": obs.observation_date.isoformat(),
            "observation_text": obs.observation_text,
            "child_words": "",
            "action_taken": obs.action_taken,
            "severity": obs.severity,
            "parent_contacted": obs.parent_contacted,
            "follow_up_required": obs.follow_up_required,
            "follow_up_date": obs.follow_up_date.isoformat() if obs.follow_up_date else "",
            "edit_reason": "",
        }
    return {
        "note_type": "",  # never pre-selected: the teacher must choose
        "students": [student_id] if student_id else [],
        "tags": [],
        "observation_date": date.today().isoformat(),
        "observation_text": "",
        "child_words": "",
        "action_taken": "",
        "severity": "",
        "parent_contacted": False,
        "follow_up_required": False,
        "follow_up_date": "",
        "edit_reason": "",
    }


def _note_form_context(*, mode, students, initial, obs=None, errors=None, mismatch=""):
    from datetime import timedelta
    base = obs.created_at.date() if obs is not None else date.today()
    return {
        "note_mode": mode,
        "obs": obs,
        "students": students,
        "students_data": [
            {"id": s.pk, "name": f"{s.first_name} {s.last_name}", "cls": s.class_name} for s in students
        ],
        "available_classes": sorted({s.class_name for s in students}),
        "severity_choices": WelfareSeverity.choices,
        "note_initial": initial,
        "tags_by_type": {k.value if hasattr(k, "value") else k: v for k, v in TAGS_BY_TYPE.items()},
        "positive_only_tags": sorted(POSITIVE_ONLY_TAGS),
        "concern_signal_tags": sorted(CONCERN_SIGNAL_TAGS),
        "form_errors": errors or [],
        "mismatch_warning": mismatch,
        "today_date_iso": date.today().isoformat(),
        "min_date_iso": (base - timedelta(days=1)).isoformat(),
        "edit_window_hours": int(WelfareObservation.edit_window().total_seconds() // 3600),
    }


def _apply_cleaned(obs, cleaned):
    obs.note_type = cleaned["note_type"]
    obs.tags = cleaned["tags"]
    obs.observation_date = cleaned["observation_date"]
    obs.observation_text = cleaned["observation_text"]
    obs.action_taken = cleaned["action_taken"]
    obs.severity = cleaned["severity"]
    obs.concern_type = cleaned["concern_type"]
    obs.parent_contacted = cleaned["parent_contacted"]
    obs.follow_up_required = cleaned["follow_up_required"]
    obs.follow_up_date = cleaned["follow_up_date"]


def _snapshot(obs):
    """Human-readable values of the tracked fields, keyed by label."""
    snap = {}
    for field, label in _TRACKED_FIELDS:
        value = getattr(obs, field)
        if field == "student_id":
            value = f"{obs.student.first_name} {obs.student.last_name} ({obs.student.class_name})"
        elif field == "note_type":
            value = obs.type_label
        elif field == "severity":
            value = obs.get_severity_display()
        elif field == "tags":
            value = ", ".join(value or []) or None
        elif isinstance(value, bool):
            value = "Yes" if value else "No"
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        snap[label] = value
    return snap


def _finalize_submission(request, obs):
    """Move a note from draft/new to submitted: lock safeguarding notes,
    audit, escalate and create follow-up tasks."""
    from audit.models import log_event

    obs.status = WelfareNoteStatus.SUBMITTED
    obs.submitted_at = timezone.now()
    obs.is_locked = obs.is_safeguarding
    if obs.parent_contacted and not obs.parent_contact_datetime:
        obs.parent_contact_datetime = timezone.now()
    obs.save()

    log_event(
        actor=request.user,
        action_type="welfare_observation_created",
        model_name="WelfareObservation",
        object_id=obs.pk,
        description=f"{obs.type_label} welfare note submitted for {obs.student} ({obs.severity})",
        after={
            "student_id": obs.student_id,
            "note_type": obs.note_type,
            "tags": obs.tags,
            "concern_type": obs.concern_type,
            "severity": obs.severity,
            "observation_date": str(obs.observation_date),
            "parent_contacted": obs.parent_contacted,
            "follow_up_required": obs.follow_up_required,
        },
        request=request,
    )

    if obs.is_safeguarding or obs.severity in (WelfareSeverity.HIGH, WelfareSeverity.CRITICAL):
        _escalate_severity(obs, request, obs.severity)
        try:
            from tasks.services import generate_welfare_alert_task
            generate_welfare_alert_task(obs)
        except Exception:
            pass
    if obs.follow_up_required:
        try:
            from tasks.services import generate_welfare_followup_task
            generate_welfare_followup_task(obs)
        except Exception:
            pass


def _submitted_message(request, obs_list):
    obs = obs_list[0]
    count = len(obs_list)
    noun = "note" if count == 1 else f"{count} notes"
    if obs.is_safeguarding:
        messages.warning(request, "Safeguarding note sent to the Safeguarding Lead and Head of School.")
    elif obs.severity in (WelfareSeverity.HIGH, WelfareSeverity.CRITICAL):
        from .models import hod_role_for_class
        _role, hod_name = hod_role_for_class(obs.student.class_name)
        extra = " and HOS" if obs.severity == WelfareSeverity.CRITICAL else ""
        messages.warning(request, f"Welfare {noun} submitted and escalated to {hod_name}{extra}.")
    else:
        hours = int(WelfareObservation.edit_window().total_seconds() // 3600)
        messages.success(request, f"Welfare {noun} submitted. You can correct it for the next {hours} hours.")


class WelfareSubmitView(DepartmentScopedMixin, RoleRequiredMixin, TemplateView):
    template_name = "welfare/submit.html"
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.SUPER_ADMIN]
    required_permission = "welfare.add_welfareobservation"

    def dispatch(self, request, *args, **kwargs):
        auth_resp = super().dispatch(request, *args, **kwargs)
        if hasattr(auth_resp, 'status_code') and auth_resp.status_code in (302, 403):
            return auth_resp
        self._check_teacher_department()
        return auth_resp

    def _students(self):
        if self._is_unified():
            class_names = self._get_all_welfare_classes()
        else:
            class_names = self._get_dept_classes(self._get_department())
        return _students_for_user(self.request.user, class_names)

    def _page_context(self):
        from academics.models import Department
        department = self._get_department()
        return {
            "welfare_tab": "submit",
            "can_see_hod_dashboard": self.request.user.has_perm("welfare.can_review_observation"),
            "department": department,
            "is_unified_welfare": self._is_unified(),
            "is_ecd_welfare": (department == Department.ECD) if department else False,
            "page_title": "Add welfare note",
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self._page_context())
        student_id = self.request.GET.get("student", "")
        ctx.update(_note_form_context(
            mode="new",
            students=self._students(),
            initial=_note_initial(student_id=int(student_id) if student_id.isdigit() else None),
        ))
        return ctx

    def post(self, request, *args, **kwargs):
        from django.shortcuts import render
        data = _read_note_form(request)
        final = request.POST.get("action") != "draft"
        students = self._students()
        errors, cleaned = _validate_note_form(
            data, allowed_students=students, earliest_date=date.today(), final=final, mode="new",
        )
        if errors:
            ctx = self._page_context()
            ctx.update(_note_form_context(
                mode="new", students=students, initial=_note_initial(data=data), errors=errors,
                mismatch=cleaned["mismatch"] if errors == [cleaned["mismatch"]] else "",
            ))
            return render(request, self.template_name, ctx, status=400)

        from students.models import Student
        created = []
        for student in Student.objects.filter(pk__in=cleaned["student_ids"]):
            obs = WelfareObservation(
                student=student, submitted_by=request.user,
                status=WelfareNoteStatus.DRAFT,
            )
            _apply_cleaned(obs, cleaned)
            if final:
                _finalize_submission(request, obs)
            else:
                obs.save()
            created.append(obs)

        if not final:
            messages.success(request, "Draft saved. Only you can see it until you submit it.")
            return redirect("welfare:edit", pk=created[0].pk) if len(created) == 1 else redirect("welfare:list")
        _submitted_message(request, created)
        return redirect("welfare:detail", pk=created[0].pk)


def _safeguarding_recipients():
    """Head of School plus everyone holding the Safeguarding Lead permission."""
    from django.db.models import Q
    from users.models import User
    perm = Q(groups__permissions__codename="view_safeguarding_note") | Q(
        user_permissions__codename="view_safeguarding_note"
    )
    return User.objects.filter(
        Q(role=UserRole.HEAD_OF_SCHOOL) | perm, is_active=True,
    ).distinct()


def _escalate_severity(obs: WelfareObservation, request, severity):
    """Send in-app notifications and emails to relevant roles according to FRD escalation rules.
    Safeguarding notes go only to the Safeguarding Lead and HOS — never the HOD —
    and the notification carries no note content."""
    from communications.email_service import dispatch_notification
    from communications.models import Notification, NotificationCategory
    from users.models import User
    from .models import hod_role_for_class
    import datetime

    hod_role, hod_label = hod_role_for_class(obs.student.class_name)

    roles_to_notify = []
    urgency = "normal"

    if severity == WelfareSeverity.LOW:
        pass
    elif severity == WelfareSeverity.MEDIUM:
        roles_to_notify = [hod_role]
        urgency = "medium"
    elif severity == WelfareSeverity.HIGH:
        roles_to_notify = [hod_role]
        urgency = "high"
    elif severity == WelfareSeverity.CRITICAL:
        roles_to_notify = [hod_role, UserRole.HEAD_OF_SCHOOL]
        urgency = "critical"

    if obs.is_safeguarding:
        recipients = _safeguarding_recipients()
    elif roles_to_notify:
        recipients = User.objects.filter(
            role__in=roles_to_notify,
            is_active=True,
        )
    else:
        recipients = User.objects.none()

    for user in recipients:
        if obs.is_safeguarding:
            title = f"Safeguarding note: {obs.student}"
            message = (
                "A safeguarding note has been recorded. Open it in Hodari to read it. "
                "Its content is not included in notifications."
            )
        else:
            title = f"{severity.capitalize()} welfare observation: {obs.student}"
            if severity == WelfareSeverity.HIGH:
                message = f"{obs.observation_text[:300]}\n\nACTION REQUIRED: Parent contact required today."
            elif severity == WelfareSeverity.CRITICAL:
                message = f"{obs.observation_text[:300]}\n\nURGENT - CRITICAL: Immediate escalation required. Parent contact essential today."
            else:
                message = obs.observation_text[:500]

        # Create in-app notification
        Notification.objects.create(
            recipient=user,
            category=NotificationCategory.WELFARE,
            title=title,
            body=message,
            link=f"/welfare/{obs.pk}/"
        )

        dispatch_notification(
            user=user,
            title=title,
            message=message,
            link=f"/welfare/{obs.pk}/",
            actor=request.user
        )

    # Set automatic follow-up reminders based on severity
    if severity == WelfareSeverity.LOW:
        # 5-day follow-up reminder for teacher
        follow_up_date = obs.observation_date + datetime.timedelta(days=5)
        if not obs.follow_up_required:
            obs.follow_up_required = True
            obs.follow_up_date = follow_up_date
            obs.save(update_fields=["follow_up_required", "follow_up_date"])
    elif severity == WelfareSeverity.MEDIUM:
        # 3-day follow-up reminder for HOD
        follow_up_date = obs.observation_date + datetime.timedelta(days=3)
        if not obs.follow_up_required:
            obs.follow_up_required = True
            obs.follow_up_date = follow_up_date
            obs.save(update_fields=["follow_up_required", "follow_up_date"])


class WelfareDetailView(RoleRequiredMixin, DetailView):
    template_name = "welfare/detail.html"
    context_object_name = "obs"
    allowed_roles = [UserRole.ECD_HOD, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.TEACHER]
    required_permissions_any = ["welfare.view_welfareobservation", "welfare.add_welfareobservation"]

    def get_queryset(self):
        return WelfareObservation.all_objects.select_related("student", "submitted_by", "reviewed_by")

    def get(self, request, *args, **kwargs):
        from django.shortcuts import render
        self.object = self.get_object()
        obs = self.object
        if obs.follow_up_of_id:
            return redirect("welfare:detail", pk=obs.follow_up_of_id)
        if not can_view_observation(request.user, obs):
            # The author of a safeguarding note cannot reopen it, but can
            # still add a follow-up correction for the Safeguarding Lead.
            if obs.is_safeguarding and obs.submitted_by_id == request.user.pk:
                return render(request, "welfare/safeguarding_sent.html", {
                    "obs": obs,
                    "follow_up_count": WelfareObservation.all_objects.filter(follow_up_of=obs).count(),
                    "can_see_hod_dashboard": request.user.has_perm("welfare.can_review_observation"),
                })
            raise PermissionDenied("You do not have access to this welfare note.")
        if obs.is_draft:
            return redirect("welfare:edit", pk=obs.pk)
        return self.render_to_response(self.get_context_data(object=obs))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        obs = self.object
        user = self.request.user
        # NFR-PDPA-006: Log access to welfare record
        from audit.view_audit import log_sensitive_access
        log_sensitive_access(
            self.request, "WelfareObservation", obs.pk, "welfare",
            description=f"Welfare record viewed for {obs.student} (type: {obs.note_type}, severity: {obs.severity})"
        )
        can_review = user.has_perm("welfare.can_review_observation")
        ctx["can_see_hod_dashboard"] = can_review
        ctx["welfare_tab"] = "detail"
        if obs.is_safeguarding:
            ctx["can_review"] = can_view_safeguarding(user)
        else:
            ctx["can_review"] = can_review
        ctx["is_incident_locked"] = obs.hod_status == "resolved"

        from .models import WelfareAcknowledgment
        ctx["user_acknowledged"] = WelfareAcknowledgment.objects.filter(
            observation=obs, user=user
        ).exists()
        ctx["needs_acknowledgment"] = obs.is_safeguarding or obs.severity in [WelfareSeverity.HIGH, WelfareSeverity.CRITICAL]
        ctx["user"] = user

        edit_block = obs.edit_block_reason(user)
        ctx["can_edit"] = not edit_block
        ctx["edit_block_reason"] = edit_block
        ctx["is_author"] = obs.submitted_by_id == user.pk
        ctx["revisions"] = obs.revisions.select_related("edited_by")
        ctx["follow_ups"] = WelfareObservation.all_objects.filter(
            follow_up_of=obs
        ).select_related("submitted_by").order_by("created_at")
        ctx["can_add_follow_up"] = obs.is_safeguarding

        from academics.models import GradeClass, Department
        student_dept = GradeClass.objects.filter(name=obs.student.class_name).values_list("department", flat=True).first()
        ctx["is_ecd_welfare"] = (student_dept == Department.ECD)
        return ctx


class WelfareEditView(RoleRequiredMixin, View):
    """Edit a draft, or correct a submitted note.

    - Drafts: the author edits freely, then submits or deletes the draft.
    - Submitted notes: the author may correct within the correction window
      (before HOD review); a Super Admin may correct any time. A reason is
      required and every change is kept as a WelfareObservationRevision.
    - Safeguarding notes are never editable once submitted.
    """
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permissions_any = ["welfare.add_welfareobservation", "welfare.change_welfareobservation"]

    def _load(self, request, pk):
        obs = get_object_or_404(WelfareObservation.all_objects.select_related("student"), pk=pk)
        block = obs.edit_block_reason(request.user)
        if block:
            if obs.is_draft:
                raise PermissionDenied(block)
            messages.error(request, block)
            return obs, redirect("welfare:detail", pk=pk)
        return obs, None

    def _students(self, request, obs):
        students = _students_for_user(request.user)
        # The note's current child stays selectable even if the teacher has
        # since moved classes, so an unrelated edit can still be saved.
        from students.models import Student
        return (students | Student.objects.filter(pk=obs.student_id)).distinct().order_by("class_name", "last_name")

    def _render(self, request, obs, *, initial, errors=None, mismatch="", status=200):
        from django.shortcuts import render
        mode = "draft" if obs.is_draft else "correct"
        ctx = _note_form_context(
            mode=mode, students=self._students(request, obs), initial=initial, obs=obs,
            errors=errors, mismatch=mismatch,
        )
        ctx.update({
            "welfare_tab": "submit" if obs.is_draft else "detail",
            "page_title": "Edit draft" if obs.is_draft else "Correct welfare note",
            "can_see_hod_dashboard": request.user.has_perm("welfare.can_review_observation"),
            "is_unified_welfare": True,
        })
        return render(request, "welfare/submit.html", ctx, status=status)

    def get(self, request, pk):
        obs, response = self._load(request, pk)
        if response:
            return response
        return self._render(request, obs, initial=_note_initial(obs=obs))

    def post(self, request, pk):
        from audit.models import log_event
        obs, response = self._load(request, pk)
        if response:
            return response
        action = request.POST.get("action", "")

        if obs.is_draft and action == "delete_draft":
            log_event(
                actor=request.user, action_type="welfare_draft_deleted",
                model_name="WelfareObservation", object_id=obs.pk,
                description=f"Welfare draft deleted for {obs.student}", request=request,
            )
            obs.delete()
            messages.success(request, "Draft deleted.")
            return redirect("welfare:list")

        data = _read_note_form(request)
        mode = "draft" if obs.is_draft else "correct"
        final = not (obs.is_draft and action == "draft")
        errors, cleaned = _validate_note_form(
            data, allowed_students=self._students(request, obs),
            earliest_date=obs.created_at.date(), final=final, mode=mode,
        )
        if errors:
            return self._render(
                request, obs, initial=_note_initial(data=data), errors=errors,
                mismatch=cleaned["mismatch"] if errors == [cleaned["mismatch"]] else "", status=400,
            )

        if obs.is_draft:
            obs.student_id = cleaned["student_ids"][0]
            _apply_cleaned(obs, cleaned)
            if action == "draft":
                obs.save()
                messages.success(request, "Draft saved.")
                return redirect("welfare:edit", pk=obs.pk)
            _finalize_submission(request, obs)
            _submitted_message(request, [obs])
            return redirect("welfare:detail", pk=obs.pk)

        # ── Correction of a submitted note ──
        before = _snapshot(obs)
        old_severity = obs.severity
        obs.student_id = cleaned["student_ids"][0]
        _apply_cleaned(obs, cleaned)
        from students.models import Student
        obs.student = Student.objects.get(pk=obs.student_id)
        after = _snapshot(obs)
        changes = {
            field: {"from": before[field], "to": after[field]}
            for field in before if before[field] != after[field]
        }
        if not changes:
            messages.info(request, "No changes to save.")
            return redirect("welfare:detail", pk=obs.pk)

        from .models import WelfareObservationRevision
        obs.last_edited_by = request.user
        obs.last_edited_at = timezone.now()
        obs.save()
        WelfareObservationRevision.objects.create(
            observation=obs, edited_by=request.user,
            reason=data["edit_reason"],
            changes=changes,
        )
        log_event(
            actor=request.user,
            action_type="welfare_observation_edited",
            model_name="WelfareObservation",
            object_id=obs.pk,
            description=f"Welfare note corrected for {obs.student}: {data['edit_reason']}",
            before={f: c["from"] for f, c in changes.items()},
            after={f: c["to"] for f, c in changes.items()},
            request=request,
        )
        if obs.severity in (WelfareSeverity.HIGH, WelfareSeverity.CRITICAL) and old_severity not in (
            WelfareSeverity.HIGH, WelfareSeverity.CRITICAL
        ):
            _escalate_severity(obs, request, obs.severity)

        messages.success(request, "Correction saved. The original wording is kept in the note's history.")
        return redirect("welfare:detail", pk=obs.pk)


class WelfareFollowUpView(RoleRequiredMixin, View):
    """Add a follow-up (correction) to a submitted safeguarding note.

    Safeguarding notes are locked, so any correction — by the author or the
    Safeguarding Lead — is recorded as a separate note linked to the original,
    visible only to the Safeguarding Lead and HOS.
    """
    required_permissions_any = ["welfare.add_welfareobservation", "welfare.view_safeguarding_note"]

    def post(self, request, pk):
        from audit.models import log_event
        obs = get_object_or_404(WelfareObservation.all_objects, pk=pk)
        if not obs.is_safeguarding or obs.is_draft or obs.follow_up_of_id:
            raise PermissionDenied("Follow-up notes can only be added to safeguarding notes.")
        if obs.submitted_by_id != request.user.pk and not can_view_safeguarding(request.user):
            raise PermissionDenied("You do not have access to this safeguarding note.")
        text = request.POST.get("follow_up_text", "").strip()
        if not text:
            messages.error(request, "Please write the follow-up or correction.")
            return redirect("welfare:detail", pk=pk)

        now = timezone.now()
        follow_up = WelfareObservation.all_objects.create(
            student=obs.student,
            submitted_by=request.user,
            note_type=WelfareNoteType.SAFEGUARDING,
            concern_type=WelfareConcernType.OTHER,
            severity=WelfareSeverity.CRITICAL,
            observation_date=date.today(),
            observation_text=text,
            status=WelfareNoteStatus.SUBMITTED,
            submitted_at=now,
            is_locked=True,
            follow_up_of=obs,
        )
        log_event(
            actor=request.user,
            action_type="welfare_safeguarding_follow_up",
            model_name="WelfareObservation",
            object_id=obs.pk,
            description=f"Follow-up note #{follow_up.pk} added to safeguarding note for {obs.student}",
            request=request,
        )
        from communications.email_service import dispatch_notification
        for user in _safeguarding_recipients().exclude(pk=request.user.pk):
            dispatch_notification(
                user=user,
                title=f"Safeguarding follow-up: {obs.student}",
                message="A follow-up has been added to a safeguarding note. Open it in Hodari to read it.",
                link=f"/welfare/{obs.pk}/",
                actor=request.user,
            )
        messages.success(request, "Follow-up added and sent to the Safeguarding Lead.")
        return redirect("welfare:detail", pk=pk)


class WelfareHODDashboardView(DepartmentScopedMixin, RoleRequiredMixin, TemplateView):
    """HOD welfare dashboard - cross-module overview scoped by department.
    Pulls real data from welfare, discipline, and attendance modules.
    Supports unified mode (all departments) when department="all"."""
    template_name = "welfare/hod_dashboard.html"
    allowed_roles = [UserRole.ECD_HOD, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "welfare.view_welfareobservation"

    def get_context_data(self, **kwargs):
        from django.utils import timezone
        from django.db.models import Count, Q
        import datetime
        from collections import Counter
        from students.models import Student
        from academics.models import Department, GradeClass
        from discipline.models import DisciplineIncident, IncidentStatus
        from attendance.models import AttendanceEntry, AttendanceStatus

        ctx = super().get_context_data(**kwargs)
        # Every welfare figure on this dashboard respects note visibility
        # (e.g. HODs never see safeguarding notes).
        visible = visible_observations(self.request.user, WelfareObservation.objects.all())
        ctx["welfare_tab"] = "hod_dashboard"
        ctx["can_see_hod_dashboard"] = True  # This view is only accessible to HOD+ roles

        department = self._get_department()
        is_unified = self._is_unified()
        is_ecd = (department == Department.ECD) if department else False
        ctx["department"] = department
        ctx["is_unified_welfare"] = is_unified
        ctx["is_ecd_welfare"] = is_ecd
        ctx["page_title"] = "Welfare Dashboard"
        ctx["dept_label"] = "All Departments" if is_unified else Department(department).label

        # FR-WEL-008: HOS sees per-ECD-class aggregate breakdown
        is_hos = self.request.user.role == UserRole.HEAD_OF_SCHOOL
        ctx["is_hos_view"] = is_hos
        if is_hos and is_ecd:
            ecd_classes = list(GradeClass.objects.filter(
                department=Department.ECD
            ).values_list("name", flat=True))
            class_grid = []
            for cls_name in ecd_classes:
                cls_obs = visible.filter(student__class_name=cls_name)
                total = cls_obs.count()
                sev = dict(Counter(cls_obs.values_list("severity", flat=True)))
                concern = dict(Counter(cls_obs.values_list("concern_type", flat=True)))
                open_count = cls_obs.exclude(hod_status="resolved").count()
                uncontacted = cls_obs.filter(
                    severity__in=["high", "critical"],
                    parent_contacted=False,
                ).count()
                class_grid.append({
                    "class_name": cls_name,
                    "total": total,
                    "open": open_count,
                    "uncontacted": uncontacted,
                    "severity": sev,
                    "concern": concern,
                })
            ctx["ecd_class_grid"] = class_grid

        # Scope to department's classes (or all ECD+Primary in unified mode)
        if is_unified:
            dept_class_names = self._get_all_welfare_classes()
        else:
            dept_class_names = list(GradeClass.objects.filter(
                department=department
            ).values_list("name", flat=True))

        today = timezone.now().date()
        week_start = today - datetime.timedelta(days=today.weekday())
        week_end = week_start + datetime.timedelta(days=6)
        month_start = today.replace(day=1)

        # ── WELFARE DATA ──────────────────────────────────────────────
        week_obs = visible.filter(
            observation_date__gte=week_start,
            observation_date__lte=week_end,
            student__class_name__in=dept_class_names,
        ).select_related("student", "submitted_by")

        ctx["total_week_entries"] = week_obs.count()
        ctx["severity_counts"] = dict(Counter(week_obs.values_list("severity", flat=True)))
        ctx["concern_type_counts"] = dict(Counter(week_obs.values_list("concern_type", flat=True)))

        # Welfare totals for KPI cards
        welfare_total = visible.filter(
            student__class_name__in=dept_class_names
        ).count()
        welfare_open = visible.filter(
            hod_status__in=["pending", "in_progress"],
            student__class_name__in=dept_class_names,
        ).count()
        welfare_critical = visible.filter(
            severity__in=["high", "critical"],
            hod_status__in=["pending", "in_progress"],
            student__class_name__in=dept_class_names,
        ).count()
        welfare_resolved = visible.filter(
            hod_status="resolved",
            student__class_name__in=dept_class_names,
        ).count()
        welfare_this_month = visible.filter(
            observation_date__gte=month_start,
            student__class_name__in=dept_class_names,
        ).count()

        ctx["welfare_total"] = welfare_total
        ctx["welfare_open"] = welfare_open
        ctx["welfare_critical"] = welfare_critical
        ctx["welfare_resolved"] = welfare_resolved
        ctx["welfare_this_month"] = welfare_this_month
        ctx["welfare_resolution_pct"] = round((welfare_resolved / welfare_total * 100) if welfare_total else 0)

        # FR-WEL-004: Open entries requiring HOD action
        ctx["open_entries"] = visible.filter(
            hod_status__in=["pending", "in_progress"],
            student__class_name__in=dept_class_names,
        ).select_related("student", "submitted_by").order_by("-severity", "-observation_date")[:15]

        # FR-WEL-005: Children with open welfare concerns
        children_with_open = Student.objects.filter(
            pk__in=visible.filter(
                hod_status__in=["pending", "in_progress"],
                student__class_name__in=dept_class_names,
            ).values("student_id"),
        )

        children_data = []
        # Batch-fetch latest open observation per child to avoid N+1
        open_obs = visible.filter(
            hod_status__in=["pending", "in_progress"],
            student__in=children_with_open,
        ).select_related("student").order_by("-severity", "-observation_date")
        obs_by_student = {}
        for obs in open_obs:
            if obs.student_id not in obs_by_student:
                obs_by_student[obs.student_id] = obs
        for child in children_with_open:
            children_data.append({
                "student": child,
                "latest_obs": obs_by_student.get(child.id)
            })

        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        children_data.sort(key=lambda x: (
            severity_order.get(x["latest_obs"].severity, 4),
            x["latest_obs"].observation_date
        ))
        ctx["children_with_open"] = children_data

        # FR-WEL-006: Flag children not contacted after High/Critical entries
        one_day_ago = timezone.now() - datetime.timedelta(days=1)
        ctx["uncontacted_critical"] = visible.filter(
            severity__in=["high", "critical"],
            parent_contacted=False,
            created_at__lt=one_day_ago,
            student__class_name__in=dept_class_names,
        ).select_related("student", "submitted_by")

        ctx["resolved_entries"] = visible.filter(
            hod_status="resolved",
            student__class_name__in=dept_class_names,
        ).select_related("student", "submitted_by", "reviewed_by").order_by("-reviewed_at")[:10]

        # ── DISCIPLINE DATA ────────────────────────────────────────────
        discipline_qs = DisciplineIncident.objects.filter(
            student__class_name__in=dept_class_names
        )
        discipline_month = discipline_qs.filter(
            incident_date__gte=month_start,
        )

        ctx["discipline_total"] = discipline_qs.count()
        ctx["discipline_this_month"] = discipline_month.count()
        ctx["discipline_pending"] = discipline_qs.filter(
            status__in=[IncidentStatus.PENDING_REVIEW, IncidentStatus.UNDER_INVESTIGATION]
        ).count()
        ctx["discipline_severity_counts"] = dict(
            Counter(discipline_month.values_list("severity", flat=True))
        )

        # Recent discipline incidents for the table
        ctx["recent_discipline"] = discipline_qs.select_related(
            "student", "reported_by"
        ).order_by("-incident_date", "-created_at")[:8]

        # ── ATTENDANCE DATA ────────────────────────────────────────────
        attendance_qs = AttendanceEntry.objects.filter(
            class_name__in=dept_class_names,
        )
        attendance_today = attendance_qs.filter(date=today)
        attendance_week = attendance_qs.filter(
            date__gte=week_start,
            date__lte=week_end,
        )

        today_total = attendance_today.count()
        today_present = attendance_today.filter(status=AttendanceStatus.PRESENT).count()
        today_absent = attendance_today.filter(status=AttendanceStatus.ABSENT).count()
        today_late = attendance_today.filter(status=AttendanceStatus.LATE).count()
        today_excused = attendance_today.filter(status=AttendanceStatus.EXCUSED).count()

        ctx["att_today_total"] = today_total
        ctx["att_today_present"] = today_present
        ctx["att_today_absent"] = today_absent
        ctx["att_today_late"] = today_late
        ctx["att_today_excused"] = today_excused
        ctx["att_today_pct"] = round((today_present / today_total * 100) if today_total else 0)

        # Weekly attendance rate
        week_total = attendance_week.count()
        week_present = attendance_week.filter(status=AttendanceStatus.PRESENT).count()
        ctx["att_week_pct"] = round((week_present / week_total * 100) if week_total else 0)

        # Chronic absentees (absent 3+ times this month)
        chronic_pks = list(
            attendance_qs.filter(
                date__gte=month_start,
                status__in=[AttendanceStatus.ABSENT, AttendanceStatus.EXCUSED],
            ).values("student").annotate(
                absence_count=Count("id")
            ).filter(absence_count__gte=3).order_by("-absence_count")[:10].values_list("student", "absence_count")
        )
        student_map = {
            s.pk: s for s in Student.objects.filter(pk__in=[pk for pk, _ in chronic_pks])
        }
        ctx["chronic_absentees"] = [
            {"student": student_map.get(pk), "absence_count": cnt}
            for pk, cnt in chronic_pks if pk in student_map
        ]

        # ── MONTHLY TREND CHART DATA (all modules combined) ────────────
        monthly_trend = []
        for i in range(5, -1, -1):
            m = month_start.month - i
            y = month_start.year
            while m <= 0:
                m += 12
                y -= 1
            ms = month_start.replace(year=y, month=m)
            if ms.month == 12:
                me = ms.replace(year=ms.year + 1, month=1)
            else:
                me = ms.replace(month=ms.month + 1)

            w_count = visible.filter(
                observation_date__gte=ms, observation_date__lt=me,
                student__class_name__in=dept_class_names,
            ).count()
            d_count = DisciplineIncident.objects.filter(
                incident_date__gte=ms, incident_date__lt=me,
                student__class_name__in=dept_class_names,
            ).count()
            monthly_trend.append({
                "month": ms.strftime("%b"),
                "month_full": ms.strftime("%b %Y"),
                "welfare": w_count,
                "discipline": d_count,
                "total": w_count + d_count,
            })
        import json
        ctx["monthly_trend_json"] = json.dumps(monthly_trend)

        return ctx


class WelfareHODReviewView(RoleRequiredMixin, View):
    # FR-WEL-004: HOD of relevant department + SA may review; dept check in post()
    allowed_roles = [UserRole.ECD_HOD, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.SUPER_ADMIN]
    required_permission = "welfare.can_review_observation"

    def post(self, request, pk):
        from core.utils import append_review_comment

        obs = get_object_or_404(WelfareObservation, pk=pk)
        # FR-WEL-004: Block cross-department HOD review
        _assert_observation_department_match(request.user, obs)
        if not can_view_observation(request.user, obs):
            raise PermissionDenied("You do not have access to this welfare note.")
        is_locked = obs.hod_status == "resolved"

        if is_locked:
            comment = request.POST.get("comment", "").strip()
            if not comment:
                messages.error(request, "Please enter a comment.")
                return redirect("welfare:detail", pk=pk)

            before_notes = obs.hod_notes
            obs.hod_notes = append_review_comment(obs.hod_notes, request.user, comment)
            obs.save(update_fields=["hod_notes", "updated_at"])

            from audit.models import log_event
            log_event(
                actor=request.user,
                action_type="welfare_observation_comment_added",
                model_name="WelfareObservation",
                object_id=obs.pk,
                description=f"Comment added on resolved welfare observation for {obs.student}",
                before={"hod_notes": before_notes},
                after={"hod_notes": obs.hod_notes},
                request=request,
            )
            messages.success(request, "Comment added.")
            return redirect("welfare:detail", pk=pk)

        hod_status = request.POST.get("hod_status", "in_progress")
        hod_notes = request.POST.get("hod_notes", "").strip()
        follow_up = request.POST.get("follow_up_required") == "on"
        follow_up_date = request.POST.get("follow_up_date") or None

        # FR-WEL-002: Critical entries cannot be marked Resolved without acknowledgement
        if hod_status == "resolved" and obs.severity == WelfareSeverity.CRITICAL:
            if not obs.is_fully_acknowledged():
                messages.error(
                    request,
                    "Critical welfare observations require both HOD and HOS acknowledgement before being marked as Resolved."
                )
                return redirect("welfare:detail", pk=pk)

        # Store before state for audit
        before_status = obs.hod_status
        before_notes = obs.hod_notes
        
        obs.hod_status = hod_status
        obs.hod_notes = hod_notes
        obs.follow_up_required = follow_up
        obs.follow_up_date = follow_up_date
        obs.reviewed_by = request.user
        obs.reviewed_at = timezone.now()
        obs.save(update_fields=[
            "hod_status", "hod_notes", "follow_up_required",
            "follow_up_date", "reviewed_by", "reviewed_at"
        ])

        # FR-AUD-003: Log HOD review action
        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="welfare_observation_reviewed",
            model_name="WelfareObservation",
            object_id=obs.pk,
            description=f"HOD review updated for {obs.student} - status: {hod_status}",
            before={
                "hod_status": before_status,
                "hod_notes": before_notes,
            },
            after={
                "hod_status": hod_status,
                "hod_notes": hod_notes,
                "follow_up_required": follow_up,
                "follow_up_date": str(follow_up_date) if follow_up_date else None,
            },
            request=request
        )
        
        # Notify submitting teacher when observation is resolved
        if hod_status == "resolved":
            from communications.email_service import dispatch_notification
            dispatch_notification(
                user=obs.submitted_by,
                title="Welfare Observation Resolved",
                message=f"The welfare observation for {obs.student} has been marked as resolved by {request.user.get_full_name()}. Notes: {hod_notes or 'None'}",
                link=f"/welfare/{obs.pk}/",
                actor=request.user,
            )

        messages.success(request, "Review saved.")

        # Generate follow-up task if follow-up is required
        if follow_up:
            try:
                from tasks.services import generate_welfare_followup_task
                generate_welfare_followup_task(obs)
            except Exception:
                pass

        return redirect("welfare:detail", pk=pk)


class WelfareParentConfirmView(RoleRequiredMixin, View):
    """Allow parents (or admins) to digitally confirm receipt of a discipline form."""
    allowed_roles = [UserRole.PARENT, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL]
    required_permission = "welfare.view_welfareobservation"

    def post(self, request, pk):
        obs = get_object_or_404(WelfareObservation, pk=pk)
        
        # Security check: Parents can only confirm for their own children
        if request.user.role == UserRole.PARENT:
            from students.models import Guardian
            if not Guardian.objects.filter(user=request.user, students=obs.student).exists():
                raise PermissionDenied("You do not have permission to confirm this record.")

        obs.parent_confirmed = True
        obs.parent_confirmation_date = timezone.now()
        obs.save(update_fields=["parent_confirmed", "parent_confirmation_date"])

        # Log the confirmation
        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="welfare_parent_confirmed",
            model_name="WelfareObservation",
            object_id=obs.pk,
            description=f"Parent receipt confirmed for welfare observation of {obs.student}",
            after={
                "parent_confirmed": True,
                "parent_confirmation_date": str(obs.parent_confirmation_date),
            },
            request=request
        )

        messages.success(request, "Confirmation receipt recorded. Thank you.")
        return redirect("welfare:detail", pk=pk)


class WelfareAcknowledgeView(RoleRequiredMixin, View):
    """Allow HOD and HOS to acknowledge high/critical welfare observations."""
    allowed_roles = [UserRole.ECD_HOD, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "welfare.can_review_observation"

    def post(self, request, pk):
        from .models import WelfareAcknowledgment
        
        obs = get_object_or_404(WelfareObservation, pk=pk)
        # FR-WEL-004: Block cross-department acknowledgment
        _assert_observation_department_match(request.user, obs)
        if not can_view_observation(request.user, obs):
            raise PermissionDenied("You do not have access to this welfare note.")
        
        # Check if user already acknowledged
        if WelfareAcknowledgment.objects.filter(observation=obs, user=request.user).exists():
            messages.warning(request, "You have already acknowledged this observation.")
            return redirect("welfare:detail", pk=pk)
        
        # Create acknowledgment
        WelfareAcknowledgment.objects.create(
            observation=obs,
            user=request.user
        )
        
        # Log the event
        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="welfare_observation_acknowledged",
            model_name="WelfareObservation",
            object_id=obs.pk,
            description=f"{request.user.role} acknowledged welfare observation for {obs.student}",
            request=request
        )
        
        messages.success(request, "Acknowledgment recorded.")
        return redirect("welfare:detail", pk=pk)
