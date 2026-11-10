# PTC Service Layer  Business logic for Learner Progress Report module.
#
# Adjustment notes vs FRD assumptions:
# - Core subjects are read dynamically from the GradeClass subject M2M, not hardcoded.
# - "Class teacher" identity is determined via TeacherClassAssignment.is_class_teacher,
#   since the User model has a single TEACHER role (no distinct class/subject/specialist roles).
# - Weighted average reuses the existing ExamTypeConfiguration weights from academics app
#   to avoid duplicating 20/30/50 logic.
# - Attendance is always shown on the PTC report, per term (unmarked days excluded).
# - OI-PTC-03: term-to-term comparison baseline used by default; isolated in its own function.

from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from academics.grading_utils import compute_grade_with_gaps, get_pass_mark

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-request cache
# ---------------------------------------------------------------------------
# A PTC page asks for the current term, the user's class assignments and a
# class's subjects many times over. Inside ``request_cache()`` (wrapped round
# every PTC view) each is read once; outside it nothing is cached.

import contextvars
from contextlib import contextmanager

_REQUEST_CACHE: contextvars.ContextVar = contextvars.ContextVar("ptc_request_cache", default=None)


@contextmanager
def request_cache():
    token = _REQUEST_CACHE.set({})
    try:
        yield
    finally:
        _REQUEST_CACHE.reset(token)


def _memo(key, compute):
    cache = _REQUEST_CACHE.get()
    if cache is None:
        return compute()
    if key not in cache:
        cache[key] = compute()
    return cache[key]


def current_term():
    from academics.utils import get_current_term
    return _memo("current_term", get_current_term)


def current_academic_year():
    from academics.utils import get_current_academic_year
    return _memo("current_academic_year", get_current_academic_year)


def _teacher_assignments(user) -> list:
    """The user's TeacherClassAssignment rows for the current term."""
    from hr.models import TeacherClassAssignment

    def compute():
        term = current_term()
        if not term or not getattr(user, "pk", None):
            return []
        return list(
            TeacherClassAssignment.objects.filter(teacher__user=user, term=term).select_related("grade_class")
        )
    return _memo(("tca", getattr(user, "pk", None)), compute)


def _grade_classes() -> dict:
    """{class name: GradeClass}."""
    from academics.models import GradeClass
    return _memo("grade_classes", lambda: {gc.name: gc for gc in GradeClass.objects.all()})


def _class_subjects(class_name: str) -> list[tuple[str, str]]:
    """[(subject name, assessment mode)] of a class's active subjects, by name."""
    def compute():
        gc = _grade_classes().get(class_name)
        if not gc:
            return []
        return list(
            gc.subjects.filter(is_active=True).order_by("name").values_list("name", "assessment_mode")
        )
    return _memo(("class_subjects", class_name), compute)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IMPROVING_THRESHOLD = 3  # percentage points
NEEDS_ATTENTION_THRESHOLD = -3

# Hardcoded ECD fallback names  used when Department.ECD lookup is unavailable.
ECD_FALLBACK_NAMES = {"Pre-Kindergarten", "Kindergarten", "Pre-School", "ABC Class"}


def _get_ecd_class_names() -> set:
    """Return ECD class names from the registry, falling back to hardcoded names."""
    return set(_memo("ecd_class_names", _load_ecd_class_names))


def _load_ecd_class_names() -> set:
    try:
        from academics.models import Department
        db_names = {name for name, gc in _grade_classes().items() if gc.department == Department.ECD}
        if db_names:
            return db_names
    except Exception:
        pass
    return set(ECD_FALLBACK_NAMES)


# ---------------------------------------------------------------------------
# Academic Performance Grid
# ---------------------------------------------------------------------------

def get_marks_subjects_for_class(class_name: str) -> list[str]:
    """Mark-bearing subjects of a class (Quiz, Mid-Term, End-Term), by name.
    These are the subjects with marks, an average and a PTC subject comment."""
    from academics.models import AssessmentMode

    return [n for n, mode in _class_subjects(class_name) if mode != AssessmentMode.REMARK]


def get_remark_subjects_for_class(class_name: str) -> list[str]:
    """Remark-only subjects of a class: no marks, one remark grade per term."""
    from academics.models import AssessmentMode

    return [n for n, mode in _class_subjects(class_name) if mode == AssessmentMode.REMARK]


