"""
Primary report: the class's Term Scripture (set on the Comments page and
printed beside the student's Term Scripture remark), the term end date in the
title pill, the school stamp dated at the end of term, and no repeated academic
year in the page-2 header.
"""
import json
import tempfile
from datetime import date, datetime, timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import (
    AcademicYear, AssessmentMode, ClassTermScripture, Department, GradeClass, ReportCard,
    ReportCardStatus, Subject, SubjectTermRemark, Term,
)
from academics.tests import assign_role_group
from hr.models import StaffProfile, TeacherClassAssignment
from students.models import Student, StudentStatus
from users.models import User, UserRole


class TermScriptureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Term 1 - 2026/2027",
            start_date=today - timedelta(days=60), end_date=date(2026, 12, 4),
            endterm_exam_end_date=today - timedelta(days=1), grading_deadline=today + timedelta(days=10),
        )
        cls.g2 = GradeClass.objects.create(name="Grade 2", department=Department.PRIMARY, sort_order=2)
        cls.g3 = GradeClass.objects.create(name="Grade 3", department=Department.PRIMARY, sort_order=3)
        cls.hos = User.objects.create_user("scr-hos", email="scr-hos@example.test", password="x",
                                           role=UserRole.HEAD_OF_SCHOOL)
        assign_role_group(cls.hos)
        cls.teacher = User.objects.create_user("scr-teacher", email="scr-t@example.test", password="x",
                                               role=UserRole.TEACHER)
        assign_role_group(cls.teacher)
        staff = StaffProfile.objects.create(user=cls.teacher, full_name="Class Teacher",
                                            department=Department.PRIMARY,
                                            employment_start_date=today - timedelta(days=365))
        TeacherClassAssignment.objects.create(teacher=staff, term=cls.term, grade_class=cls.g2,
                                              is_class_teacher=True)
        cls.student = Student.objects.create(admission_no="SCR-1", first_name="Anna", last_name="Kilenga",
                                             class_name="Grade 2", academic_year=cls.year,
                                             status=StudentStatus.ACTIVE)
        cls.report = ReportCard.objects.create(student=cls.student, term=cls.term, generated_by=cls.hos)

    def setUp(self):
        self._media = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
        self._media.enable()
        self.addCleanup(self._media.disable)
        self.url = reverse("academics:primary_comments_entry")

    def _save(self, user, verse, class_name="Grade 2"):
        self.client.force_login(user)
        return self.client.post(
            self.url + "?class_name=" + class_name,
            data=json.dumps({"scripture": verse, "class_name": class_name, "term": self.term.pk}),
            content_type="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def _preview(self):
        self.client.force_login(self.hos)
        return self.client.get(reverse("academics:report_preview", args=[self.report.pk])).content.decode()

    def test_class_teacher_sets_the_verse_on_the_comments_page(self):
        r = self._save(self.teacher, "  Romans 5:1-11,  Psalm 100:1-5 ")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(ClassTermScripture.verse_for("Grade 2", self.term), "Romans 5:1-11, Psalm 100:1-5")
        page = self.client.get(self.url + f"?class_name=Grade 2&term={self.term.pk}").content.decode()
        self.assertIn('value="Romans 5:1-11, Psalm 100:1-5"', page)

    def test_teacher_cannot_set_another_class_verse(self):
        r = self._save(self.teacher, "John 3:16", class_name="Grade 3")
        self.assertEqual(r.status_code, 403)
        self.assertFalse(ClassTermScripture.objects.exists())

    def test_teacher_locked_out_once_reports_go_for_sign_off(self):
        ReportCard.objects.filter(pk=self.report.pk).update(status=ReportCardStatus.PENDING_SIGN_OFF)
        self.assertEqual(self._save(self.teacher, "John 3:16").status_code, 409)
        self.assertEqual(self._save(self.hos, "John 3:16").status_code, 200)

    def test_report_prints_verse_beside_the_scripture_remark(self):
        subject = Subject.objects.create(name="Term Scripture", department=Department.PRIMARY,
                                         assessment_mode=AssessmentMode.REMARK)
        subject.classes.add(self.g2)
        SubjectTermRemark.objects.create(student=self.student, term=self.term, subject_name="Term Scripture",
                                         remark="High", entered_by=self.hos)
        ClassTermScripture.objects.create(term=self.term, class_name="Grade 2", verse="Psalm 100:1-5")
        page = self._preview()
        self.assertIn('<b>Term Scripture</b><span class="verse">(Psalm 100:1-5)</span></td><td>High</td>', page)

    def test_verse_goes_on_the_bible_studies_row_without_a_scripture_subject(self):
        subject = Subject.objects.create(name="Bible Studies", department=Department.PRIMARY,
                                         assessment_mode=AssessmentMode.REMARK)
        subject.classes.add(self.g2)
        SubjectTermRemark.objects.create(student=self.student, term=self.term, subject_name="Bible Studies",
                                         remark="Outstanding", entered_by=self.hos)
        ClassTermScripture.objects.create(term=self.term, class_name="Grade 2", verse="Psalm 100:1-5")
        page = self._preview()
        self.assertIn('<b>Bible Studies</b><span class="verse">(Term Scripture: '
                      'Psalm 100:1-5)</span></td><td>Outstanding</td>', page)
        self.assertNotIn("<b>Term Scripture</b>", page)

    def test_verse_gets_its_own_row_without_a_scripture_subject(self):
        ClassTermScripture.objects.create(term=self.term, class_name="Grade 2", verse="Psalm 100:1-5")
        self.assertIn('<b>Term Scripture</b><span class="verse">(Psalm 100:1-5)</span>', self._preview())

    def test_term_pill_page_two_header_and_stamp(self):
        ReportCard.objects.filter(pk=self.report.pk).update(
            status=ReportCardStatus.PUBLISHED, signed_off_by=self.hos,
            signed_off_at=timezone.make_aware(datetime(2026, 11, 19, 10)),
        )
        page = self._preview()
        self.assertIn("Term 1 - 2026/2027 · Term ends 4 Dec 2026</span>", page)
        self.assertNotIn("2026/2027, 2026/2027", page)
        self.assertIn('<div class="stamp-date">04 DEC 2026</div>', page)
