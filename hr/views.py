import json
from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View, TemplateView, FormView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum, Q, Count
from django.utils import timezone
from django.urls import reverse
from django.http import JsonResponse, HttpResponse
from core.permissions import RoleRequiredMixin
from users.models import UserRole
from audit.models import log_event
from academics.models import Department, GradeClass, Subject, Term
from django import forms
from .models import (
    StaffProfile, StaffDocument, TeacherClassAssignment,
    StaffOnboardingProgress, OnboardingChecklistItem,
    InductionChecklistCompletion,
    PayrollRun, PayrollEntry, PayslipStatus, PayrollStatus,
    StatutoryFiling, StatutoryFilingStatus,
    LeaveRequest, LeaveAllocation, LeaveStatus, LeaveType,
    OffboardingRecord, OffboardingStatus,
)
from .forms import (
    TeacherAssignmentForm, TeacherAssignmentEditForm, StaffDepartureForm,
    OnboardingStepForm, LeaveRequestForm, LeaveApproveForm,
    PayrollEntryForm, PayrollRunForm, OffboardingForm,
)


# 
# STAFF LIST / DETAIL / EDIT
# 

class StaffListView(RoleRequiredMixin, View):
    """Redirect to the unified user/staff management list."""
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "hr.view_staffprofile"

    def get(self, request, *args, **kwargs):
        from django.shortcuts import redirect as _redirect
        return _redirect("users:user_list")


class StaffEditView(RoleRequiredMixin, View):
    """Redirect to the unified user edit form."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL]
    required_permission = "hr.change_staffprofile"

    def get(self, request, pk, *args, **kwargs):
        from django.shortcuts import redirect as _redirect
        staff = get_object_or_404(StaffProfile, pk=pk)
        return _redirect("users:user_edit", pk=staff.user.pk)


class StaffDetailView(RoleRequiredMixin, DetailView):
    model = StaffProfile
    template_name = "hr/staff_detail.html"
    context_object_name = "staff"
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD,
        UserRole.ADMIN_OFFICER, UserRole.FINANCE_OFFICER, UserRole.TEACHER]
    required_permission = "hr.view_staffprofile"

    def _hod_department(self, user):
        if user.role == UserRole.PRIMARY_HOD:
            return "PRIMARY"
        if user.role == UserRole.ECD_HOD:
            return "ECD"
        if user.role == UserRole.LOWER_SECONDARY_HOD:
            return "LOWER_SECONDARY"
        return None

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role == UserRole.TEACHER:
            pk = kwargs.get("pk")
            own_profile = StaffProfile.objects.filter(user=request.user, pk=pk).exists()
            if own_profile:
                return super(RoleRequiredMixin, self).dispatch(request, *args, **kwargs)
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        user = self.request.user
        if user.role == UserRole.TEACHER and obj.user_id != user.pk:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You can only view your own staff profile.")
        dept = self._hod_department(user)
        if dept and obj.department != dept:
            from audit.models import log_event
            log_event(
                actor=self.request.user,
                action_type="ROLE_ACCESS_DENIED",
                model_name="StaffProfile",
                object_id=obj.pk,
                description=(
                    f"HOD {self.request.user} denied access to staff profile {obj.pk} "
                    f"(department={obj.department}, expected={dept})"
                ),
                request=self.request,
            )
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You can only view staff in your own department.")
        return obj

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        staff = self.object
        request_user = self.request.user
        is_admin = request_user.has_perm("hr.change_staffprofile")

        ctx["is_admin_viewing"] = is_admin
        ctx["is_own_profile"] = staff.user.pk == request_user.pk
        ctx["departure_form"] = StaffDepartureForm()
        ctx["assignments"] = TeacherClassAssignment.objects.filter(teacher=staff).select_related("term", "grade_class")
        ctx["documents"] = StaffDocument.objects.filter(staff=staff)

        # Subject colors + class data for assignment grid
        import json
        from academics.models import Subject, GradeClass, Term
        subject_colors = {s.name: s.color for s in Subject.objects.filter(is_active=True)}
        ctx["subject_colors_json"] = json.dumps(subject_colors)
        class_subjects = {}
        for gc in GradeClass.objects.all():
            class_subjects[str(gc.id)] = sorted(
                gc.subjects.filter(is_active=True).values_list("name", flat=True)
            )
        ctx["class_subjects_json"] = json.dumps(class_subjects)
        # Build per-term assignment data for the grid
        from academics.utils import get_current_term
        current_term = get_current_term()
        ctx["current_term_id"] = str(current_term.id) if current_term else ""
        assignment_grid = {}
        for ass in ctx["assignments"]:
            term_id = str(ass.term_id)
            if term_id not in assignment_grid:
                assignment_grid[term_id] = {
                    "term_name": ass.term.name,
                    "year_name": ass.term.academic_year.name,
                    "classes": {}
                }
            class_id = str(ass.grade_class_id)
            if class_id in assignment_grid[term_id]["classes"]:
                existing = assignment_grid[term_id]["classes"][class_id]
                subjects = set(existing["subjects"]) | set(ass.subjects_taught or [])
                assignment_grid[term_id]["classes"][class_id] = {
                    "name": ass.grade_class.name,
                    "subjects": sorted(subjects),
                    "is_ct": existing["is_ct"] or ass.is_class_teacher,
                }
            else:
                assignment_grid[term_id]["classes"][class_id] = {
                    "name": ass.grade_class.name,
                    "subjects": ass.subjects_taught or [],
                    "is_ct": ass.is_class_teacher,
                }
        ctx["assignment_grid_json"] = json.dumps(assignment_grid)

        # Leave info
        ctx["leave_requests"] = LeaveRequest.objects.filter(staff=staff).order_by("-start_date")[:5]
        allocations = LeaveAllocation.objects.filter(staff=staff, year=timezone.now().year)
        ctx["leave_allocations"] = allocations

        # Payroll info
        ctx["payroll_entries"] = PayrollEntry.objects.filter(staff=staff).select_related("payroll_run").order_by("-payroll_run__period_start")[:6]

        # Onboarding progress
        try:
            ctx["onboarding"] = StaffOnboardingProgress.objects.get(staff=staff)
            display_step = _display_step_from_staff(staff)
            ctx["onboarding_display_step"] = display_step
            ctx["onboarding_pct"] = (
                100 if staff.onboarding_completed
                else int(max(0, display_step - 1) / ONBOARDING_TOTAL_STEPS * 100)
            )
        except StaffOnboardingProgress.DoesNotExist:
            ctx["onboarding"] = None
            ctx["onboarding_pct"] = 0

        # Offboarding
        try:
            ctx["offboarding"] = OffboardingRecord.objects.get(staff=staff)
        except OffboardingRecord.DoesNotExist:
            ctx["offboarding"] = None

        # Attendance History
        from attendance.models import StaffAttendanceEntry, AttendanceStatus
        from datetime import date, timedelta
        import calendar

        today = date.today()
        year, month = today.year, today.month
        month_days = list(calendar.Calendar().itermonthdates(year, month))

        entries = StaffAttendanceEntry.objects.filter(
            staff=staff, date__month=month, date__year=year
        )
        att_map = {e.date: e.status for e in entries}

        cal_data = []
        for d in month_days:
            status = att_map.get(d)
            color = "none"
            if status == AttendanceStatus.PRESENT: color = "green"
            elif status == AttendanceStatus.ABSENT: color = "red"
            elif status == AttendanceStatus.LATE: color = "yellow"
            elif status == AttendanceStatus.EXCUSED: color = "blue"
            cal_data.append({"date": d, "is_current_month": d.month == month, "color": color, "status": status or "N/A"})

        ctx["calendar_days"] = cal_data
        ctx["current_month_name"] = calendar.month_name[month]
        return ctx



class StaffCreateView(RoleRequiredMixin, View):
    """Redirect to the unified user creation form."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL]
    required_permission = "hr.view_staffprofile"

    def get(self, request, *args, **kwargs):
        messages.info(request, "Create a new staff member by setting up their account and employment details in one step.")
        return redirect("users:user_create")


# 
# TEACHER ASSIGNMENT
# 

#
# B7 — STAFF DEPARTURE / OFFBOARDING
#

