from __future__ import annotations

import re

from django import forms

from admissions.models import AssessmentSchedule, MeetingSchedule


def _strip_html(value):
    """Strip HTML tags from user input to prevent stored XSS."""
    if not value:
        return value
    return re.sub(r'<[^>]+>', '', value).strip()


class AssessmentScheduleForm(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # FR-ADM-015: Facilitating teacher as dropdown from staff
        from users.models import User, UserRole
        teachers = User.objects.filter(
            role__in=[UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.ECD_HOD],
            is_active=True,
        ).order_by("first_name", "last_name")
        teacher_choices = [("", "Select teacher")]
        for t in teachers:
            name = t.get_full_name() or t.username
            teacher_choices.append((name, name))
        self.fields["facilitating_teacher_name"].widget = forms.Select(attrs={"class": "hf2-select"}, choices=teacher_choices)
        self.fields["facilitating_teacher_name"].required = True

        # FR-ADM-013: 12-hour time format
        self.fields["scheduled_time"].widget = forms.Select(
            attrs={"class": "hf2-select"},
            choices=[("", "Select time")] + [
                (f"{h:02d}:{m:02d}", f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}")
                for h in range(7, 18) for m in (0, 15, 30, 45)
            ],
        )

    class Meta:
        model = AssessmentSchedule
        fields = [
            "scheduled_date",
            "scheduled_time",
            "facilitating_teacher_name",
        ]
        widgets = {
            "scheduled_date": forms.DateInput(attrs={"type": "date"}),
        }


class AssessmentResultForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["result"].required = True
        self.fields["teacher_comments"].required = True
        self.fields["teacher_comments"].widget.attrs["placeholder"] = "Narrative notes about the assessment..."
        self.fields["hod_comments"].widget.attrs["placeholder"] = "Internal HOD notes (staff only, never shown to parents)..."
        self.fields["parent_facing_comments"].widget.attrs["placeholder"] = "Feedback to share with parents (e.g. strengths, areas for growth)..."

    def clean_teacher_comments(self):
        return _strip_html(self.cleaned_data.get("teacher_comments", ""))

    def clean_hod_comments(self):
        return _strip_html(self.cleaned_data.get("hod_comments", ""))

    def clean_parent_facing_comments(self):
        return _strip_html(self.cleaned_data.get("parent_facing_comments", ""))

    class Meta:
        model = AssessmentSchedule
        fields = [
            "teacher_comments",
            "hod_comments",
            "parent_facing_comments",
            "result",
        ]


class HodReviewForm(forms.ModelForm):
    """HOD submits their review comments before forwarding to HOS."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["hod_comments"].widget.attrs["placeholder"] = "Internal HOD review notes (staff only)..."
        self.fields["parent_facing_comments"].widget.attrs["placeholder"] = "Optional feedback for parents..."

    def clean_hod_comments(self):
        return _strip_html(self.cleaned_data.get("hod_comments", ""))

    def clean_parent_facing_comments(self):
        return _strip_html(self.cleaned_data.get("parent_facing_comments", ""))

    class Meta:
        model = AssessmentSchedule
        fields = ["hod_comments", "parent_facing_comments"]


class MeetingScheduleForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # 12-hour time dropdown
        self.fields["meeting_time"].widget = forms.Select(
            attrs={"class": "hf2-select"},
            choices=[("", "Select time")] + [
                (f"{h:02d}:{m:02d}", f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}")
                for h in range(7, 18) for m in (0, 15, 30, 45)
            ],
        )

        self.fields["notes"].widget.attrs["placeholder"] = "Optional notes for the meeting..."

    def clean_notes(self):
        return _strip_html(self.cleaned_data.get("notes", ""))

    class Meta:
        model = MeetingSchedule
        fields = ["meeting_date", "meeting_time", "notes"]
        widgets = {
            "meeting_date": forms.DateInput(attrs={"type": "date"}),
        }


