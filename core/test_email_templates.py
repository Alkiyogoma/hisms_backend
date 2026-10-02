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
        self.assertFalse(send_email_safe("a@x.test", "S", "B"))
        log = EmailSendLog.objects.get()
        self.assertFalse(log.success)
        self.assertIn("Not configured", log.error_message)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="smtp.example.test",
                       EMAIL_PORT=465, EMAIL_USE_SSL=True, EMAIL_USE_TLS=False, EMAIL_HOST_USER="u",
                       EMAIL_HOST_PASSWORD="p", DEFAULT_FROM_EMAIL="office@example.test")
    def test_smtp_comes_from_env_settings(self):
        from core.email_backend import resolve_delivery
        conn, from_email, info = resolve_delivery()
        self.assertEqual(info["mode"], "smtp")
        self.assertTrue(info["delivering"])
        self.assertEqual((info["host"], info["port"], info["security"], info["user"]), ("smtp.example.test", 465, "SSL", "u"))
        self.assertNotIn("p", info.values())
        self.assertTrue(conn.use_ssl)
        self.assertEqual((conn.username, conn.password), ("u", "p"))
        self.assertEqual(from_email, "office@example.test")

    @override_settings(EMAIL_BACKEND="core.email_backend.DatabaseEmailBackend", EMAIL_HOST="smtp.example.test",
                       EMAIL_PORT=587, EMAIL_USE_TLS=True, EMAIL_HOST_USER="u", EMAIL_HOST_PASSWORD="p")
    def test_legacy_database_backend_name_uses_env_smtp(self):
        from core.email_backend import resolve_delivery
        conn, _from, info = resolve_delivery()
        self.assertEqual(info["mode"], "smtp")
        self.assertEqual((conn.host, conn.port, conn.use_tls, conn.username), ("smtp.example.test", 587, True, "u"))

    def test_messaging_form_has_no_smtp_fields(self):
        from core.forms import SchoolSettingsMessagingForm
        self.assertFalse({f for f in SchoolSettingsMessagingForm().fields if f.startswith("email_") or f == "default_from_email"})

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="smtp.gmail.com",
                       EMAIL_PORT=587, EMAIL_USE_TLS=True, EMAIL_HOST_USER="noreply@example.test",
                       EMAIL_HOST_PASSWORD="app-pass-secret", DEFAULT_FROM_EMAIL="noreply@example.test")
    def test_settings_page_shows_env_smtp_without_password(self):
        admin = User.objects.create_superuser(username="sa", email="sa@example.test", password="pw-12345!",
                                              role=UserRole.SUPER_ADMIN)
        self.client.force_login(admin)
        html = self.client.get(reverse("core:school_settings") + "?tab=messaging").content.decode()
        self.assertIn("smtp.gmail.com:587", html)
        self.assertIn("STARTTLS", html)
        self.assertIn("noreply@example.test", html)
        self.assertNotIn("app-pass-secret", html)
        self.assertNotIn('name="email_host"', html)
