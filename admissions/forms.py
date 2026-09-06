from django import forms
import re

from academics.models import Department, GradeClass
from admissions.models import Applicant, ApplicantStatus, InquiryChannel, get_previous_school_choices


def _strip_html(value):
    """Strip HTML tags from user input to prevent stored XSS."""
    if not value:
        return value
    return re.sub(r'<[^>]+>', '', value).strip()


class ApplicantCreateForm(forms.ModelForm):
    applying_department = forms.ChoiceField(
        choices=[("", "Select department")]
        + [(d, lbl) for d, lbl in Department.choices if d != Department.ADMINISTRATION],
        required=True,
        label="Department applying to*",
    )
    class Meta:
        model = Applicant
        fields = [
            "parent_phone",
            "parent_full_name",
            "parent_email",
            "parent_invoice_name",
            "parent_relationship",
            "child_full_name",
            "child_date_of_birth",
            "grade_applying_for",
            "previous_school",
            "previous_school_other",
            "sibling_details",
            "inquiry_channel",
            "preferred_meeting_date",
            "preferred_meeting_time",
            "target_enrollment_year",
            "target_enrollment_month",
            "notes",
            "sibling_matched_parent",
            "sibling_link_decision",
            "photo",
        ]
        widgets = {
            "child_date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "preferred_meeting_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "sibling_matched_parent": forms.HiddenInput(),
            "sibling_link_decision": forms.HiddenInput(),
            "inquiry_channel": forms.Select(
                choices=[("", "Select how this inquiry came in…"), *InquiryChannel.choices]
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        dept = None
        if self.data.get("applying_department"):
            dept = self.data.get("applying_department")
        elif self.instance and self.instance.pk:
            ag = GradeClass.objects.filter(name__iexact=self.instance.grade_applying_for).first()
            if ag:
                dept = ag.department

        grades_qs = GradeClass.objects.order_by("sort_order", "name")
        if dept:
            grades_qs = grades_qs.filter(department__iexact=dept)

        grade_choices = [("", "Select grade / class")] + [(g.name, g.name) for g in grades_qs]
        self.fields["grade_applying_for"] = forms.ChoiceField(choices=grade_choices, label="Grade / class applying for*")
        if dept:
            self.fields["applying_department"].initial = dept
        self.fields["parent_full_name"].label = "Parent / guardian name*"
        self.fields["parent_phone"].label = "Phone number*"
        self.fields["parent_email"].label = "Email address"
        self.fields["parent_invoice_name"].label = "Preferred address on invoice"
        self.fields["child_full_name"].label = "Student full name*"
        self.fields["child_date_of_birth"].label = "Date of birth*"
        self.fields["previous_school"].label = "Previous school"
        self.fields["previous_school"].widget = forms.Select(
            attrs={"class": "hf2-select"},
            choices=get_previous_school_choices(),
        )
        self.fields["sibling_details"].label = "Siblings currently enrolled at Hodari"
        self.fields["notes"].label = "Inquiry notes"
        self.fields["inquiry_channel"].label = "How did this inquiry come in?*"
        # Show a blank prompt so the channel must be actively chosen.
        self.fields["inquiry_channel"].choices = [
            ("", "Select how this inquiry came in…"),
            *InquiryChannel.choices,
        ]
        self.fields["photo"].label = "Student Photo"
        self.fields["photo"].help_text = "Optional. High quality photo for the student file."
        # Start blank so the user must actively choose a channel (this also gates
        # the reveal of the rest of the form). Model still defaults to WALK_IN.
        self.fields["inquiry_channel"].initial = ""

        self.fields["parent_phone"].help_text = "Primary contact number for all school communication."
        self.fields["parent_email"].help_text = "Optional. Used for digital communications and report delivery."
        self.fields["parent_invoice_name"].help_text = "Used on all fee invoices for this family."
        self.fields["sibling_details"].help_text = "System will check automatically once parent details are entered."

        # S5: Custom error messages for mandatory fields
        self.fields["parent_full_name"].error_messages["required"] = "Parent / guardian name is required."
        self.fields["parent_phone"].error_messages["required"] = "Phone number is required."
        self.fields["child_full_name"].error_messages["required"] = "Student name is required."
        self.fields["child_date_of_birth"].error_messages["required"] = "Date of birth is required."
        self.fields["grade_applying_for"].error_messages["required"] = "Grade / class applying for is required."
        self.fields["applying_department"].error_messages["required"] = "Department is required."

        self.fields["parent_full_name"].widget.attrs["placeholder"] = "e.g. Mr. John Kamau"
        self.fields["parent_phone"].widget.attrs["placeholder"] = "+255 7XX XXX XXX"
        self.fields["parent_email"].widget.attrs["placeholder"] = "parent@email.com"
        self.fields["parent_invoice_name"].widget.attrs["placeholder"] = "How should invoices be addressed?"
        self.fields["child_full_name"].widget.attrs["placeholder"] = "Student's full name"
        self.fields["previous_school_other"].widget.attrs["placeholder"] = "Enter school name"
        self.fields["previous_school_other"].help_text = "Only required if 'Other' selected above."
        self.fields["parent_relationship"].label = "Relationship to student*"
        self.fields["parent_relationship"].widget = forms.Select(
            attrs={"class": "hf2-select"},
            choices=[("", "Select relationship...")] + Applicant.PARENT_RELATIONSHIP_CHOICES,
        )
        self.fields["parent_relationship"].error_messages["required"] = "Please select the parent/guardian's relationship to the student."
        self.fields["sibling_details"].widget.attrs["placeholder"] = "Name and class of any siblings (if applicable)"
        self.fields["notes"].widget.attrs["placeholder"] = (
            "Any relevant context from this inquiry — special requirements, how they heard about us, urgency, etc."
        )

        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs["class"] = "hf2-select"
            elif isinstance(field.widget, forms.DateInput):
                field.widget.attrs["class"] = "hf2-input hf2-date"
            elif isinstance(field.widget, forms.FileInput):
                field.widget.attrs["class"] = "hf2-file-input"
            else:
                field.widget.attrs["class"] = "hf2-input"

            if name == "child_date_of_birth":
                field.widget.attrs.setdefault("max", "2099-12-31")

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("sibling_details") or "").strip():
            cleaned["sibling_currently_enrolled"] = True

        # If 'Other' selected for previous school, use the free-text field
        if cleaned.get("previous_school") == "__other__":
            other = (cleaned.get("previous_school_other") or "").strip()
            if other:
                cleaned["previous_school"] = other
            else:
                self.add_error("previous_school_other", "Please enter the school name.")

        # S5: inquiry_channel is a HiddenInput with a model default,
        # so Django's built-in required check won't catch a truly empty value.
        channel = cleaned.get("inquiry_channel", "")
        if not channel or channel not in dict(InquiryChannel.choices):
            self.add_error("inquiry_channel", "Please select how this inquiry was received.")

        # FR-ADM-030: parent_relationship is required
        if not cleaned.get("parent_relationship"):
            self.add_error("parent_relationship", "Please select the parent/guardian's relationship to the student.")

        return cleaned

    def clean_notes(self):
        return _strip_html(self.cleaned_data.get("notes", ""))


class ApplicantEditForm(forms.ModelForm):
    """FR-ADM-002: Edit inquiry fields. AO and SA only.
    Used as a modal on the applicant detail page.
    """
    applying_department = forms.ChoiceField(
        choices=[("", "Select department")]
        + [(d, lbl) for d, lbl in Department.choices if d != Department.ADMINISTRATION],
        required=True,
        label="Department applying to*",
    )
    class Meta:
        model = Applicant
        fields = [
            "parent_phone",
            "parent_full_name",
            "parent_email",
            "parent_invoice_name",
            "parent_relationship",
            "child_full_name",
            "child_date_of_birth",
            "grade_applying_for",
            "previous_school",
            "previous_school_other",
            "sibling_details",
            "inquiry_channel",
            "target_enrollment_year",
            "target_enrollment_month",
            "notes",
            "photo",
        ]
        widgets = {
            "child_date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "inquiry_channel": forms.Select(attrs={"class": "hf2-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        dept = None
        if self.data.get("applying_department"):
            dept = self.data.get("applying_department")
        elif self.instance and self.instance.pk:
            ag = GradeClass.objects.filter(name__iexact=self.instance.grade_applying_for).first()
            if ag:
                dept = ag.department

        grades_qs = GradeClass.objects.order_by("sort_order", "name")
        if dept:
            grades_qs = grades_qs.filter(department__iexact=dept)

        grade_choices = [("", "Select grade / class")] + [(g.name, g.name) for g in grades_qs]
        self.fields["grade_applying_for"] = forms.ChoiceField(choices=grade_choices, label="Grade / class applying for*")
        if dept:
            self.fields["applying_department"].initial = dept
        self.fields["parent_full_name"].label = "Parent / guardian name*"
        self.fields["parent_phone"].label = "Phone number*"
        self.fields["parent_email"].label = "Email address"
        self.fields["parent_invoice_name"].label = "Preferred address on invoice"
        self.fields["child_full_name"].label = "Student full name*"
        self.fields["child_date_of_birth"].label = "Date of birth*"
        self.fields["previous_school"].label = "Previous school"
        self.fields["previous_school"].widget = forms.Select(
            attrs={"class": "hf2-select"},
            choices=get_previous_school_choices(),
        )
        self.fields["sibling_details"].label = "Siblings currently enrolled at Hodari"
        self.fields["notes"].label = "Inquiry notes"
        self.fields["inquiry_channel"].label = "How did they hear about us? (Channel)*"
        self.fields["photo"].label = "Student Photo"
        self.fields["photo"].help_text = "Optional. High quality photo for the student file."
        self.fields["parent_relationship"].label = "Relationship to student*"
        self.fields["parent_relationship"].widget = forms.Select(
            attrs={"class": "hf2-select"},
            choices=[("", "Select relationship...")] + Applicant.PARENT_RELATIONSHIP_CHOICES,
        )
        self.fields["previous_school_other"].widget.attrs["placeholder"] = "Enter school name"
        self.fields["sibling_details"].widget.attrs["placeholder"] = "Name and class of any siblings (if applicable)"
        self.fields["notes"].widget.attrs["placeholder"] = (
            "Any relevant context from this inquiry."
        )

        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs["class"] = "hf2-select"
            elif isinstance(field.widget, forms.DateInput):
                field.widget.attrs["class"] = "hf2-input hf2-date"
            elif isinstance(field.widget, forms.FileInput):
                field.widget.attrs["class"] = "hf2-file-input"
            else:
                field.widget.attrs["class"] = "hf2-input"

            if name == "child_date_of_birth":
                field.widget.attrs.setdefault("max", "2099-12-31")

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("sibling_details") or "").strip():
            cleaned["sibling_currently_enrolled"] = True
        if cleaned.get("previous_school") == "__other__":
            other = (cleaned.get("previous_school_other") or "").strip()
            if other:
                cleaned["previous_school"] = other
            else:
                self.add_error("previous_school_other", "Please enter the school name.")
        channel = cleaned.get("inquiry_channel", "")
        if not channel or channel not in dict(InquiryChannel.choices):
            self.add_error("inquiry_channel", "Please select how this inquiry was received.")
        if not cleaned.get("parent_relationship"):
            self.add_error("parent_relationship", "Please select the parent/guardian's relationship to the student.")
        return cleaned

    def clean_notes(self):
        return _strip_html(self.cleaned_data.get("notes", ""))


class ApplicantFilterForm(forms.Form):
    q = forms.CharField(required=False)
    status = forms.ChoiceField(
        required=False,
        choices=[("", "All statuses"), *ApplicantStatus.choices],
    )
    department = forms.ChoiceField(required=False, label="Department")
    grade = forms.ChoiceField(required=False)
    start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    status_scope = forms.ChoiceField(
        required=False,
        choices=[
            ("", "All statuses"),
            ("active", "Active only"),
            ("enrolled_range", "Enrolled this period"),
        ],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].choices = [("", "All departments")] + [
            (d, lbl) for d, lbl in Department.choices if d != Department.ADMINISTRATION
        ]
        dept = self.data.get("department") if self.is_bound else None
        grades_qs = GradeClass.objects.order_by("sort_order", "name")
        if dept:
            grades_qs = grades_qs.filter(department__iexact=dept)
        self.fields["grade"].choices = [("", "All grades")] + [(g.name, g.name) for g in grades_qs]
