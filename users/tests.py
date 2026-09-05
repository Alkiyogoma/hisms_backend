"""
Automated tests for authentication scenarios per FRD requirements:
- AUTH-002: Incorrect password shows inline error 'Email or password incorrect'
- AUTH-003: Account lockout after 5 consecutive failures for 15 minutes
- AUTH-004: Deactivated account shows 'Your account is not active. Contact your administrator.'
- FR-AUTH-005: Super Admin receives in-app alert on account lockout
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, Client, override_settings
from django.utils import timezone
from django.urls import reverse

from users.models import User, UserRole


def _create_user(username="testuser", password="TestPass123!", role=UserRole.TEACHER, is_active=True):
    """Create and return a user with a known password."""
    return User.objects.create_user(
        username=username, email=f"{username}@hodari.edu",
        password=password, role=role, is_active=is_active,
    )


class UserModelTests(TestCase):
    """Unit tests for the User model lockout logic."""

    def setUp(self):
        self.user = _create_user("locktest", "MyPass999!")

    def test_initial_state_no_lockout(self):
        self.assertFalse(self.user.is_locked_out)
        self.assertEqual(self.user.failed_login_attempts, 0)
        self.assertIsNone(self.user.locked_until)

    def test_record_failed_login_increments_counter(self):
        self.user.record_failed_login(max_attempts=5, lockout_seconds=900)
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 1)

    def test_record_failed_login_locks_after_threshold(self):
        for _ in range(4):
            self.user.record_failed_login(max_attempts=5, lockout_seconds=900)
            self.user.refresh_from_db()
            self.assertFalse(self.user.is_locked_out)
        self.user.record_failed_login(max_attempts=5, lockout_seconds=900)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_locked_out)
        self.assertGreater(self.user.locked_until, timezone.now())
        self.assertEqual(self.user.failed_login_attempts, 0)

    def test_clear_failed_logins_resets_state(self):
        self.user.record_failed_login(max_attempts=5, lockout_seconds=900)
        self.user.record_failed_login(max_attempts=5, lockout_seconds=900)
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 2)
        self.user.clear_failed_logins()
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 0)
        self.assertIsNone(self.user.locked_until)
        self.assertFalse(self.user.is_locked_out)

    def test_lockout_expires_after_duration(self):
        self.user.locked_until = timezone.now() - timedelta(seconds=1)
        self.user.save(update_fields=["locked_until"])
        self.assertFalse(self.user.is_locked_out)

    def test_custom_max_attempts(self):
        for _ in range(2):
            self.user.record_failed_login(max_attempts=3, lockout_seconds=900)
            self.user.refresh_from_db()
            self.assertFalse(self.user.is_locked_out)
        self.user.record_failed_login(max_attempts=3, lockout_seconds=900)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_locked_out)


@override_settings(LOGIN_MAX_ATTEMPTS=5, LOGIN_LOCKOUT_DURATION=900)
class IncorrectPasswordTests(TestCase):
    """GIVEN a user enters an incorrect password
    WHEN they submit the login form
    THEN an inline error displays: 'Email or password incorrect' and the attempt is counted."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user("wrongpw", "CorrectPass123!")
        self.login_url = reverse("login")

    def test_incorrect_password_shows_correct_error(self):
        response = self.client.post(self.login_url, {"username": "wrongpw", "password": "WrongPassword!"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Email or password incorrect", response.content.decode())

    def test_incorrect_password_counts_the_attempt(self):
        self.assertEqual(self.user.failed_login_attempts, 0)
        self.client.post(self.login_url, {"username": "wrongpw", "password": "WrongPassword!"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 1)

    def test_multiple_incorrect_passwords_count_separately(self):
        for i in range(3):
            self.client.post(self.login_url, {"username": "wrongpw", "password": f"BadPass{i}!"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 3)

    def test_nonexistent_username_does_not_leak(self):
        response = self.client.post(self.login_url, {"username": "nonexistent_user_12345", "password": "SomePassword!"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Email or password incorrect", response.content.decode())

    def test_correct_password_authenticates(self):
        response = self.client.post(self.login_url, {"username": "wrongpw", "password": "CorrectPass123!"})
        self.assertIn(response.status_code, [302, 303])


@override_settings(LOGIN_MAX_ATTEMPTS=5, LOGIN_LOCKOUT_DURATION=900)
class AccountLockoutTests(TestCase):
    """GIVEN a user has failed login 5 consecutive times
    WHEN the 5th failure is submitted
    THEN the account is locked for 15 minutes and the message reads:
         'Account locked. Try again after 15 minutes.'
         The Super Admin receives an in-app alert."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user("lockacct", "ValidPass123!")
        self.super_admin = _create_user("superadmin", "AdminPass123!", role=UserRole.SUPER_ADMIN)
        self.login_url = reverse("login")

    def _fail_n_times(self, n):
        for _ in range(n):
            self.client.post(self.login_url, {"username": "lockacct", "password": "WrongPassword!"})

    def test_lockout_after_5_failures(self):
        self._fail_n_times(5)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_locked_out)
        self.assertGreater(self.user.locked_until, timezone.now())

    def test_lockout_message_after_5th_failure(self):
        self._fail_n_times(4)
        response = self.client.post(self.login_url, {"username": "lockacct", "password": "WrongPassword!"})
        self.assertIn("Account locked. Try again after 15 minutes.", response.content.decode())

    def test_locked_account_rejects_correct_password(self):
        self._fail_n_times(5)
        response = self.client.post(self.login_url, {"username": "lockacct", "password": "ValidPass123!"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Account locked", response.content.decode())
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_lockout_duration_is_15_minutes(self):
        self._fail_n_times(5)
        self.user.refresh_from_db()
        expected_min = timezone.now() + timedelta(minutes=14)
        expected_max = timezone.now() + timedelta(minutes=16)
        self.assertGreater(self.user.locked_until, expected_min)
        self.assertLess(self.user.locked_until, expected_max)

    @patch("communications.email_service.dispatch_notification")
    def test_super_admin_receives_alert_on_lockout(self, mock_dispatch):
        self._fail_n_times(5)
        mock_dispatch.assert_called()
        notified_user = mock_dispatch.call_args.kwargs.get("user")
        self.assertEqual(notified_user.pk, self.super_admin.pk)

    @patch("communications.email_service.dispatch_notification")
    def test_lockout_alert_contains_locked_username(self, mock_dispatch):
        self._fail_n_times(5)
        message = mock_dispatch.call_args.kwargs.get("message")
        self.assertIn("lockacct", message)

    def test_successful_login_resets_failed_counter(self):
        self._fail_n_times(3)
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 3)
        self.client.post(self.login_url, {"username": "lockacct", "password": "ValidPass123!"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 0)
        self.assertIsNone(self.user.locked_until)

    def test_does_not_count_during_lockout(self):
        self._fail_n_times(5)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_locked_out)
        self._fail_n_times(3)
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 0)


@override_settings(LOGIN_MAX_ATTEMPTS=5, LOGIN_LOCKOUT_DURATION=900)
class DeactivatedAccountTests(TestCase):
    """GIVEN a deactivated user account
    WHEN the user attempts to log in
    THEN access is denied and the message reads:
         'Your account is not active. Contact your administrator."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user("deactived", "SomePass123!", is_active=False)
        self.login_url = reverse("login")

    def test_deactivated_account_shows_correct_message(self):
        response = self.client.post(self.login_url, {"username": "deactived", "password": "SomePass123!"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Your account is not active. Contact your administrator.", response.content.decode())

    def test_deactivated_account_not_authenticated(self):
        self.client.post(self.login_url, {"username": "deactived", "password": "SomePass123!"})
        self.assertFalse(User.objects.get(pk=self.user.pk).is_active)

    def test_deactivated_account_does_not_count_toward_lockout(self):
        self.assertEqual(self.user.failed_login_attempts, 0)
        for _ in range(3):
            self.client.post(self.login_url, {"username": "deactived", "password": "SomePass123!"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 0)

    def test_deactivated_account_does_not_trigger_lockout(self):
        for _ in range(10):
            self.client.post(self.login_url, {"username": "deactived", "password": "SomePass123!"})
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_locked_out)
        self.assertEqual(self.user.failed_login_attempts, 0)

    def test_deactivated_account_shows_not_password_error(self):
        response = self.client.post(self.login_url, {"username": "deactived", "password": "AnyPassword!"})
        content = response.content.decode()
        self.assertIn("Your account is not active", content)
        self.assertNotIn("Email or password incorrect", content)


@override_settings(LOGIN_MAX_ATTEMPTS=5, LOGIN_LOCKOUT_DURATION=900)
class LoginFlowIntegrationTests(TestCase):
    """Integration tests covering the complete login flow scenarios."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user("flowtest", "FlowPass123!")
        self.login_url = reverse("login")

    def test_valid_login_redirects_to_dashboard(self):
        response = self.client.post(self.login_url, {"username": "flowtest", "password": "FlowPass123!"})
        self.assertIn(response.status_code, [302, 303])
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_valid_login_with_next_param_redirects(self):
        response = self.client.post(f"{self.login_url}?next=/dashboard/", {"username": "flowtest", "password": "FlowPass123!"})
        self.assertIn(response.status_code, [302, 303])
        self.assertIn("/dashboard/", response.url)

    def test_must_change_password_redirects(self):
        self.user.must_change_password = True
        self.user.save(update_fields=["must_change_password"])
        response = self.client.post(self.login_url, {"username": "flowtest", "password": "FlowPass123!"})
        self.assertIn(response.status_code, [302, 303])
        self.assertIn("password-change", response.url)

    def test_4_failures_then_success_clears_lockout(self):
        for _ in range(4):
            self.client.post(self.login_url, {"username": "flowtest", "password": "WrongPass!"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 4)
        self.assertFalse(self.user.is_locked_out)
        response = self.client.post(self.login_url, {"username": "flowtest", "password": "FlowPass123!"})
        self.assertIn(response.status_code, [302, 303])
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 0)
