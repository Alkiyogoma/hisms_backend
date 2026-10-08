from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Case, Count, F, Q, Value, When
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView, View

from academics.models import Department, GradeClass
from audit.models import log_event
from core.permissions import RoleRequiredMixin
from core.scoping import DepartmentScopedMixin
from core.teacher_context import get_teacher_assigned_classes
from users.models import UserRole

from students.models import Student
from .consistency import notes_conflicts
from .forms import (
    CONTACT_FIELDS,
    DisciplineAmendForm,
    DisciplineIncidentForm,
    DisciplineReviewForm,
    apply_parent_contact,
    parent_contact_initial,
)
from .models import (
    INCIDENT_BEHAVIOURS,
    LEVEL_SEVERITY,
    LOCATIONS,
    SCHOOL_ACTIONS,
    DisciplineIncident,
    IncidentAmendment,
    IncidentSeverity,
    IncidentStatus,
    severity_for_levels,
)

STAFF_TITLES = {
    UserRole.SUPER_ADMIN: "Super Admin",
    UserRole.HEAD_OF_SCHOOL: "Head of School",
    UserRole.PRIMARY_HOD: "Head of Primary",
    UserRole.LOWER_SECONDARY_HOD: "Head of Lower Secondary",
    UserRole.ECD_HOD: "Head of ECD",
}


def staff_name(user):
    profile = getattr(user, "staff_profile", None)
    return (profile.full_name if profile else "") or user.get_full_name() or user.username


def staff_title(user):
    """The role printed beside a member of staff's name, from their user record."""
    return STAFF_TITLES.get(user.role) or user.get_role_display()


def is_shared_account(user):
    """System accounts (Django superusers) are not a named member of staff, so they
    cannot be recorded as the person who reported or amended an incident."""
    return user.is_superuser


SHARED_ACCOUNT_MESSAGE = (
    "This is a shared system account, not a named member of staff. "
    "Sign in with your own account to record this."
)


def can_amend(user):
    """The super admin and the Head of School may correct a submitted incident."""
    return user.has_role(UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL)



def _primary_only_head(user):
    """Heads Primary but no other section and is not school-wide."""
    return (
        not user.is_school_wide
        and user.has_role(UserRole.PRIMARY_HOD)
        and set(user.section_departments) <= {"PRIMARY"}
    )


def _heads_student_section(user, student):
    """True if ``student``'s class is in a section ``user`` heads (or user is school-wide)."""
    if user.is_school_wide:
        return True
    from core.scoping import hod_class_names
    classes = hod_class_names(user)
    return classes is None or student.class_name in classes

def visible_incidents(user):
    """Incidents ``user`` may see: teachers their own reports, a head of section
    the learners in the sections they head, school-wide staff everything."""
    from core.scoping import hod_class_names
    qs = DisciplineIncident.objects.select_related("student", "reported_by", "reviewed_by")
    if user.role == UserRole.TEACHER:
        return qs.filter(reported_by=user)
    classes = hod_class_names(user)
    if classes is not None:
        qs = qs.filter(Q(student__class_name__in=classes) | Q(reported_by=user))
    return qs


def search_incidents(qs, text):
    """Match a learner's name or admission number, or a reference such as DISC-12."""
    words = text.split()
    if not words:
        return qs
    names = Q()
    for word in words:
        names &= Q(student__first_name__icontains=word) | Q(student__last_name__icontains=word)
    match = names | Q(student__admission_no__icontains=text.strip())
    ref = text.strip().upper().removeprefix("DISC-").removeprefix("#")
    if ref.isdigit():
        match |= Q(pk=int(ref))
    return qs.filter(match)


