"""
Welfare observations app models — FRD Section 13 (ECD).
"""
from django.conf import settings
from django.db import models
from core.models import TimeStampedModel


class WelfareSeverity(models.TextChoices):
    LOW = "low", "Low (general observation)"
    MEDIUM = "medium", "Medium (requires monitoring)"
    HIGH = "high", "High (HOD follow-up required)"
    CRITICAL = "critical", "Critical (immediate escalation)"


class WelfareConcernType(models.TextChoices):
    BEHAVIORAL = "behavioral", "Behavioral"
    HEALTH = "health", "Health"
    ATTENDANCE = "attendance", "Attendance"
    ACADEMIC = "academic", "Academic"
    HOME_SITUATION = "home_situation", "Home situation"
    OTHER = "other", "Other"


class WelfareNoteType(models.TextChoices):
    POSITIVE = "positive", "Positive"
    OBSERVATION = "observation", "Observation"
    CONCERN = "concern", "Concern"
    SAFEGUARDING = "safeguarding", "Safeguarding"


class WelfareNoteStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"


# Tags offered per note type. A note may carry tags from outside its type's
# list (e.g. after the type is changed); ``type_tag_mismatch`` flags that.
POSITIVE_TAGS = ["Kindness", "Effort", "Achievement", "Leadership", "Creativity", "Friendship", "Growth", "Faith and character"]
CONCERN_TAGS = ["Emotional", "Social", "Learning", "Health", "Attendance", "Home", "Behaviour", "Bullying"]
TAGS_BY_TYPE = {
    WelfareNoteType.POSITIVE: POSITIVE_TAGS,
    WelfareNoteType.OBSERVATION: ["Emotional", "Social", "Learning", "Health", "Attendance", "Home", "Behaviour", "Friendship"],
    WelfareNoteType.CONCERN: CONCERN_TAGS,
    WelfareNoteType.SAFEGUARDING: [],
}
ALL_TAGS = list(dict.fromkeys(tag for tags in TAGS_BY_TYPE.values() for tag in tags))

# Tags that only make sense on a positive note, and tags that signal a
# concern. Friendship/Social/Learning can go either way and never mismatch.
POSITIVE_ONLY_TAGS = {"Kindness", "Effort", "Achievement", "Leadership", "Creativity", "Growth", "Faith and character"}
CONCERN_SIGNAL_TAGS = {"Emotional", "Health", "Attendance", "Home", "Behaviour", "Bullying"}

# First matching tag decides the legacy concern_type column. Health comes
# first so health-tagged notes are always caught by the sensitive-note rules.
_TAG_CONCERN_TYPE = [
    ("Health", "health"),
    ("Attendance", "attendance"),
    ("Home", "home_situation"),
    ("Learning", "academic"),
    ("Behaviour", "behavioral"),
    ("Bullying", "behavioral"),
    ("Emotional", "behavioral"),
    ("Social", "behavioral"),
]


def clean_tags(raw_tags):
    """Keep only known tags, de-duplicated, in the order given."""
    return [t for t in dict.fromkeys(raw_tags or []) if t in ALL_TAGS]


def concern_type_for_tags(tags):
    for tag, concern_type in _TAG_CONCERN_TYPE:
        if tag in tags:
            return concern_type
    return WelfareConcernType.OTHER


def type_tag_mismatch(note_type, tags):
    """Return a user-facing warning when the note type and its tags disagree,
    e.g. a Positive note tagged Health. Empty string when they agree."""
    tags = set(tags or [])
    if note_type == WelfareNoteType.POSITIVE:
        clash = sorted(tags & CONCERN_SIGNAL_TAGS)
        if clash:
            return (
                f"This note is marked Positive but tagged {', '.join(clash)}. "
                "Is it really a positive note?"
            )
    elif note_type in (WelfareNoteType.OBSERVATION, WelfareNoteType.CONCERN):
        clash = sorted(tags & POSITIVE_ONLY_TAGS)
        if clash:
            label = WelfareNoteType(note_type).label
            return (
                f"This note is marked {label} but tagged {', '.join(clash)}. "
                "Should it be a Positive note?"
            )
    return ""


