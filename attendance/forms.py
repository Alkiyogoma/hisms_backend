from __future__ import annotations

from datetime import date as date_type
from django import forms
from django.utils import timezone

from attendance.models import AttendanceStatus


class AttendanceFilterForm(forms.Form):
    date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    class_name = forms.ChoiceField(required=False, label="Select Class / Grade")

    def clean_date(self):
        """Custom date cleaning to handle edge cases"""
        date_value = self.cleaned_data.get('date')
        if date_value is None:
            return None
        
        # If it's already a date object, return it
        if isinstance(date_value, (date_type, timezone.datetime)):
            return date_value
            
        # If it's a string, try to parse it
        if isinstance(date_value, str):
            try:
                from django.utils.dateparse import parse_date
                parsed = parse_date(date_value)
                if parsed:
                    return parsed
            except (ValueError, TypeError):
                pass
                
        # If all else fails, return None (will use default in view)
        return None

    def __init__(self, *args, **kwargs):
        allowed_classes = kwargs.pop("allowed_classes", None)
        include_all_option = kwargs.pop("include_all_option", True)
        super().__init__(*args, **kwargs)
        from academics.models import GradeClass
        if allowed_classes is not None:
            classes = [(name, name) for name in sorted({(c or "").strip() for c in allowed_classes if (c or "").strip()})]
        else:
            classes = list(GradeClass.objects.all().order_by("name").values_list("name", "name"))

        choices = classes
        if include_all_option:
            choices = [("", "All Classes")] + classes
        elif not choices:
            choices = [("", "No Classes Assigned")]
        self.fields["class_name"].choices = choices
        
        # Apply specific classes based on widget type for consistent premium UI
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs["class"] = "hf-select mt-1"
            else:
                field.widget.attrs["class"] = "hf-input mt-1"


class AttendanceMarkForm(forms.Form):
    student_id = forms.IntegerField()
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    status = forms.ChoiceField(choices=AttendanceStatus.choices)
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs["class"] = "hf-select mt-1"
            else:
                field.widget.attrs["class"] = "hf-input mt-1"

    def clean_status(self):
        status = self.cleaned_data.get("status")
        student_id = self.cleaned_data.get("student_id")
        if status == "late" and student_id:
            from students.models import Student
            from attendance.services import is_ecd_student
            try:
                student = Student.objects.get(pk=student_id)
                if not is_ecd_student(student):
                    raise forms.ValidationError("Late status is only available for ECD classes.")
            except Student.DoesNotExist:
                pass
        return status


class AttendanceCorrectionForm(forms.Form):
    entry_id = forms.IntegerField()
    status = forms.ChoiceField(choices=AttendanceStatus.choices)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs["class"] = "hf-select mt-1"
            else:
                field.widget.attrs["class"] = "hf-input mt-1"

    def clean_status(self):
        status = self.cleaned_data.get("status")
        entry_id = self.cleaned_data.get("entry_id")
        if status == "late" and entry_id:
            from attendance.models import AttendanceEntry
            from attendance.services import is_ecd_student
            try:
                entry = AttendanceEntry.objects.select_related("student").get(pk=entry_id)
                if not is_ecd_student(entry.student):
                    raise forms.ValidationError("Late status is only available for ECD classes.")
            except AttendanceEntry.DoesNotExist:
                pass
        return status


