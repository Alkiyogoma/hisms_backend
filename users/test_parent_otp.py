"""Parent sign-in with a phone number or email + one-time code."""
import json
import re
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import SchoolSettings
from students.models import ParentGuardian
from users.models import ParentLoginCode, User, UserRole

SMS_OK = {"success": True, "provider": "test"}
CODE_RE = re.compile(r"\b(\d{4})\b")


def _wrong(code):
    return f"{(int(code) + 1) % 10_000:04d}"


@override_settings(DEBUG=True, SMS_PROVIDER="console", PARENT_OTP_DEV_PREVIEW=False,
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
@patch("attendance.notification_service.NotificationService.send_sms", return_value=SMS_OK)
class ParentOTPLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.parent = User.objects.create_user(
            username="parent_1", email="p1@x.test", password="Temp-pass-1", role=UserRole.PARENT,
        )
        self.parent.must_change_password = True
        self.parent.save(update_fields=["must_change_password"])
        ParentGuardian.objects.create(full_name="Asha M", phone="0712 345 678", email="Asha.M@Example.test",
                                      user=self.parent)
        settings_obj = SchoolSettings.get_settings()
        settings_obj.parent_contact_phone = "0767 016 940"
        settings_obj.save()

    def _send(self, contact="+255 712-345-678", client=None):
        return (client or self.client).post(reverse("users:parent_otp_request"),
                                            json.dumps({"contact": contact}), content_type="application/json")

    def _verify(self, code, contact="0712345678", next_url=""):
        return self.client.post(reverse("users:parent_otp_verify"),
                                json.dumps({"contact": contact, "code": code, "next": next_url}),
                                content_type="application/json")

    @staticmethod
    def _code_from(send_sms):
        return CODE_RE.search(send_sms.call_args[0][1]).group(1)

    def test_full_sign_in_with_any_phone_format(self, send_sms):
        resp = self._send()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual((body["channel"], body["code_length"], body["resend_after"]), ("sms", 4, 45))
        self.assertIn("by SMS", body["message"])
        self.assertIn("678", body["masked"])
        send_sms.assert_called_once()
        self.assertEqual(send_sms.call_args[0][0], "255712345678")
        stored = ParentLoginCode.objects.get()
        code = self._code_from(send_sms)
        self.assertNotIn(code, stored.code_hash)
        # The code never goes back to the browser.
        self.assertNotIn(code, resp.content.decode())
        self.assertNotIn("dev_preview", body)

        resp = self._verify(code)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["redirect"], "/parent/")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.parent.pk)
        # Code sign-in skips the temporary-password change.
        self.assertEqual(self.client.get("/parent/").status_code, 200)

    def test_email_sign_in_says_inbox_and_spam(self, send_sms):
        resp = self._send("asha.m@example.TEST")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["channel"], "email")
        self.assertIn("inbox", body["message"])
        self.assertIn("spam", body["message"])
        send_sms.assert_not_called()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["asha.m@example.test"])
        code = CODE_RE.search(mail.outbox[0].body).group(1)
        self.assertEqual(self._verify(code, contact="Asha.M@example.test").status_code, 200)
        self.assertEqual(self.client.session["_login_method"], "email_code")

    def test_code_is_single_use(self, send_sms):
        self._send()
        code = self._code_from(send_sms)
        self.assertEqual(self._verify(code).status_code, 200)
        self.client.logout()
        self.assertEqual(self._verify(code).status_code, 401)

    def test_arbitrary_digits_are_rejected(self, send_sms):
        self._send()
        code = self._code_from(send_sms)
        self.assertEqual(self._verify(_wrong(code)).status_code, 401)
        self.assertEqual(self._verify("12").status_code, 400)
        self.assertNotIn("_auth_user_id", self.client.session)
        # And without any code requested, nothing signs in.
        self.assertEqual(self._verify("1234", contact="0799000111").status_code, 401)

    def test_wrong_code_and_attempt_limit(self, send_sms):
        self._send()
        code = self._code_from(send_sms)
        for _ in range(5):
            self.assertEqual(self._verify(_wrong(code)).status_code, 401)
        # After 5 misses even the right code no longer works.
        self.assertEqual(self._verify(code).status_code, 401)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_daily_wrong_code_cap_blocks_new_codes(self, send_sms):
        for _ in range(2):
            self._send()
            code = self._code_from(send_sms)
            for _ in range(5):
                self._verify(_wrong(code))
            cache.delete("parent-otp:cooldown:255712345678")
            cache.delete("parent-otp:hourly:255712345678")
        resp = self._send()
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()["code"], "daily_limit")

    def test_expired_code_rejected(self, send_sms):
        self._send()
        ParentLoginCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self._verify(self._code_from(send_sms)).status_code, 401)

    def test_new_code_replaces_old(self, send_sms):
        self._send()
        old = self._code_from(send_sms)
        cache.clear()  # skip the resend cooldown
        self._send()
        new = self._code_from(send_sms)
        if old != new:
            self.assertEqual(self._verify(old).status_code, 401)
        self.assertEqual(self._verify(new).status_code, 200)

    def test_unknown_contact_is_explained_with_school_number(self, send_sms):
        for contact in ("0799 000 111", "nobody@example.test"):
            resp = self._send(contact)
            self.assertEqual(resp.status_code, 404)
            body = resp.json()
            self.assertEqual(body["code"], "not_registered")
            self.assertIn("can't find that phone number or email", body["error"])
            self.assertIn("0767 016 940", body["error"])
        send_sms.assert_not_called()
        self.assertFalse(ParentLoginCode.objects.exists())

    def test_resend_cooldown_is_server_side_for_any_contact(self, send_sms):
        self.assertEqual(self._send("0712345678").status_code, 200)
        resp = self._send("0712345678")
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()["code"], "cooldown")
        # Unknown contacts are limited the same way.
        self.assertEqual(self._send("0799000111").status_code, 404)
        self.assertEqual(self._send("0799000111").status_code, 429)

    @override_settings(PARENT_OTP_MAX_PER_CLIENT_HOUR=3)
    def test_per_client_throttle(self, send_sms):
        from users import otp
        with patch.object(otp, "MAX_REQUESTS_PER_CLIENT_HOUR", 3):
            for n in range(3):
                self._send(f"07990001{n:02d}")
            self.assertEqual(self._send("0799000199").json()["code"], "client_limit")

    def test_invalid_contact(self, send_sms):
        self.assertEqual(self._send("12345").status_code, 400)
        self.assertEqual(self._send("not-an@email").status_code, 400)
        self.assertEqual(self._send("").status_code, 400)
        send_sms.assert_not_called()

    def test_inactive_parent_gets_no_code(self, send_sms):
        self.parent.is_active = False
        self.parent.save(update_fields=["is_active"])
        self.assertEqual(self._send().status_code, 404)
        send_sms.assert_not_called()

    def test_staff_phone_cannot_sign_in(self, send_sms):
        teacher = User.objects.create_user(username="t1", email="t1@x.test", password="x",
                                           role=UserRole.TEACHER)
        ParentGuardian.objects.create(full_name="Teacher Parent", phone="0755 111 222", user=teacher)
        self.assertEqual(self._send("0755111222").status_code, 404)
        send_sms.assert_not_called()

    def test_shared_number_is_refused(self, send_sms):
        other = User.objects.create_user(username="parent_2", email="p2@x.test", password="x",
                                         role=UserRole.PARENT)
        ParentGuardian.objects.create(full_name="Juma M", phone="+255712345678", user=other)
        resp = self._send()
        self.assertEqual(resp.status_code, 409)
        self.assertIn("more than one parent account", send_sms.call_args[0][1])
        self.assertFalse(ParentLoginCode.objects.exists())

    def test_unsafe_next_is_ignored(self, send_sms):
        self._send()
        resp = self._verify(self._code_from(send_sms), next_url="https://evil.example/")
        self.assertEqual(resp.json()["redirect"], "/parent/")

    def test_csrf_is_enforced(self, send_sms):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(self._send(client=client).status_code, 403)
        send_sms.assert_not_called()

    def test_session_key_rotates_on_sign_in(self, send_sms):
        self._send()
        before = self.client.session.session_key
        self._verify(self._code_from(send_sms))
        self.assertNotEqual(before, self.client.session.session_key)

    @override_settings(PARENT_OTP_DEV_PREVIEW=True)
    def test_dev_preview_only_with_debug_and_flag(self, send_sms):
        body = self._send().json()
        self.assertIn(self._code_from(send_sms), body["dev_preview"])
        cache.clear()
        with override_settings(DEBUG=False, PARENT_OTP_DEV_PREVIEW=True), \
                patch("users.otp.sms_available", return_value=True):
            body = self._send().json()
            self.assertTrue(body["ok"])
            self.assertNotIn("dev_preview", body)

    @override_settings(DEBUG=False, SMS_PROVIDER="console")
    def test_unavailable_when_sms_not_configured(self, send_sms):
        resp = self._send()
        self.assertEqual(resp.status_code, 503)
        self.assertIn("0767 016 940", resp.json()["error"])
        send_sms.assert_not_called()

    def test_sign_in_screen_has_one_field_and_four_digit_boxes(self, send_sms):
        page = self.client.get(reverse("users:login")).content.decode()
        self.assertIn('id="ps-contact"', page)
        self.assertIn(">Log in<", page)
        self.assertEqual(page.count('class="ps-digit"'), 4)
        self.assertIn('aria-label="Digit 1 of 4"', page)
        self.assertNotIn("Simulated for developer", page)


