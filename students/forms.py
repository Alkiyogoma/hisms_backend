from django import forms
from .models import Student, StudentStatus
from academics.models import GradeClass

class StudentCreateForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = [
            "first_name",
            "last_name",
            "preferred_name",
            "date_of_birth",
            "gender",
            "class_name",
            "stream_name",
            "nationality",
            "religion",
            "blood_type",
            "allergies_medical",
            "photo",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "allergies_medical": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        class_choices = [("", "Select class")] + [
            (g.name, g.name) for g in GradeClass.objects.order_by("sort_order", "name")
        ]
        self.fields["class_name"] = forms.ChoiceField(choices=class_choices, label="Class*")
        
        # Add labels/help text to match admissions style
        self.fields["first_name"].label = "First Name*"
        self.fields["last_name"].label = "Last Name*"
        self.fields["date_of_birth"].required = True
        self.fields["date_of_birth"].label = "Date of Birth*"
        self.fields["gender"].required = True
        self.fields["gender"].label = "Gender*"
        self.fields["class_name"].help_text = "Primary academic group for this student."

        # Apply specific classes based on widget type for consistent premium UI
        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs["class"] = "hf2-select mt-1"
            elif isinstance(field.widget, forms.DateInput):
                field.widget.attrs["class"] = "hf2-input hf2-date mt-1"
            elif isinstance(field.widget, forms.FileInput):
                field.widget.attrs["class"] = "hf2-file-input mt-1"
            else:
                field.widget.attrs["class"] = "hf2-input mt-1"

    def clean_gender(self):
        gender = self.cleaned_data.get("gender")
        if not gender:
            raise forms.ValidationError("Gender is required.")
        return gender

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get("date_of_birth")
        if not dob:
            raise forms.ValidationError("Date of birth is required.")
        return dob


class StudentPhotoUploadForm(forms.ModelForm):
    """Form for uploading student photos"""
    class Meta:
        model = Student
        fields = ["photo"]
        widgets = {
            "photo": forms.FileInput(attrs={"accept": "image/*", "class": "hf2-file-input mt-1"}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["photo"].label = "Student Photo"
        self.fields["photo"].help_text = "Upload a clear photo of the student (JPG, PNG format)"
    
    def clean_photo(self):
        from django.core.exceptions import ValidationError as CoreValidationError
        from core.models import MediaSettings

        photo = self.cleaned_data.get("photo")
        if photo:
            # --- Extension + size validation via MediaSettings ---
            from academics.validators import validate_attachment_file
            try:
                validate_attachment_file(photo, area="student_photos")
            except CoreValidationError as e:
                msg = e.message if hasattr(e, 'message') else str(e)
                raise forms.ValidationError(msg)

            # --- Duplicate photo detection (compare hash with other students) ---
            from academics.validators import _compute_file_hash
            from students.models import Student
            photo.seek(0)
            new_hash = _compute_file_hash(photo)
            photo.seek(0)

            # Check all other students with photos
            other_students = Student.objects.filter(
                photo__isnull=False
            ).exclude(pk=self.instance.pk if self.instance and self.instance.pk else None)

            for s in other_students:
                try:
                    existing_hash = _compute_file_hash(s.photo.path)
                    if existing_hash == new_hash:
                        raise forms.ValidationError(
                            f"This photo is already used for {s.first_name} {s.last_name} "
                            f"({s.admission_no}). Please upload a different photo."
                        )
                except (AttributeError, ValueError, OSError):
                    continue

            # --- Image format & dimension check ---
            config = MediaSettings.get_for_area("student_photos")
            min_w = getattr(config, 'min_width', 0) or 0
            min_h = getattr(config, 'min_height', 0) or 0

            try:
                from PIL import Image
                photo.seek(0)
                img = Image.open(photo)
                img.verify()  # validate image integrity
                photo.seek(0)
                img = Image.open(photo)  # re-open after verify (closes fp)
                w, h = img.size

                if (min_w > 0 and w < min_w) or (min_h > 0 and h < min_h):
                    raise forms.ValidationError(
                        f"Your photo is {w}×{h} pixels. "
                        f"It must be at least {min_w}×{min_h} pixels. "
                        f"Please use a higher resolution image."
                    )
            except forms.ValidationError:
                raise  # re-raise our own validation errors
            except Exception:
                raise forms.ValidationError(
                    "We couldn't read this file as an image. "
                    "Please try a JPG, PNG, or WebP photo."
                )
            finally:
                photo.seek(0)  # reset so Django can save the file

        return photo
