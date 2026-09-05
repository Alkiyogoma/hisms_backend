import logging

from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from core.models import TimeStampedModel

logger = logging.getLogger(__name__)
from academics.models import Department, Term, GradeClass


class StaffCategory(models.TextChoices):
    TEACHING = "teaching", "Teaching"
    NON_TEACHING = "non_teaching", "Non-teaching"


class EmploymentType(models.TextChoices):
    PERMANENT = "permanent", "Permanent"
    CONTRACT = "contract", "Contract"
    TEMPORARY = "temporary", "Temporary"
    PROBATION = "probation", "Probation"
    INTERN = "intern", "Intern"


class Gender(models.TextChoices):
    MALE = "male", "Male"
    FEMALE = "female", "Female"
    OTHER = "other", "Other"


class MaritalStatus(models.TextChoices):
    SINGLE = "single", "Single"
    MARRIED = "married", "Married"
    DIVORCED = "divorced", "Divorced"
    WIDOWED = "widowed", "Widowed"


class StaffProfile(TimeStampedModel):
    # Core user link
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_profile")

    # B2 — Personal Information
    full_name = models.CharField(max_length=150)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True, default="")
    nationality = models.CharField(max_length=50, blank=True, default="")
    marital_status = models.CharField(max_length=12, choices=MaritalStatus.choices, blank=True, default="")
    residential_address = models.TextField(blank=True, default="")

    # B2 — Emergency Contact
    emergency_contact_name = models.CharField(max_length=150, blank=True, default="")
    emergency_contact_phone = models.CharField(max_length=32, blank=True, default="")
    emergency_contact_relationship = models.CharField(max_length=50, blank=True, default="")

    # Employment Information
    employee_id = models.CharField(max_length=32, unique=True, null=True, blank=True)
    job_title = models.CharField(max_length=100)
    department = models.CharField(max_length=32, choices=Department.choices)
    departments = models.JSONField(default=list, blank=True)
    staff_category = models.CharField(
        max_length=20,
        choices=StaffCategory.choices,
        default=StaffCategory.TEACHING,
        help_text="Administration staff may be teaching or non-teaching.",
    )
    employment_type = models.CharField(
        max_length=12, choices=EmploymentType.choices,
        default=EmploymentType.PERMANENT
    )
    employment_start_date = models.DateField()
    probation_end_date = models.DateField(null=True, blank=True, help_text="End of probation period")
    confirmation_date = models.DateField(null=True, blank=True, help_text="Date staff was confirmed in position")
    contract_end_date = models.DateField(
        null=True, blank=True,
        help_text="Employment contract expiry; used for renewal reminders.",
    )
    years_of_experience = models.PositiveIntegerField(default=0, help_text="Total years of professional experience")
    highest_qualification = models.CharField(max_length=100, blank=True, default="")

    # B2 — Contact Information
    contact_phone = models.CharField(max_length=32, blank=True, default="")
    contact_email = models.EmailField()

    # B2 — Salary & Banking
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Monthly basic salary (TZS)")
    housing_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Monthly housing allowance (TZS)")
    transport_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Monthly transport allowance (TZS)")
    medical_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Monthly medical allowance (TZS)")
    other_allowances = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Monthly other allowances (TZS)")

    bank_name = models.CharField(max_length=100, blank=True, default="")
    bank_account_number = models.CharField(max_length=50, blank=True, default="")
    bank_branch = models.CharField(max_length=100, blank=True, default="")

    def gross_salary(self):
        return self.basic_salary + self.housing_allowance + self.transport_allowance + self.medical_allowance + self.other_allowances

    # B2 — Official Identification
    national_id_number = models.CharField(max_length=50, blank=True, default="", help_text="NIDA or Passport number")

    # B2 — Statutory Information
    nssf_number = models.CharField(max_length=50, blank=True, default="", help_text="NSSF membership number")
    tin_number = models.CharField(max_length=50, blank=True, default="", help_text="Tax Identification Number (TIN)")
    paye_code = models.CharField(max_length=20, blank=True, default="", help_text="PAYE tax code")
    heslb_loan_number = models.CharField(max_length=50, blank=True, default="", help_text="HESLB loan reference number")
    # B3 — HESLB Deduction Details (for payroll auto-calculation)
    heslb_has_loan = models.BooleanField(default=False, help_text="Does this employee have an active HESLB loan?")
    heslb_deduction_type = models.CharField(
        max_length=12,
        choices=[("fixed", "Fixed Amount"), ("percentage", "Percentage of Basic")],
        blank=True, default="",
        help_text="HESLB deduction type: fixed TZS amount or %% of basic salary"
    )
    heslb_deduction_value = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="HESLB deduction amount (TZS if fixed, percentage if percentage type)"
    )
    heslb_deduction_order_ref = models.CharField(max_length=100, blank=True, default="",
        help_text="HESLB deduction order reference number")
    heslb_deduction_start_date = models.DateField(null=True, blank=True,
        help_text="Date HESLB deductions should start")
    nhif_number = models.CharField(max_length=50, blank=True, default="", help_text="NHIF membership number")

    # B3 — Onboarding Status
    onboarding_step = models.PositiveSmallIntegerField(
        default=0, help_text="0=not started, 1-5=in progress, 6=completed"
    )
    onboarding_completed = models.BooleanField(default=False, help_text="Has the staff member completed onboarding?")

    # Status
    departure_date = models.DateField(null=True, blank=True)
    departure_reason = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_active", "full_name"]
        verbose_name = "Staff Profile"
        verbose_name_plural = "Staff Profiles"

    def __str__(self) -> str:
        return f"{self.full_name} ({self.job_title})"

    @property
    def system_role(self):
        return self.user.role

    @property
    def is_teaching_staff(self) -> bool:
        if self.department == Department.ADMINISTRATION:
            return self.staff_category == StaffCategory.TEACHING
        return self.staff_category != StaffCategory.NON_TEACHING

    @property
    def contract_days_remaining(self):
        if not self.contract_end_date:
            return None
        return (self.contract_end_date - timezone.now().date()).days

    @property
    def contract_expiring_soon(self) -> bool:
        days = self.contract_days_remaining
        return days is not None and 0 <= days <= 60

    @property
    def total_monthly_allowances(self):
        return self.housing_allowance + self.transport_allowance + self.medical_allowance + self.other_allowances

    def nssf_employee_contribution(self):
        """NSSF employee contribution: uses configurable rate from PayrollConfig (default 10%)."""
        from .models import PayrollConfig
        cfg = PayrollConfig.get_config()
        rate = float(cfg.nssf_employee_rate) / 100.0
        return round(float(self.basic_salary) * rate, 2)

    def nssf_employer_contribution(self):
        """NSSF employer contribution: uses configurable rate from PayrollConfig (default 10%)."""
        from .models import PayrollConfig
        cfg = PayrollConfig.get_config()
        rate = float(cfg.nssf_employer_rate) / 100.0
        return round(float(self.basic_salary) * rate, 2)

    def nhif_contribution(self):
        """NHIF contribution based on salary tier."""
        sal = float(self.basic_salary)
        if sal <= 60000:
            return 3000
        elif sal <= 100000:
            return 5000
        elif sal <= 200000:
            return 10000
        elif sal <= 500000:
            return 20000
        elif sal <= 1000000:
            return 30000
        else:
            return 50000


