"""The lesson plans page: search and the teacher filter work, plans waiting on
the viewer come first, and paging keeps the filters."""
from datetime import timedelta

from django.test import override_settings
from django.urls import reverse

from academics.models import LessonPlan, LessonPlanStatus
from academics.test_lesson_plan_attachment import MEDIA, LessonPlanFixture
from academics.tests import assign_role_group
from users.models import User, UserRole


@override_settings(MEDIA_ROOT=MEDIA)
class LessonPlanListTests(LessonPlanFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.hod = User.objects.create_user(username="hod.lp", email="hodlp@example.test", password="x",
                                           role=UserRole.PRIMARY_HOD, first_name="Esther", last_name="Agango")
        assign_role_group(cls.hod)
        cls.other = User.objects.create_user(username="t.maths", email="tm@example.test", password="x",
                                             role=UserRole.TEACHER, first_name="Joshua", last_name="Muzze")

    def plan(self, teacher=None, status=LessonPlanStatus.DRAFT, title="Fractions", weeks=0, subject="English"):
        p = LessonPlan.objects.create(
            teacher=teacher or self.teacher, term=self.term, class_name="Grade 4", subject_name=subject,
            week_start_date=self.next_monday + timedelta(weeks=weeks), day_of_week="mon", lesson_title=title,
        )
        LessonPlan.objects.filter(pk=p.pk).update(status=status, reviewed_by=self.hod if status in (
            LessonPlanStatus.APPROVED, LessonPlanStatus.REVISION_REQUESTED) else None)
        return p

    def titles(self, resp):
        return [p.lesson_title for p in resp.context["mine"]]

    def test_search_matches_title_subject_class_and_teacher(self):
        self.plan(title="Poetry: rhythm")
        self.plan(title="Long division", subject="Mathematics")
        url = reverse("academics:lesson_plans")
        self.assertEqual(self.titles(self.client.get(url, {"q": "poetry"})), ["Poetry: rhythm"])
        self.assertEqual(self.titles(self.client.get(url, {"q": "mathematics"})), ["Long division"])

    def test_teacher_sees_plans_needing_their_action_first(self):
        self.plan(title="Approved one", status=LessonPlanStatus.APPROVED, weeks=2)
        self.plan(title="Returned one", status=LessonPlanStatus.REVISION_REQUESTED, weeks=-1)
        resp = self.client.get(reverse("academics:lesson_plans"))
        self.assertEqual(self.titles(resp)[0], "Returned one")
        self.assertContains(resp, "Returned one")
        self.assertNotContains(resp, "ID: #")

    def test_hod_sees_submitted_first_and_teacher_filter_works(self):
        self.plan(title="Approved", status=LessonPlanStatus.APPROVED, weeks=3)
        self.plan(title="Waiting", status=LessonPlanStatus.SUBMITTED, weeks=-1)
        self.plan(teacher=self.other, title="Other teacher", status=LessonPlanStatus.SUBMITTED)
        self.client.force_login(self.hod)
        url = reverse("academics:lesson_plans")
        titles = self.titles(self.client.get(url))
        self.assertEqual(set(titles[:2]), {"Waiting", "Other teacher"})
        self.assertEqual(self.titles(self.client.get(url, {"teacher": self.other.pk})), ["Other teacher"])

    def test_paging_keeps_the_filters(self):
        for i in range(21):
            self.plan(title=f"Plan {i}", weeks=i)
        resp = self.client.get(reverse("academics:lesson_plans"), {"q": "plan", "status": "draft"})
        self.assertContains(resp, "q=plan&amp;status=draft&amp;mine_page=2")
