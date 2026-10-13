"""The personal data export (DSAR): every section actually exports the learner's
or guardian's records.

Most sections swallow query errors and export ``[]``, so a renamed model field
would quietly empty a section. These tests give each section one record and
require it to come back.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

import academics.tests  # noqa: F401  (applies the shared fixture-teardown shim)
from academics.models import AcademicYear, ECDEvaluation, ExamScore, ReportCard, Term
from admissions.models import Applicant
from attendance.models import AttendanceEntry
from audit.models import AuditLog
from audit.services.dsar_export import build_dsar_sections, export_guardian_data, export_student_data
from communications.models import Notification
from discipline.models import DisciplineIncident, IncidentSeverity
from finance.models import Concession, Invoice, InvoiceLineItem, Payment
from students.models import LaravelParent, ParentGuardian, Student, StudentGuardian, StudentSibling
from users.models import User, UserRole
from welfare.models import WelfareObservation

TODAY = date.today()


class DSARExportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("dsar_staff", "dsar_staff@x.edu", "pw", role=UserRole.SUPER_ADMIN)
        year = AcademicYear.objects.create(name="2026", is_current=True)
        term = Term.objects.create(academic_year=year, name="Term 1",
                                   start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=30))
        cls.student = student = Student.objects.create(
            admission_no="ADM-DSAR", first_name="Neema", last_name="Kweka", class_name="Grade 3")
        sibling = Student.objects.create(admission_no="ADM-DSAR2", first_name="Baraka", last_name="Kweka",
                                         class_name="Grade 1")
        cls.guardian = guardian = ParentGuardian.objects.create(full_name="Rehema Kweka", phone="0700111222",
                                                                email="rehema@x.test")
        StudentGuardian.objects.create(student=student, guardian=guardian, relationship="mother", is_primary=True)
        StudentSibling.objects.create(student_a=student, student_b=sibling)
        LaravelParent.objects.create(guardian=guardian, first_name="Rehema", last_name="Kweka")

        AttendanceEntry.objects.create(student=student, date=TODAY, class_name="Grade 3", status="present",
                                       marked_by=cls.staff)

        invoice = Invoice.objects.create(student=student, term=term, invoice_number="INV-DSAR-1",
                                         amount_due=Decimal("100000"), total_due=Decimal("100000"))
        InvoiceLineItem.objects.create(invoice=invoice, description="Tuition", amount=Decimal("100000"))
        Payment.objects.create(invoice=invoice, amount=Decimal("50000"), method="cash", created_by=cls.staff)
        Concession.objects.create(student=student, term=term, concession_type="sibling",
                                  discount_value=Decimal("10"), created_by=cls.staff)

        ExamScore.objects.create(student=student, term=term, subject_name="Mathematics", exam_type="mid_term",
                                 score=Decimal("78"), entered_by=cls.staff)
        card = ReportCard.objects.create(student=student, term=term, generated_by=cls.staff)
        ECDEvaluation.objects.create(report_card=card, domain="Language", rating="G")

        DisciplineIncident.objects.create(student=student, reported_by=cls.staff, severity=IncidentSeverity.LOW,
                                          summary="Late to class.", incident_date=TODAY)
        WelfareObservation.all_objects.create(student=student, submitted_by=cls.staff, concern_type="health",
                                              observation_date=TODAY, observation_text="Headache after lunch.")

        Applicant.objects.create(parent_full_name="Rehema Kweka", parent_phone="0700111222",
                                 child_full_name="Neema Kweka", grade_applying_for="Grade 3",
                                 enrolled_student=student)
        Notification.objects.create(recipient=cls.staff, title="Attendance", body="Neema arrived late today.")
        AuditLog.objects.create(actor=cls.staff, action_type="UPDATE", model_name="Student",
                                object_id=str(student.pk), description="Updated class")
        AuditLog.objects.create(actor=cls.staff, action_type="UPDATE", model_name="ParentGuardian",
                                object_id=str(guardian.pk), description="Updated phone")

    def test_student_export_has_every_section(self):
        data = export_student_data(self.student)
        sections = {
            "guardians": data["guardians"],
            "siblings": data["siblings"],
            "attendance_history": data["attendance_history"],
            "invoices": data["financial_records"]["invoices"],
            "invoice line items": data["financial_records"]["invoices"][0]["line_items"],
            "invoice payments": data["financial_records"]["invoices"][0]["payments"],
            "concessions": data["financial_records"]["concessions"],
            "exam_scores": data["academic_records"]["exam_scores"],
            "report_cards": data["academic_records"]["report_cards"],
            "ecd_evaluations": data["academic_records"]["ecd_evaluations"],
            "discipline_records": data["discipline_records"],
            "welfare_records": data["welfare_records"],
            "admissions_history": data["admissions_history"],
            "notifications": data["notifications"],
            "audit_trail": data["audit_trail"],
        }
        empty = [name for name, rows in sections.items() if not rows]
        self.assertEqual(empty, [], f"These sections exported nothing: {empty}")

    def test_student_export_carries_the_record_contents(self):
        data = export_student_data(self.student)
        self.assertEqual(data["attendance_history"][0]["status"], "present")
        self.assertEqual(data["academic_records"]["exam_scores"][0]["subject_name"], "Mathematics")
        self.assertEqual(data["academic_records"]["ecd_evaluations"][0]["domain"], "Language")
        self.assertEqual(data["welfare_records"][0]["observation_text"], "Headache after lunch.")
        self.assertEqual(data["discipline_records"][0]["summary"], "Late to class.")
        self.assertEqual(data["notifications"][0]["body"], "Neema arrived late today.")
        self.assertEqual(data["siblings"][0]["sibling_admission_no"], "ADM-DSAR2")

    def test_student_export_flattens_for_download(self):
        sections = build_dsar_sections(export_student_data(self.student))
        self.assertTrue(sections)
        self.assertTrue(all(isinstance(rows, list) for rows in sections.values()))

    def test_guardian_export_has_every_section(self):
        data = export_guardian_data(self.guardian)
        self.assertEqual([s["admission_no"] for s in data["linked_students"]], ["ADM-DSAR"])
        self.assertEqual(data["laravel_parent_record"]["first_name"], "Rehema")
        self.assertTrue(data["audit_trail"], "The guardian's audit trail exported nothing")
        self.assertTrue(data["pdpa_consent"])
