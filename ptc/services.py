# PTC Service Layer — Business logic for Learner Progress Report module.
#
# Adjustment notes vs FRD assumptions:
# - Core subjects are read dynamically from the GradeClass subject M2M, not hardcoded.
# - "Class teacher" identity is determined via TeacherClassAssignment.is_class_teacher,
#   since the User model has a single TEACHER role (no distinct class/subject/specialist roles).
# - Weighted average reuses the existing ExamTypeConfiguration weights from academics app
#   to avoid duplicating 20/30/50 logic.
# - OI-PTC-01: attendance summary gated behind PTC_SHOW_ATTENDANCE_SUMMARY setting (default off).
# - OI-PTC-03: term-to-term comparison baseline used by default; isolated in its own function.

from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from academics.grading_utils import compute_grade_with_gaps

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PASS_THRESHOLD = 50  # FR-PTC-029: ≥ 50% = Yes for "Meets Academic Expectations"
IMPROVING_THRESHOLD = 3  # percentage points
NEEDS_ATTENTION_THRESHOLD = -3

# Feature flag: OI-PTC-01 — attendance summary on PTC screen.
# Defaults to off; set PTC_SHOW_ATTENDANCE_SUMMARY=True in settings to enable.
SHOW_ATTENDANCE = getattr(settings, "PTC_SHOW_ATTENDANCE_SUMMARY", False)

# Hardcoded ECD fallback names — used when Department.ECD lookup is unavailable.
ECD_FALLBACK_NAMES = {"Pre-Kindergarten", "Kindergarten", "Pre-School", "ABC Class"}


def _get_ecd_class_names() -> set:
    """Return ECD class names from the registry, falling back to hardcoded names."""
    try:
        from academics.models import GradeClass, Department
        db_names = set(
            GradeClass.objects.filter(department=Department.ECD).values_list("name", flat=True)
        )
        if db_names:
            return db_names
    except Exception:
        pass
    return set(ECD_FALLBACK_NAMES)


# ---------------------------------------------------------------------------
# Academic Performance Grid
# ---------------------------------------------------------------------------

