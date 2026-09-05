"""
Unit tests for waitlist BDD scenarios:

  S2 — Space-available notification
    - ENROLLED→WITHDRAWN triggers notification to Admin with grade + count
    - ADMITTED→WITHDRAWN triggers notification to Admin with grade + count
    - DENIED→WITHDRAWN does NOT trigger notification
    - WITHDRAWN with no waitlisted applicants does NOT trigger notification

  S3 — Per-grade waitlist counts
    - WaitlistView returns grade_waitlist_counts aggregated correctly
    - Counts match actual DB state per grade
"""
from unittest.mock import patch

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.utils import timezone

from admissions.models import (
    Applicant,
    ApplicantStatus,
)
from admissions.services import transition_applicant_status
from users.models import UserRole

User = get_user_model()


def _create_user(username, role=UserRole.ADMIN_OFFICER, **kwargs):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@test.hodari.edu",
        password="TestPass123!",
        role=role,
        is_staff=True,
        **kwargs,
    )
    # Assign admissions permissions so permission-based views work in tests
    from django.contrib.auth.models import Permission, Group
    group_name = f"role_{role}"
    group, _ = Group.objects.get_or_create(name=group_name)
    user.groups.add(group)
    # Also grant all admissions permissions for test flexibility
    admissions_perms = Permission.objects.filter(content_type__app_label="admissions")
    group.permissions.add(*admissions_perms)
    return user


def _create_applicant(grade="Grade 1", status=ApplicantStatus.INQUIRY_RECEIVED, **overrides):
    """Create an Applicant with sensible defaults."""
    defaults = dict(
        parent_full_name="Test Parent",
        parent_phone="+255700000000",
        parent_email="parent@test.com",
        child_full_name="Test Child",
        child_date_of_birth="2018-01-15",
        grade_applying_for=grade,
        status=status,
    )
    defaults.update(overrides)
    return Applicant.objects.create(**defaults)


# ═══════════════════════════════════════════════════════════════════════════
# S2 — Space-available notification on withdrawal
# ═══════════════════════════════════════════════════════════════════════════

