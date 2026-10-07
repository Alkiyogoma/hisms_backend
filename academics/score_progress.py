"""
Per-subject score progress for a learner in a term.

Approval and locking live on individual ExamScore rows (one learner, one
subject, one assessment type). A learner's report is complete only when every
subject taught to the class has scores and all of them are HOD-approved. The
grand average uses approved scores only and reports how many subjects it covers.

Remark-only subjects take no marks: one is done once its term remark is
entered, and any marks stored against it are ignored.
"""
from academics.grading_utils import compute_grade_with_gaps
from academics.models import ExamScore, GradeClass, ScoreStatus, get_exam_weights

# Learner-level status, most urgent first.
STATUS_RETURNED = "returned"
STATUS_COMPLETE = "approved"      # every subject approved — the report is complete
STATUS_SUBMITTED = "submitted"    # something is waiting for HOD review
STATUS_PARTIAL = "partial"        # some subjects approved, others still outstanding
STATUS_DRAFT = "draft"            # scores saved but nothing submitted yet
STATUS_PENDING = "pending"        # no scores at all


def expected_subjects_for_class(class_name):
    """Active subjects assigned to the class (empty set when none configured)."""
    gc = GradeClass.objects.filter(name=class_name).first()
    if not gc:
        return set()
    return set(gc.subjects.filter(is_active=True).values_list("name", flat=True))


def _subject_status(statuses):
    if not statuses:
        return "not_started"
    if ScoreStatus.RETURNED in statuses:
        return ScoreStatus.RETURNED
    if ScoreStatus.DRAFT in statuses:
        return ScoreStatus.DRAFT
    if ScoreStatus.SUBMITTED in statuses:
        return ScoreStatus.SUBMITTED
    return ScoreStatus.APPROVED


def build_progress(scores, expected_subjects, weights=None, remark_subjects=frozenset(), remarks=None):
    """
    Summarise one learner's scores (an iterable of ExamScore for one term).

    Subjects that have scores but are not in ``expected_subjects`` are still
    reported, so nothing a teacher entered is hidden. ``remark_subjects`` are
    remark-only; ``remarks`` is the learner's {subject: remark} for the term.
    """
    if weights is None:
        weights = get_exam_weights() or {"quiz": 20, "mid_term": 30, "end_of_term": 50}
    remarks = remarks or {}

    statuses = {}
    approved = {}
    for s in scores:
        if s.subject_name in remark_subjects:
            continue
        statuses.setdefault(s.subject_name, set()).add(s.status)
        if s.status == ScoreStatus.APPROVED:
            approved.setdefault(s.subject_name, {})[s.exam_type] = float(s.score)

    all_subjects = sorted(set(expected_subjects) | set(statuses))
    subjects = {}
    averages = []
    for name in all_subjects:
        if name in remark_subjects:
            subjects[name] = {
                "status": ScoreStatus.APPROVED if remarks.get(name) else "not_started",
                "approved_average": None, "remark_only": True,
            }
            continue
        status = _subject_status(statuses.get(name, set()))
        avg = None
        if name in approved:
            avg = compute_grade_with_gaps(scores=approved[name], weights=weights)["average"]
            if avg is not None:
                averages.append(avg)
        subjects[name] = {"status": status, "approved_average": avg}

    approved_count = sum(1 for v in subjects.values() if v["status"] == ScoreStatus.APPROVED)
    total = len(all_subjects)
    found = {v["status"] for v in subjects.values()}

    if ScoreStatus.RETURNED in found:
        overall = STATUS_RETURNED
    elif total and approved_count == total:
        overall = STATUS_COMPLETE
    elif ScoreStatus.SUBMITTED in found:
        overall = STATUS_SUBMITTED
    elif approved_count:
        overall = STATUS_PARTIAL
    elif ScoreStatus.DRAFT in found:
        overall = STATUS_DRAFT
    else:
        overall = STATUS_PENDING

    return {
        "subjects": subjects,
        "total_subjects": total,
        "mark_subjects": sum(1 for v in subjects.values() if not v.get("remark_only")),
        "approved_subjects": approved_count,
        "included_subjects": len(averages),
        "grand_average": round(sum(averages) / len(averages), 2) if averages else None,
        "is_complete": overall == STATUS_COMPLETE,
        "status": overall,
    }


def student_progress(student, term, expected_subjects=None):
    from academics.remarks import remark_subject_names, remarks_for
    if expected_subjects is None:
        expected_subjects = expected_subjects_for_class(student.class_name)
    scores = ExamScore.objects.filter(student=student, term=term)
    return build_progress(
        scores, expected_subjects, remark_subjects=remark_subject_names(),
        remarks=remarks_for(student, term),
    )


