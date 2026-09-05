from django.contrib import admin

from hr.models import (
    StaffProfile, StaffDocument, TeacherClassAssignment,
    OnboardingChecklistItem, StaffOnboardingProgress,
    PayrollRun, PayrollEntry,
    StatutoryFiling,
    LeaveAllocation, LeaveRequest,
    OffboardingRecord,
    RolloverConflict,
)


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("full_name", "employee_id", "job_title", "department", "employment_type", "is_active", "onboarding_step")
    list_filter = ("is_active", "department", "employment_type", "staff_category", "gender")
    search_fields = ("full_name", "employee_id", "job_title", "contact_phone", "contact_email")
    date_hierarchy = "employment_start_date"
    readonly_fields = ("onboarding_step", "onboarding_completed", "gross_salary", "nssf_employee_contribution", "nhif_contribution")

    fieldsets = (
        ("User Account", {"fields": ("user", "employee_id", "is_active")}),
        ("Personal Information", {"fields": ("full_name", "date_of_birth", "gender", "nationality", "marital_status", "residential_address")}),
        ("Emergency Contact", {"fields": ("emergency_contact_name", "emergency_contact_phone", "emergency_contact_relationship")}),
        ("Employment", {"fields": ("job_title", "department", "staff_category", "employment_type", "employment_start_date", "probation_end_date", "confirmation_date", "contract_end_date", "years_of_experience", "highest_qualification")}),
        ("Contact", {"fields": ("contact_phone", "contact_email")}),
        ("Salary & Banking", {"fields": ("basic_salary", "housing_allowance", "transport_allowance", "medical_allowance", "other_allowances", "gross_salary", "bank_name", "bank_account_number", "bank_branch")}),
        ("Statutory Information", {"fields": ("nssf_number", "tin_number", "paye_code", "heslb_loan_number", "nhif_number")}),
        ("Onboarding", {"fields": ("onboarding_step", "onboarding_completed")}),
        ("Departure", {"fields": ("departure_date", "departure_reason")}),
    )


@admin.register(StaffDocument)
class StaffDocumentAdmin(admin.ModelAdmin):
    list_display = ("name", "staff", "document_type", "uploaded_by", "created_at")
    list_filter = ("document_type",)
    search_fields = ("name", "staff__full_name")


@admin.register(TeacherClassAssignment)
class TeacherClassAssignmentAdmin(admin.ModelAdmin):
    list_display = ("teacher", "term", "grade_class", "is_class_teacher")
    list_filter = ("term", "is_class_teacher")
    search_fields = ("teacher__full_name", "grade_class__name")


@admin.register(OnboardingChecklistItem)
class OnboardingChecklistItemAdmin(admin.ModelAdmin):
    list_display = ("item_name", "step", "is_required", "order")
    list_filter = ("step", "is_required")
    ordering = ("step", "order")


@admin.register(StaffOnboardingProgress)
class StaffOnboardingProgressAdmin(admin.ModelAdmin):
    list_display = ("staff", "current_step", "is_completed", "completed_at")
    list_filter = ("is_completed",)


@admin.register(PayrollRun)
class PayrollRunAdmin(admin.ModelAdmin):
    list_display = ("period_name", "payroll_type", "status", "employee_count", "total_net_pay", "payment_date")
    list_filter = ("status", "payroll_type", "term")
    search_fields = ("period_name",)


@admin.register(PayrollEntry)
class PayrollEntryAdmin(admin.ModelAdmin):
    list_display = ("staff", "payroll_run", "gross_pay", "total_deductions", "net_pay", "status")
    list_filter = ("status", "payroll_run")
    search_fields = ("staff__full_name",)
    readonly_fields = ("gross_pay", "total_deductions", "net_pay")


@admin.register(StatutoryFiling)
class StatutoryFilingAdmin(admin.ModelAdmin):
    list_display = ("filing_type", "period_name", "payroll_run", "total_amount", "status", "due_date", "submitted_date")
    list_filter = ("status", "filing_type")
    search_fields = ("period_name", "reference_number")


@admin.register(LeaveAllocation)
class LeaveAllocationAdmin(admin.ModelAdmin):
    list_display = ("staff", "leave_type", "year", "total_days", "used_days", "remaining_days")
    list_filter = ("leave_type", "year")
    search_fields = ("staff__full_name",)


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ("staff", "leave_type", "start_date", "end_date", "total_days", "status")
    list_filter = ("status", "leave_type")
    search_fields = ("staff__full_name", "reason")
    date_hierarchy = "start_date"


@admin.register(OffboardingRecord)
class OffboardingRecordAdmin(admin.ModelAdmin):
    list_display = ("staff", "status", "last_working_day", "clearance_completed", "settlement_paid", "is_rehirable")
    list_filter = ("status", "settlement_paid", "is_rehirable")
    search_fields = ("staff__full_name",)


@admin.register(RolloverConflict)
class RolloverConflictAdmin(admin.ModelAdmin):
    list_display = ("teacher", "grade_class", "target_term", "conflict_type", "is_resolved", "created_at")
    list_filter = ("is_resolved", "conflict_type", "target_term")
    search_fields = ("teacher__full_name", "grade_class__name")
    readonly_fields = (
        "source_assignment", "target_term", "teacher", "grade_class",
        "conflict_type", "conflict_detail", "subjects_taught", "is_class_teacher",
        "created_at",
    )
