"""
FRD FR-ATT-003 / FR-ATT-006 / §6.1: Auto-mark attendance statuses.

Run via:
    python manage.py auto_mark_attendance

Recommended schedule (via cron / Windows Task Scheduler):
    - 8:35 AM  → marks Late (ECD only) / Present (non-ECD)
    - 4:30 PM  → marks Absent
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import time
from attendance.models import AttendanceEntry, AttendanceStatus
from attendance.services import is_ecd_student


LATE_CUTOFF = time(8, 30)   # FR-ATT-003: after 8:30 AM → Late
ABSENT_CUTOFF = time(16, 30)  # End of school day → Absent


class Command(BaseCommand):
    help = (
        "Auto-mark student attendance per FRD FR-ATT-003.\n"
        "  • Before 8:30 AM  → marks Unconfirmed students as Late (ECD) / Present (non-ECD)\n"
        "  • After 4:30 PM   → marks remaining Unconfirmed students as Absent\n"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without writing to the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        now = timezone.localtime()
        today = now.date()
        current_time = now.time()

        # Fetch all unconfirmed entries for today
        unconfirmed = AttendanceEntry.objects.filter(
            date=today,
            status=AttendanceStatus.UNCONFIRMED,
        )

        if not unconfirmed.exists():
            self.stdout.write(self.style.SUCCESS("No unconfirmed entries for today."))
            return

        late_count = 0
        present_count = 0
        absent_count = 0

        #  Phase 1: Mark Late (ECD only) or Present (non-ECD)
        if current_time >= LATE_CUTOFF:
            late_qs = unconfirmed.filter(
                check_in_time__isnull=True,  # Still no check-in
            )
            if dry_run:
                self.stdout.write(
                    f"[DRY RUN] Would mark {late_qs.count()} students as Late/Present."
                )
            else:
                entries_to_mark = list(late_qs)
                # FR-ATT-003/§6.1: Late only for ECD; non-ECD get Present
                from academics.models import GradeClass, Department
                ecd_class_names = set(
                    GradeClass.objects.filter(department=Department.ECD)
                    .values_list("name", flat=True)
                )
                ecd_entries = [e for e in entries_to_mark if (e.class_name or "") in ecd_class_names]
                non_ecd_entries = [e for e in entries_to_mark if (e.class_name or "") not in ecd_class_names]

                # Mark ECD entries as Late
                for entry in ecd_entries:
                    entry.status = AttendanceStatus.LATE
                    entry.save(update_fields=["status"])
                    try:
                        from audit.models import log_event
                        log_event(
                            actor=None,
                            action_type="ATTENDANCE_AUTO_LATE",
                            model_name="AttendanceEntry",
                            object_id=entry.pk,
                            description=f"Auto-marked Late: {entry.student_id} on {today} (no check-in by 8:30 AM)",
                            before={"status": AttendanceStatus.UNCONFIRMED},
                            after={"status": AttendanceStatus.LATE},
                        )
                    except Exception:
                        pass
                late_count = len(ecd_entries)

                # Mark non-ECD entries as Present (Late not allowed per §6.1)
                for entry in non_ecd_entries:
                    entry.status = AttendanceStatus.PRESENT
                    entry.save(update_fields=["status"])
                    try:
                        from audit.models import log_event
                        log_event(
                            actor=None,
                            action_type="ATTENDANCE_AUTO_PRESENT",
                            model_name="AttendanceEntry",
                            object_id=entry.pk,
                            description=f"Auto-marked Present: {entry.student_id} on {today} (Late restricted to ECD per §6.1)",
                            before={"status": AttendanceStatus.UNCONFIRMED},
                            after={"status": AttendanceStatus.PRESENT},
                        )
                    except Exception:
                        pass
                present_count = len(non_ecd_entries)

                self.stdout.write(
                    self.style.WARNING(f"Marked {late_count} ECD students as Late, {present_count} non-ECD as Present.")
                )

        #  Phase 2: Mark Absent 
        if current_time >= ABSENT_CUTOFF:
            # Re-query — some may have been updated to Late above
            still_unconfirmed = AttendanceEntry.objects.filter(
                date=today,
                status=AttendanceStatus.UNCONFIRMED,
            )
            if dry_run:
                self.stdout.write(
                    f"[DRY RUN] Would mark {still_unconfirmed.count()} students as Absent."
                )
            else:
                entries_to_mark = list(still_unconfirmed)
                count = still_unconfirmed.update(status=AttendanceStatus.ABSENT)
                absent_count = count

                # Audit: log each auto-marked Absent entry
                from audit.models import log_event
                for entry in entries_to_mark:
                    try:
                        log_event(
                            actor=None,
                            action_type="ATTENDANCE_AUTO_ABSENT",
                            model_name="AttendanceEntry",
                            object_id=entry.pk,
                            description=f"Auto-marked Absent: {entry.student_id} on {today} (no check-in by 4:30 PM)",
                            before={"status": AttendanceStatus.UNCONFIRMED},
                            after={"status": AttendanceStatus.ABSENT},
                        )
                    except Exception:
                        pass

                self.stdout.write(
                    self.style.ERROR(f"Marked {count} students as Absent.")
                )

        #  Summary 
        mode = " (DRY RUN)" if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"Auto-mark complete{mode}: {late_count} Late, {present_count} Present, {absent_count} Absent."
            )
        )
