"""ECD class name ↔ class type helpers, and the context for printing an ECD report."""

from __future__ import annotations

from types import SimpleNamespace


def ecd_template_type_from_class_name(class_name: str | None) -> str | None:
    """Return the ECD class type (pre_k, kindergarten, pre_school, abc) for this class, or None."""
    if not class_name:
        return None
    cn = class_name.strip().lower()
    # Exact matches first
    if cn in ("abc class", "abc"):
        return "abc"
    if cn in ("pre-school", "pre school", "preschool", "pre-school (rr)", "pre school (rr)"):
        return "pre_school"
    if cn in ("kindergarten", "kg"):
        return "kindergarten"
    if cn in ("pre-kindergarten", "pre kindergarten", "pre-k", "pre k") or "nursery" in cn:
        return "pre_k"
    # Fallback substring checks
    if "pre-kindergarten" in cn or "pre kindergarten" in cn:
        return "pre_k"
    if "kindergarten" in cn:
        return "kindergarten"
    if "pre-school" in cn or "pre school" in cn or "preschool" in cn or "pre-unit" in cn or "pre unit" in cn:
        return "pre_school"
    if "abc" in cn:
        return "abc"
    return None


def grade_class_names_for_department(department: str) -> set[str]:
    from academics.models import GradeClass

    return {n for n in GradeClass.objects.filter(department=department).values_list("name", flat=True) if n}


ECD_CLASS_LABELS = {
    "pre_k": "Pre-Kindergarten",
    "kindergarten": "Kindergarten",
    "pre_school": "Pre-School",
    "abc": "ABC Class",
}

def build_ecd_report_context(report, shared=None):
    """The ``ecd`` context for ``_ecd_report_body.html``: the term's report lines
    laid out as on the school's Word template, with this learner's ratings.

    Reports rated before the per-term templates (``legacy``) list their stored
    ratings as recorded. ``shared`` (a dict) reuses the report lines across a class print.
    """
    from academics import ecd_report
    from academics.models import ECDEvaluation

    class_type = report.ecd_template_type or ecd_template_type_from_class_name(report.student.class_name) or "pre_k"
    term_number = ecd_report.term_number_of(report.term)
    key = ("ecd_structure", class_type, term_number)
    if shared is not None and key in shared:
        structure = shared[key]
    else:
        structure = ecd_report.ReportStructure(class_type, term_number)
        if shared is not None:
            shared[key] = structure
    ctx = {
        "class_type": class_type,
        "class_label": ECD_CLASS_LABELS.get(class_type, report.student.class_name),
        "layout": structure.layout,
        "teacher_comments": (report.teacher_comments or "").strip(),
    }
    # An unsaved report is a blank sheet for a learner not yet started (class print).
    evaluations = list(ECDEvaluation.objects.filter(report_card=report).order_by("pk")) if report.pk else []
    # Rated before the per-term templates: only old-style rows, none linked to a line.
    ctx["legacy"] = bool(evaluations) and all(ev.item_id is None for ev in evaluations)
    if ctx["legacy"]:
        ctx["legacy_rows"] = [(ev.domain, ev.rating) for ev in evaluations]
        return ctx

    rows = {ev.item_id: ev for ev in evaluations if ev.item_id}
    values = {}
    printed = {}  # section pk -> this learner's copy of the section, with its lines
    for section in structure.sections:
        lines = []
        for item in section.term_items:
            ev = rows.get(item.pk)
            lines.append({
                "label": ev.domain if ev else item.label,
                "rating": "N/A" if ev and ev.rating == ecd_report.NA else (ev.rating if ev else ""),
                "score": ev.score if ev else None,
                "note": ev.note if ev else "",
            })
            if ev:
                values[item.pk] = {"rating": ev.rating, "score": ev.score, "note": ev.note}
        printed[section.pk] = SimpleNamespace(
            title=section.title, style=section.style, column=section.column, lines=lines,
        )
    parts = [
        {**part, **{place: [printed[s.pk] for s in part[place]] for place in ("sections", "top", "left", "right", "bottom")}}
        for part in structure.parts
    ]
    info = ecd_report.summary(report, structure, values)
    ctx.update({"parts": parts, "avg_score": info["average"], "remarks_label": info["remarks"]})
    return ctx
