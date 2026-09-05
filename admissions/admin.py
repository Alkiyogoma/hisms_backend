from django.contrib import admin

from admissions.models import AdmissionGrade, Applicant


@admin.register(Applicant)
class ApplicantAdmin(admin.ModelAdmin):
    list_display = (
        "child_full_name",
        "grade_applying_for",
        "parent_full_name",
        "inquiry_channel",
        "status",
        "created_at",
    )
    list_filter = ("status", "inquiry_channel", "grade_applying_for")
    search_fields = (
        "child_full_name",
        "parent_full_name",
        "parent_phone",
        "parent_email",
    )


@admin.register(AdmissionGrade)
class AdmissionGradeAdmin(admin.ModelAdmin):
    list_display = ("name", "department", "sort_order", "is_active", "created_at")
    list_filter = ("is_active", "department")
    search_fields = ("name",)
    ordering = ("sort_order", "name")
