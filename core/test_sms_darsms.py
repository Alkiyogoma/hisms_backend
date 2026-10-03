"""DarSMS bulk SMS provider: request format, phone normalisation, error handling,
OTP routing and the settings-page test button. The HTTP call is always mocked."""
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from attendance.models import Message
from attendance.notification_service import DarSMSProvider, NotificationService, normalize_tz_phone, sms_status
from core.models import SchoolSettings
from users.models import User, UserRole

DARSMS = dict(SMS_PROVIDER="darsms", DARSMS_API_KEY="darsms_test_key",
              DARSMS_API_URL="https://dev.darsms.co.tz/api/v1/integrations/sms/send", DARSMS_SENDER_ID="ENVSENDER")


def _set_sender(value):
    s = SchoolSettings.get_settings()
    s.sms_sender_id = value
    s.save()


def _response(status=200, payload=None):
    r = mock.Mock(status_code=status, text=str(payload))
    r.json.return_value = payload if payload is not None else {}
    return r


class NormalizePhoneTests(TestCase):
    def test_formats(self):
        for raw in ("0712 345 678", "+255 712-345-678", "255712345678", "712345678", "00255712345678"):
            self.assertEqual(normalize_tz_phone(raw), "255712345678", raw)
        for raw in ("", "12345", "0222123456", "+1 202 555 0100"):
            self.assertIsNone(normalize_tz_phone(raw), raw)


@override_settings(**DARSMS)
class DarSMSProviderTests(TestCase):
    @mock.patch("attendance.notification_service.requests.post")
    def test_bulk_send_posts_expected_payload(self, post):
        post.return_value = _response(200, {"success": True, "data": {"id": "batch-1"}})
        _set_sender("HODARI")
        result = NotificationService.send_bulk_sms(["0712345678", "+255754321987", "0712345678", "bad"], "Hello")
        self.assertTrue(result["success"])
        self.assertEqual(result["message_id"], "batch-1")
        self.assertEqual(result["invalid"], ["bad"])
        args, kwargs = post.call_args
        self.assertEqual(args[0], DARSMS["DARSMS_API_URL"])
        self.assertEqual(kwargs["headers"]["X-API-Key"], "darsms_test_key")
        self.assertEqual(kwargs["json"], {"senderId": "HODARI", "to": ["255712345678", "255754321987"], "message": "Hello"})

    @mock.patch("attendance.notification_service.requests.post")
    def test_sender_falls_back_to_env(self, post):
        post.return_value = _response(200, {})
        _set_sender("")
        NotificationService.send_sms("0712345678", "Hi")
        self.assertEqual(post.call_args.kwargs["json"]["senderId"], "ENVSENDER")

    @mock.patch("attendance.notification_service.requests.post")
    def test_api_errors_are_reported(self, post):
        post.return_value = _response(401, {"message": "Invalid API key"})
        result = NotificationService.send_sms("0712345678", "Hi")
        self.assertFalse(result["success"])
        self.assertIn("Invalid API key", result["error"])

        post.return_value = _response(200, {"success": False, "message": "Insufficient balance"})
        self.assertFalse(NotificationService.send_sms("0712345678", "Hi")["success"])

    @override_settings(DARSMS_API_KEY="")
    @mock.patch("attendance.notification_service.requests.post")
    def test_missing_key_does_not_call_api(self, post):
        result = DarSMSProvider().send("0712345678", "Hi")
        self.assertFalse(result["success"])
        self.assertIn("DARSMS_API_KEY", result["error"])
        post.assert_not_called()
        self.assertFalse(sms_status()["configured"])

    @mock.patch("attendance.notification_service.requests.post")
    def test_otp_goes_through_darsms(self, post):
        from communications.otp_service import generate_otp
        post.return_value = _response(200, {})
        generate_otp("0712345678")
        self.assertEqual(post.call_args.kwargs["json"]["to"], ["255712345678"])

    @mock.patch("attendance.notification_service.requests.post")
    def test_settings_page_test_sms(self, post):
        post.return_value = _response(200, {})
        admin = User.objects.create_superuser(username="sa", email="sa@example.test", password="pw-12345!",
                                              role=UserRole.SUPER_ADMIN)
        self.client.force_login(admin)
        url = reverse("core:school_settings") + "?tab=messaging"
        html = self.client.get(url).content.decode()
        self.assertIn("Send test SMS", html)
        self.assertNotIn("darsms_test_key", html)
        resp = self.client.post(url, {"action": "send_test_sms", "test_phone": "0712 345 678"}, follow=True)
        self.assertContains(resp, "Test SMS sent to 255712345678")


@override_settings(**DARSMS, SMS_MAX_AGE_MINUTES=15)
class SmsQueueTests(TestCase):
    def _age(self, msg, minutes):
        from datetime import timedelta
        from django.utils import timezone
        Message.objects.filter(pk=msg.pk).update(created_at=timezone.now() - timedelta(minutes=minutes))

    @mock.patch("attendance.tasks.send_sms_notification.delay")
    def test_queue_dispatches_after_commit(self, delay):
        with self.captureOnCommitCallbacks(execute=True):
            msg = Message.queue("0712345678", "Checked in")
        delay.assert_called_once_with(msg.id)

    @mock.patch("attendance.notification_service.requests.post")
    def test_task_sends_fresh_and_expires_old(self, post):
        from attendance.tasks import send_sms_notification
        post.return_value = _response(200, {})
        fresh = Message.objects.create(phone="0712345678", message="fresh")
        send_sms_notification.apply(args=[fresh.id])
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, 1)

        old = Message.objects.create(phone="0712345678", message="old")
        self._age(old, 20)
        post.reset_mock()
        send_sms_notification.apply(args=[old.id])
        old.refresh_from_db()
        self.assertEqual(old.status, 2)
        self.assertIn("Expired", old.error_message)
        post.assert_not_called()

    @mock.patch("attendance.tasks.send_sms_notification.delay")
    def test_sweep_sends_missed_expires_old_and_skips_imports(self, delay):
        from attendance.tasks import process_pending_sms
        missed = Message.objects.create(phone="0712345678", message="missed")
        self._age(missed, 5)
        just_queued = Message.objects.create(phone="0712345678", message="new")
        stale = Message.objects.create(phone="0712345678", message="stale")
        self._age(stale, 60)
        imported = Message.objects.create(phone="0712345678", message="laravel", laravel_message_id=99)
        self._age(imported, 5)

        process_pending_sms()

        delay.assert_called_once_with(missed.id)
        stale.refresh_from_db()
        self.assertEqual((stale.status, stale.retry_count), (2, 3))
        just_queued.refresh_from_db()
        imported.refresh_from_db()
        self.assertEqual((just_queued.status, imported.status), (0, 0))

    def test_migration_expires_backlog(self):
        import importlib
        from django.apps import apps
        mig = importlib.import_module("attendance.migrations.0010_expire_stale_sms_queue")
        pending = Message.objects.create(phone="0712345678", message="old", status=0)
        sent = Message.objects.create(phone="0712345678", message="ok", status=1)
        mig.expire_stale_sms(apps, None)
        pending.refresh_from_db()
        sent.refresh_from_db()
        self.assertEqual((pending.status, pending.retry_count, sent.status), (2, 3, 1))
