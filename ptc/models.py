# PTC Module - Learner Progress Report
# Adjustment note: UserRole does not have distinct "class_teacher" / "subject_teacher" /
# "specialist_teacher" roles. All teachers share the TEACHER role. Teacher-to-class and
# teacher-to-subject mappings are derived from TeacherClassAssignment (hr app) and
# TimetableSlot (timetable app) at query time. This matches the existing codebase pattern.

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.models import TimeStampedModel


# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------

class TermSlot(models.TextChoices):
    # FR-PTC-001: Only Term 1 and Term 3 PTC windows exist.
    TERM_1 = "term_1", "Term 1"
    TERM_3 = "term_3", "Term 3"


class EnrichmentLetterGrade(models.TextChoices):
    # Labels match the official PTC form PDF exactly.
    A_STAR = "A*", "Outstanding (90 - 100)"
    A = "A", "High (80 - 89)"
    B = "B", "Good (70 - 79)"
    C = "C", "Aspiring (60 - 69)"
    D = "D", "Basic (50 - 59)"
    E = "E", "Needs Improvement (49 - 0)"


class LearnerAttributeRating(models.TextChoices):
    E = "E", "Excellent"
    G = "G", "Good"
    S = "S", "Satisfactory"
    N = "N", "Needs Improvement"


# ---------------------------------------------------------------------------
# Fixed reference data — 12 learner attributes (FR-PTC-019)
# ---------------------------------------------------------------------------

LEARNER_ATTRIBUTES = [
    # Order matches the official PTC form PDF exactly.
    (1, "Writes with good and neat handwriting at a desirable speed"),
    (2, "Reads grade content to standard"),
    (3, "Works well independently"),
    (4, "Comprehends grade level content"),
    (5, "Works well in a group setting"),
    (6, "Respects authority and has a good attitude towards discipline"),
    (7, "Is able to manage time well"),
    (8, "Attention to personal hygiene and appearance"),
    (9, "Readiness to listen and pay attention to lessons"),
    (10, "Relates well with others"),
    (11, "Completes homework and assignments"),
    (12, "Attendance and timely arrival to school"),
]


# ---------------------------------------------------------------------------
# PTC Window — one per academic year per term slot
# ---------------------------------------------------------------------------

