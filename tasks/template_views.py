"""
Task Module — Template-based HTML views (Django class-based views).
Complements the existing DRF API views with server-rendered pages.
"""

from datetime import date as date_cls, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q, Count
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404
from django.utils import timezone
from django.views.generic import TemplateView, View

from core.permissions import RoleRequiredMixin
from users.models import UserRole

from .models import Task, TaskHistory, TaskComment, STATUS_CHOICES, PRIORITY_CHOICES


#  Helper: role-based queryset 

def _user_tasks_qs(user):
    """Return the task queryset visible to this user (RBAC-filtered).

    Role visibility:
    - SUPER_ADMIN / HEAD_OF_SCHOOL : all tasks (school-wide oversight)
    - PRIMARY_HOD / ECD_HOD        : own tasks + department members' tasks + tasks they created
    - TEACHER                       : own tasks + attendance alerts for their students
    - ADMIN_OFFICER                 : own tasks + admissions tasks
    - FINANCE_OFFICER               : own tasks + finance tasks
    - PARENT                        : own tasks + welfare/attendance tasks about their children
    """
    if user.role in (UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL):
        # School leaders see everything
        return Task.objects.all()

    if user.role in (UserRole.PRIMARY_HOD, UserRole.ECD_HOD):
        from users.models import User as U
        dept = getattr(getattr(user, 'staff_profile', None), 'department', None)
        dept_users = U.objects.none()
        if dept:
            dept_users = U.objects.filter(staff_profile__department=dept)
        return Task.objects.filter(
            Q(assigned_to=user)
            | Q(assigned_to__in=dept_users)
            | Q(created_by=user)
        )

    if user.role == UserRole.TEACHER:
        # Teachers see their own tasks + attendance alerts for students in their classes
        base = Task.objects.filter(Q(assigned_to=user) | Q(created_by=user))
        # Find classes this teacher is assigned to (via TeacherClassAssignment)
        try:
            from hr.models import TeacherClassAssignment
            from academics.utils import get_current_term
            current_term = get_current_term()
            tc_qs = TeacherClassAssignment.objects.filter(teacher__user=user)
            if current_term:
                tc_qs = tc_qs.filter(term=current_term)
            my_class_names = list(tc_qs.values_list('grade_class__name', flat=True).distinct())
        except Exception:
            my_class_names = []
        if my_class_names:
            base = base | Task.objects.filter(
                task_type='attendance_alert',
                metadata__class_name__in=my_class_names,
            )
        return base

    if user.role == UserRole.ADMIN_OFFICER:
        # Admin officers see their tasks + all admissions-related tasks
        return Task.objects.filter(
            Q(assigned_to=user)
            | Q(created_by=user)
            | Q(task_type__in=[
                'assessment_scheduling', 'enrolment_processing',
                'applicant_followup', 'admission_review',
            ])
        )

    if user.role == UserRole.FINANCE_OFFICER:
        # Finance officers see their tasks + all finance-related tasks
        return Task.objects.filter(
            Q(assigned_to=user)
            | Q(created_by=user)
            | Q(task_type__in=[
                'payment_matching', 'overdue_collection',
                'fee_verification', 'sibling_discount',
                'assessment_fee', 'fee_payment_reminder',
            ])
        )

    if user.role == UserRole.PARENT:
        # Parents see tasks about their children (welfare, attendance, finance)
        # Link: Student → StudentGuardian → ParentGuardian → User
        from students.models import Student, StudentGuardian, ParentGuardian
        try:
            guardian = ParentGuardian.objects.filter(user=user).first()
            if guardian:
                child_ids = StudentGuardian.objects.filter(
                    guardian=guardian
                ).values_list('student_id', flat=True)
                my_children = Student.objects.filter(pk__in=child_ids, is_archived=False, status='active')
            else:
                my_children = Student.objects.none()
        except Exception:
            my_children = Student.objects.none()
        child_names = [c.get_full_name() for c in my_children]
        child_pks = list(my_children.values_list('pk', flat=True))
        base = Task.objects.filter(Q(assigned_to=user) | Q(created_by=user))
        if child_names or child_pks:
            from django.contrib.contenttypes.models import ContentType
            student_ct = ContentType.objects.get_for_model(Student)
            base = base | Task.objects.filter(
                Q(task_type__in=[
                    'welfare_alert', 'welfare_followup', 'attendance_alert',
                    'fee_payment_reminder', 'overdue_collection',
                    'discipline_contact', 'report_card_ready', 'open_invoice',
                ])
                & (
                    Q(content_type=student_ct, object_id__in=child_pks)
                    | Q(metadata__student_name__in=child_names)
                    | Q(metadata__student_id__in=child_pks)
                )
            )
        return base

    # Default: own tasks only
    return Task.objects.filter(Q(assigned_to=user) | Q(created_by=user))


