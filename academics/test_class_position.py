"""
Class position on the progress report / report card, and view-only access for
assistant class teachers (scores, progress report, PTC).
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.class_position import learner_positions, rank
from academics.models import (
    AcademicYear, AssessmentMode, Department, ExamScore, ExamTypeConfiguration,
    GradeClass, ScoreStatus, Subject, Term,
)
from academics.tests import assign_role_group
from hr.models import StaffProfile, TeacherClassAssignment
from students.models import Student, StudentStatus
from users.models import User, UserRole


class RankTests(TestCase):
    def test_ties_share_a_position_and_the_next_is_skipped(self):
        averages = {1: 80.0, 2: 70.0, 3: 70.0, 4: 60.0}
        self.assertEqual(rank(averages, 1), {"position": 1, "of": 4})
        self.assertEqual(rank(averages, 2), {"position": 2, "of": 4})
        self.assertEqual(rank(averages, 3), {"position": 2, "of": 4})
        self.assertEqual(rank(averages, 4), {"position": 4, "of": 4})

    def test_learner_without_an_average_is_not_ranked(self):
        self.assertIsNone(rank({1: 80.0}, 2))


class ClassFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term1 = Term.objects.create(academic_year=cls.year, name="Term 1",
                                        start_date=today - timedelta(days=30), end_date=today + timedelta(days=60))
        cls.term2 = Term.objects.create(academic_year=cls.year, name="Term 2",
                                        start_date=today + timedelta(days=70), end_date=today + timedelta(days=150))
        for code, name, weight in (("quiz", "Quiz", 20), ("mid_term", "Mid-term", 30),
                                   ("end_of_term", "End of Term", 50)):
            ExamTypeConfiguration.objects.update_or_create(
                code=code, defaults={"name": name, "weight_percentage": Decimal(weight),
                                     "max_score": Decimal("100"), "is_active": True})
        cls.g4 = GradeClass.objects.create(name="Grade 4", department=Department.PRIMARY, sort_order=4)
        cls.g5 = GradeClass.objects.create(name="Grade 5", department=Department.PRIMARY, sort_order=5)
        for i, name in enumerate(("Mathematics", "Science")):
            s = Subject.objects.create(name=name, code=f"S{i}", department=Department.PRIMARY)
            s.classes.add(cls.g4, cls.g5)
        art = Subject.objects.create(name="Art", code="ART", department=Department.PRIMARY,
                                     assessment_mode=AssessmentMode.REMARK)
        art.classes.add(cls.g4)

        def student(adm, first, cls_name="Grade 4"):
            return Student.objects.create(admission_no=adm, first_name=first, last_name="Learner",
                                          class_name=cls_name, academic_year=cls.year, status=StudentStatus.ACTIVE)
        cls.amani = student("A1", "Amani")
        cls.baraka = student("B1", "Baraka")
        cls.caren = student("C1", "Caren")
        cls.daudi = student("D1", "Daudi")
        cls.eli = student("E1", "Eli", "Grade 5")
        cls.hos = cls._user("hos", UserRole.HEAD_OF_SCHOOL)

        def score(st, subject, etype, value, term=None, status=ScoreStatus.APPROVED):
            ExamScore.objects.create(student=st, term=term or cls.term1, subject_name=subject,
                                     exam_type=etype, score=Decimal(value), entered_by=cls.hos, status=status)
        # Quiz averages: Amani 80, Baraka 80 (tie), Caren 50. Caren's remark-only
        # Art mark is ignored. Daudi's quiz is not approved: not counted.
        score(cls.amani, "Mathematics", "quiz", 90)
        score(cls.amani, "Science", "quiz", 70)
        score(cls.baraka, "Mathematics", "quiz", 80)
        score(cls.baraka, "Science", "quiz", 80)
        score(cls.caren, "Mathematics", "quiz", 50)
        score(cls.caren, "Science", "quiz", 50)
        score(cls.caren, "Art", "quiz", 100)
        score(cls.daudi, "Mathematics", "quiz", 99, status=ScoreStatus.SUBMITTED)
        # Mid-Term: Caren 90 beats Amani 60; Baraka has none.
        score(cls.amani, "Mathematics", "mid_term", 60)
        score(cls.caren, "Mathematics", "mid_term", 90)
        # Another class never counts.
        score(cls.eli, "Mathematics", "quiz", 100)

    @classmethod
    def _user(cls, name, role):
        u = User.objects.create_user(username=name, email=f"{name}@example.test", password="x", role=role)
        assign_role_group(u)
        return u

    @staticmethod
    def _staff(user):
        return StaffProfile.objects.create(user=user, employment_start_date=date(2024, 1, 1),
                                           full_name=user.username, department="PRIMARY", job_title="Teacher")

    def score(self, st, subject, etype, value, term=None):
        ExamScore.objects.create(student=st, term=term or self.term1, subject_name=subject, exam_type=etype,
                                 score=Decimal(value), entered_by=self.hos, status=ScoreStatus.APPROVED)

    def positions(self, student, term=None):
        return {p["code"]: p for p in learner_positions(student, term or self.term1, student.class_name)}


class ClassPositionTests(ClassFixture):
    def test_quiz_ranking_with_ties_and_only_learners_with_approved_marks(self):
        quiz = {s.first_name: self.positions(s)["quiz"]["term"] for s in (self.amani, self.baraka, self.caren, self.daudi)}
        self.assertEqual(quiz, {
            "Amani": {"position": 1, "of": 3},
            "Baraka": {"position": 1, "of": 3},
            "Caren": {"position": 3, "of": 3},
            "Daudi": None,
        })

    def test_each_measure_is_ranked_separately(self):
        self.assertEqual(self.positions(self.caren)["mid_term"]["term"], {"position": 1, "of": 2})
        self.assertEqual(self.positions(self.amani)["mid_term"]["term"], {"position": 2, "of": 2})
        self.assertIsNone(self.positions(self.baraka)["mid_term"]["term"])
        self.assertIsNone(self.positions(self.amani)["end_of_term"]["term"])
        self.assertEqual([p["code"] for p in learner_positions(self.amani, self.term1, "Grade 4")],
                         ["quiz", "mid_term", "end_of_term", "overall"])

    def test_overall_average_ranks_weighted_mark_across_subjects(self):
        # Amani: Maths (90*20+60*30)/50 = 72, Science 70 -> 71.
        # Baraka 80. Caren: Maths (50*20+90*30)/50 = 74, Science 50 -> 62.
        overall = {s.first_name: self.positions(s)["overall"]["term"]["position"]
                   for s in (self.amani, self.baraka, self.caren)}
        self.assertEqual(overall, {"Baraka": 1, "Amani": 2, "Caren": 3})

    def test_year_to_date_includes_earlier_terms_only(self):
        self.score(self.caren, "Mathematics", "quiz", 100, term=self.term2)
        self.score(self.caren, "Science", "quiz", 100, term=self.term2)
        self.score(self.amani, "Mathematics", "quiz", 40, term=self.term2)
        self.score(self.amani, "Science", "quiz", 40, term=self.term2)
        # Term 1 report: year to date is Term 1 alone.
        self.assertEqual(self.positions(self.caren)["quiz"]["year"], {"position": 3, "of": 3})
        # Term 2: Caren first this term; across the year Caren 75 vs Amani 60 vs Baraka 80.
        t2 = self.positions(self.caren, self.term2)["quiz"]
        self.assertEqual(t2["term"], {"position": 1, "of": 2})
        self.assertEqual(t2["year"], {"position": 2, "of": 3})

    def test_class_print_shows_each_learners_position_with_flat_query_cost(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from academics.models import ReportCard

        def print_class():
            first = ReportCard.objects.filter(term=self.term1).order_by("pk").first()
            with CaptureQueriesContext(connection) as q:
                resp = self.client.get(reverse("academics:report_preview_class", args=[first.pk]))
            self.assertEqual(resp.status_code, 200)
            return resp, len(q.captured_queries)

        for st in (self.amani, self.baraka, self.caren):
            ReportCard.objects.create(student=st, term=self.term1, generated_by=self.hos)
        self.client.force_login(self.hos)
        resp, three = print_class()
        html = "".join(resp.context["class_reports"])
        self.assertEqual(html.count("CLASS POSITION"), 3)
        # Amani and Baraka tie first on the quiz, this term and year to date.
        self.assertEqual(html.count("<b>1</b> of 3"), 4)
        # Five more learners: each costs only their own lookups, never a
        # rescan of the class or the whole school.
        for i in range(5):
            extra = Student.objects.create(admission_no=f"X{i}", first_name=f"Extra{i}", last_name="Learner",
                                           class_name="Grade 4", academic_year=self.year,
                                           status=StudentStatus.ACTIVE)
            self.score(extra, "Mathematics", "quiz", 60 + i)
            ReportCard.objects.create(student=extra, term=self.term1, generated_by=self.hos)
        _, eight = print_class()
        self.assertLessEqual((eight - three) / 5, 15)

    def test_progress_report_shows_position_and_class_size_but_no_other_learners(self):
        self.client.force_login(self.hos)
        resp = self.client.get(reverse("academics:learner_report", args=[self.caren.pk]),
                               {"term": self.term1.pk})
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("CLASS POSITION", html)
        self.assertIn("<b>3</b> of 3", html)
        # The overall position waits for the end of term, like the average.
        self.assertIn("Awaiting end of term", html)
        for other in ("Amani", "Baraka", "Daudi"):
            self.assertNotIn(other, html)



class ClassResultsTests(ClassFixture):
    def test_rows_have_subject_marks_total_average_and_rank_like_the_report_card(self):
        from academics.class_position import class_results
        res = class_results("Grade 4", self.term1)
        self.assertEqual([s["name"] for s in res["subjects"]], ["Mathematics", "Science"])  # Art is remark-only
        self.assertEqual([s["label"] for s in res["subjects"]], ["S0", "S1"])
        rows = {r["student"].first_name: r for r in res["rows"]}
        # Ranked best first; Daudi (nothing approved) last and unranked; Eli is in another class.
        self.assertEqual([r["student"].first_name for r in res["rows"]], ["Baraka", "Amani", "Caren", "Daudi"])
        self.assertEqual(rows["Amani"]["marks"], [72.0, 70.0])
        self.assertEqual(rows["Amani"]["total"], 142.0)
        self.assertEqual(rows["Amani"]["average"], 71.0)
        self.assertEqual(rows["Amani"]["position"], self.positions(self.amani)["overall"]["term"]["position"])
        self.assertEqual(rows["Baraka"]["position"], 1)
        self.assertEqual(rows["Daudi"]["marks"], [None, None])
        self.assertIsNone(rows["Daudi"]["position"])
        self.assertEqual(res["ranked"], 3)

    def test_equal_averages_share_a_rank(self):
        from academics.class_position import class_results
        self.score(self.daudi, "Mathematics", "mid_term", 80)
        self.score(self.daudi, "Science", "quiz", 80)
        rows = {r["student"].first_name: r["position"] for r in class_results("Grade 4", self.term1)["rows"]}
        self.assertEqual(rows, {"Baraka": 1, "Daudi": 1, "Amani": 3, "Caren": 4})

    def test_single_exam_uses_only_that_exams_marks_and_positions(self):
        from academics.class_position import class_results
        res = class_results("Grade 4", self.term1, exam_type="quiz")
        rows = {r["student"].first_name: r for r in res["rows"]}
        self.assertEqual(rows["Amani"]["marks"], [90.0, 70.0])
        self.assertEqual(rows["Amani"]["average"], 80.0)
        for st in (self.amani, self.baraka, self.caren):
            self.assertEqual(rows[st.first_name]["position"], self.positions(st)["quiz"]["term"]["position"])
        mid = {r["student"].first_name: r for r in class_results("Grade 4", self.term1, exam_type="mid_term")["rows"]}
        self.assertEqual(mid["Caren"]["marks"], [90.0])  # only Maths had a mid-term
        self.assertIsNone(mid["Baraka"]["position"])

    def test_report_list_page_exam_filter(self):
        self.client.force_login(self.hos)
        url = reverse("academics:report_card_list")
        resp = self.client.get(url, {"class_name": "Grade 4", "term": self.term1.pk})
        self.assertContains(resp, "Combined term report")
        resp = self.client.get(url, {"class_name": "Grade 4", "term": self.term1.pk, "type": "quiz"})
        self.assertContains(resp, "Quiz results")
        self.assertEqual(resp.context["results"]["rows"][0]["average"], 80.0)

    def test_report_list_page_shows_the_results_sheet_and_exports_it(self):
        self.client.force_login(self.hos)
        url = reverse("academics:report_card_list")
        resp = self.client.get(url, {"class_name": "Grade 4", "term": self.term1.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Results for Term 1")
        self.assertContains(resp, ">S0</th>")
        self.assertContains(resp, reverse("academics:learner_report", args=[self.amani.pk]))
        csv = self.client.get(url, {"class_name": "Grade 4", "term": self.term1.pk, "export": "csv"})
        self.assertEqual(csv["Content-Type"], "text/csv")
        lines = csv.content.decode().splitlines()
        self.assertEqual(lines[0], "Admission No,Student,Sex,Mathematics,Science,Total,Average,Position,Grade")
        self.assertTrue(lines[1].startswith("B1,"))

class AssistantClassTeacherTests(ClassFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.assistant = cls._user("asst", UserRole.TEACHER)
        cls.class_teacher = cls._user("ct", UserRole.TEACHER)
        TeacherClassAssignment.objects.create(teacher=cls._staff(cls.class_teacher), term=cls.term1,
                                              grade_class=cls.g4, is_class_teacher=True,
                                              subjects_taught=["Mathematics"])
        asst_staff = cls._staff(cls.assistant)
        TeacherClassAssignment.objects.create(teacher=asst_staff, term=cls.term1, grade_class=cls.g4,
                                              is_assistant_class_teacher=True, subjects_taught=[])
        # Teaches Science in Grade 5 only as a subject teacher.
        TeacherClassAssignment.objects.create(teacher=asst_staff, term=cls.term1, grade_class=cls.g5,
                                              subjects_taught=["Science"])

    def test_score_grid_shows_every_subject_of_the_assisted_class_read_only(self):
        self.client.force_login(self.assistant)
        resp = self.client.get(reverse("academics:primary_assessment"),
                               {"term": self.term1.pk, "class_name": "Grade 4"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["selected_class"], "Grade 4")
        self.assertEqual(set(resp.context["subjects"]), {"Mathematics", "Science"})
        self.assertFalse(resp.context["editable_subjects"])
        cells = [c for r in resp.context["rows"] for c in r["cells"]]
        self.assertTrue(cells)
        self.assertTrue(all(c["readonly"] for c in cells))

    def test_score_grid_says_view_only_instead_of_a_save_bar(self):
        self.client.force_login(self.assistant)
        html = self.client.get(reverse("academics:primary_assessment"),
                               {"term": self.term1.pk, "class_name": "Grade 4"}).content.decode()
        self.assertIn("View only", html)
        self.assertNotIn('id="ag-save"', html)
        own = self.client.get(reverse("academics:primary_assessment"),
                              {"term": self.term1.pk, "class_name": "Grade 5"}).content.decode()
        self.assertIn('id="ag-save"', own)

    def test_other_class_keeps_subject_teacher_scope(self):
        self.client.force_login(self.assistant)
        resp = self.client.get(reverse("academics:primary_assessment"),
                               {"term": self.term1.pk, "class_name": "Grade 5"})
        self.assertEqual(set(resp.context["subjects"]), {"Science"})
        self.assertEqual(set(resp.context["editable_subjects"]), {"Science"})

    def test_cannot_save_marks_in_the_assisted_class(self):
        import json
        self.client.force_login(self.assistant)
        resp = self.client.post(
            reverse("academics:api_primary_scores", args=[self.baraka.pk]),
            data=json.dumps({"term": self.term1.pk, "scores": {"Mathematics": {"mid_term": 10}}}),
            content_type="application/json")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(ExamScore.objects.filter(student=self.baraka, exam_type="mid_term").exists())

    def test_score_api_returns_every_subject_for_a_learner(self):
        self.client.force_login(self.assistant)
        resp = self.client.get(reverse("academics:api_primary_scores", args=[self.amani.pk]),
                               {"term": self.term1.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.json()["scores"]), {"Mathematics", "Science"})

    def test_progress_report_for_own_class_only(self):
        self.client.force_login(self.assistant)
        own = self.client.get(reverse("academics:learner_report", args=[self.amani.pk]), {"term": self.term1.pk})
        self.assertEqual(own.status_code, 200)
        other_class = GradeClass.objects.create(name="Grade 6", department=Department.PRIMARY, sort_order=6)
        stranger = Student.objects.create(admission_no="F1", first_name="Fatma", last_name="Learner",
                                          class_name=other_class.name, academic_year=self.year,
                                          status=StudentStatus.ACTIVE)
        resp = self.client.get(reverse("academics:learner_report", args=[stranger.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_ptc_view_only_for_own_class(self):
        from ptc.services import can_open_ptc
        self.assertTrue(can_open_ptc(self.assistant, self.amani))
        self.assertFalse(can_open_ptc(self.assistant, self.eli))  # subject teacher there only

        self.client.force_login(self.assistant)
        resp = self.client.get(reverse("ptc:ptc_screen", args=[self.amani.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["is_readonly"])
        self.assertFalse(resp.context["can_edit_attributes"])
        # Cannot override the progress trend.
        self.client.post(reverse("ptc:ptc_screen", args=[self.amani.pk]),
                         {"progress_trend_override": "improving"})
        from ptc.models import PTCProgressTrendOverride
        self.assertFalse(PTCProgressTrendOverride.objects.exists())
        self.assertEqual(self.client.get(reverse("ptc:ptc_screen", args=[self.eli.pk])).status_code, 403)

    def test_exam_scores_menu_opens_the_assessment_grid_without_timetable_slots(self):
        # The assistant has class assignments but no timetabled lessons.
        self.client.force_login(self.assistant)
        resp = self.client.get(reverse("academics:exam_scores_entry"))
        self.assertRedirects(resp, reverse("academics:primary_assessment"), fetch_redirect_response=False)

    def test_performance_scope_covers_the_whole_assisted_class_in_other_terms(self):
        # No assignment in Term 2: the scope falls back to current assignments
        # and still gives every subject of the assisted class.
        from academics.performance import resolve_scope
        scope = resolve_scope(self.assistant, [self.term2])
        self.assertIsNone(scope.pairs["Grade 4"])
        self.assertEqual(scope.pairs["Grade 5"], {"Science"})

    def test_user_edit_form_shows_the_assisted_class(self):
        from users.forms import UserUpdateForm
        form = UserUpdateForm(instance=self.assistant)
        self.assertEqual(form.fields["teacher_role"].initial, "assistant_class_teacher")
        self.assertEqual(form.fields["class_teacher_class"].initial, self.g4.pk)

    def test_user_list_marks_the_assistant_class_teacher(self):
        self.client.force_login(self._user("sa", UserRole.SUPER_ADMIN))
        resp = self.client.get(reverse("users:user_list"))
        self.assertEqual(resp.status_code, 200)
        rows = resp.context["user_assignments"][self.assistant.pk]
        g4 = next(r for r in rows if r["class_name"] == "Grade 4")
        self.assertTrue(g4["is_assistant_class_teacher"])
