from django.conf import settings
from django.db import models
from core.models import TimeStampedModel
from students.models import Student


class IncidentSeverity(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


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
    previous_incidents = models.BooleanField(default=False)

    # Store checkbox selections as JSON lists
    incident_level_1 = models.JSONField(default=list, blank=True)
    incident_level_2 = models.JSONField(default=list, blank=True)
    incident_level_3 = models.JSONField(default=list, blank=True)
    incident_level_4 = models.JSONField(default=list, blank=True)
    actions_taken_detailed = models.JSONField(default=list, blank=True)

    # Parent contact & follow-up (moved from WelfareObservation)
    parent_contacted = models.BooleanField(default=False)
    parent_contact_datetime = models.DateTimeField(null=True, blank=True)
    follow_up_required = models.BooleanField(default=False)
    follow_up_date = models.DateField(null=True, blank=True)

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

    def __str__(self) -> str:
        parts = [f"#{self.id} - {self.student} ({self.severity})"]
        if self.location:
            parts.append(f" @ {self.location}")
        if self.time_of_incident:
            parts.append(f" {self.time_of_incident}")
        return "".join(parts)
