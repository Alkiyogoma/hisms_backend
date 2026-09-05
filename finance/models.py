from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from core.models import TimeStampedModel
from students.models import Student
from django.utils import timezone


class FeeCategory(models.TextChoices):
    TUITION = "tuition", "Tuition"
    ASSESSMENT = "assessment", "Assessment fee"
    ACTIVITY = "activity", "Activity fee"
    UNIFORM = "uniform", "Uniform / stationery"
    OTHER = "other", "Other"


class SiblingDiscountMode(models.TextChoices):
    PERCENTAGE = "percentage", "Percentage"
    FIXED = "fixed", "Fixed Amount"


class FeeStructureStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    LOCKED = "locked", "Locked"


class FeeStructure(TimeStampedModel):
    """
    Fee structure per class per term — FR-FIN-001…003.
    Admin creates these; invoice generation reads from them.
    Status lifecycle: Draft → Published → Locked
    """
    from academics.models import Term
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="fee_structures")
    class_name = models.CharField(max_length=64, db_index=True)
    is_active = models.BooleanField(default=True)

    # A1.2 — Publish/Lock workflow status
    status = models.CharField(
        max_length=12,
        choices=FeeStructureStatus.choices,
        default=FeeStructureStatus.DRAFT,
        db_index=True,
        help_text="Draft=editable, Published=visible to invoicing, Locked=read-only"
    )
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="locked_fee_structures"
    )
    locked_at = models.DateTimeField(null=True, blank=True)

    # A1.1 Extended fee fields
    late_pickup_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0,
        help_text="Late pickup charge per instance (TZS)")
    sibling_discount_mode = models.CharField(
        max_length=12, choices=SiblingDiscountMode.choices, default=SiblingDiscountMode.PERCENTAGE
    )
    sibling_discount_value = models.DecimalField(max_digits=10, decimal_places=2, default=0,
        help_text="Discount amount — interpret as percentage or fixed TZS based on mode")
    assessment_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0,
        help_text="Assessment fee used by Admissions pipeline (TZS)")

    class Meta:
        unique_together = ("term", "class_name")

    def __str__(self) -> str:
        return f"Fee structure: {self.class_name} ({self.term}) - {self.get_status_display()}"

    def can_edit(self) -> bool:
        return self.status != FeeStructureStatus.LOCKED

    def publish(self, actor=None):
        if self.status == FeeStructureStatus.DRAFT:
            self.status = FeeStructureStatus.PUBLISHED
            self.save(update_fields=["status", "updated_at"])

    def lock(self, actor=None):
        from django.utils import timezone
        self.status = FeeStructureStatus.LOCKED
        self.locked_by = actor
        self.locked_at = timezone.now()
        self.save(update_fields=["status", "locked_by_id", "locked_at", "updated_at"])

    def unlock(self, actor=None):
        # Only Super Admin can unlock
        self.status = FeeStructureStatus.PUBLISHED
        self.locked_by = None
        self.locked_at = None
        self.save(update_fields=["status", "locked_by_id", "locked_at", "updated_at"])


class FeeStructureItem(TimeStampedModel):
    """Line item within a FeeStructure."""
    structure = models.ForeignKey(FeeStructure, on_delete=models.CASCADE, related_name="items")
    category = models.CharField(max_length=20, choices=FeeCategory.choices, default=FeeCategory.TUITION)
    description = models.CharField(max_length=120)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self) -> str:
        return f"{self.description}: {self.amount}"


