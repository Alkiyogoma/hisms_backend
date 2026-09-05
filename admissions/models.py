import re
from django.core.exceptions import ValidationError
from django.db import models

from academics.models import Department, GradeClass
from core.models import TimeStampedModel


def _strip_html_tags(value):
    """Remove HTML tags from a string to prevent stored XSS."""
    if not value:
        return value
    return re.sub(r'<[^>]+>', '', str(value)).strip()


class InquiryChannel(models.TextChoices):
    WALK_IN = "walk_in", "Walk-in"
    PHONE_CALL = "phone_call", "Phone call"
    WEBSITE = "website", "Website form"
    SOCIAL = "social", "Social media referral"


class ApplicantStatus(models.TextChoices):
    INQUIRY_RECEIVED = "inquiry_received", "Inquiry received"
    MEETING_SCHEDULED = "meeting_scheduled", "Meeting scheduled"
    ASSESSMENT_PENDING = "assessment_pending", "Assessment pending"
    ASSESSMENT_FEE_PAID = "assessment_fee_paid", "Assessment fee paid"
    ASSESSMENT_CONFIRMED = "assessment_confirmed", "Assessment confirmed"
    ASSESSMENT_COMPLETED = "assessment_completed", "Assessment completed"
    HOS_REVIEW = "hos_review", "HOS review"
    HOD_REVIEW = "hod_review", "HOD review"  # Deprecated alias kept for backward compat
    HOS_DECISION = "hos_decision", "HOS decision"
    ADMITTED = "admitted", "Admitted"
    CONDITIONAL = "conditional", "Conditional"
    DENIED = "denied", "Denied"
    ENROLLED = "enrolled", "Enrolled"
    WAITLISTED = "waitlisted", "Waitlisted"
    WITHDRAWN = "withdrawn", "Withdrawn"
    MEETING_COMPLETED = "meeting_completed", "Meeting completed"
    DECLINED_AT_MEETING = "declined_at_meeting", "Declined at meeting"
    REPORT_PENDING = "report_pending", "Report pending"
    ASSESSMENT_FAILED = "assessment_failed", "Assessment failed"
    FORM_SUBMITTED = "form_submitted", "Admission form submitted"
    INVOICE_GENERATED = "invoice_generated", "Invoice generated"
    INVOICE_PAID = "invoice_paid", "Invoice paid"
    FLAGGED_FOR_REVIEW = "flagged_for_review", "Flagged for review"


# Common schools in Dar es Salaam area — used by get_previous_school_choices()
COMMON_SCHOOLS = [
    "Aga Khan Primary School",
    "Al Muntazir Primary School",
    "Al-Hikmah Boys Secondary School",
    "Anwarite Girls Secondary School",
    "Bagamoyo Secondary School",
    "Barakaelo Primary School",
    "Breath of Life Primary School",
    "Cambridge Early Years",
    "Cape Town International School",
    "Chadani Academy",
    "Cholims Primary School",
    "Citizen Primary School",
    "Clarence International School",
    "Cornerstone Academy",
    "Dar es Salaam Academy",
    "Dar es Salaam Independent School",
    "De la Salle Primary School",
    "Delphi Academy",
    "Dorobo Primary School",
    "Dream Academy",
    "Eaton Primary School",
    "Eden International School",
    "Fairmount Primary School",
    "Feza Primary School",
    "Feza International School",
    "Flamingo Primary School",
    "Fundikira Primary School",
    "Garden Primary School",
    "Gereji Primary School",
    "Globe Academy",
    "Hillcrest International School",
    "Holy Family Primary School",
    "Hope International School",
    "Imani Primary School",
    "International School of Tanganyika",
    "Jaffery Primary School",
    "Jamhuri Primary School",
    "Jangwani Secondary School",
    "Kaduna Primary School",
    "Kawe Primary School",
    "Kilimani Primary School",
    "Kite Primary School",
    "Korogwe Secondary School",
    "Loyola High School",
    "Maarifa Primary School",
    "Maktaba Primary School",
    "Manonyi Primary School",
    "Masaka Primary School",
    "Mashujaa Primary School",
    "Mikocheni Primary School",
    "Mkwawa Primary School",
    "Momentum Primary School",
    "Montessori Primary School",
    "Msimbazi Primary School",
    "Msongola Primary School",
    "Mzizima Secondary School",
    "Nafasi Academy",
    "Nashon Academy",
    "Neema Academy",
    "New Bagamoyo Primary School",
    "Norfolk Primary School",
    "Nyakasungwa Primary School",
    "Omega Primary School",
    "Omary Secondary School",
    "Our Lady of Victory Primary School",
    "Pangani Primary School",
    "Pavacademy",
    "Pugu Secondary School",
    "Rainbow Schools",
    "Rocky Primary School",
    "Royal Primary School",
    "Salama Primary School",
    "Sangara Primary School",
    "Seadco Academy",
    "Shaba Secondary School",
    "Shaaban Robert Secondary School",
    "St. Alban's Primary School",
    "St. Augustine Primary School",
    "St. Christopher's Primary School",
    "St. Francis Primary School",
    "St. Ignatius Primary School",
    "St. Joseph's Primary School",
    "St. Mary's Primary School",
    "St. Patrick's Primary School",
    "St. Peter's Primary School",
    "St. Therese Primary School",
    "Tabata Primary School",
    "Tamia Primary School",
    "Tandahimba Primary School",
    "Tangi primary School",
    "Tembea Primary School",
    "The Little Engineer",
    "TSS Primary School",
    "Uhuru Primary School",
    "Umoja Primary School",
    "United World College East Africa",
    "Valentino Primary School",
    "Wafa Primary School",
    "Wangwa Primary School",
    "Westacre Primary School",
    "Zanaki Primary School",
]


