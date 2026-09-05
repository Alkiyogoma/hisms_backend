import uuid
from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from django.core.exceptions import ValidationError

from core.models import TimeStampedModel
from users.models import User


# ============================================================================
# TASK TYPE CHOICES
# ============================================================================

TASK_TYPE_CHOICES = [
    # Admissions
    ('admission_review', 'Admission Review'),
    ('assessment_scheduling', 'Assessment Scheduling'),
    ('enrolment_processing', 'Enrolment Processing'),
    ('applicant_followup', 'Applicant Follow-up'),
    
    # Lesson Plans
    ('lesson_plan_review', 'Lesson Plan Review'),
    ('lesson_plan_revision', 'Lesson Plan Revision'),
    ('lesson_plan_submission', 'Lesson Plan Submission'),
    
    # Grading
    ('grade_approval', 'Grade Approval'),
    ('grade_submission', 'Grade Submission'),
    ('report_signoff', 'Report Sign-Off'),
    ('report_comment', 'Report Comment Submission'),
    ('at_risk_alert', 'At-Risk Student Alert'),
    
    # ECD Assessment
    ('assessment_entry', 'Assessment Data Entry'),
    ('assessment_report', 'Assessment Report Submission'),
    ('ecd_report_submission', 'ECD Report Submission'),
    
    # ECD Welfare
    ('welfare_alert', 'Welfare Alert'),
    ('welfare_followup', 'Welfare Follow-Up'),
    ('ecd_attendance', 'ECD Attendance Issue'),
    
    # Communications
    ('weekly_focus_submission', 'Weekly Focus Submission'),
    
    # Academic Compliance
    ('compliance_check', 'Compliance Check'),
    ('department_review', 'Department Review'),
    ('attendance_alert', 'Attendance Alert'),
    ('lesson_plan_compliance', 'Lesson Plan Compliance'),
    
    # Finance
    ('payment_matching', 'Payment Matching'),
    ('overdue_collection', 'Overdue Collection'),
    ('fee_verification', 'Fee Verification'),
    ('sibling_discount', 'Sibling Discount Verification'),
    ('assessment_fee', 'Assessment Fee Tracking'),
    
    # Events & Calendar
    ('event_rsvp', 'Event RSVP'),
    ('event_creation', 'Event Creation'),
    
    # Other
    ('fee_payment_reminder', 'Fee Payment Reminder'),
    ('document_upload', 'Document Upload'),
    ('attendance_confirmation', 'Attendance Confirmation'),
    ('general_task', 'General Task'),

    # Parent Portal
    ('discipline_contact', 'Discipline Parent Contact'),
    ('report_card_ready', 'Report Card Available'),
    ('open_invoice', 'Open Invoice Reminder'),
]

STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('in_progress', 'In Progress'),
    ('completed', 'Completed'),
    ('deferred', 'Deferred'),
    ('cancelled', 'Cancelled'),
]

PRIORITY_CHOICES = [
    ('critical', 'Critical'),
    ('high', 'High'),
    ('medium', 'Medium'),
    ('low', 'Low'),
]

ACTION_CHOICES = [
    ('approve', 'Approve'),
    ('reject', 'Reject'),
    ('request_revision', 'Request Revision'),
    ('acknowledge', 'Acknowledge'),
    ('submit', 'Submit'),
    ('escalate', 'Escalate'),
    ('follow_up', 'Follow-Up'),
    ('review', 'Review'),
    ('sign_off', 'Sign Off'),
    ('contact_parent', 'Contact Parent'),
    ('mark_complete', 'Mark Complete'),
    ('defer', 'Defer'),
    ('cancel', 'Cancel'),
    ('mark_overdue', 'Mark Overdue'),
    ('send_reminder', 'Send Reminder'),
]


# ============================================================================
# MODELS
# ============================================================================

