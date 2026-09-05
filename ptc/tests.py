"""
PTC Module Tests — FR-PTC-001 through FR-PTC-031 coverage.

Tests models, services, views, and RBAC for the Learner Progress Report module.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from academics.models import (
    AcademicYear, Department, GradeClass, Subject, Term,
)
from academics.models import ExamScore, ExamTypeConfiguration, ScoreStatus
from hr.models import StaffProfile, TeacherClassAssignment
from ptc.models import (
    EnrichmentGrade,
    EnrichmentLetterGrade,
    EnrichmentSubject,
    LEARNER_ATTRIBUTES,
    LearnerAttributeRating,
    LearnerAttributeRatingEntry,
    PTCDateChangeLog,
    PTCGenerationLog,
    PTCProgressTrendOverride,
    PTCSubjectComment,
    PTCWindow,
    TermSlot,
)
from ptc.services import (
    calculate_meets_expectations,
    calculate_progress_trend,
    can_enter_comment,
    can_enter_enrichment_grade,
    can_open_ptc,
    get_core_subjects_for_student,
    get_enrichment_grade_description,
)
from students.models import Student, StudentStatus
from users.models import User, UserRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_user(username, role=UserRole.TEACHER):
    return User.objects.create_user(
        username=username,
        email=f"{username}@hodari.edu",
        password="TestPass123!",
        role=role,
    )


def _create_academic_year(name="2025-2026", is_current=True):
    return AcademicYear.objects.create(name=name, is_current=is_current)


def _create_term(academic_year, name="Term 1", start_date=None, end_date=None):
    return Term.objects.create(
        academic_year=academic_year,
        name=name,
        start_date=start_date or date(2025, 9, 1),
        end_date=end_date or date(2025, 12, 15),
    )


def _create_grade_class(name="Grade 1", department=Department.PRIMARY):
    return GradeClass.objects.create(name=name, department=department)


def _create_subject(name="Mathematics", department=Department.PRIMARY):
    s = Subject.objects.create(name=name, code=name[:4].upper(), department=department)
    return s


def _create_student(admission_no="HCS0001", first_name="Alice", last_name="Wanjiku",
                    class_name="Grade 1", status=StudentStatus.ACTIVE):
    return Student.objects.create(
        admission_no=admission_no,
        first_name=first_name,
        last_name=last_name,
        class_name=class_name,
        status=status,
    )


def _create_staff_profile(user, department=Department.PRIMARY):
    profile, _ = StaffProfile.objects.get_or_create(
        user=user,
        defaults=dict(
            full_name=user.get_full_name() or user.username,
            job_title="Teacher",
            department=department,
            employment_start_date=date(2024, 9, 1),
        ),
    )
    return profile


def _create_teacher_class_assignment(teacher_user, grade_class, term,
                                     is_class_teacher=False, subjects_taught=None):
    staff = _create_staff_profile(teacher_user, grade_class.department)
    return TeacherClassAssignment.objects.create(
        teacher=staff,
        term=term,
        grade_class=grade_class,
        is_class_teacher=is_class_teacher,
        subjects_taught=subjects_taught or [],
    )


def _create_ptc_window(academic_year, term_slot=TermSlot.TERM_1, ptc_date=None):
    if ptc_date is None:
        ptc_date = date.today() + timedelta(days=30)
    return PTCWindow.objects.create(
        academic_year=academic_year,
        term_slot=term_slot,
        ptc_date=ptc_date,
    )


# ---------------------------------------------------------------------------
# FR-PTC-001: PTC windows — only Term 1 and Term 3
# ---------------------------------------------------------------------------

class PTCWindowTests(TestCase):
    """FR-PTC-001: Only Term 1 and Term 3 PTC windows exist."""

    def test_term_slot_choices(self):
        self.assertEqual(TermSlot.TERM_1, "term_1")
        self.assertEqual(TermSlot.TERM_3, "term_3")

    def test_ptc_window_creation(self):
        ay = _create_academic_year()
        window = _create_ptc_window(ay)
        self.assertEqual(window.term_slot, TermSlot.TERM_1)
        self.assertIsNotNone(window.ptc_date)

    def test_notification_window_computed(self):
        ay = _create_academic_year()
        ptc_date = date(2025, 11, 15)
        window = _create_ptc_window(ay, ptc_date=ptc_date)
        self.assertEqual(window.notification_window_opens_at, ptc_date - timedelta(days=30))
        self.assertEqual(window.notification_window_closes_at, ptc_date - timedelta(days=4))

    def test_comment_entry_window_computed(self):
        ay = _create_academic_year()
        ptc_date = date(2025, 11, 15)
        window = _create_ptc_window(ay, ptc_date=ptc_date)
        self.assertEqual(window.comment_entry_window_opens_at, ptc_date - timedelta(days=14))
        self.assertEqual(window.comment_entry_window_closes_at, ptc_date)

    def test_unique_together(self):
        ay = _create_academic_year()
        _create_ptc_window(ay, TermSlot.TERM_1)
        with self.assertRaises(Exception):
            _create_ptc_window(ay, TermSlot.TERM_1)

    def test_notification_window_wider_than_comment_window(self):
        ay = _create_academic_year()
        window = PTCWindow(academic_year=ay, term_slot=TermSlot.TERM_1,
                           ptc_date=date(2025, 11, 15),
                           notification_window_days=10,
                           comment_entry_window_days=14)
        with self.assertRaises(Exception):
            window.clean()


# ---------------------------------------------------------------------------
# FR-PTC-002: Date changes on published windows require Super Admin
# ---------------------------------------------------------------------------

class PTCDateChangeTests(TestCase):
    """FR-PTC-002: Date changes on published windows require Super Admin."""

    def test_date_change_log_creation(self):
        ay = _create_academic_year()
        window = _create_ptc_window(ay)
        admin = _create_user("admin", UserRole.SUPER_ADMIN)
        log = PTCDateChangeLog.objects.create(
            ptc_window=window,
            changed_by=admin,
            old_ptc_date=date(2025, 11, 15),
            new_ptc_date=date(2025, 11, 22),
        )
        self.assertEqual(log.old_ptc_date, date(2025, 11, 15))
        self.assertEqual(log.new_ptc_date, date(2025, 11, 22))


# ---------------------------------------------------------------------------
# FR-PTC-006: Seven fixed enrichment subjects
# ---------------------------------------------------------------------------

class EnrichmentSubjectTests(TestCase):
    """FR-PTC-006: Enrichment subjects linked to academics.Subject with is_enrichment=True."""

    def test_enrichment_subject_count(self):
        count = Subject.objects.filter(is_enrichment=True).count()
        self.assertEqual(count, 7)

    def test_enrichment_subject_creation(self):
        user = _create_user("music_teacher")
        music_subject = Subject.objects.get_or_create(
            name="Music", defaults={"is_enrichment": True, "is_active": True}
        )[0]
        enrichment, created = EnrichmentSubject.objects.get_or_create(
            subject=music_subject,
            defaults={"assigned_teacher": user, "is_active": True},
        )
        self.assertEqual(enrichment.subject.name, "Music")
        self.assertTrue(enrichment.is_active)


# ---------------------------------------------------------------------------
# FR-PTC-007: Enrichment letter grades
# ---------------------------------------------------------------------------

class EnrichmentGradeTests(TestCase):
    """FR-PTC-007: Enrichment letter grade bands."""

    def test_letter_grade_choices(self):
        self.assertEqual(len(EnrichmentLetterGrade), 6)

    def test_enrichment_grade_description(self):
        self.assertEqual(get_enrichment_grade_description("A*"), "Outstanding")
        self.assertEqual(get_enrichment_grade_description("A"), "High")
        self.assertEqual(get_enrichment_grade_description("B"), "Good")
        self.assertEqual(get_enrichment_grade_description("C"), "Aspiring")
        self.assertEqual(get_enrichment_grade_description("D"), "Basic")
        self.assertEqual(get_enrichment_grade_description("E"), "Needs Improvement")
        self.assertEqual(get_enrichment_grade_description("X"), "")

    def test_enrichment_grade_creation(self):
        ay = _create_academic_year()
        term = _create_term(ay)
        student = _create_student()
        french = Subject.objects.get_or_create(
            name="French", defaults={"is_enrichment": True, "is_active": True}
        )[0]
        subject, _ = EnrichmentSubject.objects.get_or_create(subject=french, defaults={"is_active": True})
        teacher = _create_user("french_teacher")
        subject.assigned_teacher = teacher
        subject.save()

        grade = EnrichmentGrade.objects.create(
            student=student,
            enrichment_subject=subject,
            term=term,
            letter_grade="A",
            entered_by=teacher,
        )
        self.assertEqual(grade.letter_grade, "A")

    def test_enrichment_grade_unique_together(self):
        ay = _create_academic_year()
        term = _create_term(ay)
        student = _create_student()
        pe = Subject.objects.get_or_create(
            name="PE", defaults={"is_enrichment": True, "is_active": True}
        )[0]
        subject, _ = EnrichmentSubject.objects.get_or_create(subject=pe, defaults={"is_active": True})
        EnrichmentGrade.objects.create(
            student=student, enrichment_subject=subject, term=term, letter_grade="B",
        )
        with self.assertRaises(Exception):
            EnrichmentGrade.objects.create(
                student=student, enrichment_subject=subject, term=term, letter_grade="C",
            )


# ---------------------------------------------------------------------------
# FR-PTC-012, FR-PTC-017: Subject comments
# ---------------------------------------------------------------------------

class PTCSubjectCommentTests(TestCase):
    """FR-PTC-012: No hard minimum on comment length. FR-PTC-017: Term 1 and 3 separate rows."""

    def test_comment_creation(self):
        ay = _create_academic_year()
        window = _create_ptc_window(ay)
        student = _create_student()
        teacher = _create_user("eng_teacher")
        comment = PTCSubjectComment.objects.create(
            student=student,
            ptc_window=window,
            subject_name="English",
            comment_text="Good progress in reading comprehension.",
            entered_by=teacher,
        )
        self.assertEqual(comment.comment_text, "Good progress in reading comprehension.")

    def test_comment_unique_together(self):
        ay = _create_academic_year()
        window = _create_ptc_window(ay)
        student = _create_student()
        PTCSubjectComment.objects.create(
            student=student, ptc_window=window, subject_name="Math",
        )
        with self.assertRaises(Exception):
            PTCSubjectComment.objects.create(
                student=student, ptc_window=window, subject_name="Math",
            )

    def test_empty_comment_allowed(self):
        ay = _create_academic_year()
        window = _create_ptc_window(ay)
        student = _create_student()
        comment = PTCSubjectComment.objects.create(
            student=student, ptc_window=window, subject_name="Science",
            comment_text="",
        )
        self.assertEqual(comment.comment_text, "")

    def test_term_1_and_term_3_separate_rows(self):
        ay = _create_academic_year()
        w1 = _create_ptc_window(ay, TermSlot.TERM_1)
        w3 = _create_ptc_window(ay, TermSlot.TERM_3,
                                ptc_date=date.today() + timedelta(days=90))
        student = _create_student()
        PTCSubjectComment.objects.create(
            student=student, ptc_window=w1, subject_name="Math",
        )
        c3 = PTCSubjectComment.objects.create(
            student=student, ptc_window=w3, subject_name="Math",
        )
        self.assertEqual(PTCSubjectComment.objects.filter(student=student).count(), 2)


# ---------------------------------------------------------------------------
# FR-PTC-018, FR-PTC-019, FR-PTC-023: Learner attributes
# ---------------------------------------------------------------------------

class LearnerAttributeRatingTests(TestCase):
    """FR-PTC-018/019/023: 12 fixed attributes, E/G/S/N ratings."""

    def test_12_fixed_attributes(self):
        self.assertEqual(len(LEARNER_ATTRIBUTES), 12)

    def test_attribute_rating_choices(self):
        self.assertEqual(LearnerAttributeRating.E, "E")
        self.assertEqual(LearnerAttributeRating.G, "G")
        self.assertEqual(LearnerAttributeRating.S, "S")
        self.assertEqual(LearnerAttributeRating.N, "N")

    def test_attribute_rating_creation(self):
        ay = _create_academic_year()
        window = _create_ptc_window(ay)
        student = _create_student()
        teacher = _create_user("class_teacher")
        entry = LearnerAttributeRatingEntry.objects.create(
            student=student,
            ptc_window=window,
            attribute_number=1,
            rating="E",
            entered_by=teacher,
        )
        self.assertEqual(entry.rating, "E")

    def test_attribute_unique_together(self):
        ay = _create_academic_year()
        window = _create_ptc_window(ay)
        student = _create_student()
        LearnerAttributeRatingEntry.objects.create(
            student=student, ptc_window=window, attribute_number=1, rating="E",
        )
        with self.assertRaises(Exception):
            LearnerAttributeRatingEntry.objects.create(
                student=student, ptc_window=window, attribute_number=1, rating="G",
            )

    def test_all_12_attributes_can_be_rated(self):
        ay = _create_academic_year()
        window = _create_ptc_window(ay)
        student = _create_student()
        for i in range(1, 13):
            LearnerAttributeRatingEntry.objects.create(
                student=student, ptc_window=window,
                attribute_number=i, rating="G",
            )
        self.assertEqual(
            LearnerAttributeRatingEntry.objects.filter(
                student=student, ptc_window=window,
            ).count(),
            12,
        )


# ---------------------------------------------------------------------------
# FR-PTC-024: Enrichment grade unique per student/subject/term
# ---------------------------------------------------------------------------

class EnrichmentGradeUniqueTests(TestCase):
    """FR-PTC-024: Enrichment grade is one per student per subject per term."""

    def test_unique_constraint(self):
        ay = _create_academic_year()
        term = _create_term(ay)
        student = _create_student()
        ict = Subject.objects.get_or_create(
            name="ICT", defaults={"is_enrichment": True, "is_active": True, "department": Department.PRIMARY}
        )[0]
        subject, _ = EnrichmentSubject.objects.get_or_create(subject=ict, defaults={"is_active": True})
        EnrichmentGrade.objects.create(
            student=student, enrichment_subject=subject, term=term, letter_grade="A",
        )
        with self.assertRaises(Exception):
            EnrichmentGrade.objects.create(
                student=student, enrichment_subject=subject, term=term, letter_grade="B",
            )


# ---------------------------------------------------------------------------
# FR-PTC-029: Progress trend override
# ---------------------------------------------------------------------------

class PTCProgressTrendOverrideTests(TestCase):
    """FR-PTC-029: Class teacher can override auto-derived trend."""

    def test_trend_override_creation(self):
        ay = _create_academic_year()
        window = _create_ptc_window(ay)
        student = _create_student()
        teacher = _create_user("ct")
        override = PTCProgressTrendOverride.objects.create(
            student=student,
            ptc_window=window,
            overridden_trend="improving",
            entered_by=teacher,
        )
        self.assertEqual(override.overridden_trend, "improving")

    def test_trend_override_unique_together(self):
        ay = _create_academic_year()
        window = _create_ptc_window(ay)
        student = _create_student()
        teacher = _create_user("ct")
        PTCProgressTrendOverride.objects.create(
            student=student, ptc_window=window,
            overridden_trend="stable", entered_by=teacher,
        )
        with self.assertRaises(Exception):
            PTCProgressTrendOverride.objects.create(
                student=student, ptc_window=window,
                overridden_trend="improving", entered_by=teacher,
            )


# ---------------------------------------------------------------------------
# FR-PTC-030: Generation log
# ---------------------------------------------------------------------------

class PTCGenerationLogTests(TestCase):
    """FR-PTC-030: Every time a class teacher opens a PTC screen, a log is created."""

    def test_generation_log_creation(self):
        ay = _create_academic_year()
        window = _create_ptc_window(ay)
        student = _create_student()
        teacher = _create_user("ct")
        log = PTCGenerationLog.objects.create(
            student=student,
            class_teacher=teacher,
            ptc_window=window,
        )
        self.assertIsNotNone(log.opened_at)


# ---------------------------------------------------------------------------
# FR-PTC-031: PTC date change audit log
# ---------------------------------------------------------------------------

class PTCDateChangeLogTests(TestCase):
    """FR-PTC-031: Date changes on published windows are audited."""

    def test_date_change_log_fields(self):
        ay = _create_academic_year()
        window = _create_ptc_window(ay)
        admin = _create_user("sa", UserRole.SUPER_ADMIN)
        log = PTCDateChangeLog.objects.create(
            ptc_window=window,
            changed_by=admin,
            old_ptc_date=date(2025, 11, 15),
            new_ptc_date=date(2025, 11, 22),
        )
        self.assertEqual(log.ptc_window, window)
        self.assertEqual(log.changed_by, admin)


# ---------------------------------------------------------------------------
# Services: Meets Expectations
# ---------------------------------------------------------------------------

class MeetsExpectationsTests(TestCase):
    """FR-PTC-029: ≥ 50% = Yes for Meets Academic Expectations."""

    def test_above_threshold(self):
        self.assertEqual(calculate_meets_expectations(75.0), "Yes")

    def test_at_threshold(self):
        self.assertEqual(calculate_meets_expectations(50.0), "Yes")

    def test_below_threshold(self):
        self.assertEqual(calculate_meets_expectations(49.9), "No")

    def test_none_data(self):
        self.assertEqual(calculate_meets_expectations(None), "N/A")


# ---------------------------------------------------------------------------
# Services: Progress Trend
# ---------------------------------------------------------------------------

class ProgressTrendTests(TestCase):
    """FR-PTC-029: Compare current to previous term."""

    def test_insufficient_data(self):
        ay = _create_academic_year()
        student = _create_student()
        result = calculate_progress_trend(student, 75.0, ay, all_terms_averages=[75.0])
        self.assertEqual(result["trend"], "Insufficient data")
        self.assertTrue(result["auto_derived"])

    def test_improving(self):
        ay = _create_academic_year()
        student = _create_student()
        result = calculate_progress_trend(student, 80.0, ay, all_terms_averages=[70.0, 80.0])
        self.assertEqual(result["trend"], "Improving")

    def test_stable(self):
        ay = _create_academic_year()
        student = _create_student()
        result = calculate_progress_trend(student, 72.0, ay, all_terms_averages=[70.0, 72.0])
        self.assertEqual(result["trend"], "Stable")

    def test_needs_attention(self):
        ay = _create_academic_year()
        student = _create_student()
        result = calculate_progress_trend(student, 60.0, ay, all_terms_averages=[70.0, 60.0])
        self.assertEqual(result["trend"], "Needs Attention")


# ---------------------------------------------------------------------------
# Services: can_open_ptc
# ---------------------------------------------------------------------------

class CanOpenPTCTests(TestCase):
    """FR-PTC-037: Class teacher can only open PTC for students in their own class."""

    def test_super_admin_can_open(self):
        sa = _create_user("sa", UserRole.SUPER_ADMIN)
        student = _create_student()
        self.assertTrue(can_open_ptc(sa, student))

    def test_hos_can_open(self):
        hos = _create_user("hos", UserRole.HEAD_OF_SCHOOL)
        student = _create_student()
        self.assertTrue(can_open_ptc(hos, student))

    def test_primary_hod_can_open(self):
        hod = _create_user("hod", UserRole.PRIMARY_HOD)
        student = _create_student()
        self.assertTrue(can_open_ptc(hod, student))

    def test_class_teacher_own_class(self):
        ay = _create_academic_year()
        term = _create_term(ay)
        gc = _create_grade_class("Grade 1")
        teacher = _create_user("ct")
        _create_teacher_class_assignment(teacher, gc, term, is_class_teacher=True)
        student = _create_student(class_name="Grade 1")
        self.assertTrue(can_open_ptc(teacher, student))

    def test_class_teacher_other_class(self):
        ay = _create_academic_year()
        term = _create_term(ay)
        gc = _create_grade_class("Grade 1")
        teacher = _create_user("ct")
        _create_teacher_class_assignment(teacher, gc, term, is_class_teacher=True)
        student = _create_student(class_name="Grade 2")
        self.assertFalse(can_open_ptc(teacher, student))


# ---------------------------------------------------------------------------
# Services: can_enter_comment
# ---------------------------------------------------------------------------

class CanEnterCommentTests(TestCase):
    """FR-PTC-014: Subject teachers restricted to their subjects within comment window."""

    def test_super_admin_always(self):
        sa = _create_user("sa", UserRole.SUPER_ADMIN)
        ay = _create_academic_year()
        window = _create_ptc_window(ay)
        student = _create_student()
        self.assertTrue(can_enter_comment(sa, student, "Math", window))

    def test_outside_window(self):
        ay = _create_academic_year()
        ptc_date = date.today() + timedelta(days=60)
        window = _create_ptc_window(ay, ptc_date=ptc_date)
        teacher = _create_user("t")
        student = _create_student()
        # Comment window opens 14 days before ptc_date — 60 days out is outside
        self.assertFalse(can_enter_comment(teacher, student, "Math", window))

    def test_inside_window_wrong_subject(self):
        ay = _create_academic_year()
        ptc_date = date.today() + timedelta(days=3)
        window = _create_ptc_window(ay, ptc_date=ptc_date)
        term = _create_term(ay)
        teacher = _create_user("t")
        gc = _create_grade_class()
        _create_teacher_class_assignment(teacher, gc, term, subjects_taught=["Math"])
        student = _create_student()
        self.assertFalse(can_enter_comment(teacher, student, "English", window))


# ---------------------------------------------------------------------------
# Services: can_enter_enrichment_grade
# ---------------------------------------------------------------------------

class CanEnterEnrichmentGradeTests(TestCase):
    """Specialist teachers restricted to their assigned enrichment subject."""

    def _get_or_create_enrichment(self, name, teacher=None, gc=None):
        subj, _ = Subject.objects.get_or_create(
            name=name, defaults={"is_enrichment": True, "is_active": True}
        )
        es, _ = EnrichmentSubject.objects.get_or_create(
            subject=subj, defaults={"assigned_teacher": teacher, "assigned_class": gc, "is_active": True}
        )
        return es

    def test_super_admin_always(self):
        sa = _create_user("sa", UserRole.SUPER_ADMIN)
        student = _create_student()
        es = self._get_or_create_enrichment("Music")
        self.assertTrue(can_enter_enrichment_grade(sa, student, es.id))

    def test_unassigned_teacher(self):
        teacher = _create_user("t")
        student = _create_student()
        es = self._get_or_create_enrichment("Music")
        self.assertFalse(can_enter_enrichment_grade(teacher, student, es.id))

    def test_wrong_subject(self):
        teacher = _create_user("t")
        other = _create_user("other")
        gc = _create_grade_class()
        es = self._get_or_create_enrichment("Music", teacher=other, gc=gc)
        student = _create_student()
        self.assertFalse(can_enter_enrichment_grade(teacher, student, es.id))


# ---------------------------------------------------------------------------
# Services: get_core_subjects_for_student
# ---------------------------------------------------------------------------

class CoreSubjectTests(TestCase):
    """Core subjects read dynamically from GradeClass.subjects M2M."""

    def test_no_grade_class(self):
        student = _create_student(class_name="Nonexistent")
        self.assertEqual(get_core_subjects_for_student(student), [])

    def test_with_subjects(self):
        gc = _create_grade_class()
        s1 = _create_subject("Mathematics")
        s2 = _create_subject("English")
        gc.subjects.add(s1, s2)
        student = _create_student()
        subjects = get_core_subjects_for_student(student)
        self.assertIn("Mathematics", subjects)
        self.assertIn("English", subjects)

    def test_excludes_enrichment(self):
        gc = _create_grade_class()
        s1 = _create_subject("Mathematics")
        enrichment, _ = Subject.objects.get_or_create(
            name="Music", defaults={"is_enrichment": True, "is_active": True, "department": Department.PRIMARY}
        )
        EnrichmentSubject.objects.get_or_create(subject=enrichment, defaults={"is_active": True})
        gc.subjects.add(s1, enrichment)
        student = _create_student()
        subjects = get_core_subjects_for_student(student)
        self.assertIn("Mathematics", subjects)
        self.assertNotIn("Music", subjects)


# ---------------------------------------------------------------------------
# Model constraints
# ---------------------------------------------------------------------------

class ModelConstraintTests(TestCase):
    """Basic model constraint tests."""

    def test_learner_attribute_number_range(self):
        ay = _create_academic_year()
        window = _create_ptc_window(ay)
        student = _create_student()
        entry = LearnerAttributeRatingEntry(
            student=student, ptc_window=window,
            attribute_number=13, rating="E",
        )
        with self.assertRaises(Exception):
            entry.clean()

    def test_enrichment_grade_invalid_choice(self):
        ay = _create_academic_year()
        term = _create_term(ay)
        student = _create_student()
        pe = Subject.objects.get_or_create(
            name="PE", defaults={"is_enrichment": True, "is_active": True}
        )[0]
        subject, _ = EnrichmentSubject.objects.get_or_create(subject=pe, defaults={"is_active": True})
        grade = EnrichmentGrade(
            student=student, enrichment_subject=subject,
            term=term, letter_grade="Z",
        )
        with self.assertRaises(Exception):
            grade.clean()

    def test_ptc_window_term_slot_validation(self):
        ay = _create_academic_year()
        window = PTCWindow(academic_year=ay, term_slot="term_2", ptc_date=date(2025, 11, 15))
        with self.assertRaises(Exception):
            window.clean()
