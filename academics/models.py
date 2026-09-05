from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from core.models import TimeStampedModel
from academics.validators import validate_attachment_file


class RecalcStatus(models.TextChoices):
    """Persisted status for async recalculation of progression cases (Item 9)."""
    NOT_STARTED = "not_started", "Not Started"
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class ProgressionStatus(models.TextChoices):
    """Status transitions: calculated -> pending_hod_review -> pending_hos_decision -> finalized"""
    CALCULATED = "calculated", "Calculated"
    PENDING_HOD_REVIEW = "pending_hod_review", "Pending HOD Review"
    PENDING_HOS_DECISION = "pending_hos_decision", "Pending HOS Decision"
    FINALIZED = "finalized", "Finalized"


class ProgressionOutcome(models.TextChoices):
    PROMOTE = "promote", "Promote"
    RETAIN = "retain", "Retain"
    PROMOTE_WITH_CONDITIONS = "promote_with_conditions", "Promote with Conditions"
    GRADUATE = "graduate", "Graduate"


class CalculationBasis(models.TextChoices):
    COMPLETE = "complete", "Complete — all assessments present"
    ZERO_SCORED = "zero_scored", "Zero-scored — one or more absences recorded as zero"
    REDISTRIBUTED = "redistributed", "Redistributed — excused absence, remaining components scaled"
    INCOMPLETE = "incomplete", "Incomplete — missing grade data, cannot calculate"


class AcademicYear(TimeStampedModel):
    name = models.CharField(max_length=20, unique=True)
    is_current = models.BooleanField(default=False)
    is_published = models.BooleanField(
        default=False,
        help_text="Published years are locked for Admin-level edits. Only Super Admin can unpublish.",
    )
    number_of_terms = models.PositiveSmallIntegerField(
        default=3,
        help_text="Number of terms in this academic year (e.g. 3 or 4).",
    )
    start_date = models.DateField(
        null=True, blank=True,
        help_text="First day of this academic year (calendar date).",
    )
    end_date = models.DateField(
        null=True, blank=True,
        help_text="Last day of this academic year (calendar date).",
    )

    def __str__(self) -> str:
        return self.name

    def clean(self):
        from django.utils import timezone
        today = timezone.now().date()

        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError("Start date must be before end date.")
        # Cannot set is_current on a year that has already ended
        if self.is_current and self.end_date and self.end_date < today:
            raise ValidationError(
                f"Cannot set '{self.name}' as current — it ended on {self.end_date}. "
                f"Choose a current or future academic year."
            )
        # Cannot publish a year that has already ended
        if self.is_published and self.end_date and self.end_date < today:
            raise ValidationError(
                f"Cannot publish '{self.name}' — it ended on {self.end_date}. "
                f"Past academic years cannot be published."
            )
        if self.start_date and self.end_date:
            overlaps = AcademicYear.objects.filter(
                start_date__lt=self.end_date,
                end_date__gt=self.start_date,
            )
            if self.pk:
                overlaps = overlaps.exclude(pk=self.pk)
            if overlaps.exists():
                raise ValidationError(
                    f"Date range overlaps with '{overlaps.first().name}' "
                    f"({overlaps.first().start_date} to {overlaps.first().end_date}). "
                    f"Adjust the dates or deactivate the conflicting year."
                )
        if self.number_of_terms < 3:
            raise ValidationError("An academic year must have at least 3 terms.")
        if self.is_published and self.pk:
            existing = AcademicYear.objects.filter(pk=self.pk).first()
            if existing and not existing.is_published:
                # FR-CAL-002: Publishing — all terms must have start and end dates.
                terms = self.terms.all()
                if not terms.exists():
                    raise ValidationError(
                        "Cannot publish an academic year with no terms defined. "
                        "Create all terms with start and end dates first."
                    )
                missing = [t.name for t in terms if not t.start_date or not t.end_date]
                if missing:
                    raise ValidationError(
                        f"Cannot publish: the following terms are missing start or end dates: "
                        f"{', '.join(missing)}. All terms must have complete dates before publishing."
                    )
                # FR-CAL-002: Only one academic year may be published at a time.
                other_published = AcademicYear.objects.filter(
                    is_published=True
                ).exclude(pk=self.pk)
                if other_published.exists():
                    raise ValidationError(
                        f"Cannot publish '{self.name}': '{other_published.first().name}' is already "
                        f"published. Unpublish the existing year first."
                    )
            elif existing and existing.is_published:
                if self.start_date != existing.start_date or self.end_date != existing.end_date:
                    raise ValidationError("Cannot change dates on a published academic year. Unpublish first.")
                if self.number_of_terms != existing.number_of_terms:
                    raise ValidationError("Cannot change term count on a published academic year. Unpublish first.")

    def save(self, *args, **kwargs):
        if self.is_current:
            AcademicYear.objects.filter(is_current=True).exclude(pk=self.pk).update(is_current=False)
        if self.is_published:
            AcademicYear.objects.filter(is_published=True).exclude(pk=self.pk).update(is_published=False)
        self.full_clean()
        super().save(*args, **kwargs)


class Department(models.TextChoices):
    ECD = "ECD", "ECD"
    PRIMARY = "PRIMARY", "Primary"
    LOWER_SECONDARY = "LOWER_SECONDARY", "Lower Secondary"
    ADMINISTRATION = "ADMINISTRATION", "Administration"


