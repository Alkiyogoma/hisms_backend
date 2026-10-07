"""
Performance Report — role-scoped learner and subject performance.

One report, filterable by academic year, term (or all terms), class, subject
and assessment type, with two views:

* "Who needs help" — learner/subject results sorted worst-first.
* "Subject performance across classes" — one subject compared class by class,
  plus every subject ranked within the current class filter.

What a user can see is bounded by their role (see ``resolve_scope``):

* Head of School / Super Admin — the whole school.
* Head of Department — every class in the section(s) they head.
* Class teacher — their class, all subjects.
* Subject teacher — their subject(s), in the classes they teach them.

Only marks that were entered *and approved* count. Assessments that have not
been marked are left out, never counted as zero.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from academics.grading_utils import get_grade_from_score
from academics.models import (
    Department,
    ExamScore,
    ExamTypeConfiguration,
    GradeClass,
    ScoreStatus,
    Subject,
)
from users.models import UserRole

PASS_MARK = 60.0       # grade C and above (see grading_utils.is_pass)
CRITICAL_MARK = 50.0   # grade E (see grading_utils.is_critical)


@dataclass
class Scope:
    """What a user may see. ``pairs`` maps class name -> subject names
    (``None`` meaning every subject); ``pairs is None`` means the whole school."""
    label: str
    kind: str  # "school" | "section" | "teacher"
    pairs: dict | None = None
    class_order: dict = field(default_factory=dict)

    def allows(self, class_name: str, subject: str) -> bool:
        if self.pairs is None:
            return True
        if class_name not in self.pairs:
            return False
        subjects = self.pairs[class_name]
        return subjects is None or subject in subjects

    @property
    def class_names(self) -> list:
        if self.pairs is None:
            names = list(GradeClass.objects.values_list("name", flat=True))
        else:
            names = list(self.pairs)
        return sorted(names, key=lambda n: (self.class_order.get(n, 999), n))

    @property
    def fixed_subjects(self):
        """Subject names when the scope only ever covers named subjects, else None."""
        if self.pairs is None or any(s is None for s in self.pairs.values()):
            return None
        return sorted(set().union(*self.pairs.values())) if self.pairs else []


def _class_order():
    return dict(GradeClass.objects.values_list("name", "sort_order"))


def resolve_scope(user, terms) -> Scope | None:
    """The user's scope for ``terms``; None when their role has no access."""
    order = _class_order()
    if user.is_school_wide:
        return Scope("Whole school", "school", None, order)

    from academics.approval_policy import SECTION_HEAD_ROLES, approver_class_names
    if user.has_role(*SECTION_HEAD_ROLES):
        classes = approver_class_names(user) or set()
        depts = user.section_departments or []
        label = " & ".join(Department(d).label for d in depts if d in Department.values) or "My section"
        return Scope(label, "section", {c: None for c in classes}, order)

    if user.has_role(UserRole.TEACHER):
        return _teacher_scope(user, terms, order)
    return None


def _teacher_scope(user, terms, order) -> Scope:
    from core.teacher_context import get_teacher_assigned_classes_from_tca
    from hr.models import TeacherClassAssignment

    assignments = {}
    qs = TeacherClassAssignment.objects.filter(teacher__user=user, term__in=terms).select_related("grade_class")
    for a in qs:
        entry = assignments.setdefault(a.grade_class.name, {"subjects": set(), "is_class_teacher": False})
        entry["subjects"].update(s.strip() for s in (a.subjects_taught or []) if isinstance(s, str) and s.strip())
        entry["is_class_teacher"] |= bool(a.is_class_teacher or a.is_assistant_class_teacher)
    if not assignments:
        # No assignment in the selected term(s): fall back to what the teacher
        # teaches now, so the page is not blank for past terms.
        assignments = get_teacher_assigned_classes_from_tca(user)

    pairs = {}
    for cls, info in assignments.items():
        if info["is_class_teacher"]:
            pairs[cls] = None
        elif info["subjects"]:
            pairs[cls] = set(info["subjects"])

    own_classes = [c for c, s in pairs.items() if s is None]
    subjects = set().union(*(s for s in pairs.values() if s is not None)) if pairs else set()
    if own_classes and not subjects:
        label = ", ".join(sorted(own_classes))
    elif subjects and not own_classes:
        label = ", ".join(sorted(subjects))
    elif pairs:
        label = "My classes & subjects"
    else:
        label = "No classes assigned"
    return Scope(label, "teacher", pairs, order)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _classes_for_year(student_ids, year, term_ids):
    """Class each student was in during ``year`` (latest enrolment record,
    preferring one tied to a selected term), so past years are grouped by the
    class a learner was actually in rather than their current one."""
    from students.models import EnrollmentHistory

    found = {}
    if year is None:
        return found
    rows = (
        EnrollmentHistory.objects.filter(student_id__in=student_ids, academic_year=year)
        .order_by("created_at", "id")
        .values_list("student_id", "term_id", "class_name")
    )
    for sid, tid, cls in rows:
        if not cls:
            continue
        if tid is None or tid in term_ids or sid not in found:
            found[sid] = cls
    return found


