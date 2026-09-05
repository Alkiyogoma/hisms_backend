"""
Celery Tasks for Audit module.

Periodic background tasks for:
- NFR-PDPA-003: DSAR SLA compliance reporting
"""

from celery import shared_task
from django.utils import timezone
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


@shared_task
def check_dsar_sla_compliance_task():
    """
    NFR-PDPA-003: Weekly DSAR SLA compliance report.

    Runs every Monday at 07:00 EAT via Celery beat.
    Notifies Super Admins of any DSAR exports that breached the 30-minute SLA
    in the past 7 days.
    """
    from audit.models import DSARRequest, DSARRequestStatus
    from communications.models import Notification, NotificationCategory
    from users.models import User, UserRole

    week_ago = timezone.now() - timedelta(days=7)

    recent_requests = DSARRequest.objects.filter(created_at__gte=week_ago)
    total = recent_requests.count()
    breaches = recent_requests.filter(within_sla=False).count()
    completed = recent_requests.filter(status=DSARRequestStatus.COMPLETED).count()
    failed = recent_requests.filter(status=DSARRequestStatus.FAILED).count()

    if total == 0:
        return "No DSAR requests in the past 7 days."

    # Find slowest export
    slowest = recent_requests.filter(
        duration_seconds__isnull=False
    ).order_by("-duration_seconds").first()

    admin_users = User.objects.filter(role=UserRole.SUPER_ADMIN, is_active=True)
    body = (
        f"DSAR Compliance Report (past 7 days):\n"
        f"  Total requests: {total}\n"
        f"  Completed: {completed}\n"
        f"  Failed: {failed}\n"
        f"  SLA breaches (>30min): {breaches}\n"
    )
    if slowest:
        body += (
            f"  Slowest export: {slowest.target_type}#{slowest.target_id} "
            f"({slowest.duration_seconds:.0f}s / {slowest.export_format})\n"
        )

    for admin in admin_users:
        Notification.objects.create(
            recipient=admin,
            category=NotificationCategory.SYSTEM,
            title="Weekly DSAR Compliance Report",
            body=body,
            link="/audit/dsar-requests/",
        )

    return f"DSAR compliance report sent: {total} requests, {breaches} SLA breaches."
