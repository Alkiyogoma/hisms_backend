"""Staff reminders (E17, E20) send one email per person, not an alert plus the template."""
from datetime import timedelta

from django.conf import settings as django_settings
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from admissions.models import Applicant, ApplicantStatus
from admissions.tasks import send_form_outstanding_reminder_task, send_invoice_30day_reminder_task
from communications.models import Notification
from users.models import User, UserRole

if not getattr(django_settings, "AUDIT_LOG_DB_CONSTRAINT", True):  # see academics/tests.py
    _orig = TestCase._fixture_teardown

    def _patched(self):
        try:
            _orig(self)
        except Exception:
            pass

    TestCase._fixture_teardown = _patched


def make_applicant(status, age):
    app = Applicant.objects.create(
        parent_full_name="Neema Massawe", parent_phone="+255700000123", parent_email="neema@example.test",
        child_full_name="Baraka Massawe", child_date_of_birth="2017-03-02", grade_applying_for="Grade 3",
        status=status,
    )
    # updated_at is auto_now; backdate it with a queryset update.
    Applicant.objects.filter(pk=app.pk).update(updated_at=timezone.now() - age)
    return app


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SITE_URL="https://school.test")
class StaffReminderEmailTests(TestCase):
    def test_form_outstanding_sends_one_email_per_officer(self):
        officer = User.objects.create_user(
            username="office", email="office@school.test", password="pw-12345!", role=UserRole.ADMIN_OFFICER)
        make_applicant(ApplicantStatus.ADMITTED, timedelta(days=14, hours=6))

        send_form_outstanding_reminder_task()

        to_officer = [m for m in mail.outbox if officer.email in m.to]
        self.assertEqual(len(to_officer), 1)
        self.assertIn("Form outstanding", to_officer[0].subject)
        self.assertTrue(Notification.objects.filter(recipient=officer).exists())

    def test_invoice_30_day_sends_one_email_per_staff_member(self):
        finance = User.objects.create_user(
            username="finance", email="finance@school.test", password="pw-12345!", role=UserRole.FINANCE_OFFICER)
        make_applicant(ApplicantStatus.INVOICE_GENERATED, timedelta(days=31))

        send_invoice_30day_reminder_task()

        self.assertEqual(len([m for m in mail.outbox if finance.email in m.to]), 1)
        self.assertTrue(Notification.objects.filter(recipient=finance).exists())
