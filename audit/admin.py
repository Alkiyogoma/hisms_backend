from django.contrib import admin

from audit.models import AuditLog, DSARRequest


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action_type", "model_name", "object_id", "ip_address")
    list_filter = ("action_type", "model_name")
    search_fields = ("object_id", "model_name", "actor__username")
    readonly_fields = (
        "actor",
        "action_type",
        "model_name",
        "object_id",
        "before_snapshot",
        "after_snapshot",
        "ip_address",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DSARRequest)
class DSARRequestAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "requested_by",
        "target_type",
        "target_id",
        "target_label",
        "export_format",
        "status",
        "within_sla",
        "duration_seconds",
    )
    list_filter = ("status", "target_type", "export_format", "within_sla")
    search_fields = ("target_label", "requested_by__username")
    readonly_fields = (
        "requested_by",
        "target_type",
        "target_id",
        "target_label",
        "export_format",
        "status",
        "started_at",
        "completed_at",
        "duration_seconds",
        "modules_included",
        "modules_expected",
        "within_sla",
        "error_message",
        "file_size_bytes",
        "ip_address",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
