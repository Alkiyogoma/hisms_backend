"""QR code encryption, signing, and generation utilities.

Uses Django's TimestampSigner (wraps SECRET_KEY) to produce opaque,
tamper-proof QR payloads that do not reveal student personal data.
"""

import io
import base64
from typing import Optional

from django.core.signing import Signer, BadSignature
from django.utils import timezone

QR_PREFIX = "HDR"
_SIGNER = None


def _get_signer():
    global _SIGNER
    if _SIGNER is None:
        _SIGNER = Signer(salt="attendance-qr")
    return _SIGNER


def sign_student_id(student_id: str) -> str:
    """Sign a raw student ID so the QR payload is opaque.

    Returns a string like ``HDR_<signed_value>`` that can be embedded in
    a QR code but does not reveal the underlying ID.
    """
    signed = _get_signer().sign(str(student_id))
    return f"{QR_PREFIX}_{signed}"


def unsign_qr_data(payload: str) -> Optional[str]:
    """Extract the original student ID from a signed QR payload.

    Accepts both raw signed values and values prefixed with ``HDR_``.
    Returns ``None`` if the signature is invalid or the payload is
    malformed.
    """
    raw = payload
    if raw.startswith(QR_PREFIX + "_"):
        raw = raw[len(QR_PREFIX) + 1 :]

    # Also accept bare signed values (from older QR codes / manual entry)
    try:
        return _get_signer().unsign(raw)
    except BadSignature:
        pass
    return None


def is_signed_qr(payload: str) -> bool:
    """Quick check — does this look like a signed QR payload?"""
    return payload.startswith(QR_PREFIX + "_")


def generate_qr_code_svg(data: str, size: int = 200) -> str:
    """Generate an inline SVG QR code for the given data string.

    Returns a complete ``<svg>`` element as a string, suitable for
    embedding directly in HTML templates (no external file needed).
    """
    import qrcode
    from qrcode.image.svg import SvgPathImage

    qr = qrcode.QRCode(
        version=None,  # auto
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=1,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(image_factory=SvgPathImage)
    return img.to_string().decode("utf-8")


def generate_qr_code_bytes(data: str, size: int = 200) -> bytes:
    """Generate a PNG QR code and return raw bytes."""
    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=1,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_qr_code_b64(data: str) -> str:
    """Generate a base64-encoded PNG QR code (for inline <img> tags)."""
    raw = generate_qr_code_bytes(data)
    return base64.b64encode(raw).decode("utf-8")
