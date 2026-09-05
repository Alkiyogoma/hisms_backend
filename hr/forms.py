from django import forms
from django.contrib.auth import get_user_model
from .models import (
    StaffProfile, TeacherClassAssignment,
    StaffOnboardingProgress, LeaveRequest, LeaveAllocation,
    PayrollEntry, PayrollRun, OffboardingRecord,
)
from academics.models import Department, GradeClass, Term, Subject

User = get_user_model()


class TeacherAssignmentForm(forms.Form):
    """
    Form for assigning a teacher to multiple classes with subjects.
    Uses checkboxes for multi-class and multi-subject selection.
    """
    teacher = forms.ModelChoiceField(
        queryset=StaffProfile.objects.filter(is_active=True).order_by("full_name"),
        widget=forms.Select(attrs={"class": "hf2-select"}),
        required=True,
        label="Teacher"
    )
    term = forms.ModelChoiceField(
        queryset=Term.objects.all().order_by("-end_date"),
        widget=forms.Select(attrs={"class": "hf2-select"}),
        required=True,
        label="Term"
    )
    assigned_classes = forms.MultipleChoiceField(
        choices=[],
        widget=forms.CheckboxSelectMultiple(),
        required=True,
        label="Assigned Classes"
    )
    subjects_taught = forms.MultipleChoiceField(
        choices=[],
        widget=forms.CheckboxSelectMultiple(),
        required=True,
        label="Subjects Taught"
    )
    is_class_teacher = forms.BooleanField(
        required=False,
        initial=False,
        label="Class Teacher",
        widget=forms.CheckboxInput(attrs={"class": "hf2-checkbox"})
    )
    repeat_across_terms = forms.BooleanField(
        required=False,
        initial=False,
        label="Repeat for all future terms",
        widget=forms.CheckboxInput(attrs={"class": "hf2-checkbox"}),
        help_text="Automatically create this assignment when a new term is added.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Setup all classes as choices
        all_classes = GradeClass.objects.all().order_by("name")
        self.fields["assigned_classes"].choices = [(str(gc.id), gc.name) for gc in all_classes]

        # Setup all active subjects as choices
        all_subjects = Subject.objects.filter(is_active=True).order_by("name")
        self.fields["subjects_taught"].choices = [(s.name, s.name) for s in all_subjects]

        # Initial values from POST or initial data
        if self.initial.get("subjects_taught"):
            self.fields["subjects_taught"].initial = self.initial["subjects_taught"]
        if self.initial.get("assigned_classes"):
            self.fields["assigned_classes"].initial = self.initial["assigned_classes"]
        if self.initial.get("is_class_teacher"):
            self.fields["is_class_teacher"].initial = self.initial["is_class_teacher"]
        if self.initial.get("repeat_across_terms"):
            self.fields["repeat_across_terms"].initial = self.initial["repeat_across_terms"]


class TeacherAssignmentEditForm(forms.Form):
    """Edit an existing TeacherClassAssignment — subjects and class teacher flag only."""
    subjects_taught = forms.MultipleChoiceField(
        choices=[],
        widget=forms.CheckboxSelectMultiple(),
        required=True,
        label="Subjects Taught"
    )
    is_class_teacher = forms.BooleanField(
        required=False,
        initial=False,
        label="Class Teacher",
        widget=forms.CheckboxInput(attrs={"class": "hf2-checkbox"})
    )
    repeat_across_terms = forms.BooleanField(
        required=False,
        initial=False,
        label="Repeat for all future terms",
        widget=forms.CheckboxInput(attrs={"class": "hf2-checkbox"}),
        help_text="Automatically create this assignment when a new term is added.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        all_subjects = Subject.objects.filter(is_active=True).order_by("name")
        self.fields["subjects_taught"].choices = [(s.name, s.name) for s in all_subjects]

        if self.initial.get("subjects_taught"):
            self.fields["subjects_taught"].initial = self.initial["subjects_taught"]
        if self.initial.get("is_class_teacher"):
            self.fields["is_class_teacher"].initial = self.initial["is_class_teacher"]
        if self.initial.get("repeat_across_terms"):
            self.fields["repeat_across_terms"].initial = self.initial["repeat_across_terms"]


class StaffDepartureForm(forms.Form):
    departure_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "hf2-input"}))
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"class": "hf2-input", "rows": 3, "placeholder": "Reason for departure..."}),
        required=True,
        label="Departure Reason"
    )