class StaffDocument(TimeStampedModel):
    DOCUMENT_TYPES = [
        ("id_card", "National ID / Passport"),
        ("certification", "Professional Certification"),
        ("degree", "Degree / Diploma Certificate"),
        ("contract", "Employment Contract"),
        ("payslip", "Payslip"),
        ("nssf", "NSSF Statement"),
        ("nhif", "NHIF Card"),
        ("tin", "TIN Certificate"),
        ("other", "Other"),
    ]

    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="documents")
    name = models.CharField(max_length=150)
    file = models.FileField(upload_to="staff/documents/")
    document_type = models.CharField(max_length=30, choices=DOCUMENT_TYPES, blank=True, default="other")
    notes = models.TextField(blank=True, default="")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="uploaded_staff_docs"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.staff.full_name})"


class TeacherClassAssignment(TimeStampedModel):
    teacher = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="assignments")
    term = models.ForeignKey(Term, on_delete=models.CASCADE)
    grade_class = models.ForeignKey(GradeClass, on_delete=models.CASCADE)
    is_class_teacher = models.BooleanField(default=False, help_text="Is this the class teacher?")
    is_assistant_class_teacher = models.BooleanField(default=False, help_text="Is this the assistant class teacher?")
    subjects_taught = models.JSONField(default=list)
    rollover_ignored = models.BooleanField(
        default=False,
        help_text="If True, this assignment will NOT be rolled over to the next term.",
    )
    repeat_across_terms = models.BooleanField(
        default=False,
        help_text="If True, this assignment will be automatically created for each new term until stopped.",
    )

    class Meta:
        unique_together = ("teacher", "term", "grade_class")
        verbose_name = "Teacher Class Assignment"
        verbose_name_plural = "Teacher Class Assignments"

    def __str__(self) -> str:
        return f"{self.teacher.full_name} - {self.grade_class.name} ({self.term.name})"

    def clean(self):
        super().clean()
        # Cross-department restriction: teacher can only be assigned to classes
        # in the same department as their staff profile.
        if self.grade_class.department.lower() not in [d.lower() for d in (self.teacher.departments or [])]:
            raise ValidationError(
                f"{self.teacher.full_name} is in the {self.teacher.get_department_display()} department "
                f"but {self.grade_class.name} is a {self.grade_class.get_department_display()} class. "
                "Cross-department teaching is not allowed."
            )

        # Class teacher uniqueness: a teacher can only be the class teacher
        # for ONE class per term.
        if self.is_class_teacher:
            existing_ct = TeacherClassAssignment.objects.filter(
                teacher=self.teacher,
                term=self.term,
                is_class_teacher=True,
            ).exclude(pk=self.pk)
            if existing_ct.exists():
                other = existing_ct.first()
                raise ValidationError(
                    f"{self.teacher.full_name} is already the class teacher for "
                    f"{other.grade_class.name} in {self.term.name}. "
                    "A teacher can only be class teacher of one class per term."
                )

        # Assistant class teacher uniqueness: same restriction.
        if self.is_assistant_class_teacher:
            existing_asst = TeacherClassAssignment.objects.filter(
                teacher=self.teacher,
                term=self.term,
                is_assistant_class_teacher=True,
            ).exclude(pk=self.pk)
            if existing_asst.exists():
                other = existing_asst.first()
                raise ValidationError(
                    f"{self.teacher.full_name} is already the assistant class teacher for "
                    f"{other.grade_class.name} in {self.term.name}. "
                    "A teacher can only be assistant class teacher of one class per term."
                )