class FinancePeriod(TimeStampedModel):
    """
    A6 — Finance period management.
    Represents a fiscal period (month, quarter, term) for financial tracking.
    Reconciled periods become read-only for invoices and payments.
    """
    name = models.CharField(max_length=50, unique=True)
    start_date = models.DateField(null=True, blank=True, help_text="Period start date")
    end_date = models.DateField(null=True, blank=True, help_text="Period end date")
    is_reconciled = models.BooleanField(default=False, help_text="Lock period once reconciled")
    notes = models.TextField(blank=True, help_text="Internal notes about this period")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="created_finance_periods"
    )
    reconciled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="reconciled_finance_periods"
    )
    reconciled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-start_date", "-created_at"]
        verbose_name = "Finance Period"
        verbose_name_plural = "Finance Periods"

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError("Start date must be before end date.")
        if self.pk:
            prior = FinancePeriod.objects.filter(pk=self.pk).first()
            if prior and prior.is_reconciled and not self.is_reconciled:
                raise ValidationError("Reconciled finance periods cannot be reopened.")

    def __str__(self) -> str:
        return f"{self.name} ({self.start_date} to {self.end_date})" if self.start_date else self.name

    def total_invoiced(self):
        return self.invoices.aggregate(t=Sum("total_due"))["t"] or 0

    def total_collected(self):
        from django.db.models import Sum
        return Payment.objects.filter(
            invoice__period=self,
            is_reversal=False,
        ).aggregate(t=Sum("amount"))["t"] or 0

    def total_expenses(self):
        if not self.start_date or not self.end_date:
            return 0
        return Expense.objects.filter(expense_date__gte=self.start_date, expense_date__lte=self.end_date).aggregate(t=Sum("amount"))["t"] or 0


class OpeningBalanceType(models.TextChoices):
    INVOICE = "invoice", "Invoice / Receivable"
    PAYMENT = "payment", "Payment / Cash"
    EXPENSE = "expense", "Expense / Payable"
    BANK = "bank", "Bank Balance"
    OTHER = "other", "Other"


class OpeningBalance(TimeStampedModel):
    """
    A6 — Opening balance for a finance period.
    Allows setting initial account balances when opening a new period.
    """
    period = models.ForeignKey(
        FinancePeriod, on_delete=models.PROTECT,
        related_name="opening_balances"
    )
    balance_type = models.CharField(
        max_length=12, choices=OpeningBalanceType.choices,
        default=OpeningBalanceType.INVOICE, db_index=True
    )
    description = models.CharField(max_length=120, help_text="Description of this opening balance entry")
    amount = models.DecimalField(max_digits=14, decimal_places=2, help_text="Opening balance amount (TZS)")
    is_debit = models.BooleanField(default=True, help_text="True=debit (asset), False=credit (liability)")
    reference = models.CharField(max_length=120, blank=True, help_text="Optional reference document or invoice")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="opening_balance_entries"
    )

    class Meta:
        ordering = ["-period__start_date", "balance_type"]
        verbose_name = "Opening Balance"
        verbose_name_plural = "Opening Balances"

    def clean(self):
        super().clean()
        if self.amount <= 0:
            raise ValidationError("Opening balance amount must be greater than zero.")
        if self.period.is_reconciled:
            raise ValidationError("Cannot modify opening balances in a reconciled period.")

    def __str__(self):
        return f"{self.get_balance_type_display()}: TZS {self.amount:,.0f} ({self.period.name})"



class InvoiceStatus(models.TextChoices):
    UNPAID = "unpaid", "Unpaid"
    PARTIAL = "partial", "Partially Paid"
    PAID = "paid", "Paid"
    OVERDUE = "overdue", "Overdue"

