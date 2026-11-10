from django.contrib import admin

from ptc.models import (
    LearnerAttributeRatingEntry,
    PTCDateChangeLog,
    PTCGenerationLog,
    PTCSubjectComment,
    PTCWindow,
)


@admin.register(PTCWindow)
class PTCWindowAdmin(admin.ModelAdmin):
    list_display = ("academic_year", "term_slot", "ptc_date", "is_published")
    list_filter = ("academic_year", "term_slot", "is_published")
    readonly_fields = (
        "notification_window_opens_at",
        "notification_window_closes_at",
        "comment_entry_window_opens_at",
        "comment_entry_window_closes_at",
    )


@admin.register(PTCSubjectComment)
class PTCSubjectCommentAdmin(admin.ModelAdmin):
    list_display = ("student", "subject_name", "ptc_window", "entered_by")
    list_filter = ("ptc_window", "subject_name")


@admin.register(LearnerAttributeRatingEntry)
class LearnerAttributeRatingEntryAdmin(admin.ModelAdmin):
    list_display = ("student", "ptc_window", "attribute_number", "rating", "entered_by")
    list_filter = ("ptc_window",)


@admin.register(PTCGenerationLog)
class PTCGenerationLogAdmin(admin.ModelAdmin):
    list_display = ("student", "class_name", "class_teacher", "viewed_as", "ptc_window", "opened_at")
    list_filter = ("ptc_window", "opened_at")
    readonly_fields = ("opened_at",)


@admin.register(PTCDateChangeLog)
class PTCDateChangeLogAdmin(admin.ModelAdmin):
    list_display = ("ptc_window", "changed_by", "old_ptc_date", "new_ptc_date", "changed_at")
    readonly_fields = ("changed_at",)
