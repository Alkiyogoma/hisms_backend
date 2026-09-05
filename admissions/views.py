import re
import threading
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.db.models import Prefetch
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView, TemplateView
from django.views import View
from django.http import JsonResponse


def _strip_html(value):
    """Strip HTML tags from user input to prevent stored XSS."""
    if not value:
        return value
    clean = re.sub(r'<[^>]+>', '', value)
    return clean.strip()

from admissions.forms import ApplicantCreateForm, ApplicantEditForm, ApplicantFilterForm
from admissions.mixins import AdmissionsCountsMixin, PermissionCacheMixin
from django.shortcuts import get_object_or_404, redirect, render

from admissions.forms_workflow import AssessmentScheduleForm, AssessmentResultForm, HodReviewForm, MeetingScheduleForm
from academics.models import GradeClass
from admissions.models import Applicant, ApplicantDocumentType, ApplicantStatus, AssessmentSchedule, ApplicantTimelineEntry
from admissions.services import (
    complete_enrolment,
    confirm_assessment_fee_paid,
    ensure_default_documents,
    ensure_enrolment_checklist,
    get_allowed_transition_targets,
    mark_orientation_visit_completed,
    mark_logistics_sent,
    schedule_assessment,
    sign_off_assessment_result,
    submit_hos_review,
    toggle_document_received,
    transition_applicant_status,
)

from users.models import UserRole


class HtmxRequiredMixin:
    """Mixin for views that serve HTMX partials.
    Non-HTMX GET/POST requests redirect to the applicant detail page
    instead of rendering a bare partial without layout/CSS."""

    def _is_htmx(self):
        return self.request.headers.get("HX-Request") == "true"

    def _applicant_pk(self):
        return self.kwargs.get("pk")

    def _redirect_to_detail(self):
        return redirect("admissions:detail", pk=self._applicant_pk())

    def get(self, request, *args, **kwargs):
        if not self._is_htmx():
            return self._redirect_to_detail()
        return super().get(request, *args, **kwargs)

    def render_to_response(self, context, **response_kwargs):
        if not self._is_htmx():
            return self._redirect_to_detail()
        return super().render_to_response(context, **response_kwargs)


class AdmissionsRoleRequiredMixin(LoginRequiredMixin):
    """Permission-based mixin for admissions views.
    Set `required_permission` on the view class to control access.
    Super Admin always passes. Uses has_perm() for all other roles,
    so access is driven by UI role assignment, not hardcoded role sets.
    Finance Officers are excluded from admissions by default.
    Set allow_finance_officer = True on views FO should access (e.g. fee confirm).
    """
    login_url = "/accounts/login/"
    required_permission = "admissions.view_applicant"
    allow_finance_officer = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role == UserRole.SUPER_ADMIN:
            return super().dispatch(request, *args, **kwargs)
        # FR-ADM-007: Finance Officers are excluded from admissions module
        if request.user.role == UserRole.FINANCE_OFFICER and not self.allow_finance_officer:
            raise PermissionDenied("Finance Officers do not have access to the admissions module.")
        if not request.user.has_perm(self.required_permission):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)


class AdmissionsPipelineView(AdmissionsCountsMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    template_name = "admissions/pipeline.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.view_applicant"

    def get_filtered_queryset(self):
        queryset = Applicant.objects.all().order_by("-created_at")
        form = ApplicantFilterForm(self.request.GET or None)
        if form.is_valid():
            q = (form.cleaned_data.get("q") or "").strip()
            status = form.cleaned_data.get("status")
            grade = form.cleaned_data.get("grade")
            department = form.cleaned_data.get("department")
            channel = form.cleaned_data.get("channel")
            start_date = form.cleaned_data.get("start_date")
            end_date = form.cleaned_data.get("end_date")
            status_scope = form.cleaned_data.get("status_scope")

            if q:
                queryset = queryset.filter(
                    Q(child_full_name__icontains=q)
                    | Q(parent_full_name__icontains=q)
                    | Q(grade_applying_for__icontains=q)
                    | Q(inquiry_channel__icontains=q)
                )
            if status:
                queryset = queryset.filter(status=status)
            if grade:
                queryset = queryset.filter(grade_applying_for=grade)
            elif department:
                grade_names = GradeClass.objects.filter(
                    department=department
                ).values_list("name", flat=True)
                queryset = queryset.filter(grade_applying_for__in=grade_names)
            if channel:
                queryset = queryset.filter(inquiry_channel=channel)
            if start_date:
                queryset = queryset.filter(created_at__date__gte=start_date)
            if end_date:
                queryset = queryset.filter(created_at__date__lte=end_date)
            if status_scope == "active" and not status:
                queryset = queryset.exclude(status__in=[ApplicantStatus.ENROLLED, ApplicantStatus.DENIED, ApplicantStatus.WITHDRAWN])
            if status_scope == "enrolled_range":
                queryset = queryset.filter(status=ApplicantStatus.ENROLLED)
                if start_date:
                    queryset = queryset.filter(updated_at__date__gte=start_date)
                if end_date:
                    queryset = queryset.filter(updated_at__date__lte=end_date)
        return queryset, form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        applicants, form = self.get_filtered_queryset()
        
        from django.utils import timezone
        from datetime import timedelta
        today = timezone.now().date()
        
        # All individual statuses as separate columns — BDD S1
        stages = [
            ("inquiry_received", "Inquiry Received", {ApplicantStatus.INQUIRY_RECEIVED}),
            ("meeting_scheduled", "Meeting Scheduled", {ApplicantStatus.MEETING_SCHEDULED}),
            ("meeting_completed", "Meeting Completed", {ApplicantStatus.MEETING_COMPLETED}),
            ("declined_at_meeting", "Declined at Meeting", {ApplicantStatus.DECLINED_AT_MEETING}),
            ("assessment_pending", "Assessment Pending", {ApplicantStatus.ASSESSMENT_PENDING}),
            ("assessment_confirmed", "Assessment Confirmed", {ApplicantStatus.ASSESSMENT_CONFIRMED, ApplicantStatus.ASSESSMENT_FEE_PAID}),
            ("assessment_completed", "Assessment Done", {ApplicantStatus.ASSESSMENT_COMPLETED}),
            ("report_pending", "Report Pending", {ApplicantStatus.REPORT_PENDING}),
            ("assessment_failed", "Assessment Failed", {ApplicantStatus.ASSESSMENT_FAILED}),
            ("hos_review", "HOS Review", {ApplicantStatus.HOS_REVIEW}),
            ("hos_decision", "HOS Decision", {ApplicantStatus.HOS_DECISION}),
            ("admitted", "Admitted", {ApplicantStatus.ADMITTED}),
            ("conditional", "Conditional", {ApplicantStatus.CONDITIONAL}),
            ("form_submitted", "Form Submitted", {ApplicantStatus.FORM_SUBMITTED}),
            ("invoice_generated", "Invoice Generated", {ApplicantStatus.INVOICE_GENERATED}),
            ("invoice_paid", "Invoice Paid", {ApplicantStatus.INVOICE_PAID}),
            ("enrolled", "Enrolled", {ApplicantStatus.ENROLLED}),
            ("flagged_for_review", "Flagged for Review", {ApplicantStatus.FLAGGED_FOR_REVIEW}),
            ("waitlisted", "Waitlisted", {ApplicantStatus.WAITLISTED}),
            ("denied", "Denied", {ApplicantStatus.DENIED}),
            ("withdrawn", "Withdrawn", {ApplicantStatus.WITHDRAWN}),
        ]
        
        ordered_statuses = []
        for key, label, status_set in stages:
            col_qs = (
                applicants.filter(status__in=status_set)
                .select_related("assessment")
                .prefetch_related("documents")
                .order_by("-updated_at", "-created_at")
            )
            items = []
            for applicant in col_qs[:50]: # Increased limit for active board
                # FR-ADM-010: Compute blocking flags
                flags = []
                assessment = getattr(applicant, "assessment", None)
                
                # FLAG_1: assessment_fee_unpaid
                if assessment and not assessment.assessment_fee_confirmed_paid and assessment.scheduled_date < today:
                    flags.append("assessment_fee_unpaid")
                    
                # FLAG_2: logistics_not_sent
                if assessment and assessment.assessment_fee_confirmed_paid and not assessment.logistics_sent_at and assessment.scheduled_date <= today + timedelta(days=3):
                    flags.append("logistics_not_sent")
                    
                # FLAG_3: result_not_submitted
                if applicant.status == ApplicantStatus.ASSESSMENT_COMPLETED and (not assessment or not assessment.result):
                    flags.append("result_not_submitted")
                    
                # FLAG_4: hos_review_overdue (assuming status change date is tracked in updated_at or timeline)
                if applicant.status == ApplicantStatus.HOS_REVIEW:
                    # Simplified: using updated_at as proxy for status duration
                    if (timezone.now() - applicant.updated_at).days > 5:
                        flags.append("hos_review_overdue")
                        
                # FLAG_5: missing_document
                if applicant.status == ApplicantStatus.ADMITTED:
                    required_count = len(ApplicantDocumentType.choices)
                    received_count = applicant.documents.filter(is_received=True).count()
                    if received_count < required_count:
                        flags.append("missing_document")

                docs_total = len(getattr(applicant, "documents").all())
                docs_received = sum(1 for d in applicant.documents.all() if d.is_received)
                docs_badge = ""
                if docs_total and docs_received < docs_total:
                    docs_badge = "pending"
                elif docs_total and docs_received == docs_total:
                    docs_badge = "complete"

                # Map blocking flags to assessment_badge for template rendering
                assessment_badge = ""
                if "assessment_fee_unpaid" in flags:
                    assessment_badge = "fee_unpaid"
                elif "logistics_not_sent" in flags:
                    assessment_badge = "fee_paid_wait_logistics"
                elif assessment and assessment.assessment_fee_confirmed_paid:
                    assessment_badge = "confirmed"

                items.append(
                    {
                        "applicant": applicant,
                        "assessment_badge": assessment_badge,
                        "docs_badge": docs_badge,
                    }
                )
            ordered_statuses.append(
                {
                    "key": key,
                    "label": label,
                    "total": col_qs.count(),
                    "items": items,
                }
            )
        context["ordered_statuses"] = ordered_statuses
        kanban_columns = [column for column in ordered_statuses if column["total"] > 0]
        context["kanban_columns"] = kanban_columns
        context["kanban_column_count"] = len(kanban_columns)
        context["kanban_scrollable"] = len(kanban_columns) > 8

        phase_groups = [
            {"key": "intake", "label": "Intake", "phases": ["inquiry_received", "meeting_scheduled", "meeting_completed"]},
            {"key": "assessment", "label": "Assessment", "phases": ["assessment_pending", "assessment_confirmed", "assessment_completed"]},
            {"key": "review", "label": "Review", "phases": ["report_pending", "assessment_failed", "hos_review", "hos_decision"]},
            {"key": "decision", "label": "Decision", "phases": ["admitted", "conditional", "waitlisted", "denied", "withdrawn"]},
            {"key": "enrollment", "label": "Enrollment", "phases": ["form_submitted", "invoice_generated", "invoice_paid", "enrolled", "flagged_for_review"]},
        ]
        status_map = {col["key"]: col for col in ordered_statuses}
        for pg in phase_groups:
            pg["total"] = sum(status_map.get(k, {}).get("total", 0) for k in pg["phases"])
            pg["columns"] = [status_map[k] for k in pg["phases"] if k in status_map]
        context["phase_groups"] = phase_groups

        context["filter_form"] = form
        context["admissions_tab"] = "pipeline"
        
        # FR-ADM-008: Active filters list for UI badges
        active_filters = []
        if form.is_valid():
            for field in form.cleaned_data:
                val = form.cleaned_data[field]
                if val:
                    active_filters.append({"label": field.title(), "value": val})
        context["active_filters"] = active_filters
        
        return context



class InquiryCreateView(AdmissionsCountsMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, CreateView):
    template_name = "admissions/new_inquiry.html"
    form_class = ApplicantCreateForm
    success_url = reverse_lazy("admissions:pipeline")
    login_url = "/accounts/login/"

    def dispatch(self, request, *args, **kwargs):
        # FR-ADM-001: Only Admin Officers create inquiries (HOS is excluded)
        if request.user.is_authenticated and request.user.role == UserRole.HEAD_OF_SCHOOL:
            raise PermissionDenied("Head of School cannot create new inquiries.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from django.utils import timezone
        now = timezone.now()
        ctx["enrollment_years"] = list(range(now.year, now.year + 4))
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)

        import json
        post = self.request.POST

        inquiry_notes = {
            "submitted_via": "staff_form",
            "gender": post.get("x_gender", ""),
            "current_grade": post.get("x_current_grade", ""),
        }

        self.object.notes = json.dumps(inquiry_notes)
        self.object.save(update_fields=["notes"])

        # FR-ADM-001: Audit log (fast, inline)
        from audit.models import log_event
        log_event(
            actor=self.request.user,
            action_type="INQUIRY_CREATED",
            model_name="Applicant",
            object_id=self.object.pk,
            description=f"New inquiry created for {self.object.child_full_name} ({self.object.grade_applying_for}) via {self.object.get_inquiry_channel_display()}",
            request=self.request,
        )

        # FR-ADM-005: detect potential sibling match (fast, inline)
        try:
            from students.models import ParentGuardian, Student
            from django.db.models import Q

            phone = (self.object.parent_phone or "").strip()
            name = (self.object.parent_full_name or "").strip()
            
            guardian = None
            if phone:
                guardian = ParentGuardian.objects.filter(phone=phone, is_archived=False).first()
            if not guardian and name:
                guardian = ParentGuardian.objects.filter(full_name__iexact=name, is_archived=False).first()

            if guardian:
                self.object.sibling_matched_parent = guardian
                sibs = Student.objects.filter(studentguardian__guardian=guardian, is_archived=False).order_by("last_name", "first_name")
                if sibs.exists():
                    self.object.sibling_currently_enrolled = True
                    names = ", ".join([f"{s.first_name} {s.last_name}" for s in sibs])
                    msg = (
                        f"Possible existing parent found: {guardian.full_name} -- {guardian.phone}. "
                        f"This parent has {sibs.count()} child(ren): {names}. "
                        "Confirm: link to existing parent OR create new record."
                    )
                    messages.warning(self.request, msg)
                    if not (self.object.sibling_details or "").strip():
                        self.object.sibling_details = names
                self.object.save()
                
                # Log to audit trail
                from audit.models import log_event
                log_event(
                    actor=self.request.user,
                    action_type="SIBLING_MATCH_DETECTED",
                    model_name="Applicant",
                    object_id=self.object.pk,
                    description=f"Potential sibling match for {self.object.child_full_name} with parent {guardian.full_name}",
                    request=self.request,
                )
        except Exception:
            pass

        # FR-ADM-004: Class capacity check (fast, inline)
        from academics.models import GradeClass, get_class_capacity
        from students.models import Student
        grade_name = self.object.grade_applying_for.strip()
        gc = GradeClass.objects.filter(name__iexact=grade_name).first()
        if gc:
            cap = get_class_capacity(gc)
            if cap:
                student_count = Student.objects.filter(class_name__iexact=grade_name, is_archived=False).count()
                if student_count >= cap:
                    messages.warning(self.request, f"Warning: {grade_name} is currently at max capacity ({cap} students). Applicant will likely be waitlisted.")

        # Defer heavy I/O: email + notifications + task gen (non-blocking)
        applicant_pk = self.object.pk
        applicant_id_str = str(applicant_pk)
        parent_email = (self.object.parent_email or "").strip()
        child_name = self.object.child_full_name
        grade = self.object.grade_applying_for
        parent_name = self.object.parent_full_name
        request_user = self.request.user
        ref_number = self.object.reference_number

        def _background_post_save():
            try:
                # E03: Acknowledgement email to parent
                if parent_email:
                    from communications.email_service import send_email_safe
                    from core.models import SchoolSettings
                    contact = SchoolSettings.get_settings().get_admissions_contact()
                    school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"

                    tpl_context = {
                        "parent_name": parent_name,
                        "child_name": child_name,
                        "grade": grade,
                        "ref_number": ref_number,
                        "school_name": school_name,
                        "contact_phone": contact['phone'],
                        "proposed_meeting_date": getattr(self.object, 'preferred_meeting_date', None) or "To be confirmed",
                        "proposed_meeting_time": getattr(self.object, 'preferred_meeting_time', None) or "To be confirmed",
                        "meeting_date": getattr(self.object, 'preferred_meeting_date', None) or "To be confirmed",
                        "meeting_time": getattr(self.object, 'preferred_meeting_time', None) or "To be confirmed",
                        "response_window": "48 hours",
                        "admissions_phone": contact.get('phone', ''),
                    }

                    from core.email_templates import send_dynamic_email
                    db_sent = send_dynamic_email(
                        template_type="admission_inquiry",
                        to_email=parent_email,
                        context=tpl_context,
                    )

                    if not db_sent:
                        ack_body = (
                            f"Dear {parent_name},\n\n"
                            f"Thank you for thinking of {school_name} for "
                            f"{child_name}. Your inquiry has reached us safely, and "
                            f"we're glad you got in touch.\n\n"
                            "Here's what we have on record:\n\n"
                            f"Learner: {child_name}\n"
                            f"Grade enquired for: {grade}\n"
                            f"Your reference number: {ref_number}\n\n"
                            "Our Head of School reviews each inquiry personally and "
                            "will come back to you within 48 hours with a meeting "
                            "date. You'll get an email from us either way.\n\n"
                            "Do keep your reference number somewhere handy. Quoting "
                            "it whenever you call or write helps us find your file "
                            "straight away.\n\n"
                            f"If anything above looks wrong, or you'd simply like to "
                            f"talk to someone before the meeting, call us on "
                            f"{contact['phone']}. We're always happy to answer "
                            f"questions.\n\n"
                            f"We look forward to meeting you and {child_name}.\n\n"
                            "Warm regards,\n"
                            "Admissions Office\n"
                            f"{school_name}"
                        )
                        send_email_safe(
                            to_email=parent_email,
                            subject=f"We've received your inquiry for {child_name} \u2014 {ref_number}",
                            body=ack_body,
                        )
            except Exception:
                pass

            try:
                # S01: Inquiry acknowledgement SMS to parent
                from communications.email_service import dispatch_notification
                parent_phone_val = (self.object.parent_phone or "").strip() or None
                if parent_phone_val:
                    dispatch_notification(
                        user=None,
                        title="Inquiry Received",
                        message=f"Dear {parent_name}, thank you for your interest in Hodari Christian School for {child_name}. Your reference number is {ref_number}. Our Head of School will contact you shortly to confirm a meeting date.",
                        link=None,
                        actor=None,
                        external_email=None,
                        phone=parent_phone_val,
                    )
            except Exception:
                pass

            try:
                # NOTIF-01: Notify admin officers (in-app)
                from communications.email_service import dispatch_notification, send_email_safe
                from users.models import User, UserRole
                admins = User.objects.filter(role=UserRole.ADMIN_OFFICER, is_active=True)
                for admin in admins:
                    dispatch_notification(
                        user=admin,
                        title="New Inquiry Received",
                        message=f"New inquiry received for {child_name} ({grade}).",
                        link=f"/admissions/applicant/{applicant_id_str}/",
                        actor=request_user,
                    )
                # E01: Email to admissions group + Head of School
                from core.models import SchoolSettings as _SS
                _ss = _SS.get_settings()
                admissions_email = _ss.admissions_email or "admissions@hodari.ac.tz"
                e01_context = {
                    "reference_number": ref_number,
                    "child_name": child_name,
                    "grade": grade,
                    "parent_name": parent_name,
                    "parent_phone": getattr(self.object, 'parent_phone', ''),
                    "parent_email": getattr(self.object, 'parent_email', ''),
                    "proposed_meeting_date": getattr(self.object, 'preferred_meeting_date', None) or "To be confirmed",
                    "proposed_meeting_time": getattr(self.object, 'preferred_meeting_time', None) or "To be confirmed",
                    "application_link": f"/admissions/applicant/{applicant_id_str}/",
                    "school_name": _ss.school_name or "Hodari Christian School",
                }
                from core.email_templates import send_dynamic_email
                db_sent = send_dynamic_email(
                    template_type="admission_new_inquiry",
                    to_email=admissions_email,
                    context=e01_context,
                )
                if not db_sent:
                    e01_body = (
                        f"A new admission inquiry has been submitted.\n\n"
                        f"Reference: {ref_number}\n"
                        f"Learner: {child_name}\n"
                        f"Grade enquired for: {grade}\n"
                        f"Parent or guardian: {parent_name}\n"
                        f"Phone: {getattr(self.object, 'parent_phone', '')}\n"
                        f"Email: {getattr(self.object, 'parent_email', '')}\n\n"
                        f"Meeting date requested by the parent: "
                        f"{e01_context['proposed_meeting_date']} at {e01_context['proposed_meeting_time']}\n\n"
                        f"Confirm this date or set a different one: /admissions/applicant/{applicant_id_str}/"
                    )
                    send_email_safe(
                        to_email=admissions_email,
                        subject=f"New inquiry: {child_name}, Grade {grade} — {ref_number}",
                        body=e01_body,
                    )
                # Also send to Head of School directly
                from users.models import User, UserRole
                hos_users = User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True)
                for hos in hos_users:
                    if hos.email and hos.email != admissions_email:
                        send_dynamic_email(
                            template_type="admission_new_inquiry",
                            to_email=hos.email,
                            context=e01_context,
                        ) if db_sent else send_email_safe(
                            to_email=hos.email,
                            subject=f"New inquiry: {child_name}, Grade {grade} — {ref_number}",
                            body=e01_body,
                        )
            except Exception:
                pass

            try:
                # Generate follow-up task
                from tasks.services import generate_assessment_scheduling_task
                from admissions.models import Applicant
                applicant = Applicant.objects.get(pk=applicant_pk)
                generate_assessment_scheduling_task(applicant)
            except Exception:
                pass

        threading.Thread(target=_background_post_save, daemon=True).start()

        messages.success(self.request, "Inquiry saved.")
        return response

    def get_context_data(self, **kwargs):
        import json

        ctx = super().get_context_data(**kwargs)
        grades = GradeClass.objects.order_by("sort_order", "name")
        ctx["admission_grades_json"] = json.dumps([
            {"name": g.name, "department": g.department} for g in grades
        ])
        ctx["admissions_tab"] = "new_inquiry"
        return ctx



