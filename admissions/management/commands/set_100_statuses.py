"""Management command: set applicants to target statuses for E2E testing."""
from django.core.management.base import BaseCommand
from django.db import transaction
from admissions.models import Applicant, ApplicantStatus


# 22 statuses, 100 applicants total (each count must sum to exactly 100)
TARGETS = {
    "inquiry_received":     10,
    "meeting_scheduled":    5,
    "meeting_completed":    5,
    "assessment_pending":   5,
    "assessment_fee_paid":  5,
    "assessment_confirmed": 5,
    "assessment_completed": 5,
    "report_pending":       5,
    "hos_review":           4,
    "hos_decision":         5,
    "admitted":             5,
    "conditional":          4,
    "denied":               4,
    "enrolled":             4,
    "waitlisted":           4,
    "withdrawn":            4,
    "declined_at_meeting":  4,
    "assessment_failed":    4,
    "form_submitted":       4,
    "invoice_generated":    3,
    "invoice_paid":        3,
    "flagged_for_review":   3,
}

APPLICANT_START_PK = 469  # PKs >= 469 are the 100 seeded applicants


class Command(BaseCommand):
    help = "Set 100 seeded applicants to target statuses across all 22 statuses"

    @transaction.atomic
    def handle(self, *args, **options):
        applicants = list(
            Applicant.objects.filter(pk__gte=APPLICANT_START_PK)
            .order_by("pk")
        )
        if len(applicants) != 100:
            self.stderr.write(f"Expected 100 applicants, found {len(applicants)}")
            return

        counts = {}
        idx = 0
        for status, count in TARGETS.items():
            for _ in range(count):
                a = applicants[idx]
                old = a.status
                a.status = status
                a.save(update_fields=["status", "updated_at"])
                counts[status] = counts.get(status, 0) + 1
                idx += 1

        self.stdout.write(self.style.SUCCESS(f"Updated {idx} applicants to {len(TARGETS)} statuses"))

        # Verify
        from django.db.models import Count
        verify = dict(
            Applicant.objects.filter(pk__gte=APPLICANT_START_PK)
            .values_list("status")
            .annotate(c=Count("id"))
        )
        self.stdout.write("\nFinal distribution:")
        for status, expected in TARGETS.items():
            actual = verify.get(status, 0)
            mark = "OK" if actual == expected else "MISMATCH"
            self.stdout.write(f"  {status:<30s} expected={expected:>3d}  actual={actual:>3d}  {mark}")
        total = sum(verify.values())
        self.stdout.write(f"\n  Total: {total}")
