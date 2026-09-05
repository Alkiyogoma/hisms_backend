"""
Communications app models — FRD Section 11.
Handles in-app notifications and broadcast messages.
"""
from django.conf import settings
from django.db import models
from core.models import TimeStampedModel


class NotificationCategory(models.TextChoices):
    ADMISSIONS = "admissions", "Admissions"
    ATTENDANCE = "attendance", "Attendance"
    FINANCE = "finance", "Finance"
    ACADEMIC = "academic", "Academic"
    WELFARE = "welfare", "Welfare"
    SYSTEM = "system", "System"
    BROADCAST = "broadcast", "Broadcast"


class Notification(TimeStampedModel):
    """
    In-app notification for a specific user.
    Created by system triggers or manual broadcast delivery.
    """
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    category = models.CharField(max_length=20, choices=NotificationCategory.choices, default=NotificationCategory.SYSTEM)
    title = models.CharField(max_length=200)
    body = models.TextField()
    link = models.CharField(max_length=255, blank=True)  # optional deep-link
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read"]),
            models.Index(fields=["recipient", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.recipient_id} | {self.title}"

    def save(self, *args, **kwargs):
        # Prevent manual creation of SYSTEM notifications via UI/API.
        # System triggers (middleware, signals) bypass this by using
        # Notification.objects.create() which calls save() but from
        # server-side code, not from user-facing views.
        # The guard blocks creation only when _user_initiated is set by views.
        if (
            self.category == NotificationCategory.SYSTEM
            and not self.pk
            and getattr(self, "_user_initiated", False)
        ):
            from django.core.exceptions import ValidationError
            raise ValidationError("SYSTEM notifications cannot be created manually.")
        super().save(*args, **kwargs)


class BroadcastAudience(models.TextChoices):
    ALL_PARENTS = "all_parents", "All parents"
    GRADE = "grade", "Specific grade"
    INDIVIDUAL = "individual", "Individual parent"
    STAFF = "staff", "All staff"


class BroadcastStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"


class Broadcast(TimeStampedModel):
    """
    Admin-composed broadcast message — FRD FR-COM-001…006.
    """
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="broadcasts"
    )
    audience = models.CharField(max_length=20, choices=BroadcastAudience.choices)
    target_grade = models.CharField(max_length=64, blank=True)  # if audience == GRADE
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="targeted_broadcasts",
    )  # if audience == INDIVIDUAL
    subject = models.CharField(max_length=200)
    body = models.TextField()
    status = models.CharField(max_length=10, choices=BroadcastStatus.choices, default=BroadcastStatus.DRAFT)
    preview_viewed = models.BooleanField(
        default=False,
        help_text="FR-COM-001: set True when user views the preview before sending",
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    recipient_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"[{self.status}] {self.subject}"


class WeeklyFocusStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class WeeklyFocus(TimeStampedModel):
    """
    ECD teacher's weekly communication to parents — FRD FR-COM-007…010.
    Workflow: Draft → Submitted → (Approved / Rejected).
    Only approved entries are published as notifications to class parents.
    """
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="weekly_focuses"
    )
    class_name = models.CharField(max_length=64, db_index=True)
    week_number = models.PositiveSmallIntegerField()
    academic_year = models.CharField(max_length=16)  # e.g. "2025-2026"
    theme = models.CharField(max_length=200)
    planned_activities = models.TextField()
    items_to_bring = models.TextField(
        blank=True, help_text="Any items parents should prepare or bring this week."
    )
    status = models.CharField(
        max_length=20,
        choices=WeeklyFocusStatus.choices,
        default=WeeklyFocusStatus.DRAFT,
        db_index=True,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name="weekly_focus_reviews"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewer_feedback = models.TextField(blank=True)
    is_published = models.BooleanField(default=False, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("class_name", "week_number", "academic_year")
        ordering = ["-academic_year", "-week_number"]
        indexes = [
            models.Index(fields=["class_name", "academic_year"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.class_name} | Week {self.week_number} | {self.theme[:40]}"


class PhoneOTP(TimeStampedModel):
    """One-time codes for parent phone verification (USSD / SMS flows)."""

    phone = models.CharField(max_length=32, db_index=True)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["phone", "expires_at"])]


class EmailSendLogQuerySet(models.QuerySet):
    """Immutable queryset — no delete or update allowed."""
    def delete(self):
        raise RuntimeError("Email send logs are immutable and cannot be deleted.")
    def update(self, **kwargs):
        raise RuntimeError("Email send logs are immutable and cannot be updated.")


class EmailSendLogManager(models.Manager):
    def get_queryset(self):
        return EmailSendLogQuerySet(self.model, using=self._db)


class EmailSendLog(TimeStampedModel):
    """
    OP 5.2 / FR-COM-006: Immutable log of every outbound email attempt.
    Readable by Super Admin and Admin Officer. Cannot be edited or deleted.
    """
    objects = EmailSendLogManager()

    recipient_email = models.EmailField(db_index=True)
    subject = models.CharField(max_length=255)
    success = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)
    action_type = models.CharField(
        max_length=60, blank=True,
        help_text="Trigger event, e.g. ACTIVATION, PASSWORD_RESET, INQUIRY_ACK.",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="email_send_logs",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Email Send Log"
        verbose_name_plural = "Email Send Logs"

    def delete(self, *args, **kwargs):
        raise RuntimeError("Email send logs are immutable and cannot be deleted.")

    def save(self, *args, **kwargs):
        if self.pk:
            raise RuntimeError("Email send logs are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def __str__(self):
        status = "OK" if self.success else "FAIL"
        return f"[{status}] {self.subject} -> {self.recipient_email}"
