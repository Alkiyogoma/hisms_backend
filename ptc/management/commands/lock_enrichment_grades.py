"""
Management Command: Lock Enrichment Grades After Term End (FR-PTC-009).

Locks all EnrichmentGrade records for terms that have ended (end_date < today)
so specialist teachers can no longer edit them. Only Super Admin can override
locked grades via the admin interface.

Run via cron or Celery beat: daily at 06:00 EAT.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Lock enrichment grades for terms that have ended (FR-PTC-009)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview which grades would be locked without applying changes",
        )
        parser.add_argument(
            "--term-id",
            type=int,
            default=None,
            help="Lock grades for a specific term ID only (default: all ended terms)",
        )

    def handle(self, *args, **options):
        from academics.models import Term
        from ptc.models import EnrichmentGrade

        dry_run = options["dry_run"]
        term_id = options["term_id"]
        today = timezone.now().date()

        if term_id:
            terms = Term.objects.filter(pk=term_id)
        else:
            terms = Term.objects.filter(end_date__lt=today)

        if not terms.exists():
            self.stdout.write(self.style.WARNING("No eligible terms found."))
            return

        total_locked = 0
        for term in terms:
            qs = EnrichmentGrade.objects.filter(
                term=term,
                is_locked=False,
            )
            count = qs.count()

            if count == 0:
                self.stdout.write(f"No unlocked grades for {term}.")
                continue

            if dry_run:
                self.stdout.write(
                    f"[DRY RUN] Would lock {count} enrichment grade(s) for {term}."
                )
            else:
                qs.update(is_locked=True)
                self.stdout.write(
                    self.style.SUCCESS(f"Locked {count} enrichment grade(s) for {term}.")
                )
                logger.info("Locked %d enrichment grades for %s", count, term)

            total_locked += count

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] Would lock {total_locked} grade(s) across {terms.count()} term(s)."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Locked {total_locked} grade(s) across {terms.count()} term(s)."
                )
            )
