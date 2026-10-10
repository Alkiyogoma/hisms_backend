"""
A lesson plan cannot be submitted without its file. Regression for: a teacher
could submit a plan with nothing attached, so it appeared as submitted with
nothing to review. Enforced on every route that submits, not just on screen.
"""
import shutil
import tempfile
from datetime import date, time, timedelta

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import (
    AcademicYear, Department, GradeClass, LessonPlan, LessonPlanAttachment, LessonPlanStatus, Term,
)
from academics.tests import assign_role_group
from hr.models import StaffProfile, TeacherClassAssignment
from timetable.models import TimetableSlot, Weekday
from users.models import User, UserRole

MEDIA = tempfile.mkdtemp()


def pdf(name="plan.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 lesson plan", content_type="application/pdf")


@override_settings(MEDIA_ROOT=MEDIA)
class LessonPlanFixture(TestCase):
    """A Primary teacher with an English slot in Grade 4, in the current term."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        today = date.today()
        year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term = Term.objects.create(
            academic_year=year, name="Term 1",
            start_date=today - timedelta(days=30), end_date=today + timedelta(days=60),
        )
        gc = GradeClass.objects.create(name="Grade 4", department=Department.PRIMARY)
        cls.teacher = User.objects.create_user(
            username="t.lp", email="tlp@example.test", password="x", role=UserRole.TEACHER,
            first_name="Faith", last_name="Mmuya",
        )
        assign_role_group(cls.teacher)
        staff = StaffProfile.objects.create(
            user=cls.teacher, employment_start_date=date(2024, 1, 1),
            full_name="Faith Mmuya", department="PRIMARY", job_title="Teacher",
        )
        TeacherClassAssignment.objects.create(
            teacher=staff, term=cls.term, grade_class=gc, subjects_taught=["English"],
        )
        cls.slot = TimetableSlot.objects.create(
            term=cls.term, class_name="Grade 4", subject_name="English", teacher=cls.teacher,
            day_of_week=Weekday.MON, start_time=time(8, 0), end_time=time(9, 0), room=None,
        )
        cls.next_monday = today - timedelta(days=today.weekday()) + timedelta(weeks=1)

    def setUp(self):
        self.client.force_login(self.teacher)

    def form_data(self, action="submit", **kw):
        data = {
            "class_name": "Grade 4", "subject_name": "English",
            "week_start_date": self.next_monday.isoformat(), "day_of_week": "mon",
            "lesson_title": "Fractions", "action": action,
        }
        data.update(kw)
        return data

    def draft(self, **kw):
        return LessonPlan.objects.create(
            teacher=self.teacher, term=self.term, class_name="Grade 4", subject_name="English",
            week_start_date=self.next_monday, day_of_week="mon", lesson_title="Fractions", **kw,
        )


@override_settings(MEDIA_ROOT=MEDIA)
class LessonPlanFileRequiredTests(LessonPlanFixture):
    # ── main form ────────────────────────────────────────────────────
    def test_form_states_the_rules_and_starts_with_submit_disabled(self):
        resp = self.client.get(reverse("academics:new_lesson_plan"))
        self.assertContains(resp, "Lesson plan file")
        self.assertContains(resp, "Accepted: ")
        self.assertContains(resp, "MB each")
        self.assertContains(resp, 'accept=".')
        self.assertContains(resp, "function syncSubmitButton")

    def test_submit_without_a_file_is_refused(self):
        resp = self.client.post(reverse("academics:new_lesson_plan"), self.form_data())
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(LessonPlan.objects.exists())
        self.assertContains(resp, "Attach the lesson plan file before submitting")

    def test_a_rejected_file_does_not_count(self):
        bad = SimpleUploadedFile("plan.exe", b"MZ", content_type="application/octet-stream")
        resp = self.client.post(reverse("academics:new_lesson_plan"), self.form_data(attachments=[bad]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(LessonPlan.objects.exists())
        self.assertFalse(LessonPlanAttachment.objects.exists())

    def test_submit_with_a_file(self):
        resp = self.client.post(reverse("academics:new_lesson_plan"), self.form_data(attachments=[pdf()]))
        self.assertEqual(resp.status_code, 302)
        plan = LessonPlan.objects.get()
        self.assertEqual(plan.status, LessonPlanStatus.SUBMITTED)
        self.assertEqual(plan.attachments.count(), 1)

    def test_draft_can_be_saved_without_a_file(self):
        self.client.post(reverse("academics:new_lesson_plan"), self.form_data(action="draft"))
        self.assertEqual(LessonPlan.objects.get().status, LessonPlanStatus.DRAFT)

    def test_resubmitting_a_draft_that_already_has_its_file(self):
        plan = self.draft()
        LessonPlanAttachment.objects.create(lesson_plan=plan, file=pdf(), filename="plan.pdf", uploaded_by=self.teacher)
        resp = self.client.post(reverse("academics:lesson_plan_edit", args=[plan.pk]), self.form_data())
        self.assertEqual(resp.status_code, 302)
        plan.refresh_from_db()
        self.assertEqual(plan.status, LessonPlanStatus.SUBMITTED)

    # ── other routes ─────────────────────────────────────────────────
    def test_quick_create_from_timetable_needs_a_file(self):
        url = reverse("academics:quick_lesson_plan")
        resp = self.client.post(url, {"slot_id": self.slot.pk, "action": "submit", "week": 1})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["HX-Retarget"], "#quick-lp-error")
        self.assertContains(resp, "Attach the lesson plan file before submitting")
        self.assertFalse(LessonPlan.objects.exists())

        self.client.post(url, {"slot_id": self.slot.pk, "action": "submit", "week": 1, "attachments": [pdf()]})
        plan = LessonPlan.objects.get()
        self.assertEqual(plan.status, LessonPlanStatus.SUBMITTED)
        self.assertEqual(plan.attachments.count(), 1)

    def test_quick_create_modal_states_the_rules(self):
        resp = self.client.get(reverse("academics:quick_lesson_plan"), {"slot_id": self.slot.pk})
        self.assertContains(resp, "Accepted: ")
        self.assertContains(resp, 'id="quick-lp-error"')

    def test_edit_modal_cannot_submit_a_plan_without_a_file(self):
        plan = self.draft()
        resp = self.client.post(reverse("academics:edit_lesson_plan_modal", args=[plan.pk]),
                                {"action": "submit", "lesson_title": "Fractions"})
        self.assertContains(resp, "Attach the lesson plan file before submitting")
        plan.refresh_from_db()
        self.assertEqual(plan.status, LessonPlanStatus.DRAFT)

        self.client.post(reverse("academics:edit_lesson_plan_modal", args=[plan.pk]),
                         {"action": "submit", "lesson_title": "Fractions", "attachments": [pdf()]})
        plan.refresh_from_db()
        self.assertEqual(plan.status, LessonPlanStatus.SUBMITTED)

    def test_submit_button_route_needs_a_file(self):
        plan = self.draft()
        self.client.post(reverse("academics:submit_lesson_plan", args=[plan.pk]))
        plan.refresh_from_db()
        self.assertEqual(plan.status, LessonPlanStatus.DRAFT)

    def test_list_submit_button_disabled_without_a_file(self):
        self.draft()
        resp = self.client.get(reverse("academics:lesson_plans"))
        self.assertContains(resp, 'title="Attach the lesson plan file before submitting"')

    # ── the model itself (admin and any other code path) ─────────────
    def test_model_refuses_becoming_submitted_without_a_file(self):
        plan = self.draft()
        plan.status = LessonPlanStatus.SUBMITTED
        with self.assertRaises(ValidationError):
            plan.full_clean()

    def test_plan_submitted_earlier_without_a_file_can_still_be_edited(self):
        plan = self.draft()
        LessonPlan.objects.filter(pk=plan.pk).update(status=LessonPlanStatus.SUBMITTED)
        plan.refresh_from_db()
        plan.lesson_title = "Fractions, part 2"
        plan.full_clean()  # does not raise
