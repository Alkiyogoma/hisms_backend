from django.db import models
from django.conf import settings
from core.models import TimeStampedModel

class EventCategory(models.TextChoices):
    ACADEMIC = "academic", "Academic"
    SOCIAL = "social", "Social"
    HOLIDAY = "holiday", "Holiday / Public Holiday"
    OTHER = "other", "Other"

class CalendarEvent(TimeStampedModel):
    title = models.CharField(max_length=150)
    category = models.CharField(max_length=20, choices=EventCategory.choices, default=EventCategory.ACADEMIC)
    description = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_public_holiday = models.BooleanField(default=False)
    notify_parents = models.BooleanField(default=True)
    notify_staff = models.BooleanField(default=True)
    is_published = models.BooleanField(default=True)
    is_archived = models.BooleanField(
        default=False,
        help_text="FR-CAL-001: past events are archived rather than deleted",
    )
    reminder_sent = models.BooleanField(default=False)
    parent_acknowledgement_required = models.BooleanField(
        default=False,
        help_text="FR-CAL-001: requires parent acknowledgement when viewing event",
    )
    auto_generated = models.BooleanField(
        default=False,
        help_text="True if created automatically from another model (Term, PTCWindow, etc.)",
    )
    source_model = models.CharField(
        max_length=50, blank=True, default="",
        help_text="Source model identifier: term, ptc, term_exam",
    )
    source_id = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="ID of the source object if auto-generated",
    )

    class Meta:
        ordering = ["start_date"]
        indexes = [
            models.Index(fields=["source_model", "source_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.start_date}: {self.title}"


class EventAcknowledgement(TimeStampedModel):
    event = models.ForeignKey(
        CalendarEvent, on_delete=models.CASCADE, related_name="acknowledgements"
    )
    parent = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_acknowledgements"
    )
    acknowledged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("event", "parent")
        verbose_name = "Event Acknowledgement"
        verbose_name_plural = "Event Acknowledgements"

    def __str__(self) -> str:
        return f"{self.parent} acknowledged {self.event.title}"
