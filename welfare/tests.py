from datetime import date, timedelta

from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import AcademicYear, Department, GradeClass, Term
from students.models import Student
from users.models import User, UserRole

from .models import (
    WelfareNoteStatus, WelfareNoteType, WelfareObservation, WelfareSeverity,
    type_tag_mismatch,
)
from .visibility import visible_observations

# Same SQLite FK-check workaround as academics/tests.py (see the comment there).
from django.conf import settings as django_settings
if not getattr(django_settings, "AUDIT_LOG_DB_CONSTRAINT", True):
    _orig_fixture_teardown = TestCase._fixture_teardown

    def _patched_fixture_teardown(self):
        try:
            _orig_fixture_teardown(self)
        except Exception:
            pass

    TestCase._fixture_teardown = _patched_fixture_teardown


def make_user(username, role):
    from users.role_models import ROLE_DEFAULT_PERMISSIONS
    user = User.objects.create_user(
        username=username, email=f"{username}@school.test", password="pw-12345!", role=role,
        first_name=username.title(), last_name="Test",
    )
    group, _ = Group.objects.get_or_create(name=f"role_{role}")
    group.permissions.add(*Permission.objects.filter(codename__in=ROLE_DEFAULT_PERMISSIONS.get(role, [])))
    group.user_set.add(user)
    return user


class WelfareTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = date.today()
        year = AcademicYear.objects.create(name=str(today.year), is_current=True)
        cls.term = Term.objects.create(
            academic_year=year, name="Term 1",
            start_date=today - timedelta(days=30), end_date=today + timedelta(days=30),
        )
        cls.grade3 = GradeClass.objects.create(name="Grade 3", department=Department.PRIMARY)
        cls.grade8 = GradeClass.objects.create(name="Grade 8", department=Department.LOWER_SECONDARY)
        cls.pupil3 = Student.objects.create(admission_no="A3", first_name="Asha", last_name="Three", class_name="Grade 3")
        cls.pupil8 = Student.objects.create(admission_no="A8", first_name="Baraka", last_name="Eight", class_name="Grade 8")

        cls.admin = make_user("super", UserRole.SUPER_ADMIN)
        cls.hos = make_user("hos", UserRole.HEAD_OF_SCHOOL)
        cls.primary_hod = make_user("phod", UserRole.PRIMARY_HOD)
        cls.admin_officer = make_user("office", UserRole.ADMIN_OFFICER)
        cls.class_teacher8 = cls._teacher("ct8", cls.grade8, class_teacher=True, dept="LOWER_SECONDARY")
        cls.subject_teacher8 = cls._teacher("st8", cls.grade8, class_teacher=False, dept="LOWER_SECONDARY")
        cls.class_teacher3 = cls._teacher("ct3", cls.grade3, class_teacher=True, dept="PRIMARY")

    @classmethod
    def _teacher(cls, username, grade_class, class_teacher, dept):
        from hr.models import StaffProfile, TeacherClassAssignment
        user = make_user(username, UserRole.TEACHER)
        staff = StaffProfile.objects.create(
            user=user, employment_start_date=date(2024, 1, 1), full_name=username,
            department=dept, job_title="Teacher",
        )
        TeacherClassAssignment.objects.create(
            teacher=staff, term=cls.term, grade_class=grade_class,
            is_class_teacher=class_teacher, subjects_taught=["English"],
        )
        return user

    def note(self, student, author, note_type, **extra):
        values = dict(
            student=student, submitted_by=author, note_type=note_type,
            concern_type=extra.pop("concern_type", "behavioral"),
            severity=extra.pop("severity", WelfareSeverity.LOW),
            observation_date=date.today(), observation_text=f"{note_type} text",
            action_taken="Spoke to the child", parent_contacted=True,
            status=WelfareNoteStatus.SUBMITTED, submitted_at=timezone.now(),
        )
        values.update(extra)
        return WelfareObservation.all_objects.create(**values)

    def form(self, **overrides):
        data = {
            "note_type": "observation", "students": [self.pupil8.pk],
            "observation_date": date.today().isoformat(),
            "observation_text": "Quiet at break.", "action_taken": "Checked in with her.",
            "parent_contacted": "on", "action": "submit",
        }
        data.update(overrides)
        return data