def get_previous_school_choices():
    """Build previous school dropdown with DB-sourced + common schools + Other."""
    choices = [("", "Select previous school...")]
    seen = set()
    # Add schools already in the database
    from django.db.models import CharField
    for school in (
        Applicant.objects.exclude(previous_school__exact="")
        .values_list("previous_school", flat=True)
        .distinct()
        .order_by("previous_school")
    ):
        s = (school or "").strip()
        if s and s.lower() not in seen and s.lower() != "other":
            choices.append((s, s))
            seen.add(s.lower())
    # Add common schools not already present
    for school in COMMON_SCHOOLS:
        if school.lower() not in seen:
            choices.append((school, school))
            seen.add(school.lower())
    choices.append(("__other__", "Other (specify below)"))
    return choices


class AdmissionGrade(TimeStampedModel):
    name = models.CharField(max_length=64, unique=True)
    department = models.CharField(
        max_length=32,
        choices=Department.choices,
        default=Department.PRIMARY,
        db_index=True,
    )
    sort_order = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Applicant(TimeStampedModel):
    reference_number = models.CharField(
        max_length=20, unique=True, blank=True, db_index=True,
        help_text="Auto-generated reference number, e.g. ADM-0001",
    )
    parent_full_name = models.CharField(max_length=150)
    parent_phone = models.CharField(max_length=32)
    parent_email = models.EmailField(blank=True)
    parent_invoice_name = models.CharField(max_length=150, blank=True)
    child_full_name = models.CharField(max_length=150)
    child_date_of_birth = models.DateField()
    grade_applying_for = models.CharField(max_length=32)
    previous_school = models.CharField(max_length=120, blank=True)
    previous_school_other = models.CharField(max_length=120, blank=True, help_text="Free-text when 'Other' is selected")
    sibling_currently_enrolled = models.BooleanField(default=False)
    sibling_details = models.CharField(max_length=255, blank=True)
    inquiry_channel = models.CharField(max_length=20, choices=InquiryChannel.choices, default=InquiryChannel.WALK_IN)
    preferred_meeting_date = models.DateField(null=True, blank=True, help_text="Parent's preferred meeting date from inquiry form")
    preferred_meeting_time = models.TimeField(null=True, blank=True, help_text="Parent's preferred meeting time from inquiry form")
    target_enrollment_year = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Year the parent intends to enroll the child, e.g. 2027")
    target_enrollment_month = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Month (1-12) the parent intends to enroll the child, e.g. 8 for August")

    # FR-ADM-030: Parent/guardian relationship to student
    PARENT_RELATIONSHIP_CHOICES = [
        ("father", "Father"),
        ("Father", "Father"),
        ("mother", "Mother"),
        ("Mother", "Mother"),
        ("guardian", "Guardian"),
        ("Guardian", "Guardian"),
        ("legal_guardian", "Legal guardian"),
        ("Legal guardian", "Legal guardian"),
        ("legal guardian", "Legal guardian"),
        ("other", "Other"),
        ("Other", "Other"),
    ]
    parent_relationship = models.CharField(
        max_length=30,
        choices=PARENT_RELATIONSHIP_CHOICES,
        blank=True,
        default="",
        help_text="Relationship of parent/guardian to the student.",
    )
    status = models.CharField(
        max_length=40,
        choices=ApplicantStatus.choices,
        default=ApplicantStatus.INQUIRY_RECEIVED,
        db_index=True,
    )
    notes = models.TextField(blank=True)
    photo = models.ImageField(upload_to="admissions/photos/", null=True, blank=True)
    
    # FR-ADM-005: Sibling match detection
    sibling_matched_parent = models.ForeignKey(
        "students.ParentGuardian", on_delete=models.SET_NULL, null=True, blank=True, related_name="matched_applicants"
    )
    sibling_link_decision = models.CharField(
        max_length=20,
        blank=True,
        choices=[
            ("linked", "Link to existing parent"),
            ("new", "Create new parent record"),
        ]
    )

    # FR-ADM-028: Conditional admission conditions
    conditional_conditions = models.TextField(
        blank=True,
        help_text="Conditions for conditional admission (recorded when HOS selects 'Conditional')",
    )

    enrolled_student = models.OneToOneField(
        "students.Student",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="from_applicant",
    )

    _SANITIZABLE_FIELDS = (
        'parent_full_name', 'child_full_name', 'parent_phone',
        'parent_email', 'parent_invoice_name', 'previous_school',
        'previous_school_other', 'sibling_details', 'notes',
    )

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["status", "grade_applying_for"]),
            models.Index(fields=["inquiry_channel", "created_at"]),
        ]
        permissions = [
            ("confirm_assessment_fee", "Can confirm assessment fee payment"),
            ("reverse_assessment_fee", "Can reverse assessment fee confirmation"),
            ("send_assessment_logistics", "Can send assessment logistics to parent"),
            ("toggle_admission_document", "Can toggle document received status"),
            ("complete_enrolment", "Can complete student enrolment"),
            ("schedule_assessment", "Can schedule an assessment"),
            ("submit_assessment_result", "Can submit assessment result"),
            ("submit_hos_review", "Can submit HOS review"),
            ("submit_hod_review", "Can submit HOD review"),  # Deprecated alias
            ("edit_admission_note", "Can edit internal admission notes"),
            ("delete_admission_note", "Can delete internal admission notes"),
            ("upload_applicant_photo", "Can upload applicant photo"),
            ("transition_applicant_status", "Can transition applicant status"),
            ("view_assessment_calendar", "Can view assessment calendar"),
        ]

    def save(self, *args, **kwargs):
        # Auto-generate reference number on first save
        if not self.reference_number:
            last = Applicant.objects.order_by("-pk").first()
            next_num = (last.pk + 1) if last else 1
            self.reference_number = f"ADM-{next_num:04d}"
        # Sanitize all text fields before persisting — prevents stored XSS
        # regardless of whether data enters via form, API, or admin.
        for field_name in self._SANITIZABLE_FIELDS:
            val = getattr(self, field_name, None)
            if val:
                setattr(self, field_name, _strip_html_tags(val))
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        # Sanitize text fields to prevent stored XSS
        for field_name in self._SANITIZABLE_FIELDS:
            val = getattr(self, field_name, None)
            if val:
                setattr(self, field_name, _strip_html_tags(val))

        required_values = [
            self.parent_full_name,
            self.parent_phone,
            self.child_full_name,
            self.grade_applying_for,
        ]
        if any(not value.strip() for value in required_values):
            raise ValidationError("Parent, child, and grade fields are mandatory.")

        # If grade master data exists, enforce dropdown-managed value.
        if GradeClass.objects.exists():
            if not GradeClass.objects.filter(name__iexact=self.grade_applying_for.strip()).exists():
                raise ValidationError("Selected grade is not active. Please choose a valid grade from dropdown.")

    def __str__(self):
        return f"{self.child_full_name} ({self.get_status_display()})"


