"""
Lesson plan viewer. Regressions for:

* the preview of a Word lesson plan flattened its tables into running text,
  so a reviewer could not judge structure, timings or layout. Office files
  are now converted to PDF on the server and shown in the PDF viewer;
* the only way to give a teacher feedback was to reject the plan, and the
  teacher had no way to reply. Each plan now has a comment thread that does
  not change its status.
"""
import io
import shutil
import subprocess
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from academics import lesson_plan_files
from academics.models import (
    Department, GradeClass, LessonPlan, LessonPlanAttachment, LessonPlanComment, LessonPlanStatus,
)
from academics.test_lesson_plan_attachment import LessonPlanFixture
from academics.tests import assign_role_group
from communications.models import Notification
from users.models import User, UserRole

MEDIA = tempfile.mkdtemp()

_DOC = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>English lesson plan</w:t></w:r></w:p>
<w:tbl><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/>
<w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/></w:tblBorders></w:tblPr>
<w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="1500"/><w:gridCol w:w="4000"/><w:gridCol w:w="2000"/></w:tblGrid>
{rows}
</w:tbl></w:body></w:document>"""


def _row(*cells):
    return "<w:tr>" + "".join(
        f'<w:tc><w:tcPr><w:tcW w:w="2000" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>'
        for c in cells) + "</w:tr>"


def docx_bytes(activity="Warm-up game"):
    """A minimal Word file: a title and a Stages / Time / Activities / Notes grid."""
    rows = _row("Stage", "Time", "Planned Activities", "Notes") + _row("Starter", "5 min", activity, "Pairs")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   '</Relationships>')
        z.writestr("word/document.xml", _DOC.format(rows=rows))
    return buf.getvalue()


@override_settings(MEDIA_ROOT=MEDIA)
class LessonPlanViewerTests(LessonPlanFixture):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        GradeClass.objects.create(name="Grade 8", department=Department.LOWER_SECONDARY)

        def user(name, role):
            u = User.objects.create_user(username=name, email=f"{name}@example.test", password="x", role=role,
                                         first_name=name.title(), last_name="Tester")
            assign_role_group(u)
            return u
        cls.hod = user("esther", UserRole.PRIMARY_HOD)
        cls.ls_hod = user("eunice", UserRole.LOWER_SECONDARY_HOD)
        cls.other_teacher = user("joshua", UserRole.TEACHER)

    def setUp(self):
        self.plan = LessonPlan.objects.create(
            teacher=self.teacher, term=self.term, class_name="Grade 4", subject_name="English",
            week_start_date=self.next_monday + timedelta(weeks=1), day_of_week="mon", lesson_title="Phonics",
        )
        LessonPlan.objects.filter(pk=self.plan.pk).update(status=LessonPlanStatus.SUBMITTED)
        self.plan.refresh_from_db()
        self.att = LessonPlanAttachment.objects.create(
            lesson_plan=self.plan, uploaded_by=self.teacher, filename="phonics.docx",
            file=SimpleUploadedFile("phonics.docx", docx_bytes()),
        )

    def thread(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("academics:lesson_plan_thread", args=[self.plan.pk]))

    def comment(self, user, body):
        self.client.force_login(user)
        return self.client.post(reverse("academics:lesson_plan_comment", args=[self.plan.pk]), {"body": body})

    # -- preview ------------------------------------------------------------

    def test_word_file_is_previewed_as_a_pdf(self):
        data = self.thread(self.hod).json()
        self.assertEqual([(f["name"], f["kind"]) for f in data["files"]], [("phonics.docx", "pdf")])
        self.assertEqual(data["files"][0]["download_url"], self.att.file.url)  # original stays downloadable

    def test_word_conversion_keeps_the_table_layout(self):
        if not lesson_plan_files.converter():
            self.skipTest("LibreOffice is not installed")
        self.client.force_login(self.hod)
        url = reverse("academics:lesson_plan_attachment_preview", args=[self.att.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        pdf = b"".join(resp.streaming_content)
        self.assertTrue(pdf.startswith(b"%PDF"))
        if shutil.which("pdftotext"):
            text = subprocess.run(["pdftotext", "-layout", "-", "-"], input=pdf, capture_output=True).stdout.decode()
            # The grid's header cells stay side by side on one line, as written.
            header = next(line for line in text.splitlines() if "Planned Activities" in line)
            self.assertIn("Stage", header)
            self.assertIn("Notes", header)
        # Cached: the second view does not convert again.
        with mock.patch("academics.lesson_plan_files.subprocess.run") as run:
            again = self.client.get(url)
            b"".join(again.streaming_content)
        self.assertEqual(again.status_code, 200)
        run.assert_not_called()

    def test_without_libreoffice_the_preview_reports_unavailable(self):
        self.client.force_login(self.hod)
        with mock.patch("academics.lesson_plan_files.converter", return_value=None):
            resp = self.client.get(reverse("academics:lesson_plan_attachment_preview", args=[self.att.pk]))
        self.assertEqual(resp.status_code, 503)

    def test_pdf_attachment_is_served_inline(self):
        att = LessonPlanAttachment.objects.create(
            lesson_plan=self.plan, uploaded_by=self.teacher, filename="plan.pdf",
            file=SimpleUploadedFile("plan.pdf", b"%PDF-1.4 plan"),
        )
        self.client.force_login(self.teacher)
        resp = self.client.get(reverse("academics:lesson_plan_attachment_preview", args=[att.pk]))
        self.assertEqual((resp.status_code, resp["Content-Type"], resp["Content-Disposition"]),
                         (200, "application/pdf", "inline"))

    def test_uploaded_svg_cannot_run_script_when_opened(self):
        att = LessonPlanAttachment.objects.create(
            lesson_plan=self.plan, uploaded_by=self.teacher, filename="diagram.svg",
            file=SimpleUploadedFile("diagram.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
        )
        self.client.force_login(self.hod)
        resp = self.client.get(reverse("academics:lesson_plan_attachment_preview", args=[att.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("sandbox", resp["Content-Security-Policy"])
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")

    def test_new_word_upload_queues_its_preview(self):
        LessonPlan.objects.filter(pk=self.plan.pk).update(status=LessonPlanStatus.DRAFT)
        with mock.patch("academics.tasks.prepare_lesson_plan_preview_task.apply_async") as queued, \
                self.captureOnCommitCallbacks(execute=True):
            self.client.force_login(self.teacher)
            self.client.post(reverse("academics:edit_lesson_plan_modal", args=[self.plan.pk]), {
                "lesson_title": "Phonics", "attachments": SimpleUploadedFile("extra.docx", docx_bytes("Sound hunt")),
            })
        new = LessonPlanAttachment.objects.filter(filename="extra.docx").first()
        self.assertIsNotNone(new)
        queued.assert_called_once_with(args=[new.pk], retry=False)

    def test_deleting_an_attachment_removes_its_cached_preview(self):
        LessonPlan.objects.filter(pk=self.plan.pk).update(status=LessonPlanStatus.DRAFT)
        cached = lesson_plan_files.cached_pdf_path(self.att)
        cached.write_bytes(b"%PDF-1.4 cached")
        self.client.force_login(self.teacher)
        self.client.post(reverse("academics:attachment_delete", args=[self.att.pk]))
        self.assertFalse(cached.exists())

    # -- comments -----------------------------------------------------------

    def test_hod_comment_keeps_status_and_notifies_teacher(self):
        resp = self.comment(self.hod, "Please use the 5/30/5 timing convention.")
        self.assertEqual(resp.status_code, 201)
        c = LessonPlanComment.objects.get()
        self.assertEqual((c.author, c.lesson_plan, c.body), (self.hod, self.plan, "Please use the 5/30/5 timing convention."))
        self.assertIsNotNone(c.created_at)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, LessonPlanStatus.SUBMITTED)
        note = Notification.objects.get(recipient=self.teacher, title="Comment on your lesson plan")
        self.assertIn("5/30/5", note.body)
        self.assertIn(f"plan={self.plan.pk}", note.link)

    def test_teacher_reply_reaches_the_reviewer_in_the_thread(self):
        self.comment(self.hod, "Why the group work here?")
        resp = self.comment(self.teacher, "The class works in reading groups on Mondays.")
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(Notification.objects.filter(recipient=self.hod, body__contains="reading groups").exists())
        data = self.thread(self.teacher).json()
        self.assertEqual([(c["role"], c["mine"]) for c in data["comments"]], [("Reviewer", False), ("Teacher", True)])
        self.assertTrue(data["can_comment"])

    def test_teacher_question_before_any_review_goes_to_section_heads(self):
        self.comment(self.teacher, "Is the 5/30/5 timing required for double lessons?")
        self.assertTrue(Notification.objects.filter(recipient=self.hod, title__icontains="commented").exists())
        self.assertFalse(Notification.objects.filter(recipient=self.ls_hod).exists())

    def test_who_can_see_and_comment(self):
        self.assertEqual(self.thread(self.other_teacher).status_code, 403)
        self.assertEqual(self.comment(self.other_teacher, "hi").status_code, 403)
        # Another section's head sees submitted plans (as on the list) but
        # cannot comment on plans outside their department.
        self.assertFalse(self.thread(self.ls_hod).json()["can_comment"])
        self.assertEqual(self.comment(self.ls_hod, "hi").status_code, 403)
        # Drafts stay private to the teacher.
        LessonPlan.objects.filter(pk=self.plan.pk).update(status=LessonPlanStatus.DRAFT)
        self.assertEqual(self.thread(self.hod).status_code, 403)
        self.assertEqual(self.thread(self.teacher).status_code, 200)
        self.client.force_login(self.other_teacher)
        resp = self.client.get(reverse("academics:lesson_plan_attachment_preview", args=[self.att.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_empty_comment_is_refused(self):
        self.assertEqual(self.comment(self.hod, "   ").status_code, 400)
        self.assertFalse(LessonPlanComment.objects.exists())

    def test_rejection_still_works_and_feedback_shows_on_the_list(self):
        self.client.force_login(self.hod)
        self.client.post(reverse("academics:review_lesson_plan", args=[self.plan.pk]),
                         {"decision": "rejected", "feedback": "Use capital letters for headings."})
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, LessonPlanStatus.REJECTED)
        self.assertEqual(self.thread(self.teacher).json()["plan"]["feedback"], "Use capital letters for headings.")

    # -- pages --------------------------------------------------------------

    def test_list_and_queue_open_the_viewer_with_comment_counts(self):
        self.comment(self.hod, "One question.")
        self.client.force_login(self.hod)
        resp = self.client.get(reverse("academics:lesson_plans"))
        self.assertContains(resp, 'id="lpViewer"')
        self.assertContains(resp, f'data-lp-comments="{self.plan.pk}"')
        self.assertContains(resp, '<span class="lp-cmt-count">1</span>', html=False)
        self.assertNotContains(resp, "mammoth")
        resp = self.client.get(reverse("academics:lesson_plan_review_queue"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="lpViewer"')
        LessonPlan.objects.filter(pk=self.plan.pk).update(status=LessonPlanStatus.DRAFT)
        self.client.force_login(self.teacher)
        resp = self.client.get(reverse("academics:lesson_plan_edit", args=[self.plan.pk]))
        self.assertContains(resp, 'id="lpViewer"')
        self.assertNotContains(resp, "mammoth")
