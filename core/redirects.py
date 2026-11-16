"""Where to send someone after sign-in, and how to send background requests
to sign-in when their session has gone.

A session that times out used to remember whatever URL the expiring request
hit: a partial loaded by htmx, a JSON endpoint, a form's POST target. Signing
in again then landed on a URL that is not a page (404/405/raw JSON). These
helpers keep ``next`` to real pages and turn background requests into a
full-page redirect instead of swapping the login page into a fragment.
"""

from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.urls import Resolver404, resolve
from django.utils.http import url_has_allowed_host_and_scheme

PARENT_LOGIN_URL = "/parent/login/"

# Paths that are never a sensible place to land after signing in.
_NOT_A_PAGE_PREFIXES = (
    "/api/",
    "/accounts/login",
    "/accounts/logout",
    "/login/",
    "/logout/",
    "/parent/login",
    "/parent/logout",
    "/static/",
    "/media/",
)


def is_background_request(request) -> bool:
    """True for htmx, fetch/XHR and JSON API requests (not a full page load)."""
    if request.headers.get("HX-Request") == "true":
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    if request.path.startswith("/api/") or "/api/" in request.path:
        return True
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


def _same_host_path(request, url):
    """Path + query of ``url`` if it points at this site, else ''."""
    if not url or not url_has_allowed_host_and_scheme(
            url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return ""
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def safe_next_url(request, url, fallback=""):
    """Return ``url`` if it is a same-site page that exists, else ``fallback``."""
    path_and_query = _same_host_path(request, url)
    if not path_and_query:
        return fallback
    path = urlsplit(path_and_query).path
    if not path.startswith("/") or path.startswith(_NOT_A_PAGE_PREFIXES) or "/api/" in path:
        return fallback
    try:
        resolve(path)
    except Resolver404:
        return fallback
    return path_and_query


def page_to_return_to(request):
    """The page the user was looking at when this request was made.

    For a normal GET that is the request itself; for htmx it is the page that
    issued the request; for a form POST it is the page the form was on.
    """
    if request.headers.get("HX-Request") == "true":
        return safe_next_url(request, request.headers.get("HX-Current-URL", ""))
    if request.method == "GET" and not is_background_request(request):
        return safe_next_url(request, request.get_full_path())
    return safe_next_url(request, request.headers.get("Referer", ""))


def login_url_for(is_parent, next_path="", session_expired=False):
    if is_parent:
        params = {}
        base = PARENT_LOGIN_URL
    else:
        params = {"session_expired": 1, "staff": 1} if session_expired else {"staff": 1}
        base = settings.LOGIN_URL
    if next_path:
        params["next"] = next_path
    return base + ("?" + urlencode(params) if params else "")


def redirect_background_to_login(request, login_url):
    """Make a background request send the browser to ``login_url``.

    htmx follows ``HX-Redirect`` with a full page load; fetch callers get a
    401 with the URL so they can do the same.
    """
    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=200)
        response["HX-Redirect"] = login_url
        return response
    return JsonResponse(
        {"error": "session_expired", "detail": "Your session has expired. Please sign in again.",
         "redirect": login_url},
        status=401,
    )
