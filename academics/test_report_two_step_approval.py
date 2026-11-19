"""
Every report (ECD, Primary, Lower Secondary) goes through two steps on the
Review Queue: the HOD approves it, then the Head of School approves and
publishes it. The queue filters by year, term, class and assessment type, and
the HOS Sign-Off page is retired.
"""
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics import test_per_subject_approval as base
from academics.models import GradeClass, Department, ReportCard, ReportCardStatus, Term
from academics.services import hod_approve_report, sign_off_report
from hr.models import TeacherClassAssignment

QUEUE = "academics:report_review_queue"


class TwoStepApprovalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        base.PerSubjectApprovalTests.setUpTestData.__func__(cls)

    _save = base.PerSubjectApprovalTests._save
    _submit = base.PerSubjectApprovalTests._submit
    _approve = base.PerSubjectApprovalTests._approve

    def _complete_report(self):
        for subject in base.SUBJECTS:
            self._save(subject, 70)
            self._submit(subject)
            self._approve(subject)
        rc = ReportCard.objects.get(student=self.student, term=self.term)
        rc.teacher_comments = "Jasiel worked steadily this term and should keep reading every day."
        rc.comments_submitted = True
        rc.status = ReportCardStatus.PENDING_SIGN_OFF
        rc.save()
        return rc

    def _row(self, rc, user, **params):
        self.client.force_login(user)
        resp = self.client.get(reverse(QUEUE), params)
        self.assertEqual(resp.status_code, 200)
        return resp, next((r for r in resp.context["reports"] if r["report"].pk == rc.pk), None)

    def test_primary_report_waits_for_hod_before_hos_can_publish(self):
        rc = self._complete_report()
        with self.assertRaisesMessage(ValidationError, "waiting for HOD approval"):
            sign_off_report(rc, self.hos)

        _, row = self._row(rc, self.hod)
        self.assertTrue(row["awaiting_hod"])
        self.assertFalse(row["ready_to_publish"])

        self.client.post(reverse(QUEUE), {"action": "hod_approve", "report_id": rc.pk})
        rc.refresh_from_db()
        self.assertEqual(rc.ecd_hod_approved_by, self.hod)
        self.assertEqual(rc.status, ReportCardStatus.PENDING_SIGN_OFF)

        resp, row = self._row(rc, self.hos)
        self.assertTrue(row["ready_to_publish"])
        self.assertContains(resp, "HOS Approve and Publish")
        self.client.post(reverse(QUEUE), {"action": "approve", "report_id": rc.pk})
        rc.refresh_from_db()
        self.assertEqual(rc.status, ReportCardStatus.PUBLISHED)

    def test_hod_cannot_publish(self):
        rc = self._complete_report()
        hod_approve_report(rc, self.hod)
        self.client.force_login(self.hod)
        resp = self.client.post(reverse(QUEUE), {"action": "approve", "report_id": rc.pk})
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("Only the Head of School" in m for m in msgs), msgs)
        rc.refresh_from_db()
        self.assertNotEqual(rc.status, ReportCardStatus.PUBLISHED)

    def test_hod_cannot_approve_an_incomplete_report(self):
        self._save("English", 70)
        self._submit("English")
        rc = ReportCard.objects.get(student=self.student, term=self.term)
        rc.teacher_comments = "x" * 60
        rc.status = ReportCardStatus.PENDING_SIGN_OFF
        rc.save()
        with self.assertRaisesMessage(ValidationError, "incomplete"):
            hod_approve_report(rc, self.hod)

    def test_returning_clears_the_hod_approval(self):
        rc = self._complete_report()
        hod_approve_report(rc, self.hod)
        self.client.force_login(self.hos)
        self.client.post(reverse(QUEUE), {"action": "reject", "report_id": rc.pk, "reason": "Fix comment"})
        rc.refresh_from_db()
        self.assertIsNone(rc.ecd_hod_approved_by_id)

    def test_preview_class_buttons(self):
        rc = self._complete_report()
        self.client.force_login(self.hod)
        page = self.client.get(reverse("academics:report_preview", args=[rc.pk])).content.decode()
        self.assertIn("HOD Approve All (1)", page)
        self.assertNotIn("HOS Approve and Publish", page)
        self.client.post(reverse("academics:signoff_action"), {
            "class_name": "Grade 4", "term_id": self.term.pk, "action": "hod_approve",
        })
        rc.refresh_from_db()
        self.assertEqual(rc.ecd_hod_approved_by, self.hod)

        self.client.force_login(self.hos)
        page = self.client.get(reverse("academics:report_preview", args=[rc.pk])).content.decode()
        self.assertIn("HOS Approve and Publish All (1)", page)
        self.client.post(reverse("academics:signoff_action"), {
            "class_name": "Grade 4", "term_id": self.term.pk, "action": "approve",
        })
        rc.refresh_from_db()
        self.assertEqual(rc.status, ReportCardStatus.PUBLISHED)

    def test_filters_by_class_and_term(self):
        rc = self._complete_report()
        GradeClass.objects.create(name="Grade 5", department=Department.PRIMARY)
        _, row = self._row(rc, self.hos, class_name="Grade 4")
        self.assertIsNotNone(row)
        _, row = self._row(rc, self.hos, class_name="Grade 5")
        self.assertIsNone(row)
        other = Term.objects.create(academic_year=self.year, name="Term 2",
                                    start_date=self.term.end_date, end_date=self.term.end_date)
        resp, row = self._row(rc, self.hos, year=self.year.pk, term=other.pk)
        self.assertIsNone(row)
        self.assertEqual(resp.context["term"], other)
        resp, row = self._row(rc, self.hos, type="quiz")
        self.assertEqual(row["average"], 70)

    def test_teacher_column_is_the_class_teacher(self):
        rc = self._complete_report()
        _, row = self._row(rc, self.hos)
        self.assertEqual(row["teacher"], "")  # none assigned: not whoever generated it
        tca = TeacherClassAssignment.objects.get(teacher__user=self.teachers["English"])
        tca.is_class_teacher = True
        tca.save()
        resp, row = self._row(rc, self.hos)
        self.assertEqual(row["teacher"], "English Teacher")

    def test_hos_signoff_page_is_gone(self):
        self.client.force_login(self.hos)
        resp = self.client.get(reverse("academics:hos_signoff_list"))
        self.assertRedirects(resp, reverse(QUEUE), fetch_redirect_response=False)
        page = self.client.get(reverse(QUEUE)).content.decode()
        self.assertNotIn("HOS Sign-Off", page)