@override_settings(DEBUG=True, SMS_PROVIDER="console")
@patch("attendance.notification_service.NotificationService.send_sms", return_value=SMS_OK)
class ParentPhoneLoginAPITests(TestCase):
    """Mobile app: parents get JWT tokens from phone + SMS code."""

    def setUp(self):
        cache.clear()
        self.parent = User.objects.create_user(username="parent_1", email="p1@x.test",
                                               role=UserRole.PARENT)
        ParentGuardian.objects.create(full_name="Asha M", phone="0712 345 678", user=self.parent)

    def test_send_and_verify_returns_tokens(self, send_sms):
        resp = self.client.post(reverse("api_parent_send_code"), {"phone": "0712345678"},
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["resend_after"], 45)
        code = CODE_RE.search(send_sms.call_args[0][1]).group(1)

        resp = self.client.post(reverse("api_parent_verify_code"), {"phone": "0712345678", "code": _wrong(code)},
                                content_type="application/json")
        self.assertEqual(resp.status_code, 401)
        resp = self.client.post(reverse("api_parent_verify_code"), {"phone": "0712345678", "code": code},
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["access"] and body["refresh"])
        self.assertEqual(body["user"]["role"], UserRole.PARENT)

    def test_unknown_number_gets_the_generic_reply(self, send_sms):
        known = self.client.post(reverse("api_parent_send_code"), {"phone": "0712345678"},
                                 content_type="application/json").json()
        unknown = self.client.post(reverse("api_parent_send_code"), {"phone": "0799000111"},
                                   content_type="application/json").json()
        self.assertEqual(known, unknown)
        send_sms.assert_called_once()


