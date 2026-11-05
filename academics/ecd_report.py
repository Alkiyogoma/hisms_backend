"""ECD progress reports built from the per-term setup (ECDReportLayout / ECDSection / ECDItem).

One place for: which lines a class's report has in a term, how a learner's
ratings are saved and checked, and the Average Point / Remarks.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from academics.ecd_utils import ecd_template_type_from_class_name
from academics.models import (
    ECDClassType, ECDEvaluation, ECDItem, ECDReportLayout, ECDSection, term_number_from_name,
)

NA = "NA"
ACADEMIC_VALUES = ("4", "3", "2", "1")
DEFAULT_ACADEMIC_KEY = [["4", "Outstanding"], ["3", "Good"], ["2", "Satisfactory"], ["1", "Not yet"]]
DEFAULT_TRAITS_KEY = [["E", "Excellent"], ["G", "Good"], ["S", "Satisfactory"], ["N", "Needs Improvement"]]


def term_number_of(term) -> int:
    """1, 2 or 3 for a Term (falls back to its name, then Term 1)."""
    if term is None:
        return 1
    return term.term_number or term_number_from_name(term.name) or 1


def class_type_of(class_name) -> str | None:
    return ecd_template_type_from_class_name(class_name)


def get_layout(class_type, term_number) -> ECDReportLayout:
    layout = ECDReportLayout.objects.filter(class_type=class_type, term_number=term_number).first()
    if layout is None:
        # Not set up yet: an unsaved default so pages still render.
        layout = ECDReportLayout(
            class_type=class_type, term_number=term_number,
            title=f"{ECDClassType(class_type).label.upper()} PROGRESS REPORT",
            academic_key=DEFAULT_ACADEMIC_KEY, traits_key=DEFAULT_TRAITS_KEY,
        )
    return layout


def arrange(sections):
    """Lay sections out as on the printed report: full-width blocks before the
    two columns, the left and right columns, then full-width blocks after."""
    top, left, right, bottom = [], [], [], []
    seen_column = False
    for section in sections:
        if section.column == ECDSection.Column.LEFT:
            left.append(section)
            seen_column = True
        elif section.column == ECDSection.Column.RIGHT:
            right.append(section)
            seen_column = True
        else:
            (bottom if seen_column else top).append(section)
    return {"top": top, "left": left, "right": right, "bottom": bottom}


class ReportStructure:
    """The lines on one class type's report for one term, grouped for display."""

    def __init__(self, class_type, term_number):
        self.class_type = class_type
        self.term_number = term_number
        self.layout = get_layout(class_type, term_number)
        items = list(
            ECDItem.objects.for_term(term_number)
            .filter(section__class_type=class_type, section__is_active=True)
            .select_related("section").order_by("section__sort_order", "section__pk", "sort_order", "pk")
        )
        self.items = items
        self.items_by_id = {i.pk: i for i in items}
        sections = {}
        for item in items:
            sections.setdefault(item.section_id, item.section).term_items = []
        for item in items:
            sections[item.section_id].term_items.append(item)
        self.sections = list(sections.values())
        academic_options = [{"value": v, "label": label} for v, label in self.academic_key]
        traits_options = [{"value": v, "label": label} for v, label in self.traits_key]
        self.parts = []
        for key, label, options in (
            (ECDSection.Part.ACADEMIC, "Academic Progress", academic_options),
            (ECDSection.Part.TRAITS, "General Traits", traits_options),
        ):
            part_sections = [s for s in self.sections if s.part == key]
            self.parts.append({
                "key": key, "label": label, "options": options,
                "na": key == ECDSection.Part.ACADEMIC,
                "sections": part_sections, **arrange(part_sections),
            })

    @property
    def academic_key(self):
        return self.layout.academic_key or DEFAULT_ACADEMIC_KEY

    @property
    def traits_key(self):
        return self.layout.traits_key or DEFAULT_TRAITS_KEY

    def allowed_ratings(self, item):
        if item.section.part == ECDSection.Part.ACADEMIC:
            return set(ACADEMIC_VALUES) | {NA}
        return {code for code, _ in self.traits_key} | {NA}

    def remarks_for(self, avg):
        if avg is None:
            return ""
        labels = dict(self.academic_key)
        value = "4" if avg >= 3.5 else "3" if avg >= 2.5 else "2" if avg >= 1.5 else "1"
        return labels.get(value, "")


