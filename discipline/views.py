from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, TemplateView, View

from academics.models import Department, GradeClass
from audit.models import log_event
from core.permissions import RoleRequiredMixin
from core.scoping import DepartmentScopedMixin
from core.teacher_context import get_teacher_assigned_classes
from users.models import UserRole

from students.models import Student
from .forms import DisciplineIncidentForm, DisciplineReviewForm
from .models import DisciplineIncident, IncidentSeverity, IncidentStatus


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
        incident = form.save(commit=False)
        incident.reported_by = self.request.user

        # Process detailed fields from POST
        incident.incident_date = self.request.POST.get("incident_date") or None
        incident.time_of_incident = self.request.POST.get("time_of_incident") or None
        raw_location = self.request.POST.get("location", "").strip()
        if raw_location == "__other__":
            raw_location = self.request.POST.get("location_other", "").strip()
        incident.location = raw_location
        incident.previous_incidents = self.request.POST.get("previous_incidents") == "on"
        incident.incident_level_1 = self.request.POST.getlist("level_1")
        incident.incident_level_2 = self.request.POST.getlist("level_2")
        incident.incident_level_3 = self.request.POST.getlist("level_3")
        incident.incident_level_4 = self.request.POST.getlist("level_4")
        incident.actions_taken_detailed = self.request.POST.getlist("actions_detailed")

        # Parent contact & follow-up
        incident.parent_contacted = self.request.POST.get("parent_contacted") == "on"
        contact_date = self.request.POST.get("parent_contact_date")
        contact_time = self.request.POST.get("parent_contact_time")
        if incident.parent_contacted and contact_date and contact_time:
            try:
                from datetime import datetime as dt
                incident.parent_contact_datetime = dt.strptime(f"{contact_date} {contact_time}", "%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pass  # Invalid date/time format — leave field as None
        incident.follow_up_required = self.request.POST.get("follow_up_required") == "on"
        incident.follow_up_date = self.request.POST.get("follow_up_date") or None

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

    def get_queryset(self):
        qs = DisciplineIncident.objects.select_related(
            "student", "reported_by"
        ).order_by("-created_at")

        role = self.request.user.role

        # Teachers see only their own reports
        if role == UserRole.TEACHER:
            qs = qs.filter(reported_by=self.request.user)

        # Apply filters
        status = self.request.GET.get("status")
        if status:
            qs = qs.filter(status=status)

        severity = self.request.GET.get("severity")
        if severity:
            qs = qs.filter(severity=severity)

        student = self.request.GET.get("student")
        if student:
            qs = qs.filter(student__id=student)

        class_name = self.request.GET.get("class_name")
        if class_name:
            qs = qs.filter(student__class_name=class_name)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["discipline_tab"] = "list"
        ctx["status_choices"] = IncidentStatus.choices
        ctx["severity_choices"] = IncidentSeverity.choices
        ctx["current_filters"] = {
            k: v for k, v in self.request.GET.items() if v
        }

        # Stats summary
        qs = self.get_queryset()
        ctx["stats"] = {
            "total": qs.count(),
            "pending": qs.filter(status=IncidentStatus.PENDING_REVIEW).count(),
            "resolved": qs.filter(status=IncidentStatus.RESOLVED).count(),
        }

        # FRD: Behaviour list filter — Primary + Secondary only (no ECD)
        role = self.request.user.role
        if role == UserRole.PRIMARY_HOD:
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
        qs = super().get_queryset().select_related(
            "student", "reported_by", "reviewed_by"
        )
        # Teachers can only see their own incidents
        if self.request.user.role == UserRole.TEACHER:
            qs = qs.filter(reported_by=self.request.user)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["discipline_tab"] = "detail"
        ctx["review_form"] = DisciplineReviewForm(
            initial={"status": self.object.status, "hod_notes": self.object.hod_notes}
        )
        ctx["can_review"] = self.request.user.has_perm("discipline.can_review_incident")
        ctx["is_incident_locked"] = self.object.status in {
            IncidentStatus.RESOLVED,
            IncidentStatus.DISMISSED,
        }
        return ctx


class DisciplineReviewView(DepartmentScopedMixin, RoleRequiredMixin, View):
    """HOD review action — update status and add notes."""
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
        if request.user.role in (UserRole.PRIMARY_HOD, UserRole.ECD_HOD):
            if not self._check_teacher_department(request.user, incident.student):
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied("You can only review incidents from your own department.")
        is_locked = incident.status in {IncidentStatus.RESOLVED, IncidentStatus.DISMISSED}

        if is_locked:
            comment = request.POST.get("comment", "").strip()
            if not comment:
                messages.error(request, "Please enter a comment.")
                return redirect("discipline:detail", pk=pk)

            before_notes = incident.hod_notes
            incident.hod_notes = append_review_comment(incident.hod_notes, request.user, comment)
            incident.save(update_fields=["hod_notes", "updated_at"])

            log_event(
                actor=request.user,
                action_type="discipline_incident_comment_added",
                model_name="DisciplineIncident",
                object_id=incident.pk,
                description=f"Comment added on closed discipline incident for {incident.student}",
                before={"hod_notes": before_notes},
                after={"hod_notes": incident.hod_notes},
                request=request,
            )
            messages.success(request, "Comment added.")
            return redirect("discipline:detail", pk=pk)

        form = DisciplineReviewForm(request.POST)

        if form.is_valid():
            before_status = incident.status
            incident.status = form.cleaned_data["status"]
            incident.hod_notes = form.cleaned_data.get("hod_notes", "")
            incident.reviewed_by = request.user
            incident.reviewed_at = timezone.now()
            incident.save(update_fields=[
                "status", "hod_notes", "reviewed_by", "reviewed_at", "updated_at"
            ])

            # Log to audit trail
            log_event(
                actor=request.user,
                action_type="discipline_incident_reviewed",
                model_name="DisciplineIncident",
                object_id=incident.pk,
                description=f"HOD review for {incident.student}: {incident.get_status_display()}",
                before={"status": before_status},
                after={"status": incident.status, "hod_notes": incident.hod_notes[:200]},
                request=request,
            )

            messages.success(request, f"Incident #{incident.pk} marked as {incident.get_status_display()}.")
        else:
            messages.error(request, "Invalid review submission.")

        return redirect("discipline:detail", pk=pk)


class DisciplineHODQueueView(DepartmentScopedMixin, RoleRequiredMixin, TemplateView):
    """Dedicated HOD approval queue showing pending incidents for their department."""
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

        role = self.request.user.role

        # FRD: Behaviour is Primary + Secondary only — exclude ECD
        if role == UserRole.PRIMARY_HOD:
            classes = self._get_dept_classes(Department.PRIMARY)
        else:
            classes = self._get_non_ecd_classes()

        # Pending incidents
        pending = DisciplineIncident.objects.filter(
            student__class_name__in=list(classes),
            status=IncidentStatus.PENDING_REVIEW,
        ).select_related("student", "reported_by").order_by("-severity", "-created_at")

        ctx["pending_incidents"] = pending
        ctx["pending_count"] = pending.count()

        # Stats
        ctx["stats"] = {
            "total": DisciplineIncident.objects.filter(
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
        if request.user.role in (UserRole.PRIMARY_HOD, UserRole.ECD_HOD):
            if not self._check_teacher_department(request.user, incident.student):
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied("You can only confirm incidents from your own department.")

        # Parent scoping: parents can only confirm incidents for their own child
        if request.user.role == UserRole.PARENT:
            guardian_profile = getattr(request.user, 'guardian_profile', None)
            if not guardian_profile:
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied("No guardian profile linked to your account.")
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
