"""
Performance Report: one report, two views (who needs help / subject across
classes), filterable by academic year, term or all terms, class, subject and
assessment type, scoped by role, built only from approved marks.
"""
from datetime import date, timedelta
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
from hr.models import StaffProfile, TeacherClassAssignment
from students.models import EnrollmentHistory, Student, StudentStatus
from users.models import User, UserRole

URL = reverse("academics:performance_report")


class PerformanceReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.old_year = AcademicYear.objects.create(name="2025/2026")
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.old_term = Term.objects.create(
            academic_year=cls.old_year, name="Term 3",
            start_date=today - timedelta(days=200), end_date=today - timedelta(days=120),
        )
        cls.term1 = Term.objects.create(
            academic_year=cls.year, name="Term 1",
            start_date=today - timedelta(days=30), end_date=today + timedelta(days=60),
        )
        cls.term2 = Term.objects.create(
            academic_year=cls.year, name="Term 2",
            start_date=today + timedelta(days=70), end_date=today + timedelta(days=150),
        )
        for code, name, weight in (("quiz", "Quiz", 20), ("mid_term", "Mid-term", 30), ("end_of_term", "End of Term", 50)):
            ExamTypeConfiguration.objects.update_or_create(
                code=code, defaults={"name": name, "weight_percentage": Decimal(weight),
                                     "max_score": Decimal("100"), "is_active": True},
            )
        cls.g4 = GradeClass.objects.create(name="Grade 4", department=Department.PRIMARY, sort_order=4)
        cls.g5 = GradeClass.objects.create(name="Grade 5", department=Department.PRIMARY, sort_order=5)
        cls.g8 = GradeClass.objects.create(name="Grade 8", department=Department.LOWER_SECONDARY, sort_order=8)
        for i, name in enumerate(("Mathematics", "Science")):
            s = Subject.objects.create(name=name, code=f"S{i}", department=Department.PRIMARY)
            s.classes.add(cls.g4, cls.g5, cls.g8)

        def student(adm, first, cls_name):
            return Student.objects.create(
                admission_no=adm, first_name=first, last_name="Learner",
                class_name=cls_name, academic_year=cls.year, status=StudentStatus.ACTIVE,
            )
        cls.amani = student("A1", "Amani", "Grade 4")
        cls.baraka = student("B1", "Baraka", "Grade 5")
        cls.caren = student("C1", "Caren", "Grade 8")

        cls.hos = cls._user("hos", UserRole.HEAD_OF_SCHOOL)
        cls.hod = cls._user("hod", UserRole.PRIMARY_HOD)
        cls.class_teacher = cls._user("ct", UserRole.TEACHER)
        cls.subject_teacher = cls._user("st", UserRole.TEACHER)
        cls.admin = cls._user("admin", UserRole.ADMIN_OFFICER)
        ct_staff = cls._staff(cls.class_teacher)
        st_staff = cls._staff(cls.subject_teacher)
        TeacherClassAssignment.objects.create(teacher=ct_staff, term=cls.term1, grade_class=cls.g4,
                                              is_class_teacher=True, subjects_taught=["Mathematics"])
        for gc in (cls.g4, cls.g5):
            TeacherClassAssignment.objects.create(teacher=st_staff, term=cls.term1, grade_class=gc,
                                                  subjects_taught=["Science"])

        def score(st, subject, etype, value, term=None, status=ScoreStatus.APPROVED):
            ExamScore.objects.create(student=st, term=term or cls.term1, subject_name=subject,
                                     exam_type=etype, score=Decimal(value), entered_by=cls.hos, status=status)
        # Amani: Maths quiz 40 (w20) + mid 70 (w30) -> (800+2100)/50 = 58.0 ; Science 45
        score(cls.amani, "Mathematics", "quiz", 40)
        score(cls.amani, "Mathematics", "mid_term", 70)
        score(cls.amani, "Science", "quiz", 45)
        # Unapproved marks never count.
        score(cls.amani, "Mathematics", "end_of_term", 0, status=ScoreStatus.SUBMITTED)
        score(cls.baraka, "Mathematics", "quiz", 90)
        score(cls.baraka, "Science", "quiz", 80)
        score(cls.baraka, "Science", "quiz", 60, term=cls.term2)
        score(cls.caren, "Mathematics", "quiz", 30)
        # Last year Baraka was in Grade 4.
        score(cls.baraka, "Mathematics", "quiz", 20, term=cls.old_term)
        EnrollmentHistory.objects.create(student=cls.baraka, academic_year=cls.old_year, class_name="Grade 4")

    @classmethod
    def _user(cls, name, role):
        u = User.objects.create_user(username=name, email=f"{name}@example.test", password="x", role=role)
        assign_role_group(u)
        return u

    @staticmethod
    def _staff(user):
        return StaffProfile.objects.create(user=user, employment_start_date=date(2024, 1, 1),
                                           full_name=user.username, department="PRIMARY", job_title="Teacher")

    def _get(self, user, **params):
        self.client.force_login(user)
        return self.client.get(URL, params)

    def _rows(self, resp):
        return {(r["name"].split()[0], r["subject"]): r["mark"] for r in resp.context["page_obj"].paginator.object_list}

    def test_hos_sees_whole_school_worst_first_from_approved_marks(self):
        resp = self._get(self.hos, term=self.term1.pk, show="all")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["scope"].label, "Whole school")
        rows = list(resp.context["page_obj"].object_list)
        self.assertEqual([r["mark"] for r in rows], sorted(r["mark"] for r in rows))
        marks = self._rows(resp)
        self.assertEqual(marks[("Amani", "Mathematics")], 58.0)  # submitted 0 ignored
        self.assertEqual(marks[("Caren", "Mathematics")], 30.0)
        s = resp.context["summary"]
        self.assertEqual((s["flagged"], s["critical"]), (1, 2))

    def test_default_list_shows_only_learners_below_pass(self):
        resp = self._get(self.hos, term=self.term1.pk)
        self.assertEqual(set(self._rows(resp)), {("Caren", "Mathematics"), ("Amani", "Science"), ("Amani", "Mathematics")})

    def test_hod_is_limited_to_their_phase(self):
        resp = self._get(self.hod, term=self.term1.pk, show="all")
        self.assertNotIn(("Caren", "Mathematics"), self._rows(resp))
        self.assertEqual(resp.context["class_options"], ["Grade 4", "Grade 5"])

    def test_class_teacher_sees_their_class_all_subjects(self):
        resp = self._get(self.class_teacher, term=self.term1.pk, show="all")
        self.assertEqual(set(self._rows(resp)), {("Amani", "Mathematics"), ("Amani", "Science")})
        self.assertTrue(resp.context["class_locked"])

    def test_subject_teacher_sees_their_subject_across_their_classes(self):
        resp = self._get(self.subject_teacher, term=self.term1.pk, show="all", view="subjects")
        self.assertEqual(set(self._rows(resp)), {("Amani", "Science"), ("Baraka", "Science")})
        self.assertTrue(resp.context["subject_locked"])
        self.assertEqual([c["name"] for c in resp.context["by_class"]], ["Grade 4", "Grade 5"])

    def test_filters_by_assessment_type_class_and_subject(self):
        resp = self._get(self.hos, term=self.term1.pk, show="all", type="mid_term")
        self.assertEqual(self._rows(resp), {("Amani", "Mathematics"): 70.0})
        resp = self._get(self.hos, term=self.term1.pk, show="all", **{"class": "Grade 5", "subject": "Science"})
        self.assertEqual(self._rows(resp), {("Baraka", "Science"): 80.0})

    def test_all_terms_averages_term_results_within_the_year(self):
        resp = self._get(self.hos, year=self.year.pk, term="all", show="all")
        self.assertIsNone(resp.context["selected_term"])
        self.assertEqual(self._rows(resp)[("Baraka", "Science")], 70.0)

    def test_past_year_groups_learners_by_class_they_were_in(self):
        resp = self._get(self.hos, year=self.old_year.pk, show="all")
        rows = list(resp.context["page_obj"].object_list)
        self.assertEqual([(r["name"], r["class_name"], r["mark"]) for r in rows], [("Baraka Learner", "Grade 4", 20.0)])

    def test_subject_view_compares_classes_and_ranks_subjects(self):
        resp = self._get(self.hos, term=self.term1.pk, view="subjects", focus="Mathematics")
        by_class = {c["name"]: c["average"] for c in resp.context["by_class"]}
        self.assertEqual(by_class, {"Grade 4": 58.0, "Grade 5": 90.0, "Grade 8": 30.0})
        self.assertEqual([s["name"] for s in resp.context["ranking"]], ["Science", "Mathematics"])
        self.assertContains(resp, "Mathematics by class")

    def test_band_filters_and_search(self):
        resp = self._get(self.hos, term=self.term1.pk, show="critical")
        self.assertEqual(set(self._rows(resp)), {("Caren", "Mathematics"), ("Amani", "Science")})
        resp = self._get(self.hos, term=self.term1.pk, show="support")
        self.assertEqual(set(self._rows(resp)), {("Amani", "Mathematics")})
        resp = self._get(self.hos, term=self.term1.pk, show="all", q="bara")
        self.assertEqual({k[0] for k in self._rows(resp)}, {"Baraka"})

    def test_group_by_learner_lists_weak_subjects(self):
        resp = self._get(self.hos, term=self.term1.pk, group="learner")
        rows = {r["name"]: r for r in resp.context["help_rows"]}
        self.assertEqual(set(rows), {"Amani Learner", "Caren Learner"})
        self.assertEqual(rows["Amani Learner"]["mark"], 51.5)
        self.assertEqual([w["subject"] for w in rows["Amani Learner"]["weak"]], ["Science", "Mathematics"])

    def test_class_subject_matrix(self):
        resp = self._get(self.hos, term=self.term1.pk, view="subjects")
        m = resp.context["matrix"]
        self.assertEqual(m["subjects"], ["Mathematics", "Science"])
        self.assertEqual([r["name"] for r in m["rows"]], ["Grade 4", "Grade 5", "Grade 8"])
        self.assertIsNone(m["rows"][2]["cells"][1])  # Grade 8 has no Science marks
        self.assertEqual(m["totals"][1]["average"], 62.5)

    def test_print_document_lists_every_row_and_both_views(self):
        resp = self._get(self.hos, term=self.term1.pk, format="print", show="all", page_size=25)
        self.assertTemplateUsed(resp, "academics/performance_report_print.html")
        self.assertEqual(len(resp.context["help_rows"]), 5)
        self.assertContains(resp, "Subject performance across classes")
        self.assertContains(resp, "Head of School")

    def test_csv_export(self):
        resp = self._get(self.hos, term=self.term1.pk, export="csv")
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn("Caren Learner", resp.content.decode())

    def test_other_roles_are_denied(self):
        self.assertEqual(self._get(self.admin).status_code, 403)


