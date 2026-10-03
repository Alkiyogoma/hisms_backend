"""
Per-subject score progress for a learner in a term.

Approval and locking live on individual ExamScore rows (one learner, one
subject, one assessment type). A learner's report is complete only when every
subject taught to the class has scores and all of them are HOD-approved. The
grand average uses approved scores only and reports how many subjects it covers.
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


def build_progress(scores, expected_subjects, weights=None):
    """
    Summarise one learner's scores (an iterable of ExamScore for one term).

    Subjects that have scores but are not in ``expected_subjects`` are still
    reported, so nothing a teacher entered is hidden.
    """
    if weights is None:
        weights = get_exam_weights() or {"quiz": 20, "mid_term": 30, "end_of_term": 50}

    statuses = {}
    approved = {}
    for s in scores:
        statuses.setdefault(s.subject_name, set()).add(s.status)
        if s.status == ScoreStatus.APPROVED:
            approved.setdefault(s.subject_name, {})[s.exam_type] = float(s.score)

    all_subjects = sorted(set(expected_subjects) | set(statuses))
    subjects = {}
    averages = []
    for name in all_subjects:
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
        "approved_subjects": approved_count,
        "included_subjects": len(averages),
        "grand_average": round(sum(averages) / len(averages), 2) if averages else None,
        "is_complete": overall == STATUS_COMPLETE,
        "status": overall,
    }


def student_progress(student, term, expected_subjects=None):
    if expected_subjects is None:
        expected_subjects = expected_subjects_for_class(student.class_name)
    scores = ExamScore.objects.filter(student=student, term=term)
    return build_progress(scores, expected_subjects)


def class_progress(student_ids, term_id, class_name):
    """{student_id: progress} for a whole class in two queries."""
    expected = expected_subjects_for_class(class_name)
    weights = get_exam_weights() or {"quiz": 20, "mid_term": 30, "end_of_term": 50}
    by_student = {sid: [] for sid in student_ids}
    for s in ExamScore.objects.filter(student_id__in=student_ids, term_id=term_id):
        by_student[s.student_id].append(s)
    return {sid: build_progress(rows, expected, weights) for sid, rows in by_student.items()}


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