def hod_role_for_class(class_name):
    """Return (UserRole, label) of the HOD responsible for a class."""
    from users.models import UserRole
    from academics.models import Department, GradeClass
    dept = GradeClass.objects.filter(name=class_name).values_list("department", flat=True).first()
    if dept == Department.ECD:
        return UserRole.ECD_HOD, "ECD HOD"
    if dept == Department.LOWER_SECONDARY:
        return UserRole.LOWER_SECONDARY_HOD, "Lower Secondary HOD"
    return UserRole.PRIMARY_HOD, "Primary HOD"


class WelfareAcknowledgment(TimeStampedModel):
    """
    Tracks acknowledgments from HOD and HOS for high/critical welfare observations.
    """
    observation = models.ForeignKey(
        "WelfareObservation", on_delete=models.CASCADE, related_name="acknowledgments"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="welfare_acknowledgments"
    )
    acknowledged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("observation", "user")
        ordering = ["-acknowledged_at"]

    def __str__(self) -> str:
        return f"{self.user} acknowledged {self.observation_id}"


class SubmittedObservationManager(models.Manager):
    """Default manager: submitted, top-level notes only.

    Drafts are private to their author and safeguarding follow-up notes live
    under their parent note, so neither should ever show up in lists,
    dashboards, counts, reports or the parent portal. Code that genuinely
    needs them uses ``WelfareObservation.all_objects``.
    """

    def get_queryset(self):
        return super().get_queryset().filter(
            status=WelfareNoteStatus.SUBMITTED, follow_up_of__isnull=True
        )


