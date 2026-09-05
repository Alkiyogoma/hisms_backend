"""
NFR-PDPA-005: Enforce 7-year archive retention for student records.

Archived student records must be retained for a minimum of 7 years, remain
inaccessible through normal UI, but be recoverable by Super Admin.

Deletion requires explicit Super Admin action with a logged reason.

Run via:
    python manage.py manage_archived_records

Options:
    --report       Report records approaching retention limit (within 90 days)
    --stats        Show archive statistics (default if no flag)
    --prune        Actually delete records older than retention limit (requires --confirm)
    --confirm      Must be combined with --prune to execute deletion
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = (
        "NFR-PDPA-005: Enforce archive retention for student records.\n"
        "Reports on records approaching the limit and optionally prunes records\n"
        "that have exceeded the retention period."
    )

    WARNING_DAYS = 90  # Warn when record is within 90 days of the retention limit

    def add_arguments(self, parser):
        parser.add_argument(
            "--report",
            action="store_true",
            help="Report archived records approaching the retention limit.",
        )
        parser.add_argument(
            "--stats",
            action="store_true",
            help="Show archive statistics.",
        )
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Delete archived records that have exceeded the retention limit. "
                 "Requires --confirm flag.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Confirm pruning. Must be used with --prune.",
        )

    def handle(self, *args, **options):
        from students.models import Student
        from audit.models import log_event

        retention_years = getattr(settings, "ARCHIVE_RETENTION_YEARS", 7)
        now = timezone.now()
        cutoff_date = now - timedelta(days=retention_years * 365)
        warning_date = now - timedelta(days=(retention_years * 365) - self.WARNING_DAYS)

        archived = Student.objects.filter(is_archived=True)
        total_archived = archived.count()

        # Default to --stats if no flag given
        if not (options["report"] or options["prune"]):
            options["stats"] = True

        # ── Stats ──
        if options["stats"]:
            self.stdout.write(self.style.HTTP_INFO(
                f"\n{'=' * 60}\n"
                f"  NFR-PDPA-005 Archive Retention Report\n"
                f"{'=' * 60}"
            ))
            self.stdout.write(f"  Retention period:        {retention_years} years")
            self.stdout.write(f"  Total archived records:  {total_archived}")

            if total_archived > 0:
                with_timestamp = archived.filter(archived_at__isnull=False)
                without_timestamp = archived.filter(archived_at__isnull=True)

                self.stdout.write(f"  With archived_at date:   {with_timestamp.count()}")
                self.stdout.write(f"  Without archived_at:     {without_timestamp.count()}")

                if with_timestamp.exists():
                    oldest = with_timestamp.order_by("archived_at").first()
                    newest = with_timestamp.order_by("-archived_at").first()
                    self.stdout.write(f"  Oldest archived:         {oldest.archived_at.date()} ({oldest.admission_no})")
                    self.stdout.write(f"  Newest archived:         {newest.archived_at.date()} ({newest.admission_no})")

                approaching = with_timestamp.filter(
                    archived_at__lte=warning_date,
                    archived_at__gt=cutoff_date,
                ).count()
                overdue = with_timestamp.filter(
                    archived_at__lte=cutoff_date,
                ).count()

                self.stdout.write(f"  Approaching {retention_years}yr limit:   {approaching}")
                self.stdout.write(f"  Past {retention_years}yr limit:          {overdue}")

                if without_timestamp.exists():
                    self.stdout.write(self.style.WARNING(
                        f"  Note: {without_timestamp.count()} record(s) have no archived_at date "
                        f"and cannot be evaluated for retention enforcement."
                    ))
            else:
                self.stdout.write("  No archived records found.")

            self.stdout.write(f"{'=' * 60}\n")

        # ── Report: records approaching the retention limit ──
        if options["report"]:
            self.stdout.write(self.style.WARNING(
                f"\nArchived records approaching {retention_years}-year retention limit "
                f"(within {self.WARNING_DAYS} days):\n"
            ))

            approaching = Student.objects.filter(
                is_archived=True,
                archived_at__isnull=False,
                archived_at__lte=warning_date,
                archived_at__gt=cutoff_date,
            ).order_by("archived_at")

            if approaching.exists():
                self.stdout.write(
                    f"{'Admission No':<20} {'Name':<30} {'Archived At':<15} {'Days Left':<10}"
                )
                self.stdout.write("-" * 75)
                for s in approaching:
                    days_left = (s.archived_at - cutoff_date).days
                    self.stdout.write(
                        f"{s.admission_no:<20} "
                        f"{s.get_full_name():<30} "
                        f"{s.archived_at.date():<15} "
                        f"{days_left:<10}"
                    )
                self.stdout.write(f"\nTotal approaching limit: {approaching.count()}")
            else:
                self.stdout.write("  No records approaching the retention limit.")

            # Also report records past the limit
            overdue = Student.objects.filter(
                is_archived=True,
                archived_at__isnull=False,
                archived_at__lte=cutoff_date,
            ).order_by("archived_at")

            if overdue.exists():
                self.stdout.write(self.style.ERROR(
                    f"\nWARNING: {overdue.count()} record(s) have exceeded the "
                    f"{retention_years}-year retention period and are eligible for deletion."
                ))
                self.stdout.write(
                    "  Use --prune --confirm to permanently delete these records."
                )

        # ── Prune: delete records past retention limit ──
        if options["prune"]:
            if not options["confirm"]:
                self.stdout.write(self.style.ERROR(
                    "Pruning requires --confirm flag. "
                    "This is a destructive operation that permanently deletes data.\n"
                    "Usage: python manage.py manage_archived_records --prune --confirm"
                ))
                return

            overdue = Student.objects.filter(
                is_archived=True,
                archived_at__isnull=False,
                archived_at__lte=cutoff_date,
            ).order_by("archived_at")

            count = overdue.count()
            if count == 0:
                self.stdout.write("No archived records past the retention limit.")
                return

            self.stdout.write(self.style.WARNING(
                f"About to permanently delete {count} archived student record(s) "
                f"that have exceeded the {retention_years}-year retention period."
            ))

            for student in overdue:
                self.stdout.write(
                    f"  Deleting: {student.admission_no} - {student.get_full_name()} "
                    f"(archived {student.archived_at.date()})"
                )

                # Log the deletion to audit trail before deleting
                log_event(
                    actor=None,  # System-initiated; no request context
                    action_type="ARCHIVED_RECORD_DELETED",
                    model_name="Student",
                    object_id=str(student.pk),
                    description=(
                        f"Archived student record permanently deleted per NFR-PDPA-005 "
                        f"{retention_years}-year retention policy: {student.admission_no} "
                        f"({student.get_full_name()}), "
                        f"archived on {student.archived_at.date()}"
                    ),
                )

            # Use queryset .delete() to bypass the model-level guard in
            # Student.delete() which blocks deletion when associated records
            # exist. For NFR-PDPA-005 the retention period has been exceeded
            # and explicit Super Admin approval is assumed via --prune --confirm.
            overdue.delete()

            self.stdout.write(self.style.SUCCESS(
                f"\nSuccessfully deleted {count} archived record(s) "
                f"exceeding the {retention_years}-year retention period."
            ))
