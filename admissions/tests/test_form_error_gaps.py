"""
Admission forms must show why a save failed — including errors that are not
tied to one field — and keep what was typed.
"""
from django import forms
from django.contrib.messages import get_messages
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from tasks.models import Task
from users.models import User, UserRole


class ErrorSummaryPartialTests(TestCase):
    def test_lists_non_field_and_field_errors(self):
        class F(forms.Form):
            child_full_name = forms.CharField(label="Child Name")
            token = forms.CharField(widget=forms.HiddenInput, label="Sibling link")

        form = F(data={"token": ""})
        form.is_valid()
        form.add_error(None, "Selected grade is not active. Please choose a valid grade from dropdown.")
        html = render_to_string("_partials/form_error_summary.html", {"form": form})
        self.assertIn("Selected grade is not active", html)
        self.assertIn('href="#id_child_full_name"', html)
        self.assertIn("Child Name", html)
        self.assertIn("Sibling link: This field is required.", html)  # hidden fields are not silent

    def test_renders_nothing_without_errors(self):
        class F(forms.Form):
            x = forms.CharField(required=False)
        form = F(data={})
        form.is_valid()
        self.assertEqual(render_to_string("_partials/form_error_summary.html", {"form": form}).strip(), "")


class InquiryCreateErrorTests(TestCase):
    def setUp(self):
        self.ao = User.objects.create_user(
            username="ao", email="ao@example.test", password="x",
            role=UserRole.ADMIN_OFFICER, is_superuser=True,
        )
        self.client.force_login(self.ao)

    def test_empty_inquiry_names_fields_and_shows_summary(self):
        resp = self.client.post(reverse("admissions:new_inquiry"), {"child_full_name": "Asha Mushi"})
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn("form-error-summary", content)
        self.assertIn('value="Asha Mushi"', content)
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any(m.startswith("The inquiry details were not saved: please fix") for m in msgs), msgs)


class AssessmentReportTaskTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="t1", email="t1@example.test", password="x", role=UserRole.TEACHER,
        )
        self.task = Task.objects.create(
            task_type="assessment_report", title="Assessment report", assigned_to=self.teacher,
        )
        self.url = reverse("tasks:assessment_report_submit", args=[self.task.pk])
        self.client.force_login(self.teacher)

    def _post(self, action, **data):
        return self.client.post(self.url, {"action": action, **data}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    def test_empty_report_cannot_be_submitted(self):
        resp = self._post("submit_report")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIn("teacher_comments", body["errors"])
        self.assertIn("Supervisor's Comment is required.", body["error"])
        self.task.refresh_from_db()
        self.assertNotEqual(self.task.status, "completed")

    def test_out_of_range_percentage_is_named(self):
        resp = self._post("submit_report", pct_math_eot="150", teacher_comments="Settled well.")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["errors"], {
            "pct_math_eot": "End of Term Mathematics: 150% must be between 0 and 100.",
        })

    def test_draft_keeps_valid_entries_and_reports_the_rest(self):
        resp = self._post("save_draft", pct_math_eot="150", pct_english_eot="72", teacher_comments="Draft")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertIn("pct_math_eot", body["errors"])
        self.task.refresh_from_db()
        data = self.task.metadata["assessment_data"]
        self.assertEqual(data["pct_english_eot"], "72")
        self.assertEqual(data["pct_math_eot"], "")

    def test_complete_report_submits(self):
        resp = self._post("submit_report", pct_math_eot="81", teacher_comments="Confident reader.")
        self.assertEqual(resp.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "completed")
