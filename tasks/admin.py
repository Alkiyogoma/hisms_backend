from django.contrib import admin
from django.utils.html import format_html
from .models import Task, TaskHistory, TaskComment, TaskTemplate, TaskNotificationLog


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = (
        'title_short',
        'assigned_to_name',
        'status_badge',
        'priority_badge',
        'due_date_formatted',
        'is_overdue'
    )
    list_filter = ('status', 'priority', 'task_type', 'is_overdue', 'created_at')
    search_fields = ('title', 'assigned_to__first_name', 'assigned_to__last_name', 'description')
    readonly_fields = ('id', 'created_at', 'updated_at', 'completed_at', 'is_overdue')
    
    fieldsets = (
        ('Task Information', {
            'fields': ('id', 'task_type', 'title', 'description')
        }),
        ('Assignment', {
            'fields': ('assigned_to', 'created_by')
        }),
        ('Status & Priority', {
            'fields': ('status', 'priority', 'is_overdue')
        }),
        ('Timing', {
            'fields': ('due_date', 'created_at', 'updated_at', 'completed_at')
        }),
        ('Related Object', {
            'fields': ('content_type', 'object_id'),
            'classes': ('collapse',)
        }),
        ('Task Data', {
            'fields': ('metadata', 'actions_available'),
            'classes': ('collapse',)
        }),
        ('Notifications', {
            'fields': ('notification_sent', 'reminder_sent'),
            'classes': ('collapse',)
        }),
        ('Internal Notes', {
            'fields': ('internal_notes',)
        }),
    )
    
    def title_short(self, obj):
        return obj.title[:50] + '...' if len(obj.title) > 50 else obj.title
    title_short.short_description = 'Title'
    
    def assigned_to_name(self, obj):
        return obj.assigned_to.get_full_name()
    assigned_to_name.short_description = 'Assigned To'
    
    def status_badge(self, obj):
        colors = {
            'pending': '#FF6B6B',
            'in_progress': '#4ECDC4',
            'completed': '#45B7D1',
            'deferred': '#FFA07A',
            'cancelled': '#95A5A6',
        }
        color = colors.get(obj.status, '#95A5A6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    
    def priority_badge(self, obj):
        colors = {
            'critical': '#D32F2F',
            'high': '#F57C00',
            'medium': '#1976D2',
            'low': '#388E3C',
        }
        color = colors.get(obj.priority, '#95A5A6')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 3px;">{}</span>',
            color,
            obj.get_priority_display()
        )
    priority_badge.short_description = 'Priority'
    
    def due_date_formatted(self, obj):
        from django.utils.timezone import now
        if obj.due_date < now() and obj.status != 'completed':
            return format_html(
                '<span style="color: red; font-weight: bold;">{}</span>',
                obj.due_date.strftime('%Y-%m-%d %H:%M')
            )
        return obj.due_date.strftime('%Y-%m-%d %H:%M')
    due_date_formatted.short_description = 'Due Date'


@admin.register(TaskHistory)
class TaskHistoryAdmin(admin.ModelAdmin):
    list_display = ('task', 'action', 'changed_by', 'created_at')
    list_filter = ('action', 'created_at')
    search_fields = ('task__title', 'comment')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(TaskComment)
class TaskCommentAdmin(admin.ModelAdmin):
    list_display = ('task', 'author', 'is_internal', 'created_at')
    list_filter = ('is_internal', 'created_at')
    search_fields = ('task__title', 'content')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(TaskTemplate)
class TaskTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'task_type', 'priority', 'is_active')
    list_filter = ('task_type', 'priority', 'is_active')
    search_fields = ('name', 'title_template')


@admin.register(TaskNotificationLog)
class TaskNotificationLogAdmin(admin.ModelAdmin):
    list_display = ('task', 'notification_type', 'sent_to', 'channel', 'status', 'created_at')
    list_filter = ('notification_type', 'channel', 'status', 'created_at')
    search_fields = ('task__title', 'sent_to__first_name', 'sent_to__last_name')
    readonly_fields = ('id', 'created_at', 'updated_at')
