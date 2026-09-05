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

from .models import WelfareObservation, WelfareSeverity, WelfareConcernType


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
    with search filters and charts."""
    template_name = "welfare/student_incidents.html"
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "welfare.view_welfareobservation"

    def dispatch(self, request, *args, **kwargs):
        # Let LoginRequiredMixin redirect anonymous users first
        auth_resp = super().dispatch(request, *args, **kwargs)
        if hasattr(auth_resp, 'status_code') and auth_resp.status_code in (302, 403):
            return auth_resp
        if not self.department:
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
        department = self._get_department()
        is_ecd = (department == Department.ECD)
        ctx["department"] = department
        ctx["is_ecd_welfare"] = is_ecd
        ctx["page_title"] = "Incidents"

        # Base queryset scoped by department and role
        qs = WelfareObservation.objects.select_related(
            'student', 'submitted_by', 'reviewed_by'
        ).order_by('-observation_date', '-created_at')
        
        # Scope to department classes
        dept_class_names = list(GradeClass.objects.filter(
            department=department
        ).values_list('name', flat=True))
        qs = qs.filter(student__class_name__in=dept_class_names)

        if role == UserRole.TEACHER:
            my_classes = get_teacher_assigned_classes(request.user)
            # Intersect teacher's classes with department classes
            valid_classes = list(set(my_classes) & set(dept_class_names))
            qs = qs.filter(student__class_name__in=valid_classes)

        # Apply search filters
        q = request.GET.get('q', '').strip()
        severity = request.GET.get('severity', '')
        concern = request.GET.get('concern', '')
        status = request.GET.get('status', '')
        class_name = request.GET.get('class_name', '')
        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')

        if q:
            from django.db.models import Q
            qs = qs.filter(
                Q(student__first_name__icontains=q) |
                Q(student__last_name__icontains=q) |
                Q(student__admission_no__icontains=q) |
                Q(observation_text__icontains=q)
            )
        if severity:
            qs = qs.filter(severity=severity)
        if concern:
            qs = qs.filter(concern_type=concern)
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

        # Full dataset (scoped by department) for stats
        base_qs = WelfareObservation.objects.filter(student__class_name__in=dept_class_names)

        total_count = base_qs.count()
        today = timezone.now().date()
        this_month_start = today.replace(day=1)
        this_month_count = base_qs.filter(observation_date__gte=this_month_start).count()
        last_month_end = this_month_start - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        last_month_count = base_qs.filter(
            observation_date__gte=last_month_start,
            observation_date__lte=last_month_end,
        ).count()
        open_count = base_qs.exclude(hod_status='resolved').count()
        resolved_count = base_qs.filter(hod_status='resolved').count()
        parent_contacted_count = base_qs.filter(parent_contacted=True).count()

        # Severity breakdown
        severity_data = dict(Counter(base_qs.values_list('severity', flat=True)))

        # Concern type breakdown
        concern_data = dict(Counter(base_qs.values_list('concern_type', flat=True)))

        # Monthly trend (last 6 months)
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
            base_qs.values('student__first_name', 'student__last_name', 'student__class_name', 'student_id')
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
        ctx['department'] = getattr(self, 'department', 'primary')
        ctx['is_ecd_welfare'] = (ctx['department'] == 'ecd')
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
            'severity_choices': WelfareSeverity.choices,
            'concern_choices': WelfareConcernType.choices,
            'status_choices': [('pending', 'Pending'), ('in_progress', 'In progress'), ('resolved', 'Resolved')],
            # Pass current filter values back to template
            'filter_q': q,
            'filter_severity': severity,
            'filter_concern': concern,
            'filter_status': status,
            'filter_class': class_name,
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
        UserRole.ECD_HOD, UserRole.PRIMARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.TEACHER
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

        # Scope by department
        department = self._get_department()
        dept_class_names = GradeClass.objects.filter(
            department=department
        ).values_list("name", flat=True)
        qs = qs.filter(student__class_name__in=dept_class_names)

        role = self.request.user.role
        if role == UserRole.TEACHER:
            qs = qs.filter(submitted_by=self.request.user)
        severity = self.request.GET.get("severity")
        if severity:
            qs = qs.filter(severity=severity)
        return qs

    def get_context_data(self, **kwargs):
        from academics.models import Department
        ctx = super().get_context_data(**kwargs)
        department = self._get_department()
        is_ecd = (department == Department.ECD)
        # NFR-PDPA-006: Log access to welfare list (sensitive data)
        from audit.view_audit import log_sensitive_access
        log_sensitive_access(
            self.request, "WelfareObservation", None, "welfare",
            description=f"Welfare list viewed ({'ECD' if is_ecd else 'Primary'} department)"
        )
        ctx["welfare_tab"] = "list"
        ctx["department"] = department
        ctx["is_ecd_welfare"] = is_ecd
        ctx["page_title"] = "ECD Welfare" if is_ecd else "Primary Welfare"
        ctx["can_see_hod_dashboard"] = self.request.user.has_perm("welfare.can_review_observation")
        qs = self.get_queryset()
        ctx["active_observations"] = qs.exclude(hod_status="resolved")
        ctx["resolved_observations"] = qs.filter(hod_status="resolved")[:15]
        ctx["severity_choices"] = WelfareSeverity.choices
        ctx["today_date"] = date.today().strftime("%d %B %Y")
        return ctx


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

    def get_context_data(self, **kwargs):
        from students.models import Student
        from academics.models import Department, GradeClass
        ctx = super().get_context_data(**kwargs)
        ctx["welfare_tab"] = "submit"
        ctx["severity_choices"] = WelfareSeverity.choices
        ctx["concern_type_choices"] = WelfareConcernType.choices
        ctx["can_see_hod_dashboard"] = self.request.user.has_perm("welfare.can_review_observation")

        department = self._get_department()
        is_ecd = (department == Department.ECD)
        ctx["department"] = department
        ctx["is_ecd_welfare"] = is_ecd
        ctx["page_title"] = "ECD Welfare" if is_ecd else "Primary Welfare"

        # Filter classes by department
        dept_classes = GradeClass.objects.filter(
            department=department
        ).values_list("name", flat=True).distinct()
        dept_class_names = sorted(dept_classes)

        all_students = Student.objects.filter(is_archived=False)
        if self.request.user.role == UserRole.TEACHER:
            my_classes = get_teacher_assigned_classes(self.request.user)
            # Intersect teacher's classes with department classes
            valid_classes = sorted(set(my_classes) & set(dept_class_names))
            students = all_students.filter(class_name__in=valid_classes)
            available_classes = valid_classes
        else:
            # HODs / Admins see only department classes
            students = all_students.filter(class_name__in=dept_class_names)
            available_classes = dept_class_names

        ctx["students"] = students.order_by("class_name", "last_name")
        ctx["available_classes"] = available_classes
        ctx["today_date"] = date.today().strftime("%d %B %Y")
        ctx["today_date_iso"] = date.today().strftime("%Y-%m-%d")
        from datetime import timedelta
        ctx["yesterday_iso"] = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        return ctx

    def post(self, request, *args, **kwargs):
        from students.models import Student
        from academics.models import Department
        from django.utils import timezone

        department = self._get_department()
        is_ecd = (department == Department.ECD)
        submit_redirect = "welfare:submit_ecd" if is_ecd else "welfare:submit_primary"

        student_id = request.POST.get("student")
        concern_type = request.POST.get("concern_type")
        severity = request.POST.get("severity", WelfareSeverity.LOW)
        obs_date = request.POST.get("observation_date") or date.today()
        obs_text = request.POST.get("observation_text", "").strip()
        action_taken = request.POST.get("action_taken", "").strip()

        # FR-WEL-002: Validate observation date — defaults to today, max 1 day backdate
        from datetime import timedelta
        try:
            obs_date_parsed = date.fromisoformat(obs_date) if isinstance(obs_date, str) else obs_date
        except (ValueError, TypeError):
            messages.error(request, "Invalid observation date format.")
            return redirect(submit_redirect)

        today = date.today()
        yesterday = today - timedelta(days=1)
        if obs_date_parsed < yesterday:
            messages.error(request, "Observation date cannot be more than 1 day in the past. Please correct the date.")
            return redirect(submit_redirect)
        if obs_date_parsed > today:
            messages.error(request, "Observation date cannot be in the future. Please correct the date.")
            return redirect(submit_redirect)
        obs_date = obs_date_parsed

        parent_contacted = request.POST.get("parent_contacted") == "on"
        parent_contact_datetime = None
        follow_up_required = request.POST.get("follow_up_required") == "on"
        follow_up_date = request.POST.get("follow_up_date") or None

        # FR-WEL-003: Capture the "Other" concern description and prepend to observation_text
        other_desc = request.POST.get("other_concern_description", "").strip()
        if concern_type == "other" and other_desc:
            obs_text = f"Other concern description: {other_desc}\n\n{obs_text}" if obs_text else f"Other concern description: {other_desc}"
        elif concern_type == "other" and not other_desc:
            messages.error(request, "Please describe the concern when selecting 'Other' as the concern type.")
            return redirect(submit_redirect)

        if not obs_text:
            messages.error(request, "Please provide observation details.")
            return redirect(submit_redirect)

        # FR-WEL-001: All mandatory fields must be completed
        if not concern_type:
            messages.error(request, "Please select a concern type.")
            return redirect(submit_redirect)
        if not severity:
            messages.error(request, "Please select a severity level.")
            return redirect(submit_redirect)
        if not action_taken:
            messages.error(request, "Please describe the action taken today.")
            return redirect(submit_redirect)
        if not parent_contacted:
            messages.error(request, "Please confirm whether the parent was contacted (toggle required).")
            return redirect(submit_redirect)

        if parent_contacted:
            contact_date = request.POST.get("parent_contact_date")
            contact_time = request.POST.get("parent_contact_time")
            if contact_date and contact_time:
                from datetime import datetime
                parent_contact_datetime = datetime.strptime(f"{contact_date} {contact_time}", "%Y-%m-%d %H:%M")
            else:
                parent_contact_datetime = timezone.now()

        try:
            student = Student.objects.get(pk=student_id)
        except Student.DoesNotExist:
            messages.error(request, "Invalid student selected.")
            return redirect(submit_redirect)

        # Validate student belongs to the correct department
        from academics.models import GradeClass
        student_dept = GradeClass.objects.filter(name=student.class_name).values_list("department", flat=True).first()
        if student_dept and student_dept != department:
            messages.error(request, f"Selected student does not belong to {'ECD' if is_ecd else 'Primary'} classes.")
            return redirect(submit_redirect)

        if request.user.role == UserRole.TEACHER:
            my_classes = get_teacher_assigned_classes(request.user)
            if student.class_name not in my_classes:
                messages.error(request, "You can only submit welfare observations for your assigned class(es).")
                return redirect(submit_redirect)

        obs = WelfareObservation.objects.create(
            student=student,
            submitted_by=request.user,
            concern_type=concern_type,
            severity=severity,
            observation_date=obs_date,
            observation_text=obs_text,
            action_taken=action_taken,
            parent_contacted=parent_contacted,
            parent_contact_datetime=parent_contact_datetime,
            follow_up_required=follow_up_required,
            follow_up_date=follow_up_date,
            is_locked=severity == WelfareSeverity.CRITICAL,
        )

        # FR-AUD-003: Log welfare observation submission
        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="welfare_observation_created",
            model_name="WelfareObservation",
            object_id=obs.pk,
            description=f"Welfare observation created for {student} - {severity} severity {concern_type}",
            after={
                "student_id": student.pk,
                "concern_type": concern_type,
                "severity": severity,
                "observation_date": str(obs_date),
                "parent_contacted": parent_contacted,
                "follow_up_required": follow_up_required,
            },
            request=request
        )

        # Auto-escalate High/Critical → notify relevant HOD (+ HOS if Critical)
        if severity in [WelfareSeverity.HIGH, WelfareSeverity.CRITICAL]:
            _escalate_severity(obs, request, severity)
            try:
                from tasks.services import generate_welfare_alert_task
                generate_welfare_alert_task(obs)
            except Exception:
                pass
            hod_name = "ECD HOD" if is_ecd else "Primary HOD"
            if severity == WelfareSeverity.CRITICAL:
                messages.warning(
                    request,
                    f"Critical observation submitted and escalated to {hod_name} and HOS."
                )
            else:
                messages.warning(
                    request,
                    f"High severity observation submitted and escalated to {hod_name}."
                )
        else:
            messages.success(request, "Welfare observation submitted.")

        if follow_up_required:
            try:
                from tasks.services import generate_welfare_followup_task
                generate_welfare_followup_task(obs)
            except Exception:
                pass

        return redirect("welfare:detail", pk=obs.pk)


def _escalate_severity(obs: WelfareObservation, request, severity):
    """Send in-app notifications and emails to relevant roles according to FRD escalation rules.
    Determines ECD vs Primary from the student's class_name via GradeClass."""
    from communications.email_service import dispatch_notification
    from communications.models import Notification, NotificationCategory
    from users.models import User
    from academics.models import Department, GradeClass
    from django.utils import timezone
    import datetime

    # Determine department from student's class
    dept = GradeClass.objects.filter(name=obs.student.class_name).values_list("department", flat=True).first()
    if dept == Department.ECD:
        hod_role = UserRole.ECD_HOD
        hod_label = "ECD HOD"
    else:
        hod_role = UserRole.PRIMARY_HOD
        hod_label = "Primary HOD"

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

    if roles_to_notify:
        recipients = User.objects.filter(
            role__in=roles_to_notify,
            is_active=True,
        )

        for user in recipients:
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
    model = WelfareObservation
    context_object_name = "obs"
    allowed_roles = [UserRole.ECD_HOD, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.TEACHER]
    required_permission = "welfare.view_welfareobservation"

    def get_object(self, queryset=None):
        obs = super().get_object(queryset)
        # FR-WEL-003: Block cross-department access to welfare detail
        _assert_observation_department_match(self.request.user, obs)
        return obs

    def get_queryset(self):
        qs = super().get_queryset().select_related("student", "submitted_by", "reviewed_by")
        if self.request.user.role == UserRole.TEACHER:
            qs = qs.filter(submitted_by=self.request.user)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # NFR-PDPA-006: Log access to welfare record
        from audit.view_audit import log_sensitive_access
        log_sensitive_access(
            self.request, "WelfareObservation", self.object.pk, "welfare",
            description=f"Welfare record viewed for {self.object.student} (severity: {self.object.severity})"
        )
        can_review = self.request.user.has_perm("welfare.can_review_observation")
        ctx["can_see_hod_dashboard"] = can_review
        ctx["welfare_tab"] = "detail"
        ctx["can_review"] = can_review
        ctx["is_incident_locked"] = self.object.hod_status == "resolved"
        
        # Acknowledgment check
        from .models import WelfareAcknowledgment
        ctx["user_acknowledged"] = WelfareAcknowledgment.objects.filter(
            observation=self.object, user=self.request.user
        ).exists()
        ctx["needs_acknowledgment"] = self.object.severity in [WelfareSeverity.HIGH, WelfareSeverity.CRITICAL]
        ctx["user"] = self.request.user
        
        # Check if this is an ECD welfare observation
        from academics.models import GradeClass, Department
        student_dept = GradeClass.objects.filter(name=self.object.student.class_name).values_list("department", flat=True).first()
        ctx["is_ecd_welfare"] = (student_dept == Department.ECD)
        return ctx


