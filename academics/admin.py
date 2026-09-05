from django.contrib import admin
from django.core.exceptions import ValidationError

from academics.models import (
    AcademicYear, LessonPlan, Term, ExamTypeConfiguration, ECDTemplateConfiguration,
    ExamScore, ReportCard, ECDEvaluation, Subject, GradeClass, Room,
    ProgressionConfig, ProgressionCase, PromotionRun, ECDDomainConfig, ClassCapacity,
)


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("name", "is_current", "created_at")
    list_filter = ("is_current",)


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ("name", "academic_year", "start_date", "end_date", "is_locked", "created_at")
    list_filter = ("is_locked", "academic_year")
    search_fields = ("name",)

    def save_model(self, request, obj, form, change):
        if not obj.start_date or not obj.end_date:
            from django.contrib import messages
            messages.warning(
                request,
                "Term without start and end dates will prevent year-end progression "
                "calculation from running correctly for this academic year.",
            )
        super().save_model(request, obj, form, change)


@admin.register(LessonPlan)
class LessonPlanAdmin(admin.ModelAdmin):
    list_display = ("lesson_title", "teacher", "class_name", "subject_name", "week_start_date", "status")
    list_filter = ("status", "class_name", "subject_name")
    search_fields = ("lesson_title", "teacher__username", "teacher__first_name", "teacher__last_name")


@admin.register(ExamTypeConfiguration)
class ExamTypeConfigurationAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "weight_percentage", "max_score", "is_active", "display_order")
    list_filter = ("is_active", "display_order")
    search_fields = ("name", "code", "description")
    ordering = ("display_order", "name")
    
    fieldsets = (
        ("Basic Information", {
            "fields": ("name", "code", "description")
        }),
        ("Scoring Configuration", {
            "fields": ("weight_percentage", "max_score")
        }),
        ("Display Settings", {
            "fields": ("is_active", "display_order")
        }),
    )
    
    def save_model(self, request, obj, form, change):
        # Validate that total active weights don't exceed 100%
        if obj.is_active:
            active_weights = ExamTypeConfiguration.objects.filter(is_active=True)
            if not change:  # New exam type
                total_weight = sum(et.weight_percentage for et in active_weights) + obj.weight_percentage
            else:  # Existing exam type
                total_weight = sum(et.weight_percentage for et in active_weights if et.id != obj.id) + obj.weight_percentage
            
            if total_weight > 100:
                raise ValidationError(f"Total weight percentage cannot exceed 100%. Current total would be {total_weight}%")
        
        super().save_model(request, obj, form, change)


@admin.register(ECDTemplateConfiguration)
class ECDTemplateConfigurationAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "display_order")
    list_filter = ("is_active", "display_order")
    search_fields = ("name", "code", "description")
    ordering = ("display_order", "name")
    
    fieldsets = (
        ("Basic Information", {
            "fields": ("name", "code", "description")
        }),
        ("Display Settings", {
            "fields": ("is_active", "display_order")
        }),
    )


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "department", "is_active")
    list_filter = ("department", "is_active")
    search_fields = ("name",)


@admin.register(GradeClass)
class GradeClassAdmin(admin.ModelAdmin):
    list_display = ("name", "department", "max_capacity")
    list_filter = ("department",)
    search_fields = ("name",)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("name", "building", "capacity", "is_active")
    list_filter = ("is_active", "building")
    search_fields = ("name", "building")


@admin.register(ExamScore)
class ExamScoreAdmin(admin.ModelAdmin):
    list_display = ("student", "term", "subject_name", "exam_type", "score", "status", "entered_by")
    list_filter = ("status", "exam_type", "term", "subject_name")
    search_fields = ("student__admission_no", "student__first_name", "student__last_name", "subject_name")
    readonly_fields = ("entered_by", "created_at", "updated_at")
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("student", "term", "entered_by", "exam_type_config")


@admin.register(ProgressionConfig)
class ProgressionConfigAdmin(admin.ModelAdmin):
    list_display = ("academic_year_from", "academic_year_to", "minimum_average", "minimum_attendance", "retention_threshold", "created_by", "created_at")
    list_filter = ("academic_year_from", "academic_year_to")
    search_fields = ("academic_year_from__name", "academic_year_to__name")


@admin.register(ProgressionCase)
class ProgressionCaseAdmin(admin.ModelAdmin):
    list_display = ("student", "progression_config", "calculated_average", "status", "system_suggested_outcome", "hod_recommendation", "hos_decision")
    list_filter = ("status", "system_suggested_outcome", "progression_config")
    search_fields = ("student__admission_no", "student__first_name", "student__last_name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PromotionRun)
class PromotionRunAdmin(admin.ModelAdmin):
    list_display = ("academic_year_from", "academic_year_to", "executed_by", "status", "promoted_count", "retained_count", "graduated_count")
    list_filter = ("status", "academic_year_from", "academic_year_to")


@admin.register(ReportCard)
class ReportCardAdmin(admin.ModelAdmin):
    list_display = ("student", "term", "status", "is_ecd_report", "generated_by", "signed_off_by")
    list_filter = ("status", "is_ecd_report", "term")
    search_fields = ("student__admission_no", "student__first_name", "student__last_name")
    readonly_fields = ("generated_by", "signed_off_by", "signed_off_at", "published_at", "created_at", "updated_at")
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("student", "term", "generated_by", "signed_off_by")


@admin.register(ECDDomainConfig)
class ECDDomainConfigAdmin(admin.ModelAdmin):
    list_display = ("class_type", "domain_name", "sort_order", "competency_count")
    list_filter = ("class_type",)
    search_fields = ("domain_name",)

    def competency_count(self, obj):
        return len(obj.competencies or [])
    competency_count.short_description = "Competencies"


@admin.register(ClassCapacity)
class ClassCapacityAdmin(admin.ModelAdmin):
    list_display = ("grade_class", "academic_year", "max_capacity")
    list_filter = ("academic_year",)
    search_fields = ("grade_class__name",)