class Invoice(TimeStampedModel):
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="invoices", null=True, blank=True)
    applicant = models.ForeignKey("admissions.Applicant", on_delete=models.PROTECT, related_name="invoices", null=True, blank=True)

    period = models.ForeignKey(FinancePeriod, on_delete=models.PROTECT, related_name="invoices", null=True, blank=True)
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="invoices", null=True, blank=True)
    invoice_number = models.CharField(max_length=50, unique=True, null=True, blank=True)
    amount_due = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_due = models.DecimalField(max_digits=12, decimal_places=2) # amount_due - discount_amount
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.UNPAID)
    is_finalized = models.BooleanField(default=False)
    last_reminder_at = models.DateTimeField(null=True, blank=True)
    last_reminder_type = models.CharField(max_length=20, blank=True, db_index=True)

    def clean(self):
        super().clean()
        if self.amount_due <= 0:
            raise ValidationError("Invoice amount due must be greater than zero.")
        if not self.student_id and not self.applicant_id:
            raise ValidationError("Invoice must be linked to either a student or an applicant.")

        if self.period_id and self.period.is_reconciled:

            raise ValidationError("Cannot create or modify invoices in a reconciled period.")

    def save(self, *args, **kwargs):
        # Auto-calculate total_due - Defensive logic for FR-FIN
        self.total_due = self.amount_due - self.discount_amount
        
        self.full_clean()
        if self.pk:
            prior = Invoice.objects.filter(pk=self.pk).first()
            if prior:
                if prior.is_finalized:
                    locked_fields = ("student_id", "period_id", "amount_due", "is_finalized")
                    for field in locked_fields:
                        if getattr(self, field) != getattr(prior, field):
                            raise ValidationError("Finalized invoices cannot be materially changed.")
                if prior.period and prior.period.is_reconciled:
                    raise ValidationError("Invoices in reconciled periods are read-only.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.invoice_number or f"INV-{self.id}"


class InvoiceLineItem(TimeStampedModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="line_items")
    description = models.CharField(max_length=120)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    is_discount = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"{self.description}: {self.amount}"


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    BANK_TRANSFER = "bank_transfer", "Bank Transfer"
    CHEQUE = "cheque", "Cheque"
    MOBILE_MONEY = "mobile_money", "Mobile Money"
    MPESA = "mpesa", "M-Pesa"
    TIGO_PESA = "tigo_pesa", "Tigo Pesa"
    AIRTEL_MONEY = "airtel_money", "Airtel Money"
    HALOPESA = "halopesa", "HaloPesa"
    CREDIT_CARD = "credit_card", "Credit Card"
    DEBIT_CARD = "debit_card", "Debit Card"
    OTHER = "other", "Other"



class FinanceConfig(TimeStampedModel):
    """
    A6 — System-wide finance configuration settings.
    Singleton model — only one configuration record should exist.
    """
    # General settings
    school_currency = models.CharField(max_length=10, default="TZS", help_text="Default currency code")
    fiscal_year_start = models.DateField(null=True, blank=True, help_text="Start of the fiscal year")
    fiscal_year_end = models.DateField(null=True, blank=True, help_text="End of the fiscal year")
    
    # Invoice defaults
    invoice_prefix = models.CharField(max_length=10, default="INV", help_text="Default invoice number prefix")
    invoice_due_days = models.PositiveIntegerField(default=30, help_text="Default days until invoice due")
    invoice_terms = models.TextField(blank=True, help_text="Default invoice terms and conditions")
    
    # Payment defaults
    default_payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices,
        default=PaymentMethod.CASH
    )
    
    # Reminder defaults
    reminder_grace_days = models.PositiveIntegerField(default=7, help_text="Days after due date before reminders start")
    auto_reminder_enabled = models.BooleanField(default=False, help_text="Enable automated fee reminders")
    
    # Notification
    finance_officer_email = models.EmailField(blank=True, help_text="Email for finance notifications")
    
    # Late fee defaults
    late_fee_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        help_text="Late fee as percentage of outstanding (e.g. 5 = 5%)"
    )
    late_fee_max_days = models.PositiveIntegerField(default=90, help_text="Maximum days to apply late fee")
    
    # Metadata
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="finance_config_updates"
    )
    
    class Meta:
        verbose_name = "Finance Configuration"
        verbose_name_plural = "Finance Configuration"

    def save(self, *args, **kwargs):
        # Enforce singleton by always updating the first/only record
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return "Finance Configuration"

    @classmethod
    def get_config(cls):
        """Get or create the singleton config record."""
        config, created = cls.objects.get_or_create(pk=1)
        return config


