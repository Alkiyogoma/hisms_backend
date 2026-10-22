"""
The report preview offers the whole class: "Print all" opens every report in
the learner's class for that term on one page (one learner per printed page),
and "Approve & Sign Off All" signs off the class's unpublished reports.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import AcademicYear, Department, GradeClass, ReportCard, ReportCardStatus, Term
from academics.tests import assign_role_group
from students.models import Student, StudentStatus
from users.models import User, UserRole


class ClassReportPrintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Term 1",
            start_date=today - timedelta(days=30), end_date=today + timedelta(days=60),
        )
        GradeClass.objects.create(name="Grade 5", department=Department.PRIMARY)
        GradeClass.objects.create(name="Grade 6", department=Department.PRIMARY)
        cls.hos = User.objects.create_user(username="hos", email="hos@example.test", password="x",
                                           role=UserRole.HEAD_OF_SCHOOL)
        assign_role_group(cls.hos)

        def report(adm, first, last, cls_name, status=ReportCardStatus.DRAFT):
            s = Student.objects.create(admission_no=adm, first_name=first, last_name=last, class_name=cls_name,
                                       academic_year=cls.year, status=StudentStatus.ACTIVE)
            return ReportCard.objects.create(student=s, term=cls.term, generated_by=cls.hos, status=status)

        cls.zawadi = report("G5-1", "Zawadi", "Mushi", "Grade 5")
        cls.baraka = report("G5-2", "Baraka", "Kimaro", "Grade 5")
        cls.published = report("G5-3", "Neema", "Swai", "Grade 5", ReportCardStatus.PUBLISHED)
        cls.other = report("G6-1", "Juma", "Ally", "Grade 6")

    def setUp(self):
        import tempfile
        from django.test import override_settings
        self._media = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
        self._media.enable()
        self.addCleanup(self._media.disable)
        self.client.force_login(self.hos)

    def test_preview_offers_print_all_and_sign_off_all_for_the_class(self):
        page = self.client.get(reverse("academics:report_preview", args=[self.zawadi.pk])).content.decode()
        self.assertIn(reverse("academics:report_preview_class", args=[self.zawadi.pk]), page)
        self.assertIn("Print all (3)", page)
        self.assertIn("Approve & Sign Off All (2)", page)
        self.assertIn('name="class_name" value="Grade 5"', page)
        self.assertIn(f'name="term_id" value="{self.term.pk}"', page)

    def test_class_page_has_every_learner_in_the_class_in_order(self):
        resp = self.client.get(reverse("academics:report_preview_class", args=[self.zawadi.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["class_reports"]), 3)
        page = resp.content.decode()
        self.assertEqual(page.count('<div class="report-wrapper">'), 3)
        self.assertLess(page.index("Baraka Kimaro"), page.index("Zawadi Mushi"))
        self.assertLess(page.index("Zawadi Mushi"), page.index("Neema Swai"))
        self.assertNotIn("Juma", page)
        self.assertIn("Print all", page)

    def test_class_page_follows_the_preview_access_rules(self):
        teacher = User.objects.create_user(username="t", email="t@example.test", password="x", role=UserRole.TEACHER)
        assign_role_group(teacher)
        self.client.force_login(teacher)
        resp = self.client.get(reverse("academics:report_preview_class", args=[self.zawadi.pk]))
        self.assertEqual(resp.status_code, 403)