class PTCWindow(TimeStampedModel):
    """
    Defines the PTC schedule for a given academic year and term slot.
    Two windows per year: Term 1 and Term 3 (FR-PTC-001).
    """

    academic_year = models.ForeignKey(
        "academics.AcademicYear",
        on_delete=models.PROTECT,
        related_name="ptc_windows",
    )
    term_slot = models.CharField(
        max_length=10,
        choices=TermSlot.choices,
        help_text="Term 1 or Term 3 (FR-PTC-001)",
    )
    ptc_date = models.DateField(
        help_text="The date the parent-teacher conference is held",
    )

    # --- 30-day notification window (FR-PTC-003, FR-PTC-004) ---
    notification_window_days = models.PositiveIntegerField(
        default=30,
        help_text="Days before PTC date when the notification window opens. Configurable by Super Admin.",
    )
    notification_window_opens_at = models.DateField(
        null=True,
        blank=True,
        editable=False,
        help_text="Computed: ptc_date - notification_window_days",
    )
    notification_window_closes_at = models.DateField(
        null=True,
        blank=True,
        editable=False,
        help_text="Computed: ptc_date - 4 days (hardcoded per FR-PTC-003)",
    )

    # --- 14-day comment / attribute entry window (FR-PTC-014, FR-PTC-021) ---
    comment_entry_window_days = models.PositiveIntegerField(
        default=14,
        help_text="Days before PTC date when the comment/attribute entry window opens. Fixed by FRD but stored for flexibility.",
    )
    comment_entry_window_opens_at = models.DateField(
        null=True,
        blank=True,
        editable=False,
        help_text="Computed: ptc_date - comment_entry_window_days",
    )
    comment_entry_window_closes_at = models.DateField(
        null=True,
        blank=True,
        editable=False,
        help_text="Computed: ptc_date (window closes on PTC date itself)",
    )

    # --- Publication & audit ---
    is_published = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Once published, date changes require Super Admin and are audited (FR-PTC-002).",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ptc_windows_created",
        null=True,
        blank=True,
    )
    last_modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ptc_windows_modified",
        null=True,
        blank=True,
    )

    class Meta:
        unique_together = ("academic_year", "term_slot")
        ordering = ["academic_year__name", "term_slot"]

    def clean(self):
        super().clean()
        if self.term_slot not in dict(TermSlot.choices):
            raise ValidationError("term_slot must be 'term_1' or 'term_3'.")
        # Ensure notification window is strictly wider than comment entry window
        if self.notification_window_days <= self.comment_entry_window_days:
            raise ValidationError(
                "notification_window_days must be greater than comment_entry_window_days."
            )

    def save(self, *args, **kwargs):
        from datetime import timedelta, date
        from django.utils.dateparse import parse_date
        if isinstance(self.ptc_date, str):
            parsed = parse_date(self.ptc_date)
            if parsed is not None:
                self.ptc_date = parsed
        if isinstance(self.notification_window_days, str):
            self.notification_window_days = int(self.notification_window_days)
        if isinstance(self.comment_entry_window_days, str):
            self.comment_entry_window_days = int(self.comment_entry_window_days)
        if self.ptc_date:
            self.notification_window_opens_at = self.ptc_date - timedelta(days=self.notification_window_days)
            self.notification_window_closes_at = self.ptc_date - timedelta(days=4)
            self.comment_entry_window_opens_at = self.ptc_date - timedelta(days=self.comment_entry_window_days)
            self.comment_entry_window_closes_at = self.ptc_date
        super().save(*args, **kwargs)

    def __str__(self):
        return f"PTC {self.get_term_slot_display()} — {self.academic_year.name} ({self.ptc_date})"

    # --- Convenience properties ---

    @property
    def is_notification_window_open(self):
        from django.utils import timezone
        today = timezone.now().date()
        if not self.notification_window_opens_at or not self.notification_window_closes_at:
            return False
        return self.notification_window_opens_at <= today <= self.notification_window_closes_at

    @property
    def is_comment_entry_window_open(self):
        from django.utils import timezone
        today = timezone.now().date()
        if not self.comment_entry_window_opens_at or not self.comment_entry_window_closes_at:
            return False
        return self.comment_entry_window_opens_at <= today <= self.comment_entry_window_closes_at

    @property
    def days_until_ptc(self):
        from django.utils import timezone
        delta = self.ptc_date - timezone.now().date()
        return delta.days


# ---------------------------------------------------------------------------
# Enrichment Subjects — linked to academics.Subject with is_enrichment=True (FR-PTC-006)
# EnrichmentSubject stores the PTC-specific config (teacher, class, weight).
# The subject itself lives in academics.Subject — no duplicate data.
# ---------------------------------------------------------------------------


class EnrichmentSubject(TimeStampedModel):
    """
    PTC enrichment configuration linked to an academics.Subject.
    One row per enrichment subject, holding the specialist teacher,
    assigned class, and weight for report-card calculation.
    """

    subject = models.OneToOneField(
        "academics.Subject",
        on_delete=models.PROTECT,
        related_name="enrichment_config",
        help_text="Academic subject marked as enrichment (is_enrichment=True)",
    )
    assigned_teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrichment_subjects",
        help_text="Specialist teacher assigned to this enrichment subject",
    )
    assigned_class = models.ForeignKey(
        "academics.GradeClass",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrichment_subjects",
        help_text="Class this enrichment subject is assigned to",
    )
    weight_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="Weight percentage for report card calculation (0 = letter grade only, no weighted avg)",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Enrichment Subject"
        verbose_name_plural = "Enrichment Subjects"
        ordering = ["subject__name"]
        permissions = [
            ("manage_enrichment_config", "Can manage enrichment subject configuration"),
        ]

    def __str__(self):
        teacher = self.assigned_teacher.get_full_name() if self.assigned_teacher else "Unassigned"
        return f"{self.subject.name} — {teacher}"