class Payment(TimeStampedModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments", null=True, blank=True)
    is_unmatched = models.BooleanField(default=False, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=40, choices=PaymentMethod.choices)
    payment_date = models.DateField(default=timezone.now, db_index=True, help_text="Date payment was received")
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    is_reversal = models.BooleanField(default=False)
    reversed_payment = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversal_entry",
    )
    correction_reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments")

    def clean(self):
        super().clean()
        if self.invoice_id and self.invoice.period and self.invoice.period.is_reconciled and not self.is_reversal:
            raise ValidationError("Cannot record payments against reconciled periods.")
        if self.is_reversal and not self.correction_reason:
            raise ValidationError("A reason must be provided for a reversal.")
        if self.amount == 0:
            raise ValidationError("Payment amount cannot be zero.")

    def save(self, *args, **kwargs):
        self.full_clean()
        if self.pk:
            prior = Payment.objects.filter(pk=self.pk).only(
                "invoice_id",
                "amount",
                "method",
                "reference",
                "is_reversal",
                "reversed_payment_id",
                "created_by_id",
            ).first()
            if prior:
                if (
                    prior.invoice_id != self.invoice_id
                    or prior.amount != self.amount
                    or prior.method != self.method
                    or prior.reference != self.reference
                    or prior.is_reversal != self.is_reversal
                    or prior.reversed_payment_id != self.reversed_payment_id
                    or prior.correction_reason != self.correction_reason
                    or prior.created_by_id != self.created_by_id
                ):
                    raise ValidationError("Payment records are immutable and cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Payment records are immutable and cannot be deleted.")

    def __str__(self) -> str:
        return f"PAY-{self.id} {self.amount}"

class UnmatchedPayment(TimeStampedModel):
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField(default=timezone.now)
    method = models.CharField(max_length=40)
    reference = models.CharField(max_length=120, blank=True)
    bank_statement_details = models.TextField(blank=True)
    
    is_resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="resolved_unmatched_payments")
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, null=True, blank=True, related_name="resolved_from_unmatched")
    
    def __str__(self):
        return f"UNMATCHED {self.amount} - {self.reference}"


class ExpenseStatus(models.TextChoices):
    PENDING = "pending", "Pending Approval"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class ExpenseCategory(models.TextChoices):
    SALARY = "salary", "Salaries & Wages"
    UTILITIES = "utilities", "Utilities (Water, Electricity, Internet)"
    SUPPLIES = "supplies", "School Supplies & Materials"
    MAINTENANCE = "maintenance", "Maintenance & Repairs"
    TRANSPORT = "transport", "Transport & Fuel"
    CATERING = "catering", "Catering & Meals"
    EVENTS = "events", "Events & Activities"
    PROFESSIONAL = "professional", "Professional Services"
    RENT = "rent", "Rent & Lease"
    TECHNOLOGY = "technology", "Technology & Software"
    MARKETING = "marketing", "Marketing & Advertising"
    INSURANCE = "insurance", "Insurance"
    TAX = "tax", "Taxes & Licenses"
    OTHER_EXPENSE = "other", "Other"


class Budget(TimeStampedModel):
    """
    Budget allocation per category per term.
    Tracks planned vs actual spending.
    """
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="budgets")
    category = models.CharField(max_length=30, choices=ExpenseCategory.choices, db_index=True)
    allocated_amount = models.DecimalField(max_digits=14, decimal_places=2, help_text="Budgeted amount (TZS)")
    notes = models.TextField(blank=True)
    is_frozen = models.BooleanField(default=False, help_text="Lock budget once approved")

    class Meta:
        unique_together = ("term", "category")
        verbose_name = "Budget"
        verbose_name_plural = "Budgets"

    def spent_amount(self):
        """Return total approved expense amount for this term + category."""
        total = Expense.objects.filter(
            term=self.term,
            category=self.category,
            status=ExpenseStatus.APPROVED,
        ).aggregate(t=Sum("amount"))["t"] or 0
        return total

    def remaining_amount(self):
        return self.allocated_amount - self.spent_amount()

    def utilization_pct(self):
        if self.allocated_amount <= 0:
            return 0
        return round((self.spent_amount() / self.allocated_amount) * 100, 1)

    def __str__(self):
        return f"Budget {self.get_category_display()} ({self.term}): TZS {self.allocated_amount:,.0f}"


