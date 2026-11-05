"""Parent sign-in by phone/email + 4-digit one-time code (Parent Portal v1)."""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from students.models import ParentGuardian, Student, StudentGuardian, StudentStatus
from users.models import User, UserRole


@override_settings(DEBUG=False)
class ParentCodeLoginTests(TestCase):
    url = reverse("parent_portal:login")

    def setUp(self):
        cache.clear()
        self.sent = []
        patcher = mock.patch("parent_portal.auth_views._deliver",
                             side_effect=lambda chan, addr, code: self.sent.append((chan, addr, code)) or True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.student = Student.objects.create(admission_no="PL-001", first_name="Amani", last_name="Mwakasege",
                                              class_name="Grade 3", status=StudentStatus.ACTIVE)
        # Stored in a "human" format on purpose: lookup must normalise it.
        self.guardian = ParentGuardian.objects.create(full_name="Esther Mwakasege", phone="0712 345 678",
                                                      email="parent.sample@gmail.com")
        StudentGuardian.objects.create(student=self.student, guardian=self.guardian, relationship="parent",
                                       is_primary=True)

    def _lookup(self, who):
        return self.client.post(self.url, {"action": "lookup", "who": who})

    def _verify(self, code):
        return self.client.post(self.url, {"action": "verify", "code": code})

    def test_phone_sign_in_creates_portal_login_without_activation(self):
        self.assertIsNone(self.guardian.user)
        resp = self._lookup("+255 712 345 678")
        self.assertEqual(resp.status_code, 302)
        chan, addr, code = self.sent[-1]
        self.assertEqual((chan, addr, len(code)), ("sms", "255712345678", 4))

        page = self.client.get(self.url)
        self.assertContains(page, "•••• 678")
        self.assertContains(page, "Enter your code")

        resp = self._verify(code)
        self.assertRedirects(resp, reverse("parent_portal:dashboard"), fetch_redirect_response=False)
        self.guardian.refresh_from_db()
        user = self.guardian.user
        self.assertIsNotNone(user)
        self.assertEqual(user.role, UserRole.PARENT)
        self.assertTrue(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_email_sign_in_reenables_switched_off_login(self):
        user = User.objects.create_user(username="esther", email="parent.sample@gmail.com", password="x-Pass-123",
                                        role=UserRole.PARENT, is_active=False)
        user.must_change_password = True
        user.save()
        self.guardian.user = user
        self.guardian.save()

        self._lookup("Parent.Sample@gmail.com")
        chan, addr, code = self.sent[-1]
        self.assertEqual(chan, "email")
        self.assertContains(self.client.get(self.url), "p•••••••••••e@gmail.com")
        self._verify(code)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertFalse(user.must_change_password)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_unknown_number_says_not_found_and_sends_nothing(self):
        resp = self._lookup("0799 000 111")
        self.assertContains(resp, "can&#x27;t find that phone number or email")
        self.assertEqual(self.sent, [])

    def test_archived_parent_and_parent_without_active_child_cannot_sign_in(self):
        self.guardian.is_archived = True
        self.guardian.save()
        self.assertContains(self._lookup("0712345678"), "can&#x27;t find")
        self.guardian.is_archived = False
        self.guardian.save()
        Student.objects.filter(pk=self.student.pk).update(status=StudentStatus.WITHDRAWN
                                                          if hasattr(StudentStatus, "WITHDRAWN") else "inactive")
        self.assertContains(self._lookup("0712345678"), "can&#x27;t find")
        self.assertEqual(self.sent, [])

    def test_wrong_code_is_refused_and_too_many_tries_void_the_code(self):
        self._lookup("0712345678")
        code = self.sent[-1][2]
        wrong = "0000" if code != "0000" else "1111"
        self.assertContains(self._verify(wrong), "That code is not right")
        for _ in range(4):
            resp = self._verify(wrong)
        self.assertContains(resp, "Too many wrong tries")
        self._verify(code)  # the right code no longer works once voided
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_resend_is_locked_for_45_seconds(self):
        self._lookup("0712345678")
        self.client.post(self.url, {"action": "resend"})
        self.assertEqual(len(self.sent), 1)
