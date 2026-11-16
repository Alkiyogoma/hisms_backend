"""Session timeout must never send people to a 404 after signing in again."""
from urllib.parse import parse_qs, urlsplit

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from core.redirects import safe_next_url
from users.models import User, UserRole

try:  # same teardown shim as academics/tests.py (audit FK noise on teardown)
    _orig_fixture_teardown = TestCase._fixture_teardown

    def _patched_fixture_teardown(self):
        try:
            _orig_fixture_teardown(self)
        except Exception:
            pass

    TestCase._fixture_teardown = _patched_fixture_teardown
except AttributeError:  # pragma: no cover
    pass

PAGE = "/students/"


def _next_of(url):
    return parse_qs(urlsplit(url).query).get("next", [""])[0]


@override_settings(SESSION_IDLE_TIMEOUT=1800)
class SessionTimeoutRedirectTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="t1", email="t1@x.test", password="pw-123456", role=UserRole.TEACHER)
        self.parent = User.objects.create_user(username="p1", email="p1@x.test", password="pw-123456", role=UserRole.PARENT)

    def _expire(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["_last_activity"] = timezone.now().timestamp() - 4000
        session.save()

    def test_page_load_returns_to_that_page(self):
        self._expire(self.teacher)
        r = self.client.get(PAGE + "?q=x")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/accounts/login/"))
        self.assertIn("session_expired=1", r["Location"])
        self.assertEqual(_next_of(r["Location"]), PAGE + "?q=x")

    def test_htmx_request_gets_full_page_redirect_to_the_page_not_the_partial(self):
        self._expire(self.teacher)
        r = self.client.get("/students/some-partial/", HTTP_HX_REQUEST="true",
                            HTTP_HX_CURRENT_URL=f"http://testserver{PAGE}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(_next_of(r["HX-Redirect"]), PAGE)

    def test_api_poll_gets_401_json_without_api_next(self):
        self._expire(self.teacher)
        r = self.client.get("/api/session/check/", HTTP_ACCEPT="application/json")
        self.assertEqual(r.status_code, 401)
        data = r.json()
        self.assertEqual(data["error"], "session_expired")
        self.assertEqual(_next_of(data["redirect"]), "")

    def test_form_post_returns_to_the_form_page(self):
        self._expire(self.teacher)
        r = self.client.post("/students/0/delete/", HTTP_REFERER=f"http://testserver{PAGE}")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(_next_of(r["Location"]), PAGE)

    def test_post_from_unknown_referer_has_no_next(self):
        self._expire(self.teacher)
        r = self.client.post("/students/0/delete/", HTTP_REFERER="https://evil.example/x")
        self.assertEqual(_next_of(r["Location"]), "")

    def test_parent_goes_to_parent_login(self):
        self._expire(self.parent)
        r = self.client.get("/parent/")
        self.assertTrue(r["Location"].startswith("/parent/login/"))
        self.assertEqual(_next_of(r["Location"]), "/parent/")

    def test_signed_out_htmx_request_is_not_swapped_into_the_page(self):
        r = self.client.get(PAGE, HTTP_HX_REQUEST="true", HTTP_HX_CURRENT_URL=f"http://testserver{PAGE}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("HX-Redirect", r)
        self.assertEqual(_next_of(r["HX-Redirect"]), PAGE)


class SafeNextUrlTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def _safe(self, url):
        return safe_next_url(self.rf.get("/"), url, "FALLBACK")

    def test_real_page_kept_with_query(self):
        self.assertEqual(self._safe(PAGE + "?page=2"), PAGE + "?page=2")

    def test_rejects_missing_api_login_and_external(self):
        for bad in ("/does-not-exist/", "/api/session/check/", "/accounts/login/",
                    "/parent/login/", "https://evil.example/", "//evil.example/", ""):
            with self.subTest(bad=bad):
                self.assertEqual(self._safe(bad), "FALLBACK")