class DisciplineSubmitView(DepartmentScopedMixin, RoleRequiredMixin, CreateView):
    """Teacher-facing form to submit a new discipline incident."""
    model = DisciplineIncident
    form_class = DisciplineIncidentForm
    template_name = "discipline/submit.html"
    allowed_roles = [
        UserRole.TEACHER,
        UserRole.PRIMARY_HOD,
        UserRole.HEAD_OF_SCHOOL,
        UserRole.SUPER_ADMIN,
    ]
    required_permission = "discipline.add_disciplineincident"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        if is_shared_account(self.request.user):
            form.add_error(None, SHARED_ACCOUNT_MESSAGE)
            return self.form_invalid(form)
        incident = form.save(commit=False)
        incident.reported_by = self.request.user

        # Process detailed fields from POST
        raw_location = self.request.POST.get("location", "").strip()
        if raw_location == "__other__":
            raw_location = self.request.POST.get("location_other", "").strip()
        incident.location = raw_location
        incident.incident_level_1 = self.request.POST.getlist("level_1")
        incident.incident_level_2 = self.request.POST.getlist("level_2")
        incident.incident_level_3 = self.request.POST.getlist("level_3")
        incident.incident_level_4 = self.request.POST.getlist("level_4")
        incident.actions_taken_detailed = self.request.POST.getlist("actions_detailed")
        apply_parent_contact(incident, form.cleaned_data)

        # Auto-escalate High severity
        if incident.severity == IncidentSeverity.HIGH:
            incident.escalated = True

        incident.save()

        # Log to audit trail
        log_event(
            actor=self.request.user,
            action_type="discipline_incident_created",
            model_name="DisciplineIncident",
            object_id=incident.pk,
            description=f"Behaviour incident created for {incident.student} - {incident.get_severity_display()}",
            after={
                "student_id": incident.student.pk,
                "severity": incident.severity,
                "summary": incident.summary[:200],
            },
            request=self.request,
        )

        # Notify HOD on High/Critical severity
        if incident.severity == IncidentSeverity.HIGH or incident.severity == IncidentSeverity.CRITICAL:
            self._notify_hod(incident)

        messages.success(self.request, f"Behaviour incident recorded for {incident.student.first_name}.")
        return redirect("discipline:detail", pk=incident.pk)

    def form_invalid(self, form):
        messages.error(self.request, "Please correct the errors below.")
        return super().form_invalid(form)

    def _notify_hod(self, incident):
        try:
            from communications.email_service import dispatch_notification
            from users.models import User

            # Determine appropriate HOD from student's class department
            dept = GradeClass.objects.filter(
                name=incident.student.class_name
            ).values_list("department", flat=True).first()

            hod_roles = [UserRole.PRIMARY_HOD]

            hods = User.objects.filter(role__in=hod_roles, is_active=True).distinct()
            for hod in hods:
                dispatch_notification(
                    user=hod,
                    title=f"{incident.get_severity_display()} behaviour incident: {incident.student}",
                    message=(
                        f"A {incident.get_severity_display()} behaviour incident has been "
                        f"submitted for {incident.student.first_name} {incident.student.last_name} "
                        f"({incident.student.class_name}).\n\n"
                        f"{incident.summary[:300]}"
                    ),
                    link=f"/behaviour/{incident.pk}/",
                    actor=self.request.user,
                )
        except Exception:
            pass  # Never block on notification failure

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["discipline_tab"] = "submit"
        ctx["severity_choices"] = IncidentSeverity.choices
        ctx["today_date"] = date.today().strftime("%d %B %Y")
        ctx["today_date_iso"] = date.today().strftime("%Y-%m-%d")
        # Re-fill the form after a failed submit, so nothing typed is lost.
        posted = self.request.POST if self.request.method == "POST" else None
        ctx["posted"] = posted
        ctx["posted_lists"] = {
            name: posted.getlist(name) if posted else []
            for name in ("level_1", "level_2", "level_3", "level_4", "actions_detailed")
        }
        ctx["behaviours"] = INCIDENT_BEHAVIOURS
        ctx["school_actions"] = SCHOOL_ACTIONS
        ctx["locations"] = LOCATIONS
        ctx["shared_account"] = is_shared_account(self.request.user)

        # FRD: Behaviour is PRIMARY + SECONDARY only — exclude ECD department
        non_ecd_classes = self._get_non_ecd_classes()

        role = self.request.user.role
        if role == UserRole.TEACHER:
            teacher_classes = set(get_teacher_assigned_classes(self.request.user))
            ctx["available_classes"] = sorted(teacher_classes & set(non_ecd_classes))
        else:
            ctx["available_classes"] = non_ecd_classes

        # Students: only from available classes
        if role == UserRole.TEACHER:
            ctx["students"] = Student.objects.filter(
                class_name__in=ctx["available_classes"], is_archived=False
            ).order_by("class_name", "last_name")
        else:
            ctx["students"] = Student.objects.filter(
                class_name__in=non_ecd_classes, is_archived=False
            ).order_by("class_name", "last_name")

        # Previous incidents come from the learner's record, never from the reporter.
        # The form counts those on or before the chosen incident date.
        dates = {}
        records = (
            DisciplineIncident.objects.filter(student__in=ctx["students"])
            .exclude(status=IncidentStatus.DISMISSED)
            .only("student_id", "incident_date", "created_at")
        )
        for record in records:
            dates.setdefault(record.student_id, []).append(record.occurred_on.isoformat())
        ctx["previous_dates"] = {pk: ",".join(sorted(d)) for pk, d in dates.items()}
        return ctx


