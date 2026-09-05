from django import forms
from django.utils import timezone

from academics.models import GradeClass, Department
from students.models import Student

from .models import DisciplineIncident, IncidentSeverity, IncidentStatus


class DisciplineIncidentForm(forms.ModelForm):
    """Form for teachers to submit a discipline incident."""

    class Meta:
        model = DisciplineIncident
        fields = [
            "student", "severity", "summary", "action_taken",
            "time_of_incident", "location", "previous_incidents",
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
                   "incident_level_4", "actions_taken_detailed", "previous_incidents"]:
            self.fields[f].required = False


class DisciplineReviewForm(forms.Form):
    """Form for HOD to review a discipline incident."""

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
                "placeholder": "HOD notes on this incident (optional for resolved/dismissed).",
            }
        ),
    )
