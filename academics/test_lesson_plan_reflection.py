"""After an approved lesson is taught, the plan waits on a reflection from the
teacher who taught it, and is complete only once that is written. Regression
for: an approved plan moved on with no record of how the lesson went."""
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse

from academics.models import LESSON_PLAN_APPROVED_STATUSES, LessonPlan, LessonPlanStatus
from academics.test_lesson_plan_attachment import MEDIA, LessonPlanFixture
from academics.tests import assign_role_group
from users.models import User, UserRole


@override_settings(MEDIA_ROOT=MEDIA)
class LessonPlanReflectionTests(LessonPlanFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.hod = User.objects.create_user(username="hod.refl", email="hodrefl@example.test", password="x",
                                           role=UserRole.PRIMARY_HOD, first_name="Esther", last_name="Agango")
        assign_role_group(cls.hod)
        cls.other = User.objects.create_user(username="t.other", email="tother@example.test", password="x",
                                             role=UserRole.TEACHER, first_name="Joshua", last_name="Muzze")
        assign_role_group(cls.other)
        cls.admin = User.objects.create_user(username="sa.refl", email="sarefl@example.test", password="x",
                                             role=UserRole.SUPER_ADMIN)

    def approved(self, status=LessonPlanStatus.APPROVED):
        p = self.draft()
        LessonPlan.objects.filter(pk=p.pk).update(status=status, reviewed_by=self.hod)
        p.refresh_from_db()
        return p

    def taught(self, plan, **data):
        return self.client.post(reverse("academics:mark_lesson_plan_taught", args=[plan.pk]), data)

    def reflect(self, plan, text):
        return self.client.post(reverse("academics:lesson_plan_reflection", args=[plan.pk]), {"reflection": text})

    # ── the flow ─────────────────────────────────────────────────────
    def test_taught_plan_awaits_reflection_and_is_not_complete(self):
        plan = self.approved()
        self.taught(plan)
        plan.refresh_from_db()
        self.assertEqual(plan.status, LessonPlanStatus.AWAITING_REFLECTION)
        self.assertIsNotNone(plan.taught_at)
        self.assertEqual(plan.reflection, "")

    def test_reflection_completes_the_plan(self):
        plan = self.approved()
        self.taught(plan)
        self.reflect(plan, "  Most pupils got equivalent fractions; slower group needs more visuals.  ")
        plan.refresh_from_db()
        self.assertEqual(plan.status, LessonPlanStatus.COMPLETE)
        self.assertEqual(plan.reflection, "Most pupils got equivalent fractions; slower group needs more visuals.")
        self.assertIsNotNone(plan.reflection_at)

    def test_reflection_sent_with_taught_completes_at_once(self):
        plan = self.approved()
        self.taught(plan, reflection="Went well.")
        plan.refresh_from_db()
        self.assertEqual(plan.status, LessonPlanStatus.COMPLETE)
        self.assertEqual(plan.reflection, "Went well.")

    def test_empty_reflection_does_not_complete(self):
        plan = self.approved(LessonPlanStatus.AWAITING_REFLECTION)
        self.reflect(plan, "   ")
        plan.refresh_from_db()
        self.assertEqual(plan.status, LessonPlanStatus.AWAITING_REFLECTION)

    def test_complete_requires_reflection_in_the_model(self):
        plan = self.approved()
        plan.status = LessonPlanStatus.COMPLETE
        with self.assertRaises(ValidationError):
            plan.full_clean()

    def test_only_approved_plans_can_be_marked_taught(self):
        for status in (LessonPlanStatus.DRAFT, LessonPlanStatus.SUBMITTED, LessonPlanStatus.REJECTED):
            plan = self.draft()
            LessonPlan.objects.filter(pk=plan.pk).update(status=status)
            self.taught(plan)
            plan.refresh_from_db()
            self.assertEqual(plan.status, status)

    def test_reflection_needs_the_lesson_to_be_taught_first(self):
        plan = self.approved()
        self.reflect(plan, "Too early")
        plan.refresh_from_db()
        self.assertEqual(plan.status, LessonPlanStatus.APPROVED)
        self.assertEqual(plan.reflection, "")

    # ── only the teacher who taught it ───────────────────────────────
    def test_nobody_else_can_mark_taught_or_reflect(self):
        plan = self.approved()
        waiting = self.approved(LessonPlanStatus.AWAITING_REFLECTION)
        for user in (self.hod, self.other, self.admin):
            self.client.force_login(user)
            self.assertEqual(self.taught(plan).status_code, 403, user.username)
            self.assertEqual(self.reflect(waiting, "Not mine").status_code, 403, user.username)
        plan.refresh_from_db()
        waiting.refresh_from_db()
        self.assertEqual(plan.status, LessonPlanStatus.APPROVED)
        self.assertEqual(waiting.status, LessonPlanStatus.AWAITING_REFLECTION)
        self.assertEqual(waiting.reflection, "")

    # ── what the list shows ──────────────────────────────────────────
    def test_teacher_sees_the_actions(self):
        a = self.approved()
        w = self.approved(LessonPlanStatus.AWAITING_REFLECTION)
        resp = self.client.get(reverse("academics:lesson_plans"))
        self.assertContains(resp, f"openReflectionModal({a.pk}, 'approved'")
        self.assertContains(resp, f"openReflectionModal({w.pk}, 'awaiting_reflection'")

    def test_hod_sees_which_plans_await_reflection(self):
        w = self.approved(LessonPlanStatus.AWAITING_REFLECTION)
        a = self.approved()
        self.client.force_login(self.hod)
        url = reverse("academics:lesson_plans")
        resp = self.client.get(url)
        self.assertEqual(resp.context["awaiting_reflection_count"], 1)
        self.assertNotContains(resp, f"openReflectionModal({a.pk},")
        self.assertNotContains(resp, f"openReflectionModal({w.pk},")
        resp = self.client.get(url, {"status": LessonPlanStatus.AWAITING_REFLECTION})
        self.assertEqual([p.status for p in resp.context["mine"]], [LessonPlanStatus.AWAITING_REFLECTION])
        self.assertContains(resp, "Awaiting Reflection")

    def test_taught_and_complete_plans_still_count_as_approved(self):
        for status in LESSON_PLAN_APPROVED_STATUSES:
            self.approved(status)
        resp = self.client.get(reverse("academics:lesson_plans"), {"status": "approved"})
        self.assertEqual(resp.context["approved_count"], 3)
        self.assertEqual(len(resp.context["mine"]), 3)

    def test_reflection_shows_in_the_viewer(self):
        plan = self.approved()
        self.taught(plan, reflection="Group work ran long.")
        self.client.force_login(self.hod)
        data = self.client.get(reverse("academics:lesson_plan_thread", args=[plan.pk])).json()
        self.assertEqual(data["plan"]["reflection"], "Group work ran long.")
        self.assertEqual(data["plan"]["status"], "Complete")

    def test_completed_plan_cannot_be_resubmitted(self):
        plan = self.approved()
        self.taught(plan, reflection="Fine.")
        with self.assertRaises(ValidationError):
            self.client.post(reverse("academics:submit_lesson_plan", args=[plan.pk]))