class InquiryEditView(PermissionCacheMixin, AdmissionsRoleRequiredMixin, UpdateView):
    """FR-ADM-002: Edit inquiry fields. Requires change_applicant permission.
    Only allowed at Inquiry received or Meeting scheduled status.
    """
    model = Applicant
    form_class = ApplicantEditForm
    template_name = "admissions/_edit_inquiry_modal.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.change_applicant"

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.status not in (
            ApplicantStatus.INQUIRY_RECEIVED,
            ApplicantStatus.MEETING_SCHEDULED,
        ):
            messages.error(request, "Inquiry can only be edited at Inquiry received or Meeting scheduled stage.")
            return redirect("admissions:detail", pk=self.object.pk)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        if request.headers.get("HX-Request") != "true":
            return redirect("admissions:detail", pk=self.object.pk)
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        from audit.models import log_event
        log_event(
            actor=self.request.user,
            action_type="INQUIRY_EDITED",
            model_name="Applicant",
            object_id=self.object.pk,
            description=f"Inquiry details edited for {self.object.child_full_name}",
            request=self.request,
        )
        messages.success(self.request, "Inquiry details updated.")
        if self.request.headers.get("HX-Request") == "true":
            from django.http import HttpResponse
            from django.urls import reverse
            resp = HttpResponse("")
            resp["HX-Location"] = reverse("admissions:detail", kwargs={"pk": self.object.pk})
            return resp
        return redirect("admissions:detail", pk=self.object.pk)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["applicant"] = self.object
        return ctx

    def get_success_url(self):
        return reverse_lazy("admissions:detail", kwargs={"pk": self.object.pk})