# ---------------------------------------------------------------------------
# Enrichment Grades — one row per student per enrichment subject per term
# ---------------------------------------------------------------------------

class EnrichmentGrade(TimeStampedModel):
    """
    Letter grade for enrichment subjects. One per student, per subject, per term.
    Locked at term end; only Super Admin override allowed (FR-PTC-009).
    """

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="enrichment_grades",
    )
    enrichment_subject = models.ForeignKey(
        EnrichmentSubject,
        on_delete=models.PROTECT,
        related_name="grades",
    )
    term = models.ForeignKey(
        "academics.Term",
        on_delete=models.PROTECT,
        related_name="enrichment_grades",
    )
    letter_grade = models.CharField(
        max_length=2,
        choices=EnrichmentLetterGrade.choices,
        blank=True,
        default="",
    )
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="enrichment_grades_entered",
        null=True,
        blank=True,
    )
    is_locked = models.BooleanField(default=False)

    class Meta:
        unique_together = ("student", "enrichment_subject", "term")
        indexes = [
            models.Index(fields=["student", "term"]),
            models.Index(fields=["enrichment_subject", "term"]),
        ]

    def clean(self):
        super().clean()
        if self.letter_grade and self.letter_grade not in dict(EnrichmentLetterGrade.choices):
            raise ValidationError(
                f"letter_grade must be one of {', '.join(dict(EnrichmentLetterGrade.choices).keys())}"
            )

    def __str__(self):
        return f"{self.student} | {self.enrichment_subject.subject.name} | {self.term} | {self.letter_grade}"


# ---------------------------------------------------------------------------
# PTC Subject Comments — one per student, per core subject, per PTC window
# ---------------------------------------------------------------------------

class PTCSubjectComment(TimeStampedModel):
    """
    Free-text comment written by a subject teacher for a student, per core subject,
    per PTC window. Term 1 and Term 3 are stored as separate rows (FR-PTC-017).
    Independent of any Term Report comment model.
    """

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="ptc_comments",
    )
    ptc_window = models.ForeignKey(
        PTCWindow,
        on_delete=models.PROTECT,
        related_name="subject_comments",
    )
    subject_name = models.CharField(
        max_length=80,
        db_index=True,
        help_text="Core subject name matching the grade entry system",
    )
    comment_text = models.TextField(
        blank=True,
        default="",
        help_text="Free text. Soft hint: recommended 30 chars. No hard minimum (FR-PTC-012).",
    )
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ptc_comments_entered",
        null=True,
        blank=True,
    )

    class Meta:
        unique_together = ("student", "ptc_window", "subject_name")
        indexes = [
            models.Index(fields=["ptc_window", "subject_name"]),
            models.Index(fields=["student", "ptc_window"]),
        ]

    def __str__(self):
        return f"PTC Comment: {self.student} | {self.subject_name} | {self.ptc_window}"


# ---------------------------------------------------------------------------
# Learner Attribute Rating — one per student, per attribute, per PTC window
# ---------------------------------------------------------------------------

