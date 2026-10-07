"""
What parents see of their child's marks.

Regression for: the parent Grades and child pages listed every score,
including drafts and scores not yet approved by the HOD, averaged raw marks
(remark-only subjects included) and printed an overall average from partial
marks. Parents now see only approved marks of mark-bearing subjects, and an
average only once the assessments are complete.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import (
    AcademicYear, AssessmentMode, Department, ExamScore, ExamTypeConfiguration, GradeClass,
    ScoreStatus, Subject, Term,
)
from academics.tests import assign_role_group
from students.models import ParentGuardian, Student, StudentGuardian, StudentStatus
from users.models import User, UserRole

TYPES = (("quiz", "Quiz", 20, 1), ("mid_term", "Mid Term", 30, 2), ("end_of_term", "End Term", 50, 3))


class ParentGradesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term = Term.objects.create(academic_year=year, name="Term 1",
                                       start_date=today - timedelta(days=30), end_date=today + timedelta(days=60))
        ExamTypeConfiguration.objects.exclude(code__in=[t[0] for t in TYPES]).update(is_active=False)
        for code, name, weight, order in TYPES:
            ExamTypeConfiguration.objects.update_or_create(
                code=code, defaults={"name": name, "weight_percentage": Decimal(weight), "display_order": order,
                                     "max_score": Decimal("100"), "is_active": True},
            )
        gc = GradeClass.objects.create(name="Grade 5", department=Department.PRIMARY)
        Subject.objects.create(name="Alg", code="ALG", department=Department.PRIMARY,
                               departments=[Department.PRIMARY]).classes.add(gc)
        Subject.objects.create(name="Choir", code="CHO", department=Department.PRIMARY,
                               departments=[Department.PRIMARY],
                               assessment_mode=AssessmentMode.REMARK).classes.add(gc)
        cls.child = Student.objects.create(admission_no="G5-001", first_name="Sherissa", last_name="K",
                                           class_name="Grade 5", academic_year=year, status=StudentStatus.ACTIVE)
        cls.teacher = User.objects.create_user(username="t", email="t@x.test", password="x", role=UserRole.TEACHER)
        cls.parent = User.objects.create_user(username="p", email="p@x.test", password="x", role=UserRole.PARENT)
        assign_role_group(cls.parent)
        guardian = ParentGuardian.objects.create(
            full_name="Parent K", phone="+255700000001", user=cls.parent,
            pdpa_consent_given=True, pdpa_consent_method="in_person",
            pdpa_consent_version="v1", pdpa_consented_at=timezone.now(),
        )
        StudentGuardian.objects.create(student=cls.child, guardian=guardian, relationship="mother", is_primary=True)

    def _score(self, subject, code, value, status=ScoreStatus.APPROVED):
        ExamScore.objects.create(student=self.child, term=self.term, subject_name=subject, exam_type=code,
                                 score=Decimal(value), entered_by=self.teacher, status=status,
                                 is_locked=status != ScoreStatus.DRAFT)

    def _pages(self):
        self.client.force_login(self.parent)
        return [
            self.client.get(reverse("parent_portal:grades"), {"student": self.child.pk}),
            self.client.get(reverse("parent_portal:child_overview", args=[self.child.pk])),
            self.client.get(reverse("parent_portal:child_detail", args=[self.child.pk])),
        ]

    def test_only_approved_mark_bearing_scores_and_no_partial_average(self):
        self._score("Alg", "quiz", 83)
        self._score("Alg", "mid_term", 12, status=ScoreStatus.DRAFT)       # not approved
        self._score("Choir", "quiz", 19)                                   # remark-only subject
        for resp in self._pages():
            self.assertEqual(resp.status_code, 200)
            self.assertIsNone(resp.context["overall_average"])
            page = resp.content.decode()
            self.assertIn("Awaiting end of term", page)
            self.assertNotIn("Choir", page)
            self.assertNotIn(">12<", page)
            self.assertNotIn("51.0%", page)

    def test_complete_term_shows_weighted_average_and_grade(self):
        for code, value in (("quiz", 90), ("mid_term", 90), ("end_of_term", 90)):
            self._score("Alg", code, value)
        for resp in self._pages():
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.context["overall_average"], 90.0)
            self.assertNotIn("Awaiting end of term", resp.content.decode())
