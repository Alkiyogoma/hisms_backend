from django.contrib import admin
from core.models import MediaSettings, ArchiveRetentionPolicy, SchoolSettings, EmailTemplate, LessonPlanDeadline


@admin.register(MediaSettings)
class MediaSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "area",
        "allowed_extensions",
        "max_file_size_mb",
        "max_total_size_mb",
        "max_files",
        "duplicate_check",
        "is_active",
    )
    list_filter = ("is_active",)
    list_editable = ("is_active",)
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        (
            "Upload Area",
            {
                "fields": ("area", "is_active"),
            },
        ),
        (
            "File Type Restrictions",
            {
                "description": (
                    "Comma-separated file extensions without dots. "
                    "Example: <code>pdf,doc,docx,jpg,jpeg,png</code>"
                ),
                "fields": ("allowed_extensions",),
            },
        ),
        (
            "Size Limits",
            {
                "fields": ("max_file_size_mb", "max_total_size_mb", "max_files"),
            },
        ),
        (
            "Duplicate & Image Checks",
            {
                "fields": ("duplicate_check", "min_width", "min_height"),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        """Lock the area field once a row is saved (area is unique)."""
        if obj:
            return ("area",) + tuple(self.readonly_fields)
        return self.readonly_fields


@admin.register(SchoolSettings)
class SchoolSettingsAdmin(admin.ModelAdmin):
    list_display = ("school_name", "email_host", "email_host_user", "email_backend")
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        ("School Information", {
            "fields": ("school_name", "pass_mark", "admission_fee", "assessment_fee"),
        }),
        ("Attendance", {
            "fields": ("attendance_threshold_warn", "attendance_threshold_critical"),
        }),
        ("Notifications", {
            "fields": ("send_absentee_sms", "sms_sender_id", "enable_online_inquiry", "enable_auto_report_generation"),
        }),
        ("Email (SMTP)", {
            "fields": ("email_backend", "email_host", "email_port", "email_use_tls", "email_host_user", "email_host_password", "default_from_email"),
        }),
        ("WhatsApp", {
            "fields": ("whatsapp_sender_id",),
        }),
        ("PWA", {
            "fields": ("pwa_domain", "pwa_version"),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )


@admin.register(ArchiveRetentionPolicy)
class ArchiveRetentionPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "category",
        "retention_years",
        "warning_days",
        "auto_prune",
        "is_active",
        "last_pruned_at",
        "last_warning_at",
    )
    list_filter = ("is_active", "auto_prune")
    list_editable = ("retention_years", "warning_days", "auto_prune", "is_active")
    readonly_fields = ("created_at", "updated_at", "last_pruned_at", "last_warning_at")


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ("template_type", "name", "is_enabled", "is_default", "updated_at")
    list_filter = ("is_enabled", "is_default")
    list_editable = ("is_enabled",)
    readonly_fields = ("created_at", "updated_at", "is_default")
    search_fields = ("name", "subject", "template_type")

    fieldsets = (
        ("Template Info", {
            "fields": ("template_type", "name", "is_enabled", "is_default"),
        }),
        ("Subject", {
            "fields": ("subject",),
            "description": "Supports Django template syntax (e.g. {{ school_name }})",
        }),
        ("HTML Body", {
            "fields": ("html_body",),
            "description": "Full HTML email body. Leave empty for plain-text only.",
        }),
        ("Plain Text Body", {
            "fields": ("plain_body",),
            "description": "Required fallback for email clients that don't render HTML.",
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj and obj.is_default and "template_type" not in base:
            base.insert(0, "template_type")
        return tuple(base)


@admin.register(LessonPlanDeadline)
class LessonPlanDeadlineAdmin(admin.ModelAdmin):
    list_display = ("deadline_day", "deadline_time", "is_active", "created_by", "created_at")
    list_filter = ("is_active", "deadline_day")
    list_editable = ("is_active",)
    readonly_fields = ("created_at", "updated_at")