def get_core_subjects_for_student(student) -> list[str]:
    """The mark-bearing subjects of the student's class."""
    return get_marks_subjects_for_class(student.class_name)


def get_academic_performance_grid(student, academic_year) -> dict:
    """
    Pulls Quiz, Mid Term, End Term scores from Grade Entry across all three terms
    to date for the core subjects configured for the student's grade.

    Returns:
        {
            "subjects": ["English", "Mathematics", ...],
            "terms": [
                {
                    "term": <Term>,
                    "term_label": "Term 1",
                    "subjects": {
                        "English": {"quiz": 85, "mid_term": None, "end_term": None, "weighted_avg": None},
                        ...
                    },
                    "overall_average": 72.5,
                    "meets_expectations": "Yes",
                },
                ...
            ],
            "all_terms_averages": [72.5, 68.0, ...],
        }
    """
    from academics.models import ExamScore, ExamTypeConfiguration, Term, ScoreStatus

    subjects = get_core_subjects_for_student(student)
    if not subjects:
        return {"subjects": [], "terms": [], "all_terms_averages": []}

    terms = Term.objects.filter(
        academic_year=academic_year
    ).order_by("start_date")

    # Get active exam types and their weights
    exam_types = list(
        ExamTypeConfiguration.objects.filter(is_active=True).order_by("display_order")
    )
    exam_weights = {et.code: float(et.weight_percentage) for et in exam_types}

    terms_data = []
    all_averages = []

    for term in terms:
        scores = ExamScore.objects.filter(
            student=student,
            term=term,
            status=ScoreStatus.APPROVED,
        )
        # Group by subject → exam_type
        score_map = {}
        for s in scores:
            if s.subject_name not in score_map:
                score_map[s.subject_name] = {}
            score_map[s.subject_name][s.exam_type] = float(s.score)

        term_subjects = {}
        subject_avgs = []

        for subj in subjects:
            exams = score_map.get(subj, {})
            quiz_score = exams.get("quiz")
            mid_term_score = exams.get("mid_term")
            end_term_score = exams.get("end_of_term")

            # Build per-exam data with blanks (None) for unsat exams
            exam_data = {
                "quiz": quiz_score,
                "mid_term": mid_term_score,
                "end_term": end_term_score,
            }

            # Calculate weighted average for this subject (pass cached weights to avoid DB hits)
            grade_result = compute_grade_with_gaps(
                scores=exams,
                weights=exam_weights,
            )
            exam_data["weighted_avg"] = grade_result["average"] if exams else None
            exam_data["gap_case"] = grade_result["gap"]["case"]
            exam_data["gap_label"] = grade_result["gap"]["label"]
            exam_data["redistributed_weights"] = grade_result["redistributed_weights"]
            exam_data["makeup_required"] = grade_result["makeup_required"]

            # A subject with no approved marks yet is left out of the
            # average, never counted as zero.
            if exams and grade_result["average"] is not None:
                subject_avgs.append(grade_result["average"])

            term_subjects[subj] = exam_data

        # Overall average for this term
        term_overall = (
            round(sum(subject_avgs) / len(subject_avgs), 1)
            if subject_avgs
            else None
        )
        meets = calculate_meets_expectations(term_overall)

        if term_overall is not None:
            all_averages.append(term_overall)

        terms_data.append({
            "term": term,
            "term_label": term.name,
            "subjects": term_subjects,
            "overall_average": term_overall,
            "meets_expectations": meets,
        })

    return {
        "subjects": subjects,
        "terms": terms_data,
        "all_terms_averages": all_averages,
        "exam_weights": exam_weights,
    }


# ---------------------------------------------------------------------------
# Weighted Average  reuses existing ExamTypeConfiguration weights
# ---------------------------------------------------------------------------

def calculate_weighted_average(
    quiz: Optional[float],
    mid_term: Optional[float],
    end_term: Optional[float],
    weights: dict = None,
) -> Optional[float]:
    """
    Calculate weighted average from exam scores using configured exam weights.
    Delegates to compute_grade_with_gaps() for redistribution logic.
    Returns None if no scores available (case 7  all missing).
    Accepts optional weights dict to avoid repeated DB queries.
    """
    result = compute_grade_with_gaps(quiz, mid_term, end_term, weights=weights)
    return result["average"]


# ---------------------------------------------------------------------------
# Meets Academic Expectations
# ---------------------------------------------------------------------------

