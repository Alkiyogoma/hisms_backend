"""
ECD report approval and publishing.

The class teacher submits each report, the ECD HOD approves (one at a time or
a ticked selection), then the Head of School publishes, one learner, a
selection or a whole class. Publishing tells the learner's parents and links
to the report in the parent portal. The ECD HOD may also open ECD Report Setup.
"""
from unittest import mock

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.urls import reverse

from academics.models import Department, GradeClass, ReportCard, ReportCardStatus
from academics.services import sign_off_report
from academics.test_ecd_reports import COMMENT, ECDFixture
from academics.tests import assign_role_group
from students.models import ParentGuardian, Student, StudentGuardian, StudentStatus
from users.models import User, UserRole

QUEUE = "academics:report_review_queue"


class ECDApprovalTests(ECDFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ecd_hod = User.objects.create_user(username="ecdhod", email="ecdhod@example.test", password="x",
                                               role=UserRole.ECD_HOD)
        assign_role_group(cls.ecd_hod)
        cls.primary_hod = User.objects.create_user(username="prihod", email="prihod@example.test", password="x",
                                                   role=UserRole.PRIMARY_HOD)
        assign_role_group(cls.primary_hod)
        GradeClass.objects.create(name="Grade 1", department=Department.PRIMARY)
        cls.learners = [cls.learner] + [
            Student.objects.create(admission_no=f"E-10{n}", first_name=f"Learner{n}", last_name="Test",
                                   class_name="Pre-School", academic_year=cls.year, status=StudentStatus.ACTIVE)
            for n in (1, 2)
        ]

    def submit(self, student):
        """Rate every line, add the comment and submit, as the Head of School."""
        self.client.force_login(self.hos)
        self.save(student, {"values": self.rate_everything(self.structure()), "teacher_comments": COMMENT})
        resp = self.client.post(self.api("api_ecd_submit", student))
        self.assertEqual(resp.status_code, 200, resp.content)
        return ReportCard.objects.get(student=student, term=self.term)

    def queue(self, user, **data):
        self.client.force_login(user)
        return self.client.post(reverse(QUEUE), data, follow=True)

    def test_hod_approves_a_ticked_selection(self):
        a, b, c = (self.submit(s) for s in self.learners)
        self.queue(self.ecd_hod, action="ecd_hod_approve", report_ids=[a.pk, b.pk])
        for report, approved in ((a, True), (b, True), (c, False)):
            report.refresh_from_db()
            self.assertEqual(bool(report.ecd_hod_approved_by_id), approved)
        self.assertEqual(a.ecd_hod_approved_by, self.ecd_hod)
        self.assertEqual(a.status, ReportCardStatus.PENDING_SIGN_OFF)  # not published by the HOD

    def test_unsubmitted_reports_are_skipped_with_a_reason(self):
        done = self.submit(self.learners[0])
        draft = ReportCard.objects.create(student=self.learners[1], term=self.term, is_ecd_report=True,
                                          ecd_template_type="pre_school", status=ReportCardStatus.DRAFT,
                                          generated_by=self.hos)
        page = self.queue(self.ecd_hod, action="ecd_hod_approve", report_ids=[done.pk, draft.pk])
        draft.refresh_from_db()
        self.assertIsNone(draft.ecd_hod_approved_by_id)
        self.assertContains(page, "not been submitted")
        done.refresh_from_db()
        self.assertTrue(done.ecd_hod_approved_by_id)

    def test_other_section_heads_cannot_approve_ecd(self):
        report = self.submit(self.learner)
        page = self.queue(self.primary_hod, action="ecd_hod_approve", report_ids=[report.pk])
        report.refresh_from_db()
        self.assertIsNone(report.ecd_hod_approved_by_id)
        self.assertContains(page, "outside your section")

    def test_hos_cannot_publish_before_hod_approval(self):
        report = self.submit(self.learner)
        with self.assertRaisesMessage(ValidationError, "waiting for ECD HOD approval"):
            sign_off_report(report, self.hos)
        self.queue(self.hos, action="approve", report_ids=[report.pk])
        report.refresh_from_db()
        self.assertEqual(report.status, ReportCardStatus.PENDING_SIGN_OFF)

    def test_hos_publishes_a_selection_and_parents_are_told(self):
        a, b, c = (self.submit(s) for s in self.learners)
        self.queue(self.ecd_hod, action="ecd_hod_approve", report_ids=[a.pk, b.pk, c.pk])
        # No parent marked primary: every linked parent still hears.
        parent = ParentGuardian.objects.create(full_name="Mama Baraka", phone="+255700000001")
        StudentGuardian.objects.create(student=self.learner, guardian=parent, is_primary=False)
        with mock.patch("communications.email_service.send_parent_notification") as notify:
            self.queue(self.hos, action="approve", report_ids=[a.pk, b.pk])
        statuses = {r.pk: r.status for r in ReportCard.objects.filter(pk__in=[a.pk, b.pk, c.pk])}
        self.assertEqual(statuses, {a.pk: ReportCardStatus.PUBLISHED, b.pk: ReportCardStatus.PUBLISHED,
                                    c.pk: ReportCardStatus.PENDING_SIGN_OFF})
        notify.assert_called_once()
        kwargs = notify.call_args.kwargs
        self.assertEqual(kwargs["guardian"], parent)
        self.assertEqual(kwargs["link"], reverse("parent_portal:report_card", args=[self.learner.pk, a.pk]))

    def test_class_publish_leaves_reports_awaiting_hod(self):
        a, b, _ = (self.submit(s) for s in self.learners)
        self.queue(self.ecd_hod, action="ecd_hod_approve", report_ids=[a.pk])
        self.client.force_login(self.hos)
        page = self.client.post(reverse("academics:signoff_action"),
                                {"class_name": "Pre-School", "term_id": self.term.pk}, follow=True)
        self.assertContains(page, "not yet approved by the ECD HOD")
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual(a.status, ReportCardStatus.PUBLISHED)
        self.assertEqual(b.status, ReportCardStatus.PENDING_SIGN_OFF)

    def test_returning_a_report_clears_the_hod_approval(self):
        report = self.submit(self.learner)
        self.queue(self.ecd_hod, action="ecd_hod_approve", report_ids=[report.pk])
        self.queue(self.ecd_hod, action="reject", report_ids=[report.pk], reason="Please expand the comment.")
        report.refresh_from_db()
        self.assertEqual(report.status, ReportCardStatus.DRAFT)
        self.assertIsNone(report.ecd_hod_approved_by_id)

    def test_queue_shows_selection_and_ecd_links(self):
        self.submit(self.learner)
        self.client.force_login(self.ecd_hod)
        page = self.client.get(reverse(QUEUE))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="report_ids"')
        self.assertContains(page, "Awaiting ECD HOD")
        self.assertContains(page, reverse("academics:ecd_assessment") + "?class_name=Pre-School")
        self.assertTrue(page.context["can_ecd_approve"])


class ECDSetupAccessTests(ECDFixture):
    def test_migration_gives_the_ecd_hod_role_the_setup_permissions(self):
        group = Group.objects.get(name="role_ecd_hod")
        codenames = set(group.permissions.values_list("codename", flat=True))
        self.assertTrue({"view_ecdsection", "change_ecdsection", "change_ecditem", "change_ecdreportlayout"} <= codenames)

    def test_ecd_hod_can_open_and_edit_setup(self):
        hod = User.objects.create_user(username="ecdhod2", email="ecdhod2@example.test", password="x",
                                       role=UserRole.ECD_HOD)
        Group.objects.get(name="role_ecd_hod").user_set.add(hod)
        self.client.force_login(hod)
        page = self.client.get(reverse("academics:ecd_report_setup"))
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.context["can_edit"])
