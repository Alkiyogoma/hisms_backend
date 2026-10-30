"""Parent sign-in with phone number + one-time SMS code."""
import json
import re
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from students.models import ParentGuardian
from users.models import ParentLoginCode, User, UserRole

SMS_OK = {"success": True, "provider": "test"}


@override_settings(DEBUG=True, SMS_PROVIDER="console")
@patch("attendance.notification_service.NotificationService.send_sms", return_value=SMS_OK)
class ParentOTPLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.parent = User.objects.create_user(
            username="parent_1", email="p1@x.test", password="Temp-pass-1", role=UserRole.PARENT,
        )
        self.parent.must_change_password = True
        self.parent.save(update_fields=["must_change_password"])
        ParentGuardian.objects.create(full_name="Asha M", phone="0712 345 678", user=self.parent)

    def _send(self, phone="+255 712-345-678"):
        return self.client.post(reverse("users:parent_otp_request"), json.dumps({"phone": phone}),
                                content_type="application/json")

    def _verify(self, code, phone="0712345678", next_url=""):
        return self.client.post(reverse("users:parent_otp_verify"),
                                json.dumps({"phone": phone, "code": code, "next": next_url}),
                                content_type="application/json")

    @staticmethod
    def _code_from(send_sms):
        return re.search(r"\b(\d{6})\b", send_sms.call_args[0][1]).group(1)

    def test_full_sign_in_with_any_phone_format(self, send_sms):
        resp = self._send()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["resend_after"], 60)
        send_sms.assert_called_once()
        self.assertEqual(send_sms.call_args[0][0], "255712345678")
        stored = ParentLoginCode.objects.get()
        code = self._code_from(send_sms)
        self.assertNotIn(code, stored.code_hash)

        resp = self._verify(code)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["redirect"], "/parent/")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.parent.pk)
        # SMS sign-in skips the temporary-password change.
        self.assertEqual(self.client.get("/parent/").status_code, 200)

    def test_code_is_single_use(self, send_sms):
        self._send()
        code = self._code_from(send_sms)
        self.assertEqual(self._verify(code).status_code, 200)
        self.client.logout()
        self.assertEqual(self._verify(code).status_code, 401)

    def test_wrong_code_and_attempt_limit(self, send_sms):
        self._send()
        code = self._code_from(send_sms)
        wrong = f"{(int(code) + 1) % 1_000_000:06d}"
        for _ in range(5):
            self.assertEqual(self._verify(wrong).status_code, 401)
        # After 5 misses even the right code no longer works.
        self.assertEqual(self._verify(code).status_code, 401)
        self.assertNotIn("_auth_user_id", self.client.session)

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

    def test_unknown_number_gets_same_reply_and_no_sms(self, send_sms):
        known = self._send().json()
        unknown = self._send("0799 000 111").json()
        self.assertEqual(known, unknown)
        send_sms.assert_called_once()

    def test_resend_cooldown_applies_to_any_number(self, send_sms):
        for phone in ("0712345678", "0799000111"):
            self.assertEqual(self._send(phone).status_code, 200)
            self.assertEqual(self._send(phone).status_code, 429)

    def test_invalid_number(self, send_sms):
        self.assertEqual(self._send("12345").status_code, 400)
        send_sms.assert_not_called()

    def test_inactive_parent_gets_no_code(self, send_sms):
        self.parent.is_active = False
        self.parent.save(update_fields=["is_active"])
        self.assertEqual(self._send().status_code, 200)
        send_sms.assert_not_called()

    def test_staff_phone_cannot_sign_in(self, send_sms):
        teacher = User.objects.create_user(username="t1", email="t1@x.test", password="x",
                                           role=UserRole.TEACHER)
        ParentGuardian.objects.create(full_name="Teacher Parent", phone="0755 111 222", user=teacher)
        self._send("0755111222")
        send_sms.assert_not_called()

    def test_shared_number_is_refused_by_sms(self, send_sms):
        other = User.objects.create_user(username="parent_2", email="p2@x.test", password="x",
                                         role=UserRole.PARENT)
        ParentGuardian.objects.create(full_name="Juma M", phone="+255712345678", user=other)
        self._send()
        self.assertIn("more than one parent account", send_sms.call_args[0][1])
        self.assertFalse(ParentLoginCode.objects.exists())

    def test_unsafe_next_is_ignored(self, send_sms):
        self._send()
        resp = self._verify(self._code_from(send_sms), next_url="https://evil.example/")
        self.assertEqual(resp.json()["redirect"], "/parent/")

    @override_settings(DEBUG=False, SMS_PROVIDER="console")
    def test_unavailable_when_sms_not_configured(self, send_sms):
        self.assertEqual(self._send().status_code, 503)
        send_sms.assert_not_called()


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
        self.assertEqual(resp.json()["resend_after"], 60)
        code = re.search(r"\b(\d{6})\b", send_sms.call_args[0][1]).group(1)

        resp = self.client.post(reverse("api_parent_verify_code"), {"phone": "0712345678", "code": "000000"
                                if code != "000000" else "111111"}, content_type="application/json")
        self.assertEqual(resp.status_code, 401)
        resp = self.client.post(reverse("api_parent_verify_code"), {"phone": "0712345678", "code": code},
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["access"] and body["refresh"])
        self.assertEqual(body["user"]["role"], UserRole.PARENT)


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
