"""
NFR-PDPA-003: DSAR Export Service

Produces a complete export of personal data for a named individual (student or
guardian) within 30 minutes.  The export is returned as structured JSON that can
be downloaded by a Super Admin.

Data categories collected:
  - Student: identity, medical, guardians, siblings, attendance, finances,
    academics, discipline, welfare, admissions, audit trail, notifications.
  - Guardian: identity, linked students, PDPA consent, audit trail.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, date
from decimal import Decimal
from typing import Any

from django.db.models import Q, QuerySet

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_date(obj):
    """JSON-safe serialisation for dates / datetimes / Decimals / sets."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, set):
        return sorted(obj)
    return str(obj)


def _qs_to_list(qs: QuerySet, fields: list[str]) -> list[dict]:
    """Return a list-of-dicts from a queryset for the given fields."""
    return list(qs.values(*fields))


def _safe_qs_to_list(qs, fields):
    """Return a list-of-dicts, returning [] on error."""
    try:
        return _qs_to_list(qs, fields)
    except Exception:
        logger.warning("DSAR export: failed to serialise queryset", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Student export
# ---------------------------------------------------------------------------

def export_student_data(student) -> dict[str, Any]:
    """Collect *all* personal data related to a Student into a single dict."""

    data: dict[str, Any] = {
        "export_version": "1.0",
        "export_type": "student",
        "export_generated_at": datetime.now().isoformat(),
        "nfr_compliance": "NFR-PDPA-003 — complete personal data export within 30 minutes",
    }

    # ── 1. Core identity ─────────────────────────────────────────────────
    data["personal_info"] = {
        "id": student.id,
        "admission_no": student.admission_no,
        "first_name": student.first_name,
        "last_name": student.last_name,
        "preferred_name": student.preferred_name,
        "full_name": student.get_full_name(),
        "date_of_birth": _serialize_date(student.date_of_birth),
        "gender": student.gender,
        "nationality": student.nationality,
        "religion": student.religion,
        "blood_type": student.blood_type,
        "status": student.status,
        "class_name": student.class_name,
        "stream_name": student.stream_name,
        "enrolment_date": _serialize_date(student.enrolment_date),
        "phone": student.phone,
        "photo_url": student.photo.url if student.photo else None,
        "is_archived": student.is_archived,
        "created_at": _serialize_date(student.created_at),
        "updated_at": _serialize_date(student.updated_at),
    }

    # ── 2. Medical information ───────────────────────────────────────────
    data["medical_info"] = {
        "allergies_medical": student.allergies_medical or "",
        "blood_type": student.blood_type,
    }

    # ── 3. Guardian / parent information ─────────────────────────────────
    from students.models import StudentGuardian, ParentGuardian

    guardian_links = StudentGuardian.objects.filter(
        student=student
    ).select_related("guardian")

    guardians = []
    for link in guardian_links:
        g = link.guardian
        guardians.append({
            "guardian_id": g.id,
            "full_name": g.full_name,
            "phone": g.phone,
            "secondary_phone": g.secondary_phone,
            "email": g.email,
            "address": g.address,
            "preferred_invoice_name": g.preferred_invoice_name,
            "preferred_language": g.preferred_language,
            "relationship": link.relationship,
            "is_primary": link.is_primary,
            "pdpa_consent": {
                "consent_given": g.pdpa_consent_given,
                "consent_method": g.pdpa_consent_method,
                "consent_version": g.pdpa_consent_version,
                "consented_at": _serialize_date(g.pdpa_consented_at),
            },
        })
    data["guardians"] = guardians

    # ── 4. Sibling relationships ─────────────────────────────────────────
    from students.models import StudentSibling

    sib_links = StudentSibling.objects.filter(
        Q(student_a=student) | Q(student_b=student)
    )
    siblings = []
    for sl in sib_links:
        other = sl.student_b if sl.student_a_id == student.id else sl.student_a
        siblings.append({
            "sibling_student_id": other.id,
            "sibling_name": other.get_full_name(),
            "sibling_admission_no": other.admission_no,
        })
    data["siblings"] = siblings

    # ── 5. Attendance history ────────────────────────────────────────────
    from attendance.models import AttendanceEntry

    data["attendance_history"] = _safe_qs_to_list(
        AttendanceEntry.objects.filter(student=student).order_by("-date"),
        ["id", "date", "status", "time_in", "time_out", "notes", "created_at"],
    )

    # ── 6. Financial records ─────────────────────────────────────────────
    from finance.models import Invoice, InvoiceLineItem, Payment, Concession

    invoices = Invoice.objects.filter(student=student).order_by("-created_at")
    invoice_list = []
    for inv in invoices:
        line_items = list(
            InvoiceLineItem.objects.filter(invoice=inv).values(
                "id", "description", "amount", "is_discount"
            )
        )
        payments = list(
            Payment.objects.filter(invoice=inv).values(
                "id", "amount", "method", "payment_date", "reference",
                "is_reversal", "created_at",
            )
        )
        invoice_list.append({
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "amount_due": str(inv.amount_due),
            "discount_amount": str(inv.discount_amount),
            "total_due": str(inv.total_due),
            "due_date": _serialize_date(inv.due_date),
            "status": inv.status,
            "is_finalized": inv.is_finalized,
            "created_at": _serialize_date(inv.created_at),
            "line_items": line_items,
            "payments": payments,
        })
    data["financial_records"] = {
        "invoices": invoice_list,
        "concessions": _safe_qs_to_list(
            Concession.objects.filter(student=student).order_by("-created_at"),
            [
                "id", "concession_type", "reason", "discount_mode",
                "discount_value", "status", "valid_from", "valid_until",
                "created_at",
            ],
        ),
    }

    # ── 7. Academic records ──────────────────────────────────────────────
    from academics.models import ExamScore, ReportCard, ECDEvaluation

    data["academic_records"] = {
        "exam_scores": _safe_qs_to_list(
            ExamScore.objects.filter(student=student).order_by("-created_at")[:500],
            [
                "id", "term_id", "subject_id", "exam_type", "score",
                "grade", "position", "comments", "created_at",
            ],
        ),
        "report_cards": _safe_qs_to_list(
            ReportCard.objects.filter(student=student).order_by("-created_at")[:100],
            [
                "id", "term_id", "academic_year_id", "class_name",
                "overall_grade", "overall_position", "comments",
                "generated_at", "created_at",
            ],
        ),
        "ecd_evaluations": _safe_qs_to_list(
            ECDEvaluation.objects.filter(student=student).order_by("-created_at")[:100],
            ["id", "term_id", "subject_id", "rating", "comments", "created_at"],
        ),
    }

    # ── 8. Discipline records ────────────────────────────────────────────
    from discipline.models import DisciplineIncident

    data["discipline_records"] = _safe_qs_to_list(
        DisciplineIncident.objects.filter(student=student).order_by("-incident_date"),
        [
            "id", "incident_date", "incident_type", "description",
            "action_taken", "reported_by_id", "created_at",
        ],
    )

    # ── 9. Welfare observations ──────────────────────────────────────────
    try:
        from welfare.models import WelfareObservation

        data["welfare_records"] = _safe_qs_to_list(
            WelfareObservation.objects.filter(student=student).order_by("-created_at"),
            [
                "id", "observation_type", "description", "severity",
                "action_taken", "observed_by_id", "created_at",
            ],
        )
    except (ImportError, Exception):
        data["welfare_records"] = []

    # ── 10. Admissions pipeline data ─────────────────────────────────────
    from admissions.models import Applicant

    applicants = Applicant.objects.filter(
        Q(child_full_name__icontains=student.first_name)
        & Q(child_full_name__icontains=student.last_name)
    )[:5]

    data["admissions_history"] = _safe_qs_to_list(
        applicants,
        [
            "id", "parent_full_name", "parent_phone", "parent_email",
            "child_full_name", "child_date_of_birth", "grade_applying_for",
            "status", "inquiry_channel", "sibling_matched_parent_id",
            "created_at",
        ],
    )

    # ── 11. Notification / communication history ─────────────────────────
    try:
        from communications.models import Notification

        data["notifications"] = _safe_qs_to_list(
            Notification.objects.filter(
                Q(recipient=student.phone)
                | Q(message__icontains=student.first_name)
                | Q(message__icontains=student.last_name)
            ).order_by("-created_at")[:200],
            ["id", "title", "message", "is_read", "created_at"],
        )
    except (ImportError, Exception):
        data["notifications"] = []

    # ── 12. Audit trail ──────────────────────────────────────────────────
    from audit.models import AuditLog

    data["audit_trail"] = _safe_qs_to_list(
        AuditLog.objects.filter(
            Q(model_name="Student", object_id=str(student.id))
            | Q(description__icontains=student.first_name)
            | Q(description__icontains=student.last_name)
        ).order_by("-created_at")[:500],
        [
            "id", "actor_id", "action_type", "model_name", "object_id",
            "description", "created_at",
        ],
    )

    return data


# ---------------------------------------------------------------------------
# Guardian export
# ---------------------------------------------------------------------------

def export_guardian_data(guardian) -> dict[str, Any]:
    """Collect *all* personal data related to a ParentGuardian."""

    from students.models import Student

    data: dict[str, Any] = {
        "export_version": "1.0",
        "export_type": "guardian",
        "export_generated_at": datetime.now().isoformat(),
        "nfr_compliance": "NFR-PDPA-003 — complete personal data export within 30 minutes",
    }

    # ── 1. Core identity ─────────────────────────────────────────────────
    data["personal_info"] = {
        "id": guardian.id,
        "full_name": guardian.full_name,
        "phone": guardian.phone,
        "secondary_phone": guardian.secondary_phone,
        "email": guardian.email,
        "address": guardian.address,
        "preferred_invoice_name": guardian.preferred_invoice_name,
        "preferred_language": guardian.preferred_language,
        "is_archived": guardian.is_archived,
        "created_at": _serialize_date(guardian.created_at),
        "updated_at": _serialize_date(guardian.updated_at),
    }

    # ── 2. PDPA consent details ──────────────────────────────────────────
    data["pdpa_consent"] = {
        "consent_given": guardian.pdpa_consent_given,
        "consent_method": guardian.pdpa_consent_method,
        "consent_version": guardian.pdpa_consent_version,
        "consented_at": _serialize_date(guardian.pdpa_consented_at),
    }

    # ── 3. Linked students ───────────────────────────────────────────────
    linked_students = Student.objects.filter(
        studentguardian__guardian=guardian
    )
    student_list = []
    for s in linked_students:
        student_list.append({
            "student_id": s.id,
            "admission_no": s.admission_no,
            "full_name": s.get_full_name(),
            "class_name": s.class_name,
            "status": s.status,
        })
    data["linked_students"] = student_list

    # ── 4. Laravel parent record (if applicable) ────────────────────────
    try:
        from students.models import LaravelParent

        lp = LaravelParent.objects.filter(guardian=guardian).first()
        if lp:
            data["laravel_parent_record"] = {
                "id": lp.id,
                "first_name": lp.first_name,
                "last_name": lp.last_name,
                "email": lp.email,
                "phone": lp.phone,
                "secondary_phone": lp.secondary_phone,
                "address": lp.address,
                "city": lp.city,
                "state": lp.state,
                "postal_code": lp.postal_code,
                "occupation": lp.occupation,
            }
    except (ImportError, Exception):
        pass

    # ── 5. Audit trail ───────────────────────────────────────────────────
    from audit.models import AuditLog

    data["audit_trail"] = _safe_qs_to_list(
        AuditLog.objects.filter(
            Q(model_name="ParentGuardian", object_id=str(guardian.id))
            | Q(description__icontains=guardian.full_name)
            | Q(description__icontains=guardian.phone)
        ).order_by("-created_at")[:500],
        [
            "id", "actor_id", "action_type", "model_name", "object_id",
            "description", "created_at",
        ],
    )

    return data


# ---------------------------------------------------------------------------
# Search helpers for the UI
# ---------------------------------------------------------------------------

def search_students(query: str):
    """Search students by name or admission number."""
    from students.models import Student

    return Student.objects.filter(
        Q(first_name__icontains=query)
        | Q(last_name__icontains=query)
        | Q(admission_no__icontains=query),
        is_archived=False,
    ).order_by("last_name", "first_name")[:20]


def search_guardians(query: str):
    """Search guardians by name, phone, or email."""
    from students.models import ParentGuardian

    return ParentGuardian.objects.filter(
        Q(full_name__icontains=query)
        | Q(phone__icontains=query)
        | Q(email__icontains=query),
        is_archived=False,
    ).order_by("full_name")[:20]


# ---------------------------------------------------------------------------
# Format converters — flatten nested dicts into tabular rows
# ---------------------------------------------------------------------------

def _flatten_row(prefix: str, row: dict) -> dict:
    """Flatten a nested dict into ``prefix_key: value`` pairs."""
    flat = {}
    for k, v in row.items():
        if isinstance(v, dict):
            flat.update(_flatten_row(f"{prefix}{k}_", v))
        else:
            flat[f"{prefix}{k}"] = v
    return flat


def _section_to_rows(section_name: str, value) -> list[dict]:
    """Convert a single data section into a list of flat rows.

    For dicts (scalar sections like personal_info), produces a single row.
    For lists of dicts (tabular sections like attendance), produces one row per item.
    """
    if isinstance(value, dict):
        flat = _flatten_row("", value)
        return [{"section": section_name} | {
            k: _serialize_date(v) if v is not None else "" for k, v in flat.items()
        }]
    elif isinstance(value, list):
        rows = []
        for item in value:
            if isinstance(item, dict):
                flat = _flatten_row("", item)
                rows.append({
                    k: _serialize_date(v) if v is not None else ""
                    for k, v in flat.items()
                })
            else:
                rows.append({"value": _serialize_date(item) if item is not None else ""})
        return rows
    else:
        return [{"section": section_name, "value": _serialize_date(value) if value is not None else ""}]


def build_dsar_sections(data: dict) -> dict[str, list[dict]]:
    """Split the DSAR export dict into named sections, each a list of flat rows.

    Returns ``{sheet_name: [row_dicts]}``.
    Empty sections are excluded.
    """
    import re
    skip = {"export_version", "export_type", "export_generated_at", "nfr_compliance"}
    sections: dict[str, list[dict]] = {}

    for key, value in data.items():
        if key in skip:
            continue
        if isinstance(value, (dict, list)):
            rows = _section_to_rows(key, value)
            if not rows:
                continue
            # Excel sheet names: max 31 chars, no []:*?\/\\
            label = key.replace("_", " ").title()
            label = re.sub(r'[\\\/?\*\[\]:]+', "-", label[:31])
            sections[label] = rows

    return sections


def _write_sheet(ws, headers: list[str], rows: list[dict], header_font=None, header_fill=None, thin_border=None):
    """Write a header row + data rows onto *ws*."""
    if header_font is None:
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        header_font = Font(bold=True, color="FFFFFF", size=11)
        header_fill = PatternFill(start_color="023AA5", end_color="023AA5", fill_type="solid")
        thin_border = Border(
            left=Side(style="thin", color="D1D9E8"),
            right=Side(style="thin", color="D1D9E8"),
            top=Side(style="thin", color="D1D9E8"),
            bottom=Side(style="thin", color="D1D9E8"),
        )
    from openpyxl.styles import Alignment

    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    for row_idx, row in enumerate(rows, 2):
        for col_idx, h in enumerate(headers, 1):
            val = row.get(h, "")
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.border = thin_border
            cell.alignment = Alignment(vertical="center")

    # Auto-fit column widths
    for col_idx, h in enumerate(headers, 1):
        max_len = len(str(h))
        for r in rows[:50]:
            v = r.get(h, "")
            if v:
                max_len = max(max_len, min(len(str(v)), 60))
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = min(max_len + 4, 64)

    if rows:
        ws.auto_filter.ref = ws.dimensions
        ws.freeze_panes = "A2"


def _headers_for_rows(rows: list[dict]) -> list[str]:
    """Collect an ordered list of unique column headers from a list of dicts."""
    seen: set = set()
    headers: list[str] = []
    for r in rows:
        for k in r:
            if k not in seen:
                headers.append(k)
                seen.add(k)
    return headers


def _rows_to_csv(sections: dict[str, list[dict]]) -> str:
    """Convert DSAR sections to a multi-section CSV string.

    Each section is separated by a blank row and a ``# Section Name`` header.
    """
    import csv
    import io

    buf = io.StringIO()
    for section_name, rows in sections.items():
        if not rows:
            continue
        # Section header
        buf.write(f"\n# {section_name}\n")
        headers = _headers_for_rows(rows)
        writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return buf.getvalue().lstrip("\n")


def _rows_to_xlsx_bytes(sections: dict[str, list[dict]]) -> bytes:
    """Convert DSAR sections into a multi-sheet XLSX workbook (bytes)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()

    if not sections:
        ws = wb.active
        ws.title = "No Data"
        ws["A1"] = "No records found."
        import io
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="023AA5", end_color="023AA5", fill_type="solid")
    thin_border = Border(
        left=Side(style="thin", color="D1D9E8"),
        right=Side(style="thin", color="D1D9E8"),
        top=Side(style="thin", color="D1D9E8"),
        bottom=Side(style="thin", color="D1D9E8"),
    )

    # --- Cover sheet ---
    ws_cover = wb.active
    ws_cover.title = "Export Info"
    ws_cover["A1"] = "DSAR Export — NFR-PDPA-003"
    ws_cover["A1"].font = Font(bold=True, size=14, color="023AA5")
    ws_cover["A2"] = f"Generated: {datetime.now().isoformat()}"
    ws_cover["A3"] = f"Sections: {len(sections)}"
    total_rows = sum(len(r) for r in sections.values())
    ws_cover["A4"] = f"Total rows: {total_rows}"

    # --- One sheet per section ---
    for sheet_name, rows in sections.items():
        # Excel sheet names max 31 chars, no special chars
        safe_name = sheet_name[:31].replace("/", "-").replace("\\", "-")
        ws = wb.create_sheet(title=safe_name)
        headers = _headers_for_rows(rows)
        _write_sheet(ws, headers, rows, header_font, header_fill, thin_border)

    import io
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
