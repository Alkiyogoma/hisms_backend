from django import forms
from django.db.models import Q
from django.utils import timezone

from academics.models import GradeClass, Department
from students.models import Student

from .consistency import notes_conflicts
from .models import (
    INCIDENT_BEHAVIOURS,
    SCHOOL_ACTIONS,
    DisciplineIncident,
    FollowUpKind,
    IncidentSeverity,
    IncidentStatus,
    ParentContactMethod,
    severity_for_levels,
)

YES_NO = [("yes", "Yes"), ("no", "No")]
NO_FOLLOW_UP = "none"


def parent_contact_fields():
    """Parent contact and follow-up, answered explicitly rather than defaulting to No."""
    return {
        "parent_contacted": forms.ChoiceField(choices=YES_NO, widget=forms.RadioSelect),
        "parent_contact_date": forms.DateField(required=False),
        "parent_contact_method": forms.ChoiceField(
            choices=[("", "Select...")] + ParentContactMethod.choices, required=False
        ),
        "parent_contact_person": forms.CharField(max_length=120, required=False),
        "follow_up": forms.ChoiceField(choices=[(NO_FOLLOW_UP, "Not required")] + FollowUpKind.choices),
        "follow_up_details": forms.CharField(max_length=255, required=False),
        "follow_up_date": forms.DateField(required=False),
        "follow_up_time": forms.TimeField(required=False),
    }


def parent_contact_initial(incident):
    return {
        "parent_contacted": "yes" if incident.parent_contacted else "no",
        "parent_contact_date": incident.parent_contact_date,
        "parent_contact_method": incident.parent_contact_method,
        "parent_contact_person": incident.parent_contact_person,
        "follow_up": (incident.follow_up_kind or FollowUpKind.OTHER) if incident.follow_up_required else NO_FOLLOW_UP,
        "follow_up_details": incident.follow_up_details,
        "follow_up_date": incident.follow_up_date,
        "follow_up_time": incident.follow_up_time,
    }


def clean_parent_contact(form, cleaned, incident_date, notes):
    """Validate the contact/follow-up answers against the dates and against the notes."""
    contacted = cleaned.get("parent_contacted") == "yes"
    follow_up = cleaned.get("follow_up")
    today = timezone.localdate()

    if contacted:
        when = cleaned.get("parent_contact_date")
        if not when:
            form.add_error("parent_contact_date", "Enter the date the parent was contacted.")
        elif when > today:
            form.add_error("parent_contact_date", "The contact date cannot be in the future.")
        elif incident_date and when < incident_date:
            form.add_error("parent_contact_date", "The contact date cannot be before the incident.")
        if not cleaned.get("parent_contact_method"):
            form.add_error("parent_contact_method", "Say how the parent was contacted.")
        if not (cleaned.get("parent_contact_person") or "").strip():
            form.add_error("parent_contact_person", "Say which parent or guardian was contacted.")

    if follow_up and follow_up != NO_FOLLOW_UP:
        # The date is recorded where it is known; what the follow-up is, always.
        when = cleaned.get("follow_up_date")
        if when and incident_date and when < incident_date:
            form.add_error("follow_up_date", "The follow-up date cannot be before the incident.")
        if follow_up == FollowUpKind.OTHER and not (cleaned.get("follow_up_details") or "").strip():
            form.add_error("follow_up_details", "Say what the follow-up is.")

    if "parent_contacted" in cleaned and follow_up:
        for error in notes_conflicts(notes, contacted, follow_up != NO_FOLLOW_UP):
            form.add_error(None, error)


