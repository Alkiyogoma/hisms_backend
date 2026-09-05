from django.contrib import admin

from discipline.models import DisciplineIncident


@admin.register(DisciplineIncident)
class DisciplineIncidentAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "severity", "escalated", "reported_by", "created_at")
    list_filter = ("severity", "escalated")
    autocomplete_fields = ("student", "reported_by")
    search_fields = ("summary", "student__admission_no")