class Grade8SearchTests(WelfareTestBase):
    """Grade 7-9 (Lower Secondary) learners were missing from the child search."""

    def test_grade8_learner_listed_for_super_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("welfare:submit"))
        ids = [s["id"] for s in resp.context["students_data"]]
        self.assertIn(self.pupil8.pk, ids)
        self.assertIn(self.pupil3.pk, ids)

    def test_grade8_learner_listed_for_grade8_teacher(self):
        self.client.force_login(self.class_teacher8)
        resp = self.client.get(reverse("welfare:submit"))
        ids = [s["id"] for s in resp.context["students_data"]]
        self.assertEqual(ids, [self.pupil8.pk])


class NoteTypeTests(WelfareTestBase):
    def test_no_note_type_preselected(self):
        self.client.force_login(self.class_teacher8)
        resp = self.client.get(reverse("welfare:submit"))
        self.assertEqual(resp.context["note_initial"]["note_type"], "")
        self.assertNotContains(resp, 'class="type-card active"')

    def test_submit_without_type_is_rejected(self):
        self.client.force_login(self.class_teacher8)
        resp = self.client.post(reverse("welfare:submit"), self.form(note_type=""))
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "Choose what kind of note this is", status_code=400)
        self.assertFalse(WelfareObservation.all_objects.exists())

    def test_positive_with_health_tag_needs_confirmation(self):
        self.client.force_login(self.class_teacher8)
        resp = self.client.post(reverse("welfare:submit"), self.form(note_type="positive", tags=["Health"]))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("marked Positive but tagged Health", resp.context["mismatch_warning"])
        self.assertFalse(WelfareObservation.all_objects.exists())

        resp = self.client.post(
            reverse("welfare:submit"),
            self.form(note_type="positive", tags=["Health"], confirm_mismatch="1"),
        )
        obs = WelfareObservation.objects.get()
        self.assertRedirects(resp, reverse("welfare:detail", args=[obs.pk]), fetch_redirect_response=False)
        self.assertEqual((obs.note_type, obs.tags, obs.concern_type), ("positive", ["Health"], "health"))

    def test_type_tag_mismatch_rules(self):
        self.assertTrue(type_tag_mismatch("positive", ["Behaviour"]))
        self.assertTrue(type_tag_mismatch("concern", ["Kindness"]))
        self.assertFalse(type_tag_mismatch("positive", ["Kindness", "Friendship"]))
        self.assertFalse(type_tag_mismatch("concern", ["Health"]))


class DraftTests(WelfareTestBase):
    def test_draft_is_private_until_submitted(self):
        self.client.force_login(self.class_teacher8)
        resp = self.client.post(reverse("welfare:submit"), self.form(action="draft", note_type=""))
        draft = WelfareObservation.all_objects.get()
        self.assertRedirects(resp, reverse("welfare:edit", args=[draft.pk]), fetch_redirect_response=False)
        self.assertTrue(draft.is_draft)
        self.assertFalse(WelfareObservation.objects.exists())

        # Nobody else can open it; the HOS doesn't see it in the queue.
        self.client.force_login(self.hos)
        self.assertEqual(self.client.get(reverse("welfare:edit", args=[draft.pk])).status_code, 403)

        # Author finishes and submits it.
        self.client.force_login(self.class_teacher8)
        self.client.post(reverse("welfare:edit", args=[draft.pk]), self.form(note_type="concern", severity="medium"))
        draft.refresh_from_db()
        self.assertEqual(draft.status, WelfareNoteStatus.SUBMITTED)
        self.assertIsNotNone(draft.submitted_at)
        self.assertEqual(draft.note_type, "concern")

    def test_safeguarding_cannot_be_drafted(self):
        self.client.force_login(self.class_teacher8)
        resp = self.client.post(reverse("welfare:submit"), self.form(action="draft", note_type="safeguarding"))
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(WelfareObservation.all_objects.exists())