def calculate_meets_expectations(overall_average: Optional[float]) -> str:
    """
    Returns "Yes" if overall_average is at or above the school pass mark
    (School Settings, 50% by default), "No" otherwise.
    If average is None (no data), returns "N/A".
    """
    if overall_average is None:
        return "N/A"
    return "Yes" if overall_average >= get_pass_mark() else "No"


# ---------------------------------------------------------------------------
# Progress Trend (FR-PTC-029, OI-PTC-03)
# ---------------------------------------------------------------------------

def _compare_baseline_term_to_term(
    current_overall_average: Optional[float],
    student,
    academic_year,
    all_terms_averages: list[float] | None = None,
) -> Optional[float]:
    """
    OI-PTC-03: Default comparison baseline  term-to-term.
    Compares current overall average to the previous term's overall average.
    Isolated in its own function so the comparison can be swapped to exam-to-exam
    later without touching the rest of the trend logic.

    If all_terms_averages is provided (pre-computed from the grid), use it directly
    to avoid redundant DB queries. Otherwise falls back to computing the grid.
    """
    from academics.models import Term

    if current_overall_average is None:
        return None

    # Use pre-computed averages if available (avoids N+1 query pattern)
    if all_terms_averages is not None and len(all_terms_averages) >= 2:
        # Find the current average in the ordered list, return the one before it
        rounded_current = round(current_overall_average, 1)
        for i in range(len(all_terms_averages) - 1, -1, -1):
            if abs(round(all_terms_averages[i], 1) - rounded_current) < 0.05:
                if i > 0:
                    return all_terms_averages[i - 1]
                return None
        return None

    # Fallback: compute the grid (only when pre-computed data not available)
    grid = get_academic_performance_grid(student, academic_year)
    term_averages = {td["term"].id: td["overall_average"] for td in grid["terms"]}
    terms = list(
        Term.objects.filter(academic_year=academic_year).order_by("start_date")
    )

    # Use epsilon comparison for floats
    rounded_current = round(current_overall_average, 1)
    for i in range(len(terms) - 1, -1, -1):
        avg = term_averages.get(terms[i].id)
        if avg is not None and abs(round(avg, 1) - rounded_current) < 0.05:
            if i > 0:
                prev_avg = term_averages.get(terms[i - 1].id)
                if prev_avg is not None:
                    return prev_avg
            break

    return None


def calculate_progress_trend(
    student,
    current_overall_average: Optional[float],
    academic_year,
    all_terms_averages: list[float] | None = None,
) -> dict:
    """
    FR-PTC-029: Compare current to previous data point.
    Returns {"trend": str, "auto_derived": bool, "previous_average": float|None}
    """
    previous_avg = _compare_baseline_term_to_term(
        current_overall_average, student, academic_year,
        all_terms_averages=all_terms_averages,
    )

    if previous_avg is None or current_overall_average is None:
        return {
            "trend": "Insufficient data",
            "auto_derived": True,
            "previous_average": previous_avg,
        }

    diff = current_overall_average - previous_avg

    if diff > IMPROVING_THRESHOLD:
        trend = "Improving"
    elif diff < NEEDS_ATTENTION_THRESHOLD:
        trend = "Needs Attention"
    else:
        trend = "Stable"

    return {
        "trend": trend,
        "auto_derived": True,
        "previous_average": previous_avg,
    }


# ---------------------------------------------------------------------------
# Attendance Summary (OI-PTC-01)
# ---------------------------------------------------------------------------

def get_attendance_summary(student, academic_year) -> dict:
    """
    Days present, absent, late to date across all terms, pulled from Attendance module.
    Gated behind PTC_SHOW_ATTENDANCE_SUMMARY feature flag.
    """
    from attendance.models import PRESENT_STATUSES, AttendanceEntry, AttendanceStatus
    from academics.models import Term

    terms = Term.objects.filter(academic_year=academic_year)
    if not terms.exists():
        return {"present": 0, "absent": 0, "late": 0, "total": 0, "rate": 0}

    # Get earliest start date and today
    earliest = terms.order_by("start_date").first().start_date
    today = timezone.now().date()

    entries = AttendanceEntry.objects.filter(
        student=student,
        date__range=[earliest, today],
    )

    late = entries.filter(status=AttendanceStatus.LATE).count()
    present = entries.filter(status__in=PRESENT_STATUSES).count()  # includes late
    absent = entries.filter(status=AttendanceStatus.ABSENT).count()
    total = present + absent

    rate = round((present / total) * 100, 1) if total > 0 else 0

    return {
        "present": present,
        "absent": absent,
        "late": late,
        "total": total,
        "rate": rate,
    }