class Room(TimeStampedModel):
    name = models.CharField(max_length=64, unique=True)
    building = models.CharField(max_length=100, blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name

    class Meta:
        ordering = ["name"]



class GradeClass(TimeStampedModel):
    name = models.CharField(max_length=64, unique=True)
    department = models.CharField(max_length=32, choices=Department.choices)
    max_capacity = models.PositiveIntegerField(default=25)
    sort_order = models.PositiveIntegerField(default=0, help_text="Numeric order for grade progression/promotion")
    is_exit_grade = models.BooleanField(default=False, help_text="True for the highest grade (e.g. Grade 9)")

    # Allowed class names — FRD 2.3: exactly these classes, no parallel streams
    ALLOWED_CLASS_NAMES = {
        Department.ECD: {"Pre-K", "KG", "Pre-School", "ABC"},
        Department.PRIMARY: {f"Grade {i}" for i in range(1, 7)},
        Department.LOWER_SECONDARY: {f"Grade {i}" for i in range(7, 10)},
    }
    ALL_ALLOWED = set().union(*ALLOWED_CLASS_NAMES.values())

    def clean(self):
        super().clean()
        self.name = self.name.strip()
        self.department = self.department.strip().upper()
        if self.max_capacity is not None and self.max_capacity <= 0:
            raise ValidationError(
                "Class capacity must be greater than zero. "
                "Set a positive capacity or remove the class."
            )
        # Auto-map to canonical name and department for known classes
        _name_map = {n.lower(): n for n in self.ALL_ALLOWED}
        canonical = _name_map.get(self.name.lower())
        if canonical:
            self.name = canonical
            for dept, names in self.ALLOWED_CLASS_NAMES.items():
                if self.name in names:
                    self.department = dept
                    break

        # FRD OP 7.1: Block parallel streams (e.g. Grade 3A, Grade 3B)
        import re
        if re.match(r'^Grade\s+\d+[A-Za-z]$', self.name, re.IGNORECASE):
            raise ValidationError(
                f"Parallel streams are not allowed. '{self.name}' cannot be created. "
                f"Use '{re.sub(r'[A-Za-z]+$', '', self.name).strip()}' instead."
            )

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Class"
        verbose_name_plural = "Classes"


class ClassCapacity(TimeStampedModel):
    """
    FRD OP 8.1: Class capacity per academic year.
    Allows different capacity values for the same class across years.
    Falls back to GradeClass.max_capacity if no record exists for the year.
    """
    grade_class = models.ForeignKey(GradeClass, on_delete=models.CASCADE, related_name="yearly_capacities")
    academic_year = models.ForeignKey("AcademicYear", on_delete=models.CASCADE, related_name="class_capacities")
    max_capacity = models.PositiveIntegerField(default=25)

    class Meta:
        unique_together = ("grade_class", "academic_year")
        verbose_name = "Class Capacity"
        verbose_name_plural = "Class Capacities"

    def __str__(self):
        return f"{self.grade_class.name} ({self.academic_year.name}): {self.max_capacity}"


def get_class_capacity(grade_class, academic_year=None):
    """
    FRD OP 8.1: Get capacity for a class in a given academic year.
    If academic_year is None, uses the current academic year.
    Falls back to GradeClass.max_capacity if no ClassCapacity record exists.
    """
    if academic_year is None:
        academic_year = AcademicYear.objects.filter(is_current=True).first()
    if academic_year and isinstance(grade_class, GradeClass):
        cc = ClassCapacity.objects.filter(grade_class=grade_class, academic_year=academic_year).first()
        if cc:
            return cc.max_capacity
    if isinstance(grade_class, GradeClass):
        return grade_class.max_capacity
    return 25


class Subject(TimeStampedModel):
    name = models.CharField(max_length=80, unique=True)
    code = models.CharField(max_length=10, unique=True, null=True, blank=True, help_text="e.g. MATH, ENG")
    color = models.CharField(max_length=20, default="#023AA5", help_text="Hex color for timetable display")
    department = models.CharField(max_length=32, choices=Department.choices)
    departments = models.JSONField(default=list, blank=True, help_text="List of departments this subject belongs to")
    is_active = models.BooleanField(default=True)
    is_enrichment = models.BooleanField(default=False, help_text="Enrichment subjects (Bible, ICT, PE, etc.) use letter grades and one grade per term")
    classes = models.ManyToManyField(GradeClass, related_name="subjects", blank=True)

    def __str__(self) -> str:
        return self.name

    def clean(self):
        super().clean()
        if self.pk and self.classes.exists():
            subject_depts = set(self.departments or [])
            if not subject_depts and self.department:
                subject_depts = {self.department}
            for gc in self.classes.all():
                if gc.department not in subject_depts:
                    raise ValidationError(
                        f'Class "{gc.name}" (department: {gc.get_department_display()}) '
                        f'is not compatible with subject "{self.name}" '
                        f'(departments: {", ".join(subject_depts)}). '
                        f'Remove the class or add its department to this subject.'
                    )

    def get_departments_display(self):
        labels = dict(Department.choices)
        return ", ".join(labels.get(d, d) for d in (self.departments or []))

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

class WeekDay(models.TextChoices):
    MON = "monday", "Monday"
    TUE = "tuesday", "Tuesday"
    WED = "wednesday", "Wednesday"
    THU = "thursday", "Thursday"
    FRI = "friday", "Friday"

class TimetableEntry(TimeStampedModel):
    term = models.ForeignKey(
        "academics.Term",
        on_delete=models.PROTECT,
        related_name="timetable_entries",
        null=True,
        blank=True,
        help_text="Term this timetable belongs to — FR-TT-002",
    )
    class_name = models.CharField(max_length=64, db_index=True)
    day_of_week = models.CharField(max_length=10, choices=WeekDay.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    teacher = models.ForeignKey("users.User", on_delete=models.SET_NULL, null=True, blank=True)
    room = models.CharField(max_length=40, blank=True)

    class Meta:
        unique_together = ("term", "class_name", "day_of_week", "start_time")
        verbose_name = "Timetable Entry"
        verbose_name_plural = "Timetable Entries"

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError("end_time must be after start_time.")
        # FR-TT-003: Prevent teacher double-booking
        if self.teacher_id and self.term_id and self.day_of_week and self.start_time:
            conflicts = TimetableEntry.objects.filter(
                term=self.term,
                day_of_week=self.day_of_week,
                teacher=self.teacher,
                start_time=self.start_time,
            ).exclude(pk=self.pk)
            if conflicts.exists():
                conflict = conflicts.first()
                raise ValidationError(
                    f"Teacher is already assigned to {conflict.class_name} "
                    f"on {self.day_of_week} at {self.start_time}."
                )
        # Room double-booking: same room cannot host two classes at the same time
        if self.room and self.room.strip() and self.term_id and self.day_of_week and self.start_time:
            room_conflicts = TimetableEntry.objects.filter(
                term=self.term,
                day_of_week=self.day_of_week,
                room=self.room,
                start_time=self.start_time,
            ).exclude(pk=self.pk)
            if room_conflicts.exists():
                conflict = room_conflicts.first()
                raise ValidationError(
                    f'Room "{self.room}" is already assigned to {conflict.class_name} '
                    f'({conflict.subject}) on {self.day_of_week} at {self.start_time}.'
                )

    def __str__(self) -> str:
        return f"{self.class_name} | {self.day_of_week} | {self.start_time}"

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

class Term(TimeStampedModel):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="terms")
    name = models.CharField(max_length=40)
    is_locked = models.BooleanField(default=False)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    grading_deadline = models.DateField(null=True, blank=True, help_text="Deadline for teachers to complete score entry.")
    midterm_exam_start_date = models.DateField(null=True, blank=True, help_text="Start of mid-term examination period.")
    midterm_exam_end_date = models.DateField(null=True, blank=True, help_text="End of mid-term examination period.")
    endterm_exam_start_date = models.DateField(null=True, blank=True, help_text="Start of end-of-term examination period.")
    endterm_exam_end_date = models.DateField(null=True, blank=True, help_text="End of end-of-term examination period.")
    quiz_start_date = models.DateField(null=True, blank=True, help_text="Start of quiz period for this term.")
    quiz_end_date = models.DateField(null=True, blank=True, help_text="End of quiz period for this term.")
    midterm_grade_marking_days = models.PositiveSmallIntegerField(
        default=10,
        help_text="Working days after mid-term exam for teachers to enter grades (excludes weekends).",
    )
    endterm_grade_marking_days = models.PositiveSmallIntegerField(
        default=10,
        help_text="Working days after end-term exam for teachers to enter grades (excludes weekends).",
    )
    quiz_grade_marking_days = models.PositiveSmallIntegerField(
        default=5,
        help_text="Working days after quiz period for teachers to enter grades (excludes weekends).",
    )
    # Deprecated: kept temporarily for backward compat; use endterm fields above.
    exam_start_date = models.DateField(null=True, blank=True, help_text="DEPRECATED — use endterm_exam_start_date.")
    exam_end_date = models.DateField(null=True, blank=True, help_text="DEPRECATED — use endterm_exam_end_date.")

    class Meta:
        unique_together = ("academic_year", "name")

    def clean(self):
        super().clean()
        if self.pk:
            prior = Term.objects.filter(pk=self.pk).first()
            if prior and prior.is_locked and not self.is_locked:
                raise ValidationError("Locked terms cannot be unlocked.")
        if self.start_date and self.end_date:
            if self.start_date > self.end_date:
                raise ValidationError("Term start date must be before end date.")
            year = self.academic_year
            if year.start_date and self.start_date < year.start_date:
                raise ValidationError(
                    f"Term start date ({self.start_date}) is before the academic year "
                    f"start date ({year.start_date})."
                )
            if year.end_date and self.end_date > year.end_date:
                raise ValidationError(
                    f"Term end date ({self.end_date}) is after the academic year "
                    f"end date ({year.end_date})."
                )
            siblings = Term.objects.filter(academic_year=year).exclude(pk=self.pk)
            for sibling in siblings:
                if sibling.start_date and sibling.end_date:
                    if self.start_date < sibling.end_date and self.end_date > sibling.start_date:
                        raise ValidationError(
                            f"Term date range overlaps with '{sibling.name}' "
                            f"({sibling.start_date} to {sibling.end_date})."
                        )
        # Exam date validation
        for label, start, end in [
            ("Mid-Term", self.midterm_exam_start_date, self.midterm_exam_end_date),
            ("End-Term", self.endterm_exam_start_date, self.endterm_exam_end_date),
            ("Quiz", self.quiz_start_date, self.quiz_end_date),
        ]:
            if start and end and start > end:
                raise ValidationError(f"{label} exam start date must be before end date.")
            if start and self.start_date and start < self.start_date:
                raise ValidationError(f"{label} exam start date is before term start.")
            if end and self.end_date and end > self.end_date:
                raise ValidationError(f"{label} exam end date is after term end.")

        # Grade marking window validation
        if self.midterm_grade_marking_days and self.midterm_grade_marking_days > 60:
            raise ValidationError("Mid-term grade marking days cannot exceed 60.")
        if self.endterm_grade_marking_days and self.endterm_grade_marking_days > 60:
            raise ValidationError("End-term grade marking days cannot exceed 60.")
        if self.quiz_grade_marking_days and self.quiz_grade_marking_days > 60:
            raise ValidationError("Quiz grade marking days cannot exceed 60.")

        # Grading deadline must be on or after the latest marking window end date
        if self.grading_deadline:
            marking_dates = [
                d for d in [
                    self.compute_grade_marking_end_date("quiz"),
                    self.compute_grade_marking_end_date("midterm"),
                    self.compute_grade_marking_end_date("endterm"),
                ] if d is not None
            ]
            if marking_dates:
                latest_marking = max(marking_dates)
                if self.grading_deadline < latest_marking:
                    raise ValidationError(
                        f"Grading deadline ({self.grading_deadline}) cannot be before the "
                        f"latest grade marking window ends ({latest_marking}). "
                        f"Adjust the marking days or set a later deadline."
                    )

    def compute_grade_marking_end_date(self, exam_type):
        """Compute grade marking end date by adding N working days (Mon-Fri) to exam end date.

        Args:
            exam_type: 'midterm', 'endterm', or 'quiz'
        Returns:
            date or None if exam end date is not set.
        """
        from datetime import timedelta
        if exam_type == "midterm":
            exam_end = self.midterm_exam_end_date
            days = self.midterm_grade_marking_days or 10
        elif exam_type == "endterm":
            exam_end = self.endterm_exam_end_date
            days = self.endterm_grade_marking_days or 10
        else:
            exam_end = self.quiz_end_date
            days = self.quiz_grade_marking_days or 5
        if not exam_end:
            return None
        remaining = days
        current = exam_end
        while remaining > 0:
            current += timedelta(days=1)
            if current.weekday() < 5:
                remaining -= 1
        return current

    @property
    def midterm_grade_marking_end_date(self):
        return self.compute_grade_marking_end_date("midterm")

    @property
    def endterm_grade_marking_end_date(self):
        return self.compute_grade_marking_end_date("endterm")

    @property
    def quiz_grade_marking_end_date(self):
        return self.compute_grade_marking_end_date("quiz")

    @classmethod
    def get_current(cls):
        """FR-CAL-003: Auto-determine the current term from configured dates.
        Resolution order:
          1. A term whose date range includes today (is in progress).
          2. The most recently ended unlocked term (covers gaps between terms).
          3. None if no unlocked term exists.
        """
        from django.utils import timezone
        today = timezone.now().date()

        # 1 — In-progress term
        term = cls.objects.filter(
            start_date__lte=today,
            end_date__gte=today,
            is_locked=False,
        ).order_by('start_date').first()
        if term is not None:
            return term

        # 2 — Most recently ended term (bridges gaps between terms)
        term = cls.objects.filter(
            end_date__lte=today,
            is_locked=False,
        ).order_by('-end_date').first()
        if term is not None:
            return term

        return None

    def __str__(self) -> str:
        return f"{self.academic_year.name} - {self.name}"


class LessonPlanStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    REVISION_REQUESTED = "revision_requested", "Revision Requested"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    MISSING = "missing", "Missing"


class LessonPlan(TimeStampedModel):
    teacher = models.ForeignKey("users.User", on_delete=models.PROTECT, related_name="lesson_plans")
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="lesson_plans")
    class_name = models.CharField(max_length=64, db_index=True)
    subject_name = models.CharField(max_length=80, db_index=True)
    day_of_week = models.CharField(max_length=8, blank=True, default="", help_text="Specific day (mon/tue/wed/thu/fri) for per-slot plans")
    week_start_date = models.DateField(db_index=True)
    lesson_title = models.CharField(max_length=150, blank=True, default="")
    objectives = models.TextField(blank=True, default="")
    activities = models.TextField(blank=True, default="")
    assessment_strategy = models.TextField(blank=True, default="")
    resources = models.TextField(blank=True, default="")
    status = models.CharField(max_length=30, choices=LessonPlanStatus.choices, default=LessonPlanStatus.DRAFT, db_index=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        "users.User", on_delete=models.PROTECT, null=True, blank=True, related_name="lesson_plan_reviews"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewer_feedback = models.TextField(blank=True)

    class Meta:
        permissions = [
            ("can_review_lessonplan", "Can review and approve lesson plans"),
            ("view_all_lessonplans", "Can view and create lesson plans for all teachers"),
        ]
        indexes = [
            models.Index(fields=["status", "week_start_date"]),
            models.Index(fields=["teacher", "status"]),
        ]

    def clean(self):
        super().clean()
        if not self.class_name.strip() or not self.subject_name.strip():
            raise ValidationError("Class and subject are required.")
        if self.status in {LessonPlanStatus.APPROVED, LessonPlanStatus.REJECTED, LessonPlanStatus.REVISION_REQUESTED} and not self.reviewed_by_id:
            raise ValidationError("reviewed_by is required for approved/rejected/revision plans.")
        if self.status in {LessonPlanStatus.REJECTED, LessonPlanStatus.REVISION_REQUESTED} and not (self.reviewer_feedback or "").strip():
            raise ValidationError("Feedback is required when rejecting or requesting revision.")

    def __str__(self) -> str:
        return f"{self.week_start_date} {self.class_name} {self.subject_name} ({self.status})"


class LessonPlanAttachment(TimeStampedModel):
    lesson_plan = models.ForeignKey(LessonPlan, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(
        upload_to="lesson_plans/attachments/",
        max_length=500,
        validators=[validate_attachment_file],
    )
    filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey("users.User", on_delete=models.PROTECT)

    def __str__(self) -> str:
        return self.filename


# --------------------------------------------------------------------------- #
# Academic Assessments — FRD Section 9 (FR-ACAD-001…013)                       #
# --------------------------------------------------------------------------- #

class ExamTypeConfiguration(TimeStampedModel):
    """
    Dynamic exam type configuration for Primary & Lower Secondary.
    Configurable by Super Admin - replaces hardcoded exam types.
    """
    name = models.CharField(max_length=50, unique=True)
    code = models.CharField(max_length=20, unique=True, help_text="Unique code for internal use")
    description = models.CharField(max_length=100, blank=True)
    weight_percentage = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        help_text="Weight percentage for overall calculation (e.g., 20.00 for 20%)"
    )
    max_score = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=100,
        help_text="Maximum score for this exam type"
    )
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0, help_text="Order in forms and reports")
    
    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = "Exam Type Configuration"
        verbose_name_plural = "Exam Type Configurations"

    def clean(self):
        super().clean()
        if self.weight_percentage < 0 or self.weight_percentage > 100:
            raise ValidationError("Weight percentage must be between 0 and 100.")
        if self.max_score <= 0:
            raise ValidationError("Max score must be greater than 0.")

    def __str__(self):
        return f"{self.name} ({self.weight_percentage}%)"


