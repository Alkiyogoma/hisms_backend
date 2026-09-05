from django.conf import settings
from django.db import models
from core.models import TimeStampedModel
from core.utils import get_client_ip


class AuditLogQuerySet(models.QuerySet):
    """
    FRD SA-004: Immutable audit log queryset.

    Blocks ALL delete and update operations at the QuerySet level to
    prevent bypassing the model-level delete() override via:
    - AuditLog.objects.filter(...).delete()
    - AuditLog.objects.filter(...).update(...)
    - AuditLog.objects.all().delete()
    """

    def delete(self):
        raise RuntimeError("Audit logs are immutable and cannot be deleted.")

    def update(self, **kwargs):
        raise RuntimeError("Audit logs are immutable and cannot be updated.")


class AuditLogManager(models.Manager):
    """Manager that returns the immutable QuerySet."""

    def get_queryset(self):
        return AuditLogQuerySet(self.model, using=self._db)


class AuditLog(TimeStampedModel):
    objects = AuditLogManager()

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, db_constraint=getattr(settings, "AUDIT_LOG_DB_CONSTRAINT", True), related_name="audit_entries")
    action_type = models.CharField(max_length=80)
    model_name = models.CharField(max_length=120)
    object_id = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)  # Human-readable summary
    before_snapshot = models.JSONField(null=True, blank=True)
    after_snapshot = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def delete(self, *args, **kwargs):
        raise RuntimeError("Audit logs are immutable and cannot be deleted.")

    def save(self, *args, **kwargs):
        if self.pk:
            raise RuntimeError("Audit logs are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.action_type} {self.model_name}#{self.object_id}"

def log_event(actor, action_type, model_name, object_id="", description="", before=None, after=None, request=None, before_value=None, after_value=None):
    ip = get_client_ip(request)

    # Normalize kwargs vs old implementations
    before_snapshot = before
    if before_value is not None: before_snapshot = {"value": before_value}
    
    after_snapshot = after
    if after_value is not None: after_snapshot = {"value": after_value}

    return AuditLog.objects.create(
        actor=actor,
        action_type=action_type,
        model_name=model_name,
        object_id=str(object_id),
        description=description,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        ip_address=ip
    )


class DSARRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    EXPIRED = "expired", "Expired (timeout)"


class DSARRequest(TimeStampedModel):
    """
    NFR-PDPA-003: Tracks every Data Subject Access Request (DSAR) export.

    Records who requested the export, when, what was exported, the format,
    whether it completed within the 30-minute SLA, and which modules were
    included in the export.
    """
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="dsar_requests",
    )
    target_type = models.CharField(
        max_length=16,
        choices=[("student", "Student"), ("guardian", "Guardian")],
    )
    target_id = models.PositiveIntegerField()
    target_label = models.CharField(
        max_length=200,
        help_text="Human-readable label for the data subject (e.g. name).",
    )
    export_format = models.CharField(
        max_length=8,
        choices=[("json", "JSON"), ("csv", "CSV"), ("xlsx", "XLSX")],
        default="json",
    )
    status = models.CharField(
        max_length=16,
        choices=DSARRequestStatus.choices,
        default=DSARRequestStatus.PENDING,
        db_index=True,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(
        null=True,
        blank=True,
        help_text="Elapsed time in seconds from start to completion.",
    )
    modules_included = models.JSONField(
        default=list,
        blank=True,
        help_text="List of data modules included in the export.",
    )
    modules_expected = models.JSONField(
        default=list,
        blank=True,
        help_text="Full list of modules that should be in a complete DSAR export.",
    )
    within_sla = models.BooleanField(
        null=True,
        blank=True,
        help_text="True if export completed within 30 minutes.",
    )
    error_message = models.TextField(
        blank=True,
        help_text="Error details if the export failed.",
    )
    file_size_bytes = models.PositiveIntegerField(
        null=True,
        blank=True,
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "DSAR Request"
        verbose_name_plural = "DSAR Requests"

    def __str__(self) -> str:
        return f"DSAR {self.target_type}#{self.target_id} — {self.status} ({self.export_format})"

    @property
    def is_within_sla(self):
        """Check if the export completed within the 30-minute SLA."""
        if self.duration_seconds is None:
            return None
        return self.duration_seconds <= 1800  # 30 minutes = 1800 seconds


# Expected modules for a complete DSAR export (NFR-PDPA-003)
DSAR_EXPECTED_MODULES = [
    "identity",
    "medical",
    "guardians",
    "siblings",
    "attendance",
    "finances",
    "academics",
    "discipline",
    "welfare",
    "admissions",
    "audit_trail",
    "notifications",
    "pdpa_consent",
]

