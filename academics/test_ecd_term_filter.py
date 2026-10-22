"""
The ECD evaluation page's filter bar reaches earlier years and terms: the class
list for that term comes from the enrolment history (learners have usually
moved up since), reports are shown as recorded, and nothing can be changed.
"""
import json
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import (
    AcademicYear, Department, ECDEvaluation, GradeClass, ReportCard, ReportCardStatus, Term,
)
from academics.tests import assign_role_group
from students.models import EnrollmentHistory, Student, StudentStatus
from users.models import User, UserRole


class ECDEarlierTermTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.hos = User.objects.create_user(username="hos", email="hos@example.test", password="x",
                                           role=UserRole.HEAD_OF_SCHOOL)
        assign_role_group(cls.hos)
        cls.old_year = AcademicYear.objects.create(name="2025/2026")
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.old_term = Term.objects.create(
            academic_year=cls.old_year, name="Term 3",
            start_date=today - timedelta(days=200), end_date=today - timedelta(days=120),
        )
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Term 1",
            start_date=today - timedelta(days=30), end_date=today + timedelta(days=60),
        )
        GradeClass.objects.create(name="KG", department=Department.ECD)
        GradeClass.objects.create(name="Grade 1", department=Department.PRIMARY)
        # Was in KG last year, has moved up to Grade 1 since.
        cls.moved_up = Student.objects.create(
            admission_no="E-001", first_name="Amani", last_name="Mushi",
            class_name="Grade 1", academic_year=cls.year, status=StudentStatus.ACTIVE,
        )
        EnrollmentHistory.objects.create(student=cls.moved_up, academic_year=cls.old_year, class_name="KG")
        cls.old_report = ReportCard.objects.create(
            student=cls.moved_up, term=cls.old_term, is_ecd_report=True, ecd_template_type="kindergarten",
            status=ReportCardStatus.PUBLISHED, teacher_comments="A joyful learner.", generated_by=cls.hos,
        )
        ECDEvaluation.objects.create(report_card=cls.old_report, domain="kn1", rating="E")
        # In KG now.
        cls.current = Student.objects.create(
            admission_no="E-002", first_name="Baraka", last_name="Kimaro",
            class_name="KG", academic_year=cls.year, status=StudentStatus.ACTIVE,
        )

    def setUp(self):
        self.client.force_login(self.hos)

    def _get(self, name, *args, **params):
        return self.client.get(reverse(f"academics:{name}", args=args), params)

    def test_earlier_year_lists_the_class_as_it_was(self):
        old = self._get("api_ecd_students", class_name="KG", term=self.old_term.id).json()
        self.assertEqual([s["id"] for s in old["students"]], [self.moved_up.id])
        self.assertTrue(old["read_only"])
        now = self._get("api_ecd_students", class_name="KG", term=self.term.id).json()
        self.assertEqual([s["id"] for s in now["students"]], [self.current.id])
        self.assertFalse(now["read_only"])

    def test_earlier_report_is_shown_as_recorded(self):
        d = self._get("api_ecd_evaluation", self.moved_up.id, class_name="KG", term=self.old_term.id).json()
        self.assertEqual(d["report_card_id"], self.old_report.id)
        self.assertEqual(d["evaluations"], {"kn1": "E"})
        self.assertEqual(d["teacher_comments"], "A joyful learner.")
        self.assertEqual(d["ecd_template_type"], "kindergarten")
        self.assertTrue(d["read_only"] and d["is_locked"])

    def test_browsing_an_earlier_term_never_creates_a_report(self):
        d = self._get("api_ecd_evaluation", self.current.id, class_name="KG", term=self.old_term.id)
        self.assertEqual(d.status_code, 200)
        self.assertTrue(d.json()["no_report"])
        self.assertFalse(ReportCard.objects.filter(student=self.current, term=self.old_term).exists())

    def test_earlier_terms_cannot_be_changed(self):
        url = reverse("academics:api_ecd_evaluation", args=[self.moved_up.id])
        resp = self.client.post(f"{url}?class_name=KG&term={self.old_term.id}",
                                json.dumps({"evaluations": {"kn1": "N"}}), content_type="application/json")
        self.assertEqual(resp.status_code, 403)
        submit = reverse("academics:api_ecd_submit", args=[self.moved_up.id])
        self.assertEqual(self.client.post(f"{submit}?class_name=KG&term={self.old_term.id}").status_code, 403)
        self.assertEqual(ECDEvaluation.objects.get(report_card=self.old_report).rating, "E")

    def test_page_opens_the_earlier_term_read_only(self):
        page = self._get("ecd_assessment", year=self.old_year.id, term=self.old_term.id, class_name="KG")
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.context["ecd_read_only"])
        self.assertEqual(page.context["ecd_class_name"], "KG")
        self.assertContains(page, "Read only")
        self.assertNotContains(page, 'class="class-tab')
