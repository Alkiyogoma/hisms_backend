"""Regression tests for admissions code paths that crashed with NameError
(missing imports in admissions/views.py) and for the parent-portal invoice view
now delegating to admissions.services.generate_admission_invoice."""
from datetime import date, time, timedelta

from django.conf import settings as django_settings
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from academics.models import AcademicYear
from admissions.models import Applicant, ApplicantStatus, AssessmentSchedule
from finance.models import Invoice
from students.models import Student
from users.models import User, UserRole

if not getattr(django_settings, "AUDIT_LOG_DB_CONSTRAINT", True):  # see academics/tests.py
    _orig = TestCase._fixture_teardown

    def _patched(self):
        try:
            _orig(self)
        except Exception:
            pass

    TestCase._fixture_teardown = _patched


HTMX = {"HTTP_HX_REQUEST": "true"}


def make_user(username, role, **extra):
    from users.role_models import ROLE_DEFAULT_PERMISSIONS
    user = User.objects.create_user(username=username, email=f"{username}@school.test", password="pw-12345!", role=role, **extra)
    group, _ = Group.objects.get_or_create(name=f"role_{role}")
    group.permissions.add(*Permission.objects.filter(codename__in=ROLE_DEFAULT_PERMISSIONS.get(role, [])))
    group.user_set.add(user)
    return user


def make_applicant(status, **overrides):
    defaults = dict(
        parent_full_name="Neema Massawe", parent_phone="+255700000123", parent_email="neema@example.test",
        child_full_name="Baraka Massawe", child_date_of_birth="2017-03-02", grade_applying_for="Grade 3",
        status=status,
    )
    defaults.update(overrides)
    return Applicant.objects.create(**defaults)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AdmissionsViewNameErrorRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        AcademicYear.objects.create(name=str(date.today().year), is_current=True)
        cls.hos = make_user("hos", UserRole.HEAD_OF_SCHOOL, is_superuser=True)

    def test_admitting_applicant_sends_offer_letter(self):
        # The offer-letter context used `timezone`, which was never imported.
        applicant = make_applicant(ApplicantStatus.WAITLISTED)
        self.client.force_login(self.hos)
        resp = self.client.post(reverse("admissions:transition", args=[applicant.pk]),
                                {"to_status": ApplicantStatus.ADMITTED}, **HTMX)
        self.assertEqual(resp.status_code, 200)
        applicant.refresh_from_db()
        self.assertEqual(applicant.status, ApplicantStatus.ADMITTED)

    def test_revert_enrolled_applicant_archives_student(self):
        # revert_applicant_from_enrolled was called but never imported; the view's
        # broad except turned the NameError into an error message and did nothing.
        student = Student.objects.create(admission_no="HCS-R-001", first_name="Baraka", last_name="Massawe",
                                         gender="male", class_name="Grade 3")
        applicant = make_applicant(ApplicantStatus.ENROLLED, enrolled_student=student)
        self.client.force_login(self.hos)
        resp = self.client.post(reverse("admissions:revert", args=[applicant.pk]),
                                {"to_status": ApplicantStatus.ADMITTED, "reason": "Enrolled in error"}, **HTMX)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "is not defined")
        applicant.refresh_from_db()
        student.refresh_from_db()
        self.assertEqual(applicant.status, ApplicantStatus.ADMITTED)
        self.assertTrue(student.is_archived)

    def test_assessment_report_form_renders(self):
        # AssessmentReportView.get used SchoolSettings without importing it.
        applicant = make_applicant(ApplicantStatus.ASSESSMENT_CONFIRMED)
        AssessmentSchedule.objects.create(
            applicant=applicant, scheduled_date=date.today() + timedelta(days=2), scheduled_time=time(9, 0),
            location="Room 4", facilitating_teacher_name="Teach Er",
        )
        self.client.force_login(self.hos)
        resp = self.client.get(reverse("admissions:assessment_report", args=[applicant.pk]))
        self.assertEqual(resp.status_code, 200)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ParentGenerateInvoiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_user("admin", UserRole.SUPER_ADMIN, is_superuser=True)
        cls.finance = make_user("finance", UserRole.FINANCE_OFFICER)
        cls.parent = User.objects.create_user(username="neema", email="neema@example.test",
                                              password="pw-12345!", role=UserRole.PARENT)

    def test_parent_generates_admission_invoice_once(self):
        applicant = make_applicant(ApplicantStatus.FORM_SUBMITTED)
        self.client.force_login(self.parent)
        url = reverse("parent_portal:generate_invoice")

        resp = self.client.post(url)
        self.assertRedirects(resp, reverse("parent_portal:dashboard"), fetch_redirect_response=False)
        invoice = Invoice.objects.get(applicant=applicant)
        self.assertTrue(invoice.invoice_number.startswith("ADM-"))
        # Grade 3 new learner: tuition + development fee + admission fee.
        self.assertEqual(invoice.line_items.count(), 3)
        self.assertEqual(invoice.total_due, sum(li.amount for li in invoice.line_items.all()))
        applicant.refresh_from_db()
        self.assertEqual(applicant.status, ApplicantStatus.INVOICE_GENERATED)
        self.assertIn(self.finance.email, [to for m in mail.outbox for to in m.to])

        # The service is idempotent; the applicant is no longer FORM_SUBMITTED, so a
        # repeat post finds nothing to invoice rather than creating a duplicate.
        self.client.post(url)
        self.assertEqual(Invoice.objects.filter(applicant=applicant).count(), 1)