#  Main Dashboard 

class TaskDashboardView(RoleRequiredMixin, TemplateView):
    """Main tasks dashboard — the 'Tasks & Todo' page."""
    template_name = "tasks/dashboard.html"
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.TEACHER, UserRole.FINANCE_OFFICER,
        UserRole.ADMIN_OFFICER, UserRole.PARENT,
    ]
    required_permission = "tasks.view_task"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        today = timezone.now().date()

        qs = _user_tasks_qs(user).filter(status__in=['pending', 'in_progress'])

        # Filters from GET params
        status_filter = self.request.GET.get('status', '')
        priority_filter = self.request.GET.get('priority', '')
        category_filter = self.request.GET.get('category', '')

        if status_filter:
            qs = qs.filter(status=status_filter)
        if priority_filter:
            qs = qs.filter(priority=priority_filter)
        if category_filter:
            # Category maps to task_type prefix
            category_map = {
                'academics': ['lesson_plan_review', 'lesson_plan_submission', 'grade_approval',
                              'grade_submission', 'report_signoff', 'report_comment', 'at_risk_alert',
                              'assessment_entry', 'ecd_report_submission', 'compliance_check',
                              'department_review', 'attendance_alert', 'lesson_plan_compliance',
                              'report_card_ready'],
                'admissions': ['admission_review', 'assessment_scheduling', 'enrolment_processing',
                               'applicant_followup'],
                'finance': ['payment_matching', 'overdue_collection', 'fee_verification',
                            'sibling_discount', 'assessment_fee', 'fee_payment_reminder',
                            'open_invoice'],
                'welfare': ['welfare_alert', 'welfare_followup', 'ecd_attendance',
                           'discipline_contact'],
                'communications': ['weekly_focus_submission'],
                'hr': ['compliance_check'],
            }
            types = category_map.get(category_filter, [])
            if types:
                qs = qs.filter(task_type__in=types)

        # Stats (unfiltered)
        base_qs = _user_tasks_qs(user)
        all_active = base_qs.filter(status__in=['pending', 'in_progress'])

        ctx['stats'] = {
            'total_pending': all_active.count(),
            'due_this_week': all_active.filter(
                due_date__lte=timezone.now() + timedelta(days=5),
                due_date__gte=timezone.now(),
            ).count(),
            'overdue': all_active.filter(is_overdue=True).count(),
        }

        # Categorize tasks
        overdue_tasks = qs.filter(is_overdue=True).select_related('assigned_to', 'created_by')[:20]
        due_this_week = qs.filter(
            is_overdue=False,
            due_date__lte=timezone.now() + timedelta(days=5),
            due_date__gte=timezone.now(),
        ).select_related('assigned_to', 'created_by').order_by('due_date')[:20]

        upcoming_tasks = qs.filter(
            is_overdue=False,
            due_date__gt=timezone.now() + timedelta(days=5),
        ).select_related('assigned_to', 'created_by').order_by('due_date')[:20]

        all_tasks = qs.select_related('assigned_to', 'created_by').order_by(
            '-priority', 'due_date'
        )

        # Priority ordering: critical=0, high=1, medium=2, low=3
        priority_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        all_tasks_list = sorted(all_tasks, key=lambda t: (
            priority_order.get(t.priority, 4),
            t.due_date or timezone.now() + timedelta(days=365),
        ))

        # Filter categories based on user role
        role = user.role
        allowed_categories = []
        allowed_category_keys = []
        
        if role in (UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL):
            allowed_categories = [
                ('academics', 'Academics'),
                ('admissions', 'Admissions'),
                ('finance', 'Finance'),
                ('welfare', 'Welfare'),
                ('communications', 'Communications'),
                ('hr', 'HR'),
            ]
        elif role in (UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.TEACHER):
            allowed_categories = [
                ('academics', 'Academics'),
                ('welfare', 'Welfare'),
                ('communications', 'Communications'),
            ]
        elif role == UserRole.ADMIN_OFFICER:
            allowed_categories = [
                ('admissions', 'Admissions'),
                ('academics', 'Academics'),
            ]
        elif role == UserRole.FINANCE_OFFICER:
            allowed_categories = [
                ('finance', 'Finance'),
            ]
        elif role == UserRole.PARENT:
            allowed_categories = [
                ('welfare', 'Welfare'),
                ('finance', 'Finance'),
                ('academics', 'Academics'),
            ]
        
        allowed_category_keys = [cat[0] for cat in allowed_categories]
        
        # Reset category filter if it's not allowed
        if category_filter and category_filter not in allowed_category_keys:
            category_filter = ''
            # Also reset qs to not include the invalid filter
            qs = _user_tasks_qs(user).filter(status__in=['pending', 'in_progress'])
            if status_filter:
                qs = qs.filter(status=status_filter)
            if priority_filter:
                qs = qs.filter(priority=priority_filter)
            
            # Re-calculate task lists with the reset qs
            overdue_tasks = qs.filter(is_overdue=True).select_related('assigned_to', 'created_by')[:20]
            due_this_week = qs.filter(
                is_overdue=False,
                due_date__lte=timezone.now() + timedelta(days=5),
                due_date__gte=timezone.now(),
            ).select_related('assigned_to', 'created_by').order_by('due_date')[:20]
        
            upcoming_tasks = qs.filter(
                is_overdue=False,
                due_date__gt=timezone.now() + timedelta(days=5),
            ).select_related('assigned_to', 'created_by').order_by('due_date')[:20]
        
            all_tasks = qs.select_related('assigned_to', 'created_by').order_by(
                '-priority', 'due_date'
            )
        
            # Re-sort all_tasks
            priority_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
            all_tasks_list = sorted(all_tasks, key=lambda t: (
                priority_order.get(t.priority, 4),
                t.due_date or timezone.now() + timedelta(days=365),
            ))
        
        ctx.update({
            'overdue_tasks': overdue_tasks,
            'due_this_week': due_this_week,
            'upcoming_tasks': upcoming_tasks,
            'all_tasks': all_tasks_list[:100],
            'task_count': len(all_tasks_list),
            'status_choices': STATUS_CHOICES,
            'priority_choices': PRIORITY_CHOICES,
            'current_status': status_filter,
            'current_priority': priority_filter,
            'current_category': category_filter,
            'categories': allowed_categories,
        })

        # Calculate assessment completion % for each task
        _GRADE_KEYS = [
            'math_eot','english_eot','science_eot','average_eot',
            'math_cam','english_cam','science_cam','average_cam',
        ]
        _TRAIT_KEYS = [
            'wh_follows_directions','wh_works_independently','wh_not_disturb','wh_completes_neatly',
            'pt_honest','pt_flexibility','pt_attention','pt_creativity',
            'st_courteous','st_self_control','st_respects','st_relates_well',
        ]
        _total = len(_GRADE_KEYS) + len(_TRAIT_KEYS)
        task_progress = {}
        for t in all_tasks_list:
            if t.task_type == 'assessment_report' and t.status != 'completed':
                data = t.metadata.get('assessment_data', {})
                filled = 0
                for k in _GRADE_KEYS:
                    if data.get('grade_%s' % k, ''):
                        filled += 1
                for k in _TRAIT_KEYS:
                    if data.get('trait_%s' % k, ''):
                        filled += 1
                task_progress[t.pk] = round((filled / _total) * 100) if _total else 0
        ctx['task_progress'] = task_progress

        ctx['today_date'] = today.strftime('%A, %d %B %Y')
        return ctx