class LearnerProfileAndProgressReportTests(PerformanceReportTests):
    """Student profile and the printable Learner Progress Report use the same
    approved-marks figures as the Performance Report."""

    def test_learner_record_uses_approved_marks_with_class_comparison(self):
        from academics import performance
        rec = performance.learner_record(self.amani, self.term1)
        self.assertEqual({s["subject"]: s["mark"] for s in rec["subjects"]}, {"Mathematics": 58.0, "Science": 45.0})
        self.assertEqual((rec["average"], rec["grade"], rec["position"], rec["class_size"]), (51.5, "D", 1, 1))
        maths = next(s for s in rec["subjects"] if s["subject"] == "Mathematics")
        self.assertEqual(maths["cells"][:2], [40.0, 70.0])  # quiz, mid-term; end of term not approved
        self.assertIsNone(maths["cells"][2])
        rec = performance.learner_record(self.baraka, self.term1)
        self.assertEqual([h["term"] for h in rec["history"]], [self.old_term, self.term1, self.term2])
        self.assertEqual(rec["trend"], "improving")

    def test_profile_shows_real_figures_and_hides_empty_cards(self):
        self.client.force_login(self.hos)
        resp = self.client.get(reverse("students:detail", args=[self.amani.pk]), {"term": self.term1.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["record"]["average"], 51.5)
        self.assertContains(resp, "Print progress report")
        self.assertNotContains(resp, "Discipline Incidents</h2>")
        self.assertNotContains(resp, "No guardians linked")
        self.assertNotContains(resp, "Recent Attendance</div>")  # no attendance marked
        empty = Student.objects.create(admission_no="Z1", first_name="Zuri", last_name="Learner",
                                       class_name="Grade 4", status=StudentStatus.ACTIVE)
        resp = self.client.get(reverse("students:detail", args=[empty.pk]))
        self.assertNotContains(resp, "Academic &amp; Attendance Performance</h2>")
        self.assertNotContains(resp, "<span>Academic Record</span>")

    def test_progress_report_prints_term_results(self):
        self.client.force_login(self.hos)
        resp = self.client.get(reverse("academics:learner_report", args=[self.amani.pk]), {"term": self.term1.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "academics/learner_report_print.html")
        self.assertContains(resp, "Learner Progress Report")
        self.assertContains(resp, "58.0")
        self.assertContains(resp, "Parent / guardian")
        # Without a PDF engine the download falls back to the print page.
        import sys
        from unittest import mock
        with mock.patch.dict(sys.modules, {"weasyprint": None}):
            resp = self.client.get(reverse("academics:learner_report", args=[self.amani.pk]), {"format": "pdf"})
        self.assertEqual(resp["Content-Type"].split(";")[0], "text/html")
        self.assertTrue(resp.context["print_now"])
