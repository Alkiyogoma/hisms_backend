from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings

from communications.email_layout import (
    format_message_html,
    looks_like_html,
    render_branded_email,
    render_plain_email,
    section_for_link,
)


@override_settings(SITE_URL="https://school.example")
class BrandedEmailLayoutTests(TestCase):
    def test_label_value_lines_become_a_details_table(self):
        html, cta = format_message_html(
            "Unconfirmed attendance as of 09:00 today.\n\nGrade 5 (1): Maria Andrea\nGrade 6 (1): Eben M"
        )
        self.assertIn("Grade 5 (1)", html)
        self.assertIn("Maria Andrea", html)
        self.assertEqual(html.count("<table"), 1)
        self.assertEqual(cta, "")

    def test_trailing_view_line_becomes_the_button(self):
        html, cta = format_message_html("Form outstanding.\n\nView the application: /admissions/applicant/3/")
        self.assertEqual(cta, "https://school.example/admissions/applicant/3/")
        self.assertNotIn("View the application", html)

    def test_text_is_escaped(self):
        html, _ = format_message_html("<script>alert(1)</script>")
        self.assertNotIn("<script>", html)

    def test_full_email_has_branding_greeting_and_absolute_link(self):
        user = type("U", (), {"first_name": "Amina"})()
        out = render_branded_email("Unconfirmed Attendance", "Two students.", "/attendance/", recipient=user)
        self.assertIn("Dear Amina,", out)
        self.assertIn('href="https://school.example/attendance/"', out)
        self.assertIn("branding/hodari-logo-white.png", out)
        self.assertIn("Attendance", out)  # section label from the link

    def test_plain_version_has_signature_and_absolute_link(self):
        out = render_plain_email("Hello there.", "/attendance/")
        self.assertIn("View details: https://school.example/attendance/", out)
        self.assertIn("Warm regards,", out)

    def test_helpers(self):
        self.assertEqual(section_for_link("/finance/invoices/1/"), "Finance")
        self.assertEqual(section_for_link("/unknown/"), "")
        self.assertTrue(looks_like_html("<p>x</p>"))
        self.assertFalse(looks_like_html("Grade 5: 3 < 4"))


@override_settings(
    SITE_URL="https://school.example",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class SendEmailSafeBrandingTests(TestCase):
    def _send(self, **kwargs):
        from communications.email_service import send_email_safe
        locmem = {"delivering": True, "detail": ""}
        with patch("core.email_backend.resolve_delivery",
                   return_value=(None, "noreply@school.example", locmem)):
            return send_email_safe(to_email="a@example.com", subject="Subject", actor=None, **kwargs)

    def test_plain_text_is_wrapped_in_the_branded_layout(self):
        self.assertTrue(self._send(body="Line one\nLine two"))
        html, mime = mail.outbox[-1].alternatives[0]
        self.assertEqual(mime, "text/html")
        self.assertIn("Line one<br>Line two", html)
        self.assertIn("<!DOCTYPE html>", html)

    def test_existing_html_is_left_alone(self):
        self.assertTrue(self._send(body="plain", html_body="<p>Custom</p>"))
        self.assertEqual(mail.outbox[-1].alternatives[0][0], "<p>Custom</p>")