# 
# B3 — ONBOARDING WORKFLOW
# 

class OnboardingChecklistItem(TimeStampedModel):
    """Template items that appear in every staff onboarding checklist."""
    step = models.PositiveSmallIntegerField(help_text="Onboarding step (1-5)")
    item_name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    is_required = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["step", "order"]
        verbose_name = "Onboarding Checklist Item"
        verbose_name_plural = "Onboarding Checklist Items"

    def __str__(self):
        return f"Step {self.step}: {self.item_name}"


class StaffOnboardingProgress(TimeStampedModel):
    """Tracks an individual staff member's onboarding progress."""
    staff = models.OneToOneField(StaffProfile, on_delete=models.CASCADE, related_name="onboarding")
    current_step = models.PositiveSmallIntegerField(default=1, help_text="Current step (1-5)")
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Documents checklist tracking
    documents_submitted = models.BooleanField(default=False)
    contract_signed = models.BooleanField(default=False)
    id_verified = models.BooleanField(default=False)
    bank_details_provided = models.BooleanField(default=False)
    statutory_registered = models.BooleanField(default=False)
    system_account_created = models.BooleanField(default=False)
    induction_completed = models.BooleanField(default=False)
    probation_passed = models.BooleanField(default=False)
    orientation_completed = models.BooleanField(default=False)
    emergency_contacts_provided = models.BooleanField(default=False)

    # Metadata
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="completed_onboardings"
    )

    class Meta:
        verbose_name = "Staff Onboarding Progress"
        verbose_name_plural = "Staff Onboarding Progress"

    def __str__(self):
        return f"Onboarding: {self.staff.full_name} (Step {self.current_step}/5)"


