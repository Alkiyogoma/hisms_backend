"""
Management command for year-end student promotion.

Usage:
  python manage.py promote_students --from-year=<year_id> --to-year=<year_id>
  python manage.py promote_students --from-year=1 --to-year=2 --min-average=50 --min-attendance=80

Promotes students from one academic year to the next:
- Students meeting criteria are promoted to the next grade (class).
- Students not meeting criteria repeat the same grade.
- Students in the final grade (highest sort_order) are marked as graduated.

NOTE: Thresholds are read from ProgressionConfig when available.
CLI flags override config values with a warning.
"""
import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from academics.models import AcademicYear, GradeClass, ProgressionConfig
from students.models import Student, StudentStatus, EnrollmentHistory

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Promote students to the next academic year and grade based on criteria."

    def add_arguments(self, parser):
        parser.add_argument(
            "--from-year",
            type=int,
            required=True,
            help="ID of the current academic year (students from this year get promoted).",
        )
        parser.add_argument(
            "--to-year",
            type=int,
            required=True,
            help="ID of the new academic year (students move into this year).",
        )
        parser.add_argument(
            "--min-average",
            type=float,
            default=None,
            help="Minimum overall average score required for promotion (0-100). Overrides ProgressionConfig.",
        )
        parser.add_argument(
            "--min-attendance",
            type=float,
            default=None,
            help="Minimum attendance rate percentage required for promotion (0-100). Overrides ProgressionConfig.",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Actually apply the promotion. Without this flag, runs in dry-run mode.",
        )

    def handle(self, *args, **options):
        from_year_id = options["from_year"]
        to_year_id = options["to_year"]
        commit = options["commit"]

        if from_year_id == to_year_id:
            raise CommandError("from-year and to-year must be different.")

        try:
            from_year = AcademicYear.objects.get(pk=from_year_id)
            to_year = AcademicYear.objects.get(pk=to_year_id)
        except AcademicYear.DoesNotExist as e:
            raise CommandError(str(e))

        # Read from ProgressionConfig when available
        config = ProgressionConfig.objects.filter(
            academic_year_from=from_year, academic_year_to=to_year
        ).first()
        if config:
            min_avg = options.get("min_average") if options["min_average"] is not None else config.minimum_average
            min_att = options.get("min_attendance") if options["min_attendance"] is not None else config.minimum_attendance
            if options.get("min_average") is not None:
                self.stdout.write(self.style.WARNING(
                    f"  --min-average={options['min_average']} overrides config value {config.minimum_average}"
                ))
            if options.get("min_attendance") is not None:
                self.stdout.write(self.style.WARNING(
                    f"  --min-attendance={options['min_attendance']} overrides config value {config.minimum_attendance}"
                ))
            self.stdout.write(self.style.SUCCESS(
                f"  Using ProgressionConfig: avg≥{min_avg}%, att≥{min_att}%"
            ))
        else:
            min_avg = options.get("min_average") or 0.0
            min_att = options.get("min_attendance") or 0.0
            self.stdout.write(self.style.WARNING(
                "  No ProgressionConfig found for this year pair. Using CLI defaults (or 0 if not set)."
            ))

        self.stdout.write(f"Promoting students from {from_year.name} → {to_year.name}")
        self.stdout.write(f"  Minimum average score: {min_avg}%")
        self.stdout.write(f"  Minimum attendance rate: {min_att}%")
        self.stdout.write(f"  Mode: {'LIVE (--commit)' if commit else 'DRY-RUN (no --commit)'}")
        self.stdout.write("")

        # Gather all GradeClasses ordered by sort_order for progression lookup
        grade_order = list(
            GradeClass.objects.filter().order_by("sort_order", "name").values_list("name", flat=True)
        )
        grade_index = {name: i for i, name in enumerate(grade_order)}
        is_final_grade = {name: (i == len(grade_order) - 1) for i, name in enumerate(grade_order)}

        students = Student.objects.filter(
            academic_year=from_year,
            is_archived=False,
            status=StudentStatus.ACTIVE,
        ).order_by("class_name", "last_name")

        total = students.count()
        promoted = 0
        repeated = 0
        graduated = 0
        skipped = 0  # Students whose class_name is not in GradeClass

        self.stdout.write(f"Found {total} active student(s) to evaluate.\n")

        for student in students:
            # Determine if student meets criteria
            meets_criteria = self._check_criteria(student, min_avg, min_att)

            current_class = student.class_name
            current_idx = grade_index.get(current_class)

            if current_idx is None:
                # Student's class_name is not in GradeClass registry — skip
                self.stdout.write(f"  WARNING: {student.admission_no} {student.get_full_name()} — class '{current_class}' not in GradeClass, skipped")
                skipped += 1
                continue

            if meets_criteria:
                if is_final_grade.get(current_class, False):
                    # Final grade — graduate
                    action = "GRADUATED"
                    if commit:
                        student.status = StudentStatus.GRADUATED
                        student.academic_year = to_year
                        student.save(update_fields=["status", "academic_year", "updated_at"])
                        EnrollmentHistory.objects.create(
                            student=student,
                            academic_year=to_year,
                            class_name=current_class,
                            stream_name=student.stream_name,
                            action="graduated",
                        )
                    graduated += 1
                else:
                    # Promote to next grade
                    next_class = grade_order[current_idx + 1]
                    action = f"PROMOTED to {next_class}"
                    if commit:
                        EnrollmentHistory.objects.create(
                            student=student,
                            academic_year=from_year,
                            class_name=student.class_name,
                            stream_name=student.stream_name,
                            action="promoted",
                            notes=f"Promoted from {current_class} to {next_class}",
                        )
                        student.class_name = next_class
                        student.academic_year = to_year
                        student.save(update_fields=["class_name", "academic_year", "updated_at"])
                    promoted += 1
            else:
                # Repeat same grade
                action = f"REPEATING {current_class}"
                if commit:
                    EnrollmentHistory.objects.create(
                        student=student,
                        academic_year=from_year,
                        class_name=student.class_name,
                        stream_name=student.stream_name,
                        action="promoted",
                        notes=f"Repeating {current_class} (did not meet promotion criteria)",
                    )
                    student.academic_year = to_year
                    student.save(update_fields=["academic_year", "updated_at"])
                repeated += 1

            self.stdout.write(f"  {'✓' if commit else '·'} {student.admission_no} {student.get_full_name()} ({current_class}) → {action}")

        # Summary
        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write(f"  Total evaluated:    {total}")
        self.stdout.write(f"  Promoted:           {promoted}")
        self.stdout.write(f"  Repeated:           {repeated}")
        self.stdout.write(f"  Graduated:          {graduated}")
        self.stdout.write(f"  Skipped (no grade): {skipped}")
        self.stdout.write("=" * 60)
        self.stdout.write("")

        if not commit:
            self.stdout.write(
                self.style.WARNING("DRY-RUN complete. Re-run with --commit to apply changes.")
            )
        else:
            self.stdout.write(self.style.SUCCESS("Promotion applied successfully."))

    def _check_criteria(self, student: Student, min_avg: float, min_att: float) -> bool:
        """Check if a student meets the promotion criteria."""
        if min_avg > 0:
            # Check the most recent published report card's average
            from academics.models import ReportCard, ReportCardStatus

            report = (
                ReportCard.objects.filter(
                    student=student, status=ReportCardStatus.PUBLISHED
                )
                .order_by("-term__end_date")
                .first()
            )
            if report and report.overall_average is not None:
                if float(report.overall_average) < min_avg:
                    return False
            elif min_avg > 0:
                # No report card found — can't verify average
                return False

        if min_att > 0:
            rate = student.get_attendance_rate()
            if rate < min_att:
                return False

        return True
