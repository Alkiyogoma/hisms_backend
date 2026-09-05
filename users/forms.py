import re

from django import forms
from django.db import transaction
from django.utils import timezone

from academics.models import Department, GradeClass, Subject, Term
from academics.utils import get_current_term
from hr.models import StaffCategory, StaffDocument, TeacherClassAssignment
from users.models import User, UserRole
from users.staff_assignment import apply_django_admin_flags, sync_staff_profile


class StaffAssignmentFieldsMixin(forms.Form):
    """Department-scoped class/subject fields for staff onboarding."""

    departments = forms.MultipleChoiceField(choices=[], required=True, label="Departments")
    staff_category = forms.ChoiceField(
        choices=StaffCategory.choices,
        required=False,
        initial=StaffCategory.TEACHING,
        label="Staff type (Administration)",
    )
    contract_end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "hf2-input"}),
        label="Contract end date",
    )

    # ── Consolidated employment fields (from HR StaffProfile) ──
    job_title = forms.CharField(
        max_length=100, required=True,
        label="Job Title",
        help_text="e.g. Class Teacher, Bursar, Head of Department",
        widget=forms.TextInput(attrs={"class": "hf2-input", "placeholder": "e.g. Class Teacher"}),
    )
    employee_id = forms.CharField(
        max_length=32, required=True,
        label="Employee ID",
        help_text="Unique employee identifier (e.g. STAFF-001)",
        widget=forms.TextInput(attrs={"class": "hf2-input", "placeholder": "e.g. STAFF-001"}),
    )
    employment_type = forms.ChoiceField(
        choices=[("", "Select Type"), ("permanent", "Permanent"), ("contract", "Contract"), ("temporary", "Temporary"), ("probation", "Probation"), ("intern", "Intern")],
        required=False, initial="permanent",
        label="Employment Type",
        widget=forms.Select(attrs={"class": "hf2-select"}),
    )
    employment_start_date = forms.DateField(
        required=False,
        label="Employment Start Date",
        widget=forms.DateInput(attrs={"type": "date", "class": "hf2-input"}),
    )
    probation_end_date = forms.DateField(
        required=False,
        label="Probation End Date",
        widget=forms.DateInput(attrs={"type": "date", "class": "hf2-input"}),
    )
    date_of_birth = forms.DateField(
        required=False,
        label="Date of Birth",
        widget=forms.DateInput(attrs={"type": "date", "class": "hf2-input"}),
    )
    gender = forms.ChoiceField(
        choices=[("", "Select"), ("male", "Male"), ("female", "Female"), ("other", "Other")],
        required=False,
        label="Gender",
        widget=forms.Select(attrs={"class": "hf2-select"}),
    )
    marital_status = forms.ChoiceField(
        choices=[("", "Select"), ("single", "Single"), ("married", "Married"), ("divorced", "Divorced"), ("widowed", "Widowed")],
        required=False,
        label="Marital Status",
        widget=forms.Select(attrs={"class": "hf2-select"}),
    )
    contact_phone = forms.CharField(
        max_length=32, required=True,
        label="Contact Phone",
        widget=forms.TextInput(attrs={"class": "hf2-input", "placeholder": "+255 ..."}),
    )
    national_id_number = forms.CharField(
        max_length=50, required=True,
        label="Official ID (NIDA or Passport)",
        help_text="National ID (NIDA) or passport number",
        widget=forms.TextInput(attrs={"class": "hf2-input", "placeholder": "e.g. NIDA-1234567890123"}),
    )
    residential_address = forms.CharField(
        required=False,
        label="Residential Address",
        widget=forms.Textarea(attrs={"class": "hf2-input", "rows": 2, "placeholder": "Home address"}),
    )
    nationality = forms.CharField(
        max_length=50, required=False,
        label="Nationality",
        widget=forms.TextInput(attrs={"class": "hf2-input", "placeholder": "e.g. Tanzanian"}),
    )
    years_of_experience = forms.IntegerField(
        required=False, initial=0,
        label="Years of Experience",
        widget=forms.NumberInput(attrs={"class": "hf2-input"}),
    )
    highest_qualification = forms.CharField(
        max_length=100, required=False,
        label="Highest Qualification",
        widget=forms.TextInput(attrs={"class": "hf2-input", "placeholder": "e.g. Bachelor of Education"}),
    )
    confirmation_date = forms.DateField(
        required=False,
        label="Confirmation Date",
        widget=forms.DateInput(attrs={"type": "date", "class": "hf2-input"}),
    )

    # ── Emergency contacts ──
    emergency_contact_name = forms.CharField(
        max_length=150, required=False,
        label="Emergency Contact Name",
        widget=forms.TextInput(attrs={"class": "hf2-input", "placeholder": "Full name"}),
    )
    emergency_contact_phone = forms.CharField(
        max_length=32, required=False,
        label="Emergency Contact Phone",
        widget=forms.TextInput(attrs={"class": "hf2-input", "placeholder": "+255 ..."}),
    )
    emergency_contact_relationship = forms.ChoiceField(
        choices=[("", "Select relationship..."), ("Spouse", "Spouse"), ("Parent", "Parent"), ("Sibling", "Sibling"), ("Child", "Child"), ("Friend", "Friend"), ("Other", "Other")],
        required=False,
        label="Relationship",
        widget=forms.Select(attrs={"class": "hf2-select"}),
    )
    emergency_contact_relationship_other = forms.CharField(
        max_length=50, required=False,
        label="Other relationship",
        widget=forms.TextInput(attrs={"class": "hf2-input", "placeholder": "Specify relationship..."}),
    )

    # ── Salary & Banking ──
    basic_salary = forms.DecimalField(
        max_digits=12, decimal_places=2, required=False, initial=0,
        label="Basic Salary (TZS)",
        widget=forms.NumberInput(attrs={"class": "hf2-input"}),
    )
    housing_allowance = forms.DecimalField(
        max_digits=12, decimal_places=2, required=False, initial=0,
        label="Housing Allowance (TZS)",
        widget=forms.NumberInput(attrs={"class": "hf2-input"}),
    )
    transport_allowance = forms.DecimalField(
        max_digits=12, decimal_places=2, required=False, initial=0,
        label="Transport Allowance (TZS)",
        widget=forms.NumberInput(attrs={"class": "hf2-input"}),
    )
    medical_allowance = forms.DecimalField(
        max_digits=12, decimal_places=2, required=False, initial=0,
        label="Medical Allowance (TZS)",
        widget=forms.NumberInput(attrs={"class": "hf2-input"}),
    )
    other_allowances = forms.DecimalField(
        max_digits=12, decimal_places=2, required=False, initial=0,
        label="Other Allowances (TZS)",
        widget=forms.NumberInput(attrs={"class": "hf2-input"}),
    )
    bank_name = forms.CharField(
        max_length=100, required=False,
        label="Bank Name",
        widget=forms.TextInput(attrs={"class": "hf2-input", "placeholder": "e.g. CRDB Bank"}),
    )
    bank_account_number = forms.CharField(
        max_length=50, required=False,
        label="Account Number",
        widget=forms.TextInput(attrs={"class": "hf2-input", "placeholder": "Account number"}),
    )
    bank_branch = forms.CharField(
        max_length=100, required=False,
        label="Branch",
        widget=forms.TextInput(attrs={"class": "hf2-input", "placeholder": "e.g. Kawe Branch"}),
    )

    # ── Statutory Information ──
    nssf_number = forms.CharField(
        max_length=50, required=False,
        label="NSSF Number",
        widget=forms.TextInput(attrs={"class": "hf2-input", "placeholder": "NSSF membership number"}),
    )
    tin_number = forms.CharField(
        max_length=50, required=False,
        label="TIN Number",
        widget=forms.TextInput(attrs={"class": "hf2-input", "placeholder": "Tax Identification Number"}),
    )
    nhif_number = forms.CharField(
        max_length=50, required=False,
        label="NHIF Number",
        widget=forms.TextInput(attrs={"class": "hf2-input", "placeholder": "NHIF membership number"}),
    )

    def _init_staff_assignment_fields(self):
        self.fields["departments"].choices = list(Department.choices)

    def _save_staff_assignment(self, user):
        depts = self.cleaned_data.get("departments") or []

        profile = sync_staff_profile(
            user,
            departments=depts,
            staff_category=self.cleaned_data.get("staff_category") or StaffCategory.TEACHING,
            contract_end_date=self.cleaned_data.get("contract_end_date"),
            job_title=self.cleaned_data.get("job_title", ""),
            employee_id=self.cleaned_data.get("employee_id", ""),
            employment_type=self.cleaned_data.get("employment_type", "permanent"),
            employment_start_date=self.cleaned_data.get("employment_start_date"),
            probation_end_date=self.cleaned_data.get("probation_end_date"),
            confirmation_date=self.cleaned_data.get("confirmation_date"),
            date_of_birth=self.cleaned_data.get("date_of_birth"),
            gender=self.cleaned_data.get("gender", ""),
            marital_status=self.cleaned_data.get("marital_status", ""),
            contact_phone=self.cleaned_data.get("contact_phone", ""),
            residential_address=self.cleaned_data.get("residential_address", ""),
            nationality=self.cleaned_data.get("nationality", ""),
            national_id_number=self.cleaned_data.get("national_id_number", ""),
            years_of_experience=self.cleaned_data.get("years_of_experience") or 0,
            highest_qualification=self.cleaned_data.get("highest_qualification", ""),
            # Emergency contacts
            emergency_contact_name=self.cleaned_data.get("emergency_contact_name", ""),
            emergency_contact_phone=self.cleaned_data.get("emergency_contact_phone", ""),
            emergency_contact_relationship=(
                self.cleaned_data.get("emergency_contact_relationship_other", "")
                or "Other"
            ) if self.cleaned_data.get("emergency_contact_relationship") == "Other"
            else self.cleaned_data.get("emergency_contact_relationship", ""),
            # Salary & banking
            basic_salary=self.cleaned_data.get("basic_salary") or 0,
            housing_allowance=self.cleaned_data.get("housing_allowance") or 0,
            transport_allowance=self.cleaned_data.get("transport_allowance") or 0,
            medical_allowance=self.cleaned_data.get("medical_allowance") or 0,
            other_allowances=self.cleaned_data.get("other_allowances") or 0,
            bank_name=self.cleaned_data.get("bank_name", ""),
            bank_account_number=self.cleaned_data.get("bank_account_number", ""),
            bank_branch=self.cleaned_data.get("bank_branch", ""),
            # Statutory
            nssf_number=self.cleaned_data.get("nssf_number", ""),
            tin_number=self.cleaned_data.get("tin_number", ""),
            nhif_number=self.cleaned_data.get("nhif_number", ""),
        )

    def _load_staff_assignment_initial(self, user):
        profile = getattr(user, "staff_profile", None)
        if not profile:
            return
        self.fields["departments"].initial = profile.departments or [profile.department] if profile.department else []
        self.fields["staff_category"].initial = profile.staff_category
        self.fields["contract_end_date"].initial = profile.contract_end_date
        self.fields["job_title"].initial = profile.job_title
        self.fields["employee_id"].initial = profile.employee_id
        self.fields["employment_type"].initial = profile.employment_type
        self.fields["employment_start_date"].initial = profile.employment_start_date
        self.fields["probation_end_date"].initial = profile.probation_end_date
        self.fields["confirmation_date"].initial = profile.confirmation_date
        self.fields["date_of_birth"].initial = profile.date_of_birth
        self.fields["gender"].initial = profile.gender
        self.fields["marital_status"].initial = profile.marital_status
        self.fields["contact_phone"].initial = profile.contact_phone
        self.fields["residential_address"].initial = profile.residential_address
        self.fields["nationality"].initial = profile.nationality
        self.fields["years_of_experience"].initial = profile.years_of_experience
        self.fields["highest_qualification"].initial = profile.highest_qualification
        # Emergency contacts
        self.fields["emergency_contact_name"].initial = profile.emergency_contact_name
        self.fields["emergency_contact_phone"].initial = profile.emergency_contact_phone
        self.fields["emergency_contact_relationship"].initial = profile.emergency_contact_relationship
        if profile.emergency_contact_relationship not in ("", "Spouse", "Parent", "Sibling", "Child", "Friend", "Other"):
            self.fields["emergency_contact_relationship_other"].initial = profile.emergency_contact_relationship
            self.fields["emergency_contact_relationship"].initial = "Other"
        # Salary & banking
        self.fields["basic_salary"].initial = profile.basic_salary
        self.fields["housing_allowance"].initial = profile.housing_allowance
        self.fields["transport_allowance"].initial = profile.transport_allowance
        self.fields["medical_allowance"].initial = profile.medical_allowance
        self.fields["other_allowances"].initial = profile.other_allowances
        self.fields["bank_name"].initial = profile.bank_name
        self.fields["bank_account_number"].initial = profile.bank_account_number
        self.fields["bank_branch"].initial = profile.bank_branch
        # Statutory
        self.fields["nssf_number"].initial = profile.nssf_number
        self.fields["tin_number"].initial = profile.tin_number
        self.fields["nhif_number"].initial = profile.nhif_number

    def _apply_widget_classes(self):
        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs["class"] = "hf2-select"
            elif isinstance(field.widget, forms.FileInput):
                field.widget.attrs["class"] = "hf2-file-input"
            elif isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            else:
                field.widget.attrs.setdefault("class", "hf2-input")


