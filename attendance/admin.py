from django.contrib import admin

from attendance.models import AttendanceEntry, OtpCode, Message, NotificationLog


@admin.register(AttendanceEntry)
class AttendanceEntryAdmin(admin.ModelAdmin):
    list_display = ("date", "student", "class_name", "status", "marked_by", "corrected_by", "check_in_time", "check_out_time")
    list_filter = ("date", "class_name", "status", "is_early_departure")
    search_fields = ("student__admission_no", "student__first_name", "student__last_name", "class_name", "laravel_attendance_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(OtpCode)
class OtpCodeAdmin(admin.ModelAdmin):
    list_display = ("parent", "code", "expires_at", "verified", "created_at")
    list_filter = ("verified", "expires_at", "created_at")
    search_fields = ("parent__full_name", "parent__phone", "code")
    readonly_fields = ("created_at", "updated_at")
    
    def has_change_permission(self, request, obj=None):
        # Prevent editing OTP codes for security
        return False


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("phone", "status", "sent_at", "retry_count", "created_at")
    list_filter = ("status", "sent_at", "created_at")
    search_fields = ("phone", "message")
    readonly_fields = ("created_at", "updated_at", "sent_at")
    
    def get_queryset(self, request):
        return super().get_queryset(request).order_by('-created_at')


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("notification_type", "recipient_phone", "delivery_status", "sent_at", "retry_count")
    list_filter = ("notification_type", "delivery_status", "sent_at", "created_at")
    search_fields = ("recipient_phone", "recipient_email", "message")
    readonly_fields = ("created_at", "updated_at", "sent_at")
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('attendance_entry', 'otp_code').order_by('-created_at')