# ---------------------------------------------------------------------------
# Permission checks
# ---------------------------------------------------------------------------

def _get_teacher_class_names(user) -> set:
    """Class names the teacher is assigned to this term."""
    return {a.grade_class.name for a in _teacher_assignments(user)}


def _get_teacher_subjects(user) -> set:
    """Subject names this teacher is assigned to teach this term."""
    out = set()
    for a in _teacher_assignments(user):
        out.update(a.subjects_taught or [])
    return out


def _is_class_teacher(user, class_name: str = None) -> bool:
    """Check if user is a class teacher (optionally for a specific class)."""
    return any(
        a.is_class_teacher and (not class_name or a.grade_class.name == class_name)
        for a in _teacher_assignments(user)
    )


def _is_assistant_class_teacher(user, class_name: str = None) -> bool:
    """Assistant class teacher this term (optionally of ``class_name``). They
    may view the PTC report of students in that class, never change it."""
    return any(
        a.is_assistant_class_teacher and (not class_name or a.grade_class.name == class_name)
        for a in _teacher_assignments(user)
    )


def _get_class_teacher_for_class(class_name: str):
    """Get the class teacher User for a given class name."""
    from hr.models import TeacherClassAssignment

    def compute():
        term = current_term()
        if not term:
            return None
        assignment = TeacherClassAssignment.objects.filter(
            term=term, grade_class__name=class_name, is_class_teacher=True,
        ).select_related("teacher__user").first()
        return assignment.teacher.user if assignment else None
    return _memo(("class_teacher", class_name), compute)


def _is_ecd_student(student) -> bool:
    """Check if student is in ECD (Grades < 1 or ECD department classes)."""
    from academics.models import Department
    grade_class = _grade_classes().get(student.class_name)
    if grade_class:
        return grade_class.department == Department.ECD
    # Fallback: ECD class names
    return student.class_name in ECD_FALLBACK_NAMES


def _get_ptc_eligible_students(user, ptc_window=None):
    """
    Get students eligible for PTC: Grades 1-9 only, no ECD (FR-PTC-032).
    For class teachers: only students in their class.
    """
    from students.models import Student
    from academics.models import GradeClass, Department

    # Exclude ECD students  enforced at query level
    ecd_class_names = set(
        GradeClass.objects.filter(department=Department.ECD).values_list("name", flat=True)
    )
    ecd_names = _get_ecd_class_names()

    students = Student.objects.filter(
        is_archived=False,
        status="active",
    ).exclude(class_name__in=ecd_class_names | ecd_names)

    # For class teachers: filter to own class only (FR-PTC-037)
    if _is_class_teacher(user) or _is_assistant_class_teacher(user):
        own_classes = _get_teacher_class_names(user)
        students = students.filter(class_name__in=own_classes)

    return students.order_by("class_name", "last_name", "first_name")


def can_open_ptc(user, student) -> bool:
    """
    FR-PTC-037: Class teacher can only open PTC for students in their own class.
    Enforced at query level (class teacher assignment check), not just UI.
    """
    # Super Admin / HOS / Primary HOD can view any student's PTC
    from users.models import UserRole
    if user.role in {
        UserRole.SUPER_ADMIN,
        UserRole.HEAD_OF_SCHOOL,
        UserRole.PRIMARY_HOD,
        UserRole.LOWER_SECONDARY_HOD,
    }:
        return True

    # Class teacher: must be assigned to the student's class
    if _is_class_teacher(user, student.class_name):
        return True

    # Assistant class teacher: view only, their own class (the screen is
    # read-only for anyone but the class teacher and leadership).
    if _is_assistant_class_teacher(user, student.class_name):
        return True

    return False


def can_enter_comment(user, student, subject_name: str, ptc_window) -> bool:
    """
    Subject teachers can only comment on subjects they teach, within the comment window.
    FR-PTC-014: read-only outside window.
    """
    from users.models import UserRole

    if user.role == UserRole.SUPER_ADMIN:
        return True

    # Must be within the comment entry window
    if not ptc_window.is_comment_entry_window_open:
        return False

    # Subject teacher must be assigned to this subject
    teacher_subjects = _get_teacher_subjects(user)
    return subject_name in teacher_subjects


