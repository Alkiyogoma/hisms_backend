from django.contrib import admin

from discipline.models import DisciplineIncident


@admin.register(DisciplineIncident)
class DisciplineIncidentAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "severity", "escalated", "reported_by", "created_at", "deleted_at")
    list_filter = ("severity", "escalated", ("deleted_at", admin.EmptyFieldListFilter))
    autocomplete_fields = ("student", "reported_by")
    search_fields = ("summary", "student__admission_no")
    readonly_fields = ("deleted_at", "deleted_by", "deletion_reason", "duplicate_of")

    def get_queryset(self, request):
        # Deleted incidents stay visible here, marked by "deleted at".
        return DisciplineIncident.all_objects.select_related("student", "reported_by")
