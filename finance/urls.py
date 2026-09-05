from django.urls import path
from . import views
from .views_email import SendInvoiceEmailView

app_name = "finance"

urlpatterns = [
    path("", views.FinanceDashboardView.as_view(), name="dashboard"),
    path("invoices/", views.InvoiceListView.as_view(), name="invoice_list"),
    path("invoices/<int:pk>/", views.InvoiceDetailView.as_view(), name="invoice_detail"),
    path("invoices/<int:pk>/print/", views.InvoicePrintView.as_view(), name="invoice_print"),
    path("payments/<int:pk>/receipt/", views.PaymentReceiptPrintView.as_view(), name="payment_receipt_print"),
    path("invoices/<int:pk>/pay/", views.RecordPaymentView.as_view(), name="record_payment"),
    path("invoices/correction/", views.PaymentCorrectionView.as_view(), name="payment_correction"),
    path("invoices/generate/", views.AutoGenerateInvoicesView.as_view(), name="auto_generate_invoices"),
    path("invoices/generate-midterm/", views.AutoGenerateMidtermInvoicesView.as_view(), name="auto_generate_midterm_invoices"),
    path("fee-structures/", views.FeeStructureSetupView.as_view(), name="fee_structure_setup"),
    path("fee-structures/create/", views.FeeStructureSetupView.as_view(), name="fee_structure_create"),
    path("unmatched-payments/", views.UnmatchedPaymentListView.as_view(), name="unmatched_payments"),

    path("parent/invoices/", views.ParentInvoiceListView.as_view(), name="parent_invoices"),
    path("parent/invoices/<int:pk>/", views.ParentInvoiceDetailView.as_view(), name="parent_invoice_detail"),

    path("invoices/<int:pk>/remind/", views.SendInvoiceReminderView.as_view(), name="send_reminder"),
    path("invoices/<int:pk>/email/", SendInvoiceEmailView.as_view(), name="send_invoice_email"),
    path("fee-structures/copy/", views.CopyFeeStructureView.as_view(), name="fee_structure_copy"),

    # Expenses (Phase 2) — A3 Deep
    path("expenses/", views.ExpenseListView.as_view(), name="expense_list"),
    path("expenses/create/", views.ExpenseCreateView.as_view(), name="expense_create"),
    path("expenses/<int:pk>/", views.ExpenseDetailView.as_view(), name="expense_detail"),
    path("expenses/<int:pk>/edit/", views.ExpenseUpdateView.as_view(), name="expense_edit"),
    path("expenses/<int:pk>/approve/", views.ExpenseApproveView.as_view(), name="expense_approve"),

    # Budgets
    path("budgets/", views.BudgetListView.as_view(), name="budget_list"),
    path("budgets/create/", views.BudgetCreateView.as_view(), name="budget_create"),
    path("budgets/<int:pk>/edit/", views.BudgetCreateView.as_view(), name="budget_edit"),
    path("budgets/<int:pk>/", views.BudgetDetailView.as_view(), name="budget_detail"),

    # A7 — Budget vs Actual report
    path("reports/budget-vs-actual/", views.BudgetVsActualReportView.as_view(), name="budget_vs_actual"),

    # A7 — Batch expense approval
    path("expenses/batch-approval/", views.ExpenseBatchApprovalView.as_view(), name="expense_batch_approval"),

    # Recurring expenses
    path("recurring/", views.RecurringExpenseListView.as_view(), name="recurring_list"),
    path("recurring/create/", views.RecurringExpenseCreateView.as_view(), name="recurring_create"),
    path("recurring/<int:pk>/edit/", views.RecurringExpenseCreateView.as_view(), name="recurring_edit"),
    path("recurring/process/", views.RecurringExpenseProcessView.as_view(), name="recurring_process"),

    # Collection by class drill-down
    path("collection/class/<str:class_name>/", views.ClassCollectionDetailView.as_view(), name="class_collection_detail"),

    # Financial Reports (Phase 3)
    path("reports/", views.FinancialReportsView.as_view(), name="financial_reports"),

    # A2 — Batch payment recording
    path("payments/batch/", views.BatchPaymentView.as_view(), name="batch_payment"),

    # A2 — Payment receipts list
    path("payments/", views.PaymentReceiptListView.as_view(), name="payment_list"),

    # A2 — Bank statement upload / reconciliation
    path("bank-statement/", views.BankStatementUploadView.as_view(), name="bank_statement_upload"),

    # A4 — Bulk invoice print
    path("invoices/bulk-print/", views.BulkInvoicePrintView.as_view(), name="bulk_invoice_print"),

    # A4 — Invoice correction
    path("invoices/<int:pk>/correct/", views.InvoiceCorrectionView.as_view(), name="invoice_correction"),

    # A2.3 — Manual/Misc Invoice creation
    path("invoices/manual/", views.ManualInvoiceCreateView.as_view(), name="manual_invoice_create"),

    # A5.3 — CSV Export for reports
    path("reports/export/csv/", views.ReportCSVExportView.as_view(), name="report_csv_export"),

    # A5.1 — Student Account Statement
    path("statements/<int:student_id>/", views.StudentAccountStatementView.as_view(), name="student_statement"),

    # Fee Structure Publish/Lock
    path("fee-structures/<int:pk>/publish/", views.FeeStructurePublishView.as_view(), name="fee_structure_publish"),
    path("fee-structures/<int:pk>/lock/", views.FeeStructureLockView.as_view(), name="fee_structure_lock"),
    path("fee-structures/<int:pk>/unlock/", views.FeeStructureUnlockView.as_view(), name="fee_structure_unlock"),

    # A4 — Overdue Accounts
    path("overdue/", views.OverdueAccountsListView.as_view(), name="overdue_accounts"),
    path("overdue/send-reminder/", views.SendBulkReminderView.as_view(), name="send_bulk_reminder"),

    # A4.3 — Reminder Configuration
    path("reminder-configuration/", views.ReminderConfigurationView.as_view(), name="reminder_configuration"),

    # A2.2 — Assessment Fee Invoice (from admissions pipeline)
    path("assessment/<int:applicant_id>/", views.AssessmentFeeInvoiceView.as_view(), name="assessment_fee_invoice"),

    # A6 — Finance Period Management
    path("periods/", views.FinancePeriodListView.as_view(), name="period_list"),
    path("periods/create/", views.FinancePeriodCreateView.as_view(), name="period_create"),
    path("periods/<int:pk>/edit/", views.FinancePeriodCreateView.as_view(), name="period_edit"),
    path("periods/<int:pk>/", views.FinancePeriodDetailView.as_view(), name="period_detail"),
    path("periods/<int:pk>/reconcile/", views.FinancePeriodReconcileView.as_view(), name="period_reconcile"),

    # A6 — Opening Balances
    path("periods/<int:pk>/opening-balances/", views.OpeningBalanceSetupView.as_view(), name="opening_balance"),

    # A6 — Finance Configuration
    path("config/", views.FinanceConfigView.as_view(), name="finance_config"),

    # A5 — Concession management
    path("concessions/", views.ConcessionListView.as_view(), name="concession_list"),
    path("concessions/create/", views.ConcessionCreateView.as_view(), name="concession_create"),
    path("concessions/<int:pk>/approve/", views.ConcessionApproveView.as_view(), name="concession_approve"),
]


