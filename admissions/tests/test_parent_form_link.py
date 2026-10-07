import json
import re
import shutil
import tempfile
from datetime import date, timedelta

from django.conf import settings as django_settings
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from academics.models import AcademicYear, Department, GradeClass
from admissions.models import (
    AdmissionFormInvite, Applicant, ApplicantStatus, EntryRoute,
)
from admissions.parent_form import start_parent_form
from finance.models import Invoice
from users.models import User, UserRole

if not getattr(django_settings, "AUDIT_LOG_DB_CONSTRAINT", True):  # see academics/tests.py
    _orig = TestCase._fixture_teardown

    def _patched(self):
        try:
            _orig(self)
        except Exception:
            pass

    TestCase._fixture_teardown = _patched


def make_user(username, role):
    from users.role_models import ROLE_DEFAULT_PERMISSIONS
    user = User.objects.create_user(username=username, email=f"{username}@school.test", password="pw-12345!", role=role)
    group, _ = Group.objects.get_or_create(name=f"role_{role}")
    group.permissions.add(*Permission.objects.filter(codename__in=ROLE_DEFAULT_PERMISSIONS.get(role, [])))
    group.user_set.add(user)
    return user


def upload(name="doc.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


def form_data(**child):
    c = {"name": "Neema Kweka", "dob": "2017-02-03", "gender": "Female", "nationality": "Tanzanian",
         "religion": "Christian", "grade": "Grade 3", "prevSchool": "Kawe Primary", "allergies": "Peanuts",
         "isNew": True, "uniform": {}}
    c.update(child)
    return {
        "children": [c],
        "guardians": [{"name": "Rehema Kweka", "phone": "+255711000111", "email": "rehema@example.test", "rel": "Mother"}],
        "consent": {"core": True}, "step": 9,
    }


_MEDIA = tempfile.mkdtemp(prefix="hisms-test-media-")


# Emails are sent in-process; the SMS/notification side effects aren't under test.
@patch("communications.email_service.dispatch_notification", lambda *a, **k: None)
@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SITE_URL="https://school.test",
    MEDIA_ROOT=_MEDIA,
)
class ParentFormLinkTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        AcademicYear.objects.create(name=str(date.today().year), is_current=True)
        GradeClass.objects.create(name="Grade 3", department=Department.PRIMARY)
        cls.officer = make_user("office", UserRole.ADMIN_OFFICER)

    def start(self):
        self.client.force_login(self.officer)
        resp = self.client.post(reverse("admissions:send_parent_form"), {
            "parent_full_name": "Rehema Kweka", "parent_email": "rehema@example.test", "parent_phone": "+255711000111",
        })
        applicant = Applicant.objects.get()
        self.assertRedirects(resp, reverse("admissions:detail", args=[applicant.pk]), fetch_redirect_response=False)
        link = re.search(r"https://school\.test(/admissions/form/[^\s]+/)", mail.outbox[-1].body).group(1)
        self.client.logout()
        return applicant, link

    def post(self, link, action, data, **files):
        payload = {"action": action, "data": json.dumps(data)}
        payload.update({f"file_{k}": v for k, v in files.items()})
        return self.client.post(link, payload)

    def test_admin_starts_application_with_contact_details_only(self):
        applicant, link = self.start()
        self.assertEqual(applicant.entry_route, EntryRoute.PARENT_LINK)
        self.assertEqual(applicant.status, ApplicantStatus.ADMITTED)
        invite = applicant.form_invite
        self.assertEqual(invite.status, "sent")
        self.assertNotIn(link.split("/")[-2], invite.token_hash)  # only the hash is stored
        self.assertEqual(mail.outbox[-1].to, ["rehema@example.test"])

    def test_parent_saves_and_returns_then_submits(self):
        applicant, link = self.start()
        self.assertEqual(self.client.get(link).status_code, 200)
        invite = AdmissionFormInvite.objects.get()
        self.assertEqual(invite.status, "in_progress")

        # Save part-way with one document, come back later.
        resp = self.post(link, "save_draft", dict(form_data(), step=6), birth=upload("birth.pdf"))
        self.assertTrue(resp.json()["ok"])
        self.assertIn("birth", resp.json()["uploaded"])
        self.assertContains(self.client.get(link), "birth.pdf")

        # Missing required documents are refused.
        resp = self.post(link, "submit_admission", form_data())
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Passport photo", resp.json()["error"])

        resp = self.post(link, "submit_admission", form_data(), photo=upload("p.jpg"), report=upload("r.pdf"))
        self.assertTrue(resp.json()["ok"], resp.json())

        applicant.refresh_from_db()
        self.assertEqual(applicant.child_full_name, "Neema Kweka")
        self.assertEqual(applicant.child_date_of_birth, date(2017, 2, 3))
        self.assertEqual(applicant.status, ApplicantStatus.INVOICE_GENERATED)
        self.assertEqual(AdmissionFormInvite.objects.get().status, "submitted")
        self.assertTrue(Invoice.objects.filter(applicant=applicant, invoice_number__startswith="ADM-").exists())
        self.assertIn("We've received the admission form", mail.outbox[-1].subject)

        # The form is now read-only for the parent.
        self.assertEqual(self.post(link, "save_draft", form_data()).status_code, 400)

    def test_non_tanzanian_needs_passport_or_permit(self):
        _applicant, link = self.start()
        resp = self.post(link, "submit_admission", form_data(nationality="Kenyan"),
                         birth=upload(), photo=upload("p.png"), report=upload())
        self.assertIn("Passport or residence permit", resp.json()["error"])

    def test_svg_upload_rejected(self):
        _applicant, link = self.start()
        resp = self.post(link, "save_draft", form_data(), birth=SimpleUploadedFile("x.svg", b"<svg/>"))
        self.assertEqual(resp.status_code, 400)

    def test_admin_requests_changes_then_accepts(self):
        applicant, link = self.start()
        self.post(link, "submit_admission", form_data(prevSchool="None"), birth=upload(), photo=upload("p.jpg"))
        self.client.force_login(self.officer)
        self.client.post(reverse("admissions:parent_form_review", args=[applicant.pk]),
                         {"action": "request_changes", "message": "Birth certificate is blurred"})
        invite = AdmissionFormInvite.objects.get()
        self.assertEqual(invite.status, "changes_requested")
        new_link = re.search(r"https://school\.test(/admissions/form/[^\s]+/)", mail.outbox[-1].body).group(1)
        self.assertNotEqual(new_link, link)
        self.client.logout()
        self.assertEqual(self.client.get(link).status_code, 404)  # old link no longer works
        self.assertContains(self.client.get(new_link), "Birth certificate is blurred")

        resp = self.post(new_link, "submit_admission", form_data(prevSchool="None"), birth=upload("clear.pdf"))
        self.assertTrue(resp.json()["ok"], resp.json())
        self.assertEqual(Invoice.objects.filter(applicant=applicant).count(), 1)  # not re-invoiced

        self.client.force_login(self.officer)
        self.client.post(reverse("admissions:parent_form_review", args=[applicant.pk]), {"action": "accept"})
        invite.refresh_from_db()
        self.assertEqual(invite.status, "accepted")
        self.assertTrue(applicant.documents.get(document_type="birth_certificate").is_received)

    def test_form_details_fill_student_record_on_enrolment(self):
        from admissions.parent_form import student_details_from_form
        applicant, link = self.start()
        self.post(link, "submit_admission", form_data(prevSchool="None"), birth=upload(), photo=upload("p.jpg"))
        applicant.refresh_from_db()
        details = student_details_from_form(applicant)
        self.assertEqual(details["gender"], "female")
        self.assertEqual(details["nationality"], "Tanzanian")
        self.assertIn("Peanuts", details["allergies_medical"])

    def test_expired_and_unknown_links(self):
        _applicant, link = self.start()
        AdmissionFormInvite.objects.update(expires_at=timezone.now() - timedelta(days=1))
        self.assertEqual(self.client.get(link).status_code, 410)
        self.assertEqual(self.client.get("/admissions/form/not-a-real-token/").status_code, 404)