class DisciplineListView(DepartmentScopedMixin, RoleRequiredMixin, ListView):
    """List discipline incidents — teacher sees own, HOD sees department."""
    model = DisciplineIncident
    template_name = "discipline/list.html"
    context_object_name = "incidents"
    paginate_by = 25
    allowed_roles = [
        UserRole.TEACHER,
        UserRole.PRIMARY_HOD,
        UserRole.HEAD_OF_SCHOOL,
        UserRole.SUPER_ADMIN,
    ]
    required_permission = "discipline.view_disciplineincident"

    FILTERS = ("q", "status", "severity", "class_name", "student")

    def scoped(self):
        return visible_incidents(self.request.user)

    def get_queryset(self):
        qs = self.scoped()
        get = self.request.GET
        if get.get("q", "").strip():
            qs = search_incidents(qs, get["q"])
        if get.get("status"):
            qs = qs.filter(status=get["status"])
        if get.get("severity"):
            qs = qs.filter(severity=get["severity"])
        if get.get("student", "").isdigit():
            qs = qs.filter(student_id=get["student"])
        if get.get("class_name"):
            qs = qs.filter(student__class_name=get["class_name"])
        # Newest incident first, by the date it happened.
        return qs.annotate(amendment_count=Count("amendments")).order_by(
            F("incident_date").desc(nulls_last=True), "-created_at"
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["discipline_tab"] = "list"
        ctx["today_date"] = timezone.localdate().strftime("%d %B %Y")
        ctx["status_choices"] = IncidentStatus.choices
        ctx["severity_choices"] = IncidentSeverity.choices
        ctx["current_filters"] = {k: self.request.GET[k] for k in self.FILTERS if self.request.GET.get(k)}
        # Keeps the filters when moving between pages.
        query = self.request.GET.copy()
        query.pop("page", None)
        ctx["filter_query"] = query.urlencode()

        # The cards count everything this user can see, whatever is filtered below.
        counts = self.scoped().aggregate(
            total=Count("pk"),
            pending=Count("pk", filter=Q(status=IncidentStatus.PENDING_REVIEW)),
            investigating=Count("pk", filter=Q(status=IncidentStatus.UNDER_INVESTIGATION)),
            resolved=Count("pk", filter=Q(status=IncidentStatus.RESOLVED)),
        )
        ctx["stats"] = counts

        # FRD: Behaviour list filter — Primary + Secondary only (no ECD)
        if _primary_only_head(self.request.user):
            ctx["available_classes"] = self._get_dept_classes(Department.PRIMARY)
        else:
            ctx["available_classes"] = self._get_non_ecd_classes()

        return ctx


class DisciplineDetailView(RoleRequiredMixin, DetailView):
    """View a single discipline incident."""
    model = DisciplineIncident
    template_name = "discipline/detail.html"
    context_object_name = "incident"
    allowed_roles = [
        UserRole.TEACHER,
        UserRole.PRIMARY_HOD,
        UserRole.HEAD_OF_SCHOOL,
        UserRole.SUPER_ADMIN,
    ]
    required_permission = "discipline.view_disciplineincident"

    def get_queryset(self):
        # Teachers see their own reports; heads of section their own sections.
        return visible_incidents(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["discipline_tab"] = "detail"
        incident = self.object
        locked = incident.status in {IncidentStatus.RESOLVED, IncidentStatus.DISMISSED}
        ctx["is_incident_locked"] = locked
        ctx["can_review"] = self.request.user.has_perm("discipline.can_review_incident")
        ctx["review_form"] = kwargs.get("review_form") or DisciplineReviewForm(
            incident=incident,
            locked=locked,
            initial={"status": incident.status, "hod_notes": incident.hod_notes,
                     **parent_contact_initial(incident)},
        )
        ctx["reviewer_title"] = staff_title(self.request.user)
        ctx["can_amend"] = can_amend(self.request.user)
        ctx["amendments"] = list(incident.amendments.select_related("changed_by"))
        for amendment in ctx["amendments"]:
            amendment.by_name = staff_name(amendment.changed_by)
            amendment.by_title = staff_title(amendment.changed_by)
        ctx["last_amended"] = ctx["amendments"][-1].changed_at if ctx["amendments"] else None

        earlier = incident.earlier_incidents()
        ctx["previous_count"] = len(earlier)
        ctx["previous_latest"] = max((i.occurred_on for i in earlier), default=None)

        ctx["record_conflicts"] = notes_conflicts(
            [incident.summary, incident.action_taken, incident.hod_notes],
            incident.parent_contacted,
            incident.follow_up_required,
        )

        reporter, reviewer = incident.reported_by, incident.reviewed_by
        ctx["reporter_name"], ctx["reporter_title"] = staff_name(reporter), staff_title(reporter)
        if reviewer:
            ctx["reviewer_name"], ctx["reviewed_by_title"] = staff_name(reviewer), staff_title(reviewer)
            ctx["same_reviewer"] = reviewer.pk == reporter.pk

        photo = incident.student.photo_file()
        ctx["photo_src"] = photo.url if photo else None
        ctx["photo_checked"] = True
        ctx["issued_on"] = timezone.localdate()
        return ctx


class DisciplineReviewView(DepartmentScopedMixin, RoleRequiredMixin, View):
    """Review action — update status and add notes."""
    allowed_roles = [
        UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD,
        UserRole.HEAD_OF_SCHOOL,
        UserRole.SUPER_ADMIN,
    ]
    required_permission = "discipline.can_review_incident"

    def post(self, request, pk):
        from core.utils import append_review_comment

        # Department scoping: HODs can only review incidents from their department
        from users.models import UserRole
        incident = get_object_or_404(DisciplineIncident, pk=pk)
        if request.user.has_role(UserRole.PRIMARY_HOD, UserRole.ECD_HOD):
            if not _heads_student_section(request.user, incident.student):
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied("You can only review incidents from your own department.")
        is_locked = incident.status in {IncidentStatus.RESOLVED, IncidentStatus.DISMISSED}
        form = DisciplineReviewForm(request.POST, incident=incident, locked=is_locked)
        if not form.is_valid():
            # Show the page again with the reviewer's entries and what to fix.
            view = DisciplineDetailView()
            view.setup(request, pk=pk)
            view.object = view.get_object()
            messages.error(request, "Not saved. Fix the points below and save again.")
            return view.render_to_response(view.get_context_data(review_form=form))

        contact_fields = CONTACT_FIELDS
        before = {f: str(getattr(incident, f)) for f in ["status", "hod_notes"] + contact_fields}
        apply_parent_contact(incident, form.cleaned_data)

        if is_locked:
            incident.hod_notes = append_review_comment(
                incident.hod_notes, request.user, form.cleaned_data["comment"]
            )
            incident.save(update_fields=["hod_notes", *contact_fields, "updated_at"])
            action, description, done = (
                "discipline_incident_comment_added",
                f"Comment added on closed discipline incident for {incident.student}",
                "Comment added.",
            )
        else:
            incident.status = form.cleaned_data["status"]
            incident.hod_notes = form.cleaned_data.get("hod_notes", "")
            incident.reviewed_by = request.user
            incident.reviewed_at = timezone.now()
            incident.save(update_fields=[
                "status", "hod_notes", "reviewed_by", "reviewed_at", *contact_fields, "updated_at"
            ])
            action, description, done = (
                "discipline_incident_reviewed",
                f"Review for {incident.student}: {incident.get_status_display()}",
                f"{incident.reference} marked as {incident.get_status_display()}.",
            )

        log_event(
            actor=request.user,
            action_type=action,
            model_name="DisciplineIncident",
            object_id=incident.pk,
            description=description,
            before=before,
            after={f: str(getattr(incident, f)) for f in before},
            request=request,
        )
        messages.success(request, done)
        return redirect("discipline:detail", pk=pk)


def _amend_values(incident):
    """Each amendable field as people read it: (label, value, is_free_text)."""
    def joined(items):
        return ", ".join(items) if items else "None"

    values = [
        ("Student", f"{incident.student.first_name} {incident.student.last_name} ({incident.student.class_name})", False),
        ("Date of incident", f"{incident.incident_date.day} {incident.incident_date:%B %Y}" if incident.incident_date else "Not recorded", False),
        ("Time of incident", f"{incident.time_of_incident:%H:%M}" if incident.time_of_incident else "Not recorded", False),
        ("Location", incident.location or "Not recorded", False),
        ("Level", incident.get_severity_display(), False),
    ]
    for n in range(1, 5):
        values.append((f"Level {n} behaviours", joined(getattr(incident, f"incident_level_{n}")), False))
    values += [
        ("Actions taken", joined(incident.actions_taken_detailed), False),
        ("Incident summary", incident.summary, True),
        ("Action details", incident.action_taken or "None", True),
    ]
    return values


class DisciplineAmendView(RoleRequiredMixin, UpdateView):
    """Correct a submitted incident. The reason, the previous and new values, who
    changed it and when are kept and shown on the record."""
    model = DisciplineIncident
    form_class = DisciplineAmendForm
    template_name = "discipline/amend.html"
    context_object_name = "incident"
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "discipline.view_disciplineincident"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not can_amend(request.user):
            raise PermissionDenied("Only the super admin and the Head of School can amend an incident.")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return super().get_queryset().select_related("student")

    def get_form(self, form_class=None):
        # Snapshot the record before the form writes the new values onto it.
        self.before = _amend_values(self.get_object())
        return super().get_form(form_class)

    def form_valid(self, form):
        if is_shared_account(self.request.user):
            form.add_error(None, SHARED_ACCOUNT_MESSAGE)
            return self.form_invalid(form)
        incident = form.save(commit=False)
        incident.severity = severity_for_levels(form.ticked_levels())
        if incident.severity in (IncidentSeverity.HIGH, IncidentSeverity.CRITICAL):
            incident.escalated = True
        changes = [
            {"field": label, "before": old, "after": new, "text": text}
            for (label, old, text), (_, new, _) in zip(self.before, _amend_values(incident))
            if old != new
        ]
        if not changes:
            form.add_error(None, "Nothing has been changed, so there is nothing to save.")
            return self.form_invalid(form)
        incident.save()
        reason = form.cleaned_data["reason"]
        IncidentAmendment.objects.create(
            incident=incident, changed_by=self.request.user, reason=reason, changes=changes,
        )
        log_event(
            actor=self.request.user,
            action_type="discipline_incident_amended",
            model_name="DisciplineIncident",
            object_id=incident.pk,
            description=f"{incident.reference} amended: {reason[:200]}",
            before={c["field"]: c["before"] for c in changes},
            after={c["field"]: c["after"] for c in changes},
            request=self.request,
        )
        messages.success(
            self.request,
            f"{incident.reference} amended. If a copy has already gone home, print this record "
            "and send it again: it replaces the earlier copy.",
        )
        return redirect("discipline:detail", pk=incident.pk)

    def form_invalid(self, form):
        messages.error(self.request, "Not saved. Fix the points below and save again.")
        return super().form_invalid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["discipline_tab"] = "detail"
        ctx["locations"] = LOCATIONS
        ctx["shared_account"] = is_shared_account(self.request.user)
        return ctx


class DisciplineHODQueueView(DepartmentScopedMixin, RoleRequiredMixin, TemplateView):
    """Incidents awaiting review by whoever is responsible for the learner's section."""
    template_name = "discipline/hod_queue.html"
    allowed_roles = [
        UserRole.PRIMARY_HOD,
        UserRole.HEAD_OF_SCHOOL,
        UserRole.SUPER_ADMIN,
    ]
    required_permission = "discipline.view_disciplineincident"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["discipline_tab"] = "queue"
        ctx["today_date"] = timezone.localdate().strftime("%d %B %Y")

        # FRD: Behaviour is Primary + Secondary only — exclude ECD
        if _primary_only_head(self.request.user):
            classes = self._get_dept_classes(Department.PRIMARY)
        else:
            classes = self._get_non_ecd_classes()

        # Pending incidents, most serious first (severity is text, so rank it).
        rank = Case(
            *[When(severity=sev, then=Value(level)) for level, sev in LEVEL_SEVERITY.items()],
            default=Value(0),
        )
        pending = visible_incidents(self.request.user).filter(
            student__class_name__in=list(classes),
            status=IncidentStatus.PENDING_REVIEW,
        ).order_by(rank.desc(), F("incident_date").desc(nulls_last=True), "-created_at")

        ctx["pending_incidents"] = pending
        ctx["pending_count"] = pending.count()

        # Stats
        ctx["stats"] = {
            "total": visible_incidents(self.request.user).filter(
                student__class_name__in=list(classes)
            ).count(),
            "pending": pending.count(),
            "high_critical": pending.filter(
                severity__in=[IncidentSeverity.HIGH, IncidentSeverity.CRITICAL]
            ).count(),
        }

        return ctx


class DisciplineParentConfirmView(DepartmentScopedMixin, RoleRequiredMixin, View):
    """Parent digital confirmation of a discipline incident."""
    allowed_roles = [
        UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD,
        UserRole.HEAD_OF_SCHOOL,
        UserRole.SUPER_ADMIN,
        UserRole.PARENT,
    ]
    required_permission = "discipline.change_disciplineincident"

    def post(self, request, pk):
        incident = get_object_or_404(DisciplineIncident, pk=pk)

        # Department scoping: HODs can only confirm incidents from their department
        from users.models import UserRole
        if request.user.has_role(UserRole.PRIMARY_HOD, UserRole.ECD_HOD):
            if not _heads_student_section(request.user, incident.student):
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied("You can only confirm incidents from your own department.")

        # Parent scoping: parents can only confirm incidents for their own child
        if request.user.role == UserRole.PARENT:
            guardian_profile = getattr(request.user, 'guardian_profile', None)
            if not guardian_profile:
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied("No parent profile linked to your account.")
            from students.models import StudentGuardian
            if not StudentGuardian.objects.filter(guardian=guardian_profile, student=incident.student).exists():
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied("You can only confirm incidents for your own child.")

        # Idempotency guard
        if incident.parent_confirmed:
            messages.info(request, "Parent confirmation was already recorded.")
            return redirect("discipline:detail", pk=pk)
        incident.parent_confirmed = True
        incident.parent_confirmation_date = timezone.now()
        incident.save(update_fields=["parent_confirmed", "parent_confirmation_date", "updated_at"])

        log_event(
            actor=request.user,
            action_type="discipline_parent_confirmed",
            model_name="DisciplineIncident",
            object_id=incident.pk,
            description=f"Parent digitally confirmed receipt for incident #{incident.pk} ({incident.student})",
            after={
                "parent_confirmed": True,
                "parent_confirmation_date": str(incident.parent_confirmation_date),
            },
            request=request,
        )

        messages.success(request, "Parent confirmation recorded.")
        return redirect("discipline:detail", pk=pk)
