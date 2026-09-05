from datetime import date, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.db.models import Q
from core.models import TimeStampedModel


class StudentStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    GRADUATED = "graduated", "Graduated"
    SUSPENDED = "suspended", "Suspended"
    WITHDRAWN = "withdrawn", "Withdrawn"


class Student(TimeStampedModel):
    admission_no = models.CharField(max_length=32, unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    preferred_name = models.CharField(max_length=100, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(
        max_length=16,
        blank=True,
        choices=[
            ("male", "Male"),
            ("female", "Female"),
            ("other", "Other"),
        ],
    )
    nationality = models.CharField(max_length=64, blank=True)
    religion = models.CharField(max_length=64, blank=True)
    blood_type = models.CharField(
        max_length=8,
        blank=True,
        choices=[
            ("a+", "A+"),
            ("a-", "A-"),
            ("b+", "B+"),
            ("b-", "B-"),
            ("ab+", "AB+"),
            ("ab-", "AB-"),
            ("o+", "O+"),
            ("o-", "O-"),
        ],
    )
    allergies_medical = models.TextField(blank=True)
    photo = models.FileField(upload_to="students/photos/", null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=StudentStatus.choices,
        default=StudentStatus.ACTIVE,
        db_index=True,
    )
    class_name = models.CharField(max_length=64, db_index=True)
    stream_name = models.CharField(max_length=64, blank=True)
    enrolment_date = models.DateField(null=True, blank=True)
    academic_year = models.ForeignKey(
        "academics.AcademicYear",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="students",
    )

    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="archived_students",
    )

    sibling_discount_eligible = models.BooleanField(default=False, db_index=True)
    guardians = models.ManyToManyField("students.ParentGuardian", through="students.StudentGuardian", blank=True)

    # Laravel compatibility fields
    laravel_student_id = models.CharField(max_length=50, unique=True, null=True, blank=True, db_index=True)
    qr_code = models.CharField(max_length=100, unique=True, null=True, blank=True, db_index=True)
    phone = models.CharField(max_length=20, blank=True)  # For parent notifications
    image = models.ImageField(upload_to='student_photos/', null=True, blank=True)  # Laravel compatibility

    class Meta:
        unique_together = ("first_name", "last_name", "date_of_birth")
        indexes = [
            models.Index(fields=["last_name", "first_name"]),
            models.Index(fields=["laravel_student_id"]),
            models.Index(fields=["qr_code"]),
            models.Index(fields=["status", "class_name"]),
        ]

    def get_full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def age(self):
        """Compute age in years from date_of_birth."""
        if not self.date_of_birth:
            return None
        today = date.today()
        years = today.year - self.date_of_birth.year
        if (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day):
            years -= 1
        return years

    def get_today_attendance(self):
        """Get today's attendance entry for this student"""
        from attendance.models import AttendanceEntry
        return self.attendance_entries.filter(date=timezone.now().date()).first()

    def get_attendance_rate(self, start_date=None, end_date=None):
        """Calculate attendance percentage for date range.

        Accepts plain dates or Term objects.  When a Term is passed as
        *start_date*, both its start_date and end_date are extracted so the
        calculation covers the full term — not just start-to-today.
        """
        from attendance.models import AttendanceEntry, AttendanceStatus
        from academics.models import Term

        # Handle Term objects — extract the actual date range
        if isinstance(start_date, Term):
            end_date = start_date.end_date
            start_date = start_date.start_date

        if not start_date:
            start_date = timezone.now().date().replace(day=1)  # Start of current month
        if not end_date:
            end_date = timezone.now().date()

        # Ensure dates are date objects, not datetime
        if hasattr(start_date, 'date'):
            start_date = start_date.date()
        if hasattr(end_date, 'date'):
            end_date = end_date.date()
        
        total_entries = self.attendance_entries.filter(
            date__range=[start_date, end_date]
        ).count()
        
        if total_entries == 0:
            return 0
        
        present_entries = self.attendance_entries.filter(
            date__range=[start_date, end_date],
            status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE]
        ).count()
        
        return round((present_entries / total_entries) * 100, 1)

    @classmethod
    def search_students(cls, query):
        """Search students by name or ID with fuzzy matching"""
        if not query:
            return cls.objects.none()
        
        return cls.objects.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(admission_no__icontains=query) |
            Q(laravel_student_id__icontains=query),
            status=StudentStatus.ACTIVE
        ).order_by('first_name', 'last_name')

    def clean(self):
        super().clean()
        if not self.first_name.strip() or not self.last_name.strip():
            raise ValidationError("Student first and last name are required.")
        if not self.class_name.strip():
            raise ValidationError("Class is required.")
        if self.is_archived and not self.archived_at:
            self.archived_at = timezone.now()
        if self.pk:
            try:
                existing = Student.objects.get(pk=self.pk)
            except Student.DoesNotExist:
                pass
            else:
                if existing.admission_no != self.admission_no:
                    raise ValidationError("Student ID (admission number) cannot be changed after creation.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def is_below_attendance_threshold(self, threshold=85.0):
        """FR-ATT-012: Check if student's attendance rate falls below threshold."""
        rate = self.get_attendance_rate()
        return rate > 0 and rate < threshold

    @property
    def retention_expires_at(self):
        """NFR-PDPA-005: Date when this archived record becomes eligible for permanent deletion.

        Returns None if the student is not archived or has no archived_at date.
        """
        if not self.is_archived or not self.archived_at:
            return None
        years = getattr(settings, "ARCHIVE_RETENTION_YEARS", 7)
        return self.archived_at + timedelta(days=years * 365)

    @property
    def is_retention_expired(self):
        """NFR-PDPA-005: True if the archive retention period has passed."""
        expires = self.retention_expires_at
        if expires is None:
            return False
        return timezone.now() >= expires

    def can_be_permanently_deleted(self):
        """NFR-PDPA-005: Returns (True, None) if deletion is allowed, or
        (False, reason_string) if blocked."""
        if not self.is_archived:
            return False, "Student is not archived. Archive first."
        if not self.is_retention_expired:
            expires = self.retention_expires_at
            return False, (
                f"Record is still within the {getattr(settings, 'ARCHIVE_RETENTION_YEARS', 7)}-year "
                f"retention period (expires {expires.date()}). "
                f"Use the manage_archived_records command to check status."
            )
        return True, None

    def has_protected_records(self) -> bool:
        """Check if student has attendance, grade, or financial records that prevent deletion."""
        from attendance.models import AttendanceEntry
        from academics.models import ReportCard, ExamScore
        from finance.models import Invoice
        return any([
            self.attendance_entries.exists(),
            ReportCard.objects.filter(student=self).exists(),
            ExamScore.objects.filter(student=self).exists(),
            Invoice.objects.filter(student=self).exists(),
        ])

    def delete(self, *args, **kwargs):
        """Override delete to enforce NFR-PDPA-005 retention policy and
        prevent deletion when associated records exist.

        - Non-archived students: blocked if protected records exist.
        - Archived students within retention period: blocked with FRD message.
        - Archived students past retention: allowed (use manage_archived_records
          or queryset .delete() to bypass this guard for bulk pruning).
        """
        if self.has_protected_records():
            # Check retention before raising — allow past-retention deletion
            allowed, reason = self.can_be_permanently_deleted()
            if not allowed:
                raise ValidationError(
                    "This student has associated records and cannot be deleted. Use Archive instead."
                )
            # Past retention — allow deletion (e.g. Super Admin action)
        elif self.is_archived and not self.is_retention_expired:
            # Archived but within retention — never allow manual deletion
            years = getattr(settings, "ARCHIVE_RETENTION_YEARS", 7)
            raise ValidationError(
                f"Record is within the {years}-year retention period and cannot be deleted."
            )
        super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.admission_no} - {self.first_name} {self.last_name}"


