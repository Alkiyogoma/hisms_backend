from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from datetime import datetime, timedelta
import random
import string

from core.models import TimeStampedModel


class AttendanceStatus(models.TextChoices):
    UNCONFIRMED = "unconfirmed", "Unconfirmed"
    PRESENT = "present", "Present"
    ABSENT = "absent", "Absent"
    LATE = "late", "Late"
    EXCUSED = "excused", "Excused"


class AttendanceEntry(TimeStampedModel):
    date = models.DateField(db_index=True)
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="attendance_entries")
    status = models.CharField(
        max_length=16,
        choices=AttendanceStatus.choices,
        default=AttendanceStatus.UNCONFIRMED,
        db_index=True,
    )
    
    check_in_time = models.TimeField(null=True, blank=True)
    check_out_time = models.TimeField(null=True, blank=True)
    is_early_departure = models.BooleanField(default=False)

    marked_by = models.ForeignKey("users.User", on_delete=models.PROTECT, related_name="attendance_marked")
    marked_at = models.DateTimeField(default=timezone.now)

    # Corrections require an authorised user and a reason.
    corrected_by = models.ForeignKey(
        "users.User", on_delete=models.PROTECT, null=True, blank=True, related_name="attendance_corrected"
    )
    corrected_at = models.DateTimeField(null=True, blank=True)
    correction_reason = models.TextField(blank=True)
    original_status = models.CharField(
        max_length=16,
        choices=AttendanceStatus.choices,
        null=True, blank=True,
        help_text="Original status before correction (preserved for audit trail).",
    )

    class_name = models.CharField(max_length=64, db_index=True)

    # Laravel compatibility fields
    laravel_attendance_id = models.IntegerField(null=True, blank=True, unique=True)
    checkout_by = models.ForeignKey(
        "users.User", on_delete=models.PROTECT, null=True, blank=True, related_name="checkout_entries"
    )
    parent_name = models.CharField(max_length=100, blank=True)
    reason = models.TextField(blank=True)

    class Meta:
        unique_together = ("date", "student")
        indexes = [
            models.Index(fields=["date", "class_name"]),
            models.Index(fields=["student", "date"]),
            models.Index(fields=["check_in_time"]),
            models.Index(fields=["laravel_attendance_id"]),
        ]

    def clean(self):
        super().clean()
        if not self.class_name.strip():
            raise ValidationError("class_name is required.")
        if self.corrected_by_id and not (self.correction_reason or "").strip():
            raise ValidationError("correction_reason is required when corrected_by is set.")

    def __str__(self) -> str:
        return f"{self.date} {self.student_id} {self.status}"


class StaffAttendanceEntry(TimeStampedModel):
    date = models.DateField(db_index=True)
    staff = models.ForeignKey("hr.StaffProfile", on_delete=models.CASCADE, related_name="attendance_entries")
    status = models.CharField(
        max_length=16,
        choices=AttendanceStatus.choices,
        default=AttendanceStatus.PRESENT,
        db_index=True,
    )
    check_in_time = models.TimeField(null=True, blank=True)
    check_out_time = models.TimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    
    marked_by = models.ForeignKey("users.User", on_delete=models.PROTECT, related_name="staff_attendance_marked")

    class Meta:
        unique_together = ("date", "staff")
        verbose_name_plural = "Staff Attendance Entries"

    def __str__(self) -> str:
        return f"{self.date} - {self.staff.full_name} - {self.status}"


class OtpCode(TimeStampedModel):
    """
    One-Time Password codes for parent verification during student checkout.
    Integrates with Laravel OTP system for mobile app compatibility.
    """
    parent = models.ForeignKey("students.ParentGuardian", on_delete=models.CASCADE, related_name="otp_codes")
    code = models.CharField(max_length=6, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    verified = models.BooleanField(default=False, db_index=True)
    
    # Laravel compatibility
    laravel_parent_id = models.IntegerField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["parent", "verified"]),
            models.Index(fields=["code", "expires_at"]),
        ]

    def is_expired(self):
        """Check if OTP code has expired"""
        return timezone.now() > self.expires_at

    def is_valid(self):
        """Check if OTP code is valid (not verified and not expired)"""
        return not self.verified and not self.is_expired()

    @classmethod
    def generate_code(cls, parent, expiry_minutes=10):
        """Generate a new 6-digit OTP code for parent"""
        # Invalidate previous codes
        cls.objects.filter(parent=parent, verified=False).update(verified=True)
        
        # Generate new code
        code = ''.join(random.choices(string.digits, k=6))
        expires_at = timezone.now() + timedelta(minutes=expiry_minutes)
        
        return cls.objects.create(
            parent=parent,
            code=code,
            expires_at=expires_at
        )

    def __str__(self) -> str:
        return f"OTP {self.code} for {self.parent.full_name}"


class Message(TimeStampedModel):
    """
    SMS message queue for reliable delivery with retry mechanism.
    Compatible with Laravel message system and supports multiple SMS providers.
    """
    DELIVERY_STATUS = [
        (0, 'Pending'),
        (1, 'Sent'),
        (2, 'Failed'),
        (3, 'Retry'),
    ]

    phone = models.CharField(max_length=20, db_index=True)
    message = models.TextField()
    status = models.IntegerField(choices=DELIVERY_STATUS, default=0, db_index=True)
    
    # Delivery tracking
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0)
    
    # Laravel compatibility
    laravel_message_id = models.IntegerField(null=True, blank=True, unique=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["phone", "status"]),
        ]

    def mark_sent(self):
        """Mark message as successfully sent"""
        self.status = 1
        self.sent_at = timezone.now()
        self.save(update_fields=['status', 'sent_at'])

    def mark_failed(self, error_msg=""):
        """Mark message as failed with optional error message"""
        self.status = 2
        self.error_message = error_msg
        self.retry_count += 1
        self.save(update_fields=['status', 'error_message', 'retry_count'])

    def mark_retry(self):
        """Mark message for retry"""
        self.status = 3
        self.save(update_fields=['status'])

    def __str__(self) -> str:
        return f"SMS to {self.phone} - {self.get_status_display()}"


class NotificationLog(TimeStampedModel):
    """
    Comprehensive logging for all notification attempts including SMS and email.
    Tracks delivery status and provides audit trail for parent communications.
    """
    NOTIFICATION_TYPES = [
        ('checkin', 'Check-in'),
        ('checkout', 'Check-out'),
        ('otp', 'OTP Code'),
        ('absence', 'Absence Alert'),
        ('general', 'General'),
    ]
    
    DELIVERY_STATUS = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
        ('retry', 'Retry'),
    ]

    attendance_entry = models.ForeignKey(
        'AttendanceEntry', 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True,
        related_name="notification_logs"
    )
    otp_code = models.ForeignKey(
        'OtpCode', 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True,
        related_name="notification_logs"
    )
    
    recipient_phone = models.CharField(max_length=20, blank=True)
    recipient_email = models.EmailField(blank=True)
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)
    message = models.TextField()
    
    delivery_status = models.CharField(max_length=20, choices=DELIVERY_STATUS, default='pending')
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["notification_type", "delivery_status"]),
            models.Index(fields=["recipient_phone", "created_at"]),
            models.Index(fields=["attendance_entry", "notification_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_notification_type_display()} - {self.get_delivery_status_display()}"