class ECDTemplateConfiguration(TimeStampedModel):
    """
    Dynamic ECD template configuration for different ECD class types.
    Configurable by Super Admin - replaces hardcoded template types.
    """
    name = models.CharField(max_length=50, unique=True)
    code = models.CharField(max_length=20, unique=True, help_text="Unique code for internal use")
    description = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0, help_text="Order in forms and reports")
    
    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = "ECD Template Configuration"
        verbose_name_plural = "ECD Template Configurations"

    def __str__(self):
        return self.name


def get_active_ecd_templates():
    """Get active ECD templates as choices for forms."""
    return [
        (template.code, template.name)
        for template in ECDTemplateConfiguration.objects.filter(is_active=True).order_by('display_order', 'name')
    ]


# Legacy compatibility - keep for existing code that might reference it
class ExamType(models.TextChoices):
    QUIZ = "quiz", "Quiz"
    MID_TERM = "mid_term", "Mid-term"
    END_OF_TERM = "end_of_term", "End of Term"


def get_exam_weights():
    """Get current exam weights from database configuration."""
    weights = {}
    for exam_type in ExamTypeConfiguration.objects.filter(is_active=True):
        weights[exam_type.code] = float(exam_type.weight_percentage)
    return weights


def get_active_exam_types():
    """Get active exam types as choices for forms."""
    return [
        (exam_type.code, f"{exam_type.name} ({exam_type.weight_percentage}%)")
        for exam_type in ExamTypeConfiguration.objects.filter(is_active=True).order_by('display_order', 'name')
    ]