class StudentSibling(TimeStampedModel):
    """Confirmed sibling relationship (undirected, stored in one row)."""

    student_a = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="sibling_links_a")
    student_b = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="sibling_links_b")
    created_by = models.ForeignKey("users.User", on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        unique_together = ("student_a", "student_b")
        indexes = [
            models.Index(fields=["student_a"]),
            models.Index(fields=["student_b"]),
        ]

    def clean(self):
        super().clean()
        if self.student_a_id == self.student_b_id:
            raise ValidationError("A student cannot be a sibling of themselves.")

    def __str__(self) -> str:
        return f"{self.student_a_id} ↔ {self.student_b_id}"


class GuardianRelationship(models.TextChoices):
    MOTHER = "mother", "Mother"
    FATHER = "father", "Father"
    PARENT = "parent", "Parent"
    STEP_MOTHER = "step_mother", "Step-Mother"
    STEP_FATHER = "step_father", "Step-Father"
    GRANDPARENT = "grandparent", "Grandparent"
    AUNT = "aunt", "Aunt"
    UNCLE = "uncle", "Uncle"
    SIBLING = "sibling", "Sibling"
    GUARDIAN = "guardian", "Legal Guardian"
    EMERGENCY = "emergency", "Emergency Contact"
    OTHER = "other", "Other"


class ParentGuardian(TimeStampedModel):
    user = models.OneToOneField("users.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="guardian_profile")
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=32, db_index=True)
    secondary_phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    preferred_invoice_name = models.CharField(max_length=150, blank=True)
    preferred_language = models.CharField(
        max_length=16,
        blank=True,
        choices=[
            ("en", "English"),
            ("sw", "Swahili"),
        ],
    )

    pdpa_consent_given = models.BooleanField(default=False)
    pdpa_consent_method = models.CharField(
        max_length=16,
        blank=True,
        choices=[
            ("in_person", "In-person"),
            ("digital", "Digital"),
        ],
    )
    pdpa_consent_version = models.CharField(max_length=32, blank=True)
    pdpa_consented_at = models.DateTimeField(null=True, blank=True)

    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="archived_guardians",
    )

    class Meta:
        unique_together = ("full_name", "phone")
        indexes = [
            models.Index(fields=["phone"]),
        ]

    def clean(self):
        super().clean()
        if not self.full_name.strip():
            raise ValidationError("Guardian full name is required.")
        if not self.phone.strip():
            raise ValidationError("Guardian phone is required.")

    def has_given_consent(self):
        """FR-PAR-004: Check if PDPA consent has been recorded (method, date, version)."""
        return (
            self.pdpa_consent_given
            and bool(self.pdpa_consent_method)
            and self.pdpa_consented_at is not None
            and bool(self.pdpa_consent_version)
        )

    def __str__(self) -> str:
        return f"{self.full_name} ({self.phone})"