class UserCreateForm(StaffAssignmentFieldsMixin, forms.ModelForm):
    first_name = forms.CharField(
        max_length=30, required=True,
        label="First Name",
        widget=forms.TextInput(attrs={"class": "hf2-input"}),
    )
    last_name = forms.CharField(
        max_length=30, required=True,
        label="Last Name",
        widget=forms.TextInput(attrs={"class": "hf2-input"}),
    )
    role = forms.ChoiceField(
        required=True,
        label="Role",
        widget=forms.Select(attrs={"class": "hf2-select"}),
    )
    password = forms.CharField(
        widget=forms.PasswordInput,
        required=False,
        help_text="Leave blank to auto-generate a temporary password sent via email.",
    )
    document_file = forms.FileField(required=False, label="Initial Document (e.g. Contract/ID)")

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        self.skip = kwargs.pop("skip", False)
        super().__init__(*args, **kwargs)
        from users.role_models import RoleConfig
        system_choices = [
            (v, l) for v, l in UserRole.choices if v != "parent"
        ]
        custom_roles = RoleConfig.objects.filter(is_system=False, is_active=True).order_by("label")
        custom_choices = [
            (f"custom_{rc.pk}", rc.label) for rc in custom_roles
        ]
        self.fields["role"].choices = [("", "Select role...")] + system_choices + custom_choices
        if not self.instance.pk:
            self.fields["role"].initial = ""
        # Also patch the model field's choices so full_clean() during save() accepts custom roles
        from users.models import User as _UserModel
        _UserModel._meta.get_field("role").choices = self.fields["role"].choices
        self._init_staff_assignment_fields()
        # Always make staff profile fields optional on user creation
        # (staff details can be completed later via edit/onboarding)
        for fname in ("departments", "job_title", "employee_id", "contact_phone", "national_id_number"):
            if fname in self.fields:
                self.fields[fname].required = False
        self._apply_widget_classes()
        self._email_sent = False

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "role",
            "password",
            "is_staff",
            "is_superuser",
            "profile_picture",
            "groups",
            "user_permissions",
        ]
        widgets = {
            "groups": forms.CheckboxSelectMultiple(),
            "user_permissions": forms.CheckboxSelectMultiple(),
            "profile_picture": forms.FileInput(attrs={"accept": ".jpg,.jpeg,.png,.gif,.webp"}),
        }

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip()
        if email and User.objects.filter(email=email).exists():
            raise forms.ValidationError(
                "An account with this email address already exists. Please use a different email."
            )
        return email

    def clean_role(self):
        role = self.cleaned_data.get("role", "").strip()
        if not role:
            raise forms.ValidationError(
                "A system role is required. Please select a role for this user."
            )
        return role

    def clean_job_title(self):
        """FRD OP 10.3: Validate job_title matches the assigned role."""
        job_title = self.cleaned_data.get("job_title", "").strip()
        role = self.cleaned_data.get("role", "").strip()
        if not job_title:
            return job_title

        ROLE_JOB_MISMATCH = {
            UserRole.TEACHER: {"head of department", "hod", "head of school", "principal", "bursar", "admin officer", "deputy"},
            UserRole.PRIMARY_HOD: {"teacher", "class teacher", "bursar", "admin officer"},
            UserRole.ECD_HOD: {"teacher", "class teacher", "bursar", "admin officer"},
            UserRole.LOWER_SECONDARY_HOD: {"teacher", "class teacher", "bursar", "admin officer"},
            UserRole.HEAD_OF_SCHOOL: {"teacher", "class teacher", "hod", "head of department"},
            UserRole.ADMIN_OFFICER: {"teacher", "class teacher", "hod", "head of department", "head of school", "principal"},
        }
        blocked = ROLE_JOB_MISMATCH.get(role, set())
        title_lower = job_title.lower()
        for keyword in blocked:
            if keyword in title_lower:
                raise forms.ValidationError(
                    f'The job title "{job_title}" does not match the assigned role '
                    f"({self.get_field('role').choices}). Please correct the job title or role."
                )
        return job_title

    def clean_profile_picture(self):
        """FR-STAFF-009: Validate profile picture format + duplicate detection."""
        pic = self.cleaned_data.get("profile_picture")
        if pic:
            import os
            from academics.validators import _compute_file_hash
            ext = os.path.splitext(pic.name)[1].lower()
            allowed = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
            if ext not in allowed:
                raise forms.ValidationError(
                    f"Unsupported image format '{ext}'. Please upload JPG, PNG, GIF, or WebP."
                )
            if pic.size > 5 * 1024 * 1024:
                raise forms.ValidationError("Image must be under 5 MB.")

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

    def save(self, commit=True):
        user = super().save(commit=False)
        # FR-STAFF-002: auto-generate temp password if none provided
        temp_password = self.cleaned_data.get("password")
        if temp_password:
            user.set_password(temp_password)
        else:
            import secrets
            temp_password = secrets.token_urlsafe(12)
            user.set_password(temp_password)
            user.must_change_password = True
        apply_django_admin_flags(user, role=self.cleaned_data.get("role"))
        if commit:
            user.save()
            self.save_m2m()
            if not self.skip:
                self._save_staff_assignment(user)
            else:
                # Minimal profile so onboarding can pick it up later
                from hr.models import StaffProfile
                from academics.models import Department
                StaffProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        "full_name": f"{user.first_name} {user.last_name}".strip() or user.username,
                        "contact_email": user.email,
                        "is_active": user.is_active,
                        "employment_start_date": timezone.now().date(),
                        "job_title": "(Not yet assigned)",
                        "department": Department.PRIMARY,
                    },
                )

            document_file = self.cleaned_data.get("document_file")
            if document_file:
                profile = getattr(user, "staff_profile", None)
                if profile:
                    StaffDocument.objects.create(
                        staff=profile,
                        name=document_file.name,
                        file=document_file,
                        document_type="contract",
                    )
            # FR-STAFF-002: send activation email with temp password
            self._email_sent = self._send_activation_email(user, temp_password)
        return user

    def _send_activation_email(self, user, temp_password):
        """FR-STAFF-002: Send activation email with temp password and login link."""
        from communications.email_service import send_email_safe
        from django.template.loader import render_to_string
        from django.conf import settings

        login_url = self.request.build_absolute_uri("/accounts/login/") if self.request else "/accounts/login/"
        subject = f"Your Hodari SMS Account - {getattr(settings, 'SCHOOL_NAME', 'Hodari Christian School')}"
        context = {
            "user": user,
            "temp_password": temp_password,
            "login_url": login_url,
            "school_name": getattr(settings, "SCHOOL_NAME", "Hodari Christian School"),
            "site_url": getattr(settings, "SITE_URL", "http://127.0.0.1:8000"),
            "static_url": getattr(settings, "STATIC_URL", "/static/"),
        }

        # Try dynamic DB template first
        from core.email_templates import send_dynamic_email
        db_result = send_dynamic_email(
            template_type="activation",
            to_email=user.email,
            context=context,
            actor=self.request.user if self.request else None,
        )
        if db_result:
            return True

        # Fallback to static template
        try:
            html_message = render_to_string("registration/activation_email.html", context)
        except Exception:
            html_message = None
        plain_message = (
            f"Hello {user.first_name or user.username},\n\n"
            f"A staff account has been created for you at Hodari SMS.\n\n"
            f"Login URL: {login_url}\n"
            f"Username: {user.username}\n"
            f"Temporary Password: {temp_password}\n\n"
            f"Please log in and change your password immediately.\n"
            f"You will be prompted to set a new password on your first login.\n\n"
            f"Hodari Christian School"
        )
        result = send_email_safe(
            to_email=user.email,
            subject=subject,
            body=plain_message,
            html_body=html_message or plain_message,
            actor=self.request.user if self.request else None,
            action_type="ACTIVATION",
        )
        return result