class OnboardingStepForm(forms.Form):
    """Generic form for onboarding step updates."""
    step = forms.IntegerField(widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        step_number = kwargs.pop("step_number", 1)
        super().__init__(*args, **kwargs)
        self.fields["step"].initial = step_number

        # Dynamically add fields based on step
        if step_number == 1:
            self.fields["full_name"] = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": "hf2-input"}))
            self.fields["date_of_birth"] = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date", "class": "hf2-input"}))
            self.fields["gender"] = forms.ChoiceField(choices=[("", "---"), ("male", "Male"), ("female", "Female"), ("other", "Other")], required=False, widget=forms.Select(attrs={"class": "hf2-select"}))
            self.fields["nationality"] = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"class": "hf2-input"}))
            self.fields["marital_status"] = forms.ChoiceField(choices=[("", "---"), ("single", "Single"), ("married", "Married"), ("divorced", "Divorced"), ("widowed", "Widowed")], required=False, widget=forms.Select(attrs={"class": "hf2-select"}))
            self.fields["residential_address"] = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "hf2-input", "rows": 2}))
            self.fields["contact_phone"] = forms.CharField(max_length=32, required=False, widget=forms.TextInput(attrs={"class": "hf2-input"}))
            self.fields["contact_email"] = forms.EmailField(widget=forms.EmailInput(attrs={"class": "hf2-input"}))
            self.fields["profile_picture"] = forms.FileField(
                required=False,
                widget=forms.FileInput(attrs={"accept": ".jpg,.jpeg,.png,.gif,.webp,image/*", "class": "ob-input"}),
            )
            self.fields["emergency_contact_name"] = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": "hf2-input"}))
            self.fields["emergency_contact_phone"] = forms.CharField(max_length=32, required=False, widget=forms.TextInput(attrs={"class": "hf2-input"}))
            self.fields["emergency_contact_relationship"] = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"class": "hf2-input"}))

        elif step_number == 2:
            self.fields["job_title"] = forms.CharField(max_length=100, widget=forms.TextInput(attrs={"class": "hf2-input"}))
            self.fields["department"] = forms.ChoiceField(choices=Department.choices, widget=forms.Select(attrs={"class": "hf2-select"}))
            self.fields["staff_category"] = forms.ChoiceField(choices=[("teaching", "Teaching"), ("non_teaching", "Non-teaching")], widget=forms.Select(attrs={"class": "hf2-select"}))
            self.fields["employment_type"] = forms.ChoiceField(choices=[("permanent", "Permanent"), ("contract", "Contract"), ("temporary", "Temporary"), ("probation", "Probation"), ("intern", "Intern")], widget=forms.Select(attrs={"class": "hf2-select"}))
            self.fields["employment_start_date"] = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "hf2-input"}))
            self.fields["probation_end_date"] = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date", "class": "hf2-input"}))
            self.fields["years_of_experience"] = forms.IntegerField(required=False, widget=forms.NumberInput(attrs={"class": "hf2-input"}))
            self.fields["highest_qualification"] = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={"class": "hf2-input"}))

            from users.models import UserRole
            self.fields["access_role"] = forms.ChoiceField(
                choices=UserRole.choices,
                widget=forms.Select(attrs={"class": "ob-select"}),
                label="System Role",
            )
            self.fields["access_is_active"] = forms.TypedChoiceField(
                choices=[(True, "Active"), (False, "Inactive")],
                coerce=lambda v: v in (True, "True", "true", "1", 1),
                empty_value=False,
                widget=forms.Select(attrs={"class": "ob-select"}),
                label="Account Active",
            )
            self.fields["system_account_created"] = forms.ChoiceField(
                choices=[("yes", "Yes, account is active and accessible"), ("no", "Not yet, pending")],
                widget=forms.Select(attrs={"class": "ob-select"}),
                label="System Access Confirmed",
            )

        elif step_number == 3:
            self.fields["basic_salary"] = forms.DecimalField(max_digits=12, decimal_places=2, widget=forms.NumberInput(attrs={"class": "hf2-input"}))
            self.fields["housing_allowance"] = forms.DecimalField(max_digits=12, decimal_places=2, required=False, widget=forms.NumberInput(attrs={"class": "hf2-input"}))
            self.fields["transport_allowance"] = forms.DecimalField(max_digits=12, decimal_places=2, required=False, widget=forms.NumberInput(attrs={"class": "hf2-input"}))
            self.fields["medical_allowance"] = forms.DecimalField(max_digits=12, decimal_places=2, required=False, widget=forms.NumberInput(attrs={"class": "hf2-input"}))
            self.fields["other_allowances"] = forms.DecimalField(max_digits=12, decimal_places=2, required=False, widget=forms.NumberInput(attrs={"class": "hf2-input"}))
            self.fields["bank_name"] = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={"class": "hf2-input"}))
            self.fields["bank_account_number"] = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"class": "hf2-input"}))
            self.fields["bank_branch"] = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={"class": "hf2-input"}))
            self.fields["nssf_number"] = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"class": "hf2-input"}))
            self.fields["tin_number"] = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"class": "hf2-input"}))
            self.fields["heslb_loan_number"] = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"class": "hf2-input"}))
            self.fields["heslb_has_loan"] = forms.BooleanField(required=False, label="Has HESLB loan?", widget=forms.CheckboxInput(attrs={"class": "hf2-checkbox"}))
            self.fields["heslb_deduction_type"] = forms.ChoiceField(
                required=False, label="Deduction type",
                choices=[("", "---"), ("fixed", "Fixed Amount (TZS)"), ("percentage", "Percentage of Basic")],
                widget=forms.Select(attrs={"class": "hf2-select"})
            )
            self.fields["heslb_deduction_value"] = forms.DecimalField(
                max_digits=10, decimal_places=2, required=False, label="Deduction amount/percentage",
                widget=forms.NumberInput(attrs={"class": "hf2-input", "step": "0.01"})
            )
            self.fields["heslb_deduction_order_ref"] = forms.CharField(
                max_length=100, required=False, label="Deduction order reference",
                widget=forms.TextInput(attrs={"class": "hf2-input"})
            )
            self.fields["heslb_deduction_start_date"] = forms.DateField(
                required=False, label="Deduction start date",
                widget=forms.DateInput(attrs={"type": "date", "class": "hf2-input"})
            )
            self.fields["nhif_number"] = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"class": "hf2-input"}))

        elif step_number == 4:
            self.fields["documents_notes"] = forms.CharField(
                required=False,
                widget=forms.Textarea(attrs={"class": "ob-input", "rows": 2, "placeholder": "Notes about submitted documents..."}),
            )
            for field_name, _doc_type, label in [
                ("document_degree", "degree", "Academic Certificate"),
                ("document_certification", "certification", "Professional Certification"),
                ("document_id_card", "id_card", "National ID or Passport"),
                ("document_contract", "contract", "Employment Contract"),
                ("document_other", "other", "Other Document"),
            ]:
                self.fields[field_name] = forms.FileField(
                    required=False,
                    label=label,
                    widget=forms.FileInput(attrs={"class": "ob-input", "accept": ".pdf,.jpg,.jpeg,.png,.webp,.doc,.docx"}),
                )

        elif step_number == 5:
            # Induction Checklist only - handled in _process_step_submission
            pass

    def clean_profile_picture(self):
        pic = self.cleaned_data.get("profile_picture")
        if pic:
            from academics.validators import validate_attachment_file, _compute_file_hash
            validate_attachment_file(pic, area="profile_photos")

            # Duplicate photo detection
            pic.seek(0)
            new_hash = _compute_file_hash(pic)
            pic.seek(0)
            for u in User.objects.filter(profile_picture__isnull=False):
                try:
                    if _compute_file_hash(u.profile_picture.path) == new_hash:
                        raise forms.ValidationError(
                            f"This photo is already used by {u.get_full_name() or u.username}. "
                            "Please upload a different photo."
                        )
                except (AttributeError, ValueError, OSError):
                    continue
        return pic


class LeaveRequestForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ["leave_type", "start_date", "end_date", "reason", "contact_during_leave", "handover_notes", "supporting_document"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date", "class": "hf2-input"}),
            "end_date": forms.DateInput(attrs={"type": "date", "class": "hf2-input"}),
            "leave_type": forms.Select(attrs={"class": "hf2-select"}),
            "reason": forms.Textarea(attrs={"class": "hf2-input", "rows": 3}),
            "contact_during_leave": forms.TextInput(attrs={"class": "hf2-input", "placeholder": "Phone number during leave"}),
            "handover_notes": forms.Textarea(attrs={"class": "hf2-input", "rows": 2}),
            "supporting_document": forms.FileInput(attrs={"class": "hf2-input"}),
        }


class LeaveApproveForm(forms.Form):
    action = forms.ChoiceField(choices=[("approve", "Approve"), ("reject", "Reject")], widget=forms.RadioSelect)
    rejection_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "hf2-input", "rows": 2, "placeholder": "Reason for rejection (required if rejecting)"})
    )


class PayrollEntryForm(forms.ModelForm):
    class Meta:
        model = PayrollEntry
        fields = [
            "staff", "basic_pay", "housing_allowance", "transport_allowance",
            "medical_allowance", "other_allowances", "overtime_pay", "bonus_pay",
            "paye_tax", "nssf_employee", "nhif_deduction", "hesb_deduction",
            "other_deductions", "deduction_notes", "unpaid_leave_days", "leave_deduction",
        ]
        widgets = {
            "staff": forms.Select(attrs={"class": "hf2-select"}),
            "basic_pay": forms.NumberInput(attrs={"class": "hf2-input"}),
            "housing_allowance": forms.NumberInput(attrs={"class": "hf2-input"}),
            "transport_allowance": forms.NumberInput(attrs={"class": "hf2-input"}),
            "medical_allowance": forms.NumberInput(attrs={"class": "hf2-input"}),
            "other_allowances": forms.NumberInput(attrs={"class": "hf2-input"}),
            "overtime_pay": forms.NumberInput(attrs={"class": "hf2-input"}),
            "bonus_pay": forms.NumberInput(attrs={"class": "hf2-input"}),
            "paye_tax": forms.NumberInput(attrs={"class": "hf2-input"}),
            "nssf_employee": forms.NumberInput(attrs={"class": "hf2-input"}),
            "nhif_deduction": forms.NumberInput(attrs={"class": "hf2-input"}),
            "hesb_deduction": forms.NumberInput(attrs={"class": "hf2-input"}),
            "other_deductions": forms.NumberInput(attrs={"class": "hf2-input"}),
            "deduction_notes": forms.Textarea(attrs={"class": "hf2-input", "rows": 2}),
            "unpaid_leave_days": forms.NumberInput(attrs={"class": "hf2-input"}),
            "leave_deduction": forms.NumberInput(attrs={"class": "hf2-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["staff"].queryset = StaffProfile.objects.filter(is_active=True).select_related("user")


class PayrollRunForm(forms.ModelForm):
    class Meta:
        model = PayrollRun
        fields = ["period_name", "payroll_type", "term", "period_start", "period_end", "payment_date"]
        widgets = {
            "period_name": forms.TextInput(attrs={"class": "hf2-input", "placeholder": "e.g. January 2026"}),
            "payroll_type": forms.Select(attrs={"class": "hf2-select"}),
            "term": forms.Select(attrs={"class": "hf2-select"}),
            "period_start": forms.DateInput(attrs={"type": "date", "class": "hf2-input"}),
            "period_end": forms.DateInput(attrs={"type": "date", "class": "hf2-input"}),
            "payment_date": forms.DateInput(attrs={"type": "date", "class": "hf2-input"}),
        }


class OffboardingForm(forms.ModelForm):
    class Meta:
        model = OffboardingRecord
        fields = [
            "resignation_received", "clearance_assets_returned", "clearance_library",
            "clearance_finance", "final_payslip_generated", "exit_interview_completed",
            "system_account_deactivated", "notice_period_days", "last_working_day",
            "final_settlement_amount", "settlement_paid", "settlement_paid_date",
            "exit_interview_date", "exit_reason", "feedback_notes", "is_rehirable",
        ]
        widgets = {
            "last_working_day": forms.DateInput(attrs={"type": "date", "class": "hf2-input"}),
            "settlement_paid_date": forms.DateInput(attrs={"type": "date", "class": "hf2-input"}),
            "exit_interview_date": forms.DateInput(attrs={"type": "date", "class": "hf2-input"}),
            "exit_reason": forms.Textarea(attrs={"class": "hf2-input", "rows": 3}),
            "feedback_notes": forms.Textarea(attrs={"class": "hf2-input", "rows": 3}),
            "final_settlement_amount": forms.NumberInput(attrs={"class": "hf2-input"}),
            "notice_period_days": forms.NumberInput(attrs={"class": "hf2-input"}),
        }
