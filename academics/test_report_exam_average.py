"""
Exam average, total and grade on the printed progress report.

Regression for: a learner with one approved mark (Mathematics, 83) was printed
"Exam average 51.00, D, Basic — 2 of 16 subjects · incomplete": the average
was taken from partial marks, remark-only subjects were counted, and a stale
stored average was preferred. The average, total and grade now come only from
approved, mark-bearing subjects, and nothing is printed until the term's
assessments are complete ("Awaiting end of term").
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import (
    AcademicYear, AssessmentMode, Department, ExamScore, ExamTypeConfiguration, GradeClass,
    ReportCard, ScoreStatus, Subject, SubjectTermRemark, Term,
)
from academics.score_progress import exam_summary
from academics.services import recalculate_report_card_average
from academics.tests import assign_role_group
from students.models import Student, StudentStatus
from users.models import User, UserRole

TYPES = (("quiz", "Quiz", 20, 1), ("mid_term", "Mid Term", 30, 2), ("end_of_term", "End Term", 50, 3))


class ReportExamAverageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Term 1",
            start_date=today - timedelta(days=30), end_date=today + timedelta(days=60),
        )
        ExamTypeConfiguration.objects.exclude(code__in=[t[0] for t in TYPES]).update(is_active=False)
        for code, name, weight, order in TYPES:
            ExamTypeConfiguration.objects.update_or_create(
                code=code, defaults={"name": name, "weight_percentage": Decimal(weight), "display_order": order,
                                     "max_score": Decimal("100"), "is_active": True},
            )
        cls.gc = GradeClass.objects.create(name="Grade 5", department=Department.PRIMARY)
        for i, name in enumerate(["Alg", "Geo"]):
            Subject.objects.create(name=name, code=f"M{i}", department=Department.PRIMARY,
                                   departments=[Department.PRIMARY]).classes.add(cls.gc)
        cls.choir = Subject.objects.create(
            name="Choir", code="CHO", department=Department.PRIMARY, departments=[Department.PRIMARY],
            assessment_mode=AssessmentMode.REMARK,
        )
        cls.choir.classes.add(cls.gc)
        cls.student = Student.objects.create(
            admission_no="G5-001", first_name="Sherissa", last_name="Ketorare",
            class_name="Grade 5", academic_year=cls.year, status=StudentStatus.ACTIVE,
        )
        cls.hos = User.objects.create_user(username="hos", email="hos@example.test", password="x",
                                           role=UserRole.HEAD_OF_SCHOOL)
        assign_role_group(cls.hos)

    def _approve(self, subject, code, score):
        ExamScore.objects.create(
            student=self.student, term=self.term, subject_name=subject, exam_type=code,
            score=Decimal(score), entered_by=self.hos, status=ScoreStatus.APPROVED, is_locked=True,
        )

    def _preview(self, rc):
        self.client.force_login(self.hos)
        return self.client.get(reverse("academics:report_preview", args=[rc.pk])).content.decode()

    def test_incomplete_term_prints_no_average_or_grade(self):
        self._approve("Alg", "quiz", 83)
        # A stray mark on a remark-only subject must not count either.
        self._approve("Choir", "quiz", 19)
        # Stale average saved by the old calculation.
        rc = ReportCard.objects.create(student=self.student, term=self.term, generated_by=self.hos,
                                       overall_average=Decimal("51.00"))

        s = exam_summary(self.student, self.term)
        self.assertFalse(s["complete"])
        self.assertIsNone(s["average"])
        self.assertEqual((s["assessed"], s["total_subjects"]), (0, 2))
        self.assertEqual([r["subject"] for r in s["rows"]], ["Alg"])
        self.assertEqual(s["rows"][0]["cells"], [16.6, None, None])  # 83/100 x 20
        self.assertIsNone(s["rows"][0]["grade"])

        page = self._preview(rc)
        self.assertIn("Awaiting end of term", page)
        self.assertIn("0 of 2 mark-bearing subjects assessed", page)
        self.assertNotIn("51.00", page)
        self.assertNotIn("Basic", page.split("GRADING SCALE")[0].split("ACADEMIC PROGRESS")[1])

        recalculate_report_card_average(rc)
        rc.refresh_from_db()
        self.assertIsNone(rc.overall_average)

    def test_complete_term_prints_average_total_and_grade(self):
        for code, score in (("quiz", 90), ("mid_term", 90), ("end_of_term", 90)):
            self._approve("Alg", code, score)      # 18 + 27 + 45 = 90 -> A*
        for code, score in (("quiz", 70), ("mid_term", 80), ("end_of_term", 80)):
            self._approve("Geo", code, score)      # 14 + 24 + 40 = 78 -> B
        SubjectTermRemark.objects.create(student=self.student, term=self.term, subject_name="Choir",
                                         remark="Good", entered_by=self.hos)
        rc = ReportCard.objects.create(student=self.student, term=self.term, generated_by=self.hos)

        s = exam_summary(self.student, self.term)
        self.assertTrue(s["complete"])
        self.assertEqual([(r["total"], r["grade"], r["label"]) for r in s["rows"]],
                         [(90.0, "A*", "Outstanding"), (78.0, "B", "Good")])
        self.assertEqual(s["column_averages"], [16.0, 25.5, 42.5])
        self.assertEqual((s["average"], s["grade"], s["label"]), (84.0, "A", "High"))

        page = self._preview(rc)
        self.assertNotIn("Awaiting end of term", page)
        self.assertIn("<td>84</td>", page)
        self.assertIn("Outstanding", page)

        recalculate_report_card_average(rc)
        rc.refresh_from_db()
        self.assertEqual(rc.overall_average, Decimal("84.00"))

    def test_command_clears_stale_averages(self):
        self._approve("Alg", "quiz", 83)
        rc = ReportCard.objects.create(student=self.student, term=self.term, generated_by=self.hos,
                                       overall_average=Decimal("51.00"))
        out = StringIO()
        call_command("recalculate_report_averages", "--dry-run", stdout=out)
        rc.refresh_from_db()
        self.assertEqual(rc.overall_average, Decimal("51.00"))
        self.assertIn("1 report(s) would change", out.getvalue())

        call_command("recalculate_report_averages", stdout=StringIO())
        rc.refresh_from_db()
        self.assertIsNone(rc.overall_average)


class ReportLayoutTests(TestCase):
    """Header once, admission number, photo, traits two per row, days absent,
    and the Student Progress Report waiting for the end of term."""

    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Term 1",
            start_date=today - timedelta(days=30), end_date=today + timedelta(days=60),
        )
        ExamTypeConfiguration.objects.exclude(code__in=[t[0] for t in TYPES]).update(is_active=False)
        for code, name, weight, order in TYPES:
            ExamTypeConfiguration.objects.update_or_create(
                code=code, defaults={"name": name, "weight_percentage": Decimal(weight), "display_order": order,
                                     "max_score": Decimal("100"), "is_active": True},
            )
        cls.gc = GradeClass.objects.create(name="Grade 5", department=Department.PRIMARY)
        Subject.objects.create(name="Alg", code="M0", department=Department.PRIMARY,
                               departments=[Department.PRIMARY]).classes.add(cls.gc)
        cls.student = Student.objects.create(
            admission_no="ADM-2026-065", first_name="Joan", last_name="Namunga",
            class_name="Grade 5", academic_year=cls.year, status=StudentStatus.ACTIVE,
            enrolment_date=today - timedelta(days=30),
        )
        cls.hos = User.objects.create_user(username="hos", email="hos@example.test", password="x",
                                           role=UserRole.HEAD_OF_SCHOOL)
        assign_role_group(cls.hos)
        cls.rc = ReportCard.objects.create(
            student=cls.student, term=cls.term, generated_by=cls.hos,
            general_traits={"wh_works_independently": "E", "st_courteous": "G"},
        )

    def setUp(self):
        import tempfile
        from django.test import override_settings
        self._media = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
        self._media.enable()
        self.addCleanup(self._media.disable)
        self.client.force_login(self.hos)

    def _preview(self):
        return self.client.get(reverse("academics:report_preview", args=[self.rc.pk])).content.decode()

    def test_header_once_admission_number_and_traits_two_per_row(self):
        page = self._preview()
        self.assertEqual(page.count('class="header"'), 1)
        self.assertIn('class="page2-head"', page)
        self.assertIn("Admission No: ADM-2026-065", page)
        self.assertIn('<th colspan="4">Work Habits</th>', page)
        self.assertIn("traits-full", page)
        self.assertIn("Works well independently</td><td class=\"g\">E", page)
        # No hardcoded reopen date or teacher name.
        self.assertIn("School Reopens on: To be announced", page)
        self.assertNotIn("RANGE MARWA", page)
        Term.objects.create(academic_year=self.year, name="Term 2",
                            start_date=self.term.end_date + timedelta(days=30),
                            end_date=self.term.end_date + timedelta(days=120))
        self.assertIn("School Reopens on: " + (self.term.end_date + timedelta(days=30)).strftime("%-d"),
                      self._preview())

    def test_photo_from_admission_document_is_embedded(self):
        from django.core.files.base import ContentFile
        from admissions.models import Applicant, ApplicantDocumentReceipt, ApplicantDocumentType
        applicant = Applicant.objects.create(
            child_full_name="Joan Namunga", parent_full_name="Parent", parent_phone="0700000000",
            grade_applying_for="Grade 5", enrolled_student=self.student,
        )
        doc = ApplicantDocumentReceipt(applicant=applicant, document_type=ApplicantDocumentType.STUDENT_PHOTO)
        doc.file.save("joan.png", ContentFile(b"\x89PNG\r\n\x1a\nfake"), save=True)
        page = self._preview()
        self.assertIn('src="data:image/png;base64,', page)

    def test_days_absent_counts_school_days_only(self):
        from attendance.models import AttendanceEntry, AttendanceStatus
        today = timezone.localdate()
        weekday = next(today - timedelta(days=i) for i in range(1, 10) if (today - timedelta(days=i)).weekday() < 5)
        saturday = next(today - timedelta(days=i) for i in range(1, 10) if (today - timedelta(days=i)).weekday() == 5)
        for d in (weekday, saturday):
            AttendanceEntry.objects.create(student=self.student, date=d, status=AttendanceStatus.ABSENT,
                                           class_name="Grade 5", marked_by=self.hos)
        page = self._preview()
        self.assertIn("Days Absent : 1", page)
        self.assertIn("of 1 school day marked", page)

    def test_student_progress_report_waits_for_end_of_term(self):
        ExamScore.objects.create(
            student=self.student, term=self.term, subject_name="Alg", exam_type="quiz",
            score=Decimal("83"), entered_by=self.hos, status=ScoreStatus.APPROVED, is_locked=True,
        )
        page = self.client.get(reverse("academics:learner_report", args=[self.student.pk]),
                               {"term": self.term.pk}).content.decode()
        self.assertIn("Awaiting end of term", page)
        self.assertNotIn("83.0%", page)
        self.assertNotIn(">83.0<", page)
