"""
Management command: sync_from_laravel

Polls the Laravel MySQL database directly for attendance records that have not
yet been synced to Django (fallback sync for when webhook delivery fails).

Two modes:
  --full         Full migration of all historical data (uses DataMigrator)
  --incremental  (default) Sync only records newer than last sync timestamp

Usage:
  python manage.py sync_from_laravel
  python manage.py sync_from_laravel --full
  python manage.py sync_from_laravel --incremental --since=2026-05-01
"""

import logging
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.conf import settings

from attendance.laravel_extractor import LaravelDatabaseExtractor, LaravelFieldMapper
from attendance.data_migrator import DataMigrator, MigrationResult
from attendance.models import AttendanceEntry, AttendanceStatus

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync attendance records from Laravel MySQL database to Django"

    def add_arguments(self, parser):
        parser.add_argument(
            "--full",
            action="store_true",
            help="Run full migration of all historical data (uses DataMigrator)",
        )
        parser.add_argument(
            "--incremental",
            action="store_true",
            help="Sync only records newer than the last sync timestamp (default)",
        )
        parser.add_argument(
            "--since",
            type=str,
            default=None,
            help="ISO date (YYYY-MM-DD) to sync from. Default: 7 days ago",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be synced without making changes",
        )

    def handle(self, *args, **options):
        full = options.get("full", False)
        incremental = options.get("incremental", False)
        since_str = options.get("since")
        dry_run = options.get("dry_run", False)

        if full and incremental:
            raise CommandError("Cannot use --full and --incremental together. Choose one.")

        # Default to incremental
        mode = "incremental"
        if full:
            mode = "full"

        # Parse since date
        if since_str:
            try:
                since_date = datetime.strptime(since_str, "%Y-%m-%d").date()
            except ValueError:
                raise CommandError("--since must be in YYYY-MM-DD format")
        else:
            since_date = timezone.now().date() - timedelta(days=7)

        self.stdout.write(f"Mode: {mode}")
        self.stdout.write(f"Since: {since_date.isoformat()}")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be made"))

        if mode == "full":
            self._run_full_migration(dry_run)
        else:
            self._run_incremental_sync(since_date, dry_run)

    def _run_incremental_sync(self, since_date, dry_run=False):
        """Sync only attendance records newer than since_date."""
        self.stdout.write("Connecting to Laravel database...")

        extractor = LaravelDatabaseExtractor()
        if not extractor.connect():
            self.stdout.write(self.style.ERROR("Failed to connect to Laravel database"))
            return

        try:
            total_synced = 0
            total_skipped = 0
            total_errors = 0
            offset = 0
            batch_size = 500

            self.stdout.write(
                f"Fetching attendance records since {since_date.isoformat()}..."
            )

            while True:
                records = extractor.extract_attendance_records(
                    batch_size=batch_size,
                    offset=offset,
                    date_filter=since_date.isoformat(),
                )

                if not records:
                    break

                for laravel_record in records:
                    try:
                        result = self._sync_single_record(laravel_record, dry_run)
                        if result == "synced":
                            total_synced += 1
                        elif result == "skipped":
                            total_skipped += 1
                        elif result == "error":
                            total_errors += 1
                    except Exception as e:
                        total_errors += 1
                        logger.error(
                            "Error syncing record %s: %s",
                            laravel_record.get("attendance_id", "?"),
                            str(e),
                        )

                offset += batch_size
                self.stdout.write(
                    f"  Processed {offset} records... "
                    f"(synced: {total_synced}, skipped: {total_skipped}, errors: {total_errors})"
                )

            self.stdout.write(
                self.style.SUCCESS(
                    f"\nSync complete: {total_synced} synced, "
                    f"{total_skipped} skipped, {total_errors} errors"
                )
            )

        finally:
            extractor.disconnect()

    def _sync_single_record(self, laravel_record, dry_run=False):
        """Sync a single Laravel attendance record to Django."""
        from students.models import Student

        laravel_id = laravel_record.get("attendance_id")
        student_id = laravel_record.get("student_id")

        if not laravel_id or not student_id:
            return "skipped"

        # Check if already synced
        existing = AttendanceEntry.objects.filter(
            laravel_attendance_id=laravel_id
        ).first()
        if existing:
            return "skipped"

        # Find the Django student
        student = Student.objects.filter(
            laravel_student_id=str(student_id), status="active"
        ).first()
        if not student:
            logger.warning("Student %s not found in Django, skipping", student_id)
            return "skipped"

        if dry_run:
            return "synced"

        # Extract data
        mapped = LaravelFieldMapper.map_attendance_data(laravel_record)

        checkin = laravel_record.get("checkin")
        checkout = laravel_record.get("checkout")
        record_date = (
            checkin.date() if checkin
            else (checkout.date() if checkout
                  else timezone.now().date())
        )

        # Check for existing entry by student + date
        date_entry = AttendanceEntry.objects.filter(
            student=student, date=record_date
        ).first()

        if date_entry:
            # Update existing
            if checkin and not date_entry.check_in_time:
                date_entry.check_in_time = checkin.time()
            if checkout and not date_entry.check_out_time:
                date_entry.check_out_time = checkout.time()
            if laravel_record.get("parent_name"):
                date_entry.parent_name = laravel_record["parent_name"]
            if laravel_record.get("reason"):
                date_entry.reason = laravel_record["reason"]
            date_entry.laravel_attendance_id = laravel_id
            date_entry.save()
        else:
            # Create new
            from users.models import User
            system_user = User.objects.filter(username="laravel_webhook").first()
            if not system_user:
                system_user = User.objects.create_user(
                    username="laravel_webhook",
                    email="webhook@hodari.ac.tz",
                    first_name="Laravel",
                    last_name="Webhook",
                    is_active=True,
                )

            entry_data = {
                "student": student,
                "date": record_date,
                "status": mapped.get("status", AttendanceStatus.PRESENT),
                "class_name": student.class_name or "Unknown",
                "marked_by": system_user,
                "laravel_attendance_id": laravel_id,
                "parent_name": laravel_record.get("parent_name", ""),
                "reason": laravel_record.get("reason", ""),
                "is_early_departure": mapped.get("is_early_departure", False),
            }

            if checkin:
                entry_data["check_in_time"] = checkin.time()
            if checkout:
                entry_data["check_out_time"] = checkout.time()

            AttendanceEntry.objects.create(**entry_data)

        return "synced"

    def _run_full_migration(self, dry_run=False):
        """Run the full DataMigrator for all historical data."""
        self.stdout.write("Running full migration of all Laravel data...")

        extractor = LaravelDatabaseExtractor()
        if not extractor.connect():
            self.stdout.write(self.style.ERROR("Failed to connect to Laravel database"))
            return

        try:
            migrator = DataMigrator(extractor)
            migrator.set_dry_run(dry_run)

            report = migrator.run_full_migration()

            self.stdout.write("\n=== Migration Report ===")
            for operation, result in report.to_dict().get("operation_results", {}).items():
                if isinstance(result, dict):
                    self.stdout.write(
                        f"  {operation}: {result.get('successful', 0)} successful, "
                        f"{result.get('failed', 0)} failed, "
                        f"{result.get('skipped', 0)} skipped"
                    )

        finally:
            extractor.disconnect()
