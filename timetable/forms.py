from __future__ import annotations

from django import forms

from timetable.models import TimetableSlot


class TimetableSlotForm(forms.ModelForm):
    class_name = forms.ChoiceField(choices=[])
    subject_name = forms.ChoiceField(choices=[])

    class Meta:
        model = TimetableSlot
        fields = [
            "term",
            "class_name",
            "subject_name",
            "teacher",
            "day_of_week",
            "start_time",
            "end_time",
        ]
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, **kwargs):
        from academics.utils import get_current_term
        from academics.models import GradeClass, Subject
        from users.models import User, UserRole
        from hr.models import TeacherClassAssignment

        # Inject missing term + day_of_week from POST data before super().__init__
        # CreateView passes data as kwargs['data'] (a QueryDict); normal form use passes as args[0].
        from django.http import QueryDict

        _data = kwargs.get("data") or (args[0] if args and isinstance(args[0], (dict, QueryDict)) else None)
        if _data is not None:
            # QueryDict.get() returns single value; dict() on QueryDict yields lists.
            _get = _data.get
            current_term = get_current_term()
            if current_term and not _get("term"):
                if isinstance(_data, QueryDict):
                    _data = _data.copy()
                    _data["term"] = str(current_term.pk)
                else:
                    _data = dict(_data)
                    _data["term"] = str(current_term.pk)
            repeat_raw = (_get("repeat_days") or "").strip()
            days = [d.strip() for d in repeat_raw.split(",") if d.strip()] if repeat_raw else []
            if days and not _get("day_of_week"):
                if isinstance(_data, QueryDict):
                    _data = _data.copy()
                    _data["day_of_week"] = days[0]
                else:
                    _data = dict(_data)
                    _data["day_of_week"] = days[0]
            if kwargs.get("data") is not None:
                kwargs["data"] = _data
            elif args and isinstance(args[0], (dict, QueryDict)):
                args = (_data,) + args[1:]

        super().__init__(*args, **kwargs)

        # Ensure day_of_week is never required — clean() handles it from repeat_days
        self.fields["day_of_week"].required = False
        self.fields["term"].required = False

        # Populate dynamic choices
        classes = GradeClass.objects.all().order_by("name").values_list("name", "name")
        subjects = Subject.objects.filter(is_active=True).order_by("name").values_list("name", "name")
        self.fields["class_name"].choices = [("", "Select Class")] + list(classes)
        self.fields["subject_name"].choices = [("", "Select Subject")] + list(subjects)

        current_term = get_current_term()
        
        if current_term:
            self.fields["term"].initial = current_term
            self.fields["term"].queryset = self.fields["term"].queryset.filter(pk=current_term.pk)

        # Filter teachers based on selected class
        class_name = self.data.get("class_name", "").strip()

        teacher_ids = set()
        if current_term:
            tcas = TeacherClassAssignment.objects.filter(term=current_term)
            if class_name:
                tcas = tcas.filter(grade_class__name__iexact=class_name)
            teacher_ids = {tca.teacher.user_id for tca in tcas}

        # Always include the currently-submitted teacher so ModelChoiceField
        # validation passes — clean() handles the real assignment check.
        submitted_teacher = self.data.get("teacher", "").strip()
        if submitted_teacher:
            try:
                teacher_ids.add(int(submitted_teacher))
            except (ValueError, TypeError):
                pass
        if self.instance and self.instance.pk:
            teacher_ids.add(self.instance.teacher_id)

        self.fields["teacher"].queryset = User.objects.filter(
            id__in=teacher_ids,
            is_active=True,
        ).order_by("first_name", "last_name")

        # Also filter subject choices based on selected class + teacher
        self._filter_subjects_for_teacher()

        # Apply specific classes based on widget type for consistent premium UI
        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs["class"] = "hf-select mt-1"
            else:
                field.widget.attrs["class"] = "hf-input mt-1"

    def _filter_subjects_for_teacher(self):
        from academics.utils import get_current_term
        from hr.models import TeacherClassAssignment

        current_term = get_current_term()
        class_name = self.data.get("class_name", "").strip()
        teacher_id = self.data.get("teacher", "").strip()

        if not class_name or not current_term:
            return

        # Get subjects taught in this class
        tcas = TeacherClassAssignment.objects.filter(
            term=current_term,
            grade_class__name__iexact=class_name,
        )
        if teacher_id:
            tcas = tcas.filter(teacher__user_id=teacher_id)

        allowed_subjects = set()
        for tca in tcas:
            allowed_subjects.update(tca.subjects_taught or [])

        if allowed_subjects:
            current_val = self.data.get("subject_name", "").strip()
            self.fields["subject_name"].choices = [("", "Select Subject")] + [
                (s, s) for s in sorted(allowed_subjects)
            ]
            if current_val and current_val in allowed_subjects:
                self.fields["subject_name"].initial = current_val

    def clean(self):
        cleaned = super().clean()

        # Auto-set term from current term if not provided
        if not cleaned.get("term"):
            from academics.utils import get_current_term
            current_term = get_current_term()
            if current_term:
                cleaned["term"] = current_term
                self.instance.term = current_term

        # Map repeat_days → day_of_week for each selected day
        repeat_raw = self.data.get("repeat_days", "").strip()
        days_to_create = [d.strip() for d in repeat_raw.split(",") if d.strip()] if repeat_raw else []

        if not cleaned.get("day_of_week"):
            if days_to_create:
                cleaned["day_of_week"] = days_to_create[0]
                self.instance.day_of_week = days_to_create[0]
            else:
                self.add_error("day_of_week", "Please select at least one day.")
        if self.errors:
            return cleaned

        class_name = cleaned.get("class_name", "").strip()
        subject_name = cleaned.get("subject_name", "").strip()
        teacher = cleaned.get("teacher")
        term = cleaned.get("term")

        if not all([class_name, subject_name, teacher, term]):
            return cleaned

        from hr.models import TeacherClassAssignment
        tca = TeacherClassAssignment.objects.filter(
            teacher__user_id=teacher.pk,
            term=term,
            grade_class__name__iexact=class_name,
        ).first()

        if not tca:
            raise forms.ValidationError({
                "teacher": f"{teacher.get_full_name()} is not assigned to {class_name}. "
                           "Assign them to this class first.",
            })

        assigned = [s.lower().strip() for s in (tca.subjects_taught or [])]
        if subject_name.lower().strip() not in assigned:
            raise forms.ValidationError({
                "subject_name": (
                    f"{teacher.get_full_name()} is not assigned to teach "
                    f"'{subject_name}' in {class_name}. "
                    f"Assigned subjects: {', '.join(tca.subjects_taught or [])}."
                ),
            })

        # Double-booking check: warn during form validation so user sees it
        # before submit. Actual enforcement happens in model.clean() via full_clean().
        repeat_raw = self.data.get("repeat_days", "").strip()
        days_to_check = [d.strip() for d in repeat_raw.split(",") if d.strip()] if repeat_raw else []
        start_time = cleaned.get("start_time")
        end_time = cleaned.get("end_time")

        if days_to_check and start_time and end_time and teacher:
            from timetable.models import TimetableSlot

            conflict_msgs = []
            term = cleaned.get("term")
            for day in days_to_check:
                overlap = TimetableSlot.objects.filter(
                    term=term,
                    day_of_week=day,
                    start_time__lt=end_time,
                    end_time__gt=start_time,
                )
                teacher_clash = overlap.filter(teacher_id=teacher.pk).first()
                class_clash = overlap.filter(class_name__iexact=class_name).first()

                if teacher_clash:
                    conflict_msgs.append(
                        f"{day.title()}: teacher already booked "
                        f"({teacher_clash.class_name} {teacher_clash.subject_name} "
                        f"{teacher_clash.start_time.strftime('%H:%M')}-{teacher_clash.end_time.strftime('%H:%M')})"
                    )
                elif class_clash:
                    conflict_msgs.append(
                        f"{day.title()}: class already booked "
                        f"({class_clash.teacher.get_full_name()} {class_clash.subject_name} "
                        f"{class_clash.start_time.strftime('%H:%M')}-{class_clash.end_time.strftime('%H:%M')})"
                    )

            if conflict_msgs:
                raise forms.ValidationError("; ".join(conflict_msgs))

        return cleaned