class WelfareEditView(RoleRequiredMixin, View):
    """
    FR-WEL-002: Teacher can edit their own welfare observation within 24 hours,
    before HOD review. Only action_taken and follow_up fields are editable.
    Critical entries are locked immediately and cannot be edited.
    """
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "welfare.change_welfareobservation"

    def get(self, request, pk):
        from django.shortcuts import render
        obs = get_object_or_404(WelfareObservation, pk=pk)
        # FR-WEL-003: Block cross-department edit access
        _assert_observation_department_match(request.user, obs)

        # Only the submitting teacher can edit (SA can edit any)
        if request.user.role == UserRole.TEACHER and obs.submitted_by != request.user:
            raise PermissionDenied("You can only edit your own welfare observations.")

        # Critical entries are locked immediately
        if obs.is_locked or obs.severity == WelfareSeverity.CRITICAL:
            messages.error(request, "Critical welfare observations cannot be edited after submission.")
            return redirect("welfare:detail", pk=pk)

        # Already reviewed by HOD — no longer editable
        if obs.hod_status != "pending":
            messages.error(request, "This observation has been reviewed and can no longer be edited.")
            return redirect("welfare:detail", pk=pk)

        # 24-hour window check
        from django.utils import timezone as _tz
        from datetime import timedelta
        if obs.created_at < _tz.now() - timedelta(hours=24):
            messages.error(request, "The 24-hour edit window has expired.")
            return redirect("welfare:detail", pk=pk)

        ctx = {
            "obs": obs,
            "severity_choices": WelfareSeverity.choices,
            "concern_type_choices": WelfareConcernType.choices,
        }
        return render(request, "welfare/edit.html", ctx)

    def post(self, request, pk):
        obs = get_object_or_404(WelfareObservation, pk=pk)
        # FR-WEL-003: Block cross-department edit access
        _assert_observation_department_match(request.user, obs)

        # Only the submitting teacher can edit (SA can edit any)
        if request.user.role == UserRole.TEACHER and obs.submitted_by != request.user:
            raise PermissionDenied("You can only edit your own welfare observations.")

        # Critical entries are locked immediately
        if obs.is_locked or obs.severity == WelfareSeverity.CRITICAL:
            messages.error(request, "Critical welfare observations cannot be edited after submission.")
            return redirect("welfare:detail", pk=pk)

        # Already reviewed by HOD — no longer editable
        if obs.hod_status != "pending":
            messages.error(request, "This observation has been reviewed and can no longer be edited.")
            return redirect("welfare:detail", pk=pk)

        # 24-hour window check
        from django.utils import timezone as _tz
        from datetime import timedelta
        if obs.created_at < _tz.now() - timedelta(hours=24):
            messages.error(request, "The 24-hour edit window has expired.")
            return redirect("welfare:detail", pk=pk)

        # Only action_taken and follow_up fields are editable
        action_taken = request.POST.get("action_taken", "").strip()
        follow_up_required = request.POST.get("follow_up_required") == "on"
        follow_up_date = request.POST.get("follow_up_date") or None

        if not action_taken:
            messages.error(request, "Please describe the action taken today.")
            return redirect("welfare:edit", pk=pk)

        before_action = obs.action_taken
        before_follow_up = obs.follow_up_required
        before_follow_up_date = obs.follow_up_date

        obs.action_taken = action_taken
        obs.follow_up_required = follow_up_required
        obs.follow_up_date = follow_up_date
        obs.save(update_fields=["action_taken", "follow_up_required", "follow_up_date", "updated_at"])

        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="welfare_observation_edited",
            model_name="WelfareObservation",
            object_id=obs.pk,
            description=f"Teacher edited welfare observation for {obs.student}",
            before={
                "action_taken": before_action,
                "follow_up_required": before_follow_up,
                "follow_up_date": str(before_follow_up_date) if before_follow_up_date else None,
            },
            after={
                "action_taken": action_taken,
                "follow_up_required": follow_up_required,
                "follow_up_date": str(follow_up_date) if follow_up_date else None,
            },
            request=request,
        )

        messages.success(request, "Welfare observation updated.")
        return redirect("welfare:detail", pk=pk)