class ApplicantTimelineEntry(TimeStampedModel):
    applicant = models.ForeignKey(Applicant, on_delete=models.CASCADE, related_name="timeline")
    from_status = models.CharField(max_length=40, choices=ApplicantStatus.choices)
    to_status = models.CharField(max_length=40, choices=ApplicantStatus.choices)
    actor = models.ForeignKey("users.User", on_delete=models.PROTECT, related_name="admissions_timeline_entries")
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["applicant", "created_at"]),
        ]

    def __str__(self):
        return f"{self.applicant_id}: {self.from_status} → {self.to_status}"


class ApplicantDocumentType(models.TextChoices):
    BIRTH_CERTIFICATE = "birth_certificate", "Birth certificate"
    CLEARANCE_FORM = "clearance_form", "Clearance form (previous school)"
    ADMISSION_FORM = "admission_form", "Completed admission form"
    FEE_ARRANGEMENT_PROOF = "fee_arrangement_proof", "Proof of fee payment arrangement"


class ApplicantDocumentReceipt(TimeStampedModel):
    applicant = models.ForeignKey(Applicant, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=40, choices=ApplicantDocumentType.choices)
    is_received = models.BooleanField(default=False)
    received_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey("users.User", on_delete=models.PROTECT, null=True, blank=True)
    file = models.FileField(upload_to="admissions/documents/", null=True, blank=True)


    class Meta:
        unique_together = ("applicant", "document_type")
        indexes = [models.Index(fields=["applicant", "document_type"])]

    def __str__(self):
        return f"{self.applicant_id} {self.document_type} received={self.is_received}"


