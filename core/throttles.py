"""
Simple IP-based rate limiting using Django's cache framework.

Usage in views:
    from core.throttles import check_rate_limit, RateLimitExceeded

    def post(self, request):
        check_rate_limit(request, "otp_request", limit=5, period=300)
        # ... normal view logic
"""

import time
from functools import wraps

from django.core.cache import cache
from django.http import JsonResponse

from core.utils import get_client_ip


class RateLimitExceeded(Exception):
    """Raised when a rate limit is exceeded."""
    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after}s.")


def _cache_key(prefix, identifier):
    return f"rl:{prefix}:{identifier}"


def check_rate_limit(request, scope, limit=5, period=300, key_func=None):
    """Check and enforce a rate limit.

    Args:
        request: Django HTTP request
        scope: String scope name (e.g. "otp_request", "password_reset")
        limit: Max allowed requests in the period
        period: Time window in seconds
        key_func: Optional callable(request) -> string identifier.
                  Defaults to client IP.

    Raises:
        RateLimitExceeded if limit is exceeded. Contains retry_after.
    """
    if key_func:
        identifier = key_func(request)
    else:
        identifier = get_client_ip(request) or "unknown"

    key = _cache_key(scope, identifier)
    now = time.time()

    # Sliding window: list of timestamps of requests within the period
    timestamps = cache.get(key, [])
    # Prune timestamps outside the window
    timestamps = [ts for ts in timestamps if now - ts < period]

    if len(timestamps) >= limit:
        oldest = timestamps[0]
        retry_after = int(period - (now - oldest)) + 1
        raise RateLimitExceeded(retry_after)

    timestamps.append(now)
    cache.set(key, timestamps, period)
    return True


def rate_limit_or_429(request, scope, limit=5, period=300, key_func=None):
    """Check rate limit and return JsonResponse(429) on exceed.

    Returns None if allowed, JsonResponse if blocked.
    """
    try:
        check_rate_limit(request, scope, limit, period, key_func)
    except RateLimitExceeded as exc:
        return JsonResponse(
            {"ok": False, "error": "Too many requests. Please try again later."},
            status=429,
            headers={"Retry-After": str(exc.retry_after)},
        )
    return None


def get_remaining(request, scope, limit=5, period=300, key_func=None):
    """Return (remaining, retry_after_after_exhaust)."""
    identifier = key_func(request) if key_func else (get_client_ip(request) or "unknown")
    key = _cache_key(scope, identifier)
    now = time.time()
    timestamps = cache.get(key, [])
    timestamps = [ts for ts in timestamps if now - ts < period]
    remaining = max(0, limit - len(timestamps))
    retry_after = 0
    if timestamps:
        retry_after = int(period - (now - timestamps[0])) + 1
    return remaining, retry_after