def apply_parent_contact(incident, cleaned):
    contacted = cleaned["parent_contacted"] == "yes"
    incident.parent_contacted = contacted
    incident.parent_contact_date = cleaned.get("parent_contact_date") if contacted else None
    incident.parent_contact_method = cleaned.get("parent_contact_method", "") if contacted else ""
    incident.parent_contact_person = (cleaned.get("parent_contact_person") or "").strip() if contacted else ""
    follow_up = cleaned["follow_up"]
    incident.follow_up_required = follow_up != NO_FOLLOW_UP
    incident.follow_up_kind = follow_up if incident.follow_up_required else ""
    incident.follow_up_date = cleaned.get("follow_up_date") if incident.follow_up_required else None
    incident.follow_up_time = cleaned.get("follow_up_time") if incident.follow_up_required else None
    incident.follow_up_details = (
        (cleaned.get("follow_up_details") or "").strip() if incident.follow_up_required else ""
    )


CONTACT_FIELDS = [
    "parent_contacted", "parent_contact_date", "parent_contact_method", "parent_contact_person",
    "follow_up_required", "follow_up_kind", "follow_up_date", "follow_up_time", "follow_up_details",
]


class DisciplineIncidentForm(forms.ModelForm):
    """Form for teachers to submit a discipline incident."""

    class Meta:
        model = DisciplineIncident
        fields = [
            "student", "severity", "summary", "action_taken",
            "incident_date", "time_of_incident", "location",
            "incident_level_1", "incident_level_2", "incident_level_3",
            "incident_level_4", "actions_taken_detailed",
        ]
        widgets = {
            "student": forms.Select(attrs={"class": "form-select"}),
            "severity": forms.Select(attrs={"class": "form-select"}),
            "summary": forms.Textarea(
                attrs={
                    "class": "form-input",
                    "rows": 4,
                    "placeholder": "Describe what happened — what was observed, reported, or witnessed.",
                }
            ),
            "action_taken": forms.Textarea(
                attrs={
                    "class": "form-input",
                    "rows": 3,
                    "placeholder": "What action was taken? (e.g. spoke to student, called parent, sent to HOD)",
                }
            ),
            "time_of_incident": forms.TimeInput(attrs={"class": "form-input hf2-time", "type": "time"}),
            "location": forms.TextInput(attrs={"class": "form-input", "placeholder": "e.g. Playground, Classroom..."}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields.update(parent_contact_fields())
        self.fields["incident_date"].required = True
        # Scope students to the teacher's assigned classes
        if self.user and self.user.role == "teacher":
            from timetable.models import TimetableSlot
            assigned = set(
                TimetableSlot.objects.filter(teacher=self.user)
                .values_list("class_name", flat=True)
                .distinct()
            )
            from hr.models import TeacherClassAssignment
            assigned.update(
                TeacherClassAssignment.objects.filter(teacher__user=self.user)
                .values_list("grade_class__name", flat=True)
                .distinct()
            )
            self.fields["student"].queryset = Student.objects.filter(
                class_name__in=assigned, is_archived=False
            ).order_by("class_name", "last_name", "first_name")
        else:
            # HODs/admins see all students
            self.fields["student"].queryset = Student.objects.filter(
                is_archived=False
            ).order_by("class_name", "last_name", "first_name")

        # Teachers cannot select Critical severity — must escalate from lower
        if self.user and self.user.role == "teacher":
            self.fields["severity"].choices = [
                (k, v) for k, v in IncidentSeverity.choices
                if k in ("low", "medium", "high")
            ]

        # Add empty choice
        self.fields["student"].empty_label = "Select a student..."

        # Make JSON/list fields not required
        for f in ["incident_level_1", "incident_level_2", "incident_level_3",
                   "incident_level_4", "actions_taken_detailed"]:
            self.fields[f].required = False
        # Severity is derived from the levels ticked in clean(), not chosen.
        self.fields["severity"].required = False

    def ticked_levels(self):
        return [n for n in range(1, 5) if self.data.getlist(f"level_{n}")]

    def clean(self):
        cleaned = super().clean()
        levels = self.ticked_levels()
        is_teacher = self.user and self.user.role == "teacher"
        if is_teacher and 4 in levels:
            self.add_error(
                None,
                "Level 4 incidents are recorded by the Head of School. Report this one to them directly.",
            )
        cleaned["severity"] = severity_for_levels(levels)
        incident_date = cleaned.get("incident_date")
        if incident_date and incident_date > timezone.localdate():
            self.add_error("incident_date", "The date of the incident cannot be in the future.")
        clean_parent_contact(
            self, cleaned, incident_date, [cleaned.get("summary", ""), cleaned.get("action_taken", "")]
        )
        return cleaned


class DisciplineReviewForm(forms.Form):
    """Reviewer's form: status and notes, plus parent contact and follow-up so the
    fields can be brought up to date alongside the notes that describe them.

    On a closed incident only a comment can be added (``locked=True``)."""

    STATUS_CHOICES = [
        ("under_investigation", "Under Investigation"),
        ("resolved", "Resolved"),
        ("dismissed", "Dismissed"),
    ]

    status = forms.ChoiceField(
        choices=STATUS_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    hod_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-input",
                "rows": 3,
                "placeholder": "Review notes on this incident (optional for resolved/dismissed).",
            }
        ),
    )
    comment = forms.CharField(required=False)

    def __init__(self, *args, incident, locked=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.incident = incident
        self.locked = locked
        self.fields.update(parent_contact_fields())
        if locked:
            del self.fields["status"]
            del self.fields["hod_notes"]
            self.fields["comment"].required = True

    def review_notes(self):
        """The review notes as they will read after saving."""
        if self.locked:
            return "\n".join(filter(None, [self.incident.hod_notes, self.cleaned_data.get("comment", "")]))
        return self.cleaned_data.get("hod_notes", "")

    def clean(self):
        cleaned = super().clean()
        notes = [self.incident.summary, self.incident.action_taken, self.review_notes()]
        clean_parent_contact(self, cleaned, self.incident.occurred_on, notes)
        return cleaned


def _choices_with(options, current):
    """Choices for a tick list, keeping any value already on the record that is no longer offered."""
    return [(o, o) for o in options] + [(o, o) for o in current if o not in options]


class DisciplineAmendForm(forms.ModelForm):
    """Correct a submitted incident. A reason is required and nothing saves without one."""

    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

    class Meta:
        model = DisciplineIncident
        fields = [
            "student", "incident_date", "time_of_incident", "location", "summary", "action_taken",
            "incident_level_1", "incident_level_2", "incident_level_3", "incident_level_4",
            "actions_taken_detailed",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        incident = self.instance
        self.fields["student"].queryset = Student.objects.filter(
            Q(is_archived=False) | Q(pk=incident.student_id)
        ).order_by("class_name", "last_name", "first_name")
        self.fields["incident_date"].required = True
        self.fields["action_taken"].required = False
        for n, options in INCIDENT_BEHAVIOURS.items():
            name = f"incident_level_{n}"
            self.fields[name] = forms.MultipleChoiceField(
                choices=_choices_with(options, getattr(incident, name) or []),
                required=False, widget=forms.CheckboxSelectMultiple,
            )
        self.fields["actions_taken_detailed"] = forms.MultipleChoiceField(
            choices=_choices_with(SCHOOL_ACTIONS, incident.actions_taken_detailed or []),
            required=False, widget=forms.CheckboxSelectMultiple,
        )

    def ticked_levels(self):
        return [n for n in range(1, 5) if self.cleaned_data.get(f"incident_level_{n}")]

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("Give the reason for the change.")
        return reason

    def clean(self):
        cleaned = super().clean()
        incident_date = cleaned.get("incident_date")
        if incident_date and incident_date > timezone.localdate():
            self.add_error("incident_date", "The date of the incident cannot be in the future.")
        if self.errors:
            return cleaned
        if not self.has_changed() or self.changed_data == ["reason"]:
            raise forms.ValidationError("Nothing has been changed, so there is nothing to save.")
        incident = self.instance
        notes = [cleaned.get("summary", ""), cleaned.get("action_taken", ""), incident.hod_notes]
        for error in notes_conflicts(notes, incident.parent_contacted, incident.follow_up_required):
            self.add_error(None, error)
        return cleaned