class InductionChecklistCompletion(TimeStampedModel):
    """Tracks individual induction checklist item completion per staff member."""
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="induction_completions")
    checklist_item = models.ForeignKey(OnboardingChecklistItem, on_delete=models.CASCADE)
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="induction_completions_made"
    )

    class Meta:
        unique_together = ("staff", "checklist_item")
        verbose_name = "Induction Checklist Completion"
        verbose_name_plural = "Induction Checklist Completions"

    def __str__(self):
        status = "done" if self.is_completed else "pending"
        return f"{self.staff.full_name}: {self.checklist_item.item_name} [{status}]"


# 
# B4 — PAYROLL MODULE
# 

class PayrollStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING_APPROVAL = "pending_approval", "Pending Approval"
    APPROVED = "approved", "Approved"
    PAID = "paid", "Paid"
    LOCKED = "locked", "Locked"


class PayrollRun(TimeStampedModel):
    """A single payroll period run."""
    PAYROLL_TYPE_CHOICES = [
        ("monthly", "Monthly Salary"),
        ("bonus", "Bonus Payment"),
        ("termination", "Termination Benefits"),
        ("special", "Special Payment"),
    ]

    period_name = models.CharField(max_length=64, help_text="e.g. January 2026")
    payroll_type = models.CharField(max_length=20, choices=PAYROLL_TYPE_CHOICES, default="monthly")
    term = models.ForeignKey(Term, on_delete=models.SET_NULL, null=True, blank=True, related_name="payroll_runs")

    # Status
    status = models.CharField(
        max_length=20, choices=PayrollStatus.choices,
        default=PayrollStatus.DRAFT, db_index=True
    )

    # Period dates
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    payment_date = models.DateField(null=True, blank=True)

    # Summary (cached after calculation)
    total_gross_pay = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_deductions = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_net_pay = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    employee_count = models.PositiveIntegerField(default=0)

    # Approval
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="approved_payrolls"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="processed_payrolls"
    )

    class Meta:
        ordering = ["-period_start", "-created_at"]
        verbose_name = "Payroll Run"
        verbose_name_plural = "Payroll Runs"

    def __str__(self):
        return f"Payroll: {self.period_name} ({self.get_status_display()})"

    def calculate_summary(self):
        """Recalculate totals from payroll entries."""
        entries = self.entries.all()
        self.total_gross_pay = sum(e.gross_pay for e in entries)
        self.total_deductions = sum(e.total_deductions for e in entries)
        self.total_net_pay = sum(e.net_pay for e in entries)
        self.employee_count = entries.count()
        self.save(update_fields=[
            "total_gross_pay", "total_deductions",
            "total_net_pay", "employee_count", "updated_at"
        ])


class PayslipStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONFIRMED = "confirmed", "Confirmed"
    PAID = "paid", "Paid"


