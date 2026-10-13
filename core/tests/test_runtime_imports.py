"""Lines that used names they never imported: each raised NameError the first
time it ran. These tests run those lines."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import LessonPlanDeadline
from users.models import User, UserRole


class LessonPlanDeadlineValidationTest(TestCase):
    """core/models.py: clean() raised NameError instead of ValidationError."""

    def test_weekend_deadline_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "between 0 (Monday) and 4 (Friday)"):
            LessonPlanDeadline(deadline_day=5).clean()

    def test_second_active_deadline_is_rejected_by_clean(self):
        owner = User.objects.create_user("rt_owner", "rt_owner@x.edu", "pw", role=UserRole.SUPER_ADMIN)
        LessonPlanDeadline.objects.create(deadline_day=0, is_active=True, created_by=owner)
        with self.assertRaisesMessage(ValidationError, "Only one deadline configuration"):
            LessonPlanDeadline(deadline_day=1, is_active=True).clean()


class MigrationTriggerViewTest(TestCase):
    """academics/views/admin.py: call_command was never imported."""

    @override_settings(DEBUG=True)
    def test_runs_migrations_for_superuser_in_debug(self):
        admin = User.objects.create_superuser("rt_root", "rt_root@x.edu", "pw")
        self.client.force_login(admin)
        with patch("academics.views.admin.call_command") as call:
            resp = self.client.get(reverse("academics:force_migrate"))
        self.assertContains(resp, "Migrations successfully executed.")
        self.assertEqual(call.call_count, 4)


class MidTermInvoiceNotificationTest(TestCase):
    """finance/services.py: ParentGuardian and send_parent_notification were
    never imported, so the first mid-term invoice with a parent crashed."""

    def test_parent_notified_when_no_email_template(self):
        from academics.models import AcademicYear, Term
        from finance.models import FeeStructure, FeeStructureItem, Invoice
        from finance.services import generate_midterm_invoices
        from students.models import ParentGuardian, Student, StudentGuardian

        today = timezone.localdate()
        year = AcademicYear.objects.create(name="2026", is_current=True)
        term = Term.objects.create(academic_year=year, name="Term 2",
                                   start_date=today - timedelta(days=28), end_date=today + timedelta(days=56))
        fs = FeeStructure.objects.create(term=term, class_name="Grade 3", is_active=True)
        FeeStructureItem.objects.create(structure=fs, description="Tuition", amount=Decimal("900000"))
        student = Student.objects.create(admission_no="ADM-MID", first_name="Zawadi", last_name="Moshi",
                                         class_name="Grade 3", enrolment_date=today - timedelta(days=1))
        guardian = ParentGuardian.objects.create(full_name="Asha Moshi", phone="0711000111",
                                                 email="asha@x.test")
        StudentGuardian.objects.create(student=student, guardian=guardian, relationship="mother", is_primary=True)
        actor = User.objects.create_user("rt_fin", "rt_fin@x.edu", "pw", role=UserRole.FINANCE_OFFICER)

        with patch("core.email_templates.send_dynamic_email", return_value=False), \
                patch("communications.email_service.send_parent_notification") as notify:
            result = generate_midterm_invoices(term, actor)

        self.assertEqual(result["generated"], 1)
        self.assertTrue(Invoice.objects.filter(student=student, term=term).exists())
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs["guardian"], guardian)


class AdmissionInvoiceReminderTest(TestCase):
    """admissions/tasks.py: send_email_safe was never imported, so the 30-day
    reminder crashed whenever no email template was set up."""

    def test_fallback_email_sent_to_staff(self):
        from admissions.models import Applicant, ApplicantStatus
        from admissions.tasks import send_invoice_30day_reminder_task

        User.objects.create_user("rt_ao", "rt_ao@x.edu", "pw", role=UserRole.ADMIN_OFFICER)
        app = Applicant.objects.create(parent_full_name="Juma Ally", parent_phone="0722000222",
                                       child_full_name="Halima Ally", grade_applying_for="Grade 1",
                                       status=ApplicantStatus.INVOICE_GENERATED)
        Applicant.objects.filter(pk=app.pk).update(updated_at=timezone.now() - timedelta(days=31))

        with patch("core.email_templates.send_dynamic_email", return_value=False), \
                patch("communications.email_service.dispatch_notification"), \
                patch("communications.email_service.send_email_safe") as send:
            send_invoice_30day_reminder_task()

        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["to_email"], "rt_ao@x.edu")
        self.assertIn("Halima Ally", send.call_args.kwargs["subject"])