def get_primary_hod_classes(user):
    """Get classes scoped to Primary HOD."""
    from academics.models import GradeClass, Department

    if user.role == "super_admin":
        return list(GradeClass.objects.values_list("name", flat=True))
    return list(
        GradeClass.objects.filter(department=Department.PRIMARY).values_list("name", flat=True)
    )


def get_subject_teacher_students(user, subject_name: str):
    """Get all students a subject teacher teaches in their assigned classes."""
    from students.models import Student
    from academics.models import GradeClass, Department

    teacher_classes = _get_teacher_class_names(user)

    students = Student.objects.filter(
        is_archived=False,
        status="active",
        class_name__in=teacher_classes,
    ).exclude(class_name__in=_get_ecd_class_names())

    return students.order_by("class_name", "last_name", "first_name")


# ---------------------------------------------------------------------------
# Teacher scope: which classes and subjects a user works with
# ---------------------------------------------------------------------------

PTC_ADMIN_ROLES = ("super_admin", "head_of_school", "primary_hod", "lower_secondary_hod")


def is_ptc_admin(user) -> bool:
    return getattr(user, "role", None) in PTC_ADMIN_ROLES


def get_ptc_classes() -> list[str]:
    """Every non-ECD class, in display order."""
    from academics.models import Department, GradeClass

    ecd = _get_ecd_class_names()
    classes = sorted(_grade_classes().values(), key=lambda gc: (gc.sort_order, gc.name))
    return [gc.name for gc in classes if gc.department != Department.ECD and gc.name not in ecd]


def get_teacher_subjects_by_class(user) -> dict[str, set]:
    """{class name: subjects the user teaches there} for the current term."""
    out: dict[str, set] = {}
    for a in _teacher_assignments(user):
        out.setdefault(a.grade_class.name, set()).update(a.subjects_taught or [])
    return out


def get_class_teacher_classes(user) -> list[str]:
    """Classes the user is class teacher of in the current term."""
    return sorted({a.grade_class.name for a in _teacher_assignments(user) if a.is_class_teacher})


def comment_subjects_for(user, class_name: str) -> list[str]:
    """Subjects the user writes PTC comments for in a class: the class's
    mark-bearing subjects, limited to those the user teaches there (PTC admins
    get all of them). Remark-only subjects never take a PTC comment."""
    marks = get_marks_subjects_for_class(class_name)
    if is_ptc_admin(user):
        return marks
    taught = get_teacher_subjects_by_class(user).get(class_name, set())
    return [s for s in marks if s in taught]


def comment_save_subjects_for(user, class_name: str) -> list[str]:
    """Subjects whose PTC comment the user may save in a class: a Super Admin
    any mark-bearing subject, anyone else only the subjects they teach there.
    Head of School and HODs read every comment but do not write them."""
    marks = get_marks_subjects_for_class(class_name)
    if getattr(user, "role", None) == "super_admin":
        return marks
    taught = get_teacher_subjects_by_class(user).get(class_name, set())
    return [s for s in marks if s in taught]


def can_rate_attributes(user, class_name: str) -> bool:
    """Student attributes are rated by the class teacher (or a Super Admin)."""
    return getattr(user, "role", None) == "super_admin" or _is_class_teacher(user, class_name)


def active_students_in_class(class_name: str):
    from students.models import Student

    return Student.objects.filter(
        is_archived=False, status="active", class_name=class_name,
    ).order_by("last_name", "first_name")


# ---------------------------------------------------------------------------
# PTC windows and terms
# ---------------------------------------------------------------------------

def get_current_ptc_window(academic_year=None):
    """The PTC window entry pages work on: the one whose entry window is open,
    else the next one coming up, else the most recent one. Prefers windows of
    the current academic year."""
    return _memo(("ptc_window", getattr(academic_year, "pk", None)), lambda: _find_current_ptc_window(academic_year))


def year_ptc_windows(academic_year) -> list:
    """The PTC windows of a year, by term slot (read once per request)."""
    from ptc.models import PTCWindow

    if academic_year is None:
        return []
    return _memo(
        ("year_windows", academic_year.pk),
        lambda: list(PTCWindow.objects.filter(academic_year=academic_year).select_related("academic_year").order_by("term_slot")),
    )


