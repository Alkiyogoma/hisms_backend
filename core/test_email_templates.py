"""Email templates: On means it is sent, Off means it is never sent."""
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from communications.email_service import send_email_safe
from communications.models import EmailSendLog
from core.email_templates import reset_template, seed_default_templates, send_dynamic_email
from core.models import EmailTemplate, SchoolSettings
from users.models import User, UserRole

CTX = {"user": {"first_name": "Asha", "username": "asha"}, "temp_password": "x", "login_url": "/l/"}


class SendDynamicEmailTests(TestCase):
    def setUp(self):
        seed_default_templates()

    def test_enabled_template_is_sent(self):
        r = send_dynamic_email("activation", "a@x.test", CTX)
        self.assertTrue(r.sent)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("asha", mail.outbox[0].subject)

    def test_disabled_template_is_never_sent_even_by_fallbacks(self):
        EmailTemplate.objects.filter(template_type="activation").update(is_enabled=False)
        r = send_dynamic_email("activation", "a@x.test", CTX)
        self.assertTrue(r.disabled)
        self.assertFalse(r.sent)
        # Call sites do `if not result: <hard-coded fallback>`; a disabled template must not trigger it.
        self.assertTrue(bool(r))
        self.assertEqual(len(mail.outbox), 0)

    def test_missing_template_is_recreated_and_sent(self):
        EmailTemplate.objects.filter(template_type="password_reset").delete()
        r = send_dynamic_email("password_reset", "a@x.test", {"reset_url": "/r/", "expiry_hours": 1, "user": {"username": "a"}})
        self.assertTrue(r.sent)
        self.assertTrue(EmailTemplate.objects.filter(template_type="password_reset", is_enabled=True).exists())

    def test_broken_edit_falls_back_to_default_wording(self):
        EmailTemplate.objects.filter(template_type="activation").update(subject="{% if %}", is_default=False)
        r = send_dynamic_email("activation", "a@x.test", CTX)
        self.assertTrue(r.sent)
        self.assertIn("asha", mail.outbox[0].subject)

    def test_every_default_renders_even_with_missing_variables(self):
        # {{ x|default:ref }} used to raise when the caller didn't pass "ref",
        # which silently killed the offer letter, meeting and logistics emails.
        from core.email_templates import _build_defaults, _render_parts
        for d in _build_defaults():
            _render_parts(d, {})

    def test_offer_letter_sends(self):
        r = send_dynamic_email("admission_offer_letter", "p@x.test", {"child_name": "Baraka"})
        self.assertTrue(r.sent)
        self.assertIn("Baraka", mail.outbox[0].subject)

    def test_multiline_subject_does_not_break_sending(self):
        EmailTemplate.objects.filter(template_type="activation").update(subject="Hello\n{{ user.username }}")
        self.assertTrue(send_dynamic_email("activation", "a@x.test", CTX).sent)
        self.assertEqual(mail.outbox[0].subject, "Hello asha")


class SeedAndResetTests(TestCase):
    def test_seeding_never_overwrites_admin_edits_and_reset_keeps_switch(self):
        seed_default_templates()
        EmailTemplate.objects.filter(template_type="activation").update(
            html_body="<p>Mine</p>", is_default=False, is_enabled=False)
        seed_default_templates()
        tpl = EmailTemplate.objects.get(template_type="activation")
        self.assertEqual(tpl.html_body, "<p>Mine</p>")
        reset_template("activation")
        tpl.refresh_from_db()
        self.assertNotEqual(tpl.html_body, "<p>Mine</p>")
        self.assertTrue(tpl.is_default)
        self.assertFalse(tpl.is_enabled)  # reset changes wording only

    def test_reset_from_settings_page_keeps_template(self):
        admin = User.objects.create_user("sa", "sa@x.test", "pw", role=UserRole.SUPER_ADMIN, is_superuser=True)
        self.client.force_login(admin)
        url = reverse("core:school_settings") + "?tab=email_templates"
        self.client.get(url)
        for _ in range(2):  # the old code deleted the template on the second reset in a process
            self.client.post(url, {"action": "reset_email_template", "template_type": "activation"})
        self.assertTrue(EmailTemplate.objects.filter(template_type="activation").exists())

    def test_toggle_switch(self):
        admin = User.objects.create_user("sa", "sa@x.test", "pw", role=UserRole.SUPER_ADMIN, is_superuser=True)
        self.client.force_login(admin)
        url = reverse("core:school_settings") + "?tab=email_templates"
        resp = self.client.get(url)
        self.assertContains(resp, "Email delivery:")
        self.client.post(url, {"action": "toggle_email_template", "template_type": "activation"})
        self.assertFalse(EmailTemplate.objects.get(template_type="activation").is_enabled)
        self.client.post(url, {"action": "toggle_email_template", "template_type": "activation", "is_enabled": "on"})
        self.assertTrue(EmailTemplate.objects.get(template_type="activation").is_enabled)


class DeliveryTests(TestCase):
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend", DEBUG=False)
    def test_console_backend_is_reported_as_not_sent(self):
        s = SchoolSettings.get_settings()
        s.email_backend = "django.core.mail.backends.console.EmailBackend"
        s.save()
        self.assertFalse(send_email_safe("a@x.test", "S", "B"))
        log = EmailSendLog.objects.get()
        self.assertFalse(log.success)
        self.assertIn("Not configured", log.error_message)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_smtp_saved_in_settings_wins_over_console_env(self):
        from core.email_backend import resolve_delivery
        s = SchoolSettings.get_settings()
        s.email_backend = "django.core.mail.backends.smtp.EmailBackend"
        s.email_host, s.email_port, s.email_host_user, s.email_host_password = "smtp.example.test", 465, "u", "p"
        s.default_from_email = "office@example.test"
        s.save()
        conn, from_email, info = resolve_delivery()
        self.assertEqual(info["mode"], "smtp")
        self.assertTrue(conn.use_ssl)
        self.assertEqual(from_email, "office@example.test")

    def test_messaging_form_keeps_password_and_requires_smtp_details(self):
        from core.forms import SchoolSettingsMessagingForm
        s = SchoolSettings.get_settings()
        s.email_host_password = "secret"
        s.save()
        base = {"email_backend": SchoolSettingsMessagingForm.SMTP, "email_host": "smtp.example.test",
                "email_port": 587, "email_use_tls": "on", "email_host_user": "u", "email_host_password": "",
                "default_from_email": "o@example.test", "whatsapp_sender_id": "H",
                "admissions_phone": "1", "admissions_email": "a@example.test", "admissions_whatsapp": "1"}
        form = SchoolSettingsMessagingForm(base, instance=s)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().email_host_password, "secret")
        self.assertNotIn("secret", str(SchoolSettingsMessagingForm(instance=s)["email_host_password"]))
        bad = SchoolSettingsMessagingForm({**base, "email_host_user": ""}, instance=s)
        self.assertFalse(bad.is_valid())