class StaffDepartureView(RoleRequiredMixin, View):
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "hr.change_staffprofile"

    def post(self, request, pk):
        staff = get_object_or_404(StaffProfile.objects.select_related("user"), pk=pk)
        form = StaffDepartureForm(request.POST)
        if form.is_valid():
            departure_date = form.cleaned_data["departure_date"]
            reason = form.cleaned_data.get("reason", "")

            # FR-STF-003: Departed staff accounts are DEACTIVATED but their
            # historical data is retained. Historical records (lesson plans,
            # attendance, grades) are attribution history and must NOT block
            # deactivation. Open obligations are surfaced as warnings, not
            # hard blocks, so a departed staff member is always deactivated.
            warnings = []

            # 1. Current-term class assignments (informational warning)
            from academics.utils import get_current_term
            from hr.models import TeacherClassAssignment
            current_term = get_current_term()
            if current_term:
                active_assignments = TeacherClassAssignment.objects.filter(
                    teacher=staff, term=current_term,
                ).count()
                if active_assignments:
                    warnings.append(f"{active_assignments} class assignment(s) in the current term")

            # 2. Open (DRAFT / PENDING_APPROVAL) payroll entries (informational)
            from hr.models import PayrollEntry, PayrollRun, PayrollStatus
            open_payroll = PayrollEntry.objects.filter(
                staff=staff,
                payroll_run__status__in=[PayrollStatus.DRAFT, PayrollStatus.PENDING_APPROVAL],
            ).count()
            if open_payroll:
                warnings.append(f"{open_payroll} open payroll entry/entries (DRAFT/PENDING_APPROVAL)")

            # 3. Active (non-completed) lesson plans (informational)
            from academics.models import LessonPlan
            active_lessons = LessonPlan.objects.filter(
                teacher=staff.user,
                status__in=["DRAFT", "PLANNED"],
            ).count()
            if active_lessons:
                warnings.append(f"{active_lessons} active lesson plan(s)")

            staff.departure_date = departure_date
            staff.departure_reason = reason
            staff.is_active = False
            staff.save()

            # Deactivate user and sync departure fields
            user = staff.user
            if user:
                user.is_active = False
                user.departure_date = departure_date
                user.departure_reason = reason
                user.save(update_fields=["is_active", "departure_date", "departure_reason"])

            # Invalidate all active sessions for the deactivated user
            from django.contrib.sessions.models import Session
            for session in Session.objects.filter(expire_date__gte=timezone.now()):
                try:
                    data = session.get_decoded()
                    if data.get("_auth_user_id") == str(user.pk):
                        session.delete()
                except Exception:
                    pass

            # Create offboarding record
            OffboardingRecord.objects.get_or_create(staff=staff, defaults={
                "last_working_day": departure_date,
                "exit_reason": reason,
                "status": OffboardingStatus.COMPLETED,
                "resignation_received": True,
                "system_account_deactivated": True,
            })

            log_event(
                actor=request.user, action_type="STAFF_DEPARTED",
                model_name="StaffProfile", object_id=staff.pk,
                description=f"Staff {staff.full_name} deactivated (departed) on {departure_date}",
                request=self.request
            )
            # FR-AUD-003: user account deactivation must always be logged
            log_event(
                actor=request.user, action_type="STAFF_DEACTIVATED",
                model_name="User", object_id=user.pk if user else 0,
                description=(
                    f"Staff {staff.full_name} system account deactivated (reason: {reason}) "
                    f"on {departure_date}"
                ),
                request=self.request
            )
            if warnings:
                messages.warning(
                    request,
                    f"Staff {staff.full_name} deactivated. "
                    f"NOTE: still has {'; '.join(warnings)}.",
                )
            else:
                messages.success(request, f"Staff {staff.full_name} deactivated.")
        return redirect("hr:staff_detail", pk=pk)


# 
# B3 — ONBOARDING WORKFLOW
# 

class OnboardingSetupView(RoleRequiredMixin, TemplateView):
    """Show confirmation page and initialize onboarding for a staff member."""
    template_name = "hr/onboarding_setup.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL]
    required_permission = "hr.change_staffprofile"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        staff = get_object_or_404(StaffProfile.objects.select_related("user"), pk=self.kwargs["pk"])
        ctx["staff"] = staff
        onboarding, _ = StaffOnboardingProgress.objects.get_or_create(staff=staff)
        ctx["onboarding"] = onboarding
        ctx["already_started"] = staff.onboarding_step > 0
        if staff.onboarding_step > 0:
            viewing = _display_step_from_staff(staff)
            ctx["step_statuses"] = _get_step_statuses(staff, viewing)
            ctx["completed_count"] = sum(1 for s in ctx["step_statuses"] if s["is_completed"])
            ctx["progress_pct"] = int(ctx["completed_count"] / ONBOARDING_TOTAL_STEPS * 100)
        else:
            ctx["step_statuses"] = [
                {
                    "number": n,
                    "label": ONBOARDING_STEP_LABELS[n],
                    "is_completed": False,
                    "is_current": n == 1,
                    "is_pending": n > 1,
                }
                for n in range(1, ONBOARDING_TOTAL_STEPS + 1)
            ]
            ctx["completed_count"] = 0
            ctx["progress_pct"] = 0
        ctx["total_steps"] = ONBOARDING_TOTAL_STEPS
        return ctx

    def post(self, request, pk):
        staff = get_object_or_404(StaffProfile.objects.select_related("user"), pk=pk)
        onboarding, created = StaffOnboardingProgress.objects.get_or_create(staff=staff)
        if created:
            staff.onboarding_step = 1
            staff.save(update_fields=["onboarding_step"])
            messages.success(request, f"Onboarding started for {staff.full_name}.")
        else:
            messages.info(request, f"Onboarding already in progress for {staff.full_name}.")
        tab = max(staff.onboarding_step or 1, 1)
        return redirect(f"{reverse('hr:onboarding_tabbed', kwargs={'pk': staff.pk})}?tab={tab}")


def _try_auto_complete_onboarding(staff, onboarding):
    """Auto-detect if all onboarding steps are done and mark as completed.

    Checks the key boolean flags on StaffOnboardingProgress. If every critical
    flag is True, marks onboarding as complete — no manual confirmation needed.
    Sends a summary email to the staff member on completion.
    """
    critical_flags = [
        onboarding.emergency_contacts_provided,
        onboarding.bank_details_provided,
        onboarding.statutory_registered,
        onboarding.documents_submitted,
        onboarding.system_account_created,
        onboarding.induction_completed,
    ]
    if all(critical_flags):
        staff.onboarding_step = 10
        staff.onboarding_completed = True
        staff.save(update_fields=["onboarding_step", "onboarding_completed", "updated_at"])
        onboarding.is_completed = True
        onboarding.completed_at = timezone.now()
        onboarding.current_step = 10
        onboarding.orientation_completed = True
        onboarding.save()

        # Send onboarding completion email to the staff member
        _send_onboarding_complete_email(staff)

        return True
    return False


def _send_onboarding_complete_email(staff):
    """Send a summary email to the staff member when onboarding completes."""
    from communications.email_service import send_email_safe
    from django.template.loader import render_to_string
    from django.conf import settings

    login_url = f"{getattr(settings, 'SITE_URL', 'http://localhost:8000')}/accounts/login/"
    subject = f"Onboarding Complete: {getattr(settings, 'SCHOOL_NAME', 'Hodari Christian School')}"
    context = {
        "staff": staff,
        "school_name": getattr(settings, "SCHOOL_NAME", "Hodari Christian School"),
        "login_url": login_url,
    }

    # Try dynamic DB template first
    from core.email_templates import send_dynamic_email
    db_sent = send_dynamic_email(
        template_type="onboarding_complete",
        to_email=staff.contact_email or staff.user.email,
        context=context,
        actor=staff.user,
    )
    if db_sent:
        return

    # Fallback to static template
    try:
        html_message = render_to_string("registration/onboarding_complete_email.html", context)
    except Exception:
        html_message = None
    plain_message = (
        f"Hello {staff.full_name},\n\n"
        f"Congratulations! Your onboarding process has been completed.\n\n"
        f"Employee ID: {staff.employee_id or '—'}\n"
        f"Department: {staff.get_department_display()}\n"
        f"Job Title: {staff.job_title}\n"
        f"Start Date: {staff.employment_start_date}\n\n"
        f"You now have full access to the Hodari School Management System.\n\n"
        f"Login: {login_url}\n\n"
        f"Hodari Christian School"
    )
    recipient = staff.contact_email or staff.user.email
    if recipient:
        send_email_safe(
            to_email=recipient,
            subject=subject,
            body=plain_message,
            html_body=html_message or plain_message,
            actor=staff.user,
            action_type="ONBOARDING_COMPLETE",
        )


class OnboardingStepView(RoleRequiredMixin, View):
    """Legacy step URLs redirect to the tabbed onboarding page."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL]
    required_permission = "hr.change_staffprofile"

    def get(self, request, pk, step):
        step = min(max(int(step), 1), ONBOARDING_TOTAL_STEPS)
        return redirect(f"{reverse('hr:onboarding_tabbed', kwargs={'pk': pk})}?tab={step}")

    def post(self, request, pk, step):
        staff = get_object_or_404(StaffProfile, pk=pk)
        step = int(step)
        success, _result = _process_step_submission(
            staff, step, request.POST, request=request, files=request.FILES
        )
        if success:
            messages.success(request, f"Step {step} saved.")
            if staff.onboarding_completed:
                messages.success(request, f"Onboarding complete for {staff.full_name}!")
                return redirect("hr:staff_detail", pk=staff.pk)
            next_step = min(step + 1, ONBOARDING_TOTAL_STEPS)
            return redirect(f"{reverse('hr:onboarding_tabbed', kwargs={'pk': staff.pk})}?tab={next_step}")
        messages.error(request, "Please correct the errors below.")
        return redirect(f"{reverse('hr:onboarding_tabbed', kwargs={'pk': staff.pk})}?tab={step}")


ONBOARDING_TOTAL_STEPS = 5
ONBOARDING_STEP_LABELS = {
    1: "Personal Information",
    2: "Employment Details",
    3: "Salary & Banking",
    4: "Documents",
    5: "Completion",
}
ONBOARDING_CHECKLIST_STEPS = {
    1: [1, 6],
    2: [2, 7],
    3: [3, 4],
    4: [5],
    5: [8, 9],
}
ONBOARDING_DOCUMENT_FIELDS = [
    ("document_degree", "degree", "Academic Certificate"),
    ("document_certification", "certification", "Professional Certification"),
    ("document_id_card", "id_card", "National ID or Passport"),
    ("document_contract", "contract", "Employment Contract"),
    ("document_other", "other", "Other Document"),
]


def _display_step_from_staff(staff):
    if staff.onboarding_completed:
        return ONBOARDING_TOTAL_STEPS
    stored = staff.onboarding_step or 1
    if stored > ONBOARDING_TOTAL_STEPS:
        return ONBOARDING_TOTAL_STEPS
    return max(stored, 1)


def _progress_display_step(staff):
    if staff.onboarding_completed:
        return ONBOARDING_TOTAL_STEPS + 1
    return _display_step_from_staff(staff)


def _get_step_statuses(staff, viewing_step):
    progress_step = _progress_display_step(staff)
    statuses = []
    for s in range(1, ONBOARDING_TOTAL_STEPS + 1):
        statuses.append({
            "number": s,
            "label": ONBOARDING_STEP_LABELS.get(s, f"Step {s}"),
            "is_current": s == viewing_step,
            "is_completed": s < progress_step,
            "is_pending": s >= progress_step and not staff.onboarding_completed,
        })
    return statuses


#
# CLASS TEACHER CHECK (AJAX)
#

#
# STAFF DOCUMENT DOWNLOAD (graceful 404)
#

class StaffDocumentDownloadView(RoleRequiredMixin, View):
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.TEACHER, UserRole.FINANCE_OFFICER]
    """Serve staff documents gracefully — shows a user-friendly page if the file is missing."""
    required_permission = "hr.view_staffdocument"
    login_url = "/accounts/login/"

    def get(self, request, document_id):
        from django.http import FileResponse, Http404
        import os
        doc = get_object_or_404(StaffDocument, pk=document_id)

        # HR-HIGH: Teachers can only download their own documents
        from users.models import UserRole
        if request.user.role == UserRole.TEACHER:
            if doc.staff.user_id != request.user.pk:
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied("You can only access your own documents.")

        if not doc.file:
            return render(request, "hr/document_missing.html", {
                "doc": doc,
                "reason": "No file has been uploaded for this document.",
            }, status=404)
        try:
            file_path = doc.file.path
            if not os.path.exists(file_path):
                return render(request, "hr/document_missing.html", {
                    "doc": doc,
                    "reason": f"The file '{doc.name}' was not found on the server. It may have been moved or deleted.",
                }, status=404)
            fh = open(file_path, "rb")
            return FileResponse(fh, as_attachment=True, filename=doc.name)
        except Exception:
            return render(request, "hr/document_missing.html", {
                "doc": doc,
                "reason": "An error occurred while accessing this document.",
            }, status=404)


class StaffDocumentDeleteView(RoleRequiredMixin, View):
    """Delete a staff document attachment."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL]
    required_permission = "hr.delete_staffdocument"
    login_url = "/accounts/login/"

    def post(self, request, document_id):
        doc = get_object_or_404(StaffDocument, pk=document_id)
        staff_pk = doc.staff.pk
        if doc.file:
            try:
                doc.file.delete(save=False)
            except Exception:
                pass
        doc.delete()
        log_event(
            actor=request.user, action_type="STAFF_DOCUMENT_DELETED",
            model_name="StaffDocument", object_id=document_id,
            description=f"Deleted document '{doc.name}' for {doc.staff.full_name}",
            request=request,
        )
        messages.success(request, f"Document '{doc.name}' removed.")
        return redirect("hr:onboarding_tabbed", pk=staff_pk) + "?tab=4"