@patch("communications.email_service.dispatch_notification")
class SpaceAvailableNotificationTests(TestCase):
    """Test NOTIF-09: Admin Officer is notified when a space opens in a grade
    that has waitlisted applicants."""

    def setUp(self):
        self.admin = _create_user("admin1", role=UserRole.ADMIN_OFFICER)
        self.hos = _create_user("hos_user", role=UserRole.HEAD_OF_SCHOOL)

    # ── ENROLLED → WITHDRAWN ──────────────────────────────────────────────

    def test_enrolled_withdrawn_triggers_notification(self, mock_dispatch):
        """BDD: GIVEN an enrolled student withdraws
        WHEN the space is created
        THEN Admin Officer receives notification with grade + waitlist count."""
        # Arrange: 2 waitlisted applicants for Grade 1
        _create_applicant(grade="Grade 1", status=ApplicantStatus.WAITLISTED, child_full_name="Wait 1")
        _create_applicant(grade="Grade 1", status=ApplicantStatus.WAITLISTED, child_full_name="Wait 2")
        enrolled = _create_applicant(grade="Grade 1", status=ApplicantStatus.ENROLLED, child_full_name="Enrolled Kid")

        # Act
        transition_applicant_status(
            applicant=enrolled,
            to_status=ApplicantStatus.WITHDRAWN,
            actor=self.hos,
            reason="Parent relocated",
        )

        # Assert: notification dispatched to Admin Officers
        admin_calls = [
            c for c in mock_dispatch.call_args_list
            if c.kwargs.get("user") and c.kwargs["user"].role == UserRole.ADMIN_OFFICER
        ]
        self.assertTrue(len(admin_calls) >= 1, "Admin Officer should be notified")
        msg = admin_calls[0].kwargs["message"]
        self.assertIn("Grade 1", msg)
        self.assertIn("2", msg)  # 2 waitlisted applicants

    # ── ADMITTED → WITHDRAWN ──────────────────────────────────────────────

    def test_admitted_withdrawn_triggers_notification(self, mock_dispatch):
        """BDD: WHEN an admitted student withdraws, Admin is notified."""
        _create_applicant(grade="Grade 3", status=ApplicantStatus.WAITLISTED, child_full_name="WL A")
        admitted = _create_applicant(grade="Grade 3", status=ApplicantStatus.ADMITTED, child_full_name="Admitted Kid")

        transition_applicant_status(
            applicant=admitted,
            to_status=ApplicantStatus.WITHDRAWN,
            actor=self.hos,
        )

        admin_calls = [
            c for c in mock_dispatch.call_args_list
            if c.kwargs.get("user") and c.kwargs["user"].role == UserRole.ADMIN_OFFICER
        ]
        self.assertTrue(len(admin_calls) >= 1, "Admin Officer should be notified")
        msg = admin_calls[0].kwargs["message"]
        self.assertIn("Grade 3", msg)
        self.assertIn("1", msg)  # 1 waitlisted

    # ── CONDITIONAL → WITHDRAWN ────────────────────────────────────────────

    def test_conditional_withdrawn_triggers_notification(self, mock_dispatch):
        """Conditional applicant withdrawal also frees a seat."""
        _create_applicant(grade="Grade 5", status=ApplicantStatus.WAITLISTED, child_full_name="WL B")
        conditional = _create_applicant(grade="Grade 5", status=ApplicantStatus.CONDITIONAL, child_full_name="Cond Kid")

        transition_applicant_status(
            applicant=conditional,
            to_status=ApplicantStatus.WITHDRAWN,
            actor=self.hos,
        )

        admin_calls = [
            c for c in mock_dispatch.call_args_list
            if c.kwargs.get("user") and c.kwargs["user"].role == UserRole.ADMIN_OFFICER
        ]
        self.assertTrue(len(admin_calls) >= 1, "Admin Officer notified for conditional withdrawal")
        self.assertIn("Grade 5", admin_calls[0].kwargs["message"])

    # ── DENIED → WITHDRAWN (should NOT notify) ────────────────────────────

    def test_denied_withdrawn_does_not_trigger_notification(self, mock_dispatch):
        """BDD: DENIED→WITHDRAWN should NOT trigger space-available notification."""
        _create_applicant(grade="Grade 2", status=ApplicantStatus.WAITLISTED, child_full_name="WL C")
        denied = _create_applicant(grade="Grade 2", status=ApplicantStatus.DENIED, child_full_name="Denied Kid")

        transition_applicant_status(
            applicant=denied,
            to_status=ApplicantStatus.WITHDRAWN,
            actor=self.hos,
        )

        # Filter for "Space Available" notifications only
        space_calls = [
            c for c in mock_dispatch.call_args_list
            if "Space Available" in (c.kwargs.get("title") or "")
        ]
        self.assertEqual(len(space_calls), 0, "No space-available notification for DENIED→WITHDRAWN")

    # ── No waitlisted applicants → no notification ─────────────────────────

    def test_withdrawn_no_waitlisted_does_not_trigger_notification(self, mock_dispatch):
        """If no one is on the waitlist for that grade, no notification fires."""
        enrolled = _create_applicant(grade="Grade 4", status=ApplicantStatus.ENROLLED, child_full_name="Only Kid")

        transition_applicant_status(
            applicant=enrolled,
            to_status=ApplicantStatus.WITHDRAWN,
            actor=self.hos,
        )

        space_calls = [
            c for c in mock_dispatch.call_args_list
            if "Space Available" in (c.kwargs.get("title") or "")
        ]
        self.assertEqual(len(space_calls), 0, "No notification when no one is waitlisted")

    # ── Correct count in notification ──────────────────────────────────────

    def test_notification_includes_correct_waitlist_count(self, mock_dispatch):
        """The notification message must include the exact number of waitlisted applicants."""
        for i in range(5):
            _create_applicant(grade="Grade 6", status=ApplicantStatus.WAITLISTED, child_full_name=f"WL {i}")
        enrolled = _create_applicant(grade="Grade 6", status=ApplicantStatus.ENROLLED, child_full_name="Withdraw")

        transition_applicant_status(
            applicant=enrolled,
            to_status=ApplicantStatus.WITHDRAWN,
            actor=self.hos,
        )

        admin_calls = [
            c for c in mock_dispatch.call_args_list
            if c.kwargs.get("user") and c.kwargs["user"].role == UserRole.ADMIN_OFFICER
        ]
        self.assertTrue(len(admin_calls) >= 1)
        msg = admin_calls[0].kwargs["message"]
        self.assertIn("5", msg, "Notification should say 5 applicants are on the waitlist")

    # ── Notification links to waitlist view ────────────────────────────────

    def test_notification_links_to_waitlist_view(self, mock_dispatch):
        """The notification link should point to the waitlist page."""
        _create_applicant(grade="Grade 1", status=ApplicantStatus.WAITLISTED)
        enrolled = _create_applicant(grade="Grade 1", status=ApplicantStatus.ENROLLED)

        transition_applicant_status(
            applicant=enrolled,
            to_status=ApplicantStatus.WITHDRAWN,
            actor=self.hos,
        )

        admin_calls = [
            c for c in mock_dispatch.call_args_list
            if c.kwargs.get("user") and c.kwargs["user"].role == UserRole.ADMIN_OFFICER
        ]
        self.assertTrue(len(admin_calls) >= 1)
        link = admin_calls[0].kwargs.get("link", "")
        self.assertIn("waitlist", link, "Notification link should point to waitlist view")


