"""ECD class name ↔ template key helpers (aligned with ECDEvaluationEntryView.DOMAIN_GROUPS)."""

from __future__ import annotations


def ecd_template_type_from_class_name(class_name: str | None) -> str | None:
    """Return DOMAIN_GROUPS key for this class, or None if not a known ECD class."""
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

ECD_RATING_MAP = {"E": 4, "G": 3, "S": 2, "N": 1, "4": 4, "3": 3, "2": 2, "1": 1}


def compute_ecd_average_and_remarks(evaluations, report):
    """Compute average score and auto-remarks label across all ECD domains.

    Maps E/G/S/N (or 4/3/2/1) ratings → numeric values, then determines
    a remarks label.  If ``report.ecd_remarks`` is set (teacher override)
    it is used instead of the auto label.

    Returns ``(avg_score, remarks_label)``.
    """
    scores = []
    for ev in evaluations:
        numeric = ECD_RATING_MAP.get(ev.rating)
        if numeric is not None:
            scores.append(numeric)

    if not scores:
        return None, ""

    avg = round(sum(scores) / len(scores), 2)

    if avg >= 3.5:
        auto_remarks = "Outstanding"
    elif avg >= 2.5:
        auto_remarks = "Good"
    elif avg >= 1.5:
        auto_remarks = "Satisfactory"
    else:
        auto_remarks = "Needs Improvement"

    # Teacher override via report.ecd_remarks
    remarks = report.ecd_remarks.strip() if report.ecd_remarks else ""
    if not remarks:
        remarks = auto_remarks

    return avg, remarks


def build_ecd_report_context(report):
    """Build the ``ecd`` context dict expected by ``ecd_report_preview.html``.

    Returns a dict with keys ``template_type``, ``class_label``, ``domain_groups``,
    ``teacher_comments`` — or raises ``ReportCard.DoesNotExist`` if ``report``
    is not an ECD report.
    """
    from academics.models import ECDEvaluation, ABCPaceProgress, ABCScripture, ABCReadingProgramme, ABCGeneralAssignment, ABCInternalExam
    from academics.views import ECDEvaluationEntryView

    tpl = report.ecd_template_type or ecd_template_type_from_class_name(report.student.class_name) or "pre_k"
    domain_groups = ECDEvaluationEntryView._load_domain_groups().get(tpl, [])
    evaluations = ECDEvaluation.objects.filter(report_card=report)

    # Build maps: individual competency -> rating, and group name -> rating
    eval_map = {ev.domain: ev.rating for ev in evaluations}

    grouped_rows = []
    for section_title, items in domain_groups:
        row_items = []
        for d in items:
            rating = eval_map.get(d, "")
            # If no individual rating, fall back to group-level rating
            if not rating:
                rating = eval_map.get(section_title, "")
            row_items.append({"domain": d, "rating": rating})
        grouped_rows.append({"section": section_title, "items": row_items})
    avg_score, remarks_label = compute_ecd_average_and_remarks(evaluations, report)

    ctx = {
        "template_type": tpl,
        "class_label": ECD_CLASS_LABELS.get(tpl, report.student.class_name),
        "domain_groups": grouped_rows,
        "teacher_comments": (report.teacher_comments or "").strip(),
        "avg_score": avg_score,
        "remarks_label": remarks_label,
    }

    if tpl == "abc":
        ctx["abc_pace_progress"] = ABCPaceProgress.objects.filter(report_card=report)
        ctx["abc_scripture"] = {s.quarter: s.verse for s in ABCScripture.objects.filter(report_card=report)}
        ctx["abc_reading"] = {r.quarter: r for r in ABCReadingProgramme.objects.filter(report_card=report)}
        ctx["abc_assignments"] = {a.quarter: a for a in ABCGeneralAssignment.objects.filter(report_card=report)}
        ctx["abc_internal_exams"] = ABCInternalExam.objects.filter(report_card=report)

    return ctx
