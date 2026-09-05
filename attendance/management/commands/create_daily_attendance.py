"""
FRD FR-ATT-002: Create Unconfirmed attendance entries for all active students.

Run via:
    python manage.py create_daily_attendance

Recommended schedule (daily cron at 7:00 AM):
    0 7 * * 1-5 cd /path/to/hisms_backend && python manage.py create_daily_attendance

Creates one AttendanceEntry per active student for today with status=UNCONFIRMED.
Skips if entries already exist for today (idempotent).
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import date

from attendance.models import AttendanceEntry, AttendanceStatus
from students.models import Student, StudentStatus


class Command(BaseCommand):
    help = (
        "Pre-create UNCONFIRMED attendance entries for all active students today.\n"
        "Idempotent — skips if entries already exist for the given date.\n"
        "Schedule daily at 7:00 AM via cron / Task Scheduler."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Date in YYYY-MM-DD format (default: today).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created without writing to the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # Parse date
        if options["date"]:
            try:
                target_date = date.fromisoformat(options["date"])
            except ValueError:
                self.stdout.write(self.style.ERROR("Invalid date format. Use YYYY-MM-DD."))
                return
        else:
            target_date = timezone.now().date()

        # Skip weekends (Mon-Fri only per FRD)
        if target_date.weekday() >= 5:
            self.stdout.write(
                self.style.SUCCESS(f"Skipping weekend ({target_date} is a {'Saturday' if target_date.weekday() == 5 else 'Sunday'}).")
            )
            return

        # Check if entries already exist (idempotent)
        existing_count = AttendanceEntry.objects.filter(date=target_date).count()
        if existing_count > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{existing_count} attendance entries already exist for {target_date}. Skipping."
                )
            )
            return

        # Get all active students
        active_students = Student.objects.filter(status=StudentStatus.ACTIVE, is_archived=False)

        if not active_students.exists():
            self.stdout.write(self.style.WARNING("No active students found."))
            return

        if dry_run:
            self.stdout.write(
                f"[DRY RUN] Would create {active_students.count()} UNCONFIRMED entries for {target_date}."
            )
            for student in active_students[:10]:
                self.stdout.write(f"  - {student.get_full_name()} ({student.class_name})")
            if active_students.count() > 10:
                self.stdout.write(f"  ... and {active_students.count() - 10} more")
            return

        # Use the system user (first super admin) as marked_by
        from users.models import User, UserRole
        system_user = User.objects.filter(role=UserRole.SUPER_ADMIN).first()
        if not system_user:
            system_user = User.objects.filter(is_superuser=True).first()
        if not system_user:
            self.stdout.write(self.style.ERROR("No system user found. Cannot create attendance entries."))
            return

        # Bulk create UNCONFIRMED entries
        entries = []
        for student in active_students:
            entries.append(
                AttendanceEntry(
                    date=target_date,
                    student=student,
                    status=AttendanceStatus.UNCONFIRMED,
                    class_name=student.class_name,
                    marked_by=system_user,
                )
            )

        AttendanceEntry.objects.bulk_create(entries, batch_size=500)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {len(entries)} UNCONFIRMED attendance entries for {target_date}."
            )
        )