class StaffDocumentDuplicateCheckView(RoleRequiredMixin, View):
    """AJAX endpoint to check for duplicate staff documents by SHA-256 hash."""
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.TEACHER]
    required_permission = "hr.view_staffdocument"
    login_url = "/accounts/login/"

    def post(self, request):
        import json
        from academics.validators import _compute_file_hash
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"results": []})

        hashes = data.get("hashes", [])
        staff_pk = data.get("staff_pk")
        if not hashes:
            return JsonResponse({"results": []})

        existing_docs = StaffDocument.objects.filter(staff__pk=staff_pk)
        existing_hashes = {}
        for doc in existing_docs:
            if doc.file:
                try:
                    h = _compute_file_hash(doc.file.path)
                    existing_hashes[h] = doc.name
                except (AttributeError, ValueError, OSError):
                    continue

        results = []
        for pair in hashes:
            h = pair.get("hash", "")
            fname = pair.get("filename", "")
            if h in existing_hashes:
                results.append({
                    "filename": fname,
                    "duplicate": True,
                    "existing_name": existing_hashes[h],
                })
            else:
                results.append({"filename": fname, "duplicate": False})
        return JsonResponse({"results": results})


STAFF_DOCUMENT_TYPE_CHOICES = [
    ("id_card", "National ID / Passport"),
    ("certification", "Professional Certification"),
    ("degree", "Degree / Diploma Certificate"),
    ("contract", "Employment Contract"),
    ("nssf", "NSSF Statement"),
    ("nhif", "NHIF Card"),
    ("tin", "TIN Certificate"),
    ("other", "Other"),
]


class StaffDocumentUploadView(RoleRequiredMixin, View):
    """HTMX endpoint: upload a document directly from the staff detail sidebar."""
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER,
        UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL,
    ]
    required_permission = "hr.add_staffdocument"
    login_url = "/accounts/login/"

    def post(self, request, pk):
        from academics.validators import validate_attachment_file, _compute_file_hash

        staff = get_object_or_404(StaffProfile, pk=pk)
        upload = request.FILES.get("document_file")
        doc_type = request.POST.get("document_type", "other").strip()
        doc_name = request.POST.get("document_name", "").strip()
        notes = request.POST.get("document_notes", "").strip()

        if not upload:
            messages.error(request, "Please select a file to upload.")
            return redirect("hr:staff_detail", pk=pk)

        if not doc_name:
            doc_name = upload.name

        try:
            validate_attachment_file(upload, area="staff_documents")
        except Exception as e:
            messages.error(request, str(e))
            return redirect("hr:staff_detail", pk=pk)

        file_hash = _compute_file_hash(upload)
        existing_hashes = {}
        for doc in StaffDocument.objects.filter(staff=staff):
            if doc.file:
                try:
                    h = _compute_file_hash(doc.file.path)
                    existing_hashes[h] = doc.name
                except (AttributeError, ValueError, OSError):
                    continue
        if file_hash in existing_hashes:
            messages.warning(request, f"This file is already uploaded as '{existing_hashes[file_hash]}'. Duplicate skipped.")
            return redirect("hr:staff_detail", pk=pk)

        StaffDocument.objects.create(
            staff=staff,
            name=doc_name,
            document_type=doc_type,
            file=upload,
            notes=notes,
            uploaded_by=request.user,
        )
        log_event(
            actor=request.user,
            action_type="STAFF_DOCUMENT_UPLOADED",
            model_name="StaffDocument",
            object_id=staff.pk,
            description=f"Uploaded '{doc_name}' ({doc_type}) for {staff.full_name}",
            request=request,
        )
        messages.success(request, f"Document '{doc_name}' uploaded successfully.")
        return redirect("hr:staff_detail", pk=pk)


def _advance_onboarding_step(staff, onboarding, completed_step):
    next_step = completed_step + 1
    if next_step > ONBOARDING_TOTAL_STEPS:
        staff.onboarding_step = ONBOARDING_TOTAL_STEPS + 1
        staff.onboarding_completed = True
        onboarding.is_completed = True
        onboarding.completed_at = timezone.now()
        onboarding.current_step = ONBOARDING_TOTAL_STEPS + 1
        onboarding.orientation_completed = True
    else:
        staff.onboarding_step = next_step
        onboarding.current_step = next_step


def _save_onboarding_documents(staff, files, notes, uploaded_by):
    from academics.validators import validate_attachment_file, _compute_file_hash
    saved = 0
    skipped = 0

    existing_hashes = {}
    for doc in StaffDocument.objects.filter(staff=staff):
        if doc.file:
            try:
                h = _compute_file_hash(doc.file.path)
                existing_hashes[h] = doc.name
            except (AttributeError, ValueError, OSError):
                continue

    for field_name, doc_type, label in ONBOARDING_DOCUMENT_FIELDS:
        upload = files.get(field_name) if files else None
        if not upload:
            continue
        validate_attachment_file(upload, area="staff_documents")

        file_hash = _compute_file_hash(upload)
        if file_hash in existing_hashes:
            skipped += 1
            continue

        StaffDocument.objects.create(
            staff=staff,
            name=label,
            document_type=doc_type,
            file=upload,
            notes=notes or "",
            uploaded_by=uploaded_by,
        )
        saved += 1
    return saved, skipped