STRONG_MARK = 70.0     # grade B and above

BAND_LABELS = {
    "critical": "Critical",
    "support": "Requires support",
    "pass": "On track",
    "strong": "On track",
}


def _mark_band(mark):
    """critical (E) / support (D) need help; pass (C) and strong (B and
    above) are on track — split so comparisons above the pass mark still
    show a difference."""
    if mark < CRITICAL_MARK:
        return "critical"
    if mark < PASS_MARK:
        return "support"
    if mark < STRONG_MARK:
        return "pass"
    return "strong"


def needs_help(band):
    return band in ("critical", "support")


def compute_results(scope, terms, year, exam_type=None, student_ids=None):
    """One result per (learner, subject): the learner's mark in that subject
    across ``terms`` from approved scores only.

    With ``exam_type`` the mark is that assessment's percentage; otherwise it
    is the weighted average of the assessments marked so far in each term
    (weights rescaled over what has been marked), averaged across terms.
    """
    from students.models import Student

    term_ids = [t.id for t in terms]
    qs = ExamScore.objects.filter(term_id__in=term_ids, status=ScoreStatus.APPROVED).mark_bearing()
    if exam_type:
        qs = qs.filter(exam_type=exam_type)
    if student_ids is not None:
        qs = qs.filter(student_id__in=list(student_ids))
    fixed = scope.fixed_subjects
    if fixed is not None:
        qs = qs.filter(subject_name__in=fixed)

    raw = list(qs.values_list("student_id", "term_id", "subject_name", "exam_type", "score", "max_score"))
    if not raw:
        return []

    student_ids = {r[0] for r in raw}
    students = {
        s["id"]: s for s in Student.objects.filter(id__in=student_ids).values(
            "id", "first_name", "last_name", "admission_no", "class_name"
        )
    }
    year_classes = _classes_for_year(student_ids, year, set(term_ids))
    weights = {
        code: float(w) for code, w in ExamTypeConfiguration.objects.values_list("code", "weight_percentage")
    }

    # (student, subject) -> term -> [(pct, weight)]
    per_term = defaultdict(lambda: defaultdict(list))
    for sid, tid, subject, etype, score, max_score in raw:
        if sid not in students:
            continue
        cls = year_classes.get(sid) or students[sid]["class_name"]
        if not scope.allows(cls, subject):
            continue
        max_score = float(max_score or 100) or 100.0
        pct = float(score) / max_score * 100.0
        per_term[(sid, subject)][tid].append((pct, weights.get(etype, 0.0)))

    results = []
    for (sid, subject), terms_map in per_term.items():
        term_marks = []
        for marks in terms_map.values():
            total_w = sum(w for _, w in marks)
            if total_w > 0:
                term_marks.append(sum(p * w for p, w in marks) / total_w)
            else:
                term_marks.append(sum(p for p, _ in marks) / len(marks))
        mark = round(sum(term_marks) / len(term_marks), 1)
        st = students[sid]
        name = f"{st['first_name']} {st['last_name']}".strip()
        results.append({
            "student_id": sid,
            "name": name,
            "initials": "".join(p[0] for p in name.split()[:2]).upper(),
            "admission_no": st["admission_no"],
            "class_name": year_classes.get(sid) or st["class_name"],
            "subject": subject,
            "mark": mark,
            "grade": get_grade_from_score(mark),
            "band": _mark_band(mark),
            "status": BAND_LABELS[_mark_band(mark)],
            "assessments": sum(len(m) for m in terms_map.values()),
        })
    return results


