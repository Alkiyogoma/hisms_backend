"""Student list: totals by sex and the full (all pages) Excel/PDF export follow the filters."""
import io
from datetime import date
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from academics.models import AcademicYear, Department, GradeClass
from academics.tests import assign_role_group
from students.models import Student, StudentStatus
from users.models import User, UserRole


class StudentListSummaryExportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "list-admin", email="list-admin@example.test", password="x", role=UserRole.SUPER_ADMIN)
        assign_role_group(cls.admin)
        cls.hod = User.objects.create_user(
            "list-hod", email="list-hod@example.test", password="x", role=UserRole.PRIMARY_HOD)
        assign_role_group(cls.hod)

        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.old_year = AcademicYear.objects.create(name="2025/2026")
        GradeClass.objects.create(name="Grade 3", department=Department.PRIMARY)
        GradeClass.objects.create(name="Grade 8", department=Department.LOWER_SECONDARY)

        def make(n, gender, class_name="Grade 3", year=None, **extra):
            return Student.objects.create(
                admission_no=f"LST-{n:03d}", first_name=f"Name{n:03d}", last_name="Test",
                gender=gender, class_name=class_name, academic_year=year or cls.year, **extra)

        # Grade 3, current year: 20 boys + 15 girls = 35 (more than one 30-row page)
        n = 0
        for _ in range(20):
            n += 1
            make(n, "male")
        for _ in range(15):
            n += 1
            make(n, "female")
        # Grade 8: 2 boys, 1 girl, 1 unrecorded
        for g in ("male", "male", "female", ""):
            n += 1
            make(n, g, class_name="Grade 8")
        # Old year, Grade 8, graduated girl
        n += 1
        cls.graduate = make(n, "female", class_name="Grade 8", year=cls.old_year,
                            status=StudentStatus.GRADUATED, date_of_birth=date(2010, 1, 1))
        # Archived boy (hidden from the default "active" view)
        n += 1
        cls.archived = make(n, "male", is_archived=True)

    def setUp(self):
        self.client.force_login(self.admin)

    def summary(self, **params):
        resp = self.client.get(reverse("students:list"), params)
        self.assertEqual(resp.status_code, 200)
        return resp.context["summary"]

    # ── Summary cards ────────────────────────────────────────────
    def test_counts_by_sex_cover_all_pages(self):
        s = self.summary()
        self.assertEqual(s, {"total": 40, "boys": 22, "girls": 17})
        resp = self.client.get(reverse("students:list"))
        self.assertEqual(len(resp.context["students"]), 30)  # table is paginated, cards are not
        self.assertContains(resp, 'id="student-totals"')
        self.assertContains(resp, "Total students")

    def test_cards_follow_filters(self):
        self.assertEqual(self.summary(class_name="Grade 8"), {"total": 5, "boys": 2, "girls": 2})
        self.assertEqual(self.summary(academic_year=self.old_year.pk), {"total": 1, "boys": 0, "girls": 1})
        self.assertEqual(self.summary(status="graduated"), {"total": 1, "boys": 0, "girls": 1})
        self.assertEqual(self.summary(department=Department.PRIMARY), {"total": 35, "boys": 20, "girls": 15})
        self.assertEqual(self.summary(archive_status="archived"), {"total": 1, "boys": 1, "girls": 0})
        self.assertEqual(self.summary(archive_status="all"), {"total": 41, "boys": 23, "girls": 17})

    def test_cards_respect_role_scoping(self):
        self.client.force_login(self.hod)  # primary HOD: Grade 3 only
        self.assertEqual(self.summary(), {"total": 35, "boys": 20, "girls": 15})

    def test_export_links_carry_filters_without_page(self):
        resp = self.client.get(reverse("students:list"), {"class_name": "Grade 8", "page": "1"})
        self.assertContains(resp, f'{reverse("students:list_export")}?class_name=Grade+8&amp;format=xlsx')
        self.assertNotContains(resp, "page=1&amp;format")

    # ── Excel export ─────────────────────────────────────────────
    def xlsx_rows(self, **params):
        from openpyxl import load_workbook
        resp = self.client.get(reverse("students:list_export"), {**params, "format": "xlsx"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp["Content-Type"])
        self.assertIn(".xlsx", resp["Content-Disposition"])
        wb = load_workbook(io.BytesIO(resp.content))
        return [list(r) for r in wb["Students"].iter_rows(values_only=True)], wb

    def test_excel_export_is_unpaginated_with_required_columns(self):
        rows, wb = self.xlsx_rows()
        self.assertEqual(rows[0], ["Name", "Sex", "Age", "Grade", "Status"])
        self.assertEqual(len(rows) - 1, 40)  # every matching student, not just the first 30
        summary = {r[0]: r[1] for r in wb["Summary"].iter_rows(values_only=True)}
        self.assertEqual((summary["Total students"], summary["Boys"], summary["Girls"]), (40, 22, 17))

    def test_excel_export_follows_filters_and_shows_status_and_age(self):
        rows, _ = self.xlsx_rows(archive_status="all", class_name="Grade 8")
        body = rows[1:]
        self.assertEqual(len(body), 5)
        grad = next(r for r in body if r[0] == self.graduate.get_full_name())
        self.assertEqual(grad[1:], ["Female", self.graduate.age, "Grade 8", "Graduated"])

        rows, _ = self.xlsx_rows(archive_status="archived")
        self.assertEqual(rows[1:], [[self.archived.get_full_name(), "Male", None, "Grade 3", "Archived"]])

    def test_export_respects_role_scoping(self):
        self.client.force_login(self.hod)
        rows, _ = self.xlsx_rows(archive_status="all")
        self.assertEqual({r[3] for r in rows[1:]}, {"Grade 3"})

    def test_excel_falls_back_to_csv_without_openpyxl(self):
        with mock.patch.dict("sys.modules", {"openpyxl": None}):
            resp = self.client.get(reverse("students:list_export"), {"class_name": "Grade 8", "format": "xlsx"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])
        lines = resp.content.decode("utf-8-sig").strip().splitlines()
        self.assertEqual(lines[0], "Name,Sex,Age,Grade,Status")
        self.assertEqual(len(lines) - 1, 5)

    # ── PDF export ───────────────────────────────────────────────
    def test_pdf_export_contains_every_matching_student(self):
        captured = {}

        class FakeHTML:
            def __init__(self, string, base_url=None):
                captured["html"] = string

            def write_pdf(self):
                return b"%PDF-fake"

        fake_module = mock.MagicMock(HTML=FakeHTML)
        with mock.patch.dict("sys.modules", {"weasyprint": fake_module}):
            resp = self.client.get(reverse("students:list_export"), {"format": "pdf"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertIn(".pdf", resp["Content-Disposition"])
        html = captured["html"]
        self.assertEqual(html.count("<tr><td class=\"num\">"), 40)
        self.assertIn("Name040 Test", html)
        self.assertNotIn(self.archived.get_full_name(), html)

    def test_pdf_falls_back_to_print_view_without_engine(self):
        with mock.patch.dict("sys.modules", {"weasyprint": None}):
            resp = self.client.get(reverse("students:list_export"), {"format": "pdf", "status": "graduated"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Save as PDF")
        self.assertContains(resp, self.graduate.get_full_name())
        self.assertContains(resp, "window.print()")