@patch("communications.email_service.dispatch_notification", lambda *a, **k: None)
@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SITE_URL="https://school.test",
    MEDIA_ROOT=_MEDIA,
)
class AdmissionInvoiceConsistencyTests(TestCase):
    """The form, the invoice, the emails and Finance show one total and one
    invoice number. Regression: ADM-0009 showed TSh 5,615,000 on screen but
    the email said TZS 4,917,500 (the form hard-coded a 700,000 admission fee
    while the server used School Settings)."""

    @classmethod
    def setUpTestData(cls):
        AcademicYear.objects.create(name=str(date.today().year), is_current=True)
        GradeClass.objects.create(name="Grade 6", department=Department.PRIMARY)
        cls.officer = make_user("office", UserRole.ADMIN_OFFICER)
        from core.models import SchoolSettings
        ss = SchoolSettings.get_settings()
        ss.admission_fee = 2500  # a non-default value the form used to ignore
        ss.save()

    start = ParentFormLinkTests.start
    post = ParentFormLinkTests.post

    def test_one_total_and_number_everywhere(self):
        from admissions.fees import child_lines, fee_schedule
        _applicant, link = self.start()
        page = self.client.get(link)
        schedule = json.loads(page.context["fee_schedule_json"])
        self.assertEqual(schedule["admission"], 2500)

        child = {"grade": "Grade 6", "isNew": True, "breakfast": True,
                 "uniform": {"polo": 3, "sweater": 1, "tee": 2}}
        expected = sum(l["amount"] for l in child_lines(child, fee_schedule()))
        self.assertEqual(expected, 3_500_000 + 700_000 + 2_500 + 300_000 + 300_000 + 60_000 + 25_000 + 30_000)

        resp = self.post(link, "submit_admission", form_data(**child),
                         birth=upload("b.pdf"), photo=upload("p.jpg"), report=upload("r.pdf"))
        shown = resp.json()["invoice"]
        invoice = Invoice.objects.get()

        self.assertEqual(shown["total"], expected)
        self.assertEqual(invoice.total_due, expected)
        self.assertEqual(sum(l["amount"] for l in shown["lines"]), expected)
        self.assertEqual(shown["number"], invoice.invoice_number)

        amount = f"{expected:,.0f}"
        bodies = [m.body for m in mail.outbox if invoice.invoice_number in m.body]
        self.assertTrue(bodies)
        for body in bodies:
            self.assertIn(amount, body)

        # Re-opening the submitted form shows the stored invoice, not a re-estimate.
        reopened = json.loads(self.client.get(link).context["invoice_json"])
        self.assertEqual(reopened, shown)
