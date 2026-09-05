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


class WelfareObservation(TimeStampedModel):
    """
    ECD welfare observation submitted by a teacher.
    Critical entries are locked after submission.
    FRD FR-WEL-001…010
    """
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
    parent_signed = models.BooleanField(default=False, help_text="Whether parent/guardian has signed the welfare report")
    parent_signed_at = models.DateTimeField(null=True, blank=True, help_text="When parent/guardian signed")
    parent_signature_name = models.CharField(max_length=255, blank=True, help_text="Name of parent/guardian who signed")

    class Meta:
        ordering = ["-observation_date", "-created_at"]
        permissions = [
            ("can_review_observation", "Can review and approve welfare observations"),
        ]
        indexes = [
            models.Index(fields=["student", "observation_date"]),
            models.Index(fields=["severity", "hod_status"]),
        ]

    def __str__(self) -> str:
        return f"{self.student_id} | {self.severity} | {self.observation_date}"
    
    def is_hod_acknowledged(self):
        from users.models import UserRole
        from academics.models import Department, GradeClass
        student_dept = GradeClass.objects.filter(name=self.student.class_name).values_list("department", flat=True).first()
        hod_role = UserRole.ECD_HOD if student_dept == Department.ECD else UserRole.PRIMARY_HOD
        return self.acknowledgments.filter(user__role=hod_role).exists()
    
    def is_hos_acknowledged(self):
        from users.models import UserRole
        return self.acknowledgments.filter(user__role=UserRole.HEAD_OF_SCHOOL).exists()
    
    def is_fully_acknowledged(self):
        if self.severity == WelfareSeverity.CRITICAL:
            return self.is_hod_acknowledged() and self.is_hos_acknowledged()
        elif self.severity == WelfareSeverity.HIGH:
            return self.is_hod_acknowledged()
        return True