# Legacy compatibility
EXAM_WEIGHTS = {
    ExamType.QUIZ: 20,
    ExamType.MID_TERM: 30,
    ExamType.END_OF_TERM: 50,
}


class ScoreStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted to HOD"
    APPROVED = "approved", "HOD Approved"
    RETURNED = "returned", "Returned for Correction"

class ExamScore(TimeStampedModel):
    """
    Individual exam score for a student in a subject.
    Weighted average auto-calculated from configured exam types per term.
    FR-ACAD-001 / FR-ACAD-008
    """
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="exam_scores")
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="exam_scores")
    subject_name = models.CharField(max_length=80, db_index=True)
    exam_type = models.CharField(max_length=20, db_index=True, help_text="Exam type code from ExamTypeConfiguration")
    score = models.DecimalField(max_digits=5, decimal_places=2)
    max_score = models.DecimalField(max_digits=5, decimal_places=2, default=100)
    
    # Link to configuration for validation and weight calculation
    exam_type_config = models.ForeignKey(
        ExamTypeConfiguration, 
        on_delete=models.PROTECT, 
        null=True, 
        blank=True,
        related_name="exam_scores",
        help_text="Reference to exam type configuration"
    )

    # Entry control
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="scores_entered"
    )
    status = models.CharField(
        max_length=20, choices=ScoreStatus.choices, default=ScoreStatus.DRAFT, db_index=True
    )
    is_locked = models.BooleanField(default=False)
    
    # HOD Approval
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="scores_approved"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    hod_feedback = models.TextField(blank=True)

    # Correction tracking
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="scores_corrected"
    )
    correction_reason = models.TextField(blank=True)
    previous_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Original score before correction — retained for audit trail (FR-ACAD-008).",
    )

    class Meta:
        unique_together = ("student", "term", "subject_name", "exam_type")
        indexes = [
            models.Index(fields=["student", "term"]),
            models.Index(fields=["term", "subject_name"]),
        ]

    def clean(self):
        super().clean()
        # FR-ACAD-001: integer-only enforcement at model level
        if self.score != self.score.to_integral_value():
            raise ValidationError(f"Score must be a whole number (integer) between 0 and {self.max_score}.")
        if self.score < 0 or self.score > self.max_score:
            raise ValidationError(f"Score must be a whole number (integer) between 0 and {self.max_score}.")
        
        # Set exam_type_config based on exam_type code
        if self.exam_type and not self.exam_type_config:
            try:
                self.exam_type_config = ExamTypeConfiguration.objects.get(code=self.exam_type, is_active=True)
                # Update max_score if different from configuration
                if self.exam_type_config.max_score != self.max_score:
                    self.max_score = self.exam_type_config.max_score
            except ExamTypeConfiguration.DoesNotExist:
                raise ValidationError(f"Invalid exam type '{self.exam_type}' or exam type is not active.")
        
        if self.status in [ScoreStatus.SUBMITTED, ScoreStatus.APPROVED]:
            self.is_locked = True
        elif self.status in [ScoreStatus.DRAFT, ScoreStatus.RETURNED]:
            self.is_locked = False

        if self.is_locked and not self.pk:
            raise ValidationError("Cannot create a score in locked state.")

    def __str__(self) -> str:
        return f"{self.student_id} | {self.subject_name} | {self.exam_type} | {self.score}"


class ReportCardStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING_SIGN_OFF = "pending_sign_off", "Pending HOS sign-off"
    PUBLISHED = "published", "Published to parents"


class ReportCard(TimeStampedModel):
    """
    Per-student report card for a term.
    Published only after HOS sign-off — FR-ACAD-016/017.
    """
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="report_cards")
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="report_cards")
    status = models.CharField(
        max_length=20, choices=ReportCardStatus.choices, default=ReportCardStatus.DRAFT, db_index=True
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reports_generated"
    )
    signed_off_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="reports_signed"
    )
    signed_off_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    # ECD HOD approval (before HOS sign-off)
    ecd_hod_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="ecd_reports_approved"
    )
    ecd_hod_approved_at = models.DateTimeField(null=True, blank=True)

    # Overall computed fields (populated on generation)
    overall_average = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    teacher_comments = models.TextField(blank=True)
    hos_comments = models.TextField(blank=True)

    # General traits (Work Habits, Personal Traits, Social Traits) - E/G/S/N grades
    general_traits = models.JSONField(default=dict, blank=True,
        help_text='JSON dict of trait scores, e.g. {"wh_follows_directions": "E", "pt_honest": "G", ...}')

    # FR-ATT-013: Attendance summary embedded in term report
    attendance_days_present = models.PositiveIntegerField(default=0)
    attendance_days_absent = models.PositiveIntegerField(default=0)
    attendance_days_late = models.PositiveIntegerField(default=0)
    attendance_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Attendance rate as percentage for the term")

    # Comments workflow
    comments_submitted = models.BooleanField(default=False, help_text="Teacher has submitted comments for HOD/HOS review")

    # ECD specifics
    is_ecd_report = models.BooleanField(default=False)
    ecd_template_type = models.CharField(max_length=20, blank=True) # pre_k, kindergarten, pre_school, abc
    
    # ECD remarks override (teacher-set, overrides auto-computed label)
    ecd_remarks = models.CharField(max_length=50, blank=True, help_text="Override auto-computed ECD remarks label (Outstanding/Good/Satisfactory/Needs Improvement)")

    # Parent signature
    parent_signed = models.BooleanField(default=False, help_text="Whether parent/guardian has signed the report card")
    parent_signed_at = models.DateTimeField(null=True, blank=True, help_text="When parent/guardian signed")
    parent_signature_name = models.CharField(max_length=255, blank=True, help_text="Name of parent/guardian who signed")

    class Meta:
        unique_together = ("student", "term")
        ordering = ["-term__academic_year__name", "term__name", "student__last_name"]

    def clean(self):
        super().clean()
        if self.status == ReportCardStatus.PUBLISHED and not self.signed_off_by_id:
            raise ValidationError("Report cards must be signed off by HOS before publishing.")

    def compute_weighted_average(self):
        """FR-ACAD-003: Calculate weighted average from ExamScores for this student+term.
        Uses configured ExamTypeConfiguration weights (default: Quiz 20%, Mid-Term 30%, End-Term 50%).
        Delegates to compute_grade_with_gaps() for gap detection & redistribution.
        """
        from academics.grading_utils import compute_grade_with_gaps

        scores = ExamScore.objects.filter(
            student=self.student,
            term=self.term,
            status=ScoreStatus.APPROVED,
        )
        if not scores.exists():
            return None

        # Group scores by subject
        subjects = {}
        for score in scores:
            if score.subject_name not in subjects:
                subjects[score.subject_name] = {}
            subjects[score.subject_name][score.exam_type] = float(score.score)

        weights = {}
        for et in ExamTypeConfiguration.objects.filter(is_active=True):
            weights[et.code] = float(et.weight_percentage)
        if not weights:
            weights = {"quiz": 20, "mid_term": 30, "end_of_term": 50}

        subject_averages = []
        for subject_name, exams in subjects.items():
            result = compute_grade_with_gaps(
                scores=exams,
                weights=weights,
            )
            if result["average"] is not None:
                subject_averages.append(result["average"])

        if not subject_averages:
            return None
        return round(sum(subject_averages) / len(subject_averages), 2)

    def populate_attendance_summary(self, start_date=None, end_date=None):
        """FR-ATT-013: Populate attendance summary fields from AttendanceEntry records."""
        from attendance.models import AttendanceEntry, AttendanceStatus
        from django.utils import timezone

        if start_date is None and self.term.start_date:
            start_date = self.term.start_date
        if end_date is None and self.term.end_date:
            end_date = self.term.end_date
        if start_date is None:
            start_date = timezone.now().date().replace(month=1, day=1)
        if end_date is None:
            end_date = timezone.now().date()

        qs = AttendanceEntry.objects.filter(student=self.student, date__range=[start_date, end_date])
        self.attendance_days_present = qs.filter(status=AttendanceStatus.PRESENT).count()
        self.attendance_days_late = qs.filter(status=AttendanceStatus.LATE).count()
        self.attendance_days_absent = qs.filter(status=AttendanceStatus.ABSENT).count()
        total = self.attendance_days_present + self.attendance_days_late + self.attendance_days_absent
        if total > 0:
            self.attendance_rate = round(((self.attendance_days_present + self.attendance_days_late) / total) * 100, 2)
        self.save(update_fields=[
            'attendance_days_present', 'attendance_days_absent',
            'attendance_days_late', 'attendance_rate', 'updated_at',
        ])

    def __str__(self) -> str:
        return f"Report: {self.student_id} | {self.term_id} | {self.status}"