def structure_for_report(report) -> ReportStructure | None:
    class_type = report.ecd_template_type or class_type_of(report.student.class_name)
    if not class_type:
        return None
    return ReportStructure(class_type, term_number_of(report.term))


# ── a learner's values ─────────────────────────────────────────────────────

def values_for(report) -> dict:
    """{item_id: {"rating", "score", "note"}} for the report's configured lines."""
    out = {}
    for ev in ECDEvaluation.objects.filter(report_card=report, item__isnull=False):
        out[ev.item_id] = {
            "rating": ev.rating,
            "score": float(ev.score) if ev.score is not None else None,
            "note": ev.note,
        }
    return out


def _clean_score(raw):
    if raw in (None, ""):
        return None
    try:
        score = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise ValueError("Marks must be a number.")
    if score < 0 or score > 100:
        raise ValueError("Marks must be between 0 and 100.")
    return score


def save_values(report, structure: ReportStructure, values: dict) -> None:
    """Store ``{item_id: {"rating", "score", "note"}}``; an empty entry clears the line.

    Raises ValueError (nothing saved) when a value isn't allowed for its line.
    """
    cleaned = []
    for raw_id, value in (values or {}).items():
        try:
            item = structure.items_by_id[int(raw_id)]
        except (KeyError, TypeError, ValueError):
            raise ValueError("That line is not on this term's report; reload the page.")
        value = value or {}
        rating = str(value.get("rating") or "").strip().upper()
        note = str(value.get("note") or "").strip()[:255]
        if item.section.style == ECDSection.Style.MARKS:
            rating = ""
            score = _clean_score(value.get("score"))
        else:
            score = None
            if rating and rating not in structure.allowed_ratings(item):
                raise ValueError(f'"{rating}" is not a rating for "{item.label}".')
        cleaned.append((item, rating, score, note if item.allows_text else ""))

    for item, rating, score, note in cleaned:
        if not rating and score is None and not note:
            ECDEvaluation.objects.filter(report_card=report, item=item).delete()
            continue
        ECDEvaluation.objects.update_or_create(
            report_card=report, item=item,
            defaults={"rating": rating, "score": score, "note": note,
                      "domain": item.label, "section_title": item.section.title},
        )


def missing_lines(structure: ReportStructure, values: dict) -> list:
    """Lines still to rate (marks lines need a mark)."""
    missing = []
    for item in structure.items:
        v = values.get(item.pk) or {}
        if item.section.style == ECDSection.Style.MARKS:
            if v.get("score") is None:
                missing.append(item)
        elif not v.get("rating"):
            missing.append(item)
    return missing


def average_point(structure: ReportStructure, values: dict):
    points = []
    for item in structure.items:
        section = item.section
        if section.part != ECDSection.Part.ACADEMIC or not section.counts_in_average:
            continue
        rating = (values.get(item.pk) or {}).get("rating")
        if rating in ACADEMIC_VALUES:
            points.append(int(rating))
    return round(sum(points) / len(points), 1) if points else None


def summary(report, structure: ReportStructure, values: dict | None = None) -> dict:
    values = values_for(report) if values is None else values
    avg = average_point(structure, values)
    auto = structure.remarks_for(avg)
    missing = missing_lines(structure, values)
    return {
        "average": avg,
        "remarks_auto": auto,
        "remarks": (report.ecd_remarks or "").strip() or auto,
        "total": len(structure.items),
        "rated": len(structure.items) - len(missing),
        "missing": [m.label for m in missing],
    }
