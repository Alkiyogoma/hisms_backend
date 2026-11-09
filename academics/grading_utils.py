from decimal import Decimal
from typing import Optional

# ---------------------------------------------------------------------------
# Letter-grade utilities
# ---------------------------------------------------------------------------

def get_grade_from_score(score: float | Decimal) -> str:
    if score is None:
        return "N/A"
    s = float(score)
    if s >= 90: return "A+"
    if s >= 80: return "A"
    if s >= 70: return "B"
    if s >= 60: return "C"
    if s >= 50: return "D"
    return "E"

def get_grade_label(grade: str) -> str:
    labels = {
        "A+": "Outstanding",
        "A": "High",
        "B": "Good",
        "C": "Aspiring",
        "D": "Basic",
        "E": "Needs Improvement",
    }
    return labels.get(grade, "Unknown")

def get_full_grade_display(score: float | Decimal) -> str:
    grade = get_grade_from_score(score)
    label = get_grade_label(grade)
    if grade == "N/A":
        return "N/A"
    return f"{label} ({grade})"

# ---------------------------------------------------------------------------
# Pass mark: the one place every pass/fail decision reads from.
# ---------------------------------------------------------------------------

DEFAULT_PASS_MARK = 50     # used when School Settings has no row yet
CRITICAL_MARK = 50         # grade E: below this is critical
PASS_MARK_CACHE_KEY = "academics:pass_mark"
_LOCAL_TTL = 10            # seconds; band checks call this once per result
_local = {"value": None, "expires": 0.0}


def get_pass_mark() -> float:
    """The school pass mark (%), set on the School Settings page
    (``SchoolSettings.pass_mark``). Cached in-process for a few seconds and in
    the shared cache for five minutes; saving the settings clears both here
    (other workers pick the change up within seconds)."""
    import time
    now = time.monotonic()
    if _local["value"] is not None and now < _local["expires"]:
        return _local["value"]
    from django.core.cache import cache
    value = cache.get(PASS_MARK_CACHE_KEY)
    if value is None:
        from django.db import DatabaseError
        try:
            from core.models import SchoolSettings
            value = SchoolSettings.objects.filter(pk=1).values_list("pass_mark", flat=True).first()
        except DatabaseError:
            value = None
        if value is None:
            value = DEFAULT_PASS_MARK
        cache.set(PASS_MARK_CACHE_KEY, value, 300)
    _local.update(value=float(value), expires=now + _LOCAL_TTL)
    return float(value)


def clear_pass_mark_cache() -> None:
    from django.core.cache import cache
    _local.update(value=None, expires=0.0)
    cache.delete(PASS_MARK_CACHE_KEY)


def is_pass(score: float | Decimal) -> bool:
    if score is None:
        return False
    return float(score) >= get_pass_mark()

def is_at_risk(score: float | Decimal) -> bool:
    """Below the pass mark."""
    if score is None:
        return False
    return float(score) < get_pass_mark()

def is_critical(score: float | Decimal) -> bool:
    if score is None:
        return False
    return float(score) < CRITICAL_MARK


GRADE_LOWER_BOUNDS = (("A+", 90), ("A", 80), ("B", 70), ("C", 60), ("D", 50), ("E", 0))


def pass_grade() -> str:
    """The lowest grade that passes at the current pass mark (e.g. "D" at 50%)."""
    pm = get_pass_mark()
    passing = [g for g, low in GRADE_LOWER_BOUNDS if low >= pm]
    return passing[-1] if passing else "A+"


# ---------------------------------------------------------------------------
# Assessment gap classification & grade redistribution
# ---------------------------------------------------------------------------

EXAM_TYPE_ORDER = ["quiz", "mid_term", "end_of_term"]

DEFAULT_EXAM_WEIGHTS = {"quiz": 20, "mid_term": 30, "end_of_term": 50}


def _resolve_weights(weights: Optional[dict] = None) -> dict:
    """Return weights dict, loading from DB or fallback if None."""
    if weights is not None:
        return weights
    from academics.models import ExamTypeConfiguration
    w = {}
    for et in ExamTypeConfiguration.objects.filter(is_active=True):
        w[et.code] = float(et.weight_percentage)
    return w if w else dict(DEFAULT_EXAM_WEIGHTS)