# ═══════════════════════════════════════════════════════════════════════════
# S3 — Per-grade waitlist counts
# ═══════════════════════════════════════════════════════════════════════════

class WaitlistViewGradeCountsTests(TestCase):
    """Test that WaitlistView returns correct per-grade waitlist counts."""

    def setUp(self):
        self.admin = _create_user("admin_wl", role=UserRole.ADMIN_OFFICER)
        self.client = Client()
        self.client.force_login(self.admin)

    def test_empty_waitlist_returns_empty_counts(self):
        """No waitlisted applicants → empty grade_waitlist_counts."""
        response = self.client.get("/admissions/waitlist/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["grade_waitlist_counts"], [])

    def test_single_grade_count(self):
        """3 applicants waitlisted in Grade 1 → one entry with count=3."""
        for i in range(3):
            _create_applicant(grade="Grade 1", status=ApplicantStatus.WAITLISTED, child_full_name=f"Child {i}")

        response = self.client.get("/admissions/waitlist/")
        counts = response.context["grade_waitlist_counts"]
        self.assertEqual(len(counts), 1)
        self.assertEqual(counts[0]["grade"], "Grade 1")
        self.assertEqual(counts[0]["count"], 3)

    def test_multi_grade_counts_sorted_by_count_desc(self):
        """Multiple grades → sorted by count descending, then grade name."""
        for i in range(4):
            _create_applicant(grade="Grade 2", status=ApplicantStatus.WAITLISTED, child_full_name=f"G2-{i}")
        for i in range(2):
            _create_applicant(grade="Grade 5", status=ApplicantStatus.WAITLISTED, child_full_name=f"G5-{i}")
        for i in range(1):
            _create_applicant(grade="Grade 1", status=ApplicantStatus.WAITLISTED, child_full_name=f"G1-{i}")

        response = self.client.get("/admissions/waitlist/")
        counts = response.context["grade_waitlist_counts"]

        self.assertEqual(len(counts), 3)
        # Sorted by count descending
        self.assertEqual(counts[0]["grade"], "Grade 2")
        self.assertEqual(counts[0]["count"], 4)
        self.assertEqual(counts[1]["grade"], "Grade 5")
        self.assertEqual(counts[1]["count"], 2)
        self.assertEqual(counts[2]["grade"], "Grade 1")
        self.assertEqual(counts[2]["count"], 1)

    def test_only_waitlisted_applicants_counted(self):
        """Non-waitlisted applicants in same grade should NOT be counted."""
        _create_applicant(grade="Grade 3", status=ApplicantStatus.WAITLISTED, child_full_name="WL Kid")
        _create_applicant(grade="Grade 3", status=ApplicantStatus.ENROLLED, child_full_name="Enrolled Kid")
        _create_applicant(grade="Grade 3", status=ApplicantStatus.ADMITTED, child_full_name="Admitted Kid")

        response = self.client.get("/admissions/waitlist/")
        counts = response.context["grade_waitlist_counts"]
        self.assertEqual(len(counts), 1)
        self.assertEqual(counts[0]["count"], 1, "Only waitlisted applicants should be counted")

    def test_items_still_ordered_by_created_at(self):
        """The items list should still be ordered oldest-first (created_at)."""
        older = _create_applicant(grade="Grade 1", status=ApplicantStatus.WAITLISTED, child_full_name="Older")
        older.created_at = timezone.now() - timezone.timedelta(days=10)
        older.save(update_fields=["created_at"])

        newer = _create_applicant(grade="Grade 1", status=ApplicantStatus.WAITLISTED, child_full_name="Newer")
        newer.created_at = timezone.now() - timezone.timedelta(days=1)
        newer.save(update_fields=["created_at"])

        response = self.client.get("/admissions/waitlist/")
        items = list(response.context["items"])
        self.assertEqual(items[0].child_full_name, "Older")
        self.assertEqual(items[1].child_full_name, "Newer")
