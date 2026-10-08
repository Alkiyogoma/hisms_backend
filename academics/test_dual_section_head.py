"""A user holding both Primary and Lower Secondary head roles can review Grade 1-8 plans."""
from datetime import date, timedelta

from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import (AcademicYear, Department, GradeClass, LessonPlan,
                              LessonPlanStatus, Term)
from users.models import User, UserRole
from users.role_models import ROLE_DEFAULT_PERMISSIONS, RoleConfig


def grant(role, user):
    group, _ = Group.objects.get_or_create(name=f"role_{role}")
    group.permissions.add(*Permission.objects.filter(codename__in=ROLE_DEFAULT_PERMISSIONS.get(role, [])))
    group.user_set.add(user)


class DualSectionHeadTests(TestCase):
    def setUp(self):
        today = date.today()
        year = AcademicYear.objects.create(name=str(today.year), is_current=True)
        self.term = Term.objects.create(academic_year=year, name="T1",
                                        start_date=today - timedelta(days=30), end_date=today + timedelta(days=30))
        GradeClass.objects.create(name="Grade 1", department=Department.PRIMARY)
        GradeClass.objects.create(name="Grade 8", department=Department.LOWER_SECONDARY)
        self.teacher = User.objects.create_user("tch", "tch@x.edu", "pw", role=UserRole.TEACHER)

        self.esther = User.objects.create_user("esther", "e@x.edu", "pw", role=UserRole.PRIMARY_HOD)
        grant(UserRole.PRIMARY_HOD, self.esther)
        rc = RoleConfig.objects.create(role=UserRole.LOWER_SECONDARY_HOD, label="Head of Lower Secondary")
        self.esther.extra_roles.add(rc)
        grant(UserRole.LOWER_SECONDARY_HOD, self.esther)

        self.primary_only = User.objects.create_user("po", "po@x.edu", "pw", role=UserRole.PRIMARY_HOD)
        grant(UserRole.PRIMARY_HOD, self.primary_only)

    def plan(self, class_name):
        return LessonPlan.objects.create(
            teacher=self.teacher, term=self.term, class_name=class_name, subject_name="Math",
            week_start_date=date.today(), lesson_title="L", objectives="o", activities="a",
            assessment_strategy="s", resources="r", status=LessonPlanStatus.SUBMITTED,
            submitted_at=timezone.now())

    def approve(self, user, plan):
        self.client.force_login(user)
        return self.client.post(reverse("academics:review_lesson_plan", args=[plan.pk]),
                                {"decision": LessonPlanStatus.APPROVED})

    def test_dual_head_approves_primary_and_lower_secondary(self):
        for cls in ("Grade 1", "Grade 8"):
            plan = self.plan(cls)
            resp = self.approve(self.esther, plan)
            self.assertEqual(resp.status_code, 302, cls)
            plan.refresh_from_db()
            self.assertEqual(plan.status, LessonPlanStatus.APPROVED, cls)

    def test_single_section_head_still_blocked_from_other_section(self):
        self.assertEqual(self.approve(self.primary_only, self.plan("Grade 8")).status_code, 403)
        self.assertEqual(self.approve(self.primary_only, self.plan("Grade 1")).status_code, 302)

    def test_held_roles(self):
        self.assertEqual(self.esther.held_roles, {UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD})
        self.assertEqual(self.esther.section_departments, ["PRIMARY", "LOWER_SECONDARY"])
        self.assertEqual(self.primary_only.section_departments, ["PRIMARY"])

    def test_role_config_departments_override(self):
        RoleConfig.objects.filter(role=UserRole.LOWER_SECONDARY_HOD).update(departments=["LOWER_SECONDARY", "PRIMARY"])
        fresh = User.objects.get(pk=self.esther.pk)
        self.assertEqual(sorted(fresh.section_departments), ["LOWER_SECONDARY", "PRIMARY"])

    def test_hod_dashboard_counts_both_sections(self):
        self.plan("Grade 1"); self.plan("Grade 8")
        self.client.force_login(self.esther)
        resp = self.client.get("/")
        self.assertEqual(resp.context["plans_to_review_count"], 2)
        self.client.force_login(self.primary_only)
        self.assertEqual(self.client.get("/").context["plans_to_review_count"], 1)

    def test_welfare_visibility_spans_both_sections(self):
        from students.models import Student
        from welfare.models import WelfareObservation, WelfareNoteStatus, WelfareSeverity
        from welfare.visibility import visible_observations
        from django.contrib.auth.models import Permission
        self.esther.user_permissions.add(Permission.objects.get(codename="view_welfareobservation"))
        for cls in ("Grade 1", "Grade 8"):
            s = Student.objects.create(admission_no=cls, first_name="K", last_name=cls, class_name=cls)
            WelfareObservation.all_objects.create(
                student=s, submitted_by=self.teacher, note_type="concern", concern_type="behavioral",
                severity=WelfareSeverity.LOW, observation_date=date.today(), observation_text="x",
                action_taken="y", status=WelfareNoteStatus.SUBMITTED, submitted_at=timezone.now())
        fresh = User.objects.get(pk=self.esther.pk)
        seen = visible_observations(fresh, WelfareObservation.all_objects.all())
        self.assertEqual(sorted(seen.values_list("student__class_name", flat=True)), ["Grade 1", "Grade 8"])


