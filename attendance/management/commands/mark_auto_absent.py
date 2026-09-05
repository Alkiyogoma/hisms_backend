"""
Management command to auto-mark unconfirmed attendance entries as Absent
at end of the school day.

FRD ATT-005: A student not checked in by end of school day is auto-marked Absent.

Usage:
    python manage.py mark_auto_absent                    # marks today's unconfirmed entries
    python manage.py mark_auto_absent --date 2025-06-01  # marks a specific date
    python manage.py mark_auto_absent --dry-run           # preview without saving

Already scheduled as a Celery Beat periodic task at 16:30 EAT (end of school day)
via config/celery.py → attendance.tasks.mark_auto_absent.
"""

from __future__ import annotations

from datetime import date as date_type

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from attendance.models import AttendanceEntry, AttendanceStatus
from core.utils import is_school_day
from students.models import Student


class Command(BaseCommand):
    help = "Transition unconfirmed attendance entries to Absent at end of school day."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            help="Target date in YYYY-MM-DD format (default: today).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview how many entries would be updated without saving.",
        )

    def handle(self, *args, **options):
        raw_date = options.get("date")
        dry_run = options.get("dry_run", False)

        if raw_date:
            from datetime import date
            try:
                target_date = date.fromisoformat(raw_date)
            except (ValueError, TypeError):
                raise CommandError(
                    f"Invalid --date format '{raw_date}'. Use YYYY-MM-DD."
                )
        else:
            target_date = timezone.localdate()

        # Skip weekends and public holidays
        if not is_school_day(target_date):
            self.stdout.write(
                self.style.WARNING(
                    f"{target_date} is not a school day — skipping."
                )
            )
            return

        self.stdout.write(f"Target date: {target_date}")

        # Find all active students
        students = Student.objects.filter(is_archived=False)

        # Find entries for this date
        entries = AttendanceEntry.objects.filter(date=target_date)
        student_ids_with_entries = set(entries.values_list("student_id", flat=True))

        # Unconfirmed = active students without an entry for the date
        unconfirmed_students = students.exclude(id__in=student_ids_with_entries)

        unconfirmed_count = unconfirmed_students.count()
        self.stdout.write(
            f"Unconfirmed students: {unconfirmed_count} out of {students.count()} total"
        )

        if unconfirmed_count == 0:
            self.stdout.write(self.style.SUCCESS("No unconfirmed entries to mark."))
            return

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY-RUN] Would mark {unconfirmed_count} entries as Absent."
                )
            )
            return

        # We need a system user to mark the attendance
        from users.models import User
        system_user = User.objects.filter(is_superuser=True).first()

        if not system_user:
            raise CommandError(
                "No superuser found. Create a superuser first so auto-absent "
                "can attribute the entries."
            )

        created_count = 0
        for student in unconfirmed_students:
            AttendanceEntry.objects.create(
                date=target_date,
                student=student,
                status=AttendanceStatus.ABSENT,
                marked_by=system_user,
                class_name=student.class_name or "Unknown",
            )
            created_count += 1

        # Absence Alert emails disabled — too many sends hitting Gmail rate limits.
        # Re-enable when email retry queue system is deployed.
        # if created_count:
        #     from communications.email_service import send_parent_notification
        #     from students.models import ParentGuardian
        #     for student in unconfirmed_students:
        #         guardians = ParentGuardian.objects.filter(
        #             studentguardian__student=student, studentguardian__is_primary=True
        #         )
        #         for guardian in guardians:
        #             send_parent_notification(
        #                 guardian=guardian,
        #                 title="Absence Alert (Auto-Marked)",
        #                 message=f"Your child {student.first_name} was marked absent on {target_date} as no check-in was recorded.",
        #                 link="/attendance/",
        #                 actor=system_user,
        #             )

        self.stdout.write(
            self.style.SUCCESS(
                f"Auto-marked {created_count} students as Absent for {target_date}."
            )
        )
