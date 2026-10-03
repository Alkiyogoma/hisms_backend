"""
Failed saves on exam-mark and report pages must say what is wrong, name the
field (subject / assessment / learner), keep what was typed, and never leave a
half-saved learner behind.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import ExamScore, ExamTypeConfiguration, ReportCard, ReportCardStatus
from academics import test_per_subject_approval as base


def _messages(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


class ScoreEntryErrorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        base.PerSubjectApprovalTests.setUpTestData.__func__(cls)
        ExamTypeConfiguration.objects.update_or_create(
            code="mid_term",
            defaults={"name": "Mid Term", "weight_percentage": Decimal("30"),
                      "max_score": Decimal("100"), "is_active": True},
        )

    def _post(self, scores, user=None):
        self.client.force_login(user or self.teachers["English"])
        return self.client.post(
            reverse("academics:api_primary_scores", args=[self.student.id]),
            data=json.dumps({"term": self.term.id, "scores": scores}),
            content_type="application/json",
        )

    def test_one_bad_cell_saves_nothing_and_names_the_cell(self):
        resp = self._post({"English": {"quiz": "50", "mid_term": "150"}})
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIn("English – Mid Term", body["error"])
        self.assertIn("Nothing was saved", body["error"])
        self.assertEqual(body["invalid"], [{
            "subject": "English", "type": "mid_term",
            "message": "English – Mid Term: 150 is out of range (0–100).",
        }])
        self.assertFalse(ExamScore.objects.filter(student=self.student).exists())

    def test_non_numeric_score_is_reported_not_silently_skipped(self):
        resp = self._post({"English": {"quiz": "abc"}})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("'abc' is not a number", resp.json()["error"])

    def test_decimal_score_is_named(self):
        resp = self._post({"English": {"quiz": "67.5"}})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("English – Quiz: 67.5 must be a whole number", resp.json()["error"])


class ClassicExamScoreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        base.PerSubjectApprovalTests.setUpTestData.__func__(cls)

    def _post(self, user, subject, value):
        self.client.force_login(user)
        return self.client.post(reverse("academics:exam_scores_entry"), {
            "term": self.term.id, "class_name": "Grade 4", "subject_name": subject,
            "exam_type": "quiz", f"score_{self.student.id}": value, "action": "save",
        })

    def test_nan_and_decimal_are_row_errors_not_crashes(self):
        for value, text in (("NaN", "is not a number"), ("67.5", "must be a whole number")):
            resp = self._post(self.hod, "English", value)
            self.assertEqual(resp.status_code, 200, value)
            msgs = _messages(resp)
            self.assertTrue(any("Jasiel Kajeguka" in m and text in m for m in msgs), msgs)
        self.assertFalse(ExamScore.objects.filter(student=self.student).exists())

    def test_refusal_keeps_typed_scores_and_filter(self):
        # English teacher is not assigned to Bible Studies.
        resp = self._post(self.teachers["English"], "Bible Studies", "88")
        self.assertEqual(resp.status_code, 200)
        msgs = _messages(resp)
        self.assertTrue(any(m.startswith("Scores were not saved: you are not assigned") for m in msgs), msgs)
        self.assertEqual(resp.context["submitted_scores"], {str(self.student.id): "88"})
        self.assertEqual(resp.context["filter_form"].cleaned_data["subject_name"], "Bible Studies")

    def test_missing_filter_is_named(self):
        self.client.force_login(self.hod)
        resp = self.client.post(reverse("academics:exam_scores_entry"), {"term": self.term.id, "class_name": "Grade 4"})
        self.assertEqual(resp.status_code, 200)
        msgs = _messages(resp)
        self.assertTrue(any("Scores were not saved:" in m and "is missing or invalid" in m for m in msgs), msgs)
        self.assertIn('class="hf2-error"', resp.content.decode())


class ReportActionErrorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        base.PerSubjectApprovalTests.setUpTestData.__func__(cls)

    def _report(self, **kw):
        return ReportCard.objects.create(student=self.student, term=self.term, generated_by=self.hos, **kw)

    def test_sign_off_error_is_plain_text(self):
        rc = self._report(status=ReportCardStatus.PENDING_SIGN_OFF)
        self.client.force_login(self.hos)
        resp = self.client.post(reverse("academics:signoff_action"), {"report_id": rc.pk, "action": "signoff"})
        msgs = _messages(resp)
        self.assertTrue(any(m.startswith("Nothing was changed: Cannot sign off") for m in msgs), msgs)
        self.assertFalse(any("['" in m for m in msgs), msgs)

    def test_bulk_sign_off_names_blocked_learners(self):
        self._report(status=ReportCardStatus.PENDING_SIGN_OFF)
        self.client.force_login(self.hos)
        resp = self.client.post(reverse("academics:signoff_action"), {
            "class_name": "Grade 4", "term_id": self.term.id, "action": "signoff",
        })
        stored = list(get_messages(resp.wsgi_request))
        self.assertEqual([m.level_tag for m in stored], ["error"])  # nothing succeeded
        self.assertIn("Jasiel Kajeguka (Cannot sign off", str(stored[0]))

    def test_review_queue_keeps_filters(self):
        rc = self._report(status=ReportCardStatus.PENDING_SIGN_OFF)
        self.client.force_login(self.hos)
        back = reverse("academics:report_review_queue") + "?class_name=Grade+4"
        resp = self.client.post(
            reverse("academics:report_review_queue"),
            {"action": "approve", "report_id": rc.pk},
            HTTP_REFERER="http://testserver" + back,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].endswith(back), resp["Location"])

    def test_returning_a_report_unlocks_comments_even_from_draft(self):
        rc = self._report(status=ReportCardStatus.DRAFT, comments_submitted=True, teacher_comments="x" * 60)
        self.client.force_login(self.hod)
        self.client.post(reverse("academics:report_review_queue"), {
            "action": "reject", "report_id": rc.pk, "reason": "Comment needs more detail",
        })
        rc.refresh_from_db()
        self.assertFalse(rc.comments_submitted)

    def test_returning_a_published_report_explains_what_to_do(self):
        rc = self._report(status=ReportCardStatus.PUBLISHED)
        self.client.force_login(self.hod)
        resp = self.client.post(reverse("academics:report_review_queue"), {
            "action": "reject", "report_id": rc.pk, "reason": "x",
        })
        msgs = _messages(resp)
        self.assertTrue(any("Revoke the sign-off first" in m for m in msgs), msgs)


class CommentEntryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        base.PerSubjectApprovalTests.setUpTestData.__func__(cls)
        from hr.models import TeacherClassAssignment
        tca = TeacherClassAssignment.objects.get(teacher__user=cls.teachers["English"])
        tca.is_class_teacher = True
        tca.save()
        # Comment window open: endterm exams finished, deadline ahead.
        cls.term.endterm_exam_end_date = timezone.localdate() - timedelta(days=1)
        cls.term.grading_deadline = timezone.localdate() + timedelta(days=10)
        cls.term.save()

    def setUp(self):
        self.rc = ReportCard.objects.create(student=self.student, term=self.term, generated_by=self.hos)
        self.client.force_login(self.teachers["English"])
        self.url = reverse("academics:primary_comments_entry")

    def test_short_comment_is_kept_and_named(self):
        resp = self.client.post(self.url, {
            "class_name": "Grade 4", "term_id": self.term.id, f"comment_{self.rc.id}": "Good work.",
        })
        self.rc.refresh_from_db()
        self.assertEqual(self.rc.teacher_comments, "Good work.")
        msgs = _messages(resp)
        self.assertTrue(any("too short to submit" in m and "Jasiel Kajeguka (10)" in m for m in msgs), msgs)
        self.assertIn(f"term={self.term.id}", resp["Location"])

    def test_autosave_on_published_report_is_refused_with_reason(self):
        ReportCard.objects.filter(pk=self.rc.pk).update(status=ReportCardStatus.PUBLISHED)
        resp = self.client.post(
            self.url, data=json.dumps({"report_id": self.rc.pk, "comment": "x" * 60}),
            content_type="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertIn("published", resp.json()["error"])
        self.rc.refresh_from_db()
        self.assertEqual(self.rc.teacher_comments, "")
