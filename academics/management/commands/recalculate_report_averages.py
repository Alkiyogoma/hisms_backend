"""
Recalculate the stored exam average on report cards.

Usage:
  python manage.py recalculate_report_averages --dry-run
  python manage.py recalculate_report_averages [--term=<term_id>]

The average counts approved, mark-bearing subjects only and is cleared
(None) while a term's assessments are incomplete. Run once after deploying
that rule so reports saved under the old calculation (which averaged partial
marks and remark-only subjects) are corrected. ECD reports are skipped.
"""
from django.core.management.base import BaseCommand

from academics.models import ReportCard
from academics.score_progress import exam_summary


class Command(BaseCommand):
    help = "Recalculate stored report-card exam averages from approved, mark-bearing subjects."

    def add_arguments(self, parser):
        parser.add_argument("--term", type=int, help="Only reports for this term id.")
        parser.add_argument("--dry-run", action="store_true", help="Show changes without saving.")

    def handle(self, *args, **options):
        reports = ReportCard.objects.filter(is_ecd_report=False).select_related("student", "term")
        if options["term"]:
            reports = reports.filter(term_id=options["term"])
        changed = 0
        for rc in reports:
            new = exam_summary(rc.student, rc.term)["average"]
            old = rc.overall_average
            if (old is None) == (new is None) and (old is None or round(float(old), 2) == round(new, 2)):
                continue
            changed += 1
            self.stdout.write(
                f"{rc.student.admission_no} {rc.term}: {old if old is not None else '—'} -> "
                f"{new if new is not None else '— (awaiting end of term)'}"
            )
            if not options["dry_run"]:
                rc.overall_average = new
                rc.save(update_fields=["overall_average", "updated_at"])
        verb = "would change" if options["dry_run"] else "updated"
        self.stdout.write(self.style.SUCCESS(f"{changed} report(s) {verb}."))