def _build_step_context(staff, step):
    """Build context data for a single onboarding step."""
    step = min(max(step, 1), ONBOARDING_TOTAL_STEPS)
    ctx = {
        "staff": staff,
        "step": step,
        "total_steps": ONBOARDING_TOTAL_STEPS,
        "step_label": ONBOARDING_STEP_LABELS.get(step, "Unknown Step"),
    }
    ctx["departments"] = Department.choices
    checklist_steps = ONBOARDING_CHECKLIST_STEPS.get(step, [step])
    ctx["checklist_items"] = OnboardingChecklistItem.objects.filter(
        step__in=checklist_steps
    ).order_by("step", "order")
    try:
        ctx["onboarding"] = StaffOnboardingProgress.objects.get(staff=staff)
    except StaffOnboardingProgress.DoesNotExist:
        ctx["onboarding"] = None

    form = OnboardingStepForm(step_number=step)
    if step == 1:
        # Personal Information + Emergency Contacts
        ctx["staff_documents"] = StaffDocument.objects.filter(staff=staff).order_by("-created_at")
        ctx["document_upload_fields"] = ONBOARDING_DOCUMENT_FIELDS

        # Safely set initial values
        if "full_name" in form.fields:
            form.fields["full_name"].initial = staff.full_name
        if "date_of_birth" in form.fields:
            form.fields["date_of_birth"].initial = staff.date_of_birth
        if "gender" in form.fields:
            form.fields["gender"].initial = staff.gender
        if "nationality" in form.fields:
            form.fields["nationality"].initial = staff.nationality
        if "marital_status" in form.fields:
            form.fields["marital_status"].initial = staff.marital_status
        if "residential_address" in form.fields:
            form.fields["residential_address"].initial = staff.residential_address
        if "contact_phone" in form.fields:
            form.fields["contact_phone"].initial = staff.contact_phone
        if "contact_email" in form.fields:
            form.fields["contact_email"].initial = staff.contact_email
        if "emergency_contact_name" in form.fields:
            form.fields["emergency_contact_name"].initial = staff.emergency_contact_name
        if "emergency_contact_phone" in form.fields:
            form.fields["emergency_contact_phone"].initial = staff.emergency_contact_phone
        if "emergency_contact_relationship" in form.fields:
            form.fields["emergency_contact_relationship"].initial = staff.emergency_contact_relationship

    elif step == 2:
        # Employment Details + System Access
        user = staff.user
        ctx["role_choices"] = UserRole.choices
        if "job_title" in form.fields:
            form.fields["job_title"].initial = staff.job_title
        if "department" in form.fields:
            form.fields["department"].initial = staff.department
        if "staff_category" in form.fields:
            form.fields["staff_category"].initial = staff.staff_category
        if "employment_type" in form.fields:
            form.fields["employment_type"].initial = staff.employment_type
        if "employment_start_date" in form.fields:
            form.fields["employment_start_date"].initial = staff.employment_start_date
        if "probation_end_date" in form.fields:
            form.fields["probation_end_date"].initial = staff.probation_end_date
        if "years_of_experience" in form.fields:
            form.fields["years_of_experience"].initial = staff.years_of_experience
        if "highest_qualification" in form.fields:
            form.fields["highest_qualification"].initial = staff.highest_qualification
        if "access_role" in form.fields:
            form.fields["access_role"].initial = user.role
        if "access_is_active" in form.fields:
            form.fields["access_is_active"].initial = user.is_active
        if "system_account_created" in form.fields:
            form.fields["system_account_created"].initial = "yes" if user.is_active else "no"

    elif step == 3:
        # Salary & Banking + Statutory
        if "basic_salary" in form.fields:
            form.fields["basic_salary"].initial = staff.basic_salary
        if "housing_allowance" in form.fields:
            form.fields["housing_allowance"].initial = staff.housing_allowance
        if "transport_allowance" in form.fields:
            form.fields["transport_allowance"].initial = staff.transport_allowance
        if "medical_allowance" in form.fields:
            form.fields["medical_allowance"].initial = staff.medical_allowance
        if "other_allowances" in form.fields:
            form.fields["other_allowances"].initial = staff.other_allowances
        if "bank_name" in form.fields:
            form.fields["bank_name"].initial = staff.bank_name
        if "bank_account_number" in form.fields:
            form.fields["bank_account_number"].initial = staff.bank_account_number
        if "bank_branch" in form.fields:
            form.fields["bank_branch"].initial = staff.bank_branch
        if "nssf_number" in form.fields:
            form.fields["nssf_number"].initial = staff.nssf_number
        if "tin_number" in form.fields:
            form.fields["tin_number"].initial = staff.tin_number
        if "heslb_loan_number" in form.fields:
            form.fields["heslb_loan_number"].initial = staff.heslb_loan_number
        if "nhif_number" in form.fields:
            form.fields["nhif_number"].initial = staff.nhif_number

    elif step == 4:
        # Qualifications & Documents
        ctx["staff_documents"] = StaffDocument.objects.filter(staff=staff).order_by("-created_at")
        ctx["document_upload_fields"] = ONBOARDING_DOCUMENT_FIELDS

    elif step == 5:
        # Induction + Confirmation & Completion
        from .models import InductionChecklistCompletion
        completed_ids = set(
            InductionChecklistCompletion.objects.filter(
                staff=staff, is_completed=True
            ).values_list("checklist_item_id", flat=True)
        )
        for item in ctx["checklist_items"]:
            item.is_completed_item = item.pk in completed_ids
        total_items = ctx["checklist_items"].count()
        done_items = len(completed_ids.intersection(
            set(ctx["checklist_items"].values_list("pk", flat=True))
        ))
        ctx["induction_total"] = total_items
        ctx["induction_done"] = done_items
        ctx["assignment_count"] = TeacherClassAssignment.objects.filter(teacher=staff).count()
        ctx["doc_count"] = StaffDocument.objects.filter(staff=staff).count()

    ctx["form"] = form
    return ctx


def _process_step_submission(staff, step, post_data, request=None, files=None):
    """Process an onboarding step form POST. Returns (success, form_or_errors)."""
    from audit.models import log_event
    form = OnboardingStepForm(post_data, files, step_number=step)
    if not form.is_valid():
        return False, form
    with transaction.atomic():
        if step == 1:
            # Personal Information + Emergency Contacts
            staff.full_name = form.cleaned_data.get("full_name", staff.full_name)
            staff.date_of_birth = form.cleaned_data.get("date_of_birth", staff.date_of_birth)
            staff.gender = form.cleaned_data.get("gender", staff.gender)
            staff.nationality = form.cleaned_data.get("nationality", staff.nationality)
            staff.marital_status = form.cleaned_data.get("marital_status", staff.marital_status)
            staff.residential_address = form.cleaned_data.get("residential_address", staff.residential_address)
            staff.contact_phone = form.cleaned_data.get("contact_phone", staff.contact_phone)
            staff.contact_email = form.cleaned_data.get("contact_email", staff.contact_email)
            staff.emergency_contact_name = form.cleaned_data.get("emergency_contact_name", staff.emergency_contact_name)
            staff.emergency_contact_phone = form.cleaned_data.get("emergency_contact_phone", staff.emergency_contact_phone)
            staff.emergency_contact_relationship = form.cleaned_data.get("emergency_contact_relationship", staff.emergency_contact_relationship)
            profile_picture = form.cleaned_data.get("profile_picture")
            if profile_picture:
                user = staff.user
                user.profile_picture = profile_picture
                user.save(update_fields=["profile_picture"])
        elif step == 2:
            # Employment Details + System Access
            staff.job_title = form.cleaned_data.get("job_title", staff.job_title)
            staff.department = form.cleaned_data.get("department", staff.department)
            staff.staff_category = form.cleaned_data.get("staff_category", staff.staff_category)
            staff.employment_type = form.cleaned_data.get("employment_type", staff.employment_type)
            staff.employment_start_date = form.cleaned_data.get("employment_start_date", staff.employment_start_date)
            staff.probation_end_date = form.cleaned_data.get("probation_end_date", staff.probation_end_date)
            staff.years_of_experience = form.cleaned_data.get("years_of_experience", staff.years_of_experience)
            staff.highest_qualification = form.cleaned_data.get("highest_qualification", staff.highest_qualification)
            user = staff.user
            new_role = form.cleaned_data.get("access_role", user.role)
            old_role = user.role
            # Tracker 2.3 (secondary path): role change via onboarding also requires a reason.
            role_changed = old_role != new_role
            if role_changed:
                role_reason = (post_data.get("role_change_reason") or "").strip()
                if not role_reason:
                    messages.error(
                        request,
                        "A reason is required when changing a user's role. "
                        "Please provide a reason for this role change.",
                    )
                    return False, form
            user.role = new_role
            user.is_active = form.cleaned_data.get("access_is_active", user.is_active)
            from users.staff_assignment import apply_django_admin_flags
            apply_django_admin_flags(user, role=user.role)
            user.save(update_fields=["role", "is_active", "is_staff", "is_superuser"])
            if role_changed:
                from audit.models import log_event
                from users.models import UserRole
                role_labels = dict(UserRole.choices)
                log_event(
                    actor=getattr(request, "user", None),
                    action_type="ROLE_CHANGE",
                    model_name="User",
                    object_id=user.pk,
                    description=(
                        f"Role changed from {role_labels.get(old_role, old_role) or 'previous'} "
                        f"to {role_labels.get(new_role, new_role)} for "
                        f"{user.get_full_name() or user.username}. Reason: {role_reason}"
                    ),
                    before_value=old_role,
                    after_value=new_role,
                    request=request,
                )
        elif step == 3:
            # Salary & Banking + Statutory
            staff.basic_salary = form.cleaned_data.get("basic_salary", staff.basic_salary)
            staff.housing_allowance = form.cleaned_data.get("housing_allowance", staff.housing_allowance)
            staff.transport_allowance = form.cleaned_data.get("transport_allowance", staff.transport_allowance)
            staff.medical_allowance = form.cleaned_data.get("medical_allowance", staff.medical_allowance)
            staff.other_allowances = form.cleaned_data.get("other_allowances", staff.other_allowances)
            staff.bank_name = form.cleaned_data.get("bank_name", staff.bank_name)
            staff.bank_account_number = form.cleaned_data.get("bank_account_number", staff.bank_account_number)
            staff.bank_branch = form.cleaned_data.get("bank_branch", staff.bank_branch)
            staff.nssf_number = form.cleaned_data.get("nssf_number", staff.nssf_number)
            staff.tin_number = form.cleaned_data.get("tin_number", staff.tin_number)
            staff.heslb_loan_number = form.cleaned_data.get("heslb_loan_number", staff.heslb_loan_number)
            staff.nhif_number = form.cleaned_data.get("nhif_number", staff.nhif_number)
        elif step == 4:
            # Documents
            notes = form.cleaned_data.get("documents_notes", "")
            _save_onboarding_documents(
                staff, files or {}, notes, getattr(request, "user", None)
            )
        elif step == 5:
            # Induction Checklist
            from .models import InductionChecklistCompletion
            checklist_step_ids = ONBOARDING_CHECKLIST_STEPS.get(5, [8, 9])
            checklist_qs = OnboardingChecklistItem.objects.filter(step__in=checklist_step_ids)
            all_done = True
            for item in checklist_qs:
                key = f"induction_item_{item.pk}"
                is_done = post_data.get(key) == "1"
                comp, _ = InductionChecklistCompletion.objects.update_or_create(
                    staff=staff, checklist_item=item,
                    defaults={
                        "is_completed": is_done,
                        "completed_at": timezone.now() if is_done else None,
                        "completed_by": getattr(request, 'user', None),
                    },
                )
                if not is_done:
                    all_done = False
        staff.save()
        onboarding, _ = StaffOnboardingProgress.objects.get_or_create(staff=staff)
        if step == 1:
            onboarding.emergency_contacts_provided = True
        elif step == 3:
            onboarding.bank_details_provided = True
            onboarding.statutory_registered = True
        elif step == 4:
            onboarding.documents_submitted = True
        elif step == 5:
            onboarding.induction_completed = all_done
        if step == 2 and post_data.get("system_account_created") == "yes":
            onboarding.system_account_created = True
        _advance_onboarding_step(staff, onboarding, step)
        onboarding.save()
        staff.save(update_fields=["onboarding_step", "onboarding_completed", "updated_at"])
        auto_completed = False
        if not staff.onboarding_completed and step < ONBOARDING_TOTAL_STEPS:
            auto_completed = _try_auto_complete_onboarding(staff, onboarding)
    log_event(
        actor=getattr(request, 'user', None), action_type="ONBOARDING_STEP_COMPLETED",
        model_name="StaffProfile", object_id=staff.pk,
        description=f"Onboarding step {step} completed for {staff.full_name}",
        request=request,
    )
    return True, None


