from __future__ import annotations

from datetime import time

from django.core.exceptions import ValidationError
from django.db import models

from core.models import TimeStampedModel


class Weekday(models.TextChoices):
    MON = "mon", "Monday"
    TUE = "tue", "Tuesday"
    WED = "wed", "Wednesday"
    THU = "thu", "Thursday"
    FRI = "fri", "Friday"


class TimetableSlot(TimeStampedModel):
    """Phase 1: minimal timetable entry with hard double-booking protection."""

    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="timetable_slots", null=True)
    class_name = models.CharField(max_length=64, db_index=True)
    subject = models.ForeignKey("academics.Subject", on_delete=models.PROTECT, related_name="timetable_slots", null=True, blank=True)
    subject_name = models.CharField(max_length=80) # Keep for quick lookup or fallback

    teacher = models.ForeignKey("users.User", on_delete=models.PROTECT, related_name="timetable_slots")

    day_of_week = models.CharField(max_length=8, choices=Weekday.choices, db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()

    room = models.ForeignKey("academics.Room", on_delete=models.SET_NULL, null=True, blank=True, related_name="timetable_slots")
    is_published = models.BooleanField(default=False, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["term", "class_name", "day_of_week", "start_time"]),
            models.Index(fields=["term", "teacher", "day_of_week", "start_time"]),
        ]

    def clean(self):
        super().clean()
        if not self.class_name.strip():
            raise ValidationError("Class is required.")
        if not self.subject_name.strip():
            raise ValidationError("Subject is required.")
        if not self.teacher_id:
            raise ValidationError("Teacher is required.")
        if self.start_time >= self.end_time:
            raise ValidationError("End time must be after start time.")

        # Cross-department restriction: teacher can only teach classes in
        # their own department.
        if self.teacher_id:
            from hr.models import StaffProfile
            from academics.models import GradeClass

            # Get teacher's staff profile department
            staff = StaffProfile.objects.filter(user_id=self.teacher_id).first()
            class_dept = GradeClass.objects.filter(name=self.class_name.strip()).values_list("department", flat=True).first()

            if staff and class_dept and class_dept.lower() not in [d.lower() for d in (staff.departments or [])]:
                raise ValidationError(
                    f"{staff.full_name} is in the {staff.get_department_display()} department "
                    f"but {self.class_name} is a {class_dept} class. "
                    "Cross-department teaching is not allowed."
                )

            # Subject must belong to the same department as the class
            if self.subject_name.strip() and class_dept:
                from academics.models import Subject as AcadSubject
                subject_obj = AcadSubject.objects.filter(
                    name__iexact=self.subject_name.strip(), is_active=True
                ).first()
                if subject_obj and class_dept not in (subject_obj.departments or []):
                    raise ValidationError(
                        f"Subject '{self.subject_name}' is not offered in the "
                        f"{class_dept} department but {self.class_name} is a {class_dept} class."
                    )

        # Subject must be in teacher's class assignment (TeacherClassAssignment)
        if self.teacher_id and self.subject_name.strip() and self.term_id:
            from hr.models import TeacherClassAssignment as TCA
            tca = TCA.objects.filter(
                teacher__user_id=self.teacher_id,
                term_id=self.term_id,
                grade_class__name__iexact=self.class_name.strip(),
            ).first()

            if not tca:
                name = self.teacher.get_full_name() if self.teacher_id else "Teacher"
                raise ValidationError(
                    f"{name} is not assigned to {self.class_name}. "
                    "Assign them to this class first."
                )

            assigned = [s.lower().strip() for s in (tca.subjects_taught or [])]
            if self.subject_name.lower().strip() not in assigned:
                name = self.teacher.get_full_name() if self.teacher_id else "Teacher"
                raise ValidationError(
                    f"{name} is assigned to {self.class_name} but not to teach "
                    f"'{self.subject_name}'. "
                    f"Assigned subjects: {', '.join(tca.subjects_taught or [])}."
                )

        # Double-booking checks: time overlap in same day for teacher and class.
        qs = TimetableSlot.objects.filter(term=self.term, day_of_week=self.day_of_week)
        if self.pk:
            qs = qs.exclude(pk=self.pk)

        overlap = qs.filter(start_time__lt=self.end_time, end_time__gt=self.start_time)

        if overlap.filter(teacher_id=self.teacher_id).exists():
            raise ValidationError("This teacher is already booked during this time.")

        if overlap.filter(class_name__iexact=self.class_name.strip()).exists():
            raise ValidationError("This class is already booked during this time.")

    def save(self, *args, **kwargs):
        if self.subject and not self.subject_name:
            self.subject_name = self.subject.name
        elif self.subject_name and not self.subject:
            from academics.models import Subject as AcadSubject
            s = AcadSubject.objects.filter(name__iexact=self.subject_name).first()
            if s:
                self.subject = s
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.class_name} {self.day_of_week} {self.start_time}-{self.end_time} {self.subject_name}"


class EcdBreakConfig(TimeStampedModel):
    """FRD FR-TT-007: ECD class break time configuration"""
    class_name = models.CharField(max_length=64, unique=True)
    snack_break_start = models.TimeField()
    snack_break_end = models.TimeField()
    lunch_break_start = models.TimeField()
    lunch_break_end = models.TimeField()
    nap_time_start = models.TimeField(null=True, blank=True)
    nap_time_end = models.TimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Break Config for {self.class_name}"


