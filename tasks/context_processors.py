"""
Task context processor — injects task counts into every page template
so the topbar can show a task badge/bell icon.
"""

from django.db.models import Q


def task_counts(request):
    """
    Add task count data to the template context for authenticated users.
    Used by base.html topbar to show task badge.
    """
    context = {}
    
    if not hasattr(request, 'user') or not request.user.is_authenticated:
        return context
    
    user = request.user
    role = user.role
    
    try:
        from .models import Task
        from users.models import User, UserRole
        from django.db.models import Count, Q as QQ
        
        # SUPER_ADMIN: single aggregated query instead of full table scan
        if role == UserRole.SUPER_ADMIN:
            counts = Task.objects.aggregate(
                pending=Count('pk', filter=QQ(status__in=['pending', 'in_progress'])),
                overdue=Count('pk', filter=QQ(status__in=['pending', 'in_progress'], is_overdue=True)),
            )
            context['task_pending_count'] = counts['pending']
            context['task_overdue_count'] = counts['overdue']
            return context

        if role == UserRole.HEAD_OF_SCHOOL:
            qs = Task.objects.filter(Q(assigned_to=user) | Q(created_by=user))
        elif role in (UserRole.PRIMARY_HOD, UserRole.ECD_HOD):
            # Use cached dept check to avoid lazy staff_profile query
            dept = getattr(request, '_cached_dept', None)
            if dept is None:
                dept = User.objects.filter(pk=user.pk).values_list('staff_profile__department', flat=True).first()
                request._cached_dept = dept
            if dept:
                dept_users = getattr(request, '_cached_dept_users', None)
                if dept_users is None:
                    dept_users = list(User.objects.filter(staff_profile__department=dept).values_list('pk', flat=True))
                    request._cached_dept_users = dept_users
                qs = Task.objects.filter(Q(assigned_to=user) | Q(assigned_to__in=dept_users) | Q(created_by=user))
            else:
                qs = Task.objects.filter(Q(assigned_to=user) | Q(created_by=user))
        else:
            qs = Task.objects.filter(assigned_to=user)
            if user.role == UserRole.PARENT:
                try:
                    from students.models import Student, StudentGuardian, ParentGuardian
                    from django.contrib.contenttypes.models import ContentType
                    guardian = ParentGuardian.objects.filter(user=user).first()
                    if guardian:
                        child_ids = list(StudentGuardian.objects.filter(guardian=guardian).values_list('student_id', flat=True))
                        child_names = list(Student.objects.filter(pk__in=child_ids).values_list('first_name', flat=True)) + list(Student.objects.filter(pk__in=child_ids).values_list('last_name', flat=True))
                        student_ct = ContentType.objects.get_for_model(Student)
                        child_qs = Task.objects.filter(
                            Q(task_type__in=[
                                'welfare_alert', 'welfare_followup', 'attendance_alert',
                                'fee_payment_reminder', 'overdue_collection',
                                'discipline_contact', 'report_card_ready', 'open_invoice',
                            ])
                            & (
                                Q(content_type=student_ct, object_id__in=child_ids)
                                | Q(metadata__student_id__in=child_ids)
                            )
                        )
                        qs = qs | child_qs
                except Exception:
                    pass
        
        pending = qs.filter(status__in=['pending', 'in_progress']).count()
        overdue = qs.filter(
            is_overdue=True,
            status__in=['pending', 'in_progress']
        ).count()
        
        context['task_pending_count'] = pending
        context['task_overdue_count'] = overdue
        # Store on request so navigation context processor can use it for sidebar badge
        request.task_pending_count = pending
    except Exception:
        context['task_pending_count'] = 0
        context['task_overdue_count'] = 0
        request.task_pending_count = 0
    
    return context