class RecurringExpense(TimeStampedModel):
    """
    Recurring expense template — generates Expense entries on schedule.
    """
    FREQUENCY_CHOICES = [
        ("monthly", "Monthly"),
        ("termly", "Per Term"),
        ("yearly", "Yearly"),
    ]
    PAYMENT_METHODS = [
        ("cash", "Cash"),
        ("bank_transfer", "Bank Transfer"),
        ("cheque", "Cheque"),
        ("mobile_money", "Mobile Money"),
    ]

    category = models.CharField(max_length=30, choices=ExpenseCategory.choices, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.TextField()
    frequency = models.CharField(max_length=12, choices=FREQUENCY_CHOICES, default="monthly")
    vendor = models.CharField(max_length=120, blank=True)
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    payment_method = models.CharField(max_length=30, choices=PAYMENT_METHODS, default="bank_transfer")
    is_active = models.BooleanField(default=True, db_index=True)
    next_due_date = models.DateField(db_index=True)
    last_generated_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="recurring_expenses")

    class Meta:
        ordering = ["next_due_date", "-created_at"]
        verbose_name = "Recurring Expense"
        verbose_name_plural = "Recurring Expenses"

    def __str__(self):
        return f"{self.description[:40]} ({self.get_frequency_display()}): TZS {self.amount:,.0f}"

    def process_due(self, actor):
        """Create an actual Expense entry for this recurring expense."""
        from datetime import date
        if not self.is_active:
            return None
        expense = Expense.objects.create(
            category=self.category,
            amount=self.amount,
            description=f"[Recurring] {self.description}",
            expense_date=date.today(),
            payment_method=self.payment_method,
            reference=self.reference,
            vendor=self.vendor,
            notes=self.notes,
            created_by=actor,
            is_reimbursement=False,
            status=ExpenseStatus.PENDING,
        )
        self.last_generated_date = date.today()
        # Advance next_due_date
        from dateutil.relativedelta import relativedelta
        if self.frequency == "monthly":
            self.next_due_date += relativedelta(months=1)
        elif self.frequency == "termly":
            self.next_due_date += relativedelta(months=4)
        elif self.frequency == "yearly":
            self.next_due_date += relativedelta(years=1)
        self.save(update_fields=["last_generated_date", "next_due_date"])
        return expense


class Expense(TimeStampedModel):
    """
    School expenditure tracking.
    Records outgoing payments for operational costs.
    """
    PAYMENT_METHODS = [
        ("cash", "Cash"),
        ("bank_transfer", "Bank Transfer"),
        ("cheque", "Cheque"),
        ("mobile_money", "Mobile Money"),
    ]
    
    category = models.CharField(max_length=30, choices=ExpenseCategory.choices, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.TextField()
    expense_date = models.DateField(default=timezone.now, db_index=True)
    payment_method = models.CharField(max_length=30, choices=PAYMENT_METHODS, default="cash")
    reference = models.CharField(max_length=120, blank=True, help_text="Receipt or invoice reference")
    vendor = models.CharField(max_length=120, blank=True, help_text="Payee or vendor name")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="expenses")
    is_reimbursement = models.BooleanField(default=False, help_text="Is this a reimbursement to staff?")

    # A3 — Approval workflow
    term = models.ForeignKey("academics.Term", on_delete=models.SET_NULL, null=True, blank=True, related_name="expenses")
    status = models.CharField(max_length=12, choices=ExpenseStatus.choices, default=ExpenseStatus.PENDING, db_index=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="approved_expenses"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-expense_date", "-created_at"]
        verbose_name = "Expense"
        verbose_name_plural = "Expenses"
    
    def clean(self):
        super().clean()
        if self.amount <= 0:
            raise ValidationError("Expense amount must be greater than zero.")
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"EXP-{self.id} {self.get_category_display()}: TZS {self.amount:,.0f} ({self.expense_date})"


class ConcessionType(models.TextChoices):
    """Types of fee concessions / discounts that can be awarded."""
    SIBLING = "sibling", "Sibling Discount"
    MERIT = "merit", "Merit Scholarship"
    FINANCIAL_AID = "financial_aid", "Financial Aid"
    STAFF = "staff", "Staff Concession"
    NEED_BASED = "need_based", "Needs-Based Concession"
    OTHER = "other", "Other"


class ConcessionStatus(models.TextChoices):
    PENDING = "pending", "Pending Approval"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"


class ReminderConfiguration(TimeStampedModel):
    """
    A4.3 — Auto-reminder configuration for overdue fee accounts.
    Defines when and how reminders are sent.
    """
    FREQUENCY_CHOICES = [
        ("daily", "Daily"),
        ("weekly", "Weekly"),
        ("every_3_days", "Every 3 Days"),
        ("custom", "Custom Schedule"),
    ]
    
    REMINDER_TYPE_CHOICES = [
        ("email", "Email"),
        ("sms", "SMS"),
        ("both", "Both Email & SMS"),
    ]
    
    is_active = models.BooleanField(default=True, help_text="Enable auto-reminder system")
    reminder_frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default="weekly")
    reminder_type = models.CharField(max_length=10, choices=REMINDER_TYPE_CHOICES, default="email")
    
    # Days after due date to send specific reminders
    first_reminder_days = models.PositiveIntegerField(default=7, help_text="Days after due date for first reminder")
    second_reminder_days = models.PositiveIntegerField(default=14, help_text="Days after due date for second reminder")
    final_reminder_days = models.PositiveIntegerField(default=30, help_text="Days after due date for final/escalation reminder")
    
    # Template customization
    reminder_template_sms = models.TextField(blank=True, help_text="SMS template. Use {{student_name}}, {{balance}}, {{due_date}}")
    reminder_template_email = models.TextField(blank=True, help_text="Email template. Use {{student_name}}, {{balance}}, {{due_date}}")
    
    # Escalation
    auto_escalate_to_hos = models.BooleanField(default=False, help_text="Escalate to HOS after final reminder")
    escalation_days = models.PositiveIntegerField(default=45, help_text="Days after due date for HOS escalation")
    
    # Notification target
    notify_finance_officer = models.BooleanField(default=True, help_text="Notify Finance Officer when reminders are sent")
    
    # Metadata
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reminder_config_updates")
    last_processed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        verbose_name = "Reminder Configuration"
        verbose_name_plural = "Reminder Configurations"
    
    def __str__(self):
        status = 'Active' if self.is_active else 'Inactive'
        return f"Reminder Config ({status}) - {self.get_reminder_frequency_display()}"

    def clean(self):
        super().clean()
        if self.first_reminder_days >= self.second_reminder_days:
            raise ValidationError("First reminder must be before second reminder.")
        if self.second_reminder_days >= self.final_reminder_days:
            raise ValidationError("Second reminder must be before final reminder.")


