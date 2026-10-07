"""
Mark-bearing vs remark-only subjects. Regression for: every subject was
treated as mark-bearing, so remark-only subjects (Music, Swimming, French...)
showed Quiz / Mid Term / End Term boxes that were never filled.
"""
import json
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import (
    AcademicYear, AssessmentMode, Department, ExamScore, ExamTypeConfiguration, GradeClass,
    ReportCard, ReportCardStatus, ScoreStatus, Subject, SubjectTermRemark, Term,
)
from academics.score_progress import student_progress
from academics.tests import assign_role_group
from hr.models import StaffProfile, TeacherClassAssignment
from students.models import Student, StudentStatus
from users.models import User, UserRole


class RemarkOnlySubjectTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Term 1",
            start_date=today - timedelta(days=30), end_date=today + timedelta(days=60),
        )
        for code, name, w in (("quiz", "Quiz", 20), ("mid_term", "Mid Term", 30), ("end_of_term", "End Term", 50)):
            ExamTypeConfiguration.objects.update_or_create(
                code=code, defaults={"name": name, "weight_percentage": Decimal(w),
                                     "max_score": Decimal("100"), "is_active": True},
            )
        cls.gc = GradeClass.objects.create(name="Grade 3", department=Department.PRIMARY)
        cls.science = Subject.objects.create(
            name="Zoology", code="ZOO", department=Department.PRIMARY, departments=[Department.PRIMARY],
        )
        cls.music = Subject.objects.create(
            name="Choir", code="CHO", department=Department.PRIMARY, departments=[Department.PRIMARY],
            assessment_mode=AssessmentMode.REMARK,
        )
        cls.science.classes.add(cls.gc)
        cls.music.classes.add(cls.gc)
        cls.student = Student.objects.create(
            admission_no="G3-001", first_name="Amani", last_name="Mushi",
            class_name="Grade 3", academic_year=cls.year, status=StudentStatus.ACTIVE,
        )

        def make(username, role):
            u = User.objects.create_user(
                username=username, email=f"{username}@example.test", password="x", role=role,
            )
            assign_role_group(u)
            return u

        cls.teacher = make("teacher", UserRole.TEACHER)
        staff = StaffProfile.objects.create(
            user=cls.teacher, employment_start_date=date(2024, 1, 1),
            full_name="Music Teacher", department="PRIMARY", job_title="Teacher",
        )
        TeacherClassAssignment.objects.create(
            teacher=staff, term=cls.term, grade_class=cls.gc, subjects_taught=["Choir", "Zoology"],
        )
        cls.other_teacher = make("other", UserRole.TEACHER)
        other_staff = StaffProfile.objects.create(
            user=cls.other_teacher, employment_start_date=date(2024, 1, 1),
            full_name="Other Teacher", department="PRIMARY", job_title="Teacher",
        )
        TeacherClassAssignment.objects.create(
            teacher=other_staff, term=cls.term, grade_class=cls.gc, subjects_taught=["Zoology"],
        )
        cls.admin = make("superadmin", UserRole.SUPER_ADMIN)

    def _save(self, user, scores=None, remarks=None):
        self.client.force_login(user)
        return self.client.post(
            reverse("academics:api_primary_scores", args=[self.student.id]),
            data=json.dumps({"term": self.term.id, "scores": scores or {}, "remarks": remarks or {}}),
            content_type="application/json",
        )

    # ── Subjects module ─────────────────────────────────────────────
    def test_subject_setting_is_editable_in_subjects_module(self):
        self.client.force_login(self.admin)
        data = self.client.get(reverse("academics:subject_edit_json", args=[self.science.pk])).json()
        self.assertEqual(data["assessment_mode"], "marks")
        resp = self.client.post(
            reverse("academics:subject_edit", args=[self.science.pk]),
            {"name": "Zoology", "code": "ZOO", "color": "#023AA5", "departments": ["PRIMARY"],
             "classes": [self.gc.pk], "is_active": "on", "assessment_mode": "remark"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.science.refresh_from_db()
        self.assertTrue(self.science.is_remark_only)

        page = self.client.get(reverse("academics:subjects")).content.decode()
        self.assertIn('name="assessment_mode" value="remark"', page)
        self.assertIn("Remark only", page)

    # ── Grade entry ─────────────────────────────────────────────────
    def test_entry_screen_asks_only_for_what_applies(self):
        self.client.force_login(self.teacher)
        page = self.client.get(reverse("academics:primary_assessment"), {"class_name": "Grade 3"}).content.decode()
        self.assertIn('class="hf-select remark-input-val" data-subject="Choir"', page)
        self.assertNotIn('data-subject="Choir" data-type=', page)
        self.assertIn('data-subject="Zoology" data-type="quiz"', page)

    def test_remark_saved_and_marks_ignored_for_remark_subject(self):
        resp = self._save(self.teacher, scores={"Choir": {"quiz": "80"}, "Zoology": {"quiz": "70"}},
                          remarks={"Choir": "Good"})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(SubjectTermRemark.objects.get(student=self.student, subject_name="Choir").remark, "Good")
        self.assertFalse(ExamScore.objects.filter(subject_name="Choir").exists())
        self.assertTrue(ExamScore.objects.filter(subject_name="Zoology").exists())

        data = self.client.get(
            reverse("academics:api_primary_scores", args=[self.student.id]), {"term": self.term.id},
        ).json()
        self.assertEqual(data["remarks"], {"Choir": "Good"})
        self.assertFalse(data["remarks_locked"])

    def test_invalid_or_unassigned_remark_rejected(self):
        self.assertEqual(self._save(self.teacher, remarks={"Choir": "Brilliant"}).status_code, 400)
        self.assertEqual(self._save(self.teacher, remarks={"Zoology": "Good"}).status_code, 400)
        self.assertEqual(self._save(self.other_teacher, remarks={"Choir": "Good"}).status_code, 400)
        self.assertFalse(SubjectTermRemark.objects.exists())

    def test_remarks_lock_once_report_goes_for_sign_off(self):
        self._save(self.teacher, remarks={"Choir": "Good"})
        ReportCard.objects.create(
            student=self.student, term=self.term, generated_by=self.admin,
            status=ReportCardStatus.PENDING_SIGN_OFF,
        )
        resp = self._save(self.teacher, remarks={"Choir": "High"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(SubjectTermRemark.objects.get().remark, "Good")

    # ── Progress / completeness ────────────────────────────────────
    def test_remark_subject_complete_when_remark_entered(self):
        ExamScore.objects.create(
            student=self.student, term=self.term, subject_name="Zoology", exam_type="quiz",
            score=Decimal("70"), entered_by=self.teacher, status=ScoreStatus.APPROVED, is_locked=True,
        )
        p = student_progress(self.student, self.term)
        self.assertEqual(p["subjects"]["Choir"]["status"], "not_started")
        self.assertFalse(p["is_complete"])
        self.assertEqual(p["mark_subjects"], 1)

        self._save(self.teacher, remarks={"Choir": "Good"})
        p = student_progress(self.student, self.term)
        self.assertTrue(p["is_complete"])
        self.assertEqual(p["grand_average"], 70.0)

    # ── Reports: two separate tables ────────────────────────────────
    def test_reports_render_marks_and_remarks_as_separate_tables(self):
        ExamScore.objects.create(
            student=self.student, term=self.term, subject_name="Zoology", exam_type="quiz",
            score=Decimal("70"), entered_by=self.teacher, status=ScoreStatus.APPROVED, is_locked=True,
        )
        # A stray approved mark on a remark-only subject is ignored everywhere.
        ExamScore.objects.create(
            student=self.student, term=self.term, subject_name="Choir", exam_type="quiz",
            score=Decimal("10"), entered_by=self.teacher, status=ScoreStatus.APPROVED, is_locked=True,
        )
        SubjectTermRemark.objects.create(
            student=self.student, term=self.term, subject_name="Choir", remark="Outstanding",
            entered_by=self.teacher,
        )
        rc = ReportCard.objects.create(student=self.student, term=self.term, generated_by=self.admin)

        from academics.views.reports import _build_report_card_context
        ctx = _build_report_card_context(rc)
        self.assertEqual(list(ctx["subjects"]), ["Zoology"])
        self.assertEqual(ctx["remark_rows"], [("Choir", "Outstanding")])
        self.assertEqual([r["subject"] for r in ctx["exam"]["rows"]], ["Zoology"])
        self.assertIsNone(ctx["exam"]["average"])  # only the quiz is in: awaiting end of term

        self.client.force_login(self.admin)
        page = self.client.get(
            reverse("academics:learner_report", args=[self.student.pk]), {"term": self.term.pk},
        ).content.decode()
        self.assertIn("Subjects assessed by remark", page)
        self.assertIn("Outstanding", page)
