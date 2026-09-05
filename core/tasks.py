"""
Celery Tasks for Core module.

Periodic background tasks for:
- NFR-DATA-002: Archive retention enforcement and warnings
"""

from celery import shared_task
from django.core.management import call_command
import io
import logging

logger = logging.getLogger(__name__)


@shared_task
def check_archive_retention_task():
    """
    NFR-DATA-002: Check archive retention policies and send warnings or prune.

    Runs daily at 02:00 EAT via Celery beat.
    Delegates to the manage_archived_records management command.
    """
    out = io.StringIO()
    try:
        call_command("manage_archived_records", "--report", stdout=out)
        output = out.getvalue().strip()
        logger.info("check_archive_retention_task: %s", output)

        # Notify Super Admins of records approaching retention limit
        _send_retention_warnings()

        return output
    except Exception as exc:
        logger.error("check_archive_retention_task failed: %s", exc)
        raise


@shared_task
def prune_archived_records_task():
    """
    NFR-DATA-002: Prune archived records that have exceeded their retention period.

    Only runs for categories with auto_prune=True in ArchiveRetentionPolicy.
    Runs weekly on Sundays at 01:00 EAT via Celery beat.
    """
    from core.models import ArchiveRetentionPolicy
    from django.utils import timezone
    from datetime import timedelta

    policies = ArchiveRetentionPolicy.objects.filter(is_active=True, auto_prune=True)
    results = []

    for policy in policies:
        try:
            out = io.StringIO()
            # For student records, delegate to the existing management command
            if policy.category == "student":
                call_command(
                    "manage_archived_records",
                    "--prune", "--confirm",
                    stdout=out,
                )
                output = out.getvalue().strip()
                results.append(f"student: {output}")
            else:
                # For other categories, log that auto-prune is configured but
                # the specific management command is not yet implemented
                results.append(
                    f"{policy.category}: auto_prune enabled but category-specific "
                    f"prune command not yet implemented"
                )

            policy.last_pruned_at = timezone.now()
            policy.save(update_fields=["last_pruned_at"])

        except Exception as exc:
            logger.error("prune_archived_records_task failed for %s: %s", policy.category, exc)
            results.append(f"{policy.category}: ERROR - {exc}")

    return "; ".join(results)


def _send_retention_warnings():
    """Send in-app notifications to Super Admins about records approaching retention limits."""
    from core.models import ArchiveRetentionPolicy
    from students.models import Student
    from communications.models import Notification, NotificationCategory
    from users.models import User, UserRole
    from django.utils import timezone
    from datetime import timedelta

    admins = User.objects.filter(role=UserRole.SUPER_ADMIN, is_active=True)
    if not admins.exists():
        return

    for policy in ArchiveRetentionPolicy.objects.filter(is_active=True):
        if policy.category != "student":
            continue  # Extend to other categories as prune commands are added

        cutoff = timezone.now() - timedelta(days=policy.retention_years * 365)
        warning_cutoff = cutoff + timedelta(days=policy.warning_days)

        approaching = Student.objects.filter(
            is_archived=True,
            archived_at__isnull=False,
            archived_at__lte=warning_cutoff,
            archived_at__gt=cutoff,
        ).count()

        overdue = Student.objects.filter(
            is_archived=True,
            archived_at__isnull=False,
            archived_at__lte=cutoff,
        ).count()

        if approaching == 0 and overdue == 0:
            continue

        msg_parts = []
        if approaching > 0:
            msg_parts.append(f"{approaching} record(s) approaching {policy.retention_years}yr limit")
        if overdue > 0:
            msg_parts.append(f"{overdue} record(s) past {policy.retention_years}yr limit")

        body = (
            f"Archive Retention Alert ({policy.get_category_display()}):\n"
            + "; ".join(msg_parts) + ".\n"
            f"Auto-prune: {'Enabled' if policy.auto_prune else 'Disabled (manual action required)'}"
        )

        for admin in admins:
            Notification.objects.create(
                recipient=admin,
                category=NotificationCategory.SYSTEM,
                title=f"Archive Retention Warning — {policy.get_category_display()}",
                body=body,
                link="/audit/retention/",
            )

        policy.last_warning_at = timezone.now()
        policy.save(update_fields=["last_warning_at"])