class LearnerAttributeRatingEntry(TimeStampedModel):
    """
    Rating for a fixed learner attribute. One per student, per attribute number,
    per PTC window (FR-PTC-018, FR-PTC-023).
    """

    LEARNER_ATTRIBUTE_CHOICES = [(n, label) for n, label in LEARNER_ATTRIBUTES]

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="ptc_attribute_ratings",
    )
    ptc_window = models.ForeignKey(
        PTCWindow,
        on_delete=models.PROTECT,
        related_name="attribute_ratings",
    )
    attribute_number = models.PositiveSmallIntegerField(
        choices=LEARNER_ATTRIBUTE_CHOICES,
        help_text="1-12 matching the fixed attribute list (FR-PTC-019)",
    )
    rating = models.CharField(
        max_length=1,
        choices=LearnerAttributeRating.choices,
        blank=True,
        default="",
        help_text="E/G/S/N",
    )
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ptc_attribute_ratings_entered",
        null=True,
        blank=True,
    )

    class Meta:
        unique_together = ("student", "ptc_window", "attribute_number")
        indexes = [
            models.Index(fields=["ptc_window", "student"]),
            models.Index(fields=["student", "ptc_window"]),
        ]
        ordering = ["student__last_name", "student__first_name", "attribute_number"]

    def clean(self):
        super().clean()
        if self.attribute_number < 1 or self.attribute_number > 12:
            raise ValidationError("attribute_number must be between 1 and 12.")
        if self.rating and self.rating not in dict(LearnerAttributeRating.choices):
            raise ValidationError(
                f"rating must be one of {', '.join(dict(LearnerAttributeRating.choices).keys())}"
            )

    def __str__(self):
        return f"{self.student} | Attr {self.attribute_number} | {self.rating} | {self.ptc_window}"


# ---------------------------------------------------------------------------
# PTC Generation Log — every time a class teacher opens a PTC screen (FR-PTC-030)
# ---------------------------------------------------------------------------

class PTCGenerationLog(TimeStampedModel):
    """
    Read-only log visible to Primary HOD and HOS (FR-PTC-030).
    """

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="ptc_generation_logs",
    )
    class_teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ptc_generation_logs",
    )
    ptc_window = models.ForeignKey(
        PTCWindow,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generation_logs",
    )
    opened_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-opened_at"]
        indexes = [
            models.Index(fields=["student", "opened_at"]),
            models.Index(fields=["class_teacher", "opened_at"]),
        ]

    def __str__(self):
        return f"PTC opened: {self.student} by {self.class_teacher} at {self.opened_at}"


# ---------------------------------------------------------------------------
# PTC Progress Trend Override — class teacher can override auto-derived trend (FR-PTC-029)
# ---------------------------------------------------------------------------

class PTCProgressTrendOverride(TimeStampedModel):
    """
    Allows class teachers to override the auto-derived progress trend.
    The auto-derived value remains the default; override is explicit per FR-PTC-029.
    """

    TREND_CHOICES = [
        ("improving", "Improving"),
        ("stable", "Stable"),
        ("needs_attention", "Needs Attention"),
    ]

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="ptc_trend_overrides",
    )
    ptc_window = models.ForeignKey(
        PTCWindow,
        on_delete=models.PROTECT,
        related_name="trend_overrides",
    )
    overridden_trend = models.CharField(
        max_length=20,
        choices=TREND_CHOICES,
        help_text="Teacher-selected trend override",
    )
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ptc_trend_overrides_entered",
    )

    class Meta:
        unique_together = ("student", "ptc_window")

    def __str__(self):
        return f"Trend override: {self.student} | {self.ptc_window} | {self.overridden_trend}"


# ---------------------------------------------------------------------------
# PTC Date Change Audit Log — triggered when a published window's date is changed
# ---------------------------------------------------------------------------

class PTCDateChangeLog(TimeStampedModel):
    """
    Reuses audit trail concept. Created when a PTCWindow date is altered after
    the academic year is published, by Super Admin only (FR-PTC-002).
    """

    ptc_window = models.ForeignKey(
        PTCWindow,
        on_delete=models.CASCADE,
        related_name="date_change_logs",
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ptc_date_changes",
    )
    old_ptc_date = models.DateField()
    new_ptc_date = models.DateField()
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self):
        return (
            f"Date change: {self.ptc_window} | {self.old_ptc_date} → {self.new_ptc_date} "
            f"by {self.changed_by}"
        )