class AssignSecondSectionRoleTests(TestCase):
    """Giving a Head of Primary the Head of Lower Secondary role through the
    user's Roles panel, exactly as an administrator does it."""

    def setUp(self):
        import json  # noqa: F401
        from io import StringIO
        from django.core.management import call_command
        call_command("seed_roles", stdout=StringIO())
        today = date.today()
        year = AcademicYear.objects.create(name=str(today.year), is_current=True)
        self.term = Term.objects.create(academic_year=year, name="T1",
                                        start_date=today - timedelta(days=30), end_date=today + timedelta(days=30))
        GradeClass.objects.create(name="Grade 1", department=Department.PRIMARY)
        GradeClass.objects.create(name="Grade 8", department=Department.LOWER_SECONDARY)
        self.teacher = User.objects.create_user("tch", "tch@x.edu", "pw", role=UserRole.TEACHER)
        self.admin = User.objects.create_user("sa", "sa@x.edu", "pw", role=UserRole.SUPER_ADMIN, is_superuser=True)
        self.esther = User.objects.create_user("esther", "e@x.edu", "pw", role=UserRole.PRIMARY_HOD)
        grant(UserRole.PRIMARY_HOD, self.esther)

    def _set_extra_roles(self, *roles):
        import json
        ids = list(RoleConfig.objects.filter(role__in=roles).values_list("pk", flat=True))
        self.client.force_login(self.admin)
        resp = self.client.post(f"/accounts/users/{self.esther.pk}/extra-roles/",
                                data=json.dumps({"role_ids": ids}), content_type="application/json")
        self.assertEqual(resp.status_code, 200, resp.content)
        return User.objects.get(pk=self.esther.pk)

    def test_assigning_second_role_keeps_main_role_and_approves_grades_1_to_8(self):
        esther = self._set_extra_roles(UserRole.LOWER_SECONDARY_HOD)
        self.assertEqual(sorted(esther.groups.values_list("name", flat=True)),
                         ["role_lower_secondary_hod", "role_primary_hod"])
        self.assertEqual(esther.section_departments, ["PRIMARY", "LOWER_SECONDARY"])
        self.client.force_login(esther)
        for cls in ("Grade 1", "Grade 8"):
            plan = LessonPlan.objects.create(
                teacher=self.teacher, term=self.term, class_name=cls, subject_name="Math",
                week_start_date=date.today(), lesson_title="L", objectives="o", activities="a",
                assessment_strategy="s", resources="r", status=LessonPlanStatus.SUBMITTED,
                submitted_at=timezone.now())
            resp = self.client.post(reverse("academics:review_lesson_plan", args=[plan.pk]),
                                    {"decision": LessonPlanStatus.APPROVED})
            self.assertEqual(resp.status_code, 302, cls)
            plan.refresh_from_db()
            self.assertEqual(plan.status, LessonPlanStatus.APPROVED, cls)

    def test_removing_the_extra_role_keeps_main_role(self):
        self._set_extra_roles(UserRole.LOWER_SECONDARY_HOD)
        esther = self._set_extra_roles()
        self.assertEqual(list(esther.groups.values_list("name", flat=True)), ["role_primary_hod"])
        self.assertTrue(esther.has_perm("academics.can_review_lessonplan"))
        self.assertEqual(esther.section_departments, ["PRIMARY"])

    def test_analytics_scope_is_union_of_roles(self):
        from academics.views.reports import _analytics_scope
        self.assertEqual(_analytics_scope(self.esther), (UserRole.PRIMARY_HOD, ["PRIMARY"]))
        esther = self._set_extra_roles(UserRole.LOWER_SECONDARY_HOD)
        self.assertEqual(_analytics_scope(esther), (UserRole.HEAD_OF_SCHOOL, None))
        self.client.force_login(esther)
        scope = self.client.get(reverse("academics:performance_report")).context["scope"]
        self.assertEqual(scope.kind, "section")
        self.assertIn("Primary", scope.label)
        self.assertIn("Lower Secondary", scope.label)

    def test_hod_user_list_spans_both_sections(self):
        from hr.models import StaffProfile
        for name, dept in (("p", "PRIMARY"), ("l", "LOWER_SECONDARY"), ("e", "ECD")):
            u = User.objects.create_user(f"staff_{name}", f"staff.{name}@x.edu", "pw", role=UserRole.TEACHER)
            StaffProfile.objects.create(user=u, employment_start_date=date(2024, 1, 1), full_name=name,
                                        department=dept, job_title="Teacher")
        esther = self._set_extra_roles(UserRole.LOWER_SECONDARY_HOD)
        from users.views import UserListView
        from django.test import RequestFactory
        req = RequestFactory().get("/accounts/list/")
        req.user = esther
        view = UserListView()
        view.request, view.kwargs, view.args = req, {}, ()
        names = set(view.get_queryset().values_list("username", flat=True))
        self.assertTrue({"staff_p", "staff_l"} <= names)
        self.assertNotIn("staff_e", names)