class OnboardingTabbedView(RoleRequiredMixin, TemplateView):
    """Single-page tabbed onboarding with sidebar navigation."""
    template_name = "hr/onboarding_tabbed.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL]
    required_permission = "hr.change_staffprofile"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        staff = get_object_or_404(StaffProfile.objects.select_related("user"), pk=self.kwargs["pk"])
        ctx["staff"] = staff
        tab = self.request.GET.get("tab")
        if tab and tab.isdigit():
            current_step = min(max(int(tab), 1), ONBOARDING_TOTAL_STEPS)
        else:
            current_step = _display_step_from_staff(staff)
        ctx["current_step"] = current_step
        ctx["step_statuses"] = _get_step_statuses(staff, current_step)
        completed = sum(1 for s in ctx["step_statuses"] if s["is_completed"])
        ctx["completed_count"] = completed
        ctx["progress_pct"] = int(completed / ONBOARDING_TOTAL_STEPS * 100)
        step_ctx = _build_step_context(staff, current_step)
        ctx["step_form"] = step_ctx["form"]
        ctx["step_label"] = step_ctx["step_label"]
        ctx["checklist_items"] = step_ctx.get("checklist_items")
        ctx["onboarding"] = step_ctx.get("onboarding")
        ctx["current_assignments"] = step_ctx.get("current_assignments")
        ctx["departments"] = step_ctx.get("departments")
        ctx["staff_documents"] = step_ctx.get("staff_documents")
        ctx["terms"] = step_ctx.get("terms")
        ctx["grade_classes"] = step_ctx.get("grade_classes")
        ctx["subjects"] = step_ctx.get("subjects")
        ctx["role_choices"] = step_ctx.get("role_choices")
        ctx["document_upload_fields"] = step_ctx.get("document_upload_fields")
        ctx["total_steps"] = ONBOARDING_TOTAL_STEPS
        # Step 8 / Step 10 summary context
        ctx["induction_done"] = step_ctx.get("induction_done", 0)
        ctx["induction_total"] = step_ctx.get("induction_total", 0)
        ctx["assignment_count"] = step_ctx.get("assignment_count", 0)
        ctx["doc_count"] = step_ctx.get("doc_count", 0)
        return ctx

    def post(self, request, pk):
        staff = get_object_or_404(StaffProfile.objects.select_related("user"), pk=pk)
        step = int(request.POST.get("step", 1))
        save_action = request.POST.get("save_action", "continue")
        target_tab = request.POST.get("target_tab", "").strip()
        success, result = _process_step_submission(
            staff, step, request.POST, request=request, files=request.FILES
        )
        if success:
            messages.success(request, f"Step {step} saved.")
            if staff.onboarding_completed:
                messages.success(request, f"Onboarding complete for {staff.full_name}!")
                return redirect("hr:staff_detail", pk=staff.pk)
            if save_action == "exit":
                messages.info(request, "Progress saved. You can continue onboarding later.")
                return redirect("hr:staff_detail", pk=staff.pk)
            if target_tab and target_tab.isdigit():
                goto = min(max(int(target_tab), 1), ONBOARDING_TOTAL_STEPS)
                return redirect(f"{reverse('hr:onboarding_tabbed', kwargs={'pk': staff.pk})}?tab={goto}")
            next_step = min(step + 1, ONBOARDING_TOTAL_STEPS)
            return redirect(f"{reverse('hr:onboarding_tabbed', kwargs={'pk': staff.pk})}?tab={next_step}")
        messages.error(request, "Please correct the errors below.")
        return redirect(f"{reverse('hr:onboarding_tabbed', kwargs={'pk': staff.pk})}?tab={step}")


# 
# B6 — LEAVE MANAGEMENT
# 

class LeaveRequestListView(RoleRequiredMixin, ListView):
    """List all leave requests with filtering."""
    template_name = "hr/leave_list.html"
    context_object_name = "leave_requests"
    paginate_by = 30
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "hr.view_leaverequest"

    def get_queryset(self):
        qs = LeaveRequest.objects.select_related("staff", "approved_by").order_by("-start_date")

        status = self.request.GET.get("status", "")
        leave_type = self.request.GET.get("leave_type", "")
        q = self.request.GET.get("q", "").strip()

        if status:
            qs = qs.filter(status=status)
        if leave_type:
            qs = qs.filter(leave_type=leave_type)
        if q:
            qs = qs.filter(staff__full_name__icontains=q)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["leave_types"] = LeaveType.choices
        ctx["statuses"] = LeaveStatus.choices
        ctx["selected_status"] = self.request.GET.get("status", "")
        ctx["selected_leave_type"] = self.request.GET.get("leave_type", "")
        ctx["q"] = self.request.GET.get("q", "")

        ctx["pending_count"] = LeaveRequest.objects.filter(status=LeaveStatus.PENDING).count()
        ctx["approved_count"] = LeaveRequest.objects.filter(status=LeaveStatus.APPROVED).count()
        ctx["total_requests"] = LeaveRequest.objects.count()
        return ctx


class LeaveRequestCreateView(RoleRequiredMixin, CreateView):
    """Create a new leave request."""
    model = LeaveRequest
    form_class = LeaveRequestForm
    template_name = "hr/leave_form.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "hr.add_leaverequest"

    def get_initial(self):
        initial = super().get_initial()
        staff_pk = self.request.GET.get("staff")
        if staff_pk:
            initial["staff"] = staff_pk
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        staff_pk = self.request.GET.get("staff")
        if staff_pk:
            ctx["selected_staff"] = get_object_or_404(StaffProfile, pk=staff_pk)
        # Get active staff for dropdown
        ctx["staff_list"] = StaffProfile.objects.filter(is_active=True).order_by("full_name")
        return ctx

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["staff"] = forms.ModelChoiceField(
            queryset=StaffProfile.objects.filter(is_active=True).order_by("full_name"),
            widget=forms.Select(attrs={"class": "hf2-select"}),
            required=True
        )
        return form

    def get_success_url(self):
        return reverse("hr:leave_list")

    def form_valid(self, form):
        response = super().form_valid(form)
        log_event(
            actor=self.request.user, action_type="LEAVE_REQUEST_CREATED",
            model_name="LeaveRequest", object_id=self.object.pk,
            description=f"Leave request created for {self.object.staff.full_name}",
            request=self.request
        )
        messages.success(self.request, "Leave request submitted for approval.")
        return response


class LeaveRequestApproveView(RoleRequiredMixin, View):
    """Approve or reject a leave request."""
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "hr.change_leaverequest"

    def post(self, request, pk):
        leave = get_object_or_404(LeaveRequest, pk=pk)
        action = request.POST.get("action", "")
        rejection_reason = request.POST.get("rejection_reason", "")

        if leave.status != LeaveStatus.PENDING:
            messages.error(request, "This leave request has already been processed.")
            return redirect("hr:leave_list")

        try:
            if action == "approve":
                leave.status = LeaveStatus.APPROVED
                leave.approved_by = request.user
                leave.approved_at = timezone.now()
                leave.save()

                # Update leave allocation
                allocation, _ = LeaveAllocation.objects.get_or_create(
                    staff=leave.staff,
                    year=leave.start_date.year,
                    leave_type=leave.leave_type,
                    defaults={"total_days": 0},
                )
                allocation.used_days = (allocation.used_days or 0) + leave.total_days
                allocation.pending_days = max(0, (allocation.pending_days or 0) - leave.total_days)
                allocation.save()

                # Notify staff member
                from communications.email_service import dispatch_notification
                from core.email_templates import send_dynamic_email
                from core.models import SchoolSettings
                school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"

                tpl_context = {
                    "staff_name": leave.staff.full_name,
                    "leave_type": leave.leave_type,
                    "start_date": str(leave.start_date),
                    "end_date": str(leave.end_date),
                    "approved_by": request.user.get_full_name(),
                    "school_name": school_name,
                }
                db_sent = send_dynamic_email(
                    template_type="leave_approved",
                    to_email=leave.staff.contact_email or leave.staff.user.email,
                    context=tpl_context,
                    actor=request.user,
                )
                if not db_sent:
                    dispatch_notification(
                        user=leave.staff,
                        title="Leave Approved",
                        message=f"Your leave request ({leave.leave_type}) from {leave.start_date} to {leave.end_date} has been approved by {request.user.get_full_name()}.",
                        link="/hr/my-leave/",
                        actor=request.user,
                    )

                log_event(
                    actor=request.user, action_type="LEAVE_APPROVED",
                    model_name="LeaveRequest", object_id=leave.pk,
                    description=f"Leave approved for {leave.staff.full_name}",
                    request=self.request
                )
                messages.success(request, f"Leave approved for {leave.staff.full_name}.")

            elif action == "reject":
                if not rejection_reason:
                    messages.error(request, "Please provide a reason for rejection.")
                    return redirect("hr:leave_list")

                leave.status = LeaveStatus.REJECTED
                leave.approved_by = request.user
                leave.approved_at = timezone.now()
                leave.rejection_reason = rejection_reason
                leave.save()

                # Release pending days
                allocation = LeaveAllocation.objects.filter(
                    staff=leave.staff, year=leave.start_date.year, leave_type=leave.leave_type
                ).first()
                if allocation:
                    allocation.pending_days = max(0, (allocation.pending_days or 0) - leave.total_days)
                    allocation.save()

                # Notify staff member
                from communications.email_service import dispatch_notification
                from core.email_templates import send_dynamic_email
                from core.models import SchoolSettings
                school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"

                tpl_context = {
                    "staff_name": leave.staff.full_name,
                    "leave_type": leave.leave_type,
                    "start_date": str(leave.start_date),
                    "end_date": str(leave.end_date),
                    "rejection_reason": rejection_reason,
                    "school_name": school_name,
                }
                db_sent = send_dynamic_email(
                    template_type="leave_rejected",
                    to_email=leave.staff.contact_email or leave.staff.user.email,
                    context=tpl_context,
                    actor=request.user,
                )
                if not db_sent:
                    dispatch_notification(
                        user=leave.staff,
                        title="Leave Rejected",
                        message=f"Your leave request ({leave.leave_type}) from {leave.start_date} to {leave.end_date} has been rejected by {request.user.get_full_name()}.\n\nReason: {rejection_reason}",
                        link="/hr/my-leave/",
                        actor=request.user,
                    )

                log_event(
                    actor=request.user, action_type="LEAVE_REJECTED",
                    model_name="LeaveRequest", object_id=leave.pk,
                    description=f"Leave rejected for {leave.staff.full_name}: {rejection_reason[:100]}",
                    request=self.request
                )
                messages.success(request, f"Leave request rejected.")

        except Exception as e:
            messages.error(request, f"Error processing leave request: {e}")

        return redirect("hr:leave_list")


