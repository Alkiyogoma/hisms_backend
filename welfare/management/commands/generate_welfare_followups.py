"""
Management command to auto-generate follow-up reminder notifications
for HODs and teachers on overdue welfare cases.

Usage:
    python manage.py generate_welfare_followups
    python manage.py generate_welfare_followups --dry-run
    python manage.py generate_welfare_followups --date 2026-06-20
"""

from datetime import date
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from academics.models import GradeClass, Department
from communications.email_service import dispatch_notification
from communications.models import Notification
from users.models import User, UserRole
from welfare.models import WelfareObservation, WelfareSeverity


class Command(BaseCommand):
    help = (
        "Scan welfare observations with overdue follow-ups and generate "
        "in-app notifications + Task records for assigned users."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview how many notifications would be created without saving.",
        )
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Check follow-ups due on or before this date (YYYY-MM-DD, defaults to today).",
        )

    def handle(self, **options):
        dry_run = options.get("dry_run", False)
        raw_date = options.get("date")

        target_date: date
        if raw_date:
            try:
                from datetime import date as date_cls
                target_date = date_cls.fromisoformat(raw_date)
            except ValueError:
                raise CommandError("Invalid --date format. Use YYYY-MM-DD.")
        else:
            target_date = timezone.now().date()

        self.stdout.write(
            self.style.NOTICE(
                f"{'[DRY-RUN] ' if dry_run else ''}"
                f"Scanning welfare observations with follow-ups due on or before {target_date}..."
            )
        )

        # ── Query: open observations with overdue follow-ups ─────────────
        overdue_observations = WelfareObservation.objects.filter(
            follow_up_required=True,
            follow_up_date__lte=target_date,
        ).exclude(
            hod_status="resolved",
        ).select_related(
            "student", "submitted_by", "reviewed_by"
        ).order_by("-severity", "-follow_up_date", "-observation_date")

        total = overdue_observations.count()
        self.stdout.write(f"  Found {total} overdue observation(s).\n")

        if total == 0:
            return "No overdue welfare observations found."

        # ── Per-observation processing ───────────────────────────────────
        notifications_created = 0
        tasks_created = 0
        skipped = 0

        for obs in overdue_observations:
            result = self._process_observation(obs, dry_run=dry_run)
            notifications_created += result["notifications"]
            skipped += result["skipped"]

            if not dry_run:
                action = self.style.SUCCESS("✓")
            else:
                action = self.style.WARNING("∼")

            self.stdout.write(
                f"  {action} "
                f"{obs.student.first_name} {obs.student.last_name} "
                f"({obs.get_severity_display()}) "
                f"[follow-up: {obs.follow_up_date}] "
                f"→ {result['msg']}"
            )

        # ── Generate Task records once for ALL overdue observations ──────
        if not dry_run:
            from tasks.services import generate_overdue_welfare_followup_tasks
            task_list = generate_overdue_welfare_followup_tasks()
            tasks_created = len(task_list)

        # ── Summary ──────────────────────────────────────────────────────
        self.stdout.write()
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY-RUN] Would create ~{notifications_created} notification(s) "
                    f"and tasks via the existing service. "
                    f"({skipped} skipped as duplicates.)"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created {notifications_created} notification(s) "
                    f"and {tasks_created} task(s). "
                    f"({skipped} skipped as duplicates.)"
                )
            )

        return (
            f"Processed {total} overdue observations: "
            f"{notifications_created} notifications, {tasks_created} tasks, "
            f"{skipped} skipped (duplicates)."
        )

    # ──────────────────────────────────────────────────────────────────────
    #  Internal helpers
    # ──────────────────────────────────────────────────────────────────────

    def _process_observation(self, obs, *, dry_run=False):
        """
        Create in-app notifications for a single overdue welfare observation.
        Returns dict {notifications, skipped, msg}.

        Task records are created in one batch after all observations have
        been processed (see handle()) to avoid calling the scan-N-times bug.
        """
        notifications = 0
        skipped = 0
        msg_parts = []

        # ── Determine recipients ─────────────────────────────────────────
        assigned = obs.reviewed_by or obs.submitted_by

        gc = GradeClass.objects.filter(name=obs.student.class_name).first()
        dept = gc.department if gc else Department.ECD
        hod_role = UserRole.ECD_HOD if dept == Department.ECD else UserRole.PRIMARY_HOD
        hods = User.objects.filter(role=hod_role, is_active=True)

        # For Critical: also notify HOS
        if obs.severity == WelfareSeverity.CRITICAL:
            hods = list(hods) + list(
                User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True)
            )

        recipients = set()
        if assigned:
            recipients.add(assigned)
        for hod in hods:
            recipients.add(hod)

        # ── Build notification title/message by severity ─────────────────
        sev = obs.severity
        student_name = f"{obs.student.first_name} {obs.student.last_name}"

        if sev == WelfareSeverity.CRITICAL:
            title = f"WARNING: WELFARE OVERDUE — {student_name} (Critical)"
            message = (
                f"CRITICAL welfare observation for {student_name} "
                f"({obs.student.class_name}) requires follow-up.\n"
                f"Concern: {obs.get_concern_type_display()}\n"
                f"Submitted: {obs.observation_date}\n"
                f"Follow-up was due: {obs.follow_up_date}\n"
                f"This requires immediate HOD + HOS acknowledgement."
            )
        elif sev == WelfareSeverity.HIGH:
            title = f"Welfare follow-up overdue — {student_name} (High)"
            message = (
                f"HIGH severity welfare observation for {student_name} "
                f"({obs.student.class_name}) has an overdue follow-up.\n"
                f"Concern: {obs.get_concern_type_display()}\n"
                f"Follow-up was due: {obs.follow_up_date}\n"
                f"Parent contact should be prioritised."
            )
        elif sev == WelfareSeverity.MEDIUM:
            title = f"Follow-up reminder — {student_name} (Medium)"
            message = (
                f"Welfare observation for {student_name} "
                f"({obs.student.class_name}) has a pending follow-up.\n"
                f"Concern: {obs.get_concern_type_display()}\n"
                f"Follow-up was due: {obs.follow_up_date}\n"
                f"3-day parent contact deadline applies."
            )
        else:  # LOW
            title = f"Follow-up reminder — {student_name} (Low)"
            message = (
                f"Welfare observation for {student_name} "
                f"({obs.student.class_name}) has a pending follow-up.\n"
                f"Concern: {obs.get_concern_type_display()}\n"
                f"Follow-up was due: {obs.follow_up_date}\n"
                f"Please review and update the observation."
            )

        link = f"/welfare/{obs.pk}/"

        # ── Create in-app notifications ──────────────────────────────────
        for recipient in recipients:
            if dry_run:
                notifications += 1
                continue

            # Dedup: check if a notification already exists with the same
            # recipient and link (link contains /welfare/{pk}/).
            existing = Notification.objects.filter(
                recipient=recipient,
                link=link,
            ).exists()

            if existing:
                skipped += 1
                continue

            dispatch_notification(
                user=recipient,
                title=title,
                message=message,
                link=link,
            )
            notifications += 1

        if notifications:
            msg_parts.append(f"{'would create ' if dry_run else ''}{notifications} notification(s)")
        if skipped:
            msg_parts.append(f"{skipped} skipped (duplicate)")

        return {
            "notifications": notifications,
            "skipped": skipped,
            "msg": ", ".join(msg_parts) or "no action needed",
        }
