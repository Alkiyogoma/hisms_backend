"""Validators for file attachments — reads limits from MediaSettings."""
import os
import hashlib
from django.core.exceptions import ValidationError


# Legacy fallback values (used only if MediaSettings row is missing)
LESSON_PLAN_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
LESSON_PLAN_ALLOWED_EXTENSIONS = {
    "pdf", "doc", "docx", "xls", "xlsx", "csv",
    "ppt", "pptx", "jpg", "jpeg", "png", "gif", "webp", "svg", "txt",
}


def _get_media_config(area):
    """Return MediaSettings for the given area, or build a default."""
    try:
        from core.models import MediaSettings
        return MediaSettings.get_for_area(area)
    except Exception:
        return None


def validate_attachment_file(file, area="lesson_plans"):
    """Validate a single file against the MediaSettings for *area*.

    Checks:
    1. File extension is in the allowed whitelist.
    2. File size does not exceed the per-area maximum.
    """
    config = _get_media_config(area)
    ext = os.path.splitext(file.name)[1].lower().lstrip(".")

    if config:
        allowed = config.allowed_extensions_set
        max_bytes = config.max_file_size_mb * 1024 * 1024
    else:
        allowed = LESSON_PLAN_ALLOWED_EXTENSIONS
        max_bytes = LESSON_PLAN_MAX_FILE_SIZE

    # --- Extension check ---
    if ext not in allowed:
        allowed_str = ", ".join(sorted(allowed))
        raise ValidationError(
            f"File type '.{ext}' is not allowed. "
            f"Allowed types: {allowed_str}."
        )

    # --- Size check ---
    if hasattr(file, "size") and file.size is not None:
        if file.size > max_bytes:
            max_mb = max_bytes / (1024 * 1024)
            size_mb = file.size / (1024 * 1024)
            raise ValidationError(
                f"File '{file.name}' is {size_mb:.1f} MB, which exceeds "
                f"the maximum allowed size of {max_mb:.0f} MB."
            )


def validate_upload_batch(files, area="lesson_plans"):
    """Validate a batch of files against the MediaSettings for *area*.

    Checks:
    1. Total number of files does not exceed max_files.
    2. Combined size does not exceed max_total_size_mb.
    3. Each file is individually valid (extension + size).
    """
    config = _get_media_config(area)
    if not config:
        # Fallback: validate individually only
        for f in files:
            validate_attachment_file(f, area=area)
        return

    max_files = config.max_files
    max_total_bytes = config.max_total_size_mb * 1024 * 1024

    # --- File count check ---
    if len(files) > max_files:
        raise ValidationError(
            f"You can upload a maximum of {max_files} file(s) at once."
        )

    # --- Combined size check ---
    total_size = sum(f.size for f in files if hasattr(f, "size") and f.size)
    if total_size > max_total_bytes:
        total_mb = total_size / (1024 * 1024)
        max_mb = max_total_bytes / (1024 * 1024)
        raise ValidationError(
            f"Combined file size is {total_mb:.1f} MB, which exceeds "
            f"the maximum allowed total of {max_mb:.0f} MB."
        )

    # --- Individual file validation ---
    for f in files:
        validate_attachment_file(f, area=area)


def _compute_file_hash(file_or_path, size_hint=None):
    """Compute a content hash for a file: SHA-256 of the first 64 KB + file size.

    This is fast for large files (only reads the first chunk) while still
    providing a strong fingerprint.  Two files with identical first-64 KB
    and identical total size will almost always be the same file.

    *file_or_path* may be an Django ``UploadedFile`` / ``FileField`` (has
    ``.read()``) or a filesystem ``pathlib.Path`` / ``str``.
    """
    CHUNK = 64 * 1024  # 64 KB
    sha = hashlib.sha256()

    # Read first chunk
    if hasattr(file_or_path, "read"):
        file_or_path.seek(0)
        data = file_or_path.read(CHUNK)
        sha.update(data)
        file_or_path.seek(0)
        total_size = getattr(file_or_path, "size", None) or size_hint or len(data)
    else:
        with open(str(file_or_path), "rb") as fh:
            data = fh.read(CHUNK)
            sha.update(data)
        total_size = os.path.getsize(str(file_or_path)) if not size_hint else size_hint

    # Append total size to distinguish files that share a 64 KB prefix
    sha.update(str(total_size).encode())
    return sha.hexdigest()


def check_duplicate_file(file, existing_queryset, area="lesson_plans"):
    """Check if a file is a duplicate using content hashing.

    Compares SHA-256 hashes of (first 64 KB + total size) to detect
    duplicates even when names or sizes differ.

    Returns a list of ``(existing_pk, existing_name)`` tuples for duplicates.
    Respects the ``duplicate_check`` toggle in MediaSettings.
    """
    config = _get_media_config(area)
    if config and not config.duplicate_check:
        return []

    file_size = file.size if hasattr(file, "size") and file.size else None
    if file_size is None:
        return []

    # Compute hash of the incoming file
    incoming_hash = _compute_file_hash(file)

    duplicates = []
    for existing in existing_queryset:
        # existing.file is a Django FieldFile — access via .path or .read()
        existing_file = existing.file if hasattr(existing, "file") else existing
        try:
            existing_path = existing_file.path
            existing_hash = _compute_file_hash(existing_path)
        except (AttributeError, ValueError, OSError):
            # Fallback: if file isn't on disk (e.g. remote storage),
            # fall back to name + size comparison
            existing_name = existing.filename if hasattr(existing, 'filename') else os.path.basename(existing.file.name)
            existing_sz = existing.file.size if hasattr(existing.file, 'size') else 0
            if os.path.basename(file.name) == existing_name and existing_sz == file_size:
                duplicates.append((existing.pk, existing_name))
            continue

        if incoming_hash == existing_hash:
            existing_name = existing.filename if hasattr(existing, 'filename') else os.path.basename(existing.file.name)
            duplicates.append((existing.pk, existing_name))

    return duplicates
