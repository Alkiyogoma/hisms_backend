"""
Class position on the learner's progress report / report card.

Cambridge does not encourage ranking learners, so this is deliberately narrow:

* A learner is ranked within their class on four overall measures only:
  the Quiz, Mid-Term and End-Term averages across mark-bearing subjects, and
  the overall (weighted) end-of-term average across mark-bearing subjects.
* Never by individual subject. Remark-only subjects never count.
* Approved marks only. A learner with no approved marks for a measure is not
  counted for it (they are neither ranked nor part of the class size).
* Ties share a position ("1, 2, 2, 4").
* The result is a position and a class size ("4 of 26"); other learners'
  names and marks never leave this module.

Positions are given for the term and for the academic year to date (every
term of the year up to and including the report's term).

Marks follow academics.performance.compute_results: each subject's mark is
the weighted mean of its approved assessments in a term (weights rescaled
over what has been marked), averaged across terms; a measure's average is
the mean of the learner's subject marks. All classmates' marks for the year
to date are read in one query, and kept in ``shared`` when a whole class is
printed together.
"""
from __future__ import annotations

from collections import defaultdict

# Assessment-type codes ranked on, in report order, with their report labels.
EXAM_MEASURES = (
    ("quiz", "Quiz average"),
    ("mid_term", "Mid-Term average"),
    ("end_of_term", "End-Term average"),
)
OVERALL = "overall"
OVERALL_LABEL = "Overall average"


def rank(averages: dict, student_id):
    """{"position", "of"} for ``student_id`` among ``averages`` ({learner: average});
    None when the learner has no average for this measure. Ties share the
    best position: a learner's position is one more than the number of
    learners strictly ahead of them."""
    if student_id not in averages:
        return None
    mine = averages[student_id]
    return {"position": 1 + sum(1 for v in averages.values() if v > mine), "of": len(averages)}


def _cached(shared, key, compute):
    if shared is None:
        return compute()
    if key not in shared:
        shared[key] = compute()
    return shared[key]


def classmate_ids(class_name, year, shared=None):
    """Learners who are, or were during ``year``, in ``class_name``. A superset:
    callers keep only those whose class that year is ``class_name``."""
    from students.models import EnrollmentHistory, Student

    def compute():
        ids = set(Student.objects.filter(class_name=class_name).values_list("id", flat=True))
        if year is not None:
            ids |= set(EnrollmentHistory.objects.filter(academic_year=year, class_name=class_name)
                       .values_list("student_id", flat=True))
        return ids
    return _cached(shared, ("classmate_ids", class_name, year.pk if year else None), compute)


def year_to_date_terms(term):
    """The terms of ``term``'s academic year up to and including ``term``."""
    from academics.models import Term

    if term is None:
        return []
    if not term.academic_year_id:
        return [term]
    terms = [t for t in Term.objects.filter(academic_year_id=term.academic_year_id).order_by("start_date", "pk")
             if t.pk == term.pk or (t.start_date and term.start_date and t.start_date <= term.start_date)]
    return terms or [term]


def _subject_marks(rows, exam_type, weights):
    """{(learner, subject): mark} from ``rows`` of (learner, term, subject,
    type, pct), as compute_results calculates it."""
    per = defaultdict(lambda: defaultdict(list))
    for sid, tid, subject, etype, pct in rows:
        if exam_type is None or etype == exam_type:
            per[(sid, subject)][tid].append((pct, weights.get(etype, 0.0)))
    marks = {}
    for key, terms_map in per.items():
        term_marks = []
        for vals in terms_map.values():
            total_w = sum(w for _, w in vals)
            term_marks.append(sum(p * w for p, w in vals) / total_w if total_w > 0
                              else sum(p for p, _ in vals) / len(vals))
        marks[key] = round(sum(term_marks) / len(term_marks), 1)
    return marks


def _averages(rows, exam_type, weights):
    """{learner: mean of their subject marks}, rounded so equal averages tie."""
    by_learner = defaultdict(list)
    for (sid, _), mark in _subject_marks(rows, exam_type, weights).items():
        by_learner[sid].append(mark)
    return {sid: round(sum(m) / len(m), 2) for sid, m in by_learner.items()}


def _class_rows(class_name, year, terms, shared):
    """Approved mark-bearing scores of ``class_name`` in ``terms`` as
    (learner, term, subject, type, pct), each learner placed in the class
    they were in for those terms (same rule as compute_results)."""
    from academics.models import ExamScore, ScoreStatus
    from academics.performance import _classes_for_year
    from students.models import Student

    ids = classmate_ids(class_name, year, shared)
    term_ids = {t.pk for t in terms}
    raw = list(ExamScore.objects.filter(student_id__in=ids, term_id__in=term_ids, status=ScoreStatus.APPROVED)
               .mark_bearing().values_list("student_id", "term_id", "subject_name", "exam_type", "score", "max_score"))
    present = {r[0] for r in raw}
    current = dict(Student.objects.filter(id__in=present).values_list("id", "class_name"))
    year_classes = _classes_for_year(present, year, term_ids)
    members = {sid for sid in present if (year_classes.get(sid) or current.get(sid)) == class_name}
    return [(sid, tid, subject, etype, float(score) / (float(max_score or 100) or 100.0) * 100.0)
            for sid, tid, subject, etype, score, max_score in raw if sid in members]


def learner_positions(student, term, class_name, shared=None):
    """The learner's class positions for ``term`` and the year to date:
    ``[{"code", "label", "term": {"position", "of"} | None, "year": ... | None}]``.

    ``shared`` (a dict) keeps class-wide figures between learners when a
    whole class is printed together."""
    if term is None or not class_name:
        return []
    from academics.models import ExamTypeConfiguration

    configs = _cached(shared, ("exam_type_configs",), lambda: list(
        ExamTypeConfiguration.objects.values_list("code", "weight_percentage", "is_active")))
    weights = {code: float(w) for code, w, _ in configs}
    active = {code for code, _, is_active in configs if is_active}
    measures = [(code, label) for code, label in EXAM_MEASURES if code in active] + [(OVERALL, OVERALL_LABEL)]
    year = term.academic_year

    def averages():
        ytd = year_to_date_terms(term)
        periods = {"term": [term], "year": ytd}
        out = {}
        for period, terms in periods.items():
            rows = _class_rows(class_name, year, terms, shared)
            for code, _ in measures:
                out[(period, code)] = _averages(rows, None if code == OVERALL else code, weights)
        return out
    table = _cached(shared, ("class_positions", class_name, term.pk), averages)

    return [{"code": code, "label": label,
             "term": rank(table[("term", code)], student.pk),
             "year": rank(table[("year", code)], student.pk)}
            for code, label in measures]
