"""
Task Module - REST Framework Views & ViewSets
Handles HTTP requests for tasks, with RBAC enforcement
"""

from rest_framework import viewsets, status, filters
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, NotFound, ValidationError as DRFValidationError
from django.shortcuts import get_object_or_404
from django.db.models import Q, Count, F
from django.utils import timezone
import uuid

from .models import Task, TaskHistory, TaskComment, STATUS_CHOICES, PRIORITY_CHOICES
from .serializers import (
    TaskListSerializer, TaskDetailSerializer, TaskCreateUpdateSerializer,
    TaskActionSerializer, TaskBulkUpdateSerializer, TaskCountsSerializer,
    TaskCommentSerializer, TaskDeferSerializer, TaskHistorySerializer
)
from users.models import UserRole


class TaskPermission(IsAuthenticated):
    """
    Custom permission for task access
    - Users can only see their own tasks
    - Super Admin can see all tasks
    - HODs can see tasks for their department
    - HOS can see tasks for their school
    """
    
    def has_object_permission(self, request, view, obj):
        """Check if user can access this specific task"""
        user = request.user
        
        # Super Admin sees everything
        if user.role == UserRole.SUPER_ADMIN:
            return True
        
        # User sees own tasks
        if obj.assigned_to == user:
            return True
        
        # Creator can view own created tasks
        if obj.created_by == user:
            return True
        
        return False
    
    def filter_queryset(self, view, queryset):
        """Filter queryset based on user permissions"""
        user = view.request.user
        
        if user.role == UserRole.SUPER_ADMIN:
            return queryset
        
        elif user.role in (UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD):
            return queryset.filter(
                Q(assigned_to=user) |
                Q(created_by=user)
            )
        
        else:
            return queryset.filter(assigned_to=user)


class TaskViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Task management
    
    Endpoints:
    - GET /api/tasks/ - List user's tasks (with filters)
    - POST /api/tasks/ - Create task (admin only)
    - GET /api/tasks/{id}/ - Get task detail
    - PATCH /api/tasks/{id}/ - Update task
    - DELETE /api/tasks/{id}/ - Delete task (admin only, soft delete in practice)
    - POST /api/tasks/{id}/mark_complete/ - Mark task as completed
    - POST /api/tasks/{id}/take_action/ - Take action on task
    - POST /api/tasks/{id}/defer/ - Defer task
    - POST /api/tasks/{id}/comments/ - Add comment
    - GET /api/tasks/{id}/history/ - Get audit trail
    - GET /api/tasks/counts/ - Get task counts (for badge)
    """
    
    permission_classes = [TaskPermission]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'description', 'metadata']
    ordering_fields = ['due_date', 'created_at', 'priority', 'is_overdue']
    ordering = ['-priority', 'due_date', '-created_at']
    
    def get_queryset(self):
        """Return tasks visible to the current user"""
        user = self.request.user
        
        if user.role == UserRole.SUPER_ADMIN:
            return Task.objects.all()
        
        elif user.role in (UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD):
            return Task.objects.filter(
                Q(assigned_to=user) |
                Q(created_by=user)
            )
        
        else:
            return Task.objects.filter(assigned_to=user)
    
    def get_serializer_class(self):
        """Use different serializers based on action"""
        if self.action == 'retrieve':
            return TaskDetailSerializer
        elif self.action in ['create', 'update', 'partial_update']:
            return TaskCreateUpdateSerializer
        elif self.action == 'list':
            return TaskListSerializer
        return TaskDetailSerializer
    
    def perform_create(self, serializer):
        """OP 3.1: Only admin-level roles can create tasks"""
        user = self.request.user
        TASK_CREATOR_ROLES = {
            UserRole.SUPER_ADMIN,
            UserRole.HEAD_OF_SCHOOL,
            UserRole.PRIMARY_HOD,
            UserRole.ECD_HOD,
            UserRole.LOWER_SECONDARY_HOD,
            UserRole.ADMIN_OFFICER,
        }
        if user.role not in TASK_CREATOR_ROLES:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You do not have permission to create tasks.")
        serializer.save(created_by=user)

    def perform_destroy(self, instance):
        """OP 3.1: Only Super Admin can delete tasks"""
        if self.request.user.role != UserRole.SUPER_ADMIN:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only Super Admin can delete tasks.")
        instance.delete()
    
    @action(detail=False, methods=['get'])
    def counts(self, request):
        """
        Get task counts for the current user (for navbar badge)
        
        Response:
        {
            "total": 10,
            "pending": 5,
            "in_progress": 3,
            "completed": 2,
            "overdue": 1,
            "critical": 2,
            "by_priority": {
                "critical": 2,
                "high": 4,
                "medium": 3,
                "low": 1
            }
        }
        """
        queryset = self.get_queryset()
        
        # Base counts
        total = queryset.count()
        pending = queryset.filter(status='pending').count()
        in_progress = queryset.filter(status='in_progress').count()
        completed = queryset.filter(status='completed').count()
        overdue = queryset.filter(is_overdue=True, status__in=['pending', 'in_progress']).count()
        critical = queryset.filter(priority='critical').count()
        
        # Count by priority
        by_priority = {}
        for priority in ['critical', 'high', 'medium', 'low']:
            by_priority[priority] = queryset.filter(priority=priority).count()
        
        data = {
            'total': total,
            'pending': pending,
            'in_progress': in_progress,
            'completed': completed,
            'overdue': overdue,
            'critical': critical,
            'by_priority': by_priority
        }
        
        return Response(data)
    
    @action(detail=True, methods=['post'])
    def mark_complete(self, request, pk=None):
        """
        Mark task as completed
        
        POST /api/tasks/{id}/mark_complete/
        """
        task = self.get_object()
        
        # Permission check: only assignee or creator can mark complete
        if task.assigned_to != request.user and task.created_by != request.user:
            raise PermissionDenied("You can only mark your own tasks as complete.")
        
        task.mark_completed()
        
        # Log in history
        TaskHistory.objects.create(
            task=task,
            action='completed',
            changed_by=request.user,
            comment="Task marked as completed"
        )
        
        serializer = self.get_serializer(task)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'])
    def take_action(self, request, pk=None):
        """
        Take action on a task (approve, reject, request_revision, etc.)
        
        POST /api/tasks/{id}/take_action/
        Body: {
            "action": "approve",
            "comment": "Looks good, approved"
        }
        """
        task = self.get_object()
        
        # Permission check: only assignee can take action
        if task.assigned_to != request.user:
            raise PermissionDenied("Only the assigned user can take actions on this task.")
        
        serializer = TaskActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        action_name = serializer.validated_data.get('action')
        comment = serializer.validated_data.get('comment', '')
        
        # Verify action is available
        if action_name not in task.actions_available:
            raise DRFValidationError(
                f"Action '{action_name}' is not available for this task. "
                f"Available actions: {', '.join(task.actions_available)}"
            )
        
        # Execute action based on type
        if action_name == 'mark_complete':
            task.mark_completed()
        elif action_name == 'mark_in_progress':
            task.mark_in_progress()
        elif action_name == 'defer':
            # For defer, should use defer endpoint instead
            pass
        elif action_name == 'cancel':
            task.status = 'cancelled'
            task.save()
        else:
            # For other actions (approve, reject, etc.), just log and change status
            task.status = 'in_progress'
            task.save()
        
        # Log action in history
        TaskHistory.objects.create(
            task=task,
            action='action_taken',
            old_value=task.status,
            new_value=action_name,
            changed_by=request.user,
            comment=comment
        )
        
        serializer = self.get_serializer(task)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'])
    def defer(self, request, pk=None):
        """
        Defer task to a future date
        
        POST /api/tasks/{id}/defer/
        Body: {
            "new_due_date": "2026-06-20T17:00:00Z",
            "reason": "Awaiting additional information"
        }
        """
        task = self.get_object()
        
        # Permission check
        if task.assigned_to != request.user and task.created_by != request.user:
            raise PermissionDenied("Only assignee or creator can defer a task.")
        
        serializer = TaskDeferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        new_due_date = serializer.validated_data.get('new_due_date')
        reason = serializer.validated_data.get('reason', '')
        
        task.defer_until(new_due_date, reason)
        
        serializer = self.get_serializer(task)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'])
    def comments(self, request, pk=None):
        """
        Add comment to task
        
        POST /api/tasks/{id}/comments/
        Body: {
            "comment": "This needs revision before submission"
        }
        """
        task = self.get_object()
        
        # Permission check: only assignee, creator, or manager can comment
        can_comment = (
            task.assigned_to == request.user or
            task.created_by == request.user or
            request.user.has_perm("tasks.view_department_tasks") or
            request.user.role == UserRole.HEAD_OF_SCHOOL
        )
        if not can_comment:
            raise PermissionDenied("You don't have permission to comment on this task.")
        
        if request.method == 'POST':
            serializer = TaskCommentSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            
            comment = TaskComment.objects.create(
                task=task,
                comment=serializer.validated_data['comment'],
                created_by=request.user
            )
            
            # Log in history
            TaskHistory.objects.create(
                task=task,
                action='comment_added',
                changed_by=request.user,
                comment=f"Comment: {serializer.validated_data['comment']}"
            )
            
            return Response(
                TaskCommentSerializer(comment).data,
                status=status.HTTP_201_CREATED
            )
    
    @action(detail=True, methods=['get'])
    def history(self, request, pk=None):
        """
        Get task history/audit trail
        
        GET /api/tasks/{id}/history/
        """
        task = self.get_object()
        history = task.history.all()
        serializer = TaskHistorySerializer(history, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def bulk_update_status(self, request):
        """
        Bulk update status of multiple tasks
        
        POST /api/tasks/bulk_update_status/
        Body: {
            "task_ids": ["uuid1", "uuid2"],
            "status": "completed"
        }
        """
        # Only allow for users' own tasks or admins
        if request.user.role not in {UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL}:
            raise PermissionDenied("You don't have permission for bulk operations.")
        
        serializer = TaskBulkUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        task_ids = serializer.validated_data['task_ids']
        status_val = serializer.validated_data.get('status')
        
        tasks = Task.objects.filter(id__in=task_ids)
        
        if status_val:
            tasks.update(status=status_val)
        
        return Response({
            'count': tasks.count(),
            'updated': True
        }, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['post'])
    def bulk_assign(self, request):
        """
        Bulk assign tasks to a user
        
        POST /api/tasks/bulk_assign/
        Body: {
            "task_ids": ["uuid1", "uuid2"],
            "assigned_to": "user_id"
        }
        """
        # Only allow for admins
        if request.user.role not in {UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL}:
            raise PermissionDenied("You don't have permission for bulk operations.")
        
        from users.models import User as UserModel
        
        task_ids = request.data.get('task_ids', [])
        assigned_to_id = request.data.get('assigned_to')
        
        if not task_ids or not assigned_to_id:
            raise DRFValidationError("task_ids and assigned_to are required.")
        
        try:
            assigned_to = UserModel.objects.get(id=assigned_to_id)
        except UserModel.DoesNotExist:
            raise DRFValidationError(f"User {assigned_to_id} not found.")
        
        tasks = Task.objects.filter(id__in=task_ids)
        old_assignees = {str(t.id): t.assigned_to_id for t in tasks}
        tasks.update(assigned_to=assigned_to)
        
        # Log in history
        for task_id in task_ids:
            try:
                task = Task.objects.get(id=task_id)
                TaskHistory.objects.create(
                    task=task,
                    action='assigned',
                    old_value=str(old_assignees.get(str(task_id))),
                    new_value=str(assigned_to_id),
                    changed_by=request.user
                )
            except Task.DoesNotExist:
                pass
        
        return Response({
            'count': tasks.count(),
            'assigned_to': str(assigned_to_id),
            'updated': True
        }, status=status.HTTP_200_OK)


# Additional convenience views

class TaskReportPermission(IsAuthenticated):
    """NFR-SEC-004: Task reports require view_task permission."""
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        return request.user.is_superuser or request.user.has_perm("tasks.view_task")


@api_view(['GET'])
@permission_classes([TaskReportPermission])
def user_task_counts(request):
    """
    Get task counts for dashboard widget (mini view of counts endpoint)
    """
    user = request.user
    queryset = Task.objects.filter(assigned_to=user)
    
    counts = {
        'total': queryset.count(),
        'pending': queryset.filter(status='pending').count(),
        'overdue': queryset.filter(is_overdue=True).count(),
        'critical': queryset.filter(priority='critical').count(),
    }
    
    return Response(counts)


@api_view(['GET'])
@permission_classes([TaskReportPermission])
def overdue_tasks(request):
    """
    Get all overdue tasks for the user
    """
    user = request.user
    overdue = Task.objects.filter(
        assigned_to=user,
        is_overdue=True,
        status__in=['pending', 'in_progress']
    ).order_by('-priority', 'due_date')
    
    serializer = TaskListSerializer(overdue, many=True)
    return Response(serializer.data)
