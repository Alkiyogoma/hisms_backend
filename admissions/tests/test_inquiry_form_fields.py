"""New-inquiry / edit-inquiry form fields: relationship options are not duplicated,
target enrolment year/month render with options, meeting time renders as a time
input, and the inquiry's current grade is shown and editable."""
import json
import re
from datetime import date

from django.conf import settings as django_settings
from django.test import TestCase
from django.urls import reverse

from academics.models import AcademicYear
from admissions.models import Applicant, ApplicantStatus
from admissions.tests.test_view_regressions import HTMX, make_applicant, make_user
from users.models import UserRole

if not getattr(django_settings, "AUDIT_LOG_DB_CONSTRAINT", True):  # see academics/tests.py
    _orig = TestCase._fixture_teardown

    def _patched(self):
        try:
            _orig(self)
        except Exception:
            pass

    TestCase._fixture_teardown = _patched


def _select_options(html, name):
    m = re.search(r'<select[^>]*name="%s"[^>]*>(.*?)</select>' % re.escape(name), html, re.S)
    assert m, f"select {name} not rendered"
    return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', m.group(1))


class InquiryFormFieldTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        AcademicYear.objects.create(name=str(date.today().year), is_current=True)
        cls.ao = make_user("ao", UserRole.ADMIN_OFFICER, is_superuser=True)

    def setUp(self):
        self.client.force_login(self.ao)

    def test_new_inquiry_page_fields(self):
        html = self.client.get(reverse("admissions:new_inquiry")).content.decode()

        labels = [lbl for val, lbl in _select_options(html, "parent_relationship") if val]
        self.assertEqual(labels, ["Father", "Mother", "Guardian", "Legal guardian", "Other"])

        years = [val for val, _ in _select_options(html, "target_enrollment_year") if val]
        this_year = date.today().year
        self.assertEqual(years, [str(y) for y in range(this_year, this_year + 4)])
        self.assertEqual(len([v for v, _ in _select_options(html, "target_enrollment_month") if v]), 12)

        self.assertRegex(html, r'<input type="time" name="preferred_meeting_time"')

    def test_edit_modal_normalises_legacy_relationship_and_shows_current_grade(self):
        applicant = make_applicant(
            ApplicantStatus.INQUIRY_RECEIVED,
            parent_relationship="Legal guardian",
            target_enrollment_year=2020,
            notes=json.dumps({"submitted_via": "staff_form", "current_grade": "Grade 2"}),
        )
        html = self.client.get(reverse("admissions:edit_inquiry", args=[applicant.pk]), **HTMX).content.decode()

        self.assertIn('<option value="legal_guardian" selected>', html)
        self.assertIn('<option value="2020" selected>', html)
        self.assertRegex(html, r'name="current_grade"[^>]*value="Grade 2"')

        detail = self.client.get(reverse("admissions:detail", args=[applicant.pk])).content.decode()
        self.assertIn("Current Grade", detail)
        self.assertEqual(Applicant.objects.get(pk=applicant.pk).current_grade, "Grade 2")