class Concession(TimeStampedModel):
    """
    A5 — Fee concession / discount / scholarship award.
    Grants a reduction on a student's fees for a given term.
    Approved concessions are applied during invoice generation.
    """
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="concessions")
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="concessions")
    concession_type = models.CharField(max_length=20, choices=ConcessionType.choices, db_index=True)
    reason = models.CharField(max_length=255, blank=True, help_text="Reason or description for the concession")

    # Discount value — either percentage (e.g. 25 = 25%) or fixed TZS amount
    discount_mode = models.CharField(
        max_length=12, choices=SiblingDiscountMode.choices, default=SiblingDiscountMode.PERCENTAGE
    )
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, help_text="Percentage or fixed TZS amount")

    status = models.CharField(
        max_length=12, choices=ConcessionStatus.choices,
        default=ConcessionStatus.PENDING, db_index=True
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="approved_concessions"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="created_concessions"
    )
    notes = models.TextField(blank=True, help_text="Internal notes about this concession")

    # Valid date range for the concession
    valid_from = models.DateField(null=True, blank=True, help_text="Optional start date for concession validity")
    valid_until = models.DateField(null=True, blank=True, help_text="Optional end date for concession validity")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Concession"
        verbose_name_plural = "Concessions"

    def clean(self):
        super().clean()
        if self.discount_value <= 0:
            raise ValidationError("Discount value must be greater than zero.")
        if self.discount_mode == SiblingDiscountMode.PERCENTAGE and self.discount_value > 100:
            raise ValidationError("Percentage discount cannot exceed 100%.")

    def __str__(self):
        return f"{self.get_concession_type_display()} ({self.student}, TZS {self.discount_value:,.0f})" if self.discount_mode == SiblingDiscountMode.FIXED else \
            f"{self.get_concession_type_display()} ({self.student}, {self.discount_value}%)"

    def get_discount_amount(self, fee_total):
        """Calculate the actual TZS discount given a fee total."""
        if self.discount_mode == SiblingDiscountMode.PERCENTAGE:
            return round(fee_total * self.discount_value / 100, 2)
        return self.discount_value
