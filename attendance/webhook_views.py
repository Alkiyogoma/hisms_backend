"""
Webhook receiver for Laravel attendance push integration.
Accepts HTTP POST from Laravel when attendance is marked (check-in/check-out)
and syncs it to Django's AttendanceEntry model.

Two-mode architecture:
  - Mode 1 (Webhook / real-time): Laravel pushes after every check-in/check-out
  - Mode 2 (DB Poll / fallback): Django management command polls Laravel MySQL directly
"""

import json
import logging
from datetime import datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from attendance.models import AttendanceEntry, AttendanceStatus
from attendance.services import AttendanceService, QRCodeService
from students.models import Student

logger = logging.getLogger(__name__)
User = get_user_model()


# Shared API token for authenticating webhook calls between Laravel and Django.
# Reads from Django settings (WEBHOOK_API_TOKEN) with a dev-only default.
# In production, set WEBHOOK_API_TOKEN in .env or settings.
_WEBHOOK_TOKEN = getattr(settings, "WEBHOOK_API_TOKEN", None)
if _WEBHOOK_TOKEN:
    WEBHOOK_API_TOKEN = _WEBHOOK_TOKEN
elif settings.DEBUG:
    WEBHOOK_API_TOKEN = "hodari-webhook-token-2026"
else:
    WEBHOOK_API_TOKEN = None
    logger.warning("WEBHOOK_API_TOKEN not set — webhook endpoints will reject all requests")


def _verify_webhook_token(request) -> bool:
    """Verify the webhook Authorization header matches our shared token."""
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return False
    token = auth_header.split(" ", 1)[1]
    return token == WEBHOOK_API_TOKEN


def _get_system_user():
    """Get or create a system user for webhook-triggered attendance records."""
    user = User.objects.filter(username="laravel_webhook").first()
    if not user:
        user = User.objects.create_user(
            username="laravel_webhook",
            email="webhook@hodari.ac.tz",
            first_name="Laravel",
            last_name="Webhook",
            is_active=True,
        )
    return user


def _find_student(student_id: str):
    """Find a Django Student by laravel_student_id or admission_no."""
    student = Student.objects.filter(
        laravel_student_id=str(student_id), status="active"
    ).first()
    if not student:
        student = Student.objects.filter(
            admission_no=str(student_id), status="active"
        ).first()
    return student


