"""
Faithful previews of lesson plan attachments.

Office files (Word, PowerPoint, Excel) are converted to PDF on the server
with LibreOffice, so the reviewer sees the plan's tables, headings and
layout as written, in the same PDF viewer used for uploaded PDFs. The
converted PDF is cached next to the media files and rebuilt only when the
attachment changes.

LibreOffice is a system dependency (``apt install libreoffice-writer-nogui
libreoffice-impress-nogui libreoffice-calc-nogui``). Without it, office
files are not previewable and the viewer offers the download instead.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

PDF_EXTENSIONS = {"pdf"}
OFFICE_EXTENSIONS = {"doc", "docx", "odt", "rtf", "ppt", "pptx", "odp", "xls", "xlsx", "ods"}
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "svg"}
TEXT_EXTENSIONS = {"txt", "csv"}

CONVERT_TIMEOUT = 60  # seconds; under the 120 s gunicorn worker timeout


class PreviewUnavailable(Exception):
    """The file cannot be shown as a PDF (no converter, or conversion failed)."""


def extension(name: str) -> str:
    return os.path.splitext(name or "")[1].lower().lstrip(".")


def preview_kind(name: str) -> str:
    """How the viewer shows a file: pdf (PDF viewer, including converted
    office files), image, text, or none (download only)."""
    ext = extension(name)
    if ext in PDF_EXTENSIONS or ext in OFFICE_EXTENSIONS:
        return "pdf"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in TEXT_EXTENSIONS:
        return "text"
    return "none"


def converter() -> str | None:
    """Path to the LibreOffice binary, or None when it is not installed."""
    configured = getattr(settings, "LIBREOFFICE_BINARY", "")
    if configured:
        return configured if shutil.which(configured) or os.path.exists(configured) else None
    found = shutil.which("soffice") or shutil.which("libreoffice")
    mac = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    return found or (mac if os.path.exists(mac) else None)


def _cache_dir() -> Path:
    path = Path(settings.MEDIA_ROOT) / "lesson_plans" / "previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fingerprint(attachment) -> str:
    """Changes whenever the stored file changes (name, size or content time)."""
    f = attachment.file
    try:
        size = f.size
    except (OSError, ValueError):
        size = 0
    stamp = attachment.updated_at.isoformat() if attachment.updated_at else ""
    return hashlib.sha1(f"{f.name}|{size}|{stamp}".encode()).hexdigest()[:16]


def cached_pdf_path(attachment) -> Path:
    return _cache_dir() / f"{attachment.pk}-{_fingerprint(attachment)}.pdf"


def pdf_for(attachment) -> Path:
    """A local PDF path for ``attachment``: the file itself when it is a PDF,
    otherwise a cached LibreOffice conversion. Raises PreviewUnavailable."""
    ext = extension(attachment.filename or attachment.file.name)
    if ext in PDF_EXTENSIONS:
        try:
            return Path(attachment.file.path)
        except NotImplementedError:  # remote storage: copy through the cache
            return _copy_to_cache(attachment)
    if ext not in OFFICE_EXTENSIONS:
        raise PreviewUnavailable(f".{ext} files are not shown as PDF")

    target = cached_pdf_path(attachment)
    if target.exists() and target.stat().st_size > 0:
        return target
    binary = converter()
    if not binary:
        raise PreviewUnavailable("LibreOffice is not installed on the server")

    with tempfile.TemporaryDirectory(prefix="lp-preview-") as tmp:
        tmp_path = Path(tmp)
        source = tmp_path / f"source.{ext}"
        with attachment.file.open("rb") as src, open(source, "wb") as dst:
            shutil.copyfileobj(src, dst)
        # A private profile per run, so concurrent conversions never share a
        # locked LibreOffice user directory.
        profile = (tmp_path / "profile").as_uri()
        cmd = [binary, f"-env:UserInstallation={profile}", "--headless", "--norestore",
               "--convert-to", "pdf", "--outdir", str(tmp_path), str(source)]
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=CONVERT_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("Lesson plan preview conversion failed for attachment %s: %s", attachment.pk, exc)
            raise PreviewUnavailable("The file could not be converted") from exc
        produced = tmp_path / "source.pdf"
        if not produced.exists() or produced.stat().st_size == 0:
            logger.warning("Lesson plan preview conversion produced no PDF for attachment %s", attachment.pk)
            raise PreviewUnavailable("The file could not be converted")
        _prune_old(attachment.pk, keep=target.name)
        # Atomic: a concurrent request never reads a half-written PDF.
        partial = target.with_suffix(".part")
        shutil.copyfile(produced, partial)
        os.replace(partial, target)
    return target


def _copy_to_cache(attachment) -> Path:
    target = cached_pdf_path(attachment)
    if not target.exists():
        partial = target.with_suffix(".part")
        with attachment.file.open("rb") as src, open(partial, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(partial, target)
    return target


def _prune_old(pk, keep):
    for old in _cache_dir().glob(f"{pk}-*.pdf"):
        if old.name != keep:
            try:
                old.unlink()
            except OSError:
                pass


def discard_cached(attachment) -> None:
    """Remove cached previews for an attachment being deleted."""
    _prune_old(attachment.pk, keep="")


def prepare_in_background(attachment) -> None:
    """Queue the PDF conversion of a new office attachment once the upload is
    committed. Best effort: without a worker the preview is built on first
    view instead."""
    if extension(attachment.filename) not in OFFICE_EXTENSIONS:
        return
    from django.db import transaction

    def queue(pk=attachment.pk):
        try:
            from academics.tasks import prepare_lesson_plan_preview_task
            prepare_lesson_plan_preview_task.apply_async(args=[pk], retry=False)
        except Exception as exc:  # broker down: built on first view instead
            logger.info("Could not queue lesson plan preview for attachment %s: %s", pk, exc)

    transaction.on_commit(queue)