class CorrectionTests(WelfareTestBase):
    def correct(self, obs, **overrides):
        data = self.form(
            note_type=obs.note_type, students=[obs.student_id],
            observation_text=obs.observation_text, action_taken=obs.action_taken,
            edit_reason="Typo", action="submit",
        )
        data.update(overrides)
        return self.client.post(reverse("welfare:edit", args=[obs.pk]), data)

    def test_author_can_correct_within_window_and_original_is_kept(self):
        obs = self.note(self.pupil8, self.class_teacher8, "observation", observation_text="Original wording")
        self.client.force_login(self.class_teacher8)
        self.correct(obs, observation_text="Corrected wording", note_type="concern", severity="medium")
        obs.refresh_from_db()
        self.assertEqual(obs.observation_text, "Corrected wording")
        self.assertEqual(obs.last_edited_by, self.class_teacher8)
        rev = obs.revisions.get()
        self.assertEqual(rev.edited_by, self.class_teacher8)
        self.assertEqual(rev.reason, "Typo")
        self.assertEqual(rev.changes["What happened"], {"from": "Original wording", "to": "Corrected wording"})
        self.assertEqual(rev.changes["Note type"], {"from": "Observation", "to": "Concern"})

    def test_correction_requires_reason(self):
        obs = self.note(self.pupil8, self.class_teacher8, "observation")
        self.client.force_login(self.class_teacher8)
        resp = self.correct(obs, edit_reason="", observation_text="changed")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(obs.revisions.exists())

    def test_author_blocked_after_window_but_super_admin_can_correct(self):
        old = timezone.now() - timedelta(hours=30)
        obs = self.note(self.pupil8, self.class_teacher8, "positive", submitted_at=old)
        self.client.force_login(self.class_teacher8)
        self.correct(obs, observation_text="too late")
        obs.refresh_from_db()
        self.assertEqual(obs.observation_text, "positive text")

        self.client.force_login(self.admin)
        self.correct(obs, note_type="observation", tags=["Health"], edit_reason="Wrong type")
        obs.refresh_from_db()
        self.assertEqual(obs.note_type, "observation")
        self.assertEqual(obs.revisions.get().edited_by, self.admin)

    def test_other_teacher_cannot_correct(self):
        obs = self.note(self.pupil8, self.class_teacher8, "observation")
        self.client.force_login(self.subject_teacher8)
        self.correct(obs, observation_text="hijack")
        obs.refresh_from_db()
        self.assertEqual(obs.observation_text, "observation text")

    def test_safeguarding_locked_follow_up_added_instead(self):
        obs = self.note(self.pupil8, self.class_teacher8, "safeguarding", severity="critical", is_locked=True)
        for user in (self.class_teacher8, self.admin):
            self.assertTrue(obs.edit_block_reason(user))

        # Author gets a content-free page, can add a follow-up.
        self.client.force_login(self.class_teacher8)
        resp = self.client.get(reverse("welfare:detail", args=[obs.pk]))
        self.assertTemplateUsed(resp, "welfare/safeguarding_sent.html")
        self.assertNotContains(resp, "safeguarding text")
        self.client.post(reverse("welfare:follow_up", args=[obs.pk]), {"follow_up_text": "Correction: it was Tuesday"})
        follow_up = WelfareObservation.all_objects.get(follow_up_of=obs)
        self.assertEqual(follow_up.note_type, "safeguarding")
        self.assertFalse(WelfareObservation.objects.filter(pk=follow_up.pk).exists())

        # HOS sees the original and the follow-up.
        self.client.force_login(self.hos)
        resp = self.client.get(reverse("welfare:detail", args=[obs.pk]))
        self.assertContains(resp, "safeguarding text")
        self.assertContains(resp, "Correction: it was Tuesday")


class VisibilityTests(WelfareTestBase):
    def setUp(self):
        self.positive = self.note(self.pupil8, self.class_teacher8, "positive", concern_type="other")
        self.health = self.note(self.pupil8, self.class_teacher8, "observation", concern_type="health", tags=["Health"])
        self.concern = self.note(self.pupil8, self.class_teacher8, "concern", severity="medium")
        self.safeguarding = self.note(self.pupil8, self.class_teacher8, "safeguarding", severity="critical")

    def visible(self, user):
        return set(visible_observations(user, WelfareObservation.objects.all()))

    def test_class_teacher_sees_concerns_but_not_safeguarding(self):
        self.assertEqual(self.visible(self.class_teacher8), {self.positive, self.health, self.concern})

    def test_subject_teacher_sees_everyday_notes_only(self):
        self.assertEqual(self.visible(self.subject_teacher8), {self.positive})

    def test_other_class_teacher_sees_nothing(self):
        self.assertEqual(self.visible(self.class_teacher3), set())

    def test_hod_of_other_department_sees_nothing(self):
        self.assertEqual(self.visible(self.primary_hod), set())

    def test_admin_officer_never_sees_sensitive_notes(self):
        self.assertTrue(self.visible(self.admin_officer) <= {self.positive})

    def test_hos_sees_safeguarding(self):
        self.assertIn(self.safeguarding, self.visible(self.hos))

    def test_profile_hides_safeguarding_and_sensitive_notes(self):
        url = reverse("students:detail", args=[self.pupil8.pk])
        self.client.force_login(self.hos)
        resp = self.client.get(url)
        self.assertContains(resp, ">Welfare<")
        self.assertNotIn(self.safeguarding, resp.context["welfare_incidents"])
        self.assertIn(self.concern, resp.context["welfare_incidents"])

        self.client.force_login(self.subject_teacher8)
        resp = self.client.get(url)
        self.assertEqual(list(resp.context["welfare_incidents"]), [self.positive])
        self.assertNotContains(resp, "Welfare &amp; Behavior Monitoring")
        self.assertNotContains(resp, "Report Incident")