def _pick_current(windows, today):
    open_now = sorted(
        (w for w in windows
         if w.comment_entry_window_opens_at and w.comment_entry_window_closes_at
         and w.comment_entry_window_opens_at <= today <= w.comment_entry_window_closes_at),
        key=lambda w: w.ptc_date,
    )
    if open_now:
        return open_now[0]
    upcoming = sorted((w for w in windows if w.ptc_date >= today), key=lambda w: w.ptc_date)
    if upcoming:
        return upcoming[0]
    return max(windows, key=lambda w: w.ptc_date) if windows else None


def _find_current_ptc_window(academic_year):
    from ptc.models import PTCWindow

    today = timezone.now().date()
    windows = year_ptc_windows(academic_year)
    if not windows:
        windows = list(PTCWindow.objects.select_related("academic_year"))
    return _pick_current(windows, today)


def term_number_of(term) -> int | None:
    from academics.models import term_number_from_name

    return term.term_number or term_number_from_name(term.name)


def get_year_terms(academic_year) -> list:
    """The terms of a year in order (at most three)."""
    if not academic_year:
        return []
    terms = list(_memo(("year_terms", academic_year.pk), lambda: list(academic_year.terms.all())))
    terms.sort(key=lambda t: (term_number_of(t) or 9, t.start_date or timezone.now().date(), t.pk))
    return terms[:3]


def window_for_term(windows, term):
    n = term_number_of(term)
    for w in windows:
        if w.term_number == n:
            return w
    return None


def term_for_window(window):
    if not window:
        return None
    for t in get_year_terms(window.academic_year):
        if term_number_of(t) == window.term_number:
            return t
    return None


# ---------------------------------------------------------------------------
# Completion (used on Comments, Attributes and Students pages)
# ---------------------------------------------------------------------------

def comment_completion(students, ptc_window, subjects_by_class: dict) -> dict:
    """{student_id: True when every required subject has a non-empty comment}.
    ``subjects_by_class`` maps class name to the required subject names."""
    from ptc.models import PTCSubjectComment

    have: dict[int, set] = {}
    if ptc_window:
        for sid, subj in (
            PTCSubjectComment.objects.filter(ptc_window=ptc_window, student__in=students)
            .exclude(comment_text="").values_list("student_id", "subject_name")
        ):
            have.setdefault(sid, set()).add(subj)
    return {
        s.id: bool(ptc_window) and set(subjects_by_class.get(s.class_name, [])) <= have.get(s.id, set())
        for s in students
    }


def attribute_completion(students, ptc_window) -> dict:
    """{student_id: True when all 12 attributes are rated}."""
    from django.db.models import Count
    from ptc.models import LEARNER_ATTRIBUTES, LearnerAttributeRatingEntry

    counts = {}
    if ptc_window:
        counts = dict(
            LearnerAttributeRatingEntry.objects.filter(ptc_window=ptc_window, student__in=students)
            .exclude(rating="").values("student_id").annotate(n=Count("id")).values_list("student_id", "n")
        )
    return {s.id: counts.get(s.id, 0) >= len(LEARNER_ATTRIBUTES) for s in students}


# ---------------------------------------------------------------------------
# PTC report (all three terms side by side)
# ---------------------------------------------------------------------------

RATING_BANDS = [
    (90, "Outstanding", "out"),
    (80, "High", "high"),
    (70, "Good", "good"),
    (60, "Aspiring", "asp"),
    (50, "Basic", "basic"),
    (0, "Needs Improvement", "needs"),
]
REMARK_CSS = {label: css for _, label, css in RATING_BANDS}

ASSESSMENTS = [
    # (exam type code, label, default weight)
    ("quiz", "Quiz", 20),
    ("mid_term", "Mid-Term", 30),
    ("end_of_term", "End-Term", 50),
]


def rating_for(average):
    """(label, css key) for a running average out of 100, or (None, None)."""
    if average is None:
        return None, None
    for floor, label, css in RATING_BANDS:
        if average >= floor:
            return label, css
    return RATING_BANDS[-1][1], RATING_BANDS[-1][2]


def _assessment_weights() -> dict:
    from academics.models import ExamTypeConfiguration

    configured = {
        c.code: float(c.weight_percentage)
        for c in ExamTypeConfiguration.objects.filter(code__in=[a[0] for a in ASSESSMENTS])
    }
    return {code: configured.get(code, default) for code, _, default in ASSESSMENTS}


def _term_started(term, today) -> bool:
    return bool(term.start_date and term.start_date <= today)