#  Task Actions (HTMX-compatible) 

class TaskMarkCompleteView(RoleRequiredMixin, View):
    """Mark a task as completed. Supports HTMX requests."""
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD, UserRole.TEACHER, UserRole.FINANCE_OFFICER,
        UserRole.ADMIN_OFFICER, UserRole.PARENT,
    ]

    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk)
        from users.models import UserRole
        _ADMIN_ROLES = {UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL}
        if request.user.role not in _ADMIN_ROLES:
            user_qs = _user_tasks_qs(request.user)
            if not user_qs.filter(pk=pk).exists():
                messages.error(request, "You can only complete your own tasks.")
                return redirect('tasks:dashboard')

        task.mark_completed()
        TaskHistory.objects.create(
            task=task, action='completed',
            changed_by=request.user, comment="Marked as completed"
        )

        if request.headers.get('HX-Request') == 'true':
            from django.http import HttpResponse
            return HttpResponse('')

        messages.success(request, f"Task completed: {task.title}")
        return redirect('tasks:dashboard')


class TaskTakeActionView(RoleRequiredMixin, View):
    """Take action on a task (approve, reject, etc.)."""
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD, UserRole.TEACHER, UserRole.FINANCE_OFFICER,
        UserRole.ADMIN_OFFICER, UserRole.PARENT,
    ]

    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk)
        from users.models import UserRole
        _ADMIN_ROLES = {UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL}
        if request.user.role not in _ADMIN_ROLES:
            user_qs = _user_tasks_qs(request.user)
            if not user_qs.filter(pk=pk).exists():
                messages.error(request, "Only the assigned user can take actions.")
                return redirect('tasks:dashboard')
        if task.assigned_to != request.user:
            messages.error(request, "Only the assigned user can take actions.")
            return redirect('tasks:dashboard')

        action_name = request.POST.get('action', '').strip()
        comment_text = request.POST.get('comment', '').strip()

        if action_name not in task.actions_available:
            messages.error(request, f"Action '{action_name}' is not available for this task.")
            return redirect('tasks:dashboard')

        # Execute action
        if action_name == 'mark_complete':
            task.mark_completed()
        elif action_name == 'reject':
            task.status = 'completed'
            task.metadata['rejection_reason'] = comment_text
            task.save()
        else:
            task.status = 'in_progress'
            task.save()

        TaskHistory.objects.create(
            task=task, action='action_taken',
            old_value=task.status, new_value=action_name,
            changed_by=request.user, comment=comment_text,
        )

        # Redirect to related object if available
        module_url = task.metadata.get('module_url', '')
        if module_url:
            messages.success(request, f"Action '{action_name}' completed for: {task.title}")
            return redirect(module_url)

        messages.success(request, f"Action completed: {task.title}")
        return redirect('tasks:dashboard')