def summarise(results):
    n = len(results)
    marks = [r["mark"] for r in results]
    return {
        "count": n,
        "learners": len({r["student_id"] for r in results}),
        "average": round(sum(marks) / n, 1) if n else None,
        "average_grade": get_grade_from_score(sum(marks) / n) if n else None,
        "pass_rate": round(sum(1 for m in marks if m >= PASS_MARK) / n * 100, 1) if n else None,
        "flagged": sum(1 for r in results if r["band"] == "support"),
        "critical": sum(1 for r in results if r["band"] == "critical"),
        "below_pass": sum(1 for r in results if needs_help(r["band"])),
        "learners_below_pass": len({r["student_id"] for r in results if needs_help(r["band"])}),
    }


def _group(results, key):
    groups = defaultdict(list)
    for r in results:
        groups[r[key]].append(r)
    out = []
    for name, rows in groups.items():
        avg = sum(r["mark"] for r in rows) / len(rows)
        out.append({
            "name": name,
            "average": round(avg, 1),
            "band": _mark_band(avg),
            "results": len(rows),
            "learners": len({r["student_id"] for r in rows}),
            "below_pass": sum(1 for r in rows if r["mark"] < PASS_MARK),
        })
    return out


def subject_by_class(results, subject, scope):
    """``subject`` compared across every class in scope that has marks for it."""
    rows = _group([r for r in results if r["subject"] == subject], "class_name")
    rows.sort(key=lambda g: (scope.class_order.get(g["name"], 999), g["name"]))
    return rows


def subject_ranking(results):
    rows = _group(results, "subject")
    rows.sort(key=lambda g: (-g["average"], g["name"]))
    return rows


def subject_options(scope, results):
    fixed = scope.fixed_subjects
    if fixed is not None:
        return fixed
    names = set(r["subject"] for r in results)
    subjects = Subject.objects.filter(is_active=True)
    if scope.pairs is not None:
        subjects = subjects.filter(classes__name__in=list(scope.pairs))
    names.update(subjects.values_list("name", flat=True))
    return sorted(names)


def learner_summary(results):
    """One row per learner: their average across the subjects in ``results``
    and the subjects they are below the pass mark in (weakest first)."""
    by_learner = defaultdict(list)
    for r in results:
        by_learner[r["student_id"]].append(r)
    rows = []
    for rs in by_learner.values():
        first = rs[0]
        avg = round(sum(r["mark"] for r in rs) / len(rs), 1)
        weak = sorted((r for r in rs if needs_help(r["band"])), key=lambda r: r["mark"])
        band = _mark_band(avg)
        rows.append({
            "student_id": first["student_id"],
            "name": first["name"],
            "initials": first["initials"],
            "admission_no": first["admission_no"],
            "class_name": first["class_name"],
            "mark": avg,
            "grade": get_grade_from_score(avg),
            # A learner needs help if their average or any one subject is
            # below the pass mark; the status reflects the worse of the two.
            "band": band if needs_help(band) or not weak else weak[0]["band"],
            "subjects": len(rs),
            "weak": [{"subject": r["subject"], "mark": r["mark"], "band": r["band"]} for r in weak],
        })
    for row in rows:
        row["status"] = BAND_LABELS[row["band"]]
    return rows


def class_subject_matrix(results, scope):
    """Average per class (rows) x subject (columns), with row and column
    averages, for the at-a-glance heatmap."""
    cells = defaultdict(list)
    for r in results:
        cells[(r["class_name"], r["subject"])].append(r["mark"])
    if not cells:
        return None
    subjects = sorted({s for _, s in cells})
    classes = sorted({c for c, _ in cells}, key=lambda n: (scope.class_order.get(n, 999), n))

    def cell(marks):
        if not marks:
            return None
        avg = round(sum(marks) / len(marks), 1)
        return {"average": avg, "band": _mark_band(avg), "n": len(marks)}

    rows = []
    for c in classes:
        row_marks = [m for s in subjects for m in cells.get((c, s), [])]
        rows.append({
            "name": c,
            "cells": [cell(cells.get((c, s), [])) for s in subjects],
            "overall": cell(row_marks),
        })
    totals = [cell([m for c in classes for m in cells.get((c, s), [])]) for s in subjects]
    overall = cell([m for marks in cells.values() for m in marks])
    return {"subjects": subjects, "rows": rows, "totals": totals, "overall": overall}