class ECDDomainConfig(TimeStampedModel):
    """
    Configurable ECD domain competencies per class type.
    Replaces the hardcoded DOMAIN_GROUPS in ecd.py views.
    """
    CLASS_TYPE_CHOICES = [
        ("pre_k", "Pre-K"),
        ("kindergarten", "Kindergarten"),
        ("pre_school", "Pre-School"),
        ("abc", "ABC Class"),
    ]
    class_type = models.CharField(max_length=20, choices=CLASS_TYPE_CHOICES)
    domain_name = models.CharField(max_length=100)
    competencies = models.JSONField(default=list, help_text="List of competency strings for this domain")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["class_type", "sort_order", "domain_name"]
        unique_together = ("class_type", "domain_name")

    def __str__(self) -> str:
        return f"{self.get_class_type_display()} — {self.domain_name}"


class ECDEvaluation(TimeStampedModel):
    """
    Evaluations for ECD specific domains (Numeracy, Communication, etc.)
    Graded as E (Excellent), G (Good), S (Satisfactory), N (Needs Improvement).
    """
    report_card = models.ForeignKey(ReportCard, on_delete=models.CASCADE, related_name="ecd_evaluations")
    domain = models.CharField(max_length=100)
    rating = models.CharField(
        max_length=2,
        choices=[
            ("E", "Excellent"),
            ("G", "Good"),
            ("S", "Satisfactory"),
            ("N", "Needs Improvement"),
        ]
    )

    class Meta:
        unique_together = ("report_card", "domain")

    def __str__(self) -> str:
        return f"{self.report_card_id} | {self.domain} | {self.rating}"


