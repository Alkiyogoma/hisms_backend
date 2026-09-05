"""
Task Module - REST Framework Serializers
Handles serialization/deserialization of Task objects for API endpoints
"""

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError as DRFValidationError
from django.utils import timezone
from django.core.exceptions import ValidationError
from .models import Task, TaskHistory, TaskComment
from users.models import User


class TaskCommentSerializer(serializers.ModelSerializer):
    """Serializer for task comments"""
    
    author_name = serializers.SerializerMethodField()
    author_avatar = serializers.SerializerMethodField()
    
    class Meta:
        model = TaskComment
        fields = ['id', 'comment', 'created_at', 'updated_at', 'created_by', 'author_name', 'author_avatar']
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by', 'author_name', 'author_avatar']
    
    def get_author_name(self, obj):
        return obj.created_by.get_full_name() if obj.created_by else 'System'
    
    def get_author_avatar(self, obj):
        """Return author's avatar URL if available"""
        if obj.created_by and hasattr(obj.created_by, 'profile') and obj.created_by.profile.avatar:
            return obj.created_by.profile.avatar.url
        return None


class TaskHistorySerializer(serializers.ModelSerializer):
    """Serializer for task history/audit trail"""
    
    changed_by_name = serializers.SerializerMethodField()
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    
    class Meta:
        model = TaskHistory
        fields = ['id', 'action', 'action_display', 'old_value', 'new_value', 'created_at', 'changed_by', 'changed_by_name', 'comment']
        read_only_fields = ['id', 'created_at']
    
    def get_changed_by_name(self, obj):
        return obj.changed_by.get_full_name() if obj.changed_by else 'System'


class TaskListSerializer(serializers.ModelSerializer):
    """
    Compact serializer for task lists (used in dropdown/dashboard)
    Omits heavy related objects
    """
    
    assigned_to_name = serializers.SerializerMethodField()
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    task_type_display = serializers.CharField(source='get_task_type_display', read_only=True)
    days_until_due = serializers.SerializerMethodField()
    
    class Meta:
        model = Task
        fields = [
            'id', 'task_type', 'task_type_display', 'title', 'description',
            'assigned_to', 'assigned_to_name', 'due_date', 'days_until_due',
            'status', 'status_display', 'priority', 'priority_display',
            'is_overdue', 'completed_at', 'notification_sent',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'task_type_display', 'assigned_to_name', 'days_until_due',
            'status_display', 'priority_display', 'is_overdue', 'completed_at',
            'notification_sent', 'created_at', 'updated_at'
        ]
    
    def get_assigned_to_name(self, obj):
        return obj.assigned_to.get_full_name()
    
    def get_days_until_due(self, obj):
        return obj.days_until_due


class TaskDetailSerializer(serializers.ModelSerializer):
    """
    Full serializer for task detail view
    Includes related objects, history, and comments
    """
    
    assigned_to_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    task_type_display = serializers.CharField(source='get_task_type_display', read_only=True)
    days_until_due = serializers.SerializerMethodField()
    related_object = serializers.SerializerMethodField()
    related_object_url = serializers.SerializerMethodField()
    history = TaskHistorySerializer(many=True, read_only=True)
    comments = TaskCommentSerializer(many=True, read_only=True, source='task_comments')
    
    class Meta:
        model = Task
        fields = [
            'id', 'task_type', 'task_type_display', 'title', 'description',
            'assigned_to', 'assigned_to_name', 'created_by', 'created_by_name',
            'due_date', 'days_until_due', 'completed_at',
            'status', 'status_display', 'priority', 'priority_display',
            'is_overdue', 'content_type', 'object_id', 'metadata',
            'actions_available', 'notification_sent', 'reminder_sent',
            'internal_notes', 'created_at', 'updated_at',
            'related_object', 'related_object_url', 'history', 'comments'
        ]
        read_only_fields = [
            'id', 'task_type_display', 'assigned_to_name', 'created_by_name',
            'days_until_due', 'status_display', 'priority_display', 'is_overdue',
            'completed_at', 'related_object', 'related_object_url',
            'history', 'comments', 'created_at', 'updated_at', 'notification_sent', 'reminder_sent'
        ]
    
    def get_assigned_to_name(self, obj):
        return obj.assigned_to.get_full_name()
    
    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name() if obj.created_by else 'System'
    
    def get_days_until_due(self, obj):
        return obj.days_until_due
    
    def get_related_object(self, obj):
        """Serialize related object if it exists"""
        rel_obj = obj.content_object
        if rel_obj:
            try:
                # Return key identifying info, not full serialization
                return {
                    'type': obj.content_type.model,
                    'id': str(obj.object_id),
                    'display': str(rel_obj)
                }
            except Exception:
                return None
        return None
    
    def get_related_object_url(self, obj):
        return obj.get_related_object_url()


class TaskCreateUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating and updating tasks
    Used by admin/system to generate tasks
    """
    
    class Meta:
        model = Task
        fields = [
            'task_type', 'title', 'description', 'assigned_to',
            'due_date', 'priority', 'content_type', 'object_id',
            'metadata', 'actions_available', 'internal_notes'
        ]
    
    def validate_due_date(self, value):
        """Ensure due date is in the future"""
        if value < timezone.now():
            raise DRFValidationError("Due date must be in the future.")
        return value
    
    def validate_assigned_to(self, value):
        """Ensure assigned user exists and is active"""
        if not value.is_active:
            raise DRFValidationError(f"User {value.get_full_name()} is not active.")
        return value
    
    def create(self, validated_data):
        """Create task with creator set to current user"""
        validated_data['created_by'] = self.context['request'].user
        task = Task.objects.create(**validated_data)
        
        # Log creation in history
        TaskHistory.objects.create(
            task=task,
            action='created',
            changed_by=self.context['request'].user,
            comment=f"Task created: {task.title}"
        )
        
        return task


class TaskActionSerializer(serializers.Serializer):
    """Serializer for taking action on a task"""
    
    action = serializers.ChoiceField(
        choices=['approve', 'reject', 'request_revision', 'acknowledge', 
                'submit', 'escalate', 'follow_up', 'review', 'sign_off',
                'contact_parent', 'mark_complete', 'defer', 'cancel']
    )
    comment = serializers.CharField(required=False, allow_blank=True)
    
    def validate(self, attrs):
        """Ensure action is available for this task"""
        # This would be validated in the view with task.actions_available
        return attrs


class TaskBulkUpdateSerializer(serializers.Serializer):
    """Serializer for bulk task updates"""
    
    task_ids = serializers.ListField(child=serializers.UUIDField())
    status = serializers.ChoiceField(
        choices=['pending', 'in_progress', 'completed', 'deferred', 'cancelled'],
        required=False
    )
    priority = serializers.ChoiceField(
        choices=['critical', 'high', 'medium', 'low'],
        required=False
    )
    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False
    )
    
    def validate_task_ids(self, value):
        if not value:
            raise DRFValidationError("At least one task ID is required.")
        if len(value) > 100:
            raise DRFValidationError("Cannot update more than 100 tasks at once.")
        return value


class TaskCountsSerializer(serializers.Serializer):
    """Serializer for task count statistics (for badge)"""
    
    total = serializers.IntegerField()
    pending = serializers.IntegerField()
    in_progress = serializers.IntegerField()
    completed = serializers.IntegerField()
    overdue = serializers.IntegerField()
    critical = serializers.IntegerField()
    by_priority = serializers.DictField()


class TaskDeferSerializer(serializers.Serializer):
    """Serializer for deferring a task"""
    
    new_due_date = serializers.DateTimeField()
    reason = serializers.CharField(required=False, allow_blank=True)
    
    def validate_new_due_date(self, value):
        if value <= timezone.now():
            raise DRFValidationError("Deferred date must be in the future.")
        return value
