from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftStatusModel(TimeStampedModel):
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True


class MediaSettings(TimeStampedModel):
    """
    Per-area file upload configuration.
    Admin can set allowed types and max sizes per upload area.
    """
    AREA_CHOICES = [
        ("lesson_plans", "Lesson Plan Attachments"),
        ("student_photos", "Student Photos"),
        ("admission_photos", "Admission Photos"),
        ("admission_documents", "Admission Documents"),
        ("staff_documents", "Staff Documents"),
        ("staff_leave", "Staff Leave Documents"),
        ("profile_photos", "User Profile Photos"),
        ("finance_imports", "Finance Imports (CSV)"),
    ]

    area = models.CharField(max_length=32, choices=AREA_CHOICES, unique=True)
    allowed_extensions = models.CharField(
        max_length=500,
        default="pdf,doc,docx,xls,xlsx,csv,ppt,pptx,jpg,jpeg,png,gif,webp,svg,txt",
        help_text="Comma-separated file extensions (without dots).",
    )
    max_file_size_mb = models.PositiveIntegerField(
        default=10,
        help_text="Maximum single file size in MB.",
    )
    max_total_size_mb = models.PositiveIntegerField(
        default=50,
        help_text="Maximum combined size for all files in one upload (MB).",
    )
    max_files = models.PositiveIntegerField(
        default=5,
        help_text="Maximum number of files per upload.",
    )
    duplicate_check = models.BooleanField(
        default=True,
        help_text="Reject uploads that duplicate an existing file (by name + size).",
    )
    min_width = models.PositiveIntegerField(
        default=0,
        blank=True,
        help_text="Minimum image width in pixels (0 = no limit).",
    )
    min_height = models.PositiveIntegerField(
        default=0,
        blank=True,
        help_text="Minimum image height in pixels (0 = no limit).",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Media Settings"
        verbose_name_plural = "Media Settings"
        ordering = ["area"]

    def __str__(self) -> str:
        return f"{self.get_area_display()} ({self.max_file_size_mb} MB max"

    @property
    def allowed_extensions_set(self):
        return {ext.strip().lower() for ext in self.allowed_extensions.split(",") if ext.strip()}

    @classmethod
    def get_for_area(cls, area):
        """Return the MediaSettings for the given area, creating defaults if needed."""
        obj, created = cls.objects.get_or_create(
            area=area,
            defaults={
                "allowed_extensions": cls._default_extensions(area),
                "max_file_size_mb": cls._default_max_size(area),
                "max_total_size_mb": cls._default_total_size(area),
                "max_files": cls._default_max_files(area),
                "min_width": cls._default_min_width(area),
                "min_height": cls._default_min_height(area),
            },
        )
        return obj

    @staticmethod
    def _default_extensions(area):
        photo_areas = {"student_photos", "admission_photos", "profile_photos"}
        if area in photo_areas:
            return "jpg,jpeg,png,gif,webp"
        if area == "finance_imports":
            return "csv"
        if area in {"admission_documents", "staff_documents", "staff_leave"}:
            return "pdf,doc,docx,jpg,jpeg,png"
        # lesson_plans default
        return "pdf,doc,docx,xls,xlsx,csv,ppt,pptx,jpg,jpeg,png,gif,webp,svg,txt"

    @staticmethod
    def _default_max_size(area):
        photo_areas = {"student_photos", "admission_photos", "profile_photos"}
        if area in photo_areas:
            return 5
        if area == "finance_imports":
            return 10
        return 10

    @staticmethod
    def _default_total_size(area):
        photo_areas = {"student_photos", "admission_photos", "profile_photos"}
        if area in photo_areas:
            return 5
        return 50

    @staticmethod
    def _default_max_files(area):
        if area in {"student_photos", "admission_photos", "profile_photos"}:
            return 1
        if area == "finance_imports":
            return 1
        return 5

    @staticmethod
    def _default_min_width(area):
        if area in {"student_photos", "admission_photos", "profile_photos"}:
            return 100
        return 0

    @staticmethod
    def _default_min_height(area):
        if area in {"student_photos", "admission_photos", "profile_photos"}:
            return 100
        return 0


class SchoolSettings(TimeStampedModel):
    """
    Centralized school settings — FRD Section 14 (HOS/Admin).
    Stores thresholds, API keys, and notification triggers.
    """
    # General
    school_name = models.CharField(max_length=100, default="Hodari Christian School")
    attendance_threshold_warn = models.PositiveIntegerField(default=85, help_text="Attendance % below which a warning is triggered.")
    attendance_threshold_critical = models.PositiveIntegerField(default=75, help_text="Attendance % below which critical alert is triggered.")
    
    # Admissions
    admission_fee = models.DecimalField(max_digits=10, decimal_places=2, default=2500.00)
    assessment_fee = models.DecimalField(max_digits=10, decimal_places=2, default=30000.00, help_text="Assessment fee amount in TSh. Set by finance, not editable by admissions staff.")
    enable_online_inquiry = models.BooleanField(default=True)
    
    # Notifications
    send_absentee_sms = models.BooleanField(default=True, help_text="Automatically send SMS to parents when student is marked absent.")
    sms_api_key = models.CharField(max_length=255, blank=True, null=True)
    sms_sender_id = models.CharField(max_length=20, default="HODARI")
    
    # Academic
    pass_mark = models.PositiveIntegerField(default=50)
    enable_auto_report_generation = models.BooleanField(default=False)

    # Email (SMTP)
    email_backend = models.CharField(
        max_length=255,
        default="django.core.mail.backends.console.EmailBackend",
        help_text="Django email backend. Use 'django.core.mail.backends.smtp.EmailBackend' for real SMTP.",
    )
    email_host = models.CharField(max_length=255, default="smtp.gmail.com")
    email_port = models.PositiveIntegerField(default=587)
    email_use_tls = models.BooleanField(default=True)
    email_host_user = models.CharField(max_length=255, blank=True, default="")
    email_host_password = models.CharField(max_length=255, blank=True, default="")
    default_from_email = models.EmailField(default="noreply@hodari.ac.tz")

    # WhatsApp
    whatsapp_api_key = models.CharField(max_length=255, blank=True, null=True)
    whatsapp_sender_id = models.CharField(max_length=30, blank=True, default="HODARI")

    # Admissions contact details (used in spec-compliant email/SMS templates)
    admissions_phone = models.CharField(
        max_length=32, blank=True, default="+255 700 000 000",
        help_text="Phone number shown in admissions emails and SMS.",
    )
    admissions_email = models.EmailField(
        blank=True, default="admissions@hodari.ac.tz",
        help_text="Reply-to address for admissions emails.",
    )
    admissions_whatsapp = models.CharField(
        max_length=32, blank=True, default="+255 700 000 000",
        help_text="WhatsApp number for proof-of-payment submissions.",
    )

    # PWA (Attendance PWA)
    pwa_domain = models.URLField(
        max_length=255,
        default="https://hodari.nguzo.co.tz:8443",
        help_text="Base URL for the Attendance PWA app. Change this when the domain changes.",
    )
    pwa_version = models.PositiveIntegerField(
        default=1,
        help_text="Increment to force PWA cache refresh for all users.",
    )

    # Login page branding
    login_hero_image = models.ImageField(
        upload_to="branding/login/",
        blank=True,
        null=True,
        help_text="Hero image shown on the left panel of the login page. Recommended: 800x900px.",
    )
    login_staff_hero_image = models.ImageField(
        upload_to="branding/login/",
        blank=True,
        null=True,
        help_text="Hero image shown when Staff tab is active. Falls back to login_hero_image.",
    )
    login_parent_hero_image = models.ImageField(
        upload_to="branding/login/",
        blank=True,
        null=True,
        help_text="Hero image shown when Parent tab is active. Falls back to login_hero_image.",
    )
    login_logo = models.ImageField(
        upload_to="branding/login/",
        blank=True,
        null=True,
        help_text="Logo shown on the login form. Falls back to default school logo if empty.",
    )
    login_heading = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Main heading on the left panel. Default: 'Welcome back to {school_name}'.",
    )
    login_description = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Description text below the heading on the left panel.",
    )
    login_feature1 = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="First feature bullet on the left panel.",
    )
    login_feature2 = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Second feature bullet on the left panel.",
    )
    login_feature3 = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Third feature bullet on the left panel.",
    )
    login_right_heading = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Heading on the right panel (login form). Default: 'Sign in to your account'.",
    )
    login_right_subtitle = models.CharField(
        max_length=300,
        blank=True,
        default="",
        help_text="Subtitle below the heading on the right panel.",
    )
    login_tagline = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Tagline shown below the logo on the left panel.",
    )

    class Meta:
        verbose_name = "School Settings"
        verbose_name_plural = "School Settings"

    def __str__(self) -> str:
        return f"Settings for {self.school_name}"

    @classmethod
    def get_settings(cls):
        obj, created = cls.objects.get_or_create(id=1)
        return obj

    def get_admissions_contact(self):
        """Return admissions contact details from DB settings, with env fallback."""
        import os
        return {
            "phone": self.admissions_phone or os.getenv("ADMISSIONS_PHONE", "+255 700 000 000"),
            "email": self.admissions_email or os.getenv("ADMISSIONS_EMAIL", "admissions@hodari.ac.tz"),
            "whatsapp": self.admissions_whatsapp or os.getenv("ADMISSIONS_WHATSAPP", "+255 700 000 000"),
            "reply_to": self.admissions_email or os.getenv("ADMISSIONS_REPLY_TO", "admissions@hodari.ac.tz"),
        }