class ParentsHaveNoPasswordTests(TestCase):
    """Every password route refuses parents; staff are unaffected."""

    def setUp(self):
        cache.clear()
        # A parent created before phone sign-in, still holding a password.
        self.parent = User.objects.create_user(username="parent_old", email="old@x.test",
                                               password="Old-pass-123", role=UserRole.PARENT)
        self.teacher = User.objects.create_user(username="teach", email="teach@x.test",
                                                password="Teach-pass-123", role=UserRole.TEACHER)

    def test_staff_form_refuses_parent_even_with_right_password(self):
        resp = self.client.post(reverse("users:login"), {"username": "parent_old", "password": "Old-pass-123"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Parents sign in with their phone number")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_staff_form_wrong_password_does_not_lock_parent(self):
        for _ in range(6):
            self.client.post(reverse("users:login"), {"username": "parent_old", "password": "nope"})
        self.parent.refresh_from_db()
        self.assertEqual(self.parent.failed_login_attempts, 0)
        self.assertIsNone(self.parent.locked_until)

    def test_json_password_login_refuses_parent(self):
        resp = self.client.post(reverse("users:login_api"),
                                json.dumps({"email": "old@x.test", "password": "Old-pass-123"}),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_jwt_refuses_parent_allows_staff(self):
        resp = self.client.post(reverse("token_obtain_pair"),
                                {"username": "parent_old", "password": "Old-pass-123"},
                                content_type="application/json")
        self.assertEqual(resp.status_code, 401)
        self.assertNotIn("access", resp.json())
        resp = self.client.post(reverse("token_obtain_pair"),
                                {"username": "teach", "password": "Teach-pass-123"},
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.json())

    def test_staff_password_login_still_works(self):
        resp = self.client.post(reverse("users:login"), {"username": "teach", "password": "Teach-pass-123"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.teacher.pk)

    def test_no_password_reset_email_for_parent(self):
        from django.core import mail
        self.client.post(reverse("users:password_reset_request"), {"email": "old@x.test"})
        self.assertEqual(len(mail.outbox), 0)

    def test_parent_cannot_open_password_change(self):
        self.parent.must_change_password = True
        self.parent.save(update_fields=["must_change_password"])
        self.client.force_login(self.parent)
        resp = self.client.get(reverse("users:password_change"))
        self.assertRedirects(resp, "/parent/", fetch_redirect_response=False)
        # And they aren't bounced to it from the portal (no redirect loop).
        self.assertEqual(self.client.get("/parent/").status_code, 200)

    def test_portal_access_gives_no_usable_password(self):
        from students.services import grant_portal_access
        guardian = ParentGuardian.objects.create(full_name="New Parent", phone="0755 000 111")
        with patch("students.services.send_portal_credentials", return_value=[]):
            created, _username, _ = grant_portal_access(guardian, actor=self.teacher)
        guardian.refresh_from_db()
        self.assertTrue(created)
        self.assertFalse(guardian.user.has_usable_password())
        self.assertFalse(guardian.user.must_change_password)