class CambridgeCheckpointScore(TimeStampedModel):
    """
    Cambridge Checkpoint results for Grades 6, 7, 8, and 9.
    Stored as a score (1.0 to 6.0) - FR-ACAD-005.
    """
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="checkpoint_scores")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT)
    subject_name = models.CharField(max_length=80)
    score = models.DecimalField(max_digits=3, decimal_places=1) # 0.0 to 6.0
    
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        unique_together = ("student", "academic_year", "subject_name")

    def clean(self):
        super().clean()
        if self.score < 1.0 or self.score > 6.0:
            raise ValidationError("Cambridge Checkpoint score must be between 1.0 and 6.0.")
        # FR-ACAD-005: Cambridge Primary (Grade 6) and Lower Secondary (Grades 7-9)
        if self.student.class_name not in ["Grade 6", "Grade 7", "Grade 8", "Grade 9"]:
            raise ValidationError("Cambridge Checkpoint is only for Grades 6, 7, 8, and 9.")

    def __str__(self) -> str:
        return f"Checkpoint: {self.student_id} | {self.subject_name} | {self.score}"


class ABCPaceProgress(TimeStampedModel):
    """
    A.C.E. Curriculum PACE progress tracking for ABC Class students.
    """
    report_card = models.ForeignKey(ReportCard, on_delete=models.CASCADE, related_name="abc_pace_progress")
    subject = models.CharField(max_length=64) # Math, English, Word Building, etc.
    pace_no = models.CharField(max_length=10) # e.g. M05
    sticker_no = models.CharField(max_length=10, blank=True)
    status = models.CharField(max_length=20, choices=[("complete", "Complete"), ("in_progress", "In progress")])
    supervisor_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    moderator_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    date_completed = models.CharField(max_length=40, blank=True) # Text date as per reference e.g. "15 Apr"

    class Meta:
        verbose_name = "ABC PACE Progress"
        verbose_name_plural = "ABC PACE Progress"
        unique_together = ("report_card", "subject", "pace_no")

class ABCScripture(TimeStampedModel):
    """Termly memory verses for ABC Class. FR-CAL-002: 3 terms per year."""
    report_card = models.ForeignKey(ReportCard, on_delete=models.CASCADE, related_name="abc_scripture")
    quarter = models.PositiveIntegerField(choices=[(1, "Term 1"), (2, "Term 2"), (3, "Term 3")])
    verse = models.CharField(max_length=255)

    class Meta:
        unique_together = ("report_card", "quarter")

class ABCReadingProgramme(TimeStampedModel):
    """Termly reading metrics for ABC Class. FR-CAL-002: 3 terms per year."""
    report_card = models.ForeignKey(ReportCard, on_delete=models.CASCADE, related_name="abc_reading")
    quarter = models.PositiveIntegerField(choices=[(1, "Term 1"), (2, "Term 2"), (3, "Term 3")])
    wpm = models.PositiveIntegerField(null=True, blank=True)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    comprehension_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        unique_together = ("report_card", "quarter")

class ABCGeneralAssignment(TimeStampedModel):
    """Termly general assignments for ABC Class. FR-CAL-002: 3 terms per year."""
    report_card = models.ForeignKey(ReportCard, on_delete=models.CASCADE, related_name="abc_assignments")
    quarter = models.PositiveIntegerField(choices=[(1, "Term 1"), (2, "Term 2"), (3, "Term 3")])
    item_name = models.CharField(max_length=150)
    score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        unique_together = ("report_card", "quarter", "item_name")


class ABCInternalExam(TimeStampedModel):
    """ABC Class Term 3 internal exam — FR-ACAD-013.
    
    School-run internal exam for ABC Class students who sit the Term 3 exam.
    Uses the same 4-level ECD grading scheme (E/G/S/N).
    Recorded separately from regular ABC assessments.
    """
    report_card = models.ForeignKey(ReportCard, on_delete=models.CASCADE, related_name="abc_internal_exams")
    subject = models.CharField(max_length=64)
    rating = models.CharField(
        max_length=2,
        choices=[("E", "Excellent"), ("G", "Good"), ("S", "Satisfactory"), ("N", "Needs Improvement")],
    )

    class Meta:
        verbose_name = "ABC Internal Exam"
        verbose_name_plural = "ABC Internal Exams"
        unique_together = ("report_card", "subject")

    def __str__(self) -> str:
        return f"{self.subject}: {self.get_rating_display()}"


