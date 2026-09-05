from __future__ import annotations

from django import forms

from academics.models import Department, LessonPlan, LessonPlanStatus, Subject, Term


class LessonPlanForm(forms.ModelForm):
    """Term is auto-set from the active academic year configuration — not user-selectable."""

    class Meta:
        model = LessonPlan
        fields = [
            "class_name",
            "subject_name",
            "week_start_date",
            "day_of_week",
            "lesson_title",
        ]
        widgets = {
            "week_start_date": forms.DateInput(attrs={"type": "date"}),
            "day_of_week": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.user = user
        self._current_term = None
        
        from datetime import date, timedelta
        
        from academics.models import GradeClass, Subject, Term
        from timetable.models import TimetableSlot
        from users.models import UserRole
        from academics.utils import get_current_term

        # Resolve current term (read-only — not selectable by user)
        current_term = get_current_term()
        self._current_term = current_term
        if self.instance.pk and self.instance.term_id:
            self._current_term = self.instance.term
        elif current_term and not self.instance.pk:
            self.instance.term = current_term
        
        # Block past weeks: only this Monday (current week) and future weeks allowed
        today = date.today()
        this_monday = today - timedelta(days=today.weekday())
        self.fields["week_start_date"].widget.attrs["min"] = this_monday.isoformat()
        self.fields["week_start_date"].label = "Lesson Date"
        
        # Filter choices based on user role and timetable assignments
        if user and user.role == UserRole.TEACHER:
            # Get assigned classes and subjects from timetable
            assigned = TimetableSlot.objects.filter(teacher=user)
            assigned_classes = assigned.values_list("class_name", flat=True).distinct().order_by("class_name")
            assigned_subjects = assigned.values_list("subject_name", flat=True).distinct().order_by("subject_name")
            
            class_choices = [("", "Select Assigned Class")] + [(name, name) for name in assigned_classes]
            subject_choices = [("", "Select Assigned Subject")] + [(name, name) for name in assigned_subjects]
            
            # If teacher has no assignments, show a helpful message in choices
            if not assigned_classes:
                class_choices = [("", "No classes assigned in timetable")]
            if not assigned_subjects:
                subject_choices = [("", "No subjects assigned in timetable")]
        else:
            # Admin roles see everything
            class_choices = [("", "Select Class")] + [(c.name, c.name) for c in GradeClass.objects.all().order_by("name")]
            subject_choices = [("", "Select Subject")] + [(s.name, s.name) for s in Subject.objects.filter(is_active=True).order_by("name")]
        
        self.fields["class_name"] = forms.ChoiceField(choices=class_choices, label="Class")
        self.fields["subject_name"] = forms.ChoiceField(choices=subject_choices, label="Subject")

        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs["class"] = "hf-select mt-1"
            elif isinstance(field.widget, forms.DateInput):
                field.widget.attrs["class"] = "hf-input hf2-date mt-1"
            else:
                field.widget.attrs["class"] = "hf-input mt-1"

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self._current_term:
            instance.term = self._current_term
        if commit:
            instance.save()
            self._save_m2m()
        return instance

    def clean_week_start_date(self):
        from datetime import date, timedelta
        val = self.cleaned_data.get("week_start_date")
        if val:
            today = date.today()
            this_monday = today - timedelta(days=today.weekday())
            if val < this_monday and not self.instance.pk:
                raise forms.ValidationError(
                    "Cannot submit lesson plans for past weeks. "
                    "Please select the current week or a future week."
                )
        return val

    def clean(self):
        cleaned = super().clean()
        return cleaned


class LessonPlanReviewForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[
            (LessonPlanStatus.APPROVED, "Approve"),
            (LessonPlanStatus.REJECTED, "Reject"),
            (LessonPlanStatus.REVISION_REQUESTED, "Request Revision"),
        ]
    )
    feedback = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class ExamScoreFilterForm(forms.Form):
    from academics.models import Term
    term = forms.ModelChoiceField(queryset=Term.objects.filter(is_locked=False).order_by("-academic_year__name", "name"))
    class_name = forms.ChoiceField(choices=[])
    subject_name = forms.ChoiceField(choices=[])
    exam_type = forms.ChoiceField(choices=[])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        from academics.models import GradeClass, Subject, get_active_exam_types
        class_choices = [("", "Select Class")] + [(c.name, c.name) for c in GradeClass.objects.all().order_by("name")]
        subject_choices = [("", "Select Subject")] + [(s.name, s.name) for s in Subject.objects.filter(is_active=True).order_by("name")]
        exam_type_choices = [("", "Select Exam Type")] + get_active_exam_types()
        
        self.fields["class_name"].choices = class_choices
        self.fields["subject_name"].choices = subject_choices
        self.fields["exam_type"].choices = exam_type_choices

        if not self.data.get("term"):
            from academics.utils import get_current_term
            ct = get_current_term()
            if ct:
                self.fields["term"].initial = ct

        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs["class"] = "hf-select mt-1"
            elif isinstance(field.widget, forms.DateInput):
                field.widget.attrs["class"] = "hf-input hf2-date mt-1"
            else:
                field.widget.attrs["class"] = "hf-input mt-1"


class SubjectForm(forms.ModelForm):
    departments = forms.MultipleChoiceField(
        choices=Department.choices,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "dept-pill-grid"}),
        required=True,
        label="Departments",
    )

    class Meta:
        model = Subject
        fields = ["name", "code", "color", "departments", "classes", "is_active", "is_enrichment"]
        widgets = {
            "color": forms.TextInput(attrs={"type": "color", "style": "height: 42px; padding: 2px;"}),
            "classes": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.departments:
            self.initial["departments"] = self.instance.departments
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxSelectMultiple):
                field.widget.attrs["class"] = "hf-checkbox-group mt-1"
            elif isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs["class"] = "hf-select mt-1"
            elif not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "hf-input mt-1"

    def clean(self):
        cleaned_data = super().clean()
        departments = cleaned_data.get("departments", [])
        classes = cleaned_data.get("classes")

        # FRD OP 9.1: Subject must have at least one grade level
        if not classes:
            raise forms.ValidationError("A subject must be assigned to at least one grade level.")

        if departments and classes:
            mismatched = [gc.name for gc in classes if gc.department not in departments]
            if mismatched:
                raise forms.ValidationError(
                    f"The selected class(es) — {', '.join(mismatched)} — belong to a different department "
                    f"than the selected subject departments."
                )
        return cleaned_data
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        depts = self.cleaned_data.get("departments") or []
        if depts is not None:
            instance.departments = depts
        # Auto-set singular department from departments list
        if depts and not instance.department:
            instance.department = depts[0]
        if commit:
            instance.save()
            self._save_m2m()
        return instance


class TermForm(forms.ModelForm):
    """FR-CAL-001: Super Admin configures academic year structure."""
    class Meta:
        model = Term
        fields = [
            "academic_year", "name", "start_date", "end_date",
            "midterm_exam_start_date", "midterm_exam_end_date",
            "endterm_exam_start_date", "endterm_exam_end_date",
            "quiz_start_date", "quiz_end_date",
            "midterm_grade_marking_days", "endterm_grade_marking_days",
            "quiz_grade_marking_days",
            "grading_deadline", "is_locked",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Term 1"}),
            "start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "end_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "midterm_exam_start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "midterm_exam_end_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "endterm_exam_start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "endterm_exam_end_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "quiz_start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "quiz_end_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "grading_deadline": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "academic_year": forms.Select(attrs={"class": "form-control"}),
            "is_locked": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