# 
# B4 — PAYROLL MODULE
# 

class PayrollRunListView(RoleRequiredMixin, ListView):
    """List all payroll runs."""
    template_name = "hr/payroll_list.html"
    context_object_name = "payroll_runs"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "hr.view_payrollrun"
    paginate_by = 20

    def get_queryset(self):
        return PayrollRun.objects.select_related("approved_by", "processed_by").order_by("-period_start", "-created_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from datetime import date
        ctx["draft_count"] = PayrollRun.objects.filter(status=PayrollStatus.DRAFT).count()
        ctx["pending_approval"] = PayrollRun.objects.filter(status=PayrollStatus.PENDING_APPROVAL).count()
        ctx["total_paid"] = PayrollRun.objects.filter(status=PayrollStatus.PAID).aggregate(t=Sum("total_net_pay"))["t"] or 0
        ctx["payroll_statuses"] = PayrollStatus.choices
        return ctx


class PayrollRunCreateView(RoleRequiredMixin, CreateView):
    model = PayrollRun
    form_class = PayrollRunForm
    template_name = "hr/payroll_form.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "hr.add_payrollrun"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["is_new"] = True
        return ctx

    def get_success_url(self):
        return reverse("hr:payroll_detail", kwargs={"pk": self.object.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        log_event(
            actor=self.request.user, action_type="PAYROLL_CREATED",
            model_name="PayrollRun", object_id=self.object.pk,
            description=f"Payroll run created: {self.object.period_name}",
            request=self.request
        )
        messages.success(self.request, f"Payroll run '{self.object.period_name}' created.")
        return response


class PayrollRunDetailView(RoleRequiredMixin, DetailView):
    template_name = "hr/payroll_detail.html"
    model = PayrollRun
    context_object_name = "payroll_run"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "hr.view_payrollrun"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        payroll = self.object
        entries = PayrollEntry.objects.filter(payroll_run=payroll).select_related("staff")
        ctx["entries"] = entries

        # Calculate summary
        ctx["total_gross"] = sum(e.gross_pay for e in entries)
        ctx["total_deductions"] = sum(e.total_deductions for e in entries)
        ctx["total_net"] = sum(e.net_pay for e in entries)
        ctx["total_staff"] = entries.count()
        ctx["total_paye"] = sum(e.paye_tax for e in entries)
        ctx["total_nssf"] = sum(e.nssf_employee for e in entries)
        ctx["total_nhif"] = sum(e.nhif_deduction for e in entries)

        # Active staff for adding entries
        existing_staff_ids = entries.values_list("staff_id", flat=True)
        ctx["available_staff"] = StaffProfile.objects.filter(is_active=True).exclude(
            id__in=existing_staff_ids
        ).order_by("full_name")

        return ctx


class PayrollEntryAutoPopulateView(RoleRequiredMixin, View):
    """Auto-populate payroll entries from staff profiles."""
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "hr.add_payrollentry"

    def post(self, request, pk):
        payroll = get_object_or_404(PayrollRun, pk=pk)

        if payroll.status != PayrollStatus.DRAFT:
            messages.error(request, "Can only add entries to draft payroll runs.")
            return redirect("hr:payroll_detail", pk=pk)

        active_staff = StaffProfile.objects.filter(is_active=True)
        existing_ids = PayrollEntry.objects.filter(payroll_run=payroll).values_list("staff_id", flat=True)
        new_staff = active_staff.exclude(id__in=existing_ids)

        count = 0
        for staff in new_staff:
            entry = PayrollEntry(payroll_run=payroll, staff=staff)
            entry.auto_calculate_from_profile()
            entry.save()
            count += 1

        if count:
            payroll.calculate_summary()
            messages.success(request, f"Auto-populated {count} payroll entries.")
        else:
            messages.info(request, "No new staff to add.")

        return redirect("hr:payroll_detail", pk=pk)


class PayrollEntryEditView(RoleRequiredMixin, UpdateView):
    model = PayrollEntry
    form_class = PayrollEntryForm
    template_name = "hr/payroll_entry_form.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "hr.change_payrollentry"

    def get_success_url(self):
        payroll = self.object.payroll_run
        payroll.calculate_summary()
        return reverse("hr:payroll_detail", kwargs={"pk": self.object.payroll_run.pk})

    def form_valid(self, form):
        # HR-HIGH: Only allow edits on DRAFT payroll runs
        payroll = self.object.payroll_run
        if payroll.status != PayrollStatus.DRAFT:
            messages.error(
                self.request,
                f"Cannot edit entries on a '{payroll.get_status_display()}' payroll. Only DRAFT payrolls can be edited."
            )
            return redirect("hr:payroll_detail", pk=payroll.pk)

        response = super().form_valid(form)
        self.object.calculate()
        self.object.save()
        messages.success(self.request, f"Payroll entry updated for {self.object.staff.full_name}.")
        return response


class PayrollApproveView(RoleRequiredMixin, View):
    """Approve payroll run for payment."""
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "hr.change_payrollrun"

    def post(self, request, pk):
        payroll = get_object_or_404(PayrollRun, pk=pk)

        if payroll.status != PayrollStatus.PENDING_APPROVAL:
            messages.error(request, "Payroll must be in 'Pending Approval' status.")
            return redirect("hr:payroll_detail", pk=pk)

        payroll.status = PayrollStatus.APPROVED
        payroll.approved_by = request.user
        payroll.approved_at = timezone.now()
        payroll.save()

        log_event(
            actor=request.user, action_type="PAYROLL_APPROVED",
            model_name="PayrollRun", object_id=payroll.pk,
            description=f"Payroll {payroll.period_name} approved",
            request=self.request
        )
        messages.success(request, f"Payroll '{payroll.period_name}' approved.")
        return redirect("hr:payroll_detail", pk=pk)


class PayrollSubmitView(RoleRequiredMixin, View):
    """Submit payroll for approval."""
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "hr.change_payrollrun"

    def post(self, request, pk):
        payroll = get_object_or_404(PayrollRun, pk=pk)

        if payroll.status != PayrollStatus.DRAFT:
            messages.error(request, "Only draft payroll runs can be submitted.")
            return redirect("hr:payroll_detail", pk=pk)

        entry_count = PayrollEntry.objects.filter(payroll_run=payroll).count()
        if entry_count == 0:
            messages.error(request, "Cannot submit: no payroll entries.")
            return redirect("hr:payroll_detail", pk=pk)

        payroll.status = PayrollStatus.PENDING_APPROVAL
        payroll.save()

        log_event(
            actor=request.user, action_type="PAYROLL_SUBMITTED",
            model_name="PayrollRun", object_id=payroll.pk,
            description=f"Payroll {payroll.period_name} submitted for approval",
            request=self.request
        )
        messages.success(request, f"Payroll '{payroll.period_name}' submitted for approval.")
        return redirect("hr:payroll_detail", pk=pk)


class PayrollPayslipView(RoleRequiredMixin, DetailView):
    """View/print a payslip for a payroll entry."""
    template_name = "hr/payslip_detail.html"
    model = PayrollEntry
    context_object_name = "entry"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.TEACHER]
    required_permission = "hr.view_payrollentry"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if self.request.user.role == UserRole.TEACHER and obj.staff.user_id != self.request.user.pk:
            from audit.models import log_event
            log_event(
                actor=self.request.user,
                action_type="ROLE_ACCESS_DENIED",
                model_name="PayrollEntry",
                object_id=obj.pk,
                description=(
                    f"TEACHER {self.request.user} attempted to access "
                    f"payslip belonging to staff {obj.staff_id} (pk={obj.pk})"
                ),
                request=self.request,
            )
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You can only view your own payslip.")
        return obj


# 
# B5 — STATUTORY FILING
# 

class StatutoryFilingListView(RoleRequiredMixin, ListView):
    """List all statutory filings grouped by type."""
    template_name = "hr/statutory_filing_list.html"
    context_object_name = "filings"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "hr.view_statutoryfiling"

    def get_queryset(self):
        qs = StatutoryFiling.objects.select_related("payroll_run", "submitted_by").order_by("-period_name", "filing_type")
        filing_type = self.request.GET.get("type", "")
        if filing_type:
            qs = qs.filter(filing_type=filing_type)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filing_types"] = StatutoryFiling.FILING_TYPE_CHOICES
        ctx["selected_type"] = self.request.GET.get("type", "")

        # Summary by type — single aggregated query instead of N*2
        filing_stats = StatutoryFiling.objects.values('filing_type').annotate(
            total=Count('id'),
            pending=Count('id', filter=Q(status=StatutoryFilingStatus.DRAFT)),
        )
        filing_map = {row['filing_type']: row for row in filing_stats}
        for ftype, flabel in StatutoryFiling.FILING_TYPE_CHOICES:
            row = filing_map.get(ftype, {'total': 0, 'pending': 0})
            ctx[f"count_{ftype}"] = row['total']
            ctx[f"pending_{ftype}"] = row['pending']

        return ctx


class StatutoryFilingCreateView(RoleRequiredMixin, View):
    """Generate statutory filings from a payroll run."""
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "hr.add_statutoryfiling"

    def post(self, request, pk):
        payroll = get_object_or_404(PayrollRun, pk=pk)

        if payroll.status not in [PayrollStatus.APPROVED, PayrollStatus.PAID]:
            messages.error(request, "Payroll must be approved or paid to generate filings.")
            return redirect("hr:payroll_detail", pk=pk)

        entries = PayrollEntry.objects.filter(payroll_run=payroll)

        filing_types = [
            ("nssf", "NSSF Returns"),
            ("paye", "PAYE Returns"),
            ("heslb", "HESLB Loan Repayment"),
            ("nhif", "NHIF Contributions"),
        ]

        count = 0
        for ftype, flabel in filing_types:
            if StatutoryFiling.objects.filter(filing_type=ftype, payroll_run=payroll).exists():
                continue

            employee_total = 0
            employer_total = 0

            for entry in entries:
                if ftype == "nssf":
                    employee_total += float(entry.nssf_employee)
                    employer_total += float(entry.nssf_employer)
                elif ftype == "paye":
                    employee_total += float(entry.paye_tax)
                elif ftype == "heslb":
                    employee_total += float(entry.hesb_deduction)
                elif ftype == "nhif":
                    employee_total += float(entry.nhif_deduction)

            total = employee_total + employer_total
            if total == 0:
                continue

            from datetime import date
            StatutoryFiling.objects.create(
                filing_type=ftype,
                payroll_run=payroll,
                period_name=payroll.period_name,
                employee_contribution=employee_total,
                employer_contribution=employer_total,
                total_amount=total,
                due_date=date.today(),
                submitted_by=request.user,
            )
            count += 1

        if count:
            messages.success(request, f"Generated {count} statutory filings.")
        else:
            messages.info(request, "Statutory filings already exist for this payroll.")

        return redirect("hr:statutory_filing_list")


class StatutoryFilingSubmitView(RoleRequiredMixin, View):
    """Mark a statutory filing as submitted."""
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "hr.change_statutoryfiling"

    def post(self, request, pk):
        filing = get_object_or_404(StatutoryFiling, pk=pk)
        reference = request.POST.get("reference_number", "")

        filing.status = StatutoryFilingStatus.SUBMITTED
        filing.submitted_date = timezone.now().date()
        if reference:
            filing.reference_number = reference
        filing.save()

        log_event(
            actor=request.user, action_type="STATUTORY_FILING_SUBMITTED",
            model_name="StatutoryFiling", object_id=filing.pk,
            description=f"{filing.get_filing_type_display()} for {filing.period_name} submitted",
            request=self.request
        )
        messages.success(request, f"{filing.get_filing_type_display()} marked as submitted.")
        return redirect("hr:statutory_filing_list")


# 
# B7 — OFFBOARDING DETAIL
# 

class OffboardingDetailView(RoleRequiredMixin, DetailView):
    """View and manage offboarding for a staff member."""
    template_name = "hr/offboarding_detail.html"
    model = OffboardingRecord
    context_object_name = "offboarding"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL]
    required_permission = "hr.view_offboardingrecord"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["staff"] = self.object.staff
        ctx["form"] = OffboardingForm(instance=self.object)
        ctx["total_steps"] = 7
        steps_completed = sum([
            self.object.resignation_received,
            self.object.clearance_assets_returned,
            self.object.clearance_library,
            self.object.clearance_finance,
            self.object.final_payslip_generated,
            self.object.exit_interview_completed,
            self.object.system_account_deactivated,
        ])
        ctx["steps_completed"] = steps_completed
        ctx["progress_pct"] = int(steps_completed / 7 * 100)
        return ctx

    def post(self, request, pk):
        offboarding = get_object_or_404(OffboardingRecord, pk=pk)
        form = OffboardingForm(request.POST, instance=offboarding)

        if form.is_valid():
            form.save()

            # Auto-update status
            if offboarding.all_steps_completed:
                offboarding.status = OffboardingStatus.COMPLETED
                offboarding.completed_by = request.user
                offboarding.completed_at = timezone.now()
                offboarding.save()

                # Deactivate staff if not already
                staff = offboarding.staff
                staff.is_active = False
                departure_dt = offboarding.last_working_day or timezone.now().date()
                staff.departure_date = departure_dt
                staff.save()

                # Sync departure date to User model for login auto-deactivation
                departing_user = staff.user
                if departing_user:
                    departing_user.departure_date = departure_dt
                    departing_user.is_active = False
                    departing_user.save(update_fields=["departure_date", "is_active"])

                # Invalidate all active sessions for the departing staff user
                from django.contrib.sessions.models import Session
                for session in Session.objects.filter(expire_date__gte=timezone.now()):
                    try:
                        data = session.get_decoded()
                        if data.get("_auth_user_id") == str(staff.user_id):
                            session.delete()
                    except Exception:
                        pass

                messages.success(request, " Offboarding completed. Staff account deactivated.")
            else:
                offboarding.status = OffboardingStatus.IN_PROGRESS
                offboarding.save()
                messages.success(request, "Offboarding progress updated.")

            log_event(
                actor=request.user, action_type="OFFBOARDING_UPDATED",
                model_name="OffboardingRecord", object_id=offboarding.pk,
                description=f"Offboarding updated for {offboarding.staff.full_name}",
                request=self.request
            )
        else:
            messages.error(request, "Please correct the errors below.")

        return redirect("hr:offboarding_detail", pk=pk)


# 
# B8 — DASHBOARD WIDGETS
# 

class HRDashboardView(RoleRequiredMixin, TemplateView):
    """HR dashboard with staff overview, payroll, leave, and compliance widgets."""
    template_name = "hr/dashboard.html"
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.FINANCE_OFFICER]
    required_permission = "hr.view_staffprofile"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from datetime import date, timedelta

        today = date.today()

        # Staff Overview — single aggregated query instead of 4
        staff_stats = StaffProfile.objects.aggregate(
            total=Count('id'),
            active=Count('id', filter=Q(is_active=True)),
            teaching=Count('id', filter=Q(is_active=True, staff_category="teaching")),
            non_teaching=Count('id', filter=Q(is_active=True, staff_category="non_teaching")),
        )
        ctx["total_staff"] = staff_stats['total']
        ctx["active_staff"] = staff_stats['active']
        ctx["teaching_staff"] = staff_stats['teaching']
        ctx["non_teaching_staff"] = staff_stats['non_teaching']

        # Department breakdown — always show all departments
        dept_counts = StaffProfile.objects.filter(is_active=True).values("department").annotate(count=Count("id"))
        dept_map = {d["department"]: d["count"] for d in dept_counts}
        ctx["dept_breakdown"] = {
            "ECD": dept_map.get("ECD", 0),
            "Primary": dept_map.get("Primary", 0),
            "Lower Secondary": dept_map.get("Lower Secondary", 0),
            "Administration": dept_map.get("Administration", 0),
            "Unassigned": dept_map.get("", 0) + dept_map.get(None, 0),
        }

        # Contracts expiring soon
        ctx["expiring_contracts"] = StaffProfile.objects.filter(
            is_active=True, contract_end_date__isnull=False,
            contract_end_date__gte=today, contract_end_date__lte=today + timedelta(days=60),
        ).order_by("contract_end_date")

        # Onboarding
        ctx["onboarding_pending"] = StaffProfile.objects.filter(
            is_active=True, onboarding_completed=False
        ).exclude(onboarding_step=0).count()
        ctx["onboarding_not_started"] = StaffProfile.objects.filter(
            is_active=True, onboarding_step=0
        ).count()

        # Leave Summary
        ctx["leave_pending"] = LeaveRequest.objects.filter(status=LeaveStatus.PENDING).count()
        ctx["leave_approved_today"] = LeaveRequest.objects.filter(
            status=LeaveStatus.APPROVED, approved_at__date=today
        ).count()
        ctx["staff_on_leave"] = LeaveRequest.objects.filter(
            status=LeaveStatus.APPROVED,
            start_date__lte=today, end_date__gte=today,
        ).select_related("staff")

        # Payroll widget
        latest_payroll = PayrollRun.objects.filter(status=PayrollStatus.PAID).order_by("-period_start").first()
        if latest_payroll:
            ctx["latest_payroll"] = latest_payroll
            ctx["latest_payroll_total"] = latest_payroll.total_net_pay
        else:
            ctx["latest_payroll"] = None

        draft_payroll = PayrollRun.objects.filter(status=PayrollStatus.DRAFT).order_by("-created_at").first()
        ctx["draft_payroll"] = draft_payroll

        # Statutory compliance
        overdue_filings = StatutoryFiling.objects.filter(status=StatutoryFilingStatus.DRAFT).count()
        ctx["overdue_filings"] = overdue_filings

        # Offboarding
        ctx["active_offboardings"] = OffboardingRecord.objects.exclude(
            status=OffboardingStatus.COMPLETED
        ).count()

        # Rollover conflicts (unresolved)
        from hr.models import RolloverConflict
        ctx["rollover_conflicts"] = RolloverConflict.objects.filter(
            is_resolved=False
        ).select_related("teacher", "grade_class", "target_term", "source_assignment")[:10]
        ctx["rollover_conflict_count"] = RolloverConflict.objects.filter(is_resolved=False).count()

        # Upcoming birthdays
        ctx["birthdays_this_month"] = StaffProfile.objects.filter(
            is_active=True, date_of_birth__month=today.month
        ).order_by("date_of_birth")

        ctx["today"] = today
        return ctx


