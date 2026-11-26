"""
Super Admin can still enter and correct marks on the score-entry grid after
the exam window has closed, after submission to the HOD, and on a locked
term (e.g. a student who sat her exams late, after school closed). Teachers
stay locked out; approved marks still change only through the amend flow.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import (
    AcademicYear, Department, ExamScore, ExamTypeConfiguration, GradeClass,
    ScoreStatus, Subject, Term,
)
from academics.tests import assign_role_group
from students.models import Student, StudentStatus
from users.models import User, UserRole


class SuperAdminScoreOverrideTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        # Every exam window (and the grading deadline) is in the past.
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Term 1",
            start_date=today - timedelta(days=90), end_date=today - timedelta(days=1),
            quiz_end_date=today - timedelta(days=60),
            grading_deadline=today - timedelta(days=1),
        )
        for code, name, w in (("quiz", "Quiz", 20), ("mid_term", "Mid Term", 30), ("end_of_term", "End Term", 50)):
            ExamTypeConfiguration.objects.update_or_create(
                code=code, defaults={"name": name, "weight_percentage": Decimal(w),
                                     "max_score": Decimal("100"), "is_active": True},
            )
        cls.gc = GradeClass.objects.create(name="Grade 8", department=Department.LOWER_SECONDARY)
        for name, code in (("English", "ENG"), ("Mathematics", "MAT")):
            s = Subject.objects.create(name=name, code=code, department=Department.LOWER_SECONDARY,
                                       departments=[Department.LOWER_SECONDARY])
            s.classes.add(cls.gc)
        cls.student = Student.objects.create(
            admission_no="G8-001", first_name="Ella", last_name="Late",
            class_name="Grade 8", academic_year=cls.year, status=StudentStatus.ACTIVE,
        )

        def make(username, role):
            u = User.objects.create_user(username=username, email=f"{username}@example.test",
                                         password="x", role=role)
            assign_role_group(u)
            return u

        cls.admin = make("superadmin", UserRole.SUPER_ADMIN)
        cls.hod = make("lshod", UserRole.LOWER_SECONDARY_HOD)

    def _save(self, user, scores):
        self.client.force_login(user)
        return self.client.post(
            reverse("academics:api_lower_secondary_scores", args=[self.student.id]),
            data=json.dumps({"term": self.term.id, "scores": scores}),
            content_type="application/json",
        )

    def _page(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("academics:lower_secondary_assessment"),
                               {"term": self.term.id, "class_name": "Grade 8"}).content.decode()

    def _cell(self, page, subject):
        marker = f'data-subject="{subject}" data-type="end_of_term"'
        i = page.index(marker)
        return page[i:page.index(">", i)]

    def test_super_admin_enters_marks_after_window_closed(self):
        resp = self._save(self.admin, {"English": {"end_of_term": "72"}, "Mathematics": {"end_of_term": "54"}})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["updated"], 2)
        self.assertEqual(ExamScore.objects.get(student=self.student, subject_name="English",
                                               exam_type="end_of_term").score, Decimal("72"))
        self.assertNotIn("readonly", self._cell(self._page(self.admin), "English"))

    def test_hod_still_blocked_after_window_closed(self):
        resp = self._save(self.hod, {"English": {"end_of_term": "72"}})
        self.assertEqual(resp.json().get("updated", 0), 0)
        self.assertFalse(ExamScore.objects.filter(student=self.student).exists())
        self.assertIn("readonly", self._cell(self._page(self.hod), "English"))

    def test_super_admin_works_on_locked_term_and_submitted_score(self):
        ExamScore.objects.create(student=self.student, term=self.term, subject_name="English",
                                 exam_type="end_of_term", score=Decimal("60"), max_score=Decimal("100"),
                                 status=ScoreStatus.SUBMITTED, is_locked=True, entered_by=self.hod)
        Term.objects.filter(pk=self.term.pk).update(is_locked=True)

        self.assertEqual(self._save(self.hod, {"English": {"end_of_term": "72"}}).status_code, 403)

        resp = self._save(self.admin, {"English": {"end_of_term": "72"}})
        self.assertEqual(resp.status_code, 200, resp.content)
        sc = ExamScore.objects.get(student=self.student, subject_name="English", exam_type="end_of_term")
        self.assertEqual(sc.score, Decimal("72"))
        self.assertEqual(sc.previous_score, Decimal("60"))

        page = self._page(self.admin)
        self.assertNotIn("readonly", self._cell(page, "English"))
        self.assertIn('id="ag-save"', page)

    def test_approved_marks_still_need_the_amend_flow(self):
        ExamScore.objects.create(student=self.student, term=self.term, subject_name="English",
                                 exam_type="end_of_term", score=Decimal("60"), max_score=Decimal("100"),
                                 status=ScoreStatus.APPROVED, is_locked=True, entered_by=self.hod)
        resp = self._save(self.admin, {"English": {"end_of_term": "72"}})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(ExamScore.objects.get(student=self.student, subject_name="English",
                                               exam_type="end_of_term").score, Decimal("60"))