class PayrollEntry(TimeStampedModel):
    """Individual staff member's pay entry within a payroll run."""
    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="entries")
    staff = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="payroll_entries")

    # Earnings
    basic_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    housing_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    transport_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    medical_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_allowances = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    overtime_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    bonus_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Deductions
    paye_tax = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="PAYE income tax")
    nssf_employee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="NSSF employee contribution (5%)")
    nssf_employer = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="NSSF employer contribution (10%)")
    nhif_deduction = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="NHIF deduction")
    hesb_deduction = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="HESLB loan repayment")
    other_deductions = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    deduction_notes = models.TextField(blank=True, default="")

    # Leave deduction (unpaid leave days)
    unpaid_leave_days = models.PositiveSmallIntegerField(default=0)
    leave_deduction = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Computed fields
    gross_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    total_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    net_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)

    # Status
    status = models.CharField(max_length=12, choices=PayslipStatus.choices, default=PayslipStatus.DRAFT)
    payslip_generated = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("payroll_run", "staff")
        ordering = ["staff__full_name"]
        verbose_name = "Payroll Entry"
        verbose_name_plural = "Payroll Entries"

    def __str__(self):
        return f"{self.staff.full_name} ({self.payroll_run.period_name}"

    def calculate(self):
        """Recalculate all computed fields."""
        self.gross_pay = (
            self.basic_pay + self.housing_allowance + self.transport_allowance +
            self.medical_allowance + self.other_allowances + self.overtime_pay + self.bonus_pay
        )
        self.total_deductions = (
            self.paye_tax + self.nssf_employee + self.nhif_deduction +
            self.hesb_deduction + self.other_deductions + self.leave_deduction
        )
        self.net_pay = self.gross_pay - self.total_deductions

    def save(self, *args, **kwargs):
        self.calculate()
        super().save(*args, **kwargs)

    def auto_calculate_from_profile(self):
        """Pre-fill values from the staff profile using configurable PAYE bands and NSSF rates."""
        from decimal import Decimal
        from .models import PayrollConfig
        profile = self.staff
        self.basic_pay = profile.basic_salary
        self.housing_allowance = profile.housing_allowance
        self.transport_allowance = profile.transport_allowance
        self.medical_allowance = profile.medical_allowance
        self.other_allowances = profile.other_allowances

        # Statutory deductions (convert to Decimal for type consistency)
        self.nssf_employee = Decimal(str(profile.nssf_employee_contribution()))
        self.nhif_deduction = Decimal(str(profile.nhif_contribution()))

        # GAP 6: Auto-calculate leave deduction (daily rate = basic_pay / working_days_per_month)
        cfg = PayrollConfig.get_config()
        working_days = cfg.working_days_per_month or 26
        if self.unpaid_leave_days > 0 and self.basic_pay > 0:
            daily_rate = float(self.basic_pay) / float(working_days)
            self.leave_deduction = Decimal(str(round(daily_rate * self.unpaid_leave_days, 2)))
        else:
            self.leave_deduction = Decimal('0')

        # GAP 2: PAYE using configurable DB bands (fall back to hardcoded if none configured)
        gross = float(self.gross_pay) if self.gross_pay else float(
            profile.basic_salary + profile.total_monthly_allowances
        )

        from .models import PAYETaxBand
        active_bands = PAYETaxBand.objects.filter(is_active=True).order_by('band_from')
        if active_bands.exists():
            self.paye_tax = Decimal('0')
            for band in active_bands:
                b_from = float(band.band_from)
                b_to = float(band.band_to) if band.band_to else None
                b_rate = float(band.rate_percentage)
                if gross > b_from:
                    if b_to:
                        taxable_in_band = min(gross, b_to) - b_from
                    else:
                        taxable_in_band = gross - b_from
                    if taxable_in_band > 0:
                        paye_amt = round(taxable_in_band * b_rate / 100.0, 2)
                        self.paye_tax += Decimal(str(paye_amt))
                    if b_to and gross <= b_to:
                        break
                    elif b_to is None:
                        break
        else:
            logger.warning("No PAYEConfig found; falling back to hardcoded brackets for staff %s", self.pk)
            if gross <= 270000:
                paye_val = 0
            elif gross <= 520000:
                paye_val = round((gross - 270000) * 0.08, 2)
            elif gross <= 760000:
                paye_val = round(20000 + (gross - 520000) * 0.20, 2)
            elif gross <= 1000000:
                paye_val = round(68000 + (gross - 760000) * 0.25, 2)
            else:
                paye_val = round(128000 + (gross - 1000000) * 0.30, 2)
            self.paye_tax = Decimal(str(paye_val))

        # GAP 3: Auto-calculate HESLB deduction from profile
        if profile.heslb_has_loan and profile.heslb_deduction_type:
            if profile.heslb_deduction_type == "fixed":
                self.hesb_deduction = profile.heslb_deduction_value  # already Decimal
            elif profile.heslb_deduction_type == "percentage":
                pct = float(profile.heslb_deduction_value) / 100.0
                self.hesb_deduction = Decimal(str(round(float(profile.basic_salary) * pct, 2)))
        else:
            self.hesb_deduction = Decimal('0')

        self.calculate()


