from django.contrib import admin

from communications.models import EmailSendLog


@admin.register(EmailSendLog)
class EmailSendLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "recipient_email", "subject", "action_type", "success", "actor", "ip_address")
    list_filter = ("success", "action_type")
    search_fields = ("recipient_email", "subject", "actor__username")
    readonly_fields = (
        "recipient_email",
        "subject",
        "success",
        "error_message",
        "action_type",
        "actor",
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