class PDPAConsentLog(TimeStampedModel):
    """Audit trail for all PDPA consent lifecycle events."""
    class Action(models.TextChoices):
        GIVEN = "given", "Consent Given"
        WITHDRAWN = "withdrawn", "Consent Withdrawn"
        UPDATED = "updated", "Consent Updated"

    guardian = models.ForeignKey(ParentGuardian, on_delete=models.CASCADE, related_name="consent_logs")
    action = models.CharField(max_length=16, choices=Action.choices)
    method = models.CharField(max_length=16, blank=True, choices=ParentGuardian._meta.get_field('pdpa_consent_method').choices)
    version = models.CharField(max_length=32, blank=True)
    previous_version = models.CharField(max_length=32, blank=True, help_text="Version before this change, if any")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "PDPA Consent Log"

    def __str__(self) -> str:
        return f"{self.guardian.full_name} → {self.action} @ {self.created_at}"


class StudentGuardian(TimeStampedModel):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    guardian = models.ForeignKey(ParentGuardian, on_delete=models.CASCADE)
    relationship = models.CharField(max_length=20, choices=GuardianRelationship.choices, default=GuardianRelationship.GUARDIAN)
    is_primary = models.BooleanField(default=False)

    class Meta:
        unique_together = ("student", "guardian")
        indexes = [
            models.Index(fields=["guardian", "is_primary"]),
        ]

    def clean(self):
        super().clean()
        if self.is_primary:
            existing = StudentGuardian.objects.filter(
                student=self.student,
                is_primary=True,
            ).exclude(pk=self.pk)
            if existing.exists():
                other = existing.first()
                raise ValidationError(
                    f'{self.student} already has {other.guardian.full_name} as primary contact. '
                    f'Unset that one first before setting a new primary contact.'
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.student_id} ↔ {self.guardian_id}"


