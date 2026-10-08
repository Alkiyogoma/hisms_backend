from django.conf import settings
from django.db import models
from core.models import TimeStampedModel
from students.models import Student


class IncidentSeverity(models.TextChoices):
    # One scale: the severity is the highest incident level ticked.
    LOW = "low", "Level 1 — Minor"
    MEDIUM = "medium", "Level 2 — Moderate"
    HIGH = "high", "Level 3 — Serious"
    CRITICAL = "critical", "Level 4 — Severe"


LEVEL_SEVERITY = {
    1: IncidentSeverity.LOW,
    2: IncidentSeverity.MEDIUM,
    3: IncidentSeverity.HIGH,
    4: IncidentSeverity.CRITICAL,
}


def severity_for_levels(levels):
    """Severity for the incident levels ticked: the highest level, else Level 1."""
    return LEVEL_SEVERITY[max(levels, default=1)]


# The behaviours offered at each level, and the actions the school can record.
INCIDENT_BEHAVIOURS = {
    1: ["Talking out of turn", "Homework not done", "Lateness to class", "Uniform untidy",
        "Minor disruption", "Mild disrespect", "Left class no perm"],
    2: ["Repeated Level 1", "Rudeness to staff", "Verbal bullying", "Cheating/Dishonesty",
        "Minor property damage", "Lying to staff", "Unkind rumours"],
    3: ["Persistent bullying", "Physical aggression", "Theft", "Deliberate vandalism",
        "Prohibited items", "Sustained defiance", "Inappropriate lang"],
    4: ["Assault w/ injury", "Sexual harassment", "Possess weapons", "Possess substances",
        "Threatening staff", "Serious theft/fraud", "Repeat Level 3"],
}
SCHOOL_ACTIONS = [
    "Verbal redirect", "Reflection task", "Merit deduction", "Privilege withdrawal", "Apology letter",
    "Community service", "Restorative circle", "Purposeful detention", "Formal written warning",
    "Referred to HOD", "Referred to HOS", "In-School Suspension", "Out-of-School Suspension",
    "Parent Shadow Day",
]
LOCATIONS = [
    "Classroom", "Playground", "Hallway", "Cafeteria", "Library", "Sports Field",
    "Assembly Hall", "Bus Area", "Off-campus",
]


class ParentContactMethod(models.TextChoices):
    TELEPHONE = "telephone", "By telephone"
    IN_PERSON = "in_person", "In person"
    MESSAGE = "message", "By message"
    EMAIL = "email", "By email"
    LETTER = "letter", "By letter"


class FollowUpKind(models.TextChoices):
    MEETING = "meeting", "Meeting arranged"
    CALL = "call", "Call arranged"
    OTHER = "other", "Follow-up arranged"


class IncidentStatus(models.TextChoices):
    PENDING_REVIEW = "pending_review", "Pending Review"
    UNDER_INVESTIGATION = "under_investigation", "Under Investigation"
    RESOLVED = "resolved", "Resolved"
    DISMISSED = "dismissed", "Dismissed"


class DisciplineIncident(TimeStampedModel):
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="incidents")
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reported_incidents")
    severity = models.CharField(max_length=20, choices=IncidentSeverity.choices)
    summary = models.TextField()
    action_taken = models.TextField(blank=True)
    escalated = models.BooleanField(default=False)

    # Incident date (separate from created_at — allows backdating)
    incident_date = models.DateField(null=True, blank=True, db_index=True)

    # Detailed discipline fields (moved from WelfareObservation)
    time_of_incident = models.TimeField(null=True, blank=True)
    location = models.CharField(max_length=100, blank=True)

    # Store checkbox selections as JSON lists
    incident_level_1 = models.JSONField(default=list, blank=True)
    incident_level_2 = models.JSONField(default=list, blank=True)
    incident_level_3 = models.JSONField(default=list, blank=True)
    incident_level_4 = models.JSONField(default=list, blank=True)
    actions_taken_detailed = models.JSONField(default=list, blank=True)

    # Parent contact & follow-up (moved from WelfareObservation)
    parent_contacted = models.BooleanField(default=False)
    parent_contact_date = models.DateField(null=True, blank=True)
    parent_contact_method = models.CharField(max_length=20, choices=ParentContactMethod.choices, blank=True)
    parent_contact_person = models.CharField(max_length=120, blank=True)
    follow_up_required = models.BooleanField(default=False)
    follow_up_kind = models.CharField(max_length=20, choices=FollowUpKind.choices, blank=True)
    follow_up_details = models.CharField(max_length=255, blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    follow_up_time = models.TimeField(null=True, blank=True)

    # Parent digital confirmation
    parent_confirmed = models.BooleanField(default=False)
    parent_confirmation_date = models.DateTimeField(null=True, blank=True)

    # HOD review workflow
    status = models.CharField(
        max_length=30,
        choices=IncidentStatus.choices,
        default=IncidentStatus.PENDING_REVIEW,
        db_index=True,
    )
    hod_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="discipline_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("can_review_incident", "Can review and approve discipline incidents"),
        ]
        indexes = [
            models.Index(fields=["student", "status"]),
            models.Index(fields=["severity", "status"]),
        ]

    @property
    def reference(self) -> str:
        """The record's one printed identifier."""
        return f"DISC-{self.pk}"

    @property
    def occurred_on(self):
        """Date of the incident; records without one fall back to the day it was logged."""
        return self.incident_date or self.created_at.date()

    @property
    def level_groups(self):
        """[(level, behaviours)] for each level with something ticked, lowest first."""
        lists = [self.incident_level_1, self.incident_level_2, self.incident_level_3, self.incident_level_4]
        return [(n, items) for n, items in enumerate(lists, start=1) if items]

    def earlier_incidents(self):
        """Incidents on record for this learner before this one (dismissed ones excluded)."""
        others = (
            DisciplineIncident.objects.filter(student_id=self.student_id)
            .exclude(pk=self.pk)
            .exclude(status=IncidentStatus.DISMISSED)
        )
        key = (self.occurred_on, self.pk)
        return [i for i in others if (i.occurred_on, i.pk) < key]

    def __str__(self) -> str:
        parts = [f"#{self.id} - {self.student} ({self.severity})"]
        if self.location:
            parts.append(f" @ {self.location}")
        if self.time_of_incident:
            parts.append(f" {self.time_of_incident}")
        return "".join(parts)


class IncidentAmendment(models.Model):
    """A correction made to an incident after it was submitted.

    ``changes`` is a list of {"field", "before", "after", "text"} as shown to
    people; ``text`` marks free-text fields, whose old wording is kept on screen
    but not reprinted on the copy sent home."""

    incident = models.ForeignKey(DisciplineIncident, on_delete=models.CASCADE, related_name="amendments")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField()
    changes = models.JSONField(default=list)

    class Meta:
        ordering = ["changed_at", "pk"]

    def __str__(self) -> str:
        return f"{self.incident.reference} amended {self.changed_at:%Y-%m-%d}"
