from django.contrib import admin
from .models import WelfareObservation, WelfareAcknowledgment, WelfareObservationRevision


@admin.register(WelfareObservation)
class WelfareObservationAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "note_type", "status", "severity", "observation_date", "submitted_by")
    list_filter = ("note_type", "status", "severity")

    def get_queryset(self, request):
        # Include drafts and safeguarding follow-ups, which the default manager hides.
        return WelfareObservation.all_objects.all()


admin.site.register(WelfareAcknowledgment)
admin.site.register(WelfareObservationRevision)
