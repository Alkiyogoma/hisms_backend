"""
Changing a grade after HOD approval. Regression for: once an HOD approved a
grade nobody could correct a mis-keyed mark or a re-marked script.

Only the Head of School and Super Admin may change an approved grade, a reason
is mandatory, and each change keeps the previous mark, new mark, who, when and
why, shown as a note on the grade.
"""
import json
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import (
    AcademicYear, Department, ExamScore, ExamScoreAmendment, ExamTypeConfiguration,
    GradeClass, ScoreStatus, Subject, Term,
)
from academics.tests import assign_role_group
from hr.models import StaffProfile, TeacherClassAssignment
from students.models import Student, StudentStatus
from users.models import User, UserRole


class ApprovedGradeAmendmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Term 1",
            start_date=today - timedelta(days=30), end_date=today + timedelta(days=60),
        )
        ExamTypeConfiguration.objects.update_or_create(
            code="quiz",
            defaults={"name": "Quiz", "weight_percentage": Decimal("20"),
                      "max_score": Decimal("100"), "is_active": True},
        )
        cls.gc = GradeClass.objects.create(name="Grade 3", department=Department.PRIMARY)
        subj = Subject.objects.create(
            name="Science", code="SCI", department=Department.PRIMARY, departments=[Department.PRIMARY],
        )
        subj.classes.add(cls.gc)
        cls.student = Student.objects.create(
            admission_no="G3-001", first_name="Amani", last_name="Mushi",
            class_name="Grade 3", academic_year=cls.year, status=StudentStatus.ACTIVE,
        )

        def make(username, role):
            u = User.objects.create_user(
                username=username, email=f"{username}@example.test", password="x", role=role,
                first_name=username.title(), last_name="User",
            )
            assign_role_group(u)
            return u

        cls.teacher = make("teacher", UserRole.TEACHER)
        staff = StaffProfile.objects.create(
            user=cls.teacher, employment_start_date=date(2024, 1, 1),
            full_name="Science Teacher", department="PRIMARY", job_title="Teacher",
        )
        TeacherClassAssignment.objects.create(
            teacher=staff, term=cls.term, grade_class=cls.gc, subjects_taught=["Science"],
        )
        cls.hod = make("hod", UserRole.PRIMARY_HOD)
        cls.hos = make("hos", UserRole.HEAD_OF_SCHOOL)
        cls.admin = make("superadmin", UserRole.SUPER_ADMIN)

    def setUp(self):
        # 1. Teacher enters a grade  2. HOD approves it
        self.score = ExamScore.objects.create(
            student=self.student, term=self.term, subject_name="Science", exam_type="quiz",
            score=Decimal("15"), entered_by=self.teacher, status=ScoreStatus.APPROVED,
            is_locked=True, approved_by=self.hod, approved_at=timezone.now(),
        )

    def _change(self, user, score, reason):
        self.client.force_login(user)
        return self.client.post(
            reverse("academics:exam_score_correction", args=[self.score.pk]),
            data=json.dumps({"score": score, "reason": reason}),
            content_type="application/json",
        )

    def test_head_of_school_can_change_approved_grade_with_reason(self):
        resp = self._change(self.hos, "19", "Mark was mis-keyed")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.score.refresh_from_db()
        self.assertEqual(self.score.score, Decimal("19"))
        self.assertEqual(self.score.status, ScoreStatus.APPROVED)
        a = ExamScoreAmendment.objects.get(score=self.score)
        self.assertEqual((a.previous_score, a.new_score), (Decimal("15"), Decimal("19")))
        self.assertEqual(a.changed_by, self.hos)
        self.assertEqual(a.reason, "Mark was mis-keyed")
        self.assertIsNotNone(a.created_at)

    def test_super_admin_can_change_and_history_is_kept(self):
        self.assertEqual(self._change(self.admin, "19", "Mis-keyed").status_code, 200)
        self.assertEqual(self._change(self.admin, "22", "Script re-marked").status_code, 200)
        rows = list(self.score.amendments.values_list("previous_score", "new_score", "reason"))
        self.assertEqual(rows, [
            (Decimal("15"), Decimal("19"), "Mis-keyed"),
            (Decimal("19"), Decimal("22"), "Script re-marked"),
        ])

    def test_reason_is_mandatory(self):
        resp = self._change(self.hos, "19", "   ")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("reason", resp.json()["error"].lower())
        self.score.refresh_from_db()
        self.assertEqual(self.score.score, Decimal("15"))
        self.assertFalse(ExamScoreAmendment.objects.exists())

    def test_hod_and_teacher_cannot_change_approved_grade(self):
        for user in (self.hod, self.teacher):
            resp = self._change(user, "19", "Trying")
            self.assertIn(resp.status_code, (302, 403), user.role)
        self.score.refresh_from_db()
        self.assertEqual(self.score.score, Decimal("15"))
        self.assertFalse(ExamScoreAmendment.objects.exists())

    def test_out_of_range_mark_rejected(self):
        resp = self._change(self.hos, "150", "Re-marked")
        self.assertEqual(resp.status_code, 400)
        self.score.refresh_from_db()
        self.assertEqual(self.score.score, Decimal("15"))

    def test_only_approved_grades_go_through_this_route(self):
        self.score.status = ScoreStatus.SUBMITTED
        self.score.save()
        self.assertEqual(self._change(self.hos, "19", "Re-marked").status_code, 400)

    def test_change_shown_as_note_on_grade_record(self):
        self._change(self.hos, "19", "Script re-marked")
        self.client.force_login(self.hos)
        data = self.client.get(
            reverse("academics:api_primary_scores", args=[self.student.id]), {"term": self.term.id},
        ).json()
        self.assertTrue(data["can_amend"])
        cell = data["scores"]["Science"]["quiz"]
        self.assertEqual(cell["score"], 19.0)
        self.assertEqual(cell["id"], self.score.pk)
        note = cell["amendments"][0]
        self.assertEqual((note["previous_score"], note["new_score"]), (15.0, 19.0))
        self.assertEqual(note["reason"], "Script re-marked")
        self.assertEqual(note["changed_by"], "Hos User")
        self.assertTrue(note["changed_at"])

        # The teacher sees the note too, but no change control.
        self.client.force_login(self.teacher)
        data = self.client.get(
            reverse("academics:api_primary_scores", args=[self.student.id]), {"term": self.term.id},
        ).json()
        self.assertFalse(data["can_amend"])
        self.assertEqual(len(data["scores"]["Science"]["quiz"]["amendments"]), 1)