class Task(TimeStampedModel):
    """
    Represents a single action item for a user.
    Triggered automatically by system events or created manually by admins.
    """
    
    # Basic identification
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_type = models.CharField(
        max_length=50,
        choices=TASK_TYPE_CHOICES,
        help_text="Category of task"
    )
    title = models.CharField(
        max_length=200,
        help_text="Display title for task"
    )
    description = models.TextField(
        blank=True,
        help_text="Detailed description of task"
    )
    
    # User assignment
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='assigned_tasks'
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_tasks'
    )
    
    # Temporal
    due_date = models.DateTimeField(
        null=True, blank=True,
        help_text="When this task should be completed"
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False
    )
    
    # Status & Priority
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )
    priority = models.CharField(
        max_length=20,
        choices=PRIORITY_CHOICES,
        default='medium'
    )
    is_overdue = models.BooleanField(
        default=False,
        help_text="Auto-set if due_date < now and not completed"
    )
    
    # Related objects (polymorphic relationships)
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    object_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="ID of related object (Lesson Plan, Grade, etc.)"
    )
    
    # Task-specific data (JSON for flexibility)
    metadata = models.JSONField(
        default=dict,
        help_text="Task-specific data: student_name, subject, class, etc."
    )
    
    # Action tracking
    actions_available = models.JSONField(
        default=list,
        help_text="List of available actions user can take"
    )
    
    # Notifications
    notification_sent = models.BooleanField(default=False)
    reminder_sent = models.BooleanField(default=False)
    
    # Notes/Comments
    internal_notes = models.TextField(
        blank=True,
        help_text="Internal notes about this task (not visible to user)"
    )
    
    class Meta:
        ordering = ['-priority', 'due_date', '-created_at']
        permissions = [
            ("view_department_tasks", "Can view tasks across the whole department (department managers)"),
        ]
        indexes = [
            models.Index(fields=['assigned_to', 'status']),
            models.Index(fields=['assigned_to', 'due_date']),
            models.Index(fields=['created_at']),
            models.Index(fields=['is_overdue']),
            models.Index(fields=['status', 'priority']),
        ]
        verbose_name = 'Task'
        verbose_name_plural = 'Tasks'
    
    def __str__(self):
        return f"[{self.get_priority_display()}] {self.title} → {self.assigned_to.get_full_name()}"
    
    def save(self, *args, **kwargs):
        """Update is_overdue flag before saving."""
        self.update_overdue_status()
        super().save(*args, **kwargs)
    
    @property
    def days_until_due(self):
        """Returns days until due, or None if completed."""
        if self.completed_at:
            return None
        delta = self.due_date - timezone.now()
        return delta.days
    
    @property
    def is_pending_action(self):
        """Returns True if task is waiting for user action."""
        return self.status in ['pending', 'in_progress']
    
    @property
    def content_object(self):
        """Returns the related object (e.g., LessonPlan, Grade, etc.)."""
        if not self.content_type or not self.object_id:
            return None
        try:
            return self.content_type.get_object_for_this_type(pk=self.object_id)
        except Exception:
            return None
    
    def update_overdue_status(self):
        """Update is_overdue flag based on current time and status."""
        if self.status == 'completed':
            self.is_overdue = False
        elif self.due_date:
            from datetime import date, datetime
            due = self.due_date
            now = timezone.now()
            if isinstance(due, date) and not isinstance(due, datetime):
                due = timezone.make_aware(timezone.datetime.combine(due, timezone.datetime.min.time()))
            elif timezone.is_naive(due):
                due = timezone.make_aware(due)
            self.is_overdue = due < now
        else:
            self.is_overdue = False
    
    def mark_completed(self):
        """Mark task as completed."""
        self.status = 'completed'
        self.completed_at = timezone.now()
        self.is_overdue = False
        self.save()
    
    def mark_in_progress(self):
        """Mark task as in progress."""
        self.status = 'in_progress'
        self.save()
    
    def defer_until(self, new_due_date, reason=''):
        """Defer task to a future date."""
        if new_due_date <= timezone.now():
            raise ValidationError("Deferred date must be in the future.")
        
        self.due_date = new_due_date
        self.status = 'deferred'
        
        # Log the deferral
        TaskHistory.objects.create(
            task=self,
            action='deferred',
            old_value=str(self.due_date),
            new_value=str(new_due_date),
            changed_by=None,  # System action
            comment=reason
        )
        
        self.save()
    
    def get_related_object_url(self):
        """Generate URL to related object based on content type."""
        obj = self.content_object
        if obj and hasattr(obj, 'get_absolute_url'):
            return obj.get_absolute_url()
        return None