class AssessmentSchedule(TimeStampedModel):
    applicant = models.OneToOneField(Applicant, on_delete=models.CASCADE, related_name="assessment")
    scheduled_date = models.DateField()
    scheduled_time = models.TimeField()
    location = models.CharField(max_length=120)
    facilitating_teacher_name = models.CharField(max_length=120)
    logistics_sent_at = models.DateTimeField(null=True, blank=True)

    # assessment fee tracking (Phase 1: simple confirmation gate)
    assessment_fee_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    assessment_fee_confirmed_paid = models.BooleanField(default=False)
    assessment_fee_confirmed_at = models.DateTimeField(null=True, blank=True)
    assessment_fee_confirmed_by = models.ForeignKey(
        "users.User", on_delete=models.PROTECT, null=True, blank=True, related_name="assessment_fee_confirmations"
    )
    assessment_fee_method = models.CharField(max_length=40, blank=True, help_text="Payment method used for the assessment fee")
    assessment_fee_reference = models.CharField(max_length=120, blank=True, help_text="Payment reference / receipt number")

    # FR-ACAD-010 / FR-ACAD-011: Results and Sign-off
    teacher_comments = models.TextField(blank=True)
    hod_comments = models.TextField(blank=True, help_text="Internal staff comments — never shown to parents")
    parent_facing_comments = models.TextField(blank=True, help_text="Feedback visible to parents (e.g. in offer letter or denial email)")
    result = models.CharField(
        max_length=20,
        blank=True,
        choices=[
            ("recommended", "Recommended"),
            ("not_yet_ready", "Not yet ready"),
            ("needs_support", "Needs support"),
        ]
    )
    hod_signed_off = models.BooleanField(default=False)
    hod_signed_off_at = models.DateTimeField(null=True, blank=True)
    hod_signed_off_by = models.ForeignKey(
        "users.User", on_delete=models.PROTECT, null=True, blank=True, related_name="assessment_hod_signoffs"
    )


    def __str__(self):
        return f"Assessment for {self.applicant_id} on {self.scheduled_date}"


class MeetingSchedule(TimeStampedModel):
    """Stores meeting details for the inquiry stage (inquiry_received -> meeting_scheduled)."""
    applicant = models.OneToOneField(Applicant, on_delete=models.CASCADE, related_name="meeting")
    meeting_date = models.DateField()
    meeting_time = models.TimeField()
    location = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"Meeting for {self.applicant_id} on {self.meeting_date}"


class ApplicantInternalNote(TimeStampedModel):
    """FR-ADM-029: Internal notes on applicant records.

    Notes are date-stamped, attributed to the author with name and role,
    and are NEVER visible to parents under any circumstances.
    """
    applicant = models.ForeignKey(Applicant, on_delete=models.CASCADE, related_name="internal_notes")
    author = models.ForeignKey("users.User", on_delete=models.PROTECT, related_name="applicant_internal_notes")
    body = models.TextField()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["applicant", "created_at"]),
        ]

    def __str__(self):
        return f"Note by {self.author} on {self.applicant}"


class EnrolmentChecklist(TimeStampedModel):
    applicant = models.OneToOneField(Applicant, on_delete=models.CASCADE, related_name="enrolment_checklist")
    orientation_visit_completed = models.BooleanField(default=False)
    orientation_visit_completed_at = models.DateTimeField(null=True, blank=True)
    orientation_visit_completed_by = models.ForeignKey(
        "users.User", on_delete=models.PROTECT, null=True, blank=True, related_name="orientation_confirmations"
    )

    def __str__(self):
        return f"Enrolment checklist for {self.applicant_id}"

