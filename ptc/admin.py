from django.contrib import admin

from ptc.models import (
    EnrichmentGrade,
    EnrichmentSubject,
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


@admin.register(EnrichmentSubject)
class EnrichmentSubjectAdmin(admin.ModelAdmin):
    list_display = ("subject", "assigned_teacher", "assigned_class", "is_active")
    list_filter = ("is_active",)


@admin.register(EnrichmentGrade)
class EnrichmentGradeAdmin(admin.ModelAdmin):
    list_display = ("student", "enrichment_subject", "term", "letter_grade", "entered_by", "is_locked")
    list_filter = ("enrichment_subject", "term", "is_locked")


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
    list_display = ("student", "class_teacher", "ptc_window", "opened_at")
    list_filter = ("ptc_window", "opened_at")
    readonly_fields = ("opened_at",)


@admin.register(PTCDateChangeLog)
class PTCDateChangeLogAdmin(admin.ModelAdmin):
    list_display = ("ptc_window", "changed_by", "old_ptc_date", "new_ptc_date", "changed_at")
    readonly_fields = ("changed_at",)