class ApplicantDetailView(AdmissionsCountsMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    template_name = "admissions/detail.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.view_applicant"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role == UserRole.SUPER_ADMIN:
            return super().dispatch(request, *args, **kwargs)
        # FR-ADM-007: Finance Officers should not access individual applicant detail pages
        if request.user.role == UserRole.FINANCE_OFFICER:
            raise PermissionDenied("Finance Officers do not have access to applicant detail pages.")
        if not request.user.has_perm("admissions.view_applicant"):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        applicant = get_object_or_404(
            Applicant.objects.select_related("assessment", "enrolment_checklist")
            .prefetch_related(
                "documents",
                "internal_notes__author",
            ),
            pk=kwargs["pk"],
        )
        ctx["applicant"] = applicant
        ctx["timeline"] = applicant.timeline.select_related("actor")[:50]
        ctx["status_choices"] = ApplicantStatus.choices
        ctx["assessment_form"] = AssessmentScheduleForm(
            instance=getattr(applicant, "assessment", None)
        )
        ensure_default_documents(applicant)
        ensure_enrolment_checklist(applicant)
        ctx["documents_by_type"] = {
            d.document_type: d for d in applicant.documents.all()
        }
        ctx["document_types"] = ApplicantDocumentType.choices
        ctx["document_rows"] = [
            {"key": dt, "label": label, "doc": ctx["documents_by_type"].get(dt)}
            for dt, label in ApplicantDocumentType.choices
        ]
        ctx["checklist"] = getattr(applicant, "enrolment_checklist", None)
        ctx["admissions_tab"] = "applicant"

        # FR-ADM-007: Finance Officer sees fee-status only (hide notes, timeline, etc.)
        ctx["is_fee_officer_view"] = not self.request.user.has_perm("admissions.change_applicant")

        # FR-ADM-029: Internal notes for authorised users
        from admissions.models import ApplicantInternalNote
        if self.request.user.has_perm("admissions.view_applicantinternalnote"):
            ctx["internal_notes"] = applicant.internal_notes.select_related("author").all()
        else:
            ctx["internal_notes"] = []

        # FR-ADM-002: Show edit button only for users granted change_applicant at editable statuses
        ctx["can_edit_inquiry"] = (
            self.request.user.has_perm("admissions.change_applicant")
            and applicant.status in (ApplicantStatus.INQUIRY_RECEIVED, ApplicantStatus.MEETING_SCHEDULED)
        )

        # FR-ADM-022: Block indicator
        ctx["clearance_outstanding"] = not applicant.documents.filter(
            document_type=ApplicantDocumentType.CLEARANCE_FORM, is_received=True
        ).exists()

        from finance.models import Invoice
        ctx["admission_invoice"] = Invoice.objects.filter(
            applicant=applicant, invoice_number__startswith="ADM-"
        ).first()

        # ── Progress bar steps ──
        st = applicant.status
        _PROGRESS_STEPS = [
            {"key": "meeting_scheduled",   "label": "Meeting",         "actor": "HOS",             "num": 1},
            {"key": "meeting_completed",   "label": "Held",            "actor": "HOS",             "num": 2},
            {"key": "assessment_pending",  "label": "Assess scheduled","actor": "Admin Officer",   "num": 3},
            {"key": "assessment_confirmed","label": "Fee paid",        "actor": "Finance Officer", "num": 4},
            {"key": "assessment_completed","label": "Done",            "actor": "Teacher",         "num": 5},
            {"key": "report_pending",      "label": "Report",          "actor": "Teacher",         "num": 6},
            {"key": "hos_review",          "label": "Review",          "actor": "HOS",             "num": 7},
            {"key": "hos_decision",        "label": "Decision",        "actor": "HOS",             "num": 8},
            {"key": "enrolled",            "label": "Enrolment",       "actor": "HOS / Admin",     "num": 9},
        ]
        _CURRENT_MAP = {
            "inquiry_received":     ("meeting_scheduled",  "Record meeting", "HOS"),
            "meeting_scheduled":    ("meeting_scheduled",  "Move to assessment", "Admin Officer"),
            "meeting_completed":    ("meeting_completed",  "Schedule assessment", "Admin Officer"),
            "declined_at_meeting":  ("meeting_completed",  "Reschedule meeting", "Admin Officer"),
            "assessment_pending":   ("assessment_pending", "Confirm fee", "Finance Officer"),
            "assessment_fee_paid":  ("assessment_confirmed","Send logistics", "Admin Officer"),
            "assessment_confirmed": ("assessment_confirmed","Mark complete", "Teacher"),
            "assessment_completed": ("assessment_completed","Submit report", "Teacher"),
            "report_pending":       ("report_pending",     "Review report", "HOS"),
            "hos_review":           ("hos_review",         "Record decision", "HOS"),
            "hos_decision":         ("hos_decision",       "Fill admission form", "Parent"),
            "admitted":             ("enrolled",           "Generate invoice", "Finance Officer"),
            "conditional":          ("enrolled",           "Generate invoice", "Finance Officer"),
            "form_submitted":       ("enrolled",           "Generate invoice", "Finance Officer"),
            "invoice_generated":    ("enrolled",           "Confirm payment", "Finance Officer"),
            "invoice_paid":         ("enrolled",           "Complete enrolment", "Admin Officer"),
            "waitlisted":           ("hos_decision",       "Admit from waitlist", "HOS"),
            "flagged_for_review":   ("enrolled",           "HOS review", "HOS"),
            "enrolled":             ("enrolled",           "", ""),
        }
        _TERMINAL_MAP = {
            "assessment_failed": "assessment_completed",
            "denied": "hos_decision",
            "withdrawn": "assessment_completed",
        }
        if st in _TERMINAL_MAP:
            current_key = _TERMINAL_MAP[st]
            next_hint = ""
            _STEP_ORDER = {s["key"]: i for i, s in enumerate(_PROGRESS_STEPS)}
            current_order = _STEP_ORDER.get(current_key, -1)
            for s in _PROGRESS_STEPS:
                order = _STEP_ORDER[s["key"]]
                if order <= current_order:
                    s["state"] = "done"
                elif order == current_order + 1:
                    s["state"] = "current"
                else:
                    s["state"] = "future"
            ctx["progress_steps"] = _PROGRESS_STEPS
            ctx["current_step_label"] = ""
            ctx["current_step_actor"] = ""
            ctx["next_step_label"] = ""
            ctx["next_step_actor"] = ""
        else:
            current_key, next_hint, _ = _CURRENT_MAP.get(
                st, ("meeting_scheduled", "", "")
            )
            _STEP_ORDER = {s["key"]: i for i, s in enumerate(_PROGRESS_STEPS)}
            current_order = _STEP_ORDER.get(current_key, -1)
            for s in _PROGRESS_STEPS:
                order = _STEP_ORDER[s["key"]]
                if st == "enrolled":
                    s["state"] = "done"
                elif order < current_order:
                    s["state"] = "done"
                elif order == current_order:
                    s["state"] = "current"
                elif order == current_order + 1:
                    s["state"] = "next"
                else:
                    s["state"] = "future"

            ctx["progress_steps"] = _PROGRESS_STEPS
            current_step = _PROGRESS_STEPS[current_order] if 0 <= current_order < len(_PROGRESS_STEPS) else {}
            ctx["current_step_label"] = current_step.get("label", "")
            ctx["current_step_actor"] = current_step.get("actor", "")
            ctx["next_step_label"] = next_hint
            ctx["next_step_actor"] = _PROGRESS_STEPS[current_order + 1]["actor"] if current_order + 1 < len(_PROGRESS_STEPS) else ""

        return ctx



class AssessmentListView(AdmissionsCountsMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    template_name = "admissions/assessments.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.view_applicant"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["admissions_tab"] = "assessments"
        ctx["items"] = (
            Applicant.objects.filter(assessment__isnull=False)
            .exclude(
                status__in=[
                    ApplicantStatus.ASSESSMENT_COMPLETED, 
            ApplicantStatus.HOS_REVIEW,
                    ApplicantStatus.HOS_DECISION, 
                    ApplicantStatus.ADMITTED, 
                    ApplicantStatus.CONDITIONAL, 
                    ApplicantStatus.ENROLLED, 
                    ApplicantStatus.WITHDRAWN, 
                    ApplicantStatus.DENIED,
                    ApplicantStatus.MEETING_COMPLETED,
                    ApplicantStatus.DECLINED_AT_MEETING,
                    ApplicantStatus.REPORT_PENDING,
                    ApplicantStatus.ASSESSMENT_FAILED,
                    ApplicantStatus.FORM_SUBMITTED,
                    ApplicantStatus.INVOICE_GENERATED,
                    ApplicantStatus.INVOICE_PAID,
                    ApplicantStatus.FLAGGED_FOR_REVIEW,
                ]
            )
            .select_related("assessment")
            .order_by("assessment__scheduled_date", "assessment__scheduled_time")
        )[:200]
        return ctx


class AssessmentCalendarView(AdmissionsCountsMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    template_name = "admissions/assessment_calendar.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.view_assessment_calendar"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
            
        from django.utils import timezone
        from datetime import timedelta
        today = timezone.now().date()
        three_days_out = today + timedelta(days=3)
        
        # Lists ALL assessments ordered by date ascending
        assessments = (
            Applicant.objects.filter(assessment__isnull=False)
            .select_related("assessment")
            .order_by("assessment__scheduled_date", "assessment__scheduled_time")
        )
        
        # Grouped by day
        days = {}
        for app in assessments:
            d = app.assessment.scheduled_date
            if d not in days:
                days[d] = []
            
            status_label = "UNPAID"
            status_class = "red"
            if app.status in [ApplicantStatus.ASSESSMENT_COMPLETED, ApplicantStatus.HOS_REVIEW, ApplicantStatus.HOS_DECISION, ApplicantStatus.ADMITTED, ApplicantStatus.ENROLLED]:
                status_label = "COMPLETED"
                status_class = "grey"
            elif app.assessment.logistics_sent_at:
                status_label = "CONFIRMED"
                status_class = "green"
            elif app.assessment.assessment_fee_confirmed_paid:
                status_label = "UNCONFIRMED"
                status_class = "amber"
                
            highlight = d <= three_days_out and d >= today
            
            days[d].append({
                "applicant": app,
                "assessment": app.assessment,
                "status_label": status_label,
                "status_class": status_class,
                "highlight": highlight
            })
            
        ctx["days"] = sorted(days.items())
        ctx["admissions_tab"] = "assessment_calendar"
        return ctx



class WaitlistView(AdmissionsCountsMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    template_name = "admissions/waitlist.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.view_applicant"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["admissions_tab"] = "waitlist"
        from django.db.models import Count as DbCount
        waitlist_qs = Applicant.objects.filter(status=ApplicantStatus.WAITLISTED).order_by("created_at")
        ctx["items"] = waitlist_qs[:200]
        # Per-grade waitlist counts — BDD S3
        grade_counts_raw = (
            waitlist_qs.values("grade_applying_for")
            .annotate(cnt=DbCount("id"))
            .order_by("-cnt", "grade_applying_for")
        )
        ctx["grade_waitlist_counts"] = [
            {"grade": g["grade_applying_for"], "count": g["cnt"]} for g in grade_counts_raw
        ]
        return ctx


class ApplicantTransitionView(HtmxRequiredMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    template_name = "admissions/_status_form.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.transition_applicant_status"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        ctx["applicant"] = applicant
        allowed = get_allowed_transition_targets(
            from_status=applicant.status,
            actor_role=self.request.user.role,
            actor=self.request.user,
        )
        ctx["status_choices"] = [
            (s, dict(ApplicantStatus.choices).get(s, s)) for s in allowed
        ]
        return ctx

    def post(self, request, *args, **kwargs):
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        to_status = request.POST.get("to_status", "").strip()
        reason = _strip_html(request.POST.get("reason", ""))
        conditions = _strip_html(request.POST.get("conditions", ""))
        from_status = applicant.status  # capture BEFORE transition

        # FR-ADM-030: Withdrawal reason is mandatory only if Assessment completed or beyond
        _ASSESSMENT_OR_BEYOND = {
            ApplicantStatus.ASSESSMENT_COMPLETED,
            ApplicantStatus.HOS_REVIEW,
            ApplicantStatus.HOS_DECISION,
            ApplicantStatus.ADMITTED,
            ApplicantStatus.CONDITIONAL,
            ApplicantStatus.DENIED,
            ApplicantStatus.ENROLLED,
            ApplicantStatus.WAITLISTED,
        }
        if to_status == "withdrawn" and from_status in _ASSESSMENT_OR_BEYOND and not reason:
            messages.error(request, "A reason is required to withdraw an applicant at this stage.")
            return self.get(request, *args, **kwargs)

        # FR-ADM-026: Capacity check before waitlisting
        if to_status == "waitlisted":
            from academics.models import GradeClass, get_class_capacity
            from students.models import Student
            grade_name = applicant.grade_applying_for.strip()
            gc = GradeClass.objects.filter(name__iexact=grade_name).first()
            if gc:
                cap = get_class_capacity(gc)
                if cap:
                    student_count = Student.objects.filter(class_name__iexact=grade_name, is_archived=False).count()
                    if student_count < cap:
                        messages.warning(request, f"{grade_name} still has capacity ({student_count}/{cap}). Consider admitting instead of waitlisting.")

        allowed_targets = get_allowed_transition_targets(from_status=from_status, actor_role=request.user.role, actor=request.user)
        if to_status not in allowed_targets:
            messages.error(request, "You can only move this applicant to the next valid step.")
            return self.get(request, *args, **kwargs)
        if to_status == "enrolled":
            messages.error(request, "Enrolment must be completed via the Enrol button (Complete Enrolment) to create the student record.")
            return self.get(request, *args, **kwargs)
        try:
            # FR-ADM-028: Record conditions for conditional admissions
            if to_status == "conditional" and conditions:
                applicant.conditional_conditions = conditions
                applicant.save(update_fields=["conditional_conditions", "updated_at"])

            transition_applicant_status(
                applicant=applicant,
                to_status=to_status,
                actor=request.user,
                reason=reason,
            )
            # E10: Initial report-due email to teacher when assessment is completed
            if to_status == "assessment_completed":
                try:
                    assessment = getattr(applicant, "assessment", None)
                    if assessment and assessment.facilitating_teacher_name:
                        from users.models import User as _User, UserRole as _UR
                        from core.email_templates import send_dynamic_email
                        teacher_users = _User.objects.filter(
                            is_active=True,
                            role__in=[_UR.TEACHER, _UR.PRIMARY_HOD, _UR.ECD_HOD, _UR.LOWER_SECONDARY_HOD],
                        )
                        for tu in teacher_users:
                            if f"{tu.first_name} {tu.last_name}".strip().lower() == assessment.facilitating_teacher_name.strip().lower():
                                if tu.email:
                                    send_dynamic_email(
                                        template_type="admission_report_reminder",
                                        to_email=tu.email,
                                        context={
                                            "teacher_name": assessment.facilitating_teacher_name,
                                            "learner_name": applicant.child_full_name,
                                            "intended_grade": applicant.grade_applying_for,
                                            "assessment_date": str(assessment.scheduled_date),
                                            "reference_number": applicant.reference_number,
                                            "report_link": f"/admissions/applicant/{applicant.pk}/",
                                        },
                                    )
                                break
                except Exception:
                    pass
            # Generate task based on new status
            try:
                from tasks.services import generate_enrolment_processing_task, generate_applicant_followup_task
                if to_status == 'admitted':
                    generate_enrolment_processing_task(applicant)
                elif to_status in ('meeting_scheduled', 'assessment_pending'):
                    generate_applicant_followup_task(applicant)
            except Exception:
                pass

            # NOTIF-06/07/08: Notify Admin + Parent based on HOS decision
            from communications.email_service import dispatch_notification
            from users.models import User

            if to_status in ("admitted", "conditional", "denied"):
                # Notify Admin Officer to begin next steps
                admins = User.objects.filter(role=UserRole.ADMIN_OFFICER, is_active=True)
                status_label = dict(ApplicantStatus.choices).get(to_status, to_status)
                for admin in admins:
                    dispatch_notification(
                        user=admin,
                        title=f"Applicant {status_label}",
                        message=(
                            f"{applicant.child_full_name} ({applicant.grade_applying_for}) has been "
                            f"{status_label.lower()} by the Head of School. "
                            f"{reason or 'No reason provided.'}"
                        ),
                        link=f"/admissions/applicant/{applicant.pk}/",
                        actor=request.user,
                    )

            if to_status == "denied":
                # Notify parent that application was denied
                parent_phone = (applicant.parent_phone or "").strip() or None
                if (applicant.parent_email or "").strip() or parent_phone:
                    from communications.email_service import send_email_safe
                    from core.models import SchoolSettings
                    school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"

                    deny_body = (
                        f"Dear {applicant.parent_full_name},\n\n"
                        f"Thank you for your interest in {school_name} "
                        f"for {applicant.child_full_name}.\n\n"
                        f"After careful review, we regret to inform you that the application "
                        f"has not been successful at this time.\n\n"
                        f"{reason}\n\n"
                        "We wish you and your family all the best.\n\n"
                        f"Warm regards,\n{school_name} \u2014 Admissions"
                    )
                    if (applicant.parent_email or "").strip():
                        from core.email_templates import send_dynamic_email
                        _contact_deny = SchoolSettings.get_settings().get_admissions_contact()
                        tpl_context = {
                            "parent_name": applicant.parent_full_name,
                            "child_full_name": applicant.child_full_name,
                            "reason": reason or "",
                            "reference_number": applicant.reference_number,
                            "admissions_phone": _contact_deny.get("phone", ""),
                            "school_name": school_name,
                            "admissions_email": _contact_deny.get("email", ""),
                        }
                        db_sent = send_dynamic_email(
                            template_type="admission_denied",
                            to_email=applicant.parent_email.strip(),
                            context=tpl_context,
                        )
                        if not db_sent:
                            send_email_safe(
                                to_email=applicant.parent_email.strip(),
                                subject=f"Application Update \u2014 {applicant.child_full_name}",
                                body=deny_body,
                            )
                    # SMS for denied
                    dispatch_notification(
                        user=None, title="Application Update",
                        message=f"Dear {applicant.parent_full_name}, {applicant.child_full_name}'s application was not successful. {reason or ''}",
                        link=None, actor=None, external_email=None, phone=parent_phone,
                    )

            # E05: Declined at meeting → parent email
            if to_status == "declined_at_meeting":
                from core.models import SchoolSettings
                _ss = SchoolSettings.get_settings()
                parent_email = (applicant.parent_email or "").strip() or None
                parent_phone = (applicant.parent_phone or "").strip() or None
                if parent_email:
                    from core.email_templates import send_dynamic_email
                    send_dynamic_email(
                        template_type="admission_declined_at_meeting",
                        to_email=parent_email,
                        context={
                            "parent_name": applicant.parent_full_name,
                            "child_name": applicant.child_full_name,
                            "reference_number": applicant.reference_number,
                            "school_name": _ss.school_name or "the school",
                            "admissions_email": _ss.get_admissions_contact().get("email", ""),
                        },
                    )
                if parent_phone:
                    dispatch_notification(
                        user=None, title="Application Update",
                        message=f"Dear {applicant.parent_full_name}, thank you for meeting with us regarding {applicant.child_full_name}. Unfortunately, we are unable to proceed with the application at this time.",
                        link=None, actor=None, external_email=None, phone=parent_phone,
                    )

            # Parent SMS for admitted/conditional
            if to_status in ("admitted", "conditional"):
                parent_phone = (applicant.parent_phone or "").strip() or None
                if parent_phone:
                    from core.models import SchoolSettings
                    _settings = SchoolSettings.get_settings()
                    contact = _settings.get_admissions_contact()
                    _school_name = _settings.school_name or "the school"
                    label = "admitted" if to_status == "admitted" else "conditionally admitted"
                    # S05: Offer sent SMS (spec copy)
                    sms_msg = (
                        f"Hodari: Good news — {applicant.child_full_name} has been offered a place. "
                        f"Check your email for portal login details. Ref {applicant.reference_number}."
                    )
                    dispatch_notification(
                        user=None, title=f"Applicant {to_status.title()}",
                        message=sms_msg, link=f"/admissions/applicant/{applicant.pk}/",
                        actor=None, external_email=None, phone=parent_phone,
                    )
                    # S05: Dedicated offer-sent SMS (spec copy)
                    offer_sms = (
                        f"Good news — {applicant.child_full_name} has been offered a place in Grade {applicant.grade_applying_for}. "
                        f"Please complete the form and pay the fee within 14 days to secure the spot."
                    )
                    dispatch_notification(
                        user=None, title="Offer Sent",
                        message=offer_sms, link=None,
                        actor=None, external_email=None, phone=parent_phone,
                    )

                # E15: Offer letter email to parent
                parent_email = (applicant.parent_email or "").strip() or None
                if parent_email:
                    from core.email_templates import send_dynamic_email
                    from core.models import SchoolSettings as _SS
                    _ss = _SS.get_settings()
                    _contact = _ss.get_admissions_contact()

                    # Create or get parent portal account for login credentials
                    portal_username = parent_email
                    portal_password = None
                    from students.models import ParentGuardian
                    guardian = ParentGuardian.objects.filter(
                        email__iexact=parent_email
                    ).select_related("user").first()
                    if guardian and guardian.user:
                        portal_username = parent_email
                        import secrets
                        raw_password = secrets.token_urlsafe(8)
                        portal_password = raw_password
                        guardian.user.set_password(raw_password)
                        guardian.user.save(update_fields=["password"])
                    else:
                        # Create parent user account
                        import secrets
                        raw_password = secrets.token_urlsafe(8)
                        portal_password = raw_password
                        parent_user = User.objects.create_user(
                            username=parent_email,
                            email=parent_email,
                            password=raw_password,
                            first_name=applicant.parent_full_name.split()[0] if applicant.parent_full_name else "Parent",
                            last_name=" ".join(applicant.parent_full_name.split()[1:]) if applicant.parent_full_name and len(applicant.parent_full_name.split()) > 1 else "",
                            role=UserRole.PARENT,
                        )
                        # Create or update ParentGuardian
                        if guardian:
                            guardian.user = parent_user
                            guardian.save(update_fields=["user"])
                        else:
                            from students.models import GuardianRelationship
                            guardian = ParentGuardian.objects.create(
                                full_name=applicant.parent_full_name or "Parent",
                                phone=applicant.parent_phone or "",
                                email=parent_email,
                                user=parent_user,
                            )

                    send_dynamic_email(
                        template_type="admission_offer_letter",
                        to_email=parent_email,
                        context={
                            "parent_name": applicant.parent_full_name,
                            "learner_name": applicant.child_full_name,
                            "child_name": applicant.child_full_name,
                            "ref": applicant.reference_number,
                            "reference_number": applicant.reference_number,
                            "grade": applicant.grade_applying_for,
                            "offered_grade": applicant.grade_applying_for,
                            "admission_status": "Admitted" if to_status == "admitted" else "Conditionally Admitted",
                            "academic_year": str(timezone.now().year),
                            "school_name": _ss.school_name or "the school",
                            "currency": "TZS",
                            "admission_fee": f"{_ss.admission_fee:,.0f}",
                            "admissions_email": _contact.get("email", ""),
                            "admissions_whatsapp": _contact.get("whatsapp", ""),
                            "portal_link": "/parent/admission-form/",
                            "portal_username": portal_username,
                            "portal_password": portal_password or "Contact admissions for your password",
                            "offer_expiry_date": (timezone.now() + timezone.timedelta(days=14)).strftime("%d %B %Y"),
                            "term_start_date": "See school calendar",
                            "breakfast_fee": f"{_ss.breakfast_fee:,.0f}" if hasattr(_ss, 'breakfast_fee') else "300,000",
                            "transport_provider": "Upanga Transport Company",
                            "uniform_notes": "T-shirts and sweaters are available at school. Our vendor needs measurements to tailor shorts and trousers.",
                            "meals": "Lunch is included in the fees. The mid-morning meal is an optional extra.",
                            "transport_notes": "Transport is run by our transport partner. Payments go directly to them.",
                            "consent_notes": "The form includes our data consent section, and our code of conduct, school rules and calendar are in the portal.",
                            "finance_email": _contact.get("email", ""),
                            "finance_phone": _contact.get("phone", ""),
                            "hos_email": _contact.get("email", ""),
                            "hos_phone": _contact.get("phone", ""),
                            "parent_email": parent_email,
                        },
                    )

                # NOTE: Invoice is NOT auto-generated here per spec.
                # The parent completes the admission form in the portal (step 10),
                # then generates the invoice from the portal. E18 fires at that point.
                # See parent_portal/views.py:ParentAdmissionFormView and ParentGenerateInvoiceView.

            messages.success(request, "Applicant status updated.")
        except ValidationError as e:
            if hasattr(e, 'message_dict'):
                msg = ", ".join([f"{k}: {v[0]}" for k, v in e.message_dict.items()])
            else:
                msg = e.messages[0] if hasattr(e, 'messages') else str(e).strip("[]' ")
            messages.error(request, msg)
        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        applicant = get_object_or_404(Applicant, pk=pk)
        allowed_targets = get_allowed_transition_targets(from_status=applicant.status, actor_role=self.request.user.role, actor=self.request.user)
        ctx["applicant"] = applicant
        label_map = dict(ApplicantStatus.choices)
        ctx["status_choices"] = [(s, label_map[s]) for s in allowed_targets]
        return ctx


class AssessmentScheduleView(HtmxRequiredMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    template_name = "admissions/_assessment_form.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.schedule_assessment"

    def post(self, request, *args, **kwargs):
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        # Gate: assessment can only be scheduled after meeting is completed (spec: "assessment before meeting outcome" is forbidden)
        allowed_statuses = (
            ApplicantStatus.MEETING_COMPLETED,
            ApplicantStatus.ASSESSMENT_PENDING,
            ApplicantStatus.ASSESSMENT_FEE_PAID,
            ApplicantStatus.ASSESSMENT_CONFIRMED,
            ApplicantStatus.ASSESSMENT_COMPLETED,
        )
        if applicant.status not in allowed_statuses:
            messages.error(request, "Assessment cannot be scheduled until the Head of School has recorded the meeting outcome.")
            return self.get(request, *args, **kwargs)
        # FR-ADM-011: If result exists, clear it on reschedule
        assessment = getattr(applicant, "assessment", None)
        if assessment and assessment.result:
            assessment.result = None
            assessment.teacher_comments = ""
            assessment.hod_comments = ""
            assessment.submitted_by = None
            assessment.submitted_at = None
            assessment.save(update_fields=["result", "teacher_comments", "hod_comments", "submitted_by", "submitted_at", "updated_at"])
            messages.warning(request, "Previous assessment result has been cleared and will need to be resubmitted.")
        # FR-ADM-011: Logistics auto-resend guard — warn and reset logistics_sent_at if date/time changed
        if assessment and assessment.logistics_sent_at:
            new_date = request.POST.get("scheduled_date")
            new_time = request.POST.get("scheduled_time")
            if (new_date and str(new_date) != str(assessment.scheduled_date)) or \
               (new_time and str(new_time) != str(assessment.scheduled_time)):
                # Reset logistics so AO must re-send with updated details
                assessment.logistics_sent_at = None
                assessment.save(update_fields=["logistics_sent_at", "updated_at"])
                messages.warning(request, "Assessment date/time changed after logistics were sent. Logistics have been reset — please re-send updated logistics to the parent.")
        form = AssessmentScheduleForm(request.POST, instance=assessment)
        if form.is_valid():
            try:
                cleaned = form.cleaned_data.copy()
                from core.models import SchoolSettings
                _settings = SchoolSettings.get_settings()
                cleaned["assessment_fee_amount"] = _settings.assessment_fee
                cleaned["location"] = _settings.school_name or "Main Campus"
                schedule_assessment(applicant=applicant, actor=request.user, **cleaned)
                # E06: Assessment scheduled + fee due → parent email
                parent_email = (applicant.parent_email or "").strip() or None
                if parent_email:
                    from core.email_templates import send_dynamic_email
                    from core.models import SchoolSettings as _SS
                    _ss = _SS.get_settings()
                    _contact = _ss.get_admissions_contact()
                    new_assessment = getattr(applicant, "assessment", None)
                    send_dynamic_email(
                        template_type="admission_assessment_fee_invoice",
                        to_email=parent_email,
                        context={
                            "parent_name": applicant.parent_full_name,
                            "child_name": applicant.child_full_name,
                            "grade": applicant.grade_applying_for,
                            "reference_number": applicant.reference_number,
                            "assessment_date": str(new_assessment.scheduled_date) if new_assessment else "TBD",
                            "assessment_time": str(new_assessment.scheduled_time.strftime("%I:%M %p")) if new_assessment and new_assessment.scheduled_time else "TBD",
                            "assessment_fee": f"TSh {_ss.assessment_fee:,.0f}",
                            "finance_email": _contact.get("email", ""),
                            "admissions_phone": _contact.get("phone", ""),
                            "whatsapp_number": _contact.get("whatsapp", ""),
                            "school_name": _ss.school_name or "the school",
                        },
                    )
                messages.success(request, "Assessment scheduled and moved to 'Meeting' stage.")
                return self.render_to_response(self.get_context_data())
            except ValidationError as e:
                if hasattr(e, 'message_dict'):
                    msg = ", ".join([f"{k}: {v[0]}" for k, v in e.message_dict.items()])
                else:
                    msg = e.messages[0] if hasattr(e, 'messages') else str(e).strip("[]' ")
                messages.error(request, f"Workflow error: {msg}")
                return self.render_to_response(self.get_context_data(form=form))
        else:
            messages.error(request, "Please correct the assessment form errors.")
            return self.render_to_response(self.get_context_data(form=form))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        applicant = get_object_or_404(Applicant, pk=pk)
        ctx["applicant"] = applicant
        ctx["form"] = kwargs.get("form") or AssessmentScheduleForm(instance=getattr(applicant, "assessment", None))
        return ctx


class MeetingScheduleView(HtmxRequiredMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    """Schedule a meeting for an applicant at the inquiry stage."""
    template_name = "admissions/_meeting_form.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.transition_applicant_status"

    def post(self, request, *args, **kwargs):
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        if applicant.status != ApplicantStatus.INQUIRY_RECEIVED:
            messages.error(request, "Meeting can only be scheduled at the inquiry stage.")
            return self.get(request, *args, **kwargs)
        form = MeetingScheduleForm(request.POST)
        if form.is_valid():
            meeting = form.save(commit=False)
            meeting.applicant = applicant
            meeting.save()
            transition_applicant_status(
                applicant=applicant,
                to_status=ApplicantStatus.MEETING_SCHEDULED,
                actor=request.user,
                reason=f"Meeting scheduled for {meeting.meeting_date} at {meeting.meeting_time}.",
            )

            # NOTIF: Notify parent that meeting has been scheduled (E02)
            parent_phone = (applicant.parent_phone or "").strip() or None
            if (applicant.parent_email or "").strip() or parent_phone:
                from communications.email_service import send_email_safe, dispatch_notification
                from core.models import SchoolSettings
                contact = SchoolSettings.get_settings().get_admissions_contact()
                parent_name = applicant.parent_full_name or "Parent/Guardian"
                ref = applicant.reference_number
                day_str = meeting.meeting_date.strftime("%A, %d %B %Y")
                time_str = meeting.meeting_time.strftime("%I:%M %p").lstrip("0")
                school_name = SchoolSettings.get_settings().school_name or "the school"

                email_msg = (
                    f"Dear {parent_name},\n\n"
                    f"Thank you for your interest in {school_name}. "
                    "Your meeting with our Head of School has been confirmed.\n\n"
                    f"Date: {day_str}\n"
                    f"Time: {time_str}\n"
                    f"Venue: {school_name}, main reception\n"
                    f"Reference: {ref}\n\n"
                    "The meeting takes about 45 minutes. Please bring your child "
                    "with you, along with their most recent school report if they "
                    "are currently enrolled elsewhere.\n\n"
                    f"If you need to change this appointment, call us on {contact['phone']}.\n\n"
                    "We look forward to meeting you.\n\n"
                    "Admissions Office\n"
                    f"{school_name}"
                )

                if (applicant.parent_email or "").strip():
                    from core.email_templates import send_dynamic_email
                    tpl_context = {
                        "parent_name": parent_name,
                        "child_name": applicant.child_full_name,
                        "meeting_date": day_str,
                        "meeting_time": time_str,
                        "ref": ref,
                        "school_name": school_name,
                        "contact_phone": contact['phone'],
                    }
                    db_sent = send_dynamic_email(
                        template_type="admission_meeting",
                        to_email=applicant.parent_email.strip(),
                        context=tpl_context,
                    )
                    if not db_sent:
                        send_email_safe(
                            to_email=applicant.parent_email.strip(),
                            subject=f"Your meeting with the Head of School \u2014 {ref}",
                            body=email_msg,
                        )

                # S01: Meeting confirmed SMS (spec copy)
                sms_msg = (
                    f"Hodari: Your meeting with the Head of School is confirmed for "
                    f"{day_str} at {time_str}. Ref {ref}."
                )
                dispatch_notification(
                    user=None, title="Meeting Scheduled", message=sms_msg,
                    link=f"/admissions/applicant/{applicant.pk}/", actor=None,
                    external_email=None, phone=parent_phone,
                )

            messages.success(request, "Meeting scheduled and applicant moved to Meeting scheduled.")
            return self.render_to_response(self.get_context_data())
        else:
            messages.error(request, "Please correct the form errors.")
            return self.render_to_response(self.get_context_data(form=form))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        applicant = get_object_or_404(Applicant, pk=pk)
        ctx["applicant"] = applicant
        if "form" in kwargs:
            ctx["form"] = kwargs["form"]
        elif getattr(applicant, "meeting", None):
            ctx["form"] = MeetingScheduleForm(instance=applicant.meeting)
        else:
            initial = {}
            if applicant.preferred_meeting_date:
                initial["meeting_date"] = applicant.preferred_meeting_date
            if applicant.preferred_meeting_time:
                initial["meeting_time"] = applicant.preferred_meeting_time.strftime("%H:%M") if hasattr(applicant.preferred_meeting_time, 'strftime') else str(applicant.preferred_meeting_time)
            ctx["form"] = MeetingScheduleForm(initial=initial)
        return ctx


class MeetingRescheduleView(HtmxRequiredMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    """Reschedule an existing meeting. Sends E04 notification to parent."""
    template_name = "admissions/_meeting_reschedule_form.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.transition_applicant_status"

    def post(self, request, *args, **kwargs):
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        if applicant.status != ApplicantStatus.MEETING_SCHEDULED:
            messages.error(request, "Rescheduling is only available when a meeting is scheduled.")
            return self.get(request, *args, **kwargs)
        meeting = getattr(applicant, "meeting", None)
        if not meeting:
            messages.error(request, "No meeting to reschedule.")
            return self.get(request, *args, **kwargs)
        # Reschedule cap: max 2 reschedules per applicant
        reschedule_count = ApplicantTimelineEntry.objects.filter(
            applicant=applicant,
            to_status=ApplicantStatus.MEETING_SCHEDULED,
        ).count()
        if reschedule_count >= 2:
            messages.error(request, "This applicant has reached the maximum of 2 meeting reschedules. Please proceed to record the meeting outcome instead.")
            return self.get(request, *args, **kwargs)

        form = MeetingScheduleForm(request.POST, instance=meeting)
        if form.is_valid():
            old_date = meeting.meeting_date
            old_time = meeting.meeting_time
            updated_meeting = form.save()

            # E04: Notify parent of rescheduled meeting
            parent_phone = (applicant.parent_phone or "").strip() or None
            if (applicant.parent_email or "").strip() or parent_phone:
                from communications.email_service import send_email_safe, dispatch_notification
                from core.models import SchoolSettings
                contact = SchoolSettings.get_settings().get_admissions_contact()
                parent_name = applicant.parent_full_name or "Parent/Guardian"
                ref = applicant.reference_number
                prev_day_str = old_date.strftime("%A, %d %B %Y")
                new_day_str = updated_meeting.meeting_date.strftime("%A, %d %B %Y")
                new_time_str = updated_meeting.meeting_time.strftime("%I:%M %p").lstrip("0")

                if (applicant.parent_email or "").strip():
                    from core.email_templates import send_dynamic_email
                    tpl_context = {
                        "parent_name": parent_name,
                        "child_name": applicant.child_full_name,
                        "previous_date": prev_day_str,
                        "new_date": new_day_str,
                        "new_time": new_time_str,
                        "ref": ref,
                        "school_name": SchoolSettings.get_settings().school_name or "the school",
                        "contact_phone": contact['phone'],
                    }
                    db_sent = send_dynamic_email(
                        template_type="admission_meeting_rescheduled",
                        to_email=applicant.parent_email.strip(),
                        context=tpl_context,
                    )
                    if not db_sent:
                        _school_name = SchoolSettings.get_settings().school_name or "the school"
                        send_email_safe(
                            to_email=applicant.parent_email.strip(),
                            subject=f"Rescheduled: your meeting with the Head of School \u2014 {ref}",
                            body=(
                                f"Dear {parent_name},\n\n"
                                f"We missed you at your appointment on {prev_day_str}. "
                                "Your meeting has been rescheduled.\n\n"
                                f"New date: {new_day_str}\n"
                                f"New time: {new_time_str}\n"
                                f"Venue: {_school_name}, main reception\n"
                                f"Reference: {ref}\n\n"
                                f"If this time does not suit you, call us on {contact['phone']} "
                                "and we will find one that does.\n\n"
                                "Admissions Office\n"
                                f"{_school_name}"
                            ),
                        )

                # SMS fallback for meeting rescheduled
                _school_name = SchoolSettings.get_settings().school_name or "the school"
                sms_msg = (
                    f"Hi {parent_name}, {applicant.child_full_name}'s meeting has been "
                    f"rescheduled to {new_day_str} at {new_time_str}, {_school_name}. "
                    f"Ref: {ref}. Call {contact['phone']} if needed."
                )
                dispatch_notification(
                    user=None, title="Meeting Rescheduled", message=sms_msg,
                    link=f"/admissions/applicant/{applicant.pk}/", actor=None,
                    external_email=None, phone=parent_phone,
                )

            messages.success(request, "Meeting rescheduled. Parent has been notified (E04).")
            return self.render_to_response(self.get_context_data())
        else:
            messages.error(request, "Please correct the form errors.")
            return self.render_to_response(self.get_context_data(form=form))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        applicant = get_object_or_404(Applicant, pk=pk)
        ctx["applicant"] = applicant
        meeting = getattr(applicant, "meeting", None)
        ctx["form"] = kwargs.get("form") or MeetingScheduleForm(instance=meeting)
        return ctx


class AssessmentResultSignOffView(HtmxRequiredMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    """Teacher submits assessment result. Requires submit_assessment_result permission."""
    template_name = "admissions/_assessment_result_form.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.submit_assessment_result"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_perm("admissions.submit_assessment_result"):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        assessment = getattr(applicant, "assessment", None)
        fee_paid = assessment and assessment.assessment_fee_confirmed_paid
        status_ok = applicant.status in (
            ApplicantStatus.ASSESSMENT_COMPLETED,
            ApplicantStatus.ASSESSMENT_FEE_PAID,
            ApplicantStatus.ASSESSMENT_CONFIRMED,
        )
        if not (status_ok and fee_paid):
            messages.error(request, "Assessment must be completed and fee paid before submitting a result.")
            return self.get(request, *args, **kwargs)
        # FR-ADM-017: Cannot submit result before assessment date
        assessment = getattr(applicant, "assessment", None)
        if assessment:
            from django.utils import timezone
            if timezone.now().date() < assessment.scheduled_date:
                messages.error(request, "Result cannot be submitted before the assessment date.")
                return self.get(request, *args, **kwargs)
            # FR-ADM-017: Only the facilitating teacher can submit (not any teacher)
            if request.user.role == UserRole.TEACHER:
                user_full_name = (request.user.get_full_name() or request.user.username).strip()
                assigned_teacher = (assessment.facilitating_teacher_name or "").strip()
                if user_full_name.lower() != assigned_teacher.lower():
                    messages.error(request, "Only the assigned facilitating teacher can submit this assessment result.")
                    return self.get(request, *args, **kwargs)

        form = AssessmentResultForm(request.POST, instance=getattr(applicant, "assessment", None))
        if form.is_valid():
            try:
                sign_off_assessment_result(
                    applicant=applicant,
                    actor=request.user,
                    result=form.cleaned_data["result"],
                    teacher_comments=form.cleaned_data.get("teacher_comments", ""),
                    hod_comments=form.cleaned_data["hod_comments"],
                    parent_facing_comments=form.cleaned_data.get("parent_facing_comments", ""),
                )
                messages.success(request, "Assessment result submitted.")
                return self.render_to_response(self.get_context_data())
            except ValidationError as e:
                if hasattr(e, 'message_dict'):
                    msg = ", ".join([f"{k}: {v[0]}" for k, v in e.message_dict.items()])
                else:
                    msg = e.messages[0] if hasattr(e, 'messages') else str(e).strip("[]' ")
                messages.error(request, f"Workflow error: {msg}")
                return self.render_to_response(self.get_context_data(form=form))
        else:
            messages.error(request, "Please correct the result form errors.")
            return self.render_to_response(self.get_context_data(form=form))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        applicant = get_object_or_404(Applicant, pk=pk)
        ctx["applicant"] = applicant
        ctx["form"] = kwargs.get("form") or AssessmentResultForm(instance=getattr(applicant, "assessment", None))
        return ctx


class AssessmentResultEditView(HtmxRequiredMixin, LoginRequiredMixin, TemplateView):
    """Display the teacher's submitted assessment report (read-only)."""
    template_name = "admissions/_assessment_result_form.html"
    login_url = "/accounts/login/"

    EOT = [("math_eot", "Mathematics"), ("english_eot", "English"), ("science_eot", "Science")]
    CAM = [("math_cam", "Mathematics Paper 1"), ("english_cam", "English Paper 1"), ("science_cam", "Science Paper 1")]
    TRAIT_CATS = [
        ("Work Habits", [("wh_follows_directions", "Follows directions"), ("wh_works_independently", "Works independently"), ("wh_not_disturb", "Does not disturb"), ("wh_completes_neatly", "Completes neatly")]),
        ("Personal Traits", [("pt_honest", "Is honest"), ("pt_flexibility", "Displays flexibility"), ("pt_attention", "Attention span"), ("pt_creativity", "Displays creativity")]),
        ("Social Traits", [("st_courteous", "Is courteous"), ("st_self_control", "Self-control"), ("st_respects", "Respects authority"), ("st_relates_well", "Relates well")]),
    ]

    def _pct_to_grade(self, pct):
        try:
            v = float(pct)
        except (ValueError, TypeError):
            return ""
        if v >= 90: return "A*"
        if v >= 80: return "A"
        if v >= 70: return "B"
        if v >= 60: return "C"
        if v >= 50: return "D"
        return "E"

    def _build_subject_rows(self, subjects, data):
        rows = []
        pcts = []
        for key, label in subjects:
            pct = data.get("pct_%s" % key, "")
            grade = data.get("grade_%s" % key, "") or self._pct_to_grade(pct)
            remark = data.get("remark_%s" % key, "")
            if pct:
                try: pcts.append(float(pct))
                except: pass
            rows.append({"label": label, "pct": pct, "grade": grade, "remark": remark, "is_avg": False})
        if pcts:
            avg = sum(pcts) / len(pcts)
            rows.append({"label": "Average", "pct": str(round(avg, 1)), "grade": self._pct_to_grade(avg), "remark": "", "is_avg": True})
        return rows

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        applicant = get_object_or_404(Applicant, pk=pk)
        ctx["applicant"] = applicant
        from tasks.models import Task
        task = Task.objects.filter(
            task_type="assessment_report",
        ).filter(
            Q(metadata__applicant_id=applicant.pk) | Q(metadata__applicant_id=str(applicant.pk))
        ).order_by("-updated_at").first()
        data = {}
        if task:
            data = task.metadata.get("assessment_data", {})
        if not data:
            raw = getattr(applicant.assessment, "hod_comments", "") if applicant.assessment else ""
            if raw:
                import json as _json
                try: data = _json.loads(raw)
                except Exception: pass
        if not data and applicant.assessment and applicant.assessment.teacher_comments:
            data = {"teacher_comments": applicant.assessment.teacher_comments}
        ctx["assessment_data"] = data
        ctx["eot_subjects"] = self._build_subject_rows(self.EOT, data)
        ctx["cam_subjects"] = self._build_subject_rows(self.CAM, data)
        ctx["trait_categories"] = []
        for cat_label, traits in self.TRAIT_CATS:
            ctx["trait_categories"].append({
                "label": cat_label,
                "traits": [{"label": tlabel, "value": data.get("trait_%s" % tkey, "")} for tkey, tlabel in traits]
            })
        return ctx



class HodReviewSubmitView(HtmxRequiredMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    """HOS submits review comments and forwards recommendation to HOS.
    FR-ADM-018/019: Requires submit_hos_review permission."""
    template_name = "admissions/_hos_review_form.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.submit_hos_review"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_perm("admissions.submit_hos_review"):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        if applicant.status != ApplicantStatus.HOS_REVIEW:
            from django.http import HttpResponse
            return HttpResponse('<div style="padding:10px;color:var(--text-muted);font-size:12px">Applicant is not in HOS review status.</div>')

        form = HodReviewForm(request.POST, instance=getattr(applicant, "assessment", None))
        if form.is_valid():
            try:
                submit_hos_review(
                    applicant=applicant,
                    actor=request.user,
                    hos_comments="",
                    parent_facing_comments=form.cleaned_data.get("parent_facing_comments", ""),
                )
                from django.http import HttpResponse
                resp = HttpResponse()
                resp["HX-Trigger"] = "refreshPage"
                resp.content = '<div style="padding:10px;color:#16A34A;font-size:12px;font-weight:600">Review submitted. Moving to decision...</div>'
                return resp
            except ValidationError as e:
                msg = e.messages[0] if hasattr(e, 'messages') else str(e).strip("[]' ")
                from django.http import HttpResponse
                return HttpResponse(f'<div style="padding:10px;color:#DC2626;font-size:12px;font-weight:600">{msg}</div>')
        else:
            from django.http import HttpResponse
            return HttpResponse('<div style="padding:10px;color:#DC2626;font-size:12px;font-weight:600">Please correct the form errors.</div>')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        applicant = get_object_or_404(Applicant, pk=pk)
        ctx["applicant"] = applicant
        ctx["form"] = kwargs.get("form") or HodReviewForm(instance=getattr(applicant, "assessment", None))
        ctx["is_hos_review"] = True
        return ctx


class ApplicantDecisionView(HtmxRequiredMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    """HTMX endpoint: HOS Decision card with Admit/Conditional/Deny/Waitlist buttons."""
    template_name = "admissions/_decision_form.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.transition_applicant_status"

    def post(self, request, *args, **kwargs):
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        if applicant.status not in (ApplicantStatus.HOS_DECISION, ApplicantStatus.WAITLISTED):
            messages.error(request, "Applicant is not at the decision stage.")
            return self.get(request, *args, **kwargs)
        to_status = request.POST.get("to_status", "").strip()
        reason = request.POST.get("reason", "").strip()
        conditions = request.POST.get("conditions", "").strip()
        allowed = get_allowed_transition_targets(
            from_status=applicant.status,
            actor_role=getattr(request.user, "role", None),
            actor=request.user,
        )
        if to_status not in allowed:
            messages.error(request, f"Cannot transition to '{to_status}' from the current stage.")
            return self.get(request, *args, **kwargs)
        try:
            from django.utils import timezone
            kwargs_t = {"applicant": applicant, "actor": request.user, "to_status": to_status, "reason": reason}
            if to_status == ApplicantStatus.CONDITIONAL:
                kwargs_t["conditions"] = conditions
            transition_applicant_status(**kwargs_t)
            label = dict(ApplicantStatus.choices).get(to_status, to_status)
            messages.success(request, f"Applicant moved to {label}.")

            # E15: Send offer letter email + create portal account for admitted/conditional
            if to_status in (ApplicantStatus.ADMITTED, ApplicantStatus.CONDITIONAL):
                parent_phone = (applicant.parent_phone or "").strip() or None
                if parent_phone:
                    from communications.email_service import dispatch_notification
                    from core.models import SchoolSettings as _SS
                    _settings = _SS.get_settings()
                    contact = _settings.get_admissions_contact()
                    _school_name = _settings.school_name or "the school"
                    sms_msg = (
                        f"Hodari: Good news -- {applicant.child_full_name} has been offered a place. "
                        f"Check your email for portal login details. Ref {applicant.reference_number}."
                    )
                    dispatch_notification(
                        user=None, title=f"Applicant {to_status.title()}",
                        message=sms_msg, link=f"/admissions/applicant/{applicant.pk}/",
                        actor=None, external_email=None, phone=parent_phone,
                    )
                    offer_sms = (
                        f"Good news -- {applicant.child_full_name} has been offered a place in Grade {applicant.grade_applying_for}. "
                        f"Please complete the form and pay the fee within 14 days to secure the spot."
                    )
                    dispatch_notification(
                        user=None, title="Offer Sent",
                        message=offer_sms, link=None,
                        actor=None, external_email=None, phone=parent_phone,
                    )

                parent_email = (applicant.parent_email or "").strip() or None
                if parent_email:
                    from core.email_templates import send_dynamic_email
                    from core.models import SchoolSettings as _SS
                    _ss = _SS.get_settings()
                    _contact = _ss.get_admissions_contact()

                    portal_username = parent_email
                    portal_password = None
                    from students.models import ParentGuardian
                    guardian = ParentGuardian.objects.filter(
                        email__iexact=parent_email
                    ).select_related("user").first()
                    if guardian and guardian.user:
                        portal_username = parent_email
                        import secrets
                        raw_password = secrets.token_urlsafe(8)
                        portal_password = raw_password
                        guardian.user.set_password(raw_password)
                        guardian.user.save(update_fields=["password"])
                    else:
                        import secrets
                        raw_password = secrets.token_urlsafe(8)
                        portal_password = raw_password
                        from users.models import User as _User, UserRole as _UR
                        parent_user = _User.objects.create_user(
                            username=parent_email,
                            email=parent_email,
                            password=raw_password,
                            first_name=applicant.parent_full_name.split()[0] if applicant.parent_full_name else "Parent",
                            last_name=" ".join(applicant.parent_full_name.split()[1:]) if applicant.parent_full_name and len(applicant.parent_full_name.split()) > 1 else "",
                            role=_UR.PARENT,
                        )
                        if guardian:
                            guardian.user = parent_user
                            guardian.save(update_fields=["user"])
                        else:
                            from students.models import GuardianRelationship
                            guardian = ParentGuardian.objects.create(
                                full_name=applicant.parent_full_name or "Parent",
                                phone=applicant.parent_phone or "",
                                email=parent_email,
                                user=parent_user,
                            )

                    send_dynamic_email(
                        template_type="admission_offer_letter",
                        to_email=parent_email,
                        context={
                            "parent_name": applicant.parent_full_name,
                            "learner_name": applicant.child_full_name,
                            "child_name": applicant.child_full_name,
                            "ref": applicant.reference_number,
                            "reference_number": applicant.reference_number,
                            "grade": applicant.grade_applying_for,
                            "offered_grade": applicant.grade_applying_for,
                            "admission_status": "Admitted" if to_status == "admitted" else "Conditionally Admitted",
                            "academic_year": str(timezone.now().year),
                            "school_name": _ss.school_name or "the school",
                            "currency": "TZS",
                            "admission_fee": f"{_ss.admission_fee:,.0f}",
                            "admissions_email": _contact.get("email", ""),
                            "admissions_whatsapp": _contact.get("whatsapp", ""),
                            "portal_link": "/parent/admission-form/",
                            "portal_username": portal_username,
                            "portal_password": portal_password or "Contact admissions for your password",
                            "offer_expiry_date": (timezone.now() + timezone.timedelta(days=14)).strftime("%d %B %Y"),
                            "term_start_date": "See school calendar",
                            "breakfast_fee": f"{_ss.breakfast_fee:,.0f}" if hasattr(_ss, 'breakfast_fee') else "300,000",
                            "transport_provider": "Upanga Transport Company",
                            "finance_email": _contact.get("email", ""),
                            "finance_phone": _contact.get("phone", ""),
                            "hos_email": _contact.get("email", ""),
                            "hos_phone": _contact.get("phone", ""),
                            "parent_email": parent_email,
                        },
                    )

        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e).strip("[]' ")
            messages.error(request, msg)
        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        applicant = get_object_or_404(Applicant, pk=pk)
        ctx["applicant"] = applicant
        return ctx


class ApplicantRevertView(HtmxRequiredMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    """HTMX endpoint: Revert an enrolled applicant to a previous stage (archives student)."""
    template_name = "admissions/_revert_form.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.transition_applicant_status"

    def post(self, request, *args, **kwargs):
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        if applicant.status != ApplicantStatus.ENROLLED:
            messages.error(request, "Only enrolled applicants can be reverted.")
            return self.get(request, *args, **kwargs)
        to_status = request.POST.get("to_status", "").strip()
        reason = request.POST.get("reason", "").strip()
        allowed = get_allowed_transition_targets(
            from_status=applicant.status,
            actor_role=getattr(request.user, "role", None),
            actor=request.user,
        )
        if to_status not in allowed:
            messages.error(request, f"Cannot revert to '{to_status}' from the current stage.")
            return self.get(request, *args, **kwargs)
        if not reason:
            messages.error(request, "A reason is required to revert an applicant.")
            return self.get(request, *args, **kwargs)
        try:
            revert_applicant_from_enrolled(
                applicant=applicant,
                to_status=to_status,
                actor=request.user,
                reason=reason,
            )
            label = dict(ApplicantStatus.choices).get(to_status, to_status)
            messages.success(request, f"Applicant reverted to {label}. Student record archived.")
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e).strip("[]' ")
            messages.error(request, msg or "Failed to revert applicant.")
        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        applicant = get_object_or_404(Applicant, pk=pk)
        ctx["applicant"] = applicant
        return ctx


class ConfirmAssessmentFeePaidView(HtmxRequiredMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    template_name = "admissions/_fee_actions.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.confirm_assessment_fee"

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST":
            # FR-FIN-007: Only Finance Officer and Super Admin can confirm assessment fees
            if request.user.is_authenticated and request.user.role not in (
                UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN
            ):
                raise PermissionDenied("Only Finance Officers can confirm assessment fee payments.")
            return super().dispatch(request, *args, **kwargs)
        return TemplateView.dispatch(self, request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        # FR-FIN-007: Payment method and reference are required on confirmation
        payment_method = request.POST.get("payment_method", "").strip()
        payment_reference = request.POST.get("payment_reference", "").strip()
        if not payment_method:
            messages.error(request, "A payment method is required to confirm payment.")
            return self.get(request, *args, **kwargs)
        try:
            confirm_assessment_fee_paid(
                applicant=applicant,
                actor=request.user,
                payment_method=payment_method,
                payment_reference=payment_reference,
            )
            messages.success(request, "Assessment fee confirmed paid.")
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e).strip("[]' ")
            messages.error(request, msg)
        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        ctx["applicant"] = applicant
        return ctx


class UnconfirmAssessmentFeeView(HtmxRequiredMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    """FR-FIN-011: SA can reverse a confirmed assessment fee via reversal entry."""
    template_name = "admissions/_fee_actions.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.reverse_assessment_fee"

    def dispatch(self, request, *args, **kwargs):
        # FR-FIN-011: Only Finance Officer and Super Admin can reverse assessment fees
        if request.user.is_authenticated and request.user.role not in (
            UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN
        ):
            raise PermissionDenied("Only Finance Officers can reverse assessment fee confirmations.")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        assessment = getattr(applicant, "assessment", None)
        if not assessment or not assessment.assessment_fee_confirmed_paid:
            messages.error(request, "No confirmed assessment fee to reverse.")
            return self.get(request, *args, **kwargs)

        # FR-FIN-011: Reverse the Invoice record in finance
        from finance.models import Invoice, InvoiceStatus
        invoice = Invoice.objects.filter(applicant=applicant).first()
        old_invoice_status = None
        if invoice:
            old_invoice_status = invoice.status
            invoice.status = InvoiceStatus.UNPAID
            invoice.save(update_fields=["status", "updated_at"])

        assessment.assessment_fee_confirmed_paid = False
        assessment.assessment_fee_confirmed_at = None
        assessment.assessment_fee_confirmed_by = None
        assessment.assessment_fee_method = ""
        assessment.assessment_fee_reference = ""
        assessment.save(update_fields=[
            "assessment_fee_confirmed_paid", "assessment_fee_confirmed_at",
            "assessment_fee_confirmed_by_id", "assessment_fee_method",
            "assessment_fee_reference", "updated_at",
        ])

        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="ASSESSMENT_FEE_REVERSAL",
            model_name="AssessmentSchedule",
            object_id=assessment.pk,
            description=f"Assessment fee confirmation reversed for {applicant.child_full_name} by {request.user.username}",
            request=request,
        )

        messages.success(request, "Assessment fee confirmation reversed.")
        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        ctx["applicant"] = applicant
        return ctx


class SendLogisticsView(HtmxRequiredMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    template_name = "admissions/_fee_actions.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.send_assessment_logistics"

    def post(self, request, *args, **kwargs):
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        try:
            mark_logistics_sent(applicant=applicant, actor=request.user)
            messages.success(request, "Assessment logistics marked as sent.")
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e).strip("[]' ")
            messages.error(request, msg)
        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        ctx["applicant"] = applicant
        return ctx


class ToggleDocumentView(HtmxRequiredMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    template_name = "admissions/_documents.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.toggle_admission_document"

    def post(self, request, *args, **kwargs):
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        doc_type = request.POST.get("document_type", "").strip()
        received = request.POST.get("received", "") == "1"
        uploaded_file = request.FILES.get("file")
        if uploaded_file:
            from academics.validators import validate_attachment_file
            try:
                validate_attachment_file(uploaded_file, area="admission_documents")
            except ValidationError as ve:
                messages.error(request, str(ve.message) if hasattr(ve, 'message') else str(ve))
                return self.get(request, *args, **kwargs)
        try:
            toggle_document_received(
                applicant=applicant, 
                doc_type=doc_type, 
                actor=request.user, 
                received=received,
                file=uploaded_file
            )
            # Notify applicant parent that their document was received
            from communications.email_service import dispatch_notification
            doc_label = dict(ApplicantDocumentType.choices).get(doc_type, doc_type)
            parent_email = (applicant.parent_email or "").strip() or None
            parent_phone = (applicant.parent_phone or "").strip() or None
            if parent_email:
                from core.email_templates import send_dynamic_email
                from core.models import SchoolSettings
                tpl_context = {
                    "parent_name": applicant.parent_full_name or "Parent/Guardian",
                    "child_name": applicant.child_full_name,
                    "document_type": doc_label,
                    "ref": applicant.reference_number,
                    "school_name": SchoolSettings.get_settings().school_name or "Hodari Christian School",
                    "admissions_email": SchoolSettings.get_settings().get_admissions_contact().get('email', ''),
                }
                send_dynamic_email(
                    template_type="admission_document_received",
                    to_email=parent_email,
                    context=tpl_context,
                )
            dispatch_notification(
                user=None, title="Document Received",
                message=f"Dear {applicant.parent_full_name}, your {doc_label} for {applicant.child_full_name}'s application has been received and processed.",
                link=None, actor=request.user,
                external_email=parent_email, phone=parent_phone,
            )
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e).strip("[]' ")
            messages.error(request, msg)
        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        ctx["applicant"] = applicant
        ensure_default_documents(applicant)
        ctx["documents_by_type"] = {d.document_type: d for d in applicant.documents.all()}
        ctx["document_types"] = ApplicantDocumentType.choices
        ctx["document_rows"] = [
            {"key": dt, "label": label, "doc": ctx["documents_by_type"].get(dt)}
            for dt, label in ApplicantDocumentType.choices
        ]
        return ctx


class CompleteEnrolmentView(HtmxRequiredMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    template_name = "admissions/_enrolment_actions.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.complete_enrolment"

    def post(self, request, *args, **kwargs):
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        action = request.POST.get("action", "").strip()
        if action == "orientation_done":
            mark_orientation_visit_completed(applicant=applicant, actor=request.user, completed=True)
            messages.success(request, "Orientation visit marked complete.")
            return self.get(request, *args, **kwargs)

        # FR-ADM-024 / EC-08: Validate checklist before attempting enrolment.
        # Return 400 early if critical items are missing rather than falling
        # through to a 200 response.
        from admissions.services import ensure_default_documents, ensure_enrolment_checklist
        ensure_default_documents(applicant)
        checklist = ensure_enrolment_checklist(applicant)
        missing_docs = applicant.documents.filter(is_received=False).exists()
        if missing_docs:
            msg = "All checklist items must be completed before enrolment can be finalized."
            messages.error(request, msg)
            response = self.render_to_response(self.get_context_data())
            response.status_code = 400
            return response

        try:
            override = request.POST.get("override_duplicate") == "1"
            _consent_from_inquiry = False
            if applicant.notes:
                try:
                    import json as _json
                    _notes = _json.loads(applicant.notes) if isinstance(applicant.notes, str) else applicant.notes
                    if isinstance(_notes, dict):
                        _consent_from_inquiry = _notes.get("consent", {}).get("core", False)
                except Exception:
                    pass
                if not _consent_from_inquiry and "PDPA consent: Yes" in applicant.notes:
                    _consent_from_inquiry = True
            pdpa_consent = request.POST.get("pdpa_consent") == "on" or _consent_from_inquiry
            pdpa_consent_version = request.POST.get("pdpa_consent_version", "").strip() or "v1.0"
            confirm_sib = request.POST.get("confirm_sibling") == "1"
            student = complete_enrolment(
                applicant=applicant,
                actor=request.user,
                override_duplicate=override,
                pdpa_consent_given=pdpa_consent,
                pdpa_consent_version=pdpa_consent_version,
                confirm_sibling=confirm_sib
            )
            messages.success(request, f"Enrolment complete. Student created: {student.admission_no}.")
            if request.headers.get("HX-Request") == "true":
                from django.http import HttpResponse
                from django.urls import reverse
                response = HttpResponse("")
                response["HX-Location"] = reverse("admissions:detail", kwargs={"pk": applicant.id})
                return response
            return redirect("admissions:detail", pk=applicant.id)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e).strip("[]' ")
            messages.error(request, msg)
            response = self.render_to_response(self.get_context_data())
            response.status_code = 400
            return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        ctx["applicant"] = applicant
        ensure_default_documents(applicant)
        checklist = ensure_enrolment_checklist(applicant)
        ctx["checklist"] = checklist
        ctx["documents_by_type"] = {d.document_type: d for d in applicant.documents.all()}
        ctx["document_types"] = ApplicantDocumentType.choices
        ctx["document_rows"] = [
            {"key": dt, "label": label, "doc": ctx["documents_by_type"].get(dt)}
            for dt, label in ApplicantDocumentType.choices
        ]

        from finance.models import Invoice
        ctx["admission_invoice"] = Invoice.objects.filter(
            applicant=applicant, invoice_number__startswith="ADM-"
        ).first()
        
        # FR-STU-004: Detect potential siblings for UI confirmation
        from students.models import Student, ParentGuardian
        guardian = ParentGuardian.objects.filter(phone=applicant.parent_phone.strip(), is_archived=False).first()
        if guardian:
            ctx["potential_siblings"] = Student.objects.filter(studentguardian__guardian=guardian, is_archived=False)
        else:
            ctx["potential_siblings"] = Student.objects.none()

        return ctx


class OfferLetterView(PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    template_name = "admissions/offer_letter.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.view_applicant"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        
        # FR-ADM-022 sequencing gate: clearance form MUST be received.
        clearance_received = applicant.documents.filter(
            document_type=ApplicantDocumentType.CLEARANCE_FORM, is_received=True
        ).exists()
        if not clearance_received:
            raise PermissionDenied("Cannot issue admission package. Clearance form from previous school has not been received.")
            
        ctx["applicant"] = applicant
        return ctx


class AdmissionsActionsView(AdmissionsCountsMixin, PermissionCacheMixin, AdmissionsRoleRequiredMixin, TemplateView):
    template_name = "admissions/actions.html"
    login_url = "/accounts/login/"
    required_permission = "admissions.view_applicant"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .services import get_critical_actions
        actions = get_critical_actions()
        ctx["actions"] = actions
        ctx["action_count"] = len(actions)
        ctx["admissions_tab"] = "actions"
        return ctx


class ParentGuardianLookupView(PermissionCacheMixin, AdmissionsRoleRequiredMixin, View):
    required_permission = "admissions.view_applicant"
    def get(self, request, *args, **kwargs):
        raw_phone = request.GET.get("phone", "").strip()
        if not raw_phone:
            return JsonResponse({"found": False})
        
        import re
        digits = re.sub(r"\D", "", raw_phone)
        
        from students.models import ParentGuardian, Student
        from django.db.models import Q
        
        # Try several match strategies:
        # 1. Exact match on raw string
        # 2. Match on digits-only (if the DB stores digits only)
        # 3. Suffix match on last 9 digits (most robust for local/intl mix)
        
        query = Q(phone=raw_phone)
        if digits:
            # Match digits anywhere (covers 741323901 matching +254 741 323 901)
            query |= Q(phone__icontains=digits)
            
            if len(digits) >= 9:
                suffix = digits[-9:]
                # Suffix match
                query |= Q(phone__endswith=suffix)
                # Fragment match
                s1, s2, s3 = suffix[:3], suffix[3:6], suffix[6:]
                query |= Q(phone__icontains=s1) & Q(phone__icontains=s2) & Q(phone__icontains=s3)
        
        guardian = ParentGuardian.objects.filter(query, is_archived=False).first()
        if not guardian:
            return JsonResponse({"found": False})
        
        siblings = Student.objects.filter(studentguardian__guardian=guardian, is_archived=False)
        sibling_data = [
            {
                "id": s.id,
                "full_name": f"{s.first_name} {s.last_name}",
                "class_name": s.class_name,
                "admission_no": s.admission_no
            }
            for s in siblings
        ]
        
        return JsonResponse({
            "found": True,
            "guardian": {
                "id": guardian.id,
                "full_name": guardian.full_name,
                "email": guardian.email,
                "preferred_invoice_name": guardian.preferred_invoice_name,
            },
            "siblings": sibling_data
        })

class InternalNoteCreateView(PermissionCacheMixin, AdmissionsRoleRequiredMixin, View):
    """FR-ADM-029: Add an internal note to an applicant record."""
    login_url = "/accounts/login/"
    required_permission = "admissions.view_applicantinternalnote"

    def post(self, request, *args, **kwargs):
        applicant = get_object_or_404(Applicant, pk=kwargs["pk"])
        body = _strip_html(request.POST.get("body", ""))
        if not body:
            messages.error(request, "Note cannot be empty.")
            return redirect("admissions:detail", pk=applicant.pk)

        from admissions.models import ApplicantInternalNote
        from audit.models import log_event

        note = ApplicantInternalNote.objects.create(
            applicant=applicant,
            author=request.user,
            body=body,
        )

        log_event(
            actor=request.user,
            action_type="INTERNAL_NOTE_ADDED",
            model_name="Applicant",
            object_id=applicant.pk,
            description=f"Internal note added to {applicant.child_full_name} by {request.user.username}",
            request=request,
        )

        messages.success(request, "Internal note added.")
        return redirect("admissions:detail", pk=applicant.pk)


class InternalNoteEditView(PermissionCacheMixin, AdmissionsRoleRequiredMixin, View):
    """FR-ADM-029: Users with edit_admission_note permission can edit internal notes with audit trail."""
    login_url = "/accounts/login/"
    required_permission = "admissions.edit_admission_note"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_perm("admissions.edit_admission_note"):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from admissions.models import ApplicantInternalNote
        note = get_object_or_404(ApplicantInternalNote, pk=kwargs["note_pk"])
        from django.shortcuts import render
        return render(request, "admissions/_internal_note_edit.html", {"note": note})

    def post(self, request, *args, **kwargs):
        from admissions.models import ApplicantInternalNote
        from audit.models import log_event

        note = get_object_or_404(ApplicantInternalNote, pk=kwargs["note_pk"])
        applicant = note.applicant
        body = _strip_html(request.POST.get("body", ""))
        if not body:
            messages.error(request, "Note cannot be empty.")
            return redirect("admissions:detail", pk=applicant.pk)

        old_body = note.body
        note.body = body
        note.save(update_fields=["body", "updated_at"])

        log_event(
            actor=request.user,
            action_type="INTERNAL_NOTE_EDITED",
            model_name="ApplicantInternalNote",
            object_id=note.pk,
            description=f"Internal note edited on {applicant.child_full_name} by {request.user.username}",
            before_value=old_body,
            after_value=body,
            request=request,
        )

        messages.success(request, "Internal note updated.")
        return redirect("admissions:detail", pk=applicant.pk)


class InternalNoteDeleteView(PermissionCacheMixin, AdmissionsRoleRequiredMixin, View):
    """FR-ADM-029: Users with delete_admission_note permission can delete internal notes."""
    login_url = "/accounts/login/"
    required_permission = "admissions.delete_admission_note"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_perm("admissions.delete_admission_note"):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from admissions.models import ApplicantInternalNote
        from audit.models import log_event

        note = get_object_or_404(ApplicantInternalNote, pk=kwargs["note_pk"])
        applicant = note.applicant
        note_body = note.body

        log_event(
            actor=request.user,
            action_type="INTERNAL_NOTE_DELETED",
            model_name="ApplicantInternalNote",
            object_id=note.pk,
            description=f"Internal note deleted on {applicant.child_full_name} by {request.user.username}",
            before_value=note_body,
            request=request,
        )

        note.delete()
        messages.success(request, "Internal note deleted.")
        return redirect("admissions:detail", pk=applicant.pk)


class ParentNameSearchView(PermissionCacheMixin, AdmissionsRoleRequiredMixin, View):
    """Search existing guardians by name for autocomplete on the inquiry form."""
    required_permission = "admissions.view_applicant"
    def get(self, request, *args, **kwargs):
        q = request.GET.get("q", "").strip()
        if len(q) < 2:
            return JsonResponse({"results": []})

        from students.models import ParentGuardian, Student

        guardians = ParentGuardian.objects.filter(
            full_name__icontains=q, is_archived=False
        ).order_by("full_name")[:10]

        results = []
        for g in guardians:
            sibs = Student.objects.filter(
                studentguardian__guardian=g, is_archived=False
            )[:5]
            results.append({
                "id": g.id,
                "full_name": g.full_name,
                "phone": g.phone or "",
                "email": g.email or "",
                "preferred_invoice_name": g.preferred_invoice_name or "",
                "siblings": [
                    {"full_name": f"{s.first_name} {s.last_name}", "class_name": s.class_name}
                    for s in sibs
                ],
            })

        return JsonResponse({"results": results})


class GradeCapacityCheckView(PermissionCacheMixin, AdmissionsRoleRequiredMixin, View):
    """AJAX: return current student count and max capacity for a grade."""
    required_permission = "admissions.view_applicant"

    def get(self, request, *args, **kwargs):
        grade = request.GET.get("grade", "").strip()
        if not grade:
            return JsonResponse({"error": "Missing grade parameter"}, status=400)

        from academics.models import GradeClass, get_class_capacity
        from students.models import Student

        gc = GradeClass.objects.filter(name__iexact=grade).first()
        if not gc:
            return JsonResponse({
                "grade": grade,
                "found": False,
                "message": f"No class named '{grade}' found in the system.",
            })

        count = Student.objects.filter(
            class_name__iexact=grade, is_archived=False
        ).count()
        cap = get_class_capacity(gc)
        remaining = max(cap - count, 0) if cap else None
        is_full = cap and count >= cap

        return JsonResponse({
            "grade": grade,
            "found": True,
            "current_count": count,
            "max_capacity": cap,
            "remaining": remaining,
            "is_full": is_full,
        })


class ApplicantPhotoUploadView(PermissionCacheMixin, AdmissionsRoleRequiredMixin, View):
    login_url = "/accounts/login/"
    required_permission = "admissions.upload_applicant_photo"

    # Maximum photo file size: 5 MB
    MAX_PHOTO_SIZE = 5 * 1024 * 1024
    ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

    def post(self, request, pk):
        if not request.user.has_perm("admissions.upload_applicant_photo"):
            raise PermissionDenied()
        applicant = get_object_or_404(Applicant, pk=pk)
        photo = request.FILES.get("photo")
        if not photo:
            messages.error(request, "No photo selected.")
            return redirect("admissions:detail", pk=pk)

        # Validate file size
        if photo.size > self.MAX_PHOTO_SIZE:
            messages.error(request, f"Photo file is too large. Maximum size is {self.MAX_PHOTO_SIZE // (1024 * 1024)} MB.")
            return redirect("admissions:detail", pk=pk)

        # Validate file content type
        if photo.content_type not in self.ALLOWED_PHOTO_TYPES:
            messages.error(request, "Invalid file type. Please upload a JPEG, PNG, WebP, or GIF image.")
            return redirect("admissions:detail", pk=pk)

        applicant.photo = photo
        applicant.save(update_fields=["photo", "updated_at"])
        messages.success(request, "Photo uploaded successfully.")
        return redirect("admissions:detail", pk=pk)


class AssessmentReportView(AdmissionsRoleRequiredMixin, View):
    """Teacher-facing admission assessment report form (matches PDF format)."""
    login_url = "/accounts/login/"
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD, UserRole.TEACHER, UserRole.ADMIN_OFFICER,
    ]

    def _get_assessment(self, pk):
        return get_object_or_404(
            AssessmentSchedule.objects.select_related(
                'applicant', 'applicant__parent_user',
            ),
            applicant_id=pk,
        )

    def get(self, request, pk):
        assessment = self._get_assessment(pk)
        applicant = assessment.applicant
        settings = SchoolSettings.objects.first()
        school_name = settings.school_name if settings else ""

        # Subject list for assessment
        subjects = [
            {"name": "English Language", "key": "english"},
            {"name": "Mathematics", "key": "mathematics"},
            {"name": "Science", "key": "science"},
            {"name": "Social Studies", "key": "social_studies"},
            {"name": "Religious Education", "key": "religious_ed"},
            {"name": "Physical Education", "key": "pe"},
            {"name": "Creative Arts", "key": "creative_arts"},
            {"name": "General Knowledge", "key": "general_knowledge"},
        ]

        ctx = {
            "applicant": applicant,
            "assessment": assessment,
            "school_name": school_name,
            "subjects": subjects,
        }
        return render(request, "admissions/assessment_report.html", ctx)

    def post(self, request, pk):
        assessment = self._get_assessment(pk)
        applicant = assessment.applicant

        # Save teacher comments per subject (stored as JSON)
        subject_results = {}
        remarks = {}
        for key in ["english", "mathematics", "science", "social_studies",
                     "religious_ed", "pe", "creative_arts", "general_knowledge"]:
            mark = request.POST.get(f"mark_{key}", "").strip()
            grade = request.POST.get(f"grade_{key}", "").strip()
            remark = request.POST.get(f"remark_{key}", "").strip()
            subject_results[key] = {"mark": mark, "grade": grade}
            remarks[key] = remark

        teacher_comments = _strip_html(request.POST.get("teacher_comments", ""))
        overall_remark = _strip_html(request.POST.get("overall_remark", ""))

        # Save to assessment model
        import json
        assessment.teacher_comments = teacher_comments
        # Store subject results in a structured way in hod_comments (temporary)
        # Actually, store in a separate JSON-like structure via the comments
        assessment.hod_comments = json.dumps({
            "subject_results": subject_results,
            "remarks": remarks,
            "overall_remark": overall_remark,
        })
        assessment.save(update_fields=["teacher_comments", "hod_comments", "updated_at"])

        messages.success(request, f"Assessment report saved for {applicant.child_full_name}.")
        return redirect("admissions:detail", pk=pk)


class ParentInquiryView(TemplateView):
    """Public admission inquiry form for parents. No login required."""
    template_name = "admissions/parent_inquiry.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from core.models import SchoolSettings
        from academics.models import AcademicYear
        settings_obj = SchoolSettings.get_settings()
        ctx["school_name"] = settings_obj.school_name or "Hodari Christian School"
        current_ay = AcademicYear.objects.filter(is_current=True).first()
        if current_ay:
            ctx["academic_year"] = current_ay.name
        else:
            ctx["academic_year"] = "2026\u20132027"
        return ctx

    def post(self, request, *args, **kwargs):
        import json
        from django.http import JsonResponse
        from django.utils import timezone
        from admissions.models import Applicant, ApplicantStatus, InquiryChannel

        action = request.POST.get("action", "")
        if action != "parent_submit":
            return JsonResponse({"ok": False, "error": "Invalid action."}, status=400)

        raw = request.POST.get("data", "")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return JsonResponse({"ok": False, "error": "Invalid form data."}, status=400)

        child = data.get("child", {})
        parent = data.get("parent", {})
        appt = data.get("appointment", {})

        child_name = (child.get("name") or "").strip()
        parent_name = (parent.get("name") or "").strip()
        parent_phone = (parent.get("phone") or "").strip()

        if not child_name:
            return JsonResponse({"ok": False, "error": "Child name is required."}, status=400)
        if not parent_name:
            return JsonResponse({"ok": False, "error": "Parent name is required."}, status=400)
        if not parent_phone:
            return JsonResponse({"ok": False, "error": "Phone number is required."}, status=400)

        dob = None
        dob_str = (child.get("dob") or "").strip()
        if dob_str:
            try:
                dob = timezone.datetime.strptime(dob_str, "%Y-%m-%d").date()
            except ValueError:
                try:
                    dob = timezone.datetime.strptime(dob_str, "%m/%d/%Y").date()
                except ValueError:
                    pass

        preferred_date = None
        date_str = (appt.get("date") or "").strip()
        if date_str:
            try:
                preferred_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        preferred_time = None
        time_str = (appt.get("time") or "").strip()
        if time_str:
            try:
                preferred_time = timezone.datetime.strptime(time_str, "%H:%M").time()
            except ValueError:
                pass

        grade = (child.get("grade") or "").strip()
        previous_school = (child.get("school") or "").strip()
        gender = (child.get("gender") or "").strip()
        current_grade = (child.get("currentGrade") or "").strip()
        parent_email = (parent.get("email") or "").strip()
        relationship = (parent.get("relationship") or "").strip()
        notes_text = (appt.get("notes") or "").strip()

        target_year = None
        target_month = None
        yr_str = str(data.get("enrollmentYear") or "").strip()
        mo_str = str(data.get("enrollmentMonth") or "").strip()
        month_map = {"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,"july":7,"august":8,"september":9,"october":10,"november":11,"december":12}
        if yr_str.isdigit():
            target_year = int(yr_str)
        if mo_str.isdigit():
            target_month = int(mo_str)
        elif mo_str.lower() in month_map:
            target_month = month_map[mo_str.lower()]

        inquiry_notes = {
            "submitted_via": "public_inquiry",
            "gender": gender,
            "current_school": previous_school,
            "current_grade": current_grade,
        }
        if notes_text:
            inquiry_notes["inquiry_notes"] = notes_text

        applicant = Applicant.objects.create(
            parent_full_name=parent_name,
            parent_phone=parent_phone,
            parent_email=parent_email,
            parent_relationship=relationship,
            child_full_name=child_name,
            child_date_of_birth=dob,
            grade_applying_for=grade,
            previous_school=previous_school,
            preferred_meeting_date=preferred_date,
            preferred_meeting_time=preferred_time,
            target_enrollment_year=target_year,
            target_enrollment_month=target_month,
            inquiry_channel=InquiryChannel.WEBSITE,
            status=ApplicantStatus.INQUIRY_RECEIVED,
            notes=json.dumps(inquiry_notes),
        )

        # --- Background notifications (same as staff-created inquiry) ---
        import threading

        _applicant_pk = applicant.pk
        _ref_number = applicant.reference_number
        _parent_email = parent_email
        _parent_phone_val = parent_phone
        _child_name = child_name
        _grade = grade
        _parent_name = parent_name
        _preferred_date = preferred_date
        _preferred_time = preferred_time

        def _bg_notifications():
            try:
                # E03: Acknowledgement email to parent
                if _parent_email:
                    from communications.email_service import send_email_safe
                    from core.models import SchoolSettings
                    _ss = SchoolSettings.get_settings()
                    contact = _ss.get_admissions_contact()
                    _school_name = _ss.school_name or "Hodari Christian School"

                    tpl_context = {
                        "parent_name": _parent_name,
                        "child_name": _child_name,
                        "grade": _grade,
                        "ref_number": _ref_number,
                        "school_name": _school_name,
                        "contact_phone": contact['phone'],
                        "proposed_meeting_date": _preferred_date or "To be confirmed",
                        "proposed_meeting_time": _preferred_time or "To be confirmed",
                        "meeting_date": _preferred_date or "To be confirmed",
                        "meeting_time": _preferred_time or "To be confirmed",
                        "response_window": "48 hours",
                        "admissions_phone": contact.get('phone', ''),
                    }

                    from core.email_templates import send_dynamic_email
                    db_sent = send_dynamic_email(
                        template_type="admission_inquiry",
                        to_email=_parent_email,
                        context=tpl_context,
                    )

                    if not db_sent:
                        ack_body = (
                            f"Dear {_parent_name},\n\n"
                            f"Thank you for thinking of {_school_name} for "
                            f"{_child_name}. Your inquiry has reached us safely, and "
                            f"we're glad you got in touch.\n\n"
                            "Here's what we have on record:\n\n"
                            f"Learner: {_child_name}\n"
                            f"Grade enquired for: {_grade}\n"
                            f"Your reference number: {_ref_number}\n\n"
                            "Our Head of School reviews each inquiry personally and "
                            "will come back to you within 48 hours with a meeting "
                            "date. You'll get an email from us either way.\n\n"
                            "Do keep your reference number somewhere handy. Quoting "
                            "it whenever you call or write helps us find your file "
                            "straight away.\n\n"
                            f"If anything above looks wrong, or you'd simply like to "
                            f"talk to someone before the meeting, call us on "
                            f"{contact['phone']}. We're always happy to answer "
                            f"questions.\n\n"
                            f"We look forward to meeting you and {_child_name}.\n\n"
                            "Warm regards,\n"
                            "Admissions Office\n"
                            f"{_school_name}"
                        )
                        send_email_safe(
                            to_email=_parent_email,
                            subject=f"We've received your inquiry for {_child_name} \u2014 {_ref_number}",
                            body=ack_body,
                        )
            except Exception:
                pass

            try:
                # S01: Inquiry acknowledgement SMS to parent
                from communications.email_service import dispatch_notification
                if _parent_phone_val:
                    dispatch_notification(
                        user=None,
                        title="Inquiry Received",
                        message=f"Dear {_parent_name}, thank you for your interest in Hodari Christian School for {_child_name}. Your reference number is {_ref_number}. Our Head of School will contact you shortly to confirm a meeting date.",
                        link=None,
                        actor=None,
                        external_email=None,
                        phone=_parent_phone_val,
                    )
            except Exception:
                pass

            try:
                # NOTIF-01: Notify admin officers (in-app)
                from communications.email_service import dispatch_notification, send_email_safe
                from users.models import User, UserRole
                admins = User.objects.filter(role=UserRole.ADMIN_OFFICER, is_active=True)
                for admin in admins:
                    dispatch_notification(
                        user=admin,
                        title="New Inquiry Received",
                        message=f"New inquiry received for {_child_name} ({_grade}).",
                        link=f"/admissions/applicant/{_applicant_pk}/",
                        actor=None,
                    )
                # E01: Email to admissions group + Head of School
                from core.models import SchoolSettings as _SS
                _ss2 = _SS.get_settings()
                admissions_email = _ss2.admissions_email or "admissions@hodari.ac.tz"
                e01_context = {
                    "reference_number": _ref_number,
                    "child_name": _child_name,
                    "grade": _grade,
                    "parent_name": _parent_name,
                    "parent_phone": _parent_phone_val,
                    "parent_email": _parent_email,
                    "proposed_meeting_date": _preferred_date or "To be confirmed",
                    "proposed_meeting_time": _preferred_time or "To be confirmed",
                    "application_link": f"/admissions/applicant/{_applicant_pk}/",
                    "school_name": _ss2.school_name or "Hodari Christian School",
                }
                from core.email_templates import send_dynamic_email
                db_sent2 = send_dynamic_email(
                    template_type="admission_new_inquiry",
                    to_email=admissions_email,
                    context=e01_context,
                )
                if not db_sent2:
                    e01_body = (
                        f"A new admission inquiry has been submitted.\n\n"
                        f"Reference: {_ref_number}\n"
                        f"Learner: {_child_name}\n"
                        f"Grade enquired for: {_grade}\n"
                        f"Parent or guardian: {_parent_name}\n"
                        f"Phone: {_parent_phone_val}\n"
                        f"Email: {_parent_email}\n\n"
                        f"Meeting date requested by the parent: "
                        f"{e01_context['proposed_meeting_date']} at {e01_context['proposed_meeting_time']}\n\n"
                        f"Confirm this date or set a different one: /admissions/applicant/{_applicant_pk}/"
                    )
                    send_email_safe(
                        to_email=admissions_email,
                        subject=f"New inquiry: {_child_name}, Grade {_grade} \u2014 {_ref_number}",
                        body=e01_body,
                    )
                # Also send to Head of School directly
                hos_users = User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True)
                for hos in hos_users:
                    if hos.email and hos.email != admissions_email:
                        send_dynamic_email(
                            template_type="admission_new_inquiry",
                            to_email=hos.email,
                            context=e01_context,
                        ) if db_sent2 else send_email_safe(
                            to_email=hos.email,
                            subject=f"New inquiry: {_child_name}, Grade {_grade} \u2014 {_ref_number}",
                            body=e01_body,
                        )
            except Exception:
                pass

        threading.Thread(target=_bg_notifications, daemon=True).start()

        return JsonResponse({
            "ok": True,
            "ref": applicant.reference_number,
        })


class AdminWalkInFormView(AdmissionsRoleRequiredMixin, View):
    """Admin fills the admission form on behalf of a walk-in parent.

    GET: returns HTMX partial with the form fields.
    POST: saves form data, transitions ADMITTED/CONDITIONAL -> FORM_SUBMITTED.
    """
    required_permission = "admissions.change_applicant"

    def _get_applicant(self, pk):
        from admissions.models import Applicant, ApplicantStatus
        applicant = get_object_or_404(Applicant, pk=pk)
        if applicant.status not in (ApplicantStatus.ADMITTED, ApplicantStatus.CONDITIONAL):
            return None
        return applicant

    def get(self, request, pk):
        applicant = self._get_applicant(pk)
        if not applicant:
            return render(request, "admissions/_walkin_form.html", {"error": "Applicant is not at the admission form stage."})
        return render(request, "admissions/_walkin_form.html", {"applicant": applicant})

    def post(self, request, pk):
        from django.utils import timezone
        from admissions.models import ApplicantStatus
        from admissions.services import transition_applicant_status

        applicant = self._get_applicant(pk)
        if not applicant:
            return render(request, "admissions/_walkin_form.html", {"error": "Applicant is not at the admission form stage."})

        uniform_size = _strip_html(request.POST.get("uniform_size", ""))
        uniform_measurements = _strip_html(request.POST.get("uniform_measurements", ""))
        meal_choice = request.POST.get("meal_choice", "pack")
        transport_needed = request.POST.get("transport_needed", "no")
        pdpa_consent = request.POST.get("pdpa_consent") == "on"
        code_of_conduct = request.POST.get("code_of_conduct") == "on"

        if not pdpa_consent or not code_of_conduct:
            return render(request, "admissions/_walkin_form.html", {
                "applicant": applicant,
                "error": "Both consent checkboxes are required.",
            })

        applicant.notes = (
            f"ADMISSION FORM FILLED BY STAFF (WALK-IN)\n"
            f"Uniform size: {uniform_size}\n"
            f"Uniform measurements: {uniform_measurements}\n"
            f"Meal choice: {meal_choice}\n"
            f"Transport needed: {transport_needed}\n"
            f"PDPA consent: Yes\n"
            f"Code of conduct accepted: Yes\n"
            f"Filled by: {request.user.get_full_name() or request.user.username}\n"
            f"Submitted: {timezone.now().strftime('%d %B %Y %H:%M')}"
        )
        applicant.save(update_fields=["notes", "updated_at"])

        try:
            transition_applicant_status(
                applicant=applicant,
                to_status=ApplicantStatus.FORM_SUBMITTED,
                actor=request.user,
                reason=f"Staff filled admission form on behalf of parent (walk-in).",
            )
            return render(request, "admissions/_walkin_form.html", {
                "applicant": applicant,
                "success": True,
            })
        except Exception as e:
            return render(request, "admissions/_walkin_form.html", {
                "applicant": applicant,
                "error": f"Error: {e}",
            })


class AdminGenerateInvoiceView(AdmissionsRoleRequiredMixin, View):
    """Admin generates the admission invoice at the desk (walk-in flow).

    Creates ADM- invoice and transitions FORM_SUBMITTED -> INVOICE_GENERATED.
    """
    required_permission = "admissions.change_applicant"

    def post(self, request, pk):
        from django.utils import timezone
        from admissions.models import Applicant, ApplicantStatus
        from finance.models import Invoice, InvoiceLineItem, InvoiceStatus, FinancePeriod
        from admissions.services import transition_applicant_status, ensure_default_documents
        from communications.email_service import dispatch_notification
        from users.models import User, UserRole
        from core.models import SchoolSettings

        applicant = get_object_or_404(Applicant, pk=pk)
        _allowed = (ApplicantStatus.FORM_SUBMITTED, ApplicantStatus.ADMITTED, ApplicantStatus.CONDITIONAL)
        if applicant.status not in _allowed:
            messages.error(request, "Applicant is not at the form_submitted stage.")
            return redirect("admissions:detail", pk=pk)

        existing = Invoice.objects.filter(applicant=applicant, invoice_number__startswith="ADM-").first()
        if existing:
            messages.info(request, f"Invoice {existing.invoice_number} already exists.")
            return redirect("admissions:detail", pk=pk)

        settings = SchoolSettings.get_settings()
        admission_fee = settings.admission_fee or 700000
        development_fee = 700000
        checkpoint_fee = 300000
        stem_fee = 180000
        breakfast_fee = 300000
        uniform_prices = {"polo": 20000, "sweater": 25000, "tee": 15000}
        uniform_labels = {"polo": "Polo T-shirt (white / blue / yellow)", "sweater": "Hodari sweater", "tee": "Sports team T-shirt (red / blue / green)"}
        ECD_GRADES = ["Pre-KG", "Kindergarten", "Preschool", "ABC"]
        UPPER_GRADES = ["Grade 7", "Grade 8", "Grade 9"]

        def tuition_for(g):
            if g in ECD_GRADES:
                return 3200000
            if g in UPPER_GRADES:
                return 3800000
            return 3500000

        children_data = []
        try:
            notes = json.loads(applicant.notes or "{}")
            children_data = notes.get("children", [])
        except (json.JSONDecodeError, ValueError):
            pass

        if not children_data:
            children_data = [{"name": applicant.child_full_name, "grade": applicant.grade_applying_for, "isNew": True, "breakfast": False, "stem": False, "uniform": {}}]

        line_items = []
        for ch in children_data:
            ch_name = ch.get("name") or applicant.child_full_name
            ch_grade = ch.get("grade") or applicant.grade_applying_for
            is_new = ch.get("isNew", True)
            has_stem = ch.get("stem", False)
            has_breakfast = ch.get("breakfast", False)
            uniforms = ch.get("uniform", {})

            line_items.append({"description": f"Tuition — Term 1 ({ch_name} · {ch_grade})", "amount": tuition_for(ch_grade)})
            line_items.append({"description": f"Development fee (annual) — {ch_name}", "amount": development_fee})
            if is_new:
                line_items.append({"description": f"Admission fee (one-time) — {ch_name}", "amount": admission_fee})
            if ch_grade == "Grade 6":
                line_items.append({"description": f"Cambridge Checkpoint — {ch_name}", "amount": checkpoint_fee})
            if has_stem:
                line_items.append({"description": f"STEM — Term 1 ({ch_name})", "amount": stem_fee})
            if has_breakfast:
                line_items.append({"description": f"Breakfast — Term 1 ({ch_name})", "amount": breakfast_fee})
            for uk, price in uniform_prices.items():
                qty = int(uniforms.get(uk) or 0)
                if qty > 0:
                    line_items.append({"description": f"{uniform_labels[uk]} × {qty} ({ch_name})", "amount": price * qty})

        total_due = sum(li["amount"] for li in line_items)
        if total_due == 0:
            total_due = admission_fee
            line_items = [{"description": f"Admission Fee for {applicant.child_full_name}", "amount": admission_fee}]

        period = FinancePeriod.objects.filter(is_reconciled=False).first()
        inv_no = f"ADM-{applicant.id:04d}-{timezone.now().strftime('%y%m%d')}"

        invoice = Invoice.objects.create(
            applicant=applicant,
            amount_due=total_due,
            total_due=total_due,
            due_date=timezone.now().date() + timezone.timedelta(days=14),
            status=InvoiceStatus.UNPAID,
            period=period,
            invoice_number=inv_no,
        )
        for li in line_items:
            InvoiceLineItem.objects.create(
                invoice=invoice,
                description=li["description"],
                amount=li["amount"],
            )

        try:
            if applicant.status in (ApplicantStatus.ADMITTED, ApplicantStatus.CONDITIONAL):
                transition_applicant_status(
                    applicant=applicant,
                    to_status=ApplicantStatus.FORM_SUBMITTED,
                    actor=request.user,
                    reason="Parent submitted admission form online.",
                )
            transition_applicant_status(
                applicant=applicant,
                to_status=ApplicantStatus.INVOICE_GENERATED,
                actor=request.user,
                reason=f"Invoice {inv_no} generated by staff (walk-in).",
            )
        except Exception:
            pass

        ensure_default_documents(applicant)
        applicant.documents.filter(document_type__in=[
            "birth_certificate", "clearance_form", "admission_form", "fee_arrangement_proof"
        ]).update(is_received=True, received_by=request.user)

        fo_users = User.objects.filter(role=UserRole.FINANCE_OFFICER, is_active=True)
        for fo in fo_users:
            dispatch_notification(
                user=fo, title="Admission Invoice Generated",
                message=f"Invoice {inv_no} (TZS {total_due:,.0f}) generated for {applicant.child_full_name}.",
                link=f"/admissions/applicant/{applicant.pk}/", actor=None,
            )

        # E07: Admission invoice email to parent (DB template)
        parent_email = (applicant.parent_email or "").strip() or None
        if parent_email:
            from core.email_templates import send_dynamic_email
            contact = settings.get_admissions_contact()
            e07_context = {
                "parent_name": applicant.parent_full_name or "Parent/Guardian",
                "child_name": applicant.child_full_name,
                "ref": applicant.reference_number,
                "reference_number": applicant.reference_number,
                "invoice_number": inv_no,
                "currency": "TZS",
                "amount_due": f"{total_due:,.0f}",
                "due_date": invoice.due_date.strftime("%d %B %Y") if invoice.due_date else "",
                "admissions_email": settings.admissions_email or "admissions@hodari.ac.tz",
                "admissions_whatsapp": contact.get("whatsapp") or contact.get("phone", ""),
                "contact_phone": contact.get("phone", ""),
                "school_name": settings.school_name or "Hodari Christian School",
            }
            e07_sent = send_dynamic_email(
                template_type="admission_fee_invoice",
                to_email=parent_email,
                context=e07_context,
            )
            if not e07_sent:
                dispatch_notification(
                    user=None, title="Admission Fee Invoice Generated",
                    message=(
                        f"Dear {applicant.parent_full_name},\n\n"
                        f"An admission fee invoice has been generated for {applicant.child_full_name}.\n\n"
                        f"Invoice Number: {inv_no}\n"
                        f"Amount Due: TZS {admission_fee:,.0f}\n"
                        f"Due Date: {invoice.due_date.strftime('%d %B %Y') if invoice.due_date else ''}\n\n"
                        f"Payment can be made via Bank Transfer (DTB 0225556001 or CRDB 0150829302900) "
                        f"or Mobile Money ({contact.get('phone', '')}). Please send proof of payment "
                        f"to {settings.admissions_email or 'admissions@hodari.ac.tz'}.\n\n"
                        f"Finance Office\n{settings.school_name}"
                    ),
                    link=f"/admissions/applicant/{applicant.pk}/",
                    actor=None, external_email=parent_email,
                )

        messages.success(
            request,
            f"Invoice {inv_no} generated (TZS {admission_fee:,.0f}). Due by {invoice.due_date.strftime('%d %B %Y')}."
        )
        return redirect("admissions:detail", pk=pk)

    def _create_inquiry(self, request, data):
        import json, threading
        from datetime import datetime
        from core.models import SchoolSettings
        settings_obj = SchoolSettings.get_settings()
        school_name = settings_obj.school_name or "Hodari Christian School"

        children = data.get("children", [])
        guardians = data.get("guardians", [])
        emergency = data.get("emergency", [])

        if not guardians or not guardians[0].get("name"):
            return JsonResponse({"ok": False, "error": "Parent name is required."}, status=400)
        if not guardians[0].get("phone"):
            return JsonResponse({"ok": False, "error": "Phone number is required."}, status=400)
        if not children or not children[0].get("name"):
            return JsonResponse({"ok": False, "error": "Student name is required."}, status=400)

        g = guardians[0]

        shared_notes = json.dumps({
            "submitted_via": "parent_form",
            "guardian": g,
            "children": children,
            "emergency": emergency,
            "siblings": data.get("siblings", ""),
            "heardFrom": data.get("heardFrom", ""),
            "pledge": data.get("pledge", ""),
            "expelled": data.get("expelled", ""),
            "why": data.get("why", ""),
            "summary": data.get("summary", ""),
            "consent": data.get("consent", {}),
            "signature": data.get("signature", ""),
            "signDate": data.get("signDate", ""),
        })

        created_applicants = []
        for ci, c in enumerate(children):
            applicant = Applicant(
                parent_full_name=g.get("name", ""),
                parent_phone=g.get("phone", ""),
                parent_email=g.get("email", ""),
                parent_relationship=g.get("rel", ""),
                parent_invoice_name=g.get("name", ""),
                child_full_name=c.get("name", ""),
                grade_applying_for=c.get("grade", ""),
                previous_school=c.get("prevSchool", ""),
                inquiry_channel="website",
                notes=shared_notes,
            )
            if c.get("dob"):
                try:
                    applicant.child_date_of_birth = datetime.strptime(c["dob"], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    applicant.child_date_of_birth = datetime(2020, 1, 1).date()
            else:
                applicant.child_date_of_birth = datetime(2020, 1, 1).date()

            applicant.save()
            created_applicants.append(applicant)

            # Audit log
            try:
                from audit.models import log_event
                log_event(
                    actor=None,
                    action_type="INQUIRY_CREATED",
                    model_name="Applicant",
                    object_id=applicant.pk,
                    description=f"Parent inquiry submitted for {applicant.child_full_name} ({applicant.grade_applying_for}) via website",
                    request=request,
                )
            except Exception:
                pass

        # Send one acknowledgement email listing all children
        parent_email = (g.get("email", "") or "").strip()
        if parent_email:
            child_names = ", ".join(c.get("name", f"Child {i+1}") for i, c in enumerate(children))
            first_ref = created_applicants[0].reference_number if created_applicants else "N/A"
            def _send_ack():
                try:
                    from communications.email_service import send_email_safe
                    contact = settings_obj.get_admissions_contact()
                    body = (
                        f"Dear {g.get('name', '')},\n\n"
                        f"Thank you for your interest in {school_name}.\n\n"
                        f"We have received your admission inquiry for: {child_names}.\n\n"
                        f"Your reference number is: {first_ref}\n\n"
                        f"Our admissions team will contact you shortly.\n\n"
                        f"Best regards,\n{school_name} Admissions\n"
                        f"Phone: {contact['phone']}"
                    )
                    send_email_safe(
                        subject=f"Admission Inquiry Received \u2014 {school_name}",
                        body=body,
                        to_email=parent_email,
                    )
                except Exception:
                    pass
            threading.Thread(target=_send_ack, daemon=True).start()

        refs = [a.reference_number for a in created_applicants]
        return JsonResponse({"ok": True, "ref": refs[0] if refs else "N/A", "refs": refs})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from core.models import SchoolSettings
        settings_obj = SchoolSettings.get_settings()
        ctx["school_name"] = settings_obj.school_name or "Hodari Christian School"
        ctx["academic_year"] = "2026\u20132027"
        return ctx