class EmailTemplate(TimeStampedModel):
    """
    Dynamic email templates that admins can configure from the settings page.
    Each template stores subject, HTML body, and plain-text body with
    Django template variable support (e.g. {{ user.first_name }}).
    """
    TEMPLATE_TYPES = [
        ("activation", "Staff Account Activation"),
        ("password_reset", "Password Reset"),
        ("onboarding_complete", "Onboarding Complete"),
        ("parent_portal", "Parent Portal Account"),
        ("admission_inquiry", "Admissions Inquiry Acknowledgement"),
        ("admission_meeting", "Admissions Meeting Scheduled"),
        ("admission_denied", "Admissions Application Denied"),
        ("admission_offer_letter", "Admission Offer Letter"),
        ("admission_meeting_rescheduled", "Meeting Rescheduled"),
        ("admission_assessment_logistics", "Assessment Logistics"),
        ("admission_assessment_reminder", "Assessment Reminder"),
        ("admission_fee_invoice", "Admission Fee Invoice"),
        ("admission_document_received", "Document Received"),
        ("admission_waitlist", "Waitlist Notification"),
        ("admission_assessment_fee_reminder", "Assessment Fee Reminder"),
        ("admission_report_reminder", "Report Reminder"),
        ("admission_form_reminder", "Form Reminder"),
        ("admission_invoice_reminder", "Invoice Reminder"),
        ("admission_declined_at_meeting", "Declined at Meeting"),
        ("admission_assessment_fee_invoice", "Assessment Scheduled + Fee Due"),
        ("admission_report_submitted_hos", "Report Submitted to HOS"),
        ("admission_welcome", "Welcome / Enrolment Confirmed"),
        ("admission_meeting_reminder_48h", "Meeting Reminder (48h)"),
        ("admission_meeting_reminder_24h", "Meeting Reminder (24h)"),
        ("admission_hos_review_reminder", "HOS Review Reminder (72h)"),
        ("admission_payment_receipt", "Payment Receipt"),
        ("fee_invoice", "Fee Invoice"),
        ("fee_reminder", "Fee Payment Reminder"),
        ("checkin_notification", "Attendance Check-in Notification"),
        ("checkout_notification", "Attendance Check-out Notification"),
        ("otp_code", "OTP Code"),
        ("broadcast", "Broadcast Message"),
        ("payment_received", "Payment Received Confirmation"),
        ("payment_reversal", "Payment Reversal Notice"),
        ("invoice_generated", "Invoice Generated Notification"),
        ("leave_approved", "Leave Request Approved"),
        ("leave_rejected", "Leave Request Rejected"),
        ("admission_new_inquiry", "New Inquiry (E01)"),
        ("admission_form_outstanding", "Form Outstanding 14d (E17)"),
        ("admission_invoice_outstanding_30d", "Invoice Outstanding 30d (E20)"),
    ]

    template_type = models.CharField(max_length=40, choices=TEMPLATE_TYPES, unique=True)
    name = models.CharField(max_length=100, help_text="Display name for this template.")
    subject = models.CharField(
        max_length=255, default="", blank=True,
        help_text="Email subject line. Supports {{ variable }} syntax.",
    )
    html_body = models.TextField(
        default="", blank=True,
        help_text="HTML email body. Supports Django template syntax.",
    )
    plain_body = models.TextField(
        default="", blank=True,
        help_text="Plain-text fallback body. Supports Django template syntax.",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Disable to suppress this email type entirely.",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="True for seeded default templates.",
    )

    class Meta:
        ordering = ["template_type"]
        verbose_name = "Email Template"
        verbose_name_plural = "Email Templates"

    def __str__(self) -> str:
        return f"{self.get_template_type_display()} ({self.name})"

    @classmethod
    def get_for_type(cls, template_type):
        obj, created = cls.objects.get_or_create(
            template_type=template_type,
            defaults={"name": dict(cls.TEMPLATE_TYPES).get(template_type, template_type)},
        )
        return obj

    def render_html(self, context):
        from django.template import Template, Context
        return Template(self.html_body).render(Context(context))

    def render_plain(self, context):
        from django.template import Template, Context
        return Template(self.plain_body).render(Context(context))

    def render_subject(self, context):
        from django.template import Template, Context
        return Template(self.subject).render(Context(context))