class TaskDeferView(RoleRequiredMixin, View):
    """Defer a task to a later date."""
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD, UserRole.TEACHER, UserRole.FINANCE_OFFICER,
        UserRole.ADMIN_OFFICER, UserRole.PARENT,
    ]

    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk)
        from users.models import UserRole
        _ADMIN_ROLES = {UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL}
        if request.user.role not in _ADMIN_ROLES:
            user_qs = _user_tasks_qs(request.user)
            if not user_qs.filter(pk=pk).exists():
                messages.error(request, "You can only defer your own tasks.")
                return redirect('tasks:dashboard')

        new_date = request.POST.get('defer_date')
        reason = request.POST.get('reason', '').strip()

        if not new_date:
            messages.error(request, "Please select a deferred date.")
            return redirect('tasks:dashboard')

        from datetime import datetime
        try:
            new_due = datetime.strptime(new_date, '%Y-%m-%d')
            new_due = timezone.make_aware(new_due.replace(hour=17, minute=0))
        except ValueError:
            messages.error(request, "Invalid date format.")
            return redirect('tasks:dashboard')

        task.defer_until(new_due, reason)

        if request.headers.get('HX-Request') == 'true':
            from django.http import HttpResponse
            return HttpResponse('')

        messages.success(request, f"Task deferred to {new_date}: {task.title}")
        return redirect('tasks:dashboard')


#  API endpoints for topbar badge 

class TaskCountsAPIView(RoleRequiredMixin, View):
    """JSON endpoint for the topbar task badge (called via JS fetch)."""
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD, UserRole.TEACHER, UserRole.FINANCE_OFFICER,
        UserRole.ADMIN_OFFICER, UserRole.PARENT,
    ]
    required_permission = "tasks.view_task"

    def get(self, request):
        user = request.user
        qs = _user_tasks_qs(user).filter(status__in=['pending', 'in_progress'])

        overdue = qs.filter(is_overdue=True).count()
        pending = qs.count()

        return JsonResponse({
            'pending': pending,
            'overdue': overdue,
        })


