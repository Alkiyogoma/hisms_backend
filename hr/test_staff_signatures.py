"""
Staff signatures: imported from the signing kiosk export (or uploaded by HR),
approved by the Head of School, and printed on report cards only once
approved and only where that person did their step.
"""
import csv
import io
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import AcademicYear, Department, GradeClass, ReportCard, ReportCardStatus, Term
from academics.tests import assign_role_group
from academics.views.reports import report_signatures
from hr.models import SignatureStatus, StaffProfile, StaffSignature, TeacherClassAssignment, approved_signature_uri
from hr.signatures import approve_signature, import_signature_export, reject_signature, save_signature
from students.models import Student, StudentStatus
from users.models import User, UserRole

PRIVATE = tempfile.mkdtemp(prefix="sig-test-")


def png(width=300, height=120, color=(10, 40, 160, 255)):
    buf = io.BytesIO()
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for x in range(20, width - 20):
        img.putpixel((x, height // 2), color)
    img.save(buf, "PNG")
    return buf.getvalue()


@override_settings(PRIVATE_MEDIA_ROOT=PRIVATE)
class SignatureTestBase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(PRIVATE, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term = Term.objects.create(academic_year=cls.year, name="Term 1",
                                       start_date=today - timedelta(days=30), end_date=today + timedelta(days=60))
        cls.gc = GradeClass.objects.create(name="Grade 4", department=Department.PRIMARY)

        def staff(username, role, employee_id, full_name):
            u = User.objects.create_user(username=username, email=f"{username}@example.test", password="x",
                                         role=role, first_name=full_name.split()[0], last_name=full_name.split()[-1])
            assign_role_group(u)
            p = StaffProfile.objects.create(user=u, employment_start_date=date(2024, 1, 1), full_name=full_name,
                                            department="PRIMARY", departments=["PRIMARY"], job_title="Teacher",
                                            employee_id=employee_id)
            return u, p

        cls.teacher, cls.teacher_profile = staff("teacher", UserRole.TEACHER, "HST100", "Asha Teacher")
        cls.hod, _ = staff("hod", UserRole.PRIMARY_HOD, "HST004", "Esther Hod")
        cls.hos, _ = staff("hos", UserRole.HEAD_OF_SCHOOL, "HST002", "Julieth Head")
        cls.hr, _ = staff("hr", UserRole.ADMIN_OFFICER, "HST011", "Anna Hr")
        TeacherClassAssignment.objects.create(teacher=cls.teacher_profile, term=cls.term, grade_class=cls.gc,
                                              is_class_teacher=True, subjects_taught=["English"])
        cls.student = Student.objects.create(admission_no="G4-001", first_name="Jasiel", last_name="K",
                                             class_name="Grade 4", academic_year=cls.year,
                                             status=StudentStatus.ACTIVE)


class SignatureServiceTests(SignatureTestBase):
    def test_new_signature_waits_for_approval_and_is_not_printed(self):
        sig = save_signature(self.teacher, png(), source="office tablet", uploaded_by=self.hr)
        self.assertEqual(sig.status, SignatureStatus.PENDING)
        self.assertIsNone(approved_signature_uri(self.teacher))
        approve_signature(sig, self.hos)
        self.assertTrue(approved_signature_uri(self.teacher).startswith("data:image/png;base64,"))

    def test_signature_is_stored_outside_public_media(self):
        sig = save_signature(self.teacher, png())
        self.assertTrue(Path(sig.image.path).is_relative_to(PRIVATE))
        self.assertIsNone(sig.image.storage.base_url)

    def test_replacing_an_approved_signature_needs_approval_again(self):
        sig = approve_signature(save_signature(self.teacher, png()), self.hos)
        old_path = sig.image.path
        with self.captureOnCommitCallbacks(execute=True):
            sig = save_signature(self.teacher, png(400, 150))
        self.assertEqual(sig.status, SignatureStatus.PENDING)
        self.assertIsNone(sig.reviewed_by)
        self.assertFalse(Path(old_path).exists())

    def test_only_head_of_school_can_approve(self):
        sig = save_signature(self.teacher, png())
        for user in (self.teacher, self.hod, self.hr):
            with self.assertRaises(ValidationError):
                approve_signature(sig, user)

    def test_reject_records_reason(self):
        sig = reject_signature(save_signature(self.teacher, png()), self.hos, "Smudged, please sign again")
        self.assertEqual(sig.status, SignatureStatus.REJECTED)
        self.assertEqual(sig.reject_reason, "Smudged, please sign again")
        self.assertIsNone(approved_signature_uri(self.teacher))

    def test_rejects_non_png_and_tiny_images(self):
        with self.assertRaisesMessage(ValidationError, "PNG"):
            save_signature(self.teacher, b"<svg xmlns='http://www.w3.org/2000/svg'/>")
        with self.assertRaisesMessage(ValidationError, "px"):
            save_signature(self.teacher, png(10, 10))
        with self.assertRaisesMessage(ValidationError, "damaged"):
            save_signature(self.teacher, b"\x89PNG\r\n\x1a\n" + b"junk" * 50)


class SignatureImportTests(SignatureTestBase):
    def _export(self, rows):
        folder = Path(tempfile.mkdtemp(prefix="sig-export-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        (folder / "png").mkdir()
        fields = ["staff_id", "name", "email", "png_file", "signed_at_utc", "signed_via", "consent_at_utc"]
        with (folder / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            for row in rows:
                if row.get("png_file", "").startswith("png/"):
                    (folder / row["png_file"]).write_bytes(row.pop("_bytes", png()))
                w.writerow({k: row.get(k, "") for k in fields})
        return folder

    def test_matches_by_staff_id_then_email_and_reports_the_rest(self):
        folder = self._export([
            # Shared inbox email, but the staff ID matches.
            {"staff_id": "HST100", "name": "Asha Teacher", "email": "hello@example.test",
             "png_file": "png/Asha.png", "signed_at_utc": "2026-11-19T05:06:39+00:00", "signed_via": "office tablet"},
            # No staff ID: matched by email.
            {"staff_id": "NOID-01", "name": "Julieth Head", "email": "HOS@example.test", "png_file": "png/J.png"},
            {"staff_id": "HST999", "name": "Nobody", "email": "nobody@example.test", "png_file": "png/N.png"},
            {"staff_id": "HST004", "name": "Esther Hod", "email": "hod@example.test", "png_file": "png/missing.png"},
        ])
        (folder / "png/missing.png").unlink()
        result = import_signature_export(folder, by=self.hr)
        self.assertEqual([(r[0], r[2]) for r in result.imported], [("HST100", "staff ID"), ("NOID-01", "email")])
        self.assertEqual([r[0] for r in result.unmatched], ["HST999"])
        self.assertEqual([r[0] for r in result.failed], ["HST004"])
        sig = StaffSignature.objects.get(user=self.teacher)
        self.assertEqual(sig.status, SignatureStatus.PENDING)
        self.assertEqual(sig.source, "office tablet")
        self.assertEqual(sig.signed_at.year, 2026)
        self.assertEqual(sig.uploaded_by, self.hr)

    def test_reimport_leaves_approved_signatures_alone(self):
        raw = png()
        folder = self._export([{"staff_id": "HST100", "name": "Asha", "png_file": "png/A.png", "_bytes": raw}])
        import_signature_export(folder)
        approve_signature(StaffSignature.objects.get(user=self.teacher), self.hos)
        result = import_signature_export(folder)
        self.assertEqual(len(result.unchanged), 1)
        self.assertEqual(StaffSignature.objects.get(user=self.teacher).status, SignatureStatus.APPROVED)

    def test_dry_run_stores_nothing_and_paths_cannot_escape_the_folder(self):
        folder = self._export([
            {"staff_id": "HST100", "name": "Asha", "png_file": "png/A.png"},
            {"staff_id": "HST002", "name": "Julieth", "png_file": "../../etc/passwd"},
        ])
        result = import_signature_export(folder, dry_run=True)
        self.assertEqual(len(result.imported), 1)
        self.assertEqual(len(result.failed), 1)
        self.assertFalse(StaffSignature.objects.exists())

    def test_management_command(self):
        folder = self._export([{"staff_id": "HST100", "name": "Asha", "png_file": "png/A.png"}])
        out = io.StringIO()
        call_command("import_signatures", str(folder), "--by", "hr@example.test", stdout=out)
        self.assertIn("Imported 1 signature", out.getvalue())
        self.assertEqual(StaffSignature.objects.get(user=self.teacher).uploaded_by, self.hr)


class ReportSignatureTests(SignatureTestBase):
    def setUp(self):
        for user in (self.teacher, self.hod, self.hos):
            approve_signature(save_signature(user, png()), self.hos)
        self.rc = ReportCard.objects.create(student=self.student, term=self.term, generated_by=self.teacher)

    def test_draft_report_has_no_signatures(self):
        self.assertEqual(report_signatures(self.rc),
                         {"class_teacher": None, "hod": None, "hod_name": "", "hos": None})

    def test_each_signature_appears_after_that_persons_step(self):
        self.rc.status = ReportCardStatus.PENDING_SIGN_OFF
        self.rc.save()
        s = report_signatures(self.rc)
        self.assertTrue(s["class_teacher"])
        self.assertIsNone(s["hod"])
        self.assertIsNone(s["hos"])

        self.rc.ecd_hod_approved_by = self.hod
        self.rc.save()
        s = report_signatures(self.rc)
        self.assertTrue(s["hod"])
        self.assertEqual(s["hod_name"], "Esther Hod")
        self.assertIsNone(s["hos"])

        self.rc.status = ReportCardStatus.PUBLISHED
        self.rc.signed_off_by = self.hos
        self.rc.save()
        self.assertTrue(report_signatures(self.rc)["hos"])

    def test_unapproved_signature_is_not_printed(self):
        sig = StaffSignature.objects.get(user=self.teacher)
        reject_signature(sig, self.hos, "Unclear")
        self.rc.status = ReportCardStatus.PENDING_SIGN_OFF
        self.rc.save()
        self.assertIsNone(report_signatures(self.rc)["class_teacher"])

    def test_shared_cache_looks_each_person_up_once(self):
        self.rc.status = ReportCardStatus.PENDING_SIGN_OFF
        self.rc.save()
        shared = {}
        report_signatures(self.rc, shared)
        with self.assertNumQueries(0):
            report_signatures(self.rc, shared)


class SignaturePageTests(SignatureTestBase):
    URL = "hr:signature_list"

    def test_hos_sees_and_approves(self):
        sig = save_signature(self.teacher, png())
        self.client.force_login(self.hos)
        resp = self.client.get(reverse(self.URL))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Asha Teacher")
        self.assertContains(resp, "data:image/png;base64,")
        self.assertContains(resp, "Class teacher · Grade 4")
        self.client.post(reverse("hr:signature_review"), {"action": "approve", "ids": [sig.pk]})
        sig.refresh_from_db()
        self.assertEqual(sig.status, SignatureStatus.APPROVED)
        self.assertEqual(sig.reviewed_by, self.hos)

    def test_hr_uploads_but_cannot_approve(self):
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(reverse(self.URL)).status_code, 200)
        upload = SimpleUploadedFile("sig.png", png(), content_type="image/png")
        self.client.post(reverse("hr:signature_upload"), {"staff": self.teacher_profile.pk, "image": upload})
        sig = StaffSignature.objects.get(user=self.teacher)
        self.assertEqual(sig.status, SignatureStatus.PENDING)
        resp = self.client.post(reverse("hr:signature_review"), {"action": "approve", "ids": [sig.pk]})
        self.assertNotEqual(resp.status_code, 200)
        sig.refresh_from_db()
        self.assertEqual(sig.status, SignatureStatus.PENDING)

    def test_teacher_cannot_open_the_page(self):
        self.client.force_login(self.teacher)
        resp = self.client.get(reverse(self.URL))
        self.assertIn(resp.status_code, (302, 403))
