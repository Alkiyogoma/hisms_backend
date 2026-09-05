from django.contrib import admin

from finance.models import (
    FinancePeriod, Invoice, Payment, Expense, ExpenseCategory,
    FeeStructure, FeeStructureItem, UnmatchedPayment,
    ReminderConfiguration, Concession, Budget, RecurringExpense,
    InvoiceLineItem, PaymentMethod,
)


@admin.register(FeeStructure)
class FeeStructureAdmin(admin.ModelAdmin):
    list_display = ("class_name", "term", "status", "is_active")
    list_filter = ("is_active", "status", "term")
    search_fields = ("class_name",)


@admin.register(FeeStructureItem)
class FeeStructureItemAdmin(admin.ModelAdmin):
    list_display = ("description", "structure", "category", "amount")
    list_filter = ("category",)


@admin.register(UnmatchedPayment)
class UnmatchedPaymentAdmin(admin.ModelAdmin):
    list_display = ("amount", "reference", "payment_date", "is_resolved")
    list_filter = ("is_resolved", "method")
    search_fields = ("reference",)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("id", "category", "amount", "expense_date", "vendor", "status", "created_by")
    list_filter = ("category", "status", "payment_method", "is_reimbursement")
    search_fields = ("description", "vendor", "reference")
    date_hierarchy = "expense_date"
    autocomplete_fields = ("created_by",)

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return [f.name for f in self.model._meta.fields]
        return ("created_at", "updated_at")


@admin.register(FinancePeriod)
class FinancePeriodAdmin(admin.ModelAdmin):
    list_display = ("name", "is_reconciled", "created_at")
    list_filter = ("is_reconciled",)
    search_fields = ("name",)


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "invoice_number", "student", "term", "total_due", "status", "is_finalized")
    list_filter = ("is_finalized", "status", "term")
    autocomplete_fields = ("student", "period", "term")
    search_fields = ("invoice_number", "student__admission_no", "student__first_name", "student__last_name")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "invoice", "amount", "method", "is_reversal", "created_by", "created_at")
    list_filter = ("is_reversal", "method")
    autocomplete_fields = ("invoice", "reversed_payment", "created_by")
    search_fields = ("reference", "invoice__student__admission_no", "created_by__username")

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return [f.name for f in self.model._meta.fields]
        return ("created_at", "updated_at")


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ("term", "category", "allocated_amount", "is_frozen")
    list_filter = ("is_frozen", "category", "term")
    search_fields = ("category",)


@admin.register(RecurringExpense)
class RecurringExpenseAdmin(admin.ModelAdmin):
    list_display = ("description", "category", "amount", "frequency", "next_due_date", "is_active")
    list_filter = ("is_active", "frequency", "category")
    search_fields = ("description", "vendor")


@admin.register(Concession)
class ConcessionAdmin(admin.ModelAdmin):
    list_display = ("student", "concession_type", "discount_value", "status", "approved_by")
    list_filter = ("status", "concession_type")
    search_fields = ("student__first_name", "student__last_name", "reason")


@admin.register(InvoiceLineItem)
class InvoiceLineItemAdmin(admin.ModelAdmin):
    list_display = ("description", "invoice", "amount", "is_discount")
    list_filter = ("is_discount",)


@admin.register(ReminderConfiguration)
class ReminderConfigurationAdmin(admin.ModelAdmin):
    list_display = ("is_active", "reminder_frequency", "reminder_type", "first_reminder_days", "last_processed_at")
    list_filter = ("is_active", "reminder_frequency", "reminder_type")