# 
# B5 — STATUTORY FILING
# 

class StatutoryFilingStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    CONFIRMED = "confirmed", "Confirmed"


class StatutoryFiling(TimeStampedModel):
    FILING_TYPE_CHOICES = [
        ("nssf", "NSSF Returns"),
        ("paye", "PAYE Returns"),
        ("heslb", "HESLB Loan Repayment"),
        ("nhif", "NHIF Contributions"),
        ("wcf", "Workers Compensation Fund"),
        ("sdl", "Skills Development Levy"),
    ]

    filing_type = models.CharField(max_length=10, choices=FILING_TYPE_CHOICES, db_index=True)
    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="statutory_filings")
    period_name = models.CharField(max_length=64)

    # Amounts
    employee_contribution = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    employer_contribution = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    status = models.CharField(
        max_length=12, choices=StatutoryFilingStatus.choices,
        default=StatutoryFilingStatus.DRAFT
    )

    # Filing metadata
    due_date = models.DateField(null=True, blank=True)
    submitted_date = models.DateField(null=True, blank=True)
    reference_number = models.CharField(max_length=100, blank=True, default="", help_text="Filing reference from statutory body")
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="statutory_filings"
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("filing_type", "payroll_run")
        ordering = ["-period_name", "filing_type"]
        verbose_name = "Statutory Filing"
        verbose_name_plural = "Statutory Filings"

    def __str__(self):
        return f"{self.get_filing_type_display()} ({self.period_name} ({self.get_status_display()})"


# 
# B4b — PAYROLL CONFIGURATION & STATUTORY SETTINGS
# 


class PayrollConfig(TimeStampedModel):
    """
    System-wide payroll configuration (singleton).
    NSSF rates, working days per month, and other payroll settings.
    """
    # NSSF rates (configurable, default Tanzania 10%%+10%%)
    nssf_employee_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=10.00,
        help_text="NSSF employee contribution rate as percentage (e.g. 10.00 = 10%%)"
    )
    nssf_employer_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=10.00,
        help_text="NSSF employer contribution rate as percentage (e.g. 10.00 = 10%%)"
    )
    working_days_per_month = models.PositiveIntegerField(
        default=26,
        help_text="Standard working days per month for daily rate calculations"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="payroll_config_updates"
    )

    class Meta:
        verbose_name = "Payroll Configuration"
        verbose_name_plural = "Payroll Configuration"

    def __str__(self):
        return f"Payroll Config (NSSF: {self.nssf_employee_rate}%%ee/{self.nssf_employer_rate}%%er, Work Days: {self.working_days_per_month}"

    def save(self, *args, **kwargs):
        # Enforce singleton
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_config(cls):
        """Get or create the singleton payroll config record."""
        config, created = cls.objects.get_or_create(pk=1)
        return config


