"""
The admission (Term 1 enrolment) fee book — the single place enrolment
charges are calculated.

The online admission form, the downloadable invoice, the parent's emails and
the finance Invoice all use these figures, so a parent never sees two
different amounts for the same invoice. The admission fee comes from School
Settings; the form gets the whole schedule as JSON (``fee_schedule``) and the
server builds the invoice lines with ``admission_line_items``.
"""
ECD_GRADES = ["Pre-KG", "Pre-K", "Kindergarten", "KG", "Preschool", "Pre-School", "ABC"]
UPPER_GRADES = ["Grade 7", "Grade 8", "Grade 9"]
CHECKPOINT_GRADES = ["Grade 6"]

TUITION_ECD = 3_200_000
TUITION_UPPER = 3_800_000
TUITION_DEFAULT = 3_500_000
DEVELOPMENT_FEE = 700_000
CHECKPOINT_FEE = 300_000
STEM_FEE = 180_000
BREAKFAST_FEE = 300_000
DEFAULT_ADMISSION_FEE = 700_000
UNIFORM = {
    "polo": {"label": "Polo T-shirt (white / blue / yellow)", "price": 20_000},
    "sweater": {"label": "Hodari sweater", "price": 25_000},
    "tee": {"label": "Sports team T-shirt (red / blue / green)", "price": 15_000},
}


def fee_schedule() -> dict:
    from core.models import SchoolSettings
    admission = SchoolSettings.get_settings().admission_fee or DEFAULT_ADMISSION_FEE
    return {
        "tuition": {"ecd": TUITION_ECD, "upper": TUITION_UPPER, "default": TUITION_DEFAULT},
        "ecd_grades": ECD_GRADES,
        "upper_grades": UPPER_GRADES,
        "checkpoint_grades": CHECKPOINT_GRADES,
        "development": DEVELOPMENT_FEE,
        "admission": int(admission),
        "checkpoint": CHECKPOINT_FEE,
        "stem": STEM_FEE,
        "breakfast": BREAKFAST_FEE,
        "uniform": UNIFORM,
    }


def tuition_for(grade, schedule) -> int:
    if grade in schedule["ecd_grades"]:
        return schedule["tuition"]["ecd"]
    if grade in schedule["upper_grades"]:
        return schedule["tuition"]["upper"]
    return schedule["tuition"]["default"]


def child_lines(child: dict, schedule: dict) -> list[dict]:
    """[{"label", "amount"}] for one child — mirrors childLines() in the form."""
    grade = child.get("grade") or ""
    lines = [
        {"label": "Tuition — Term 1", "amount": tuition_for(grade, schedule)},
        {"label": "Development fee (annual)", "amount": schedule["development"]},
    ]
    if child.get("isNew", True):
        lines.append({"label": "Admission fee (one-time)", "amount": schedule["admission"]})
    if grade in schedule["checkpoint_grades"]:
        lines.append({"label": "Cambridge Checkpoint", "amount": schedule["checkpoint"]})
    if child.get("stem"):
        lines.append({"label": "STEM — Term 1", "amount": schedule["stem"]})
    if child.get("breakfast"):
        lines.append({"label": "Breakfast — Term 1", "amount": schedule["breakfast"]})
    uniforms = child.get("uniform") or {}
    for key, item in schedule["uniform"].items():
        try:
            qty = max(int(uniforms.get(key) or 0), 0)
        except (TypeError, ValueError):
            qty = 0
        if qty:
            lines.append({"label": f"{item['label']} × {qty}", "amount": item["price"] * qty})
    return lines


def invoice_summary(invoice) -> dict | None:
    """The stored invoice exactly as Finance holds it, for the form to show
    (number, dates, lines and total all come from the Invoice record)."""
    if invoice is None:
        return None
    return {
        "number": invoice.invoice_number,
        "issued": invoice.created_at.strftime("%d %b %Y") if invoice.created_at else "",
        "due": invoice.due_date.strftime("%d %b %Y") if invoice.due_date else "",
        "total": int(invoice.total_due),
        "lines": [
            {"label": li.description, "amount": int(li.amount)}
            for li in invoice.line_items.order_by("pk")
        ],
    }


def admission_invoice_for(applicant):
    from finance.models import Invoice
    return Invoice.objects.filter(applicant=applicant, invoice_number__startswith="ADM-").first()


def form_fee_context(applicant) -> dict:
    """Template context for the admission form: the fee book and, once
    generated, the real invoice."""
    import json
    dump = lambda v: json.dumps(v).replace("</", "<\\/")  # noqa: E731
    return {
        "fee_schedule_json": dump(fee_schedule()),
        "invoice_json": dump(invoice_summary(admission_invoice_for(applicant))),
    }
