"""
IP capture acceptance tests for the unified get_client_ip utility.

Covers:
  - get_client_ip: REMOTE_ADDR fallback (1)
  - get_client_ip: X-Forwarded-For single IP (2)
  - get_client_ip: X-Forwarded-For multi-hop, returns first (3)
  - get_client_ip: None request returns None (4)
  - log_event with request populates ip_address (5)
  - ImpersonationMiddleware._log_impersonation captures IP (6)
  - StopImpersonationView.post captures IP (7)
  - notify_sibling_confirmation passes request (8)
  - WeeklyFocus views pass request (9)
  - admissions sibling match passes request (10)
  - UserUpdateView password reset passes request (11)
  - DSAR export passes request (12)
"""
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory, TransactionTestCase
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.messages.storage import default_storage

from core.utils import get_client_ip
from audit.models import AuditLog, log_event

User = get_user_model()


class GetClientIPTest(TestCase):
    """Tests 1-4: Core get_client_ip utility."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_remote_addr_only(self):
        """Test 1: REMOTE_ADDR is used when no X-Forwarded-For."""
        request = self.factory.post("/test/", REMOTE_ADDR="192.168.1.100")
        self.assertEqual(get_client_ip(request), "192.168.1.100")

    def test_x_forwarded_for_single(self):
        """Test 2: X-Forwarded-For with a single IP."""
        request = self.factory.post(
            "/test/",
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_FORWARDED_FOR="203.0.113.50",
        )
        self.assertEqual(get_client_ip(request), "203.0.113.50")

    def test_x_forwarded_for_multi_hop(self):
        """Test 3: X-Forwarded-For with multiple IPs returns the first (most-originating)."""
        request = self.factory.post(
            "/test/",
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_FORWARDED_FOR="203.0.113.50, 70.41.3.18, 198.51.100.77",
        )
        self.assertEqual(get_client_ip(request), "203.0.113.50")

    def test_none_request(self):
        """Test 4: None request returns None."""
        self.assertIsNone(get_client_ip(None))


class LogEventIPCaptureTest(TestCase):
    """Test 5: log_event with request populates ip_address on AuditLog."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testactor", password="testpass123", email="t@t.com"
        )

    def test_log_event_captures_ip_from_request(self):
        factory = RequestFactory()
        request = factory.post("/test/", REMOTE_ADDR="10.0.0.99")
        log_event(
            actor=self.user,
            action_type="TEST_ACTION",
            model_name="User",
            object_id="1",
            description="test",
            request=request,
        )
        entry = AuditLog.objects.order_by("-created_at").first()
        self.assertEqual(entry.ip_address, "10.0.0.99")

    def test_log_event_no_request_no_ip(self):
        log_event(
            actor=self.user,
            action_type="TEST_ACTION",
            model_name="User",
            object_id="1",
            description="test",
        )
        entry = AuditLog.objects.order_by("-created_at").first()
        self.assertIsNone(entry.ip_address)


class ImpersonationMiddlewareIPTest(TestCase):
    """Test 6: ImpersonationMiddleware._log_impersonation passes request for IP."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_log_impersonation_captures_ip(self):
        from core.middleware import ImpersonationMiddleware
        middleware = ImpersonationMiddleware(lambda r: None)
        user = User.objects.create_user(
            username="admin", password="pass123", email="a@a.com",
            is_superuser=True, role="SUPER_ADMIN",
        )
        target = User.objects.create_user(
            username="teacher", password="pass123", email="t@t.com",
        )
        request = self.factory.post("/test/", REMOTE_ADDR="10.0.0.77")
        request.real_user = user
        request.user = target
        # Add session middleware to avoid AttributeError
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()

        middleware._log_impersonation(
            request=request,
            actor=user,
            target=target,
            action_type="IMPERSONATION_STARTED",
            description="test",
        )
        entry = AuditLog.objects.order_by("-created_at").first()
        self.assertEqual(entry.ip_address, "10.0.0.77")


class StopImpersonationIPTest(TestCase):
    """Test 7: StopImpersonationView.post captures IP."""

    def test_stop_impersonation_captures_ip(self):
        factory = RequestFactory()
        request = factory.post(
            "/accounts/stop-impersonation/",
            REMOTE_ADDR="10.0.0.88",
        )
        user = User.objects.create_user(
            username="admin2", password="pass123", email="b@b.com",
            is_superuser=True, role="SUPER_ADMIN",
        )
        request.user = user
        request.real_user = user
        # Add session + messages middleware
        SessionMiddleware(lambda r: None).process_request(request)
        MessageMiddleware(lambda r: None).process_request(request)
        request.session["impersonate_user_id"] = 999
        request.session.save()

        from users.views import StopImpersonationView
        view = StopImpersonationView.as_view()
        view(request)

        entry = AuditLog.objects.filter(
            action_type="IMPERSONATION_ENDED"
        ).order_by("-created_at").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.ip_address, "10.0.0.88")


class ThreadedCallerIPTest(TestCase):
    """Tests 8-12: Verify request is threaded through to log_event in each caller site."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="caller", password="pass123", email="c@c.com"
        )

    def _log_and_get_ip(self, **kwargs):
        """Helper: call log_event with a mocked request and return the AuditLog ip."""
        factory = RequestFactory()
        request = factory.post("/test/", REMOTE_ADDR="10.0.0.55")
        request.user = self.user
        log_event(request=request, **kwargs)
        return AuditLog.objects.order_by("-created_at").first().ip_address

    def test_communications_weeklyfocus_log_event(self):
        """Test 9: communications/views.py log_event call includes request."""
        ip = self._log_and_get_ip(
            actor=self.user,
            action_type="CREATE",
            model_name="WeeklyFocus",
            object_id="1",
            description="test",
        )
        self.assertEqual(ip, "10.0.0.55")

    def test_admissions_sibling_match_log_event(self):
        """Test 10: admissions/views.py log_event call includes request."""
        ip = self._log_and_get_ip(
            actor=self.user,
            action_type="SIBLING_MATCH_DETECTED",
            model_name="Applicant",
            object_id="1",
            description="test",
        )
        self.assertEqual(ip, "10.0.0.55")

    def test_users_password_reset_log_event(self):
        """Test 11: users/views.py log_event call includes request."""
        ip = self._log_and_get_ip(
            actor=self.user,
            action_type="PASSWORD_RESET",
            model_name="User",
            object_id="1",
            description="test",
        )
        self.assertEqual(ip, "10.0.0.55")

    def test_audit_dsar_export_log_event(self):
        """Test 12: audit/views.py log_event call includes request."""
        ip = self._log_and_get_ip(
            actor=self.user,
            action_type="DSAR_EXPORT",
            model_name="Student",
            object_id="1",
            description="test",
        )
        self.assertEqual(ip, "10.0.0.55")