class LaravelParent(TimeStampedModel):
    """
    Laravel-compatible parent model for enhanced parent management.
    Works alongside existing ParentGuardian system for Laravel integration.
    """
    # Basic information
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, db_index=True)
    secondary_phone = models.CharField(max_length=32, blank=True)
    
    # Address information
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    occupation = models.CharField(max_length=100, blank=True)
    
    # Laravel compatibility
    laravel_parent_id = models.IntegerField(unique=True, null=True, blank=True)
    
    # Link to existing guardian system
    guardian = models.OneToOneField(
        ParentGuardian, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name="laravel_parent"
    )

    class Meta:
        indexes = [
            models.Index(fields=["phone"]),
            models.Index(fields=["laravel_parent_id"]),
            models.Index(fields=["last_name", "first_name"]),
        ]

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    def clean(self):
        super().clean()
        if not self.first_name.strip():
            raise ValidationError("Parent first name is required.")
        if not self.last_name.strip():
            raise ValidationError("Parent last name is required.")
        if not self.phone.strip():
            raise ValidationError("Parent phone is required.")

    def __str__(self) -> str:
        return f"{self.full_name} ({self.phone})"


class StudentLaravelParent(TimeStampedModel):
    """
    Many-to-many relationship between students and Laravel parents.
    Supports multiple parents per student with relationship types and permissions.
    """
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="laravel_parents")
    parent = models.ForeignKey(LaravelParent, on_delete=models.CASCADE, related_name="students")
    relationship = models.CharField(max_length=20, choices=GuardianRelationship.choices, default=GuardianRelationship.GUARDIAN)
    is_primary_contact = models.BooleanField(default=False)
    can_pickup = models.BooleanField(default=True)
    emergency_contact = models.BooleanField(default=False)

    class Meta:
        unique_together = ("student", "parent")
        indexes = [
            models.Index(fields=["student", "is_primary_contact"]),
            models.Index(fields=["parent", "can_pickup"]),
        ]

    def __str__(self) -> str:
        return f"{self.student.get_full_name()} ↔ {self.parent.full_name} ({self.get_relationship_display()})"


class EnrollmentHistory(TimeStampedModel):
    """
    Tracks every class or stream assignment for a student over time.
    A new record is created on:
      - Initial enrolment from admissions
      - Year-end promotion
      - Mid-year class/stream transfer

    The previous assignment is never overwritten — this provides a
    full audit trail of where each student was in any given term/year.
    """
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="enrollment_history"
    )
    academic_year = models.ForeignKey(
        "academics.AcademicYear", on_delete=models.PROTECT, null=True, blank=True,
        help_text="Academic year this assignment applied to.",
    )
    term = models.ForeignKey(
        "academics.Term", on_delete=models.PROTECT, null=True, blank=True,
        help_text="Term this assignment applied to (null for year-long assignments).",
    )
    class_name = models.CharField(max_length=64, db_index=True)
    stream_name = models.CharField(max_length=64, blank=True)
    # Historical ambiguity: rows with action='promoted' created before the 'retained'
    # action value was introduced (prior to Items 1-12 rollout) may represent actual
    # promotions OR retentions recorded under the old CLI script's shared 'promoted'
    # value. This was a deliberate decision, not an oversight — the old script recorded
    # both cases identically, so retroactively distinguishing them is impossible without
    # heuristics. Check the `notes` field for "Repeating" to identify retentions.
    action = models.CharField(
        max_length=20,
        choices=[
            ("enrolled", "Enrolled"),
            ("promoted", "Promoted"),
            ("retained", "Retained"),
            ("transferred", "Transferred"),
            ("graduated", "Graduated"),
        ],
        default="enrolled",
        help_text=(
            "What triggered this enrollment record. "
            "NOTE: Historical rows where action='promoted' may represent retentions "
            "from the old CLI script — the note field should be checked for 'Repeating' "
            "to distinguish actual promotions from retentions prior to this module's launch."
        ),
    )
    enrolled_at = models.DateField(
        auto_now_add=True,
        help_text="Date this enrollment record was created.",
    )
    notes = models.TextField(blank=True, help_text="Optional notes about this enrollment change.")
    progression_case = models.ForeignKey(
        "academics.ProgressionCase", on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Link to the progression case that produced this enrollment action.",
    )

    class Meta:
        verbose_name = "Enrollment History"
        verbose_name_plural = "Enrollment Histories"
        ordering = ["-enrolled_at", "-created_at"]
        indexes = [
            models.Index(fields=["student", "class_name"]),
            models.Index(fields=["student", "academic_year"]),
        ]

    def __str__(self) -> str:
        return f"{self.student.admission_no} → {self.class_name} ({self.get_action_display()})"