#  Task Detail (inline, for HTMX) 

class TaskDetailView(RoleRequiredMixin, View):
    """Task detail page. Serves a fragment for HTMX or a full page otherwise."""
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD, UserRole.TEACHER, UserRole.FINANCE_OFFICER,
        UserRole.ADMIN_OFFICER, UserRole.PARENT,
    ]
    required_permission = "tasks.view_task"

    def get(self, request, pk):
        task = get_object_or_404(
            Task.objects.select_related('assigned_to', 'created_by'), pk=pk
        )

        from users.models import UserRole
        _ADMIN_ROLES = {UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL}
        if request.user.role not in _ADMIN_ROLES:
            user_qs = _user_tasks_qs(request.user)
            if not user_qs.filter(pk=pk).exists():
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied("You do not have access to this task.")

        history = task.history.all().select_related('changed_by')[:10]
        comments = task.comments.all().select_related('author')

        ctx = {
            'task': task,
            'history': history,
            'comments': comments,
        }

        if request.headers.get('HX-Request'):
            template = 'tasks/_task_detail.html'
        else:
            template = 'tasks/task_detail.html'

        from django.shortcuts import render
        return render(request, template, ctx)


#  Seed existing tasks 

class SeedTasksView(RoleRequiredMixin, View):
    """
    One-click task seeder — scans all modules and creates tasks
    for existing pending items. Safe to run multiple times (dedup).
    Only accessible to super admin.
    """
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "tasks.add_task"

    def get(self, request):
        from .seeder import seed_all
        results = seed_all()
        total = results.pop('total', 0)

        from django.shortcuts import render
        return render(request, 'tasks/seed_results.html', {
            'results': results,
            'total': total,
        })

    def post(self, request):
        return self.get(request)


