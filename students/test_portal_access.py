"""Existing parents can be given portal access, or sent new login details,
from the parent page."""
from unittest import mock

from django.contrib.messages import get_messages
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from students.models import ParentGuardian
from users.models import User, UserRole


SMS_ON = mock.patch("attendance.notification_service.sms_status", return_value={"configured": True})
SMS_OFF = mock.patch("attendance.notification_service.sms_status", return_value={"configured": False})


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class GuardianPortalAccessTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="registrar", email="registrar@hodari.edu", password="Pass123!",
            role=UserRole.SUPER_ADMIN,
        )
        self.client.force_login(self.admin)
        self.guardian = ParentGuardian.objects.create(
            full_name="Mary Kajeguka", phone="+255767000001", email="mary@example.com",
        )
        self.url = reverse("students:guardian_portal_access", args=[self.guardian.pk])

    def test_detail_page_offers_create_then_resend(self):
        detail = reverse("students:guardian_detail", args=[self.guardian.pk])
        self.assertIn("Create Portal Access", self.client.get(detail).content.decode())
        self.client.post(self.url)
        content = self.client.get(detail).content.decode()
        self.assertIn("Resend Login Details", content)
        self.assertIn("parent_255767000001", content)

    def test_creates_parent_login_and_emails_username(self):
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 302)
        self.guardian.refresh_from_db()
        user = self.guardian.user
        self.assertEqual(user.role, UserRole.PARENT)
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.groups.filter(name="role_parent").exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(user.username, mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["mary@example.com"])

    def test_resend_sets_new_password_on_same_login(self):
        self.client.post(self.url)
        self.guardian.refresh_from_db()
        user = self.guardian.user
        old_hash = user.password
        user.must_change_password = False
        user.save()

        self.client.post(self.url)
        user.refresh_from_db()
        self.assertNotEqual(user.password, old_hash)
        self.assertTrue(user.must_change_password)
        self.assertEqual(User.objects.filter(role=UserRole.PARENT).count(), 1)
        self.assertEqual(len(mail.outbox), 2)

    def test_parents_without_email_do_not_collide(self):
        other = ParentGuardian.objects.create(full_name="John Kajeguka", phone="+255767000002")
        self.guardian.email = ""
        self.guardian.save()
        self.client.post(self.url)
        self.client.post(reverse("students:guardian_portal_access", args=[other.pk]))
        self.guardian.refresh_from_db()
        other.refresh_from_db()
        self.assertIsNotNone(self.guardian.user)
        self.assertIsNotNone(other.user)
        self.assertNotEqual(self.guardian.user.email, other.user.email)

    def test_archived_parent_is_refused(self):
        self.guardian.is_archived = True
        self.guardian.save()
        self.client.post(self.url)
        self.guardian.refresh_from_db()
        self.assertIsNone(self.guardian.user)

    def test_needs_change_permission(self):
        teacher = User.objects.create_user(username="t1", email="t1@hodari.edu", password="Pass123!",
                                           role=UserRole.TEACHER)
        self.client.force_login(teacher)
        self.client.post(self.url)
        self.guardian.refresh_from_db()
        self.assertIsNone(self.guardian.user)

    def _message(self, resp):
        return " ".join(str(m) for m in get_messages(resp.wsgi_request))

    @SMS_ON
    @mock.patch("attendance.notification_service.NotificationService.send_sms", return_value={"success": True})
    def test_sends_email_and_sms_when_sms_is_set_up(self, send_sms, _status):
        resp = self.client.post(self.url)
        self.guardian.refresh_from_db()
        self.assertEqual(len(mail.outbox), 1)
        send_sms.assert_called_once()
        phone, text = send_sms.call_args.args
        self.assertEqual(phone, "+255767000001")
        self.assertIn(self.guardian.user.username, text)
        message = self._message(resp)
        self.assertIn("emailed to mary@example.com", message)
        self.assertIn("sent by SMS to +255767000001", message)

    @SMS_OFF
    @mock.patch("attendance.notification_service.NotificationService.send_sms")
    def test_skips_sms_when_not_set_up(self, send_sms, _status):
        self.client.post(self.url)
        self.assertEqual(len(mail.outbox), 1)
        send_sms.assert_not_called()

    @SMS_OFF
    def test_warns_when_nothing_could_be_sent(self, _status):
        self.guardian.email = ""
        self.guardian.save()
        resp = self.client.post(self.url)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn("were NOT sent", self._message(resp))
