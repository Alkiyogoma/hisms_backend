"""
Initial migration for the tasks app.
Creates Task, TaskHistory, TaskComment, TaskTemplate, and TaskNotificationLog models.
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Task',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('task_type', models.CharField(choices=[
                    ('admission_review', 'Admission Review'),
                    ('assessment_scheduling', 'Assessment Scheduling'),
                    ('enrolment_processing', 'Enrolment Processing'),
                    ('applicant_followup', 'Applicant Follow-up'),
                    ('lesson_plan_review', 'Lesson Plan Review'),
                    ('lesson_plan_revision', 'Lesson Plan Revision'),
                    ('lesson_plan_submission', 'Lesson Plan Submission'),
                    ('grade_approval', 'Grade Approval'),
                    ('grade_submission', 'Grade Submission'),
                    ('report_signoff', 'Report Sign-Off'),
                    ('report_comment', 'Report Comment Submission'),
                    ('at_risk_alert', 'At-Risk Student Alert'),
                    ('assessment_entry', 'Assessment Data Entry'),
                    ('ecd_report_submission', 'ECD Report Submission'),
                    ('welfare_alert', 'Welfare Alert'),
                    ('welfare_followup', 'Welfare Follow-Up'),
                    ('ecd_attendance', 'ECD Attendance Issue'),
                    ('weekly_focus_submission', 'Weekly Focus Submission'),
                    ('compliance_check', 'Compliance Check'),
                    ('department_review', 'Department Review'),
                    ('attendance_alert', 'Attendance Alert'),
                    ('lesson_plan_compliance', 'Lesson Plan Compliance'),
                    ('payment_matching', 'Payment Matching'),
                    ('overdue_collection', 'Overdue Collection'),
                    ('fee_verification', 'Fee Verification'),
                    ('sibling_discount', 'Sibling Discount Verification'),
                    ('assessment_fee', 'Assessment Fee Tracking'),
                    ('event_rsvp', 'Event RSVP'),
                    ('event_creation', 'Event Creation'),
                    ('fee_payment_reminder', 'Fee Payment Reminder'),
                    ('document_upload', 'Document Upload'),
                    ('attendance_confirmation', 'Attendance Confirmation'),
                    ('general_task', 'General Task'),
                ], help_text='Category of task', max_length=50)),
                ('title', models.CharField(help_text='Display title for task', max_length=200)),
                ('description', models.TextField(blank=True, help_text='Detailed description of task')),
                ('due_date', models.DateTimeField(help_text='When this task should be completed')),
                ('completed_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('status', models.CharField(choices=[
                    ('pending', 'Pending'),
                    ('in_progress', 'In Progress'),
                    ('completed', 'Completed'),
                    ('deferred', 'Deferred'),
                    ('cancelled', 'Cancelled'),
                ], default='pending', max_length=20)),
                ('priority', models.CharField(choices=[
                    ('critical', 'Critical'),
                    ('high', 'High'),
                    ('medium', 'Medium'),
                    ('low', 'Low'),
                ], default='medium', max_length=20)),
                ('is_overdue', models.BooleanField(default=False, help_text='Auto-set if due_date < now and not completed')),
                ('object_id', models.UUIDField(blank=True, help_text='ID of related object (Lesson Plan, Grade, etc.)', null=True)),
                ('metadata', models.JSONField(default=dict, help_text='Task-specific data: student_name, subject, class, etc.')),
                ('actions_available', models.JSONField(default=list, help_text='List of available actions user can take')),
                ('notification_sent', models.BooleanField(default=False)),
                ('reminder_sent', models.BooleanField(default=False)),
                ('internal_notes', models.TextField(blank=True, help_text='Internal notes about this task (not visible to user)')),
                ('assigned_to', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assigned_tasks', to=settings.AUTH_USER_MODEL)),
                ('content_type', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='contenttypes.contenttype')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_tasks', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Task',
                'verbose_name_plural': 'Tasks',
                'ordering': ['-priority', 'due_date', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='TaskHistory',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('action', models.CharField(choices=[
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
                ], max_length=50)),
                ('old_value', models.TextField(blank=True, null=True)),
                ('new_value', models.TextField(blank=True, null=True)),
                ('comment', models.TextField(blank=True)),
                ('changed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='history', to='tasks.task')),
            ],
            options={
                'verbose_name': 'Task History',
                'verbose_name_plural': 'Task Histories',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='TaskComment',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('content', models.TextField()),
                ('is_internal', models.BooleanField(default=False, help_text='Internal notes not visible to task assignee')),
                ('author', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='comments', to='tasks.task')),
            ],
            options={
                'verbose_name': 'Task Comment',
                'verbose_name_plural': 'Task Comments',
                'ordering': ['created_at'],
            },
        ),
        migrations.CreateModel(
            name='TaskTemplate',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100)),
                ('task_type', models.CharField(max_length=50)),
                ('title_template', models.CharField(help_text='Template string with placeholders: {student_name}, {class_name}, etc.', max_length=200)),
                ('description', models.TextField(blank=True)),
                ('priority', models.CharField(max_length=20)),
                ('actions_available', models.JSONField(default=list)),
                ('metadata_template', models.JSONField(default=dict, help_text='Template for metadata with placeholders')),
                ('days_until_due', models.PositiveIntegerField(default=2, help_text='Days from creation until due date')),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Task Template',
                'verbose_name_plural': 'Task Templates',
            },
        ),
        migrations.CreateModel(
            name='TaskNotificationLog',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('notification_type', models.CharField(choices=[
                    ('task_creation', 'Task Creation'),
                    ('task_reminder', 'Task Reminder'),
                    ('task_escalation', 'Task Escalation'),
                    ('task_completion', 'Task Completion'),
                ], max_length=50)),
                ('channel', models.CharField(choices=[
                    ('in_app', 'In-App'),
                    ('email', 'Email'),
                    ('push', 'Push Notification'),
                    ('sms', 'SMS'),
                ], max_length=50)),
                ('status', models.CharField(choices=[
                    ('sent', 'Sent'),
                    ('failed', 'Failed'),
                    ('pending', 'Pending'),
                ], default='pending', max_length=20)),
                ('error_message', models.TextField(blank=True)),
                ('sent_to', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notification_logs', to='tasks.task')),
            ],
            options={
                'verbose_name': 'Task Notification Log',
                'verbose_name_plural': 'Task Notification Logs',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='task',
            index=models.Index(fields=['assigned_to', 'status'], name='tasks_task_assigned_status_idx'),
        ),
        migrations.AddIndex(
            model_name='task',
            index=models.Index(fields=['assigned_to', 'due_date'], name='tasks_task_assigned_due_idx'),
        ),
        migrations.AddIndex(
            model_name='task',
            index=models.Index(fields=['created_at'], name='tasks_task_created_idx'),
        ),
        migrations.AddIndex(
            model_name='task',
            index=models.Index(fields=['is_overdue'], name='tasks_task_overdue_idx'),
        ),
        migrations.AddIndex(
            model_name='task',
            index=models.Index(fields=['status', 'priority'], name='tasks_task_status_priority_idx'),
        ),
    ]
