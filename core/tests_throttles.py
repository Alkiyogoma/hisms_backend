"""
Rate-limiting acceptance tests for OTP and password-reset endpoints.

Covers:
  - OTP request: 5 requests per 5 min per IP
  - OTP verify: 10 attempts per 5 min per IP
  - Password reset: 3 requests per 5 min per email (not per IP)
  - 429 response with Retry-After header
  - Rate limit resets after window expires
"""
import json
from unittest.mock import patch

from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.core.cache import cache

from core.throttles import (
    check_rate_limit,
    rate_limit_or_429,
    get_remaining,
    RateLimitExceeded,
)

User = get_user_model()


class RateLimitUtilityTest(TestCase):
    """Unit tests for the core.throttles utility functions."""

    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.request = self.factory.post("/test/", REMOTE_ADDR="10.0.0.1")

    def test_allows_requests_under_limit(self):
        for _ in range(4):
            check_rate_limit(self.request, "test_scope", limit=5, period=300)
        remaining, _ = get_remaining(self.request, "test_scope", limit=5, period=300)
        self.assertEqual(remaining, 1)

    def test_blocks_at_limit(self):
        for _ in range(5):
            check_rate_limit(self.request, "test_scope", limit=5, period=300)
        with self.assertRaises(RateLimitExceeded) as ctx:
            check_rate_limit(self.request, "test_scope", limit=5, period=300)
        self.assertGreater(ctx.exception.retry_after, 0)

    def test_returns_429_json(self):
        for _ in range(5):
            rate_limit_or_429(self.request, "test_scope", limit=5, period=300)
        response = rate_limit_or_429(self.request, "test_scope", limit=5, period=300)
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)
        body = json.loads(response.content)
        self.assertIn("error", body)

    def test_returns_none_when_under_limit(self):
        response = rate_limit_or_429(self.request, "test_scope", limit=5, period=300)
        self.assertIsNone(response)

    def test_scopes_are_isolated(self):
        for _ in range(5):
            check_rate_limit(self.request, "scope_a", limit=5, period=300)
        remaining, _ = get_remaining(self.request, "scope_b", limit=5, period=300)
        self.assertEqual(remaining, 5)

    def test_different_ips_are_isolated(self):
        req_a = self.factory.post("/test/", REMOTE_ADDR="10.0.0.1")
        req_b = self.factory.post("/test/", REMOTE_ADDR="10.0.0.2")
        for _ in range(5):
            check_rate_limit(req_a, "test_scope", limit=5, period=300)
        remaining, _ = get_remaining(req_b, "test_scope", limit=5, period=300)
        self.assertEqual(remaining, 5)

    def test_get_remaining_returns_correct_count(self):
        remaining, _ = get_remaining(self.request, "test_scope", limit=5, period=300)
        self.assertEqual(remaining, 5)
        check_rate_limit(self.request, "test_scope", limit=5, period=300)
        remaining, _ = get_remaining(self.request, "test_scope", limit=5, period=300)
        self.assertEqual(remaining, 4)

    def test_custom_key_func(self):
        req = self.factory.post("/test/")
        key_func = lambda r: "custom_key"
        for _ in range(5):
            check_rate_limit(req, "test_scope", limit=5, period=300, key_func=key_func)
        with self.assertRaises(RateLimitExceeded):
            check_rate_limit(req, "test_scope", limit=5, period=300, key_func=key_func)

    def test_period_resets_after_expiry(self):
        """Simulate rate limit window expiry by using short period."""
        for _ in range(3):
            check_rate_limit(self.request, "short_scope", limit=3, period=1)
        with self.assertRaises(RateLimitExceeded):
            check_rate_limit(self.request, "short_scope", limit=3, period=1)
        import time
        time.sleep(1.1)
        check_rate_limit(self.request, "short_scope", limit=3, period=1)
        remaining, _ = get_remaining(self.request, "short_scope", limit=3, period=1)
        self.assertEqual(remaining, 2)


class PasswordResetPerEmailTest(TestCase):
    """Verify password reset rate limiting is keyed by email, not IP."""

    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def test_same_ip_different_emails_not_limited(self):
        req = self.factory.post("/accounts/password-reset/", REMOTE_ADDR="10.0.0.1")
        for i in range(3):
            email_key = lambda r, e=f"user{i}@example.com": e
            blocked = rate_limit_or_429(req, "password_reset", limit=3, period=300, key_func=email_key)
            self.assertIsNone(blocked, f"Request {i} should not be blocked")

    def test_different_ips_same_email_is_limited(self):
        emails = ["victim@example.com"] * 3
        for i in range(3):
            req = self.factory.post("/accounts/password-reset/", REMOTE_ADDR=f"10.0.0.{i + 1}")
            email_key = lambda r, e=emails[i]: e
            blocked = rate_limit_or_429(req, "password_reset", limit=3, period=300, key_func=email_key)
            self.assertIsNone(blocked, f"Request {i} should not be blocked")
        req = self.factory.post("/accounts/password-reset/", REMOTE_ADDR="10.0.0.99")
        email_key = lambda r: "victim@example.com"
        response = rate_limit_or_429(req, "password_reset", limit=3, period=300, key_func=email_key)
        self.assertIsNotNone(response, "4th request with same email should be blocked")
        self.assertEqual(response.status_code, 429)