class WelfareHODDashboardView(DepartmentScopedMixin, RoleRequiredMixin, TemplateView):
    """HOD welfare dashboard - cross-module overview scoped by department.
    Pulls real data from welfare, discipline, and attendance modules.
    HOS users additionally see a per-ECD-class aggregate breakdown."""
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
        ctx["welfare_tab"] = "hod_dashboard"
        ctx["can_see_hod_dashboard"] = True  # This view is only accessible to HOD+ roles

        department = self._get_department()
        is_ecd = (department == Department.ECD)
        ctx["department"] = department
        ctx["is_ecd_welfare"] = is_ecd
        ctx["page_title"] = "ECD Welfare" if is_ecd else "Primary Welfare"
        ctx["dept_label"] = "ECD" if is_ecd else "Primary"

        # FR-WEL-008: HOS sees per-ECD-class aggregate breakdown
        is_hos = self.request.user.role == UserRole.HEAD_OF_SCHOOL
        ctx["is_hos_view"] = is_hos
        if is_hos and is_ecd:
            ecd_classes = list(GradeClass.objects.filter(
                department=Department.ECD
            ).values_list("name", flat=True))
            class_grid = []
            for cls_name in ecd_classes:
                cls_obs = WelfareObservation.objects.filter(student__class_name=cls_name)
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

        # Scope to department's classes
        dept_class_names = list(GradeClass.objects.filter(
            department=department
        ).values_list("name", flat=True))

        today = timezone.now().date()
        week_start = today - datetime.timedelta(days=today.weekday())
        week_end = week_start + datetime.timedelta(days=6)
        month_start = today.replace(day=1)

        # ── WELFARE DATA ──────────────────────────────────────────────
        week_obs = WelfareObservation.objects.filter(
            observation_date__gte=week_start,
            observation_date__lte=week_end,
            student__class_name__in=dept_class_names,
        ).select_related("student", "submitted_by")

        ctx["total_week_entries"] = week_obs.count()
        ctx["severity_counts"] = dict(Counter(week_obs.values_list("severity", flat=True)))
        ctx["concern_type_counts"] = dict(Counter(week_obs.values_list("concern_type", flat=True)))

        # Welfare totals for KPI cards
        welfare_total = WelfareObservation.objects.filter(
            student__class_name__in=dept_class_names
        ).count()
        welfare_open = WelfareObservation.objects.filter(
            hod_status__in=["pending", "in_progress"],
            student__class_name__in=dept_class_names,
        ).count()
        welfare_critical = WelfareObservation.objects.filter(
            severity__in=["high", "critical"],
            hod_status__in=["pending", "in_progress"],
            student__class_name__in=dept_class_names,
        ).count()
        welfare_resolved = WelfareObservation.objects.filter(
            hod_status="resolved",
            student__class_name__in=dept_class_names,
        ).count()
        welfare_this_month = WelfareObservation.objects.filter(
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
        ctx["open_entries"] = WelfareObservation.objects.filter(
            hod_status__in=["pending", "in_progress"],
            student__class_name__in=dept_class_names,
        ).select_related("student", "submitted_by").order_by("-severity", "-observation_date")[:15]

        # FR-WEL-005: Children with open welfare concerns
        children_with_open = Student.objects.filter(
            welfare_observations__hod_status__in=["pending", "in_progress"],
            class_name__in=dept_class_names,
        ).distinct()

        children_data = []
        # Batch-fetch latest open observation per child to avoid N+1
        open_obs = WelfareObservation.objects.filter(
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
        ctx["uncontacted_critical"] = WelfareObservation.objects.filter(
            severity__in=["high", "critical"],
            parent_contacted=False,
            created_at__lt=one_day_ago,
            student__class_name__in=dept_class_names,
        ).select_related("student", "submitted_by")

        ctx["resolved_entries"] = WelfareObservation.objects.filter(
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

            w_count = WelfareObservation.objects.filter(
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
