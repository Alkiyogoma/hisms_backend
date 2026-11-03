from django.core.management.base import BaseCommand

from academics.ecd_seed import seed_ecd_templates
from academics.models import ECDItem, ECDReportLayout, ECDSection


class Command(BaseCommand):
    help = "Load the ECD progress-report templates (Pre-K, KG, Pre-School, ABC × Terms 1-3)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true",
            help="Replace existing ECD sections and items with the template content.",
        )

    def handle(self, *args, **options):
        seeded = seed_ecd_templates(ECDReportLayout, ECDSection, ECDItem, reset=options["reset"])
        if seeded:
            self.stdout.write(self.style.SUCCESS(f"Seeded ECD templates: {', '.join(seeded)}"))
        else:
            self.stdout.write("ECD templates already configured; use --reset to reload them.")