@csrf_exempt
@require_http_methods(["POST"])
def webhook_attendance_push(request):
    """
    Webhook endpoint: POST /attendance/webhook/attendance-push/

    Called by Laravel after every check-in or check-out operation.

    --- Request Body ---
    {
        "event": "checkin" | "checkout",
        "student_id": "12345",
        "timestamp": "2026-05-28T08:15:00+03:00",
        "class_id": 3,                    // optional, Laravel class ID
        "parent_name": "John Doe",        // optional, for checkout
        "reason": "Early pickup",          // optional, for checkout
        "checkin_by": "Teacher Name",     // optional
        "checkout_by": "Teacher Name",    // optional
        "laravel_attendance_id": 456      // optional, for dedup
    }

    --- Response ---
    { "status": "ok", "entry_id": 123 }
    or
    { "status": "error", "message": "..." }
    """
    # Verify authentication
    if not _verify_webhook_token(request):
        logger.warning("Webhook: Unauthorized request from %s", request.META.get("REMOTE_ADDR"))
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    event = data.get("event", "").lower()
    student_id = str(data.get("student_id", "")).strip()
    timestamp_str = data.get("timestamp")
    laravel_attendance_id = data.get("laravel_attendance_id")

    # Validate required fields
    if not student_id:
        return JsonResponse(
            {"status": "error", "message": "student_id is required"}, status=400
        )
    if event not in ("checkin", "checkout"):
        return JsonResponse(
            {"status": "error", "message": f"Invalid event: {event}. Must be 'checkin' or 'checkout'."},
            status=400,
        )

    # Parse timestamp
    try:
        if timestamp_str:
            if isinstance(timestamp_str, str):
                ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                if timezone.is_naive(ts):
                    ts = timezone.make_aware(ts)
            else:
                ts = timezone.now()
        else:
            ts = timezone.now()
    except (ValueError, TypeError):
        ts = timezone.now()

    # Backdating prevention: reject timestamps >30 min in the past
    from datetime import timedelta
    max_backdate = timezone.now() - timedelta(minutes=30)
    if ts < max_backdate:
        logger.warning("Webhook: Rejected backdated timestamp for student %s: %s", student_id, ts)
        return JsonResponse(
            {"status": "error", "message": "Timestamp is too far in the past (>30 min)"},
            status=400,
        )

    # Find student
    student = _find_student(student_id)
    if not student:
        logger.error(
            "Webhook: Student %s not found in Django for %s event",
            student_id,
            event,
        )
        return JsonResponse(
            {"status": "error", "message": f"Student {student_id} not found"}, status=404
        )

    today = ts.date()
    system_user = _get_system_user()

    try:
        with transaction.atomic():
            # Check for existing entry for deduplication
            existing = None
            if laravel_attendance_id:
                existing = AttendanceEntry.objects.filter(
                    laravel_attendance_id=laravel_attendance_id
                ).first()

            if not existing:
                existing = AttendanceEntry.objects.filter(
                    student=student, date=today
                ).first()

            if existing:
                # Update existing entry
                if event == "checkin" and not existing.check_in_time:
                    existing.check_in_time = ts.time()
                    existing.marked_by = system_user
                    existing.marked_at = ts
                    # FR-ATT-006: Derive status based on check-in time
                    existing.status = AttendanceService._derive_checkin_status(ts, student=student)
                elif event == "checkout":
                    existing.check_out_time = ts.time()
                    existing.checkout_by = system_user
                    if data.get("parent_name"):
                        existing.parent_name = data.get("parent_name", "").strip()
                    if data.get("reason"):
                        existing.reason = data.get("reason", "").strip()

                if laravel_attendance_id:
                    existing.laravel_attendance_id = laravel_attendance_id

                existing.class_name = student.class_name or existing.class_name
                existing.save()
                entry = existing
                created = False
            else:
                # Create new entry
                entry_data = {
                    "student": student,
                    "date": today,
                    "class_name": student.class_name or "Unknown",
                    "marked_by": system_user,
                    "marked_at": ts,
                }

                if event == "checkin":
                    # FR-ATT-006: Derive status based on check-in time
                    entry_data["status"] = AttendanceService._derive_checkin_status(ts, student=student)
                    entry_data["check_in_time"] = ts.time()
                else:
                    # Checkout without a prior checkin record — set only check_out_time.
                    # The check_in_time will be filled by the DB poll fallback sync.
                    entry_data["status"] = AttendanceStatus.PRESENT
                    entry_data["check_out_time"] = ts.time()
                    entry_data["checkout_by"] = system_user
                    if data.get("parent_name"):
                        entry_data["parent_name"] = data.get("parent_name", "").strip()
                    if data.get("reason"):
                        entry_data["reason"] = data.get("reason", "").strip()

                if laravel_attendance_id:
                    entry_data["laravel_attendance_id"] = laravel_attendance_id

                entry = AttendanceEntry.objects.create(**entry_data)
                created = True

        logger.info(
            "Webhook: %s %s for student %s (entry=%s, created=%s)",
            event,
            "updated" if not created else "created",
            student_id,
            entry.id,
            created,
        )

        return JsonResponse(
            {
                "status": "ok",
                "entry_id": entry.id,
                "event": event,
                "student_id": student_id,
                "created": created,
            }
        )

    except Exception as e:
        logger.error("Webhook: Error processing %s for %s: %s", event, student_id, str(e))
        return JsonResponse(
            {"status": "error", "message": f"Processing error: {str(e)}"}, status=500
        )


@csrf_exempt
@require_http_methods(["GET"])
def webhook_health(request):
    """
    Health check endpoint: GET /attendance/webhook/health/
    Laravel can ping this to verify Django is reachable.
    """
    return JsonResponse(
        {
            "status": "healthy",
            "service": "hodari-django-webhook",
            "timestamp": timezone.now().isoformat(),
        }
    )