def class_progress(student_ids, term_id, class_name):
    """{student_id: progress} for a whole class in two queries."""
    expected = expected_subjects_for_class(class_name)
    weights = get_exam_weights() or {"quiz": 20, "mid_term": 30, "end_of_term": 50}
    from academics.models import SubjectTermRemark
    from academics.remarks import remark_subject_names
    remark_subjects = remark_subject_names()
    by_student = {sid: [] for sid in student_ids}
    for s in ExamScore.objects.filter(student_id__in=student_ids, term_id=term_id):
        by_student[s.student_id].append(s)
    remarks = {sid: {} for sid in student_ids}
    for sid, subject, remark in SubjectTermRemark.objects.filter(
            student_id__in=student_ids, term_id=term_id).values_list("student_id", "subject_name", "remark"):
        remarks.setdefault(sid, {})[subject] = remark
    return {
        sid: build_progress(rows, expected, weights, remark_subjects, remarks.get(sid))
        for sid, rows in by_student.items()
    }


def sync_report_status(student, term):
    """
    Keep an unpublished report's status in step with its subjects: pending
    sign-off once every subject is submitted/approved, otherwise draft.
    ECD reports (domain evaluations, not exam scores) and published reports
    are left alone.
    """
    from academics.models import ReportCard, ReportCardStatus

    rc = ReportCard.objects.filter(student=student, term=term).first()
    if not rc or rc.is_ecd_report or rc.status == ReportCardStatus.PUBLISHED:
        return rc
    new_status = (
        ReportCardStatus.PENDING_SIGN_OFF
        if is_ready_for_sign_off(student, term) else ReportCardStatus.DRAFT
    )
    if rc.status != new_status:
        rc.status = new_status
        rc.save(update_fields=["status", "updated_at"])
    return rc


def is_ready_for_sign_off(student, term):
    """Every expected subject has scores and none are draft/returned/missing."""
    progress = student_progress(student, term)
    if not progress["total_subjects"]:
        return False
    return all(
        v["status"] in (ScoreStatus.SUBMITTED, ScoreStatus.APPROVED)
        for v in progress["subjects"].values()
    )


# -- the report's ACADEMIC PROGRESS table ------------------------------------

def report_grade(mark):
    """(grade, label) on the printed report's scale (A* for 90+)."""
    from academics.grading_utils import get_grade_from_score, get_grade_label
    grade = get_grade_from_score(mark)
    return ("A*" if grade == "A+" else grade), get_grade_label(grade)


def exam_summary(student, term, expected_subjects=None):
    """
    The marks table of a learner's progress report, from approved scores of
    mark-bearing subjects only. Remark-only subjects never count.

    Each assessment column shows weighted points (score / max x weight), so a
    subject's total is out of 100. A subject has a total and grade only once
    every assessment is approved. The exam average, total and grade are given
    only when every mark-bearing subject of the class is fully assessed;
    otherwise they are None and the report shows "Awaiting end of term".
    """
    from academics.models import ExamTypeConfiguration
    from academics.remarks import remark_subject_names

    types = list(ExamTypeConfiguration.objects.filter(is_active=True).order_by("display_order", "name"))
    if expected_subjects is None:
        expected_subjects = expected_subjects_for_class(student.class_name)

    points = {}
    for subject, code, score, max_score in ExamScore.objects.filter(
            student=student, term=term, status=ScoreStatus.APPROVED,
    ).mark_bearing().values_list("subject_name", "exam_type", "score", "max_score"):
        cfg = next((t for t in types if t.code == code), None)
        if cfg is None:
            continue
        points.setdefault(subject, {})[code] = (
            float(score) / (float(max_score or 100) or 100.0) * float(cfg.weight_percentage)
        )

    rows = []
    for subject in sorted(points):
        cells = [round(points[subject][t.code], 1) if t.code in points[subject] else None for t in types]
        complete = bool(types) and all(c is not None for c in cells)
        total = round(sum(points[subject][t.code] for t in types), 1) if complete else None
        grade, label = report_grade(total) if complete else (None, None)
        rows.append({"subject": subject, "cells": cells, "complete": complete,
                     "total": total, "grade": grade, "label": label})

    mark_subjects = (set(expected_subjects) | set(points)) - remark_subject_names()
    assessed = sum(1 for r in rows if r["complete"])
    summary = {
        "types": types,
        "rows": rows,
        "assessed": assessed,
        "total_subjects": len(mark_subjects),
        "complete": bool(mark_subjects) and assessed == len(mark_subjects),
        "column_averages": None, "average": None, "grade": None, "label": None,
    }
    if summary["complete"]:
        done = [r for r in rows if r["complete"]]
        summary["column_averages"] = [
            round(sum(r["cells"][i] for r in done) / len(done), 1) for i in range(len(types))
        ]
        summary["average"] = round(sum(r["total"] for r in done) / len(done), 2)
        summary["grade"], summary["label"] = report_grade(summary["average"])
    return summary
