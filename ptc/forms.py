# PTC Forms  matching existing Hodari form conventions.

from django import forms

from ptc.models import (
    PTCWindow,
    TermSlot,
    LearnerAttributeRating,
    PTCSubjectComment,
    LearnerAttributeRatingEntry,
)


class PTCWindowForm(forms.ModelForm):
    """Used by Admin Officer to configure PTC dates (FR-PTC-001)."""

    class Meta:
        model = PTCWindow
        fields = [
            "academic_year",
            "term_slot",
            "ptc_date",
            "notification_window_days",
            "comment_entry_window_days",
        ]
        widgets = {
            "ptc_date": forms.DateInput(
                attrs={"type": "date", "class": "form-input"},
                format="%Y-%m-%d",
            ),
            "academic_year": forms.Select(attrs={"class": "form-input"}),
            "term_slot": forms.Select(attrs={"class": "form-input"}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        # If window is published, only Super Admin can edit dates
        if self.instance and self.instance.is_published:
            if self.user and self.user.role != "super_admin":
                raise forms.ValidationError(
                    "This PTC window is published. Only Super Admin can edit dates."
                )
        return cleaned


class PTCSubjectCommentForm(forms.ModelForm):
    """Subject teacher comment entry form (FR-PTC-012)."""

    class Meta:
        model = PTCSubjectComment
        fields = ["comment_text"]
        widgets = {
            "comment_text": forms.Textarea(
                attrs={
                    "rows": 3,
                    "class": "form-input",
                    "placeholder": "Enter your comment for this student (recommended minimum 30 characters)...",
                    "data-soft-min": "30",
                }
            ),
        }
        labels = {
            "comment_text": "Subject Comment",
        }


class LearnerAttributeRatingForm(forms.Form):
    """
    Class-wide grid form: students as rows, 12 attributes as columns.
    Single save for the whole grid (FR-PTC-020).
    """
    # Dynamically built in the view; not a standard ModelForm.
    pass


class PTCDateChangeForm(forms.Form):
    """Super Admin date change with audit trail (FR-PTC-002)."""

    new_ptc_date = forms.DateField(
        widget=forms.DateInput(
            attrs={"type": "date", "class": "form-input"},
            format="%Y-%m-%d",
        ),
        label="New PTC Date",
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2, "class": "form-input"}),
        required=True,
        label="Reason for change",
    )