class LessonPlanDeadline(TimeStampedModel):
    """
    Dynamic lesson plan submission deadline configuration.
    Managed by users with 'change_lessonplandeadline' permission via the Role Management UI.
    """
    deadline_day = models.PositiveSmallIntegerField(
        default=0,
        help_text="Day of week deadline (0=Monday, 4=Friday).",
    )
    deadline_time = models.TimeField(
        default="08:00",
        help_text="Time of day the deadline occurs.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Only one deadline config should be active at a time.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="deadline_configs",
    )

    DAY_CHOICES = [
        (0, "Monday"),
        (1, "Tuesday"),
        (2, "Wednesday"),
        (3, "Thursday"),
        (4, "Friday"),
    ]

    class Meta:
        verbose_name = "Lesson Plan Deadline"
        verbose_name_plural = "Lesson Plan Deadlines"
        ordering = ["-is_active", "-created_at"]

    def __str__(self):
        day_name = dict(self.DAY_CHOICES).get(self.deadline_day, "Monday")
        return f"{day_name} {self.deadline_time.strftime('%H:%M')} ({'Active' if self.is_active else 'Inactive'})"

    def clean(self):
        super().clean()
        if self.deadline_day > 4:
            raise ValidationError("Deadline day must be between 0 (Monday) and 4 (Friday).")
        if self.is_active:
            existing = LessonPlanDeadline.objects.filter(is_active=True).exclude(pk=self.pk)
            if existing.exists():
                raise ValidationError("Only one deadline configuration can be active at a time.")

    def save(self, *args, **kwargs):
        if self.is_active:
            LessonPlanDeadline.objects.filter(is_active=True).exclude(pk=self.pk).update(is_active=False)
        self.full_clean()
        super().save(*args, **kwargs)

    @classmethod
    def get_active(cls):
        """Get the currently active deadline configuration."""
        config = cls.objects.filter(is_active=True).first()
        if config:
            return config
        # Fallback to settings
        from django.conf import settings as dj_settings
        day = getattr(dj_settings, "LESSON_PLAN_SUBMISSION_DEADLINE_DAY", 0)
        time_str = getattr(dj_settings, "LESSON_PLAN_SUBMISSION_DEADLINE_TIME", "08:00")
        h, m = map(int, time_str.split(":"))
        from datetime import time as time_type
        return type('DeadlineFallback', (), {
            'deadline_day': day,
            'deadline_time': time_type(h, m),
        })()