class AssessmentReportSubmitView(RoleRequiredMixin, View):
    """Handle primary assessment report submission from the tasks page."""
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD, UserRole.TEACHER, UserRole.ADMIN_OFFICER,
    ]

    EOT_SUBJECTS = [
        {"key": "math_eot", "label": "Mathematics"},
        {"key": "english_eot", "label": "English"},
        {"key": "science_eot", "label": "Science"},
    ]
    CAMBRIDGE_SUBJECTS = [
        {"key": "math_cam", "label": "Mathematics Paper 1"},
        {"key": "english_cam", "label": "English Paper 1"},
        {"key": "science_cam", "label": "Science Paper 1"},
    ]
    WORK_HABITS = [
        {"key": "wh_follows_directions", "label": "Follows directions"},
        {"key": "wh_works_independently", "label": "Works well independently"},
        {"key": "wh_not_disturb", "label": "Does not disturb others"},
        {"key": "wh_completes_neatly", "label": "Completes work neatly"},
    ]
    PERSONAL_TRAITS = [
        {"key": "pt_honest", "label": "Is honest"},
        {"key": "pt_flexibility", "label": "Displays flexibility"},
        {"key": "pt_attention", "label": "Attention span"},
        {"key": "pt_creativity", "label": "Displays creativity"},
    ]
    SOCIAL_TRAITS = [
        {"key": "st_courteous", "label": "Is courteous"},
        {"key": "st_self_control", "label": "Exhibits self-control"},
        {"key": "st_respects", "label": "Respects authority"},
        {"key": "st_relates_well", "label": "Relates well with others"},
    ]

    def _build_section(self, subjects, data):
        result = []
        for s in subjects:
            result.append({
                "key": s["key"],
                "label": s["label"],
                "pct": data.get("pct_%s" % s["key"], ""),
                "grade": data.get("grade_%s" % s["key"], ""),
                "remark": data.get("remark_%s" % s["key"], ""),
            })
        return result

    def _build_traits(self, traits, data):
        result = []
        for t in traits:
            result.append({
                "key": t["key"],
                "label": t["label"],
                "val": data.get("trait_%s" % t["key"], ""),
            })
        return result

    def _strip(self, val):
        import re
        if not val:
            return val
        return re.sub(r'<[^>]+>', '', val).strip()

    def _get_all_grade_keys(self):
        keys = []
        for s in self.EOT_SUBJECTS + self.CAMBRIDGE_SUBJECTS:
            keys.append("grade_%s" % s["key"])
        for t in self.WORK_HABITS + self.PERSONAL_TRAITS + self.SOCIAL_TRAITS:
            keys.append("trait_%s" % t["key"])
        return keys

    def get(self, request, pk):
        task = get_object_or_404(Task, pk=pk)
        if task.assigned_to != request.user and request.user.role not in (UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied()

        saved_data = task.metadata.get("assessment_data", {})

        from django.utils import timezone
        ctx = {
            "task": task,
            "eot": self._build_section(self.EOT_SUBJECTS, saved_data),
            "cambridge": self._build_section(self.CAMBRIDGE_SUBJECTS, saved_data),
            "work_habits": self._build_traits(self.WORK_HABITS, saved_data),
            "personal_traits": self._build_traits(self.PERSONAL_TRAITS, saved_data),
            "social_traits": self._build_traits(self.SOCIAL_TRAITS, saved_data),
            "teacher_comments": saved_data.get("teacher_comments", ""),
            "teacher_name": saved_data.get("teacher_name", "") or request.user.get_full_name(),
            "report_date": saved_data.get("report_date", "") or timezone.now().date().isoformat(),
            "saved": request.GET.get("saved"),
        }
        from django.shortcuts import render
        return render(request, "tasks/_assessment_report_form.html", ctx)

    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk)
        if task.assigned_to != request.user and request.user.role not in (UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied()

        import json

        assessment_data = {}

        for s in self.EOT_SUBJECTS + self.CAMBRIDGE_SUBJECTS:
            assessment_data["pct_%s" % s["key"]] = request.POST.get("pct_%s" % s["key"], "").strip()
            assessment_data["grade_%s" % s["key"]] = request.POST.get("grade_%s" % s["key"], "").strip()
            assessment_data["remark_%s" % s["key"]] = request.POST.get("remark_%s" % s["key"], "").strip()

        for t in self.WORK_HABITS + self.PERSONAL_TRAITS + self.SOCIAL_TRAITS:
            assessment_data["trait_%s" % t["key"]] = request.POST.get("trait_%s" % t["key"], "").strip()

        teacher_comments = self._strip(request.POST.get("teacher_comments", ""))
        teacher_name = self._strip(request.POST.get("teacher_name", ""))
        report_date = request.POST.get("report_date", "").strip()

        assessment_data["teacher_comments"] = teacher_comments
        assessment_data["teacher_name"] = teacher_name
        assessment_data["report_date"] = report_date

        task.metadata["assessment_data"] = assessment_data
        task.save(update_fields=["metadata"])

        applicant_id = task.metadata.get("applicant_id")
        if applicant_id:
            try:
                from admissions.models import AssessmentSchedule
                assessment = AssessmentSchedule.objects.get(applicant_id=int(applicant_id))
                assessment.hod_comments = json.dumps(assessment_data)
                assessment.save(update_fields=["hod_comments", "updated_at"])
            except Exception:
                pass

        action = request.POST.get("action", "submit_report")

        if action == "submit_report":
            task.mark_completed()
            TaskHistory.objects.create(
                task=task, action='completed',
                changed_by=request.user, comment="Assessment report submitted"
            )

        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        if is_ajax:
            return JsonResponse({"ok": True, "action": action})

        from django.shortcuts import render
        ctx = {
            "task": task,
            "eot": self._build_section(self.EOT_SUBJECTS, assessment_data),
            "cambridge": self._build_section(self.CAMBRIDGE_SUBJECTS, assessment_data),
            "work_habits": self._build_traits(self.WORK_HABITS, assessment_data),
            "personal_traits": self._build_traits(self.PERSONAL_TRAITS, assessment_data),
            "social_traits": self._build_traits(self.SOCIAL_TRAITS, assessment_data),
            "teacher_comments": teacher_comments,
            "teacher_name": teacher_name,
            "report_date": report_date,
            "saved": True,
            "draft": action == "save_draft",
        }
        return render(request, "tasks/_assessment_report_form.html", ctx)