def get_core_subjects_for_student(student) -> list[str]:
    """
    Read the core subjects dynamically from the grade's subject configuration.
    This avoids hardcoding the five subjects and allows future curriculum changes.
    """
    from academics.models import GradeClass, Subject
    from ptc.models import EnrichmentSubject

    grade_class = GradeClass.objects.filter(name=student.class_name).first()
    if not grade_class:
        return []

    # Core subjects are those assigned to this grade class that are NOT enrichment
    enrichment_subject_ids = EnrichmentSubject.objects.filter(
        is_active=True
    ).values_list("subject_id", flat=True)
    subject_names = list(
        grade_class.subjects.filter(is_active=True)
        .exclude(id__in=enrichment_subject_ids)
        .values_list("name", flat=True)
        .order_by("name")
    )
    return subject_names


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
            exam_data["weighted_avg"] = grade_result["average"]
            exam_data["gap_case"] = grade_result["gap"]["case"]
            exam_data["gap_label"] = grade_result["gap"]["label"]
            exam_data["redistributed_weights"] = grade_result["redistributed_weights"]
            exam_data["makeup_required"] = grade_result["makeup_required"]

            if grade_result["average"] is not None:
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
# Weighted Average — reuses existing ExamTypeConfiguration weights
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
    Returns None if no scores available (case 7 — all missing).
    Accepts optional weights dict to avoid repeated DB queries.
    """
    result = compute_grade_with_gaps(quiz, mid_term, end_term, weights=weights)
    return result["average"]


# ---------------------------------------------------------------------------
# Meets Academic Expectations
# ---------------------------------------------------------------------------

def calculate_meets_expectations(overall_average: Optional[float]) -> str:
    """
    Returns "Yes" if overall_average >= 50%, "No" otherwise.
    If average is None (no data), returns "N/A".
    """
    if overall_average is None:
        return "N/A"
    return "Yes" if overall_average >= PASS_THRESHOLD else "No"


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
    OI-PTC-03: Default comparison baseline — term-to-term.
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
    from attendance.models import AttendanceEntry, AttendanceStatus
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

    present = entries.filter(status=AttendanceStatus.PRESENT).count()
    absent = entries.filter(status=AttendanceStatus.ABSENT).count()
    late = entries.filter(status=AttendanceStatus.LATE).count()
    total = present + absent + late

    rate = round(((present + late) / total) * 100, 1) if total > 0 else 0

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
    """Get set of class names assigned to this teacher via TeacherClassAssignment."""
    from hr.models import TeacherClassAssignment
    from academics.utils import get_current_term

    current_term = get_current_term()
    if not current_term:
        return set()

    return set(
        TeacherClassAssignment.objects.filter(
            teacher__user=user,
            term=current_term,
        ).values_list("grade_class__name", flat=True)
    )


def _get_teacher_subjects(user) -> set:
    """Get set of subject names this teacher is assigned to teach."""
    from hr.models import TeacherClassAssignment
    from academics.utils import get_current_term

    current_term = get_current_term()
    if not current_term:
        return set()

    assignments = TeacherClassAssignment.objects.filter(
        teacher__user=user,
        term=current_term,
    )
    subjects = set()
    for a in assignments:
        if a.subjects_taught:
            subjects.update(a.subjects_taught)
    return subjects


def _is_class_teacher(user, class_name: str = None) -> bool:
    """Check if user is a class teacher (optionally for a specific class)."""
    from hr.models import TeacherClassAssignment
    from academics.utils import get_current_term

    current_term = get_current_term()
    if not current_term:
        return False

    qs = TeacherClassAssignment.objects.filter(
        teacher__user=user,
        term=current_term,
        is_class_teacher=True,
    )
    if class_name:
        qs = qs.filter(grade_class__name=class_name)
    return qs.exists()


def _get_class_teacher_for_class(class_name: str):
    """Get the class teacher User for a given class name."""
    from hr.models import TeacherClassAssignment
    from academics.utils import get_current_term

    current_term = get_current_term()
    if not current_term:
        return None

    assignment = TeacherClassAssignment.objects.filter(
        term=current_term,
        grade_class__name=class_name,
        is_class_teacher=True,
    ).select_related("teacher__user").first()

    return assignment.teacher.user if assignment else None


def _is_ecd_student(student) -> bool:
    """Check if student is in ECD (Grades < 1 or ECD department classes)."""
    from academics.models import GradeClass, Department
    grade_class = GradeClass.objects.filter(name=student.class_name).first()
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

    # Exclude ECD students — enforced at query level
    ecd_class_names = set(
        GradeClass.objects.filter(department=Department.ECD).values_list("name", flat=True)
    )
    ecd_names = _get_ecd_class_names()

    students = Student.objects.filter(
        is_archived=False,
        status="active",
    ).exclude(class_name__in=ecd_class_names | ecd_names)

    # For class teachers: filter to own class only (FR-PTC-037)
    if _is_class_teacher(user):
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


def can_enter_enrichment_grade(user, student, enrichment_subject_id: int) -> bool:
    """Specialist teachers restricted to their assigned enrichment subject only."""
    from users.models import UserRole
    from ptc.models import EnrichmentSubject

    if user.role == UserRole.SUPER_ADMIN:
        return True

    subject = EnrichmentSubject.objects.filter(
        id=enrichment_subject_id,
        assigned_teacher=user,
        is_active=True,
    ).first()

    if not subject:
        return False

    # Check if the student's class matches the assigned class
    if subject.assigned_class:
        return student.class_name == subject.assigned_class.name

    return False


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


def get_specialist_teacher_students(user, enrichment_subject_id: int):
    """Get all students a specialist teacher teaches for their enrichment subject."""
    from students.models import Student
    from ptc.models import EnrichmentSubject
    from academics.models import Department, GradeClass

    subject = EnrichmentSubject.objects.filter(
        id=enrichment_subject_id,
        assigned_teacher=user,
        is_active=True,
    ).first()

    if not subject:
        return Student.objects.none()

    ecd_names = _get_ecd_class_names()

    if subject.assigned_class:
        return Student.objects.filter(
            is_archived=False,
            status="active",
            class_name=subject.assigned_class.name,
        ).exclude(class_name__in=ecd_names).order_by("last_name", "first_name")

    # If assigned to all classes for this subject
    return Student.objects.filter(
        is_archived=False,
        status="active",
    ).exclude(class_name__in=ecd_names).order_by("class_name", "last_name", "first_name")


# ---------------------------------------------------------------------------
# Compliance Summary (FR-PTC-011, FR-PTC-016, FR-PTC-024)
# ---------------------------------------------------------------------------

def get_compliance_summary(academic_year, ptc_window, data_type: str) -> list:
    """
    One reusable function parameterized by data_type.
    Returns per-teacher compliance data: [{teacher, total_students, completed, incomplete}]

    data_type: "comments" | "enrichment_grades" | "attributes"
    """
    from students.models import Student
    from academics.models import GradeClass, Department
    from hr.models import TeacherClassAssignment
    from ptc.models import (
        PTCSubjectComment,
        EnrichmentGrade,
        LearnerAttributeRatingEntry,
        EnrichmentSubject,
    )

    results = []

    ecd_names = _get_ecd_class_names()

    # Get the term matching the PTC window's term_slot (shared across all branches)
    target_term = ptc_window.academic_year.terms.filter(
        name__icontains=ptc_window.get_term_slot_display()
    ).first() or ptc_window.academic_year.terms.first()

    # Get all students (non-ECD)
    all_students = Student.objects.filter(
        is_archived=False,
        status="active",
    ).exclude(class_name__in=ecd_names)

    if data_type == "comments":
        # Group by subject teacher
        teacher_assignments = TeacherClassAssignment.objects.filter(
            term=target_term,
        ).select_related("teacher__user", "grade_class")

        for ta in teacher_assignments:
            teacher_user = ta.teacher.user
            teacher_subjects = ta.subjects_taught or []
            if not teacher_subjects:
                continue

            students = all_students.filter(class_name=ta.grade_class.name)
            total = students.count()
            if total == 0:
                continue

            completed = 0
            for student in students:
                student_comments = PTCSubjectComment.objects.filter(
                    student=student,
                    ptc_window=ptc_window,
                    subject_name__in=teacher_subjects,
                )
                if student_comments.count() >= len(teacher_subjects):
                    completed += 1

            results.append({
                "teacher": teacher_user,
                "total_students": total,
                "completed": completed,
                "incomplete": total - completed,
            })

    elif data_type == "enrichment_grades":
        enrichment_subjects = EnrichmentSubject.objects.filter(is_active=True)
        for es in enrichment_subjects:
            if not es.assigned_teacher:
                continue

            teacher_user = es.assigned_teacher
            students = all_students
            if es.assigned_class:
                students = students.filter(class_name=es.assigned_class.name)

            total = students.count()
            if total == 0:
                continue

            completed = EnrichmentGrade.objects.filter(
                enrichment_subject=es,
                term=target_term,
                student__in=students,
            ).exclude(letter_grade="").count()

            results.append({
                "teacher": teacher_user,
                "subject": es.subject.name,
                "total_students": total,
                "completed": completed,
                "incomplete": total - completed,
            })

    elif data_type == "attributes":
        # Class teachers responsible for attribute ratings
        teacher_assignments = TeacherClassAssignment.objects.filter(
            term=target_term,
            is_class_teacher=True,
        ).select_related("teacher__user", "grade_class")

        for ta in teacher_assignments:
            teacher_user = ta.teacher.user
            students = all_students.filter(class_name=ta.grade_class.name)
            total = students.count()
            if total == 0:
                continue

            completed = 0
            for student in students:
                rated_count = LearnerAttributeRatingEntry.objects.filter(
                    student=student,
                    ptc_window=ptc_window,
                ).exclude(rating="").count()
                if rated_count >= 12:  # All 12 attributes rated
                    completed += 1

            results.append({
                "teacher": teacher_user,
                "total_students": total,
                "completed": completed,
                "incomplete": total - completed,
            })

    return results


# ---------------------------------------------------------------------------
# Letter grade for enrichment (FR-PTC-007)
# ---------------------------------------------------------------------------

ENRICHMENT_GRADE_BANDS = {
    "A*": (90, 100),
    "A": (80, 89),
    "B": (70, 79),
    "C": (60, 69),
    "D": (50, 59),
    "E": (0, 49),
}


def get_enrichment_grade_description(grade: str) -> str:
    """Return the descriptive label for an enrichment letter grade."""
    descriptions = {
        "A*": "Outstanding",
        "A": "High",
        "B": "Good",
        "C": "Aspiring",
        "D": "Basic",
        "E": "Needs Improvement",
    }
    return descriptions.get(grade, "")
