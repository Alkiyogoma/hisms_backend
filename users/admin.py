from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from users.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = list(DjangoUserAdmin.list_display) + ["role"]
    list_filter = list(DjangoUserAdmin.list_filter) + ["role"]
    search_fields = list(DjangoUserAdmin.search_fields) + ["username", "first_name", "last_name", "email"]
    fieldsets = list(DjangoUserAdmin.fieldsets) + [
        ("Institution", {"fields": ("role",)}),
    ]
    add_fieldsets = list(DjangoUserAdmin.add_fieldsets) + [
        ("Institution", {"fields": ("role",)}),
    ]

    # FRD OP 10.5: Only superusers can delete staff records
    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def delete_model(self, request, obj):
        """Set flag so User.delete() allows the hard-delete."""
        User._admin_delete_allowed = True
        obj.delete()

    def delete_queryset(self, request, queryset):
        """Bulk delete: set flag so User.delete() allows the hard-delete."""
        User._admin_delete_allowed = True
        queryset.delete()