class TaskHistory(TimeStampedModel):
    """
    Audit trail for all task changes.
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name='history'
    )
    action = models.CharField(
        max_length=50,
        choices=[
            ('created', 'Created'),
            ('status_changed', 'Status Changed'),
            ('assigned', 'Assigned'),
            ('action_taken', 'Action Taken'),
            ('comment_added', 'Comment Added'),
            ('deferred', 'Deferred'),
            ('cancelled', 'Cancelled'),
            ('completed', 'Completed'),
            ('escalated', 'Escalated'),
            ('notification_sent', 'Notification Sent'),
            ('reminder_sent', 'Reminder Sent'),
        ]
    )
    old_value = models.TextField(null=True, blank=True)
    new_value = models.TextField(null=True, blank=True)
    changed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    comment = models.TextField(blank=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Task History'
        verbose_name_plural = 'Task Histories'
    
    def __str__(self):
        return f"{self.task.title} - {self.action}"


class TaskComment(TimeStampedModel):
    """
    Comments/notes on a task visible to the assigned user.
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name='comments'
    )
    author = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True
    )
    content = models.TextField()
    is_internal = models.BooleanField(
        default=False,
        help_text="Internal notes not visible to task assignee"
    )
    
    class Meta:
        ordering = ['created_at']
        verbose_name = 'Task Comment'
        verbose_name_plural = 'Task Comments'
    
    def __str__(self):
        return f"Comment on {self.task.title} by {self.author}"


class TaskTemplate(models.Model):
    """
    Reusable template for creating recurring/bulk tasks.
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    task_type = models.CharField(max_length=50, choices=TASK_TYPE_CHOICES)
    title_template = models.CharField(
        max_length=200,
        help_text="Template string with placeholders: {student_name}, {class_name}, etc."
    )
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES)
    actions_available = models.JSONField(default=list)
    metadata_template = models.JSONField(
        default=dict,
        help_text="Template for metadata with placeholders"
    )
    days_until_due = models.PositiveIntegerField(
        default=2,
        help_text="Days from creation until due date"
    )
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Task Template'
        verbose_name_plural = 'Task Templates'
    
    def __str__(self):
        return self.name


class TaskNotificationLog(TimeStampedModel):
    """
    Log of all notifications sent for tasks (for audit trail).
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name='notification_logs'
    )
    notification_type = models.CharField(
        max_length=50,
        choices=[
            ('task_creation', 'Task Creation'),
            ('task_reminder', 'Task Reminder'),
            ('task_escalation', 'Task Escalation'),
            ('task_completion', 'Task Completion'),
        ]
    )
    sent_to = models.ForeignKey(
        User,
        on_delete=models.CASCADE
    )
    channel = models.CharField(
        max_length=50,
        choices=[
            ('in_app', 'In-App'),
            ('email', 'Email'),
            ('push', 'Push Notification'),
            ('sms', 'SMS'),
        ]
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ('sent', 'Sent'),
            ('failed', 'Failed'),
            ('pending', 'Pending'),
        ],
        default='pending'
    )
    error_message = models.TextField(blank=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Task Notification Log'
        verbose_name_plural = 'Task Notification Logs'
    
    def __str__(self):
        return f"{self.notification_type} → {self.sent_to} ({self.channel})"