class PageSmokeTests(WelfareTestBase):
    def test_welfare_pages_render_for_each_role(self):
        self.note(self.pupil8, self.class_teacher8, "concern", severity="high")
        self.note(self.pupil8, self.class_teacher8, "safeguarding", severity="critical")
        pages = ["welfare:list", "welfare:student_incidents", "welfare:hod_dashboard", "welfare:submit"]
        for user in (self.admin, self.hos, self.primary_hod, self.class_teacher8):
            self.client.force_login(user)
            for name in pages:
                resp = self.client.get(reverse(name))
                self.assertIn(resp.status_code, (200, 403), f"{name} as {user.role}")

    def test_hod_dashboard_excludes_safeguarding_for_hod(self):
        sg = self.note(self.pupil3, self.class_teacher3, "safeguarding", severity="critical")
        self.client.force_login(self.primary_hod)
        resp = self.client.get(reverse("welfare:hod_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(sg, list(resp.context["open_entries"]))


class SafeguardingPermissionTests(WelfareTestBase):
    """Only users holding "Can view safeguarding notes" see safeguarding notes —
    no role gets them automatically."""

    def setUp(self):
        self.sg = self.note(self.pupil8, self.class_teacher8, "safeguarding", severity="critical", is_locked=True)
        self.perm = Permission.objects.get(codename="view_safeguarding_note")

    def test_head_of_school_without_permission_cannot_see(self):
        Group.objects.get(name=f"role_{UserRole.HEAD_OF_SCHOOL}").permissions.remove(self.perm)
        hos = User.objects.get(pk=self.hos.pk)
        self.assertNotIn(self.sg, visible_observations(hos, WelfareObservation.objects.all()))
        self.client.force_login(hos)
        self.assertEqual(self.client.get(reverse("welfare:detail", args=[self.sg.pk])).status_code, 403)

    def test_any_user_granted_permission_sees_and_signs_off(self):
        lead = self.subject_teacher8
        lead.user_permissions.add(self.perm)
        lead = User.objects.get(pk=lead.pk)
        self.client.force_login(lead)
        overview = self.client.get(reverse("welfare:student_incidents") + "?note_type=safeguarding")
        self.assertIn(self.sg, list(overview.context["recent_incidents"]))
        self.assertContains(overview, ">Safeguarding</a>")
        self.assertContains(self.client.get(reverse("welfare:detail", args=[self.sg.pk])), "safeguarding text")
        self.client.post(reverse("welfare:acknowledge", args=[self.sg.pk]))
        self.sg.refresh_from_db()
        self.assertTrue(self.sg.is_safeguarding_acknowledged())

    def test_safeguarding_tab_hidden_without_permission(self):
        self.client.force_login(self.class_teacher8)
        resp = self.client.get(reverse("welfare:list"))
        self.assertNotContains(resp, "?note_type=safeguarding")


class SafeguardingGrantMigrationTests(TestCase):
    def test_migration_grants_head_of_school_without_removing_anything(self):
        import importlib
        from django.apps import apps as global_apps
        mig = importlib.import_module("welfare.migrations.0015_grant_safeguarding_to_head_of_school")
        group = Group.objects.create(name="role_head_of_school")
        keep = Permission.objects.get(codename="add_student", content_type__app_label="students")
        group.permissions.add(keep)
        mig.grant(global_apps, None)
        codenames = set(group.permissions.values_list("codename", flat=True))
        self.assertEqual(codenames, {"add_student", "view_safeguarding_note"})
