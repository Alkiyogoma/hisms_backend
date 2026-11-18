"""Dashboard grade letters follow the school grading scale (A+ >= 90, A >= 80,
B >= 70, C >= 60, D >= 50, E below), the same one report cards use.

The HOS "Academic performance by dept" and "Class averages" cards (and the
Primary HOD performance pulse) used an inline table that topped out at B, so
90.9% and 82.2% were both shown as "B"."""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings as django_settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.grading_utils import get_grade_from_score
from academics.models import (
    AcademicYear, Department, ExamScore, ExamTypeConfiguration, GradeClass, ScoreStatus, Term,
)
from students.models import Student, StudentStatus
from users.models import User, UserRole

if not getattr(django_settings, "AUDIT_LOG_DB_CONSTRAINT", True):  # see academics/tests.py
    _orig = TestCase._fixture_teardown

    def _patched(self):
        try:
            _orig(self)
        except Exception:
            pass

    TestCase._fixture_teardown = _patched


class GradeScaleTests(TestCase):
    def test_dashboard_percentages_map_to_primary_scale(self):
        cases = {90.9: "A+", 90: "A+", 88.3: "A", 82.2: "A", 80: "A", 79.9: "B",
                 72.9: "B", 71.4: "B", 65: "C", 55: "D", 49.9: "E"}
        self.assertEqual({p: get_grade_from_score(p) for p in cases}, cases)


class DashboardGradeLetterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term = Term.objects.create(academic_year=year, name="Term 1",
                                       start_date=today - timedelta(days=30), end_date=today + timedelta(days=60))
        ExamTypeConfiguration.objects.update_or_create(
            code="end_of_term", defaults={"name": "End of Term", "weight_percentage": Decimal(100),
                                          "max_score": Decimal(100), "is_active": True})
        GradeClass.objects.create(name="Grade 1", department=Department.PRIMARY, sort_order=1)
        GradeClass.objects.create(name="Grade 2", department=Department.PRIMARY, sort_order=2)
        GradeClass.objects.create(name="Grade 8", department=Department.LOWER_SECONDARY, sort_order=8)
        cls.hos = User.objects.create_user(username="hos", email="hos@x.test", password="x",
                                           role=UserRole.HEAD_OF_SCHOOL)
        cls.hod = User.objects.create_user(username="phod", email="phod@x.test", password="x",
                                           role=UserRole.PRIMARY_HOD)
        for n, (cls_name, mark) in enumerate((("Grade 1", "90.9"), ("Grade 2", "83.5"), ("Grade 8", "72.9"))):
            st = Student.objects.create(admission_no=f"S{n}", first_name=f"K{n}", last_name="T",
                                        class_name=cls_name, academic_year=year, status=StudentStatus.ACTIVE)
            ExamScore.objects.create(student=st, term=cls.term, subject_name="Mathematics",
                                     exam_type="end_of_term", score=Decimal(mark), entered_by=cls.hos,
                                     status=ScoreStatus.APPROVED)

    def _get(self, user):
        self.client.force_login(user)
        resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_hos_class_and_dept_cards_use_school_scale(self):
        resp = self._get(self.hos)
        classes = {c["name"]: (c["avg"], c["grade"]) for c in resp.context["class_perf_list"]}
        self.assertEqual(classes, {"Grade 1": (90.9, "A+"), "Grade 2": (83.5, "A"), "Grade 8": (72.9, "B")})
        depts = {d["name"]: (d["avg"], d["grade"]) for d in resp.context["dept_perf_list"]}
        self.assertEqual(depts["Primary"], (87.2, "A"))
        self.assertEqual(depts["Lower Secondary"], (72.9, "B"))
        self.assertEqual(depts["ECD"], (0, "-"))
        self.assertEqual(resp.context["school_grade"], "A")  # (90.9 + 83.5 + 72.9) / 3 = 82.4
        self.assertContains(resp, "87.2%")
        self.assertContains(resp, "Grade A")

    def test_primary_hod_pulse_uses_school_scale(self):
        perf = self._get(self.hod).context["dept_performance"]
        self.assertEqual((perf["avg"], perf["grade"]), (87.2, "A"))