class ArchiveRetentionPolicy(TimeStampedModel):
    """
    NFR-DATA-002: Configurable archive retention policy per data category.

    Each row defines the retention period (in years) for a specific data category.
    The manage_archived_records management command and Celery beat task read these
    settings to enforce automatic cleanup of archived records.
    """
    CATEGORY_CHOICES = [
        ("student", "Student Records"),
        ("parent", "Parent / Guardian Records"),
        ("staff", "Staff Records"),
        ("attendance", "Attendance Records"),
        ("finance", "Financial Records"),
        ("welfare", "Welfare Records"),
        ("discipline", "Discipline Records"),
        ("admission", "Admission Records"),
    ]

    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, unique=True)
    retention_years = models.PositiveIntegerField(
        default=7,
        help_text="Number of years to retain archived records before eligible for deletion.",
    )
    warning_days = models.PositiveIntegerField(
        default=90,
        help_text="Days before retention limit to send warning notifications.",
    )
    auto_prune = models.BooleanField(
        default=False,
        help_text="If True, Celery task will automatically delete records past retention. "
                  "If False, only warnings are sent.",
    )
    is_active = models.BooleanField(default=True)
    last_pruned_at = models.DateTimeField(null=True, blank=True)
    last_warning_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["category"]
        verbose_name = "Archive Retention Policy"
        verbose_name_plural = "Archive Retention Policies"

    def __str__(self) -> str:
        return f"{self.get_category_display()} — {self.retention_years}yr retention"

    @classmethod
    def get_for_category(cls, category):
        """Return the policy for a category, creating a default if needed."""
        obj, created = cls.objects.get_or_create(
            category=category,
            defaults={"retention_years": 7, "warning_days": 90},
        )
        return obj
