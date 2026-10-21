"""
Check, and build, the PDF previews of lesson plan attachments.

    python manage.py lesson_plan_previews            # check the setup and every attachment
    python manage.py lesson_plan_previews --build    # also convert those not yet converted

Run it as the user the web app runs as (e.g. ``sudo -u hodari ...``) so the
LibreOffice and folder checks match what the app sees.
"""
import os
import subprocess
from collections import Counter

from django.core.management.base import BaseCommand

from academics import lesson_plan_files as lpf
from academics.models import LessonPlanAttachment


class Command(BaseCommand):
    help = "Check lesson plan previews: LibreOffice, the preview folder, and each attachment."

    def add_arguments(self, parser):
        parser.add_argument("--build", action="store_true",
                            help="Convert office attachments that have no cached preview yet.")
        parser.add_argument("--limit", type=int, default=0, help="Only the N most recent attachments.")

    def handle(self, *args, build=False, limit=0, **options):
        ok, warn, bad = self.style.SUCCESS, self.style.WARNING, self.style.ERROR

        binary = lpf.converter()
        if binary:
            try:
                version = subprocess.run([binary, "--version"], capture_output=True, timeout=60,
                                         env={**os.environ, "HOME": "/tmp"}).stdout.decode().strip()
            except (OSError, subprocess.TimeoutExpired) as exc:
                version = f"could not run it: {exc}"
            self.stdout.write(ok(f"LibreOffice: {binary} ({version or 'no version output'})"))
        else:
            self.stdout.write(bad(
                "LibreOffice: NOT FOUND - Word/PowerPoint/Excel files cannot be converted on the server.\n"
                "  Install: sudo apt install -y libreoffice-writer-nogui libreoffice-impress-nogui "
                "libreoffice-calc-nogui fonts-dejavu-core\n"
                "  (Word and Excel files still show in the browser's own renderer meanwhile.)"))
        try:
            self.stdout.write(ok(f"Preview folder: {lpf._cache_dir()} (writable)"))
        except lpf.PreviewUnavailable as exc:
            self.stdout.write(bad(f"Preview folder: {exc}"))

        qs = LessonPlanAttachment.objects.select_related("lesson_plan").order_by("-created_at")
        if limit:
            qs = qs[:limit]
        tally = Counter()
        for att in qs:
            kind = lpf.preview_kind(att.filename)
            label = f"#{att.pk} plan {att.lesson_plan_id} {att.filename!r}"
            if not lpf.file_exists(att):
                tally["file missing"] += 1
                self.stdout.write(bad(f"  MISSING  {label}: {att.file.name} is not on the server"))
                continue
            if kind == "none":
                tally["no preview (download only)"] += 1
                self.stdout.write(warn(f"  DOWNLOAD {label}: no preview for this file type"))
                continue
            if kind != "pdf" or lpf.extension(att.filename) == "pdf":
                tally["previewable"] += 1
                continue
            if lpf.cached_pdf_path(att).exists():
                tally["previewable"] += 1
                continue
            if not build:
                tally["not converted yet (run --build)"] += 1
                continue
            try:
                lpf.pdf_for(att)
                tally["previewable"] += 1
                self.stdout.write(ok(f"  BUILT    {label}"))
            except lpf.PreviewUnavailable as exc:
                tally[f"conversion failed ({exc.reason})"] += 1
                self.stdout.write(bad(f"  FAILED   {label}: {exc}"))

        self.stdout.write("")
        for what, n in sorted(tally.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f"{n:6d}  {what}")