class PAYETaxBand(TimeStampedModel):
    """
    Configurable PAYE tax bands with versioning.
    Bands are stored as DB records so Super Admin can update without a code release.
    Previous versions are retained for historical payroll recalculation.
    """
    version = models.CharField(max_length=20, default="2024/2025", help_text="Tax band version (e.g. 2024/2025)")
    band_from = models.DecimalField(max_digits=12, decimal_places=2, help_text="Lower bound of this band (TZS)")
    band_to = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Upper bound of this band (TZS). Null = infinity."
    )
    base_tax = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Base tax amount for this band (cumulative from previous bands)"
    )
    rate_percentage = models.DecimalField(
        max_digits=5, decimal_places=2,
        help_text="Tax rate for this band as percentage (e.g. 8.00 = 8%%)"
    )
    is_active = models.BooleanField(default=True, help_text="Only active bands are used for current payroll runs")
    effective_from = models.DateField(null=True, blank=True, help_text="Date from which this band version applies")

    class Meta:
        ordering = ["band_from"]
        verbose_name = "PAYE Tax Band"
        verbose_name_plural = "PAYE Tax Bands"

    def __str__(self):
        upper = f"{self.band_to:,.0f}" if self.band_to else "∞"
        return f"PAYE {self.version}: TZS {self.band_from:,.0f} to {upper} @ {self.rate_percentage}%%"
# 
# B6 — LEAVE MANAGEMENT
# 

class LeaveType(models.TextChoices):
    ANNUAL = "annual", "Annual Leave"
    SICK = "sick", "Sick Leave"
    MATERNITY = "maternity", "Maternity Leave"
    PATERNITY = "paternity", "Paternity Leave"
    STUDY = "study", "Study Leave"
    COMPASSIONATE = "compassionate", "Compassionate Leave"
    UNPAID = "unpaid", "Unpaid Leave"
    OTHER_LEAVE = "other", "Other Leave"


class LeaveStatus(models.TextChoices):
    PENDING = "pending", "Pending Approval"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class LeaveAllocation(TimeStampedModel):
    """Annual leave entitlement per staff member per year."""
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="leave_allocations")
    year = models.PositiveSmallIntegerField(help_text="Calendar year (e.g. 2026)")
    leave_type = models.CharField(max_length=15, choices=LeaveType.choices)

    # Entitlement
    total_days = models.PositiveSmallIntegerField(default=0, help_text="Total days entitled for this year")
    used_days = models.PositiveSmallIntegerField(default=0, help_text="Days taken so far")
    pending_days = models.PositiveSmallIntegerField(default=0, help_text="Days pending approval")

    class Meta:
        unique_together = ("staff", "year", "leave_type")
        verbose_name = "Leave Allocation"
        verbose_name_plural = "Leave Allocations"

    def __str__(self):
        return f"{self.staff.full_name} ({self.get_leave_type_display()} {self.year}"

    @property
    def remaining_days(self):
        return self.total_days - self.used_days - self.pending_days


class LeaveRequest(TimeStampedModel):
    """Individual leave request from a staff member."""
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="leave_requests")
    leave_type = models.CharField(max_length=15, choices=LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    total_days = models.PositiveSmallIntegerField(editable=False)

    reason = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=12, choices=LeaveStatus.choices,
        default=LeaveStatus.PENDING, db_index=True
    )

    # Approval
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="approved_leave_requests"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")

    # Contact during leave
    contact_during_leave = models.CharField(max_length=50, blank=True, default="")
    handover_notes = models.TextField(blank=True, default="")

    # Documents (e.g., medical certificate for sick leave)
    supporting_document = models.FileField(
        upload_to="staff/leave_docs/", null=True, blank=True
    )

    class Meta:
        ordering = ["-start_date", "-created_at"]
        verbose_name = "Leave Request"
        verbose_name_plural = "Leave Requests"

    def __str__(self):
        return f"{self.staff.full_name} ({self.get_leave_type_display()} ({self.start_date} to {self.end_date})"

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError("Start date must be before end date.")
        if self.start_date and self.end_date:
            self.total_days = (self.end_date - self.start_date).days + 1

    def save(self, *args, **kwargs):
        if self.start_date and self.end_date:
            self.total_days = (self.end_date - self.start_date).days + 1
        super().save(*args, **kwargs)


# 
# B7 — OFFBOARDING
# 

class OffboardingStatus(models.TextChoices):
    INITIATED = "initiated", "Initiated"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"


