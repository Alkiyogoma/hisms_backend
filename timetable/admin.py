from django.contrib import admin

from timetable.models import TimetableSlot


@admin.register(TimetableSlot)
class TimetableSlotAdmin(admin.ModelAdmin):
    list_display = ("day_of_week", "start_time", "end_time", "class_name", "subject_name", "teacher")
    list_filter = ("day_of_week", "class_name")
    search_fields = ("class_name", "subject_name", "teacher__username", "teacher__first_name", "teacher__last_name")