# 
# FINANCE DASHBOARD WIDGETS (extra context for Finance dashboard)
# 

class FinanceDashboardWidgetDataView(RoleRequiredMixin, View):
    """JSON endpoint for finance dashboard widgets."""
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "hr.view_staffprofile"

    def get(self, request):
        from datetime import date, timedelta
        today = date.today()

        data = {
            "overdue_count": PayrollRun.objects.filter(status=PayrollStatus.PAID).count(),
            "staff_on_payroll": StaffProfile.objects.filter(is_active=True).count(),
            "pending_leave": LeaveRequest.objects.filter(status=LeaveStatus.PENDING).count(),
        }

        return JsonResponse(data)


# 
# PAYROLL CONFIGURATION & PAYE TAX BAND MANAGEMENT
# 

class PayrollConfigUpdateView(RoleRequiredMixin, UpdateView):
    """View/update the singleton PayrollConfig (NSSF rates, working days)."""
    template_name = "hr/payroll_config_form.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "hr.change_payrollconfig"

    def get_object(self, queryset=None):
        from .models import PayrollConfig
        return PayrollConfig.get_config()

    def get_form(self, form_class=None):
        from .models import PayrollConfig
        class PayrollConfigForm(forms.ModelForm):
            class Meta:
                model = PayrollConfig
                fields = ["nssf_employee_rate", "nssf_employer_rate", "working_days_per_month"]
                widgets = {
                    "nssf_employee_rate": forms.NumberInput(attrs={"class": "hf2-input", "step": "0.01"}),
                    "nssf_employer_rate": forms.NumberInput(attrs={"class": "hf2-input", "step": "0.01"}),
                    "working_days_per_month": forms.NumberInput(attrs={"class": "hf2-input"}),
                }
        return PayrollConfigForm(instance=self.object)

    def get_success_url(self):
        return reverse("hr:payroll_config")

    def form_valid(self, form):
        obj = form.save(commit=False)
        obj.updated_by = self.request.user
        obj.save()
        log_event(
            actor=self.request.user, action_type="PAYROLL_CONFIG_UPDATED",
            model_name="PayrollConfig", object_id=1,
            description=f"Payroll config updated: NSSF ee={obj.nssf_employee_rate}%, er={obj.nssf_employer_rate}%, days={obj.working_days_per_month}",
            request=self.request
        )
        messages.success(self.request, "Payroll configuration updated successfully.")
        return super().form_valid(form)