class OffboardingRecord(TimeStampedModel):
    """Tracks the offboarding process for a departing staff member."""
    staff = models.OneToOneField(StaffProfile, on_delete=models.CASCADE, related_name="offboarding")
    status = models.CharField(
        max_length=15, choices=OffboardingStatus.choices,
        default=OffboardingStatus.INITIATED
    )

    # Checklist items
    resignation_received = models.BooleanField(default=False)
    clearance_assets_returned = models.BooleanField(default=False, help_text="Laptop, ID card, keys returned")
    clearance_library = models.BooleanField(default=False, help_text="Library books returned")
    clearance_finance = models.BooleanField(default=False, help_text="Outstanding advances/payments cleared")
    final_payslip_generated = models.BooleanField(default=False)
    exit_interview_completed = models.BooleanField(default=False)
    system_account_deactivated = models.BooleanField(default=False)

    # Final settlement
    notice_period_days = models.PositiveSmallIntegerField(default=30)
    last_working_day = models.DateField(null=True, blank=True)
    final_settlement_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    settlement_paid = models.BooleanField(default=False)
    settlement_paid_date = models.DateField(null=True, blank=True)

    # Exit interview
    exit_interview_date = models.DateField(null=True, blank=True)
    exit_reason = models.TextField(blank=True, default="")
    feedback_notes = models.TextField(blank=True, default="")

    # Processing
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="initiated_offboardings"
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="completed_offboardings"
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    # Rehire eligibility
    is_rehirable = models.BooleanField(default=True, help_text="Is the staff member eligible for rehire?")

    class Meta:
        verbose_name = "Offboarding Record"
        verbose_name_plural = "Offboarding Records"

    def __str__(self):
        return f"Offboarding: {self.staff.full_name} ({self.get_status_display()})"

    @property
    def clearance_completed(self):
        return all([
            self.clearance_assets_returned,
            self.clearance_library,
            self.clearance_finance,
        ])

    @property
    def all_steps_completed(self):
        return all([
            self.resignation_received,
            self.clearance_assets_returned,
            self.clearance_library,
            self.clearance_finance,
            self.final_payslip_generated,
            self.exit_interview_completed,
            self.system_account_deactivated,
        ])


class RolloverConflict(TimeStampedModel):
    """
    Stores a rollover conflict that needs admin decision.
    Created automatically when a repeat_across_terms assignment
    cannot be cleanly applied to a new term.
    """
    CONFLICT_TYPES = [
        ("teacher_inactive", "Teacher Inactive"),
        ("class_missing", "Class No Longer Exists"),
        ("subject_conflict", "Subject Already Assigned"),
        ("class_teacher_conflict", "Class Teacher Conflict"),
        ("contract_expired", "Contract Expired"),
    ]

    source_assignment = models.ForeignKey(
        TeacherClassAssignment, on_delete=models.SET_NULL, null=True,
        related_name="rollover_conflicts_from",
        help_text="The original assignment that triggered this conflict.",
    )
    target_term = models.ForeignKey(
        Term, on_delete=models.CASCADE,
        related_name="rollover_conflicts",
    )
    teacher = models.ForeignKey(
        "hr.StaffProfile", on_delete=models.CASCADE,
        related_name="rollover_conflicts",
    )
    grade_class = models.ForeignKey(
        "academics.GradeClass", on_delete=models.CASCADE,
        related_name="rollover_conflicts",
    )
    conflict_type = models.CharField(max_length=30, choices=CONFLICT_TYPES)
    conflict_detail = models.TextField(
        help_text="Human-readable description of the conflict.",
    )
    subjects_taught = models.JSONField(default=list)
    is_class_teacher = models.BooleanField(default=False)
    is_resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="resolved_rollover_conflicts",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_action = models.CharField(
        max_length=20, blank=True,
        choices=[("skip", "Skip"), ("force", "Force Create"), ("modify", "Modified & Created")],
        help_text="What the admin decided to do with this conflict.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Rollover Conflict"
        verbose_name_plural = "Rollover Conflicts"

    def __str__(self):
        return (
            f"{self.teacher.full_name} → {self.grade_class.name} "
            f"({self.get_conflict_type_display()}) [{self.target_term.name}]"
        )
