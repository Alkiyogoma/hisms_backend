"""Import staff signatures from the signing kiosk export.

    python manage.py import_signatures /path/to/hodari-signatures-2026-11-19 --by hr@hodari.ac.tz
    python manage.py import_signatures /path/to/export --dry-run

The folder must hold manifest.csv and the png/ files it names. Rows are
matched to users by staff ID (StaffProfile.employee_id), then by email.
Every imported signature waits for the Head of School's approval at
HR → Signatures before it is printed on any report.
"""
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from hr.signatures import import_signature_export


class Command(BaseCommand):
    help = "Import staff signatures (manifest.csv + png/) from the signing kiosk export."

    def add_arguments(self, parser):
        parser.add_argument("folder", help="Export folder containing manifest.csv")
        parser.add_argument("--by", default="", help="Email or username recorded as the importer")
        parser.add_argument("--dry-run", action="store_true", help="Match and validate only; store nothing")

    def handle(self, folder, by="", dry_run=False, **options):
        from users.models import User

        importer = None
        if by:
            importer = User.objects.filter(email__iexact=by).first() or User.objects.filter(username=by).first()
            if importer is None:
                raise CommandError(f"No user '{by}'.")
        try:
            result = import_signature_export(folder, by=importer, dry_run=dry_run)
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages))

        verb = "Would import" if dry_run else "Imported"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(result.imported)} signature(s)."))
        for staff_id, name, how in result.imported:
            self.stdout.write(f"  {staff_id:<8} {name}  (matched by {how})")
        if result.unchanged:
            self.stdout.write(f"Already stored, unchanged: {len(result.unchanged)}")
        if result.unmatched:
            self.stdout.write(self.style.WARNING(f"Not matched to a user: {len(result.unmatched)}"))
            for staff_id, name, email, reason in result.unmatched:
                self.stdout.write(f"  {staff_id:<8} {name} <{email}>: {reason}")
        if result.failed:
            self.stdout.write(self.style.ERROR(f"Failed: {len(result.failed)}"))
            for staff_id, name, reason in result.failed:
                self.stdout.write(f"  {staff_id:<8} {name}: {reason}")
        if result.imported and not dry_run:
            self.stdout.write("Next: the Head of School approves them at HR → Signatures.")