# -- one learner ------------------------------------------------------------

def learner_terms(student):
    """Terms in which the learner has approved marks, oldest first."""
    from academics.models import Term
    ids = (ExamScore.objects.filter(student=student, status=ScoreStatus.APPROVED).mark_bearing()
           .values_list("term_id", flat=True).distinct())
    return list(Term.objects.filter(id__in=list(ids)).select_related("academic_year")
                .order_by("start_date", "pk"))


def default_learner_term(student, terms=None):
    """The current term when the learner has approved marks in it, else the
    latest term they have marks in, else the current term."""
    from academics.utils import get_current_term
    terms = learner_terms(student) if terms is None else terms
    current = get_current_term()
    if current and current in terms:
        return current
    return terms[-1] if terms else current


def learner_record(student, term):
    """One learner's approved results for ``term``: each subject's mark per
    assessment and weighted, with the class average and the learner's
    position in class, plus their average in every term so far."""
    school = Scope(label="Whole school", kind="school")
    types = list(ExamTypeConfiguration.objects.filter(is_active=True).order_by("display_order", "name"))
    record = {"term": term, "types": types, "subjects": [], "average": None, "grade": None, "band": None,
              "below_pass": [], "position": None, "class_size": 0, "class_average": None,
              "history": [], "trend": None, "assessments": 0, "class_diff": None}
    if term is None:
        return record
    year = term.academic_year
    everyone = compute_results(school, [term], year)
    mine = [r for r in everyone if r["student_id"] == student.pk]
    if mine:
        class_name = mine[0]["class_name"]
        classmates = [r for r in everyone if r["class_name"] == class_name]
        subject_avgs = {g["name"]: g["average"] for g in _group(classmates, "subject")}
        cells = defaultdict(lambda: defaultdict(list))
        for subject, etype, score, max_score in ExamScore.objects.filter(
                student=student, term=term, status=ScoreStatus.APPROVED).mark_bearing().values_list(
                "subject_name", "exam_type", "score", "max_score"):
            cells[subject][etype].append(float(score) / (float(max_score or 100) or 100.0) * 100.0)
        for r in sorted(mine, key=lambda r: r["subject"]):
            per_type = cells.get(r["subject"], {})
            record["subjects"].append({
                **r,
                "cells": [round(sum(per_type[t.code]) / len(per_type[t.code]), 1) if per_type.get(t.code) else None
                          for t in types],
                "class_average": subject_avgs.get(r["subject"]),
                "diff": round(r["mark"] - subject_avgs[r["subject"]], 1) if r["subject"] in subject_avgs else None,
            })
        marks = [r["mark"] for r in mine]
        avg = round(sum(marks) / len(marks), 1)
        record.update(average=avg, grade=get_grade_from_score(avg), band=_mark_band(avg),
                      below_pass=[r for r in record["subjects"] if needs_help(r["band"])],
                      assessments=sum(r["assessments"] for r in mine), class_name=class_name)
        # Position in class by average mark across subjects.
        by_learner = defaultdict(list)
        for r in classmates:
            by_learner[r["student_id"]].append(r["mark"])
        averages = sorted((round(sum(m) / len(m), 1) for m in by_learner.values()), reverse=True)
        record["position"] = averages.index(avg) + 1
        record["class_size"] = len(averages)
        record["class_average"] = round(sum(averages) / len(averages), 1)
        record["class_diff"] = round(avg - record["class_average"], 1)
    for t in learner_terms(student):
        rows = compute_results(school, [t], t.academic_year, student_ids=[student.pk])
        if rows:
            a = round(sum(r["mark"] for r in rows) / len(rows), 1)
            record["history"].append({"term": t, "average": a, "grade": get_grade_from_score(a),
                                      "band": _mark_band(a), "subjects": len(rows), "current": t == term})
    past = [h for h in record["history"] if h["term"].start_date and term.start_date
            and h["term"].start_date < term.start_date]
    if past and record["average"] is not None:
        diff = record["average"] - past[-1]["average"]
        record["trend"] = "improving" if diff > 1 else "declining" if diff < -1 else "stable"
        record["trend_diff"] = round(diff, 1)
    return record
