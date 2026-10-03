"""
Approval and locking are per learner + subject + assessment type, never the
whole report. Regression for: an approved English Quiz locked the learner's
whole report, so Bible Studies / Digital Literacy teachers could not submit
their Quiz scores and the 67% "grand average" came from one subject only.
"""
import json
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import (
    AcademicYear, Department, ExamScore, ExamTypeConfiguration, GradeClass,
    ReportCard, ReportCardStatus, ScoreStatus, Subject, Term,
)
from academics.services import sign_off_report
from academics.tests import assign_role_group
from hr.models import StaffProfile, TeacherClassAssignment
from students.models import Student, StudentStatus
from users.models import User, UserRole

SUBJECTS = ["English", "Bible Studies", "Digital Literacy"]


class PerSubjectApprovalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Term 1",
            start_date=today - timedelta(days=30), end_date=today + timedelta(days=60),
        )
        ExamTypeConfiguration.objects.update_or_create(
            code="quiz",
            defaults={"name": "Quiz", "weight_percentage": Decimal("20"),
                      "max_score": Decimal("100"), "is_active": True},
        )
        cls.gc = GradeClass.objects.create(name="Grade 4", department=Department.PRIMARY)
        for i, name in enumerate(SUBJECTS):
            subj = Subject.objects.create(
                name=name, code=f"S{i}", department=Department.PRIMARY,
                departments=[Department.PRIMARY],
            )
            subj.classes.add(cls.gc)

        cls.student = Student.objects.create(
            admission_no="G4-001", first_name="Jasiel", last_name="Kajeguka",
            class_name="Grade 4", academic_year=cls.year, status=StudentStatus.ACTIVE,
        )

        cls.teachers = {}
        for subject in SUBJECTS:
            slug = subject.lower().replace(" ", "")
            user = User.objects.create_user(
                username=f"t.{slug}", email=f"{slug}@example.test", password="x", role=UserRole.TEACHER,
                first_name=subject, last_name="Teacher",
            )
            assign_role_group(user)
            staff = StaffProfile.objects.create(
                user=user, employment_start_date=date(2024, 1, 1),
                full_name=f"{subject} Teacher", department="PRIMARY", job_title="Teacher",
            )
            TeacherClassAssignment.objects.create(
                teacher=staff, term=cls.term, grade_class=cls.gc, subjects_taught=[subject],
            )
            cls.teachers[subject] = user

        cls.hod = User.objects.create_user(username="hod", email="hod@example.test", password="x", role=UserRole.PRIMARY_HOD)
        assign_role_group(cls.hod)
        cls.hos = User.objects.create_user(username="hos", email="hos@example.test", password="x", role=UserRole.HEAD_OF_SCHOOL)
        assign_role_group(cls.hos)

    # ── helpers ──────────────────────────────────────────────────────
    def _save(self, subject, value):
        self.client.force_login(self.teachers[subject])
        return self.client.post(
            reverse("academics:api_primary_scores", args=[self.student.id]),
            data=json.dumps({"term": self.term.id, "scores": {subject: {"quiz": str(value)}}}),
            content_type="application/json",
        )

    def _submit(self, subject):
        self.client.force_login(self.teachers[subject])
        return self.client.post(
            reverse("academics:api_primary_submit_all"),
            data=json.dumps({"class_name": "Grade 4", "term": self.term.id}),
            content_type="application/json",
        )

    def _approve(self, subject):
        ids = list(ExamScore.objects.filter(
            student=self.student, subject_name=subject, status=ScoreStatus.SUBMITTED,
        ).values_list("id", flat=True))
        self.client.force_login(self.hod)
        self.client.post(
            reverse("academics:exam_score_approval_queue"),
            {"action": "approve", "score_ids": ids},
        )

    def _progress(self, as_user=None):
        self.client.force_login(as_user or self.hod)
        return self.client.get(
            reverse("academics:api_primary_scores", args=[self.student.id]),
            {"term": self.term.id},
        ).json()["progress"]

    def _score(self, subject):
        return ExamScore.objects.get(student=self.student, subject_name=subject, exam_type="quiz")

    # ── tests ────────────────────────────────────────────────────────
    def test_approved_subject_does_not_block_other_subjects(self):
        self.assertEqual(self._save("English", 67).status_code, 200)
        self.assertEqual(self._submit("English").status_code, 200)
        self._approve("English")
        self.assertEqual(self._score("English").status, ScoreStatus.APPROVED)

        # Another teacher saves and submits Quiz for a different subject.
        resp = self._save("Bible Studies", 80)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["updated"], 1)
        self.assertEqual(self._submit("Bible Studies").status_code, 200)
        self.assertEqual(self._score("Bible Studies").status, ScoreStatus.SUBMITTED)

        # English is untouched by the second submission.
        self.assertEqual(self._score("English").status, ScoreStatus.APPROVED)
        self.assertEqual(self._score("English").score, 67)

    def test_locked_subject_rejects_only_itself(self):
        self._save("English", 67)
        self._submit("English")
        self._approve("English")

        resp = self._save("English", 90)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("English", resp.json()["error"])
        self.assertNotIn("Bible Studies", resp.json()["error"])
        self.assertEqual(self._score("English").score, 67)

    def test_report_not_pending_until_every_subject_submitted(self):
        self._save("English", 67)
        self._submit("English")
        rc = ReportCard.objects.get(student=self.student, term=self.term)
        self.assertEqual(rc.status, ReportCardStatus.DRAFT)

        for subject in SUBJECTS[1:]:
            self._save(subject, 70)
            self._submit(subject)
        rc.refresh_from_db()
        self.assertEqual(rc.status, ReportCardStatus.PENDING_SIGN_OFF)

    def test_grand_average_uses_approved_subjects_and_reports_count(self):
        self._save("English", 67)
        self._submit("English")
        self._approve("English")
        self._save("Bible Studies", 81)
        self._submit("Bible Studies")  # submitted, not approved — excluded

        p = self._progress()
        self.assertEqual(p["grand_average"], 67.0)
        self.assertEqual(p["included_subjects"], 1)
        self.assertEqual(p["total_subjects"], 3)
        self.assertFalse(p["is_complete"])
        self.assertEqual(p["subjects"]["Digital Literacy"]["status"], "not_started")

        self._approve("Bible Studies")
        p = self._progress()
        self.assertEqual(p["grand_average"], 74.0)
        self.assertEqual(p["included_subjects"], 2)

    def test_student_list_shows_partial_until_all_subjects_approved(self):
        self._save("English", 67)
        self._submit("English")
        self._approve("English")

        self.client.force_login(self.hod)
        row = self.client.get(
            reverse("academics:api_primary_students"),
            {"class_name": "Grade 4", "term": self.term.id},
        ).json()["students"][0]
        self.assertEqual(row["score_status"], "partial")
        self.assertEqual((row["approved_subjects"], row["total_subjects"]), (1, 3))

        for subject in SUBJECTS[1:]:
            self._save(subject, 75)
            self._submit(subject)
            self._approve(subject)
        row = self.client.get(
            reverse("academics:api_primary_students"),
            {"class_name": "Grade 4", "term": self.term.id},
        ).json()["students"][0]
        self.assertEqual(row["score_status"], "approved")

    def test_other_teachers_submit_button_not_marked_submitted(self):
        self._save("English", 67)
        self._submit("English")
        self._approve("English")

        self.client.force_login(self.teachers["Bible Studies"])
        resp = self.client.get(reverse("academics:primary_assessment"), {"class_name": "Grade 4"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["class_submitted"])

    def test_sign_off_blocked_while_a_subject_has_no_approved_scores(self):
        self._save("English", 67)
        self._submit("English")
        self._approve("English")
        rc = ReportCard.objects.get(student=self.student, term=self.term)
        rc.teacher_comments = "x" * 60
        rc.save()

        with self.assertRaises(ValidationError) as cm:
            sign_off_report(rc, self.hos)
        self.assertIn("Bible Studies", str(cm.exception))
        self.assertIn("Digital Literacy", str(cm.exception))

    # ── remaining gaps ───────────────────────────────────────────────
    def _all_subjects_submitted(self, approve=()):
        for subject in SUBJECTS:
            self._save(subject, 70)
            self._submit(subject)
            if subject in approve:
                self._approve(subject)
        return ReportCard.objects.get(student=self.student, term=self.term)

    def test_return_report_reopens_only_chosen_subject(self):
        rc = self._all_subjects_submitted(approve=SUBJECTS)
        rc.refresh_from_db()
        self.assertEqual(rc.status, ReportCardStatus.PENDING_SIGN_OFF)

        self.client.force_login(self.hos)
        self.client.post(reverse("academics:report_review_queue"), {
            "action": "reject", "report_id": rc.pk,
            "reason": "Check the Bible Studies quiz", "subjects": ["Bible Studies"],
        })
        rc.refresh_from_db()
        self.assertEqual(rc.status, ReportCardStatus.DRAFT)
        bible = self._score("Bible Studies")
        self.assertEqual(bible.status, ScoreStatus.RETURNED)
        self.assertFalse(bible.is_locked)
        for subject in ("English", "Digital Literacy"):
            self.assertEqual(self._score(subject).status, ScoreStatus.APPROVED)
            self.assertTrue(self._score(subject).is_locked)

    def test_return_report_without_subjects_keeps_all_scores(self):
        rc = self._all_subjects_submitted(approve=SUBJECTS)
        self.client.force_login(self.hos)
        self.client.post(reverse("academics:report_review_queue"), {
            "action": "reject", "report_id": rc.pk, "reason": "Comment needs work",
        })
        rc.refresh_from_db()
        self.assertEqual(rc.status, ReportCardStatus.DRAFT)
        for subject in SUBJECTS:
            self.assertEqual(self._score(subject).status, ScoreStatus.APPROVED)

    def test_lower_secondary_hod_may_return_reports(self):
        from academics.services import reject_report_for_edit
        rc = self._all_subjects_submitted()
        ls_hod = User.objects.create_user(
            username="lshod", email="lshod@example.test", password="x",
            role=UserRole.LOWER_SECONDARY_HOD,
        )
        reject_report_for_edit(rc, ls_hod, reason="fix", subjects=["English"])
        self.assertEqual(self._score("English").status, ScoreStatus.RETURNED)

    def test_hod_returning_a_score_reopens_the_report(self):
        rc = self._all_subjects_submitted()
        rc.refresh_from_db()
        self.assertEqual(rc.status, ReportCardStatus.PENDING_SIGN_OFF)

        self.client.force_login(self.hod)
        self.client.post(reverse("academics:exam_score_approval_queue"), {
            "action": "return", "reason": "Recheck", "score_ids": [self._score("English").pk],
        })
        rc.refresh_from_db()
        self.assertEqual(rc.status, ReportCardStatus.DRAFT)
        self.assertEqual(self._score("Bible Studies").status, ScoreStatus.SUBMITTED)

    def test_teacher_without_subjects_cannot_submit_others_drafts(self):
        self._save("English", 67)  # English teacher's draft
        loner = User.objects.create_user(
            username="t.none", email="none@example.test", password="x", role=UserRole.TEACHER,
        )
        assign_role_group(loner)
        staff = StaffProfile.objects.create(
            user=loner, employment_start_date=date(2024, 1, 1),
            full_name="No Subjects", department="PRIMARY", job_title="Teacher",
        )
        TeacherClassAssignment.objects.create(
            teacher=staff, term=self.term, grade_class=self.gc, subjects_taught=[],
        )
        self.client.force_login(loner)
        self.client.post(
            reverse("academics:api_primary_submit_all"),
            data=json.dumps({"class_name": "Grade 4", "term": self.term.id}),
            content_type="application/json",
        )
        self.assertEqual(self._score("English").status, ScoreStatus.DRAFT)

    def test_review_queue_not_ready_until_every_subject_approved(self):
        rc = self._all_subjects_submitted(approve=["English"])
        rc.teacher_comments = "x" * 60
        rc.comments_submitted = True
        rc.save()
        self.client.force_login(self.hos)
        resp = self.client.get(reverse("academics:report_review_queue"))
        self.assertEqual(resp.status_code, 200)
        row = next(r for r in resp.context["reports"] if r["report"].pk == rc.pk)
        self.assertFalse(row["all_scores_approved"])
        self.assertEqual((row["approved_subjects"], row["total_subjects"]), (1, 3))
        self.assertEqual(resp.context["total_ready"], 0)

    def test_data_migration_resets_incomplete_pending_reports(self):
        from importlib import import_module
        from django.apps import apps
        mig = import_module("academics.migrations.0061_reset_incomplete_pending_reports")

        self._save("English", 67)
        self._submit("English")
        rc = ReportCard.objects.get(student=self.student, term=self.term)
        ReportCard.objects.filter(pk=rc.pk).update(status=ReportCardStatus.PENDING_SIGN_OFF)

        mig.reset_incomplete(apps, None)
        rc.refresh_from_db()
        self.assertEqual(rc.status, ReportCardStatus.DRAFT)

        for subject in SUBJECTS[1:]:
            self._save(subject, 70)
            self._submit(subject)
        ReportCard.objects.filter(pk=rc.pk).update(status=ReportCardStatus.PENDING_SIGN_OFF)
        mig.reset_incomplete(apps, None)
        rc.refresh_from_db()
        self.assertEqual(rc.status, ReportCardStatus.PENDING_SIGN_OFF)