def classify_assessment_gap(
    quiz: Optional[float] = None,
    mid_term: Optional[float] = None,
    end_term: Optional[float] = None,
    scores: Optional[dict] = None,
) -> dict:
    """
    Classify which gap cases apply to a set of assessment scores.

    Accepts either legacy positional args (quiz, mid_term, end_term) or a
    dynamic ``scores`` dict mapping exam codes to float|None values.

    Returns:
        case: int  0 (none missing) | 1–N as per edge-case table
        missing_exams: list[str]
        present_exams: list[str]
        missing_count: int
        makeup_required: bool
        label: str  human-readable description
    """
    if scores is None:
        scores = {"quiz": quiz, "mid_term": mid_term, "end_of_term": end_term}
    scores = {k: v for k, v in scores.items() if v is not None or True}

    missing = [k for k, v in scores.items() if v is None]
    present = [k for k, v in scores.items() if v is not None]
    total = len(scores)

    m = len(missing)
    if m == 0:
        return {"case": 0, "missing_exams": [], "present_exams": present,
                "missing_count": 0, "makeup_required": False,
                "label": "All assessments present: no redistribution"}
    if m == total:
        return {"case": 7, "missing_exams": missing, "present_exams": present,
                "missing_count": m, "makeup_required": True,
                "label": "Missed all assessments: grade cannot be calculated; make-up required"}
    if m >= 2:
        return {"case": 6, "missing_exams": missing, "present_exams": present,
                "missing_count": m, "makeup_required": False,
                "label": f"Missed {m} assessments: remaining {total - m} redistributed proportionally"}
    # m == 1
    missing_name = missing[0]
    return {"case": 1, "missing_exams": missing, "present_exams": present,
            "missing_count": 1, "makeup_required": False,
            "label": f"Missed {missing_name.replace('_', ' ').title()}: remaining assessments redistributed proportionally"}


def compute_redistributed_weights(
    present_exams: list[str],
    original_weights: Optional[dict] = None,
) -> dict:
    """
    Given the list of exam codes that HAVE a score, compute the redistributed
    weights (as percentages) so they sum to 100.

    Example: present=["mid_term","end_of_term"], original={quiz:20,mid_term:30,end_of_term:50}
        → {mid_term: 37.5, end_of_term: 62.5}
    """
    weights = _resolve_weights(original_weights)
    total = sum(weights.get(exam, 0) for exam in present_exams)
    if total == 0:
        return {}
    return {exam: round(weights.get(exam, 0) / total * 100, 1) for exam in present_exams}


def compute_grade_with_gaps(
    quiz: Optional[float] = None,
    mid_term: Optional[float] = None,
    end_term: Optional[float] = None,
    weights: Optional[dict] = None,
    scores: Optional[dict] = None,
) -> dict:
    """
    Compute weighted average with explicit gap classification and redistribution.

    Accepts either legacy positional args (quiz, mid_term, end_term) or a
    dynamic ``scores`` dict mapping exam codes to float|None values.

    Returns:
        average: float | None  weighted average (None when makeup_required)
        grade: str  letter grade ("N/A" when makeup_required)
        full_grade: str  display string
        gap: dict  output of classify_assessment_gap()
        redistributed_weights: dict  per-exam percentages after redistribution
        makeup_required: bool
    """
    if scores is None:
        scores = {"quiz": quiz, "mid_term": mid_term, "end_of_term": end_term}

    gap = classify_assessment_gap(scores=scores)

    if gap["makeup_required"]:
        return {
            "average": None,
            "grade": "N/A",
            "full_grade": "Make-up Required",
            "gap": gap,
            "redistributed_weights": {},
            "makeup_required": True,
        }

    original_weights = _resolve_weights(weights)
    present = gap["present_exams"]

    redistributed = compute_redistributed_weights(present, original_weights)

    weighted_sum = 0.0
    for exam_code in present:
        score = scores[exam_code]
        w = redistributed.get(exam_code, 0)
        weighted_sum += score * (w / 100.0)

    average = round(weighted_sum, 1)

    return {
        "average": average,
        "grade": get_grade_from_score(average),
        "full_grade": get_full_grade_display(average),
        "gap": gap,
        "redistributed_weights": redistributed,
        "makeup_required": False,
    }