TEACHER_ROLE_CHOICES = [
    ("normal", "Normal Teacher"),
    ("class_teacher", "Class Teacher"),
    ("assistant_class_teacher", "Assistant Class Teacher"),
]

class UserUpdateForm(StaffAssignmentFieldsMixin, forms.ModelForm):
    role = forms.ChoiceField(
        required=True,
        label="Role",
        widget=forms.Select(attrs={"class": "hf2-select"}),
    )
    new_password = forms.CharField(
        required=False,
        label="Reset Password",
        help_text="Leave blank to keep existing password. Must be 8+ characters with at least one number.",
        widget=forms.PasswordInput(attrs={"class": "hf2-input", "placeholder": "New password for this user"}),
    )
    # Teaching assignment fields
    assignment_term = forms.ModelChoiceField(
        queryset=Term.objects.all().order_by("-end_date"),
        required=False,
        label="Term",
        widget=forms.Select(attrs={"class": "hf2-select"}),
    )
    teacher_role = forms.ChoiceField(
        choices=TEACHER_ROLE_CHOICES,
        required=False,
        initial="normal",
        label="Teacher Role",
        widget=forms.Select(attrs={"class": "hf2-select"}),
    )
    class_teacher_class = forms.ModelChoiceField(
        queryset=GradeClass.objects.all(),
        required=False,
        label="Class",
        widget=forms.Select(attrs={"class": "hf2-select"}),
    )
    teaching_classes = forms.MultipleChoiceField(
        choices=[],
        required=False,
        label="Teaching Classes",
        widget=forms.CheckboxSelectMultiple(),
    )
    subjects_taught = forms.MultipleChoiceField(
        choices=[],
        required=False,
        label="Subjects Taught",
        widget=forms.CheckboxSelectMultiple(),
    )
    repeat_across_terms = forms.BooleanField(
        required=False,
        initial=False,
        label="Repeat for all future terms",
        widget=forms.CheckboxInput(attrs={"class": "hf2-checkbox"}),
        help_text="Automatically create this assignment when a new term is added.",
    )

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "role",
            "profile_picture",
            "is_active",
            "is_staff",
            "is_superuser",
            "groups",
            "user_permissions",
        ]
        widgets = {
            "groups": forms.CheckboxSelectMultiple(),
            "user_permissions": forms.CheckboxSelectMultiple(),
            "profile_picture": forms.FileInput(attrs={"accept": ".jpg,.jpeg,.png,.gif,.webp"}),
        }

    def clean_new_password(self):
        pw = self.cleaned_data.get("new_password", "")
        if not pw:
            return pw
        errors = []
        if len(pw) < 8:
            errors.append("Password must be at least 8 characters long.")
        if not re.search(r"\d", pw):
            errors.append("Password must contain at least one number.")
        if errors:
            raise forms.ValidationError(" ".join(errors))
        return pw

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip()
        if email:
            existing = User.objects.filter(email=email).exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError(
                    "An account with this email address already exists. Please use a different email."
                )
        return email

    def clean_profile_picture(self):
        """FR-STAFF-009: Validate profile picture format + duplicate detection."""
        pic = self.cleaned_data.get("profile_picture")
        if pic:
            import os
            from academics.validators import _compute_file_hash
            ext = os.path.splitext(pic.name)[1].lower()
            allowed = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
            if ext not in allowed:
                raise forms.ValidationError(
                    f"Unsupported image format '{ext}'. Please upload JPG, PNG, GIF, or WebP."
                )
            if pic.size > 5 * 1024 * 1024:
                raise forms.ValidationError("Image must be under 5 MB.")

            # Duplicate photo detection — skip the current user
            pic.seek(0)
            new_hash = _compute_file_hash(pic)
            pic.seek(0)
            current_pk = self.instance.pk if self.instance and self.instance.pk else None
            for u in User.objects.filter(profile_picture__isnull=False).exclude(pk=current_pk):
                try:
                    if _compute_file_hash(u.profile_picture.path) == new_hash:
                        raise forms.ValidationError(
                            f"This photo is already used by {u.get_full_name() or u.username}. "
                            "Please upload a different photo."
                        )
                except (AttributeError, ValueError, OSError):
                    continue
        return pic

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Build dynamic role choices: system roles + custom roles from DB, excluding Parent
        from users.role_models import RoleConfig
        system_choices = [
            (v, l) for v, l in UserRole.choices if v != "parent"
        ]
        custom_roles = RoleConfig.objects.filter(is_system=False, is_active=True).order_by("label")
        custom_choices = [
            (f"custom_{rc.pk}", rc.label) for rc in custom_roles
        ]
        all_role_choices = system_choices + custom_choices
        self.fields["role"].choices = all_role_choices
        # Also patch the model field's choices so full_clean() during save() accepts custom roles
        from users.models import User as _UserModel
        _UserModel._meta.get_field("role").choices = all_role_choices
        self._init_staff_assignment_fields()
        if self.instance and self.instance.pk:
            self._load_staff_assignment_initial(self.instance)
        # Staff details are optional on edit too (mirrors UserCreateForm):
        # national_id_number has no rendered input, and users created before
        # onboarding may have empty staff fields — a required constraint here
        # would block unrelated edits (role change, deactivation) from saving.
        for fname in ("departments", "job_title", "employee_id", "contact_phone", "national_id_number"):
            if fname in self.fields:
                self.fields[fname].required = False
        self._apply_widget_classes()
        # Setup assignment field choices
        all_classes = GradeClass.objects.all().order_by("sort_order", "name")
        self.fields["class_teacher_class"].queryset = all_classes
        self.fields["teaching_classes"].choices = [(str(g.id), g.name) for g in all_classes]
        all_subjects = Subject.objects.filter(is_active=True).order_by("name")
        self.fields["subjects_taught"].choices = [(s.name, s.name) for s in all_subjects]
        # Only show current & future terms in the assignment dropdown
        from django.utils import timezone
        today = timezone.now().date()
        self.fields["assignment_term"].queryset = Term.objects.filter(
            end_date__gte=today
        ).order_by("-end_date")
        # Pre-select current term
        term = get_current_term()
        if term and not self.initial.get("assignment_term"):
            self.fields["assignment_term"].initial = term.pk
        # Load existing assignments
        if self.instance and self.instance.pk:
            self._load_assignment_data(self.instance)

    def _load_assignment_data(self, user):
        profile = getattr(user, "staff_profile", None)
        if not profile:
            return
        term = get_current_term() or Term.objects.order_by("-end_date").first()
        if not term:
            return
        assignments = TeacherClassAssignment.objects.filter(
            teacher=profile, term=term
        ).select_related("grade_class")
        ct_class = None
        teaching_class_ids = []
        all_subjects = set()
        teacher_role = "normal"
        repeat = False
        for ass in assignments:
            if ass.is_class_teacher:
                ct_class = ass.grade_class_id
                teacher_role = "class_teacher"
            elif ass.is_assistant_class_teacher and teacher_role != "class_teacher":
                teacher_role = "assistant_class_teacher"
            teaching_class_ids.append(str(ass.grade_class_id))
            if isinstance(ass.subjects_taught, list):
                all_subjects.update(ass.subjects_taught)
            if ass.repeat_across_terms:
                repeat = True
        self.fields["teacher_role"].initial = teacher_role
        if ct_class:
            self.fields["class_teacher_class"].initial = ct_class
        self.fields["teaching_classes"].initial = teaching_class_ids
        self.fields["subjects_taught"].initial = list(all_subjects)
        self.fields["repeat_across_terms"].initial = repeat

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            new_pw = self.cleaned_data.get("new_password", "")
            if new_pw:
                user.set_password(new_pw)
                # Persist the password change explicitly since set_password()
                # only modifies the in-memory attribute.
                user.save(update_fields=["password"])
            apply_django_admin_flags(user, role=self.cleaned_data.get("role"))
            user.save(update_fields=["is_staff", "is_superuser"])
            self._save_staff_assignment(user)
            self._save_assignments(user)
        return user

    def _save_assignments(self, user):
        import json
        from academics.models import AcademicYear
        profile = getattr(user, "staff_profile", None)
        if not profile:
            return
        ct_class = self.cleaned_data.get("class_teacher_class")
        teaching_classes = self.cleaned_data.get("teaching_classes") or []
        subjects = self.cleaned_data.get("subjects_taught") or []
        teacher_role = self.cleaned_data.get("teacher_role", "normal")

        # --- Resolve selected terms (multi-term) ---
        raw_term_ids = self.data.getlist("assignment_terms") or []
        # Also check the hidden JSON field for programmatic access
        hidden_terms = self.data.get("selected_terms_json", "")
        if hidden_terms:
            try:
                raw_term_ids = json.loads(hidden_terms)
            except (json.JSONDecodeError, TypeError):
                pass
        # Check for "all" marker OR if all available terms are selected
        all_term_pks = set(str(t) for t in Term.objects.values_list("pk", flat=True))
        is_all = "all" in [str(t) for t in raw_term_ids] or (
            all_term_pks and all_term_pks.issubset(set(str(t) for t in raw_term_ids))
        )
        if is_all:
            from academics.utils import get_current_academic_year
            current_year = get_current_academic_year()
            if current_year:
                terms = list(Term.objects.filter(academic_year=current_year).order_by("start_date"))
            else:
                terms = list(Term.objects.all().order_by("-end_date")[:10])
        else:
            # Filter out non-numeric term IDs
            term_id_list = [int(t) for t in raw_term_ids if str(t).isdigit()]
            if not term_id_list:
                # Fallback: try the old single-term field
                old_term = self.cleaned_data.get("assignment_term")
                if old_term:
                    term_id_list = [old_term.pk]
                else:
                    ct = get_current_term()
                    if ct:
                        term_id_list = [ct.pk]
            if not term_id_list:
                return
            terms = list(Term.objects.filter(pk__in=term_id_list).order_by("start_date"))

        if not terms:
            return

        # Read per-class subjects from hidden JSON field
        per_class_subjects = {}
        raw_json = self.data.get("class_subjects_json", "")
        if raw_json:
            try:
                per_class_subjects = json.loads(raw_json)
            except (json.JSONDecodeError, TypeError):
                pass

        all_class_ids = set()
        ct_class_id = None
        asst_class_id = None
        if ct_class and teacher_role == "class_teacher":
            ct_class_id = str(ct_class.pk)
            all_class_ids.add(ct_class_id)
        if ct_class and teacher_role == "assistant_class_teacher":
            asst_class_id = str(ct_class.pk)
            all_class_ids.add(asst_class_id)
        all_class_ids.update(teaching_classes)

        with transaction.atomic():
            for term in terms:
                if not all_class_ids:
                    TeacherClassAssignment.objects.filter(teacher=profile, term=term).delete()
                    continue

                # Delete assignments for classes no longer selected in this term
                TeacherClassAssignment.objects.filter(
                    teacher=profile, term=term
                ).exclude(grade_class_id__in=[int(c) for c in all_class_ids]).delete()

                for cid in all_class_ids:
                    gc = GradeClass.objects.get(pk=int(cid))
                    is_ct = bool(ct_class_id and ct_class_id == cid)
                    is_asst = bool(asst_class_id and asst_class_id == cid)
                    if is_ct:
                        TeacherClassAssignment.objects.filter(
                            teacher=profile, term=term, is_class_teacher=True
                        ).exclude(grade_class=gc).update(is_class_teacher=False)
                    if is_asst:
                        TeacherClassAssignment.objects.filter(
                            teacher=profile, term=term, is_assistant_class_teacher=True
                        ).exclude(grade_class=gc).update(is_assistant_class_teacher=False)
                    class_subjects = per_class_subjects.get(cid, subjects)
                    TeacherClassAssignment.objects.update_or_create(
                        teacher=profile,
                        term=term,
                        grade_class=gc,
                        defaults={
                            "is_class_teacher": is_ct,
                            "is_assistant_class_teacher": is_asst,
                            "subjects_taught": class_subjects,
                            "repeat_across_terms": self.cleaned_data.get("repeat_across_terms", False),
                        },
                    )