class PAYETaxBandListView(RoleRequiredMixin, ListView):
    """List and manage PAYE tax bands."""
    template_name = "hr/paye_tax_band_list.html"
    context_object_name = "bands"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "hr.view_payetaxband"

    def get_queryset(self):
        from .models import PAYETaxBand
        return PAYETaxBand.objects.all().order_by("band_from")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .models import PAYETaxBand
        active_version = PAYETaxBand.objects.filter(is_active=True).values_list("version", flat=True).first()
        ctx["active_version"] = active_version or "None"
        ctx["all_versions"] = PAYETaxBand.objects.values_list("version", flat=True).distinct()
        return ctx


class PAYETaxBandCreateView(RoleRequiredMixin, View):
    """Add a new PAYE tax band or update existing ones."""
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "hr.add_payetaxband"

    def post(self, request):
        from .models import PAYETaxBand
        version = request.POST.get("version", "2024/2025")
        band_from = request.POST.get("band_from")
        band_to = request.POST.get("band_to") or None
        base_tax = request.POST.get("base_tax", 0)
        rate_pct = request.POST.get("rate_percentage")

        if not band_from or not rate_pct:
            messages.error(request, "Band lower bound and rate are required.")
            return redirect("hr:paye_tax_band_list")

        PAYETaxBand.objects.create(
            version=version,
            band_from=band_from,
            band_to=band_to,
            base_tax=base_tax,
            rate_percentage=rate_pct,
            is_active=True,
        )

        log_event(
            actor=request.user, action_type="PAYE_BAND_CREATED",
            model_name="PAYETaxBand",
            description=f"PAYE band added: TZS {band_from} to {band_to or '∞'} @ {rate_pct}%",
            request=self.request
        )
        messages.success(request, "PAYE tax band added.")
        return redirect("hr:paye_tax_band_list")


class PAYETaxBandDeleteView(RoleRequiredMixin, View):
    """Deactivate a PAYE tax band."""
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "hr.change_payetaxband"

    def post(self, request, pk):
        from .models import PAYETaxBand
        band = get_object_or_404(PAYETaxBand, pk=pk)
        band.is_active = False
        band.save()
        log_event(
            actor=request.user, action_type="PAYE_BAND_DEACTIVATED",
            model_name="PAYETaxBand", object_id=band.pk,
            description=f"PAYE band deactivated: TZS {band.band_from} to {band.band_to or '∞'}",
            request=self.request
        )
        messages.success(request, "PAYE tax band deactivated.")
        return redirect("hr:paye_tax_band_list")


class PayrollLockView(RoleRequiredMixin, View):
    """Lock a payroll run — makes it read-only after payment is confirmed."""
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "hr.change_payrollrun"

    def post(self, request, pk):
        from .models import PayrollRun, PayrollStatus
        payroll = get_object_or_404(PayrollRun, pk=pk)

        if payroll.status not in [PayrollStatus.APPROVED, PayrollStatus.PAID]:
            messages.error(request, "Payroll must be approved or paid before locking.")
            return redirect("hr:payroll_detail", pk=pk)

        payroll.status = PayrollStatus.LOCKED
        payroll.save(update_fields=["status", "updated_at"])

        log_event(
            actor=request.user, action_type="PAYROLL_LOCKED",
            model_name="PayrollRun", object_id=payroll.pk,
            description=f"Payroll {payroll.period_name} locked",
            request=self.request
        )
        messages.success(request, f"Payroll '{payroll.period_name}' has been locked (read-only).")
        return redirect("hr:payroll_detail", pk=pk)


# ──────────────────────────────────────────────
# Teacher Assignment — Edit & Delete
# ──────────────────────────────────────────────

class TeacherAssignmentEditView(RoleRequiredMixin, View):
    """Redirect to the user edit page's assignments tab."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ECD_HOD]
    required_permission = "hr.change_teacherclassassignment"

    def get(self, request, *args, **kwargs):
        assignment = get_object_or_404(TeacherClassAssignment, pk=kwargs["pk"])
        user_pk = assignment.teacher.user.pk
        return redirect(f"/accounts/edit/{user_pk}/#tab-assignments")

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)


class TeacherAssignmentDeleteView(RoleRequiredMixin, View):
    """Delete a single TeacherClassAssignment."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ECD_HOD]
    required_permission = "hr.delete_teacherclassassignment"

    def post(self, request, pk):
        ass = get_object_or_404(TeacherClassAssignment, pk=pk)
        teacher_pk = ass.teacher_id
        class_name = ass.grade_class.name
        teacher_name = ass.teacher.full_name

        if ass.is_class_teacher:
            ct_count = TeacherClassAssignment.objects.filter(
                grade_class=ass.grade_class,
                term=ass.term,
                is_class_teacher=True,
            ).count()
            if ct_count <= 1:
                messages.error(
                    request,
                    f'Cannot remove {teacher_name} as class teacher of {class_name}: '
                    f'they are the only class teacher assigned. '
                    f'Assign another class teacher first.'
                )
                return redirect("hr:staff_detail", pk=teacher_pk)

        ass.delete()
        log_event(
            actor=request.user, action_type="TEACHER_ASSIGNMENT_DELETED",
            model_name="TeacherClassAssignment",
            description=f"Deleted {teacher_name}'s assignment to {class_name}",
            request=request,
        )
        messages.success(request, f"Assignment removed: {teacher_name} → {class_name}.")
        return redirect("hr:staff_detail", pk=teacher_pk)


class PrintStaffIDCardView(RoleRequiredMixin, DetailView):
    """Printable staff ID card."""
    template_name = "hr/staff_id_card_print.html"
    model = StaffProfile
    context_object_name = "staff"
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER,
    ]
    required_permission = "hr.view_staffprofile"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        return ctx


class RolloverConflictResolveView(RoleRequiredMixin, View):
    """
    AJAX endpoint to resolve a rollover conflict.
    POST { conflict_id, action } where action is 'skip' or 'force'.
    """
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER]
    required_permission = "hr.change_teacherclassassignment"

    def post(self, request):
        import json
        from django.utils import timezone
        from hr.models import RolloverConflict, TeacherClassAssignment

        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, TypeError):
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        conflict_id = data.get("conflict_id")
        action = data.get("action")

        if action not in ("skip", "force"):
            return JsonResponse({"error": "Invalid action. Use 'skip' or 'force'."}, status=400)

        try:
            conflict = RolloverConflict.objects.get(pk=conflict_id, is_resolved=False)
        except RolloverConflict.DoesNotExist:
            return JsonResponse({"error": "Conflict not found or already resolved."}, status=404)

        if action == "skip":
            conflict.is_resolved = True
            conflict.resolved_by = request.user
            conflict.resolved_at = timezone.now()
            conflict.resolution_action = "skip"
            conflict.save(update_fields=["is_resolved", "resolved_by", "resolved_at", "resolution_action"])
            return JsonResponse({"ok": True, "action": "skip"})

        if action == "force":
            # Validate teacher still active and class exists
            teacher = conflict.teacher
            gc = conflict.grade_class
            term = conflict.target_term

            if not teacher.is_active:
                return JsonResponse({"error": f"{teacher.full_name} is no longer active."}, status=400)

            from academics.models import GradeClass
            if not GradeClass.objects.filter(pk=gc.pk).exists():
                return JsonResponse({"error": f"Class {gc.name} no longer exists."}, status=400)

            # Check for duplicate
            exists = TeacherClassAssignment.objects.filter(
                teacher=teacher, term=term, grade_class=gc,
            ).exists()
            if exists:
                return JsonResponse({"error": "Assignment already exists for this teacher/class/term."}, status=400)

            # Create the assignment
            TeacherClassAssignment.objects.create(
                teacher=teacher,
                term=term,
                grade_class=gc,
                subjects_taught=conflict.subjects_taught,
                is_class_teacher=conflict.is_class_teacher,
                is_assistant_class_teacher=False,
                repeat_across_terms=True,
            )

            conflict.is_resolved = True
            conflict.resolved_by = request.user
            conflict.resolved_at = timezone.now()
            conflict.resolution_action = "force"
            conflict.save(update_fields=["is_resolved", "resolved_by", "resolved_at", "resolution_action"])

            return JsonResponse({"ok": True, "action": "force"})

        return JsonResponse({"error": "Unknown action."}, status=400)