class ProgressionConfig(TimeStampedModel):
    """Thresholds and settings for a single year-end progression cycle.

    One row per (academic_year_from, academic_year_to) pair.
    Created by HOS/admin before calculation can begin.
    """
    academic_year_from = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="progression_configs_from"
    )
    academic_year_to = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="progression_configs_to"
    )
    minimum_average = models.FloatField(
        default=50.0, help_text="Minimum overall average required for promotion (0-100)."
    )
    minimum_attendance = models.FloatField(
        default=80.0, help_text="Minimum attendance rate percentage required for promotion (0-100)."
    )
    retention_threshold = models.FloatField(
        default=50.0,
        help_text="Threshold below which students are automatically flagged for retention (0-100).",
    )
    created_by = models.ForeignKey(
        "users.User", on_delete=models.PROTECT, related_name="progression_configs"
    )
    recalc_status = models.CharField(
        max_length=20, choices=RecalcStatus.choices, default=RecalcStatus.NOT_STARTED,
    )
    recalc_completed_count = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Number of cases successfully recalculated in the last run.",
    )
    recalc_failed_details = models.JSONField(
        default=list, blank=True,
        help_text="List of {student_id, reason} dicts for students that failed during recalculation.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Progression Config"
        verbose_name_plural = "Progression Configs"
        unique_together = ("academic_year_from", "academic_year_to")

    def __str__(self) -> str:
        return f"Progression {self.academic_year_from} → {self.academic_year_to}"


class ProgressionCase(TimeStampedModel):
    """One student's progression record for a given configuration cycle.

    This is the core review object — HOD annotates, HOS decides, then the
    promotion engine bulk-executes only finalized cases.
    """
    student = models.ForeignKey(
        "students.Student", on_delete=models.PROTECT, related_name="progression_cases"
    )
    progression_config = models.ForeignKey(
        ProgressionConfig, on_delete=models.PROTECT, related_name="cases"
    )
    calculated_average = models.FloatField(
        null=True, blank=True, help_text="Overall average (0-100) from Grade Entry."
    )
    calculated_attendance_rate = models.FloatField(
        null=True, blank=True, help_text="Attendance rate percentage (0-100) for the academic year."
    )
    calculation_basis = models.CharField(
        max_length=20, choices=CalculationBasis.choices, default=CalculationBasis.INCOMPLETE,
    )
    system_suggested_outcome = models.CharField(
        max_length=30, choices=ProgressionOutcome.choices, null=True, blank=True,
        help_text="System-calculated suggestion based on averages and attendance.",
    )
    hod_recommendation = models.CharField(
        max_length=30, choices=ProgressionOutcome.choices, null=True, blank=True,
        help_text="HOD's recommended outcome for HOS review.",
    )
    hod_recommended_by = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="hod_recommendations",
    )
    hod_recommended_at = models.DateTimeField(null=True, blank=True)
    hos_decision = models.CharField(
        max_length=30, choices=ProgressionOutcome.choices, null=True, blank=True,
        help_text="Final decision made by HOS.",
    )
    hos_decided_by = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="hos_decisions",
    )
    hos_decided_at = models.DateTimeField(null=True, blank=True)
    override_reason = models.TextField(
        blank=True,
        help_text="Required when HOD/HOS outcome differs from system suggestion.",
    )
    status = models.CharField(
        max_length=30, choices=ProgressionStatus.choices, default=ProgressionStatus.CALCULATED,
    )
    hos_return_comment = models.TextField(
        blank=True,
        help_text="Required when HOS returns a case to HOD for reconsideration.",
    )

    class Meta:
        ordering = ["student"]
        verbose_name = "Progression Case"
        verbose_name_plural = "Progression Cases"
        unique_together = ("student", "progression_config")
        permissions = [
            ("can_review_progression", "Can review and recommend progression cases (department HODs)"),
            ("can_decide_progression", "Can make final decisions on progression cases (HOS)"),
        ]

    def __str__(self) -> str:
        return f"{self.student} — {self.get_status_display()}"

    def clean(self):
        """Enforce status transition rules at the model layer."""
        if self.pk is None:
            # New case — only 'calculated' is permitted as entry point
            if self.status != ProgressionStatus.CALCULATED:
                raise ValidationError(
                    f"New ProgressionCase may only be created with status 'calculated', "
                    f"got '{self.status}'. Use the workflow to transition to other statuses."
                )
            return
        try:
            old = ProgressionCase.objects.get(pk=self.pk)
        except ProgressionCase.DoesNotExist:
            return
        old_status = old.status
        new_status = self.status

        # Allow no-change saves
        if old_status == new_status:
            return

        ALLOWED_TRANSITIONS = {
            ProgressionStatus.CALCULATED: [ProgressionStatus.PENDING_HOD_REVIEW],
            ProgressionStatus.PENDING_HOD_REVIEW: [ProgressionStatus.PENDING_HOS_DECISION],
            ProgressionStatus.PENDING_HOS_DECISION: [ProgressionStatus.FINALIZED, ProgressionStatus.PENDING_HOD_REVIEW],
        }

        allowed = ALLOWED_TRANSITIONS.get(old_status, [])
        if new_status not in allowed:
            raise ValidationError(
                f"Invalid status transition: {old_status} → {new_status}. "
                f"Allowed from {old_status}: {', '.join(allowed) if allowed else 'none'}."
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class PromotionRun(TimeStampedModel):
    """Tracks year-end student promotion batches.

    Each record represents one promotion execution. The run is resumable:
    if interrupted mid-batch, already-processed students remain committed
    and the run can be re-started on the remaining students.
    """
    academic_year_from = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="promotion_runs_from"
    )
    academic_year_to = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="promotion_runs_to"
    )
    executed_by = models.ForeignKey(
        "users.User", on_delete=models.PROTECT, related_name="promotion_runs"
    )
    promoted_count = models.PositiveIntegerField(default=0)
    retained_count = models.PositiveIntegerField(default=0)
    graduated_count = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        default="pending",
        help_text="Run status: pending, in_progress, paused, interrupted, completed.",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    processed_student_ids = models.JSONField(
        default=list, blank=True,
        help_text="List of student PKs successfully processed (enables resume on interruption).",
    )
    failed_student_ids = models.JSONField(
        default=list, blank=True,
        help_text="List of student PKs that failed during execution (eligible for retry).",
    )
    failed_count = models.PositiveIntegerField(default=0)
    failed_detail_json = models.JSONField(
        default=list, blank=True,
        help_text="List of {student_id, reason} dicts for students that failed during execution.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Promotion Run"
        verbose_name_plural = "Promotion Runs"

    def __str__(self) -> str:
        return f"Promotion {self.academic_year_from} → {self.academic_year_to} [{self.status or 'pending'}]"