def _attendance_for_term(student, term, today) -> dict | None:
    """Days present (incl. late), absent (incl. excused), late and the rate.
    Unmarked (unconfirmed) days are left out. None for a term not started."""
    from django.db.models import Count
    from attendance.models import AttendanceEntry, AttendanceStatus

    if not _term_started(term, today):
        return None
    end = min(term.end_date or today, today)
    counts = {
        row["status"]: row["n"]
        for row in AttendanceEntry.objects.filter(student=student, date__range=[term.start_date, end])
        .values("status").annotate(n=Count("id"))
    }
    late = counts.get(AttendanceStatus.LATE, 0)
    present = counts.get(AttendanceStatus.PRESENT, 0) + late
    absent = counts.get(AttendanceStatus.ABSENT, 0) + counts.get(AttendanceStatus.EXCUSED, 0)
    marked = present + absent
    return {
        "present": present,
        "absent": absent,
        "late": late,
        "rate": round(present * 100 / marked, 1) if marked else None,
    }


def _incidents_for_year(student, academic_year, terms) -> list[dict]:
    """Welfare notes and discipline incidents of the year, oldest first.
    Discipline rows carry the level only; safeguarding notes and dismissed
    incidents are left out."""
    from discipline.models import DisciplineIncident, IncidentStatus
    from welfare.models import WelfareNoteType, WelfareObservation

    start = academic_year.start_date or min((t.start_date for t in terms if t.start_date), default=None)
    end = academic_year.end_date or max((t.end_date for t in terms if t.end_date), default=None)

    def term_label(d):
        for t in terms:
            if t.start_date and t.end_date and t.start_date <= d <= t.end_date:
                return t.name
        return ""

    def in_year(d):
        return d and (not start or d >= start) and (not end or d <= end)

    rows = []
    for inc in DisciplineIncident.objects.filter(student=student).exclude(status=IncidentStatus.DISMISSED):
        d = inc.incident_date or inc.created_at.date()
        if not in_year(d):
            continue
        rows.append({
            "date": d, "term": term_label(d), "type": "Discipline",
            "level": inc.get_severity_display().split(" - ")[0], "summary": "",
        })
    for note in WelfareObservation.objects.filter(student=student).exclude(note_type=WelfareNoteType.SAFEGUARDING):
        d = note.observation_date
        if not in_year(d):
            continue
        kind = note.get_note_type_display() if note.note_type else note.get_concern_type_display()
        rows.append({
            "date": d, "term": term_label(d), "type": f"Welfare ({kind.lower()})",
            "level": ", ".join(note.tags or []) or note.get_concern_type_display(),
            "summary": note.observation_text,
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def build_ptc_report(student, academic_year, current_window=None) -> dict:
    """Everything the PTC report shows, for all three terms of the year.

    Marks: only HOD-approved scores. Each mark is converted to its term weight
    (Quiz 20, Mid-Term 30, End-Term 50); the running average is the converted
    marks rescaled over the assessments marked so far. Unmarked assessments
    are left out (never counted as zero) and, once the term has started, are
    flagged as missing. The overall average is over subjects that have marks.
    """
    from academics.models import ExamScore, ScoreStatus, SubjectTermRemark
    from ptc.models import LEARNER_ATTRIBUTES, LearnerAttributeRatingEntry, PTCSubjectComment, PTCWindow

    today = timezone.now().date()
    terms = get_year_terms(academic_year)
    marks_subjects = get_marks_subjects_for_class(student.class_name)
    remark_subjects = get_remark_subjects_for_class(student.class_name)
    weights = _assessment_weights()
    pass_mark = float(get_pass_mark())
    windows = year_ptc_windows(academic_year)

    scores: dict = {}
    for sc in ExamScore.objects.filter(
        student=student, term__in=terms, status=ScoreStatus.APPROVED, subject_name__in=marks_subjects,
    ):
        max_score = float(sc.max_score or 100) or 100.0
        scores[(sc.term_id, sc.subject_name, sc.exam_type)] = float(sc.score) / max_score * weights.get(sc.exam_type, 0)

    remarks = {
        (r.term_id, r.subject_name): r.remark
        for r in SubjectTermRemark.objects.filter(student=student, term__in=terms, subject_name__in=remark_subjects)
    }
    window_ids = [w.pk for w in windows]
    comments = {
        (c.ptc_window_id, c.subject_name): c.comment_text
        for c in PTCSubjectComment.objects.filter(student=student, ptc_window_id__in=window_ids)
    }
    ratings = {
        (r.ptc_window_id, r.attribute_number): r.rating
        for r in LearnerAttributeRatingEntry.objects.filter(student=student, ptc_window_id__in=window_ids)
    }

    term_cols = []
    for term in terms:
        started = _term_started(term, today)
        has_marks = any(k[0] == term.pk for k in scores)
        term_cols.append({
            "term": term,
            "label": term.name,
            "started": started or has_marks,
            "window": window_for_term(windows, term),
        })

    # Marks subjects
    marks_rows = []
    overall = {t["term"].pk: [] for t in term_cols}
    for subj in marks_subjects:
        cells = []
        for col in term_cols:
            term = col["term"]
            parts = []
            total = weight_marked = 0.0
            for code, _, _ in ASSESSMENTS:
                v = scores.get((term.pk, subj, code))
                if v is not None:
                    total += v
                    weight_marked += weights.get(code, 0)
                parts.append({"value": v, "missing": v is None and col["started"]})
            avg = round(total / weight_marked * 100, 1) if weight_marked else None
            label, css = rating_for(avg)
            if avg is not None:
                overall[term.pk].append(avg)
            cells.append({"parts": parts, "avg": avg, "rating": label, "css": css, "started": col["started"]})
        marks_rows.append({"subject": subj, "cells": cells})

    summary = []
    for col in term_cols:
        avgs = overall[col["term"].pk]
        avg = round(sum(avgs) / len(avgs), 1) if avgs else None
        label, css = rating_for(avg)
        summary.append({
            "avg": avg, "rating": label, "css": css, "started": col["started"],
            "meets": None if avg is None else ("Yes" if avg >= pass_mark else "No"),
        })

    remark_rows = [
        {"subject": subj, "cells": [
            {"remark": remarks.get((c["term"].pk, subj)), "css": REMARK_CSS.get(remarks.get((c["term"].pk, subj)))}
            for c in term_cols
        ]}
        for subj in remark_subjects
    ]
    comment_rows = [
        {"subject": subj, "cells": [
            (comments.get((c["window"].pk, subj)) or "") if c["window"] else "" for c in term_cols
        ]}
        for subj in marks_subjects
    ]
    attribute_rows = [
        {"number": n, "label": label, "cells": [
            (ratings.get((c["window"].pk, n)) or "") if c["window"] else "" for c in term_cols
        ]}
        for n, label in LEARNER_ATTRIBUTES
    ]
    attendance = [_attendance_for_term(student, c["term"], today) for c in term_cols]

    # "Before the PTC" check, for the term of the current PTC window
    check = None
    check_term = term_for_window(current_window) if current_window else None
    if current_window and current_window.academic_year_id == getattr(academic_year, "pk", None):
        check_col = next((c for c in term_cols if check_term and c["term"].pk == check_term.pk), None)
        missing_comments = [
            s for s in marks_subjects if not (comments.get((current_window.pk, s)) or "").strip()
        ]
        missing_remarks = [
            s for s in remark_subjects if not (check_term and remarks.get((check_term.pk, s)))
        ]
        end_term_outstanding = [
            s for s in marks_subjects if not (check_term and scores.get((check_term.pk, s, "end_of_term")) is not None)
        ]
        check = {
            "term_label": check_term.name if check_term else current_window.get_term_slot_display(),
            "marks_total": len(marks_subjects),
            "remark_total": len(remark_subjects),
            "comments_done": len(marks_subjects) - len(missing_comments),
            "remarks_done": len(remark_subjects) - len(missing_remarks),
            "missing_comments": missing_comments,
            "missing_remarks": missing_remarks,
            "end_term_outstanding": end_term_outstanding,
            "all_clear": not (missing_comments or missing_remarks or end_term_outstanding),
            "term_started": bool(check_col and check_col["started"]),
        }

    return {
        "terms": term_cols,
        "weights": weights,
        "assessments": [{"code": c, "label": l, "weight": weights[c]} for c, l, _ in ASSESSMENTS],
        "marks_rows": marks_rows,
        "summary": summary,
        "remark_rows": remark_rows,
        "comment_rows": comment_rows,
        "attribute_rows": attribute_rows,
        "attendance": attendance,
        "incidents": _incidents_for_year(student, academic_year, terms) if academic_year else [],
        "check": check,
        "pass_mark": pass_mark,
        "marks_subjects": marks_subjects,
        "remark_subjects": remark_subjects,
    }