class WelfareObservation(TimeStampedModel):
    """
    Welfare note about a learner, written by a teacher.
    Safeguarding notes are locked on submission; other notes can be corrected
    by their author for WELFARE_EDIT_WINDOW_HOURS, and by a Super Admin at any
    time. Every correction is kept in WelfareObservationRevision.
    FRD FR-WEL-001…010
    """
    objects = SubmittedObservationManager()
    all_objects = models.Manager()

    note_type = models.CharField(
        max_length=20, choices=WelfareNoteType.choices, blank=True, db_index=True
    )
    tags = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=10, choices=WelfareNoteStatus.choices,
        default=WelfareNoteStatus.SUBMITTED, db_index=True,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    # A correction to a safeguarding note is its own note pointing here.
    follow_up_of = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="follow_ups"
    )
    last_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="welfare_edited",
    )
    last_edited_at = models.DateTimeField(null=True, blank=True)

    student = models.ForeignKey(
        "students.Student", on_delete=models.PROTECT, related_name="welfare_observations"
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="welfare_submitted"
    )
    concern_type = models.CharField(
        max_length=20, choices=WelfareConcernType.choices, db_index=True
    )
    severity = models.CharField(
        max_length=10, choices=WelfareSeverity.choices, default=WelfareSeverity.LOW, db_index=True
    )
    observation_date = models.DateField(db_index=True)
    observation_text = models.TextField()
    action_taken = models.TextField(blank=True)
    
    # Parent contact tracking
    parent_contacted = models.BooleanField(default=False)
    parent_contact_datetime = models.DateTimeField(null=True, blank=True)
    parent_confirmed = models.BooleanField(default=False)
    parent_confirmation_date = models.DateTimeField(null=True, blank=True)
    
    # Follow-up tracking
    follow_up_required = models.BooleanField(default=False)
    follow_up_date = models.DateField(null=True, blank=True)
    
    is_locked = models.BooleanField(default=False)  # Critical entries lock on submit

    # HOD management
    hod_status = models.CharField(
        max_length=20,
        blank=True,
        choices=[
            ("pending", "Pending review"),
            ("in_progress", "In progress"),
            ("resolved", "Resolved"),
        ],
        default="pending",
    )
    hod_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="welfare_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    
    # Parent signature
    parent_signed = models.BooleanField(default=False, help_text="Whether parent has signed the welfare report")
    parent_signed_at = models.DateTimeField(null=True, blank=True, help_text="When parent signed")
    parent_signature_name = models.CharField(max_length=255, blank=True, help_text="Name of parent who signed")

    class Meta:
        ordering = ["-observation_date", "-created_at"]
        permissions = [
            ("can_review_observation", "Can review and approve welfare observations"),
            ("view_safeguarding_note", "Can view safeguarding notes (a child may be at risk of harm)"),
        ]
        indexes = [
            models.Index(fields=["student", "observation_date"]),
            models.Index(fields=["severity", "hod_status"]),
        ]

    def __str__(self) -> str:
        return f"{self.student_id} | {self.severity} | {self.observation_date}"
    
    # ── Type / state helpers ────────────────────────────────────────────
    @property
    def is_draft(self):
        return self.status == WelfareNoteStatus.DRAFT

    @property
    def is_safeguarding(self):
        return self.note_type == WelfareNoteType.SAFEGUARDING

    @property
    def is_sensitive(self):
        """Concerns and health notes are limited to class teacher, HOD and leadership."""
        return (
            self.note_type == WelfareNoteType.CONCERN
            or self.concern_type == WelfareConcernType.HEALTH
            or "Health" in (self.tags or [])
        )

    @property
    def type_label(self):
        return self.get_note_type_display() or "Observation"

    @property
    def was_edited(self):
        return self.last_edited_at is not None

    @staticmethod
    def edit_window():
        from datetime import timedelta
        return timedelta(hours=getattr(settings, "WELFARE_EDIT_WINDOW_HOURS", 24))

    @property
    def correction_deadline(self):
        start = self.submitted_at or self.created_at
        return start + self.edit_window() if start else None

    def edit_block_reason(self, user):
        """Why ``user`` may not edit this note, or "" if they may.

        - Drafts: author only.
        - Safeguarding notes and follow-ups: never, once submitted.
        - Super Admin: any other note, at any time.
        - Author: within the correction window and before HOD review.
        """
        from django.utils import timezone
        from users.models import UserRole

        is_author = user.pk == self.submitted_by_id
        if self.is_draft:
            return "" if is_author else "Only the author can edit a draft."
        if self.is_safeguarding or self.follow_up_of_id:
            return (
                "Safeguarding notes cannot be edited once submitted. "
                "Add a follow-up note for the Safeguarding Lead instead."
            )
        if user.role == UserRole.SUPER_ADMIN or user.is_superuser:
            return ""
        if not is_author:
            return "Only the teacher who wrote this note can correct it."
        if self.hod_status != "pending":
            return "This note has already been reviewed. Ask a Super Admin to correct it."
        deadline = self.correction_deadline
        if deadline and timezone.now() > deadline:
            hours = int(self.edit_window().total_seconds() // 3600)
            return (
                f"The {hours}-hour correction window has closed. "
                "Ask a Super Admin to correct it."
            )
        return ""

    def can_edit(self, user):
        return not self.edit_block_reason(user)

    def is_hod_acknowledged(self):
        hod_role, _label = hod_role_for_class(self.student.class_name)
        return self.acknowledgments.filter(user__role=hod_role).exists()
    
    def is_hos_acknowledged(self):
        from users.models import UserRole
        return self.acknowledgments.filter(user__role=UserRole.HEAD_OF_SCHOOL).exists()
    
    def is_safeguarding_acknowledged(self):
        """Signed off by someone holding the safeguarding permission."""
        return any(
            ack.user.has_perm("welfare.view_safeguarding_note")
            for ack in self.acknowledgments.select_related("user")
        )

    def is_fully_acknowledged(self):
        if self.is_safeguarding:
            # Safeguarding notes never go to the HOD; a safeguarding sign-off is enough.
            return self.is_safeguarding_acknowledged()
        if self.severity == WelfareSeverity.CRITICAL:
            return self.is_hod_acknowledged() and self.is_hos_acknowledged()
        elif self.severity == WelfareSeverity.HIGH:
            return self.is_hod_acknowledged()
        return True


class WelfareObservationRevision(TimeStampedModel):
    """One correction to a submitted welfare note. ``changes`` maps each
    edited field to {"from": old, "to": new}, so the original wording of the
    note is always recoverable from the first revision."""
    observation = models.ForeignKey(
        WelfareObservation, on_delete=models.CASCADE, related_name="revisions"
    )
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="welfare_revisions"
    )
    reason = models.TextField(blank=True)
    changes = models.JSONField(default=dict)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"Revision of {self.observation_id} by {self.edited_by_id}"
