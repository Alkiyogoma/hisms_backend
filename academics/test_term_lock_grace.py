"""
Terms (and academic years) stay open for TERM_LOCK_GRACE_DAYS (30) days after
their end date and lock on day 31: the daily lock command, the ECD evaluation
gate and the year validation all use the same rule.
"""
import io
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import (
    TERM_LOCK_GRACE_DAYS, AcademicYear, Term, editable_until, is_past_lock_date, lock_cutoff,
)
from academics.views.ecd import ecd_term_editable


class LockDateRuleTests(TestCase):
    END = date(2026, 12, 11)

    def test_grace_is_thirty_days(self):
        self.assertEqual(TERM_LOCK_GRACE_DAYS, 30)
        self.assertEqual(editable_until(self.END), date(2027, 1, 10))

    def test_edge_days(self):
        self.assertFalse(is_past_lock_date(self.END, today=self.END))
        self.assertFalse(is_past_lock_date(self.END, today=self.END + timedelta(days=30)))
        self.assertTrue(is_past_lock_date(self.END, today=self.END + timedelta(days=31)))

    def test_no_end_date_never_locks(self):
        self.assertFalse(is_past_lock_date(None, today=self.END))

    def test_term_and_year_helpers(self):
        year = AcademicYear.objects.create(name="2026/2027", end_date=self.END)
        term = Term.objects.create(academic_year=year, name="Term 1",
                                   start_date=date(2026, 8, 31), end_date=self.END)
        self.assertEqual(term.editable_until, date(2027, 1, 10))
        self.assertEqual(year.editable_until, date(2027, 1, 10))
        self.assertFalse(term.is_past_lock_date(self.END + timedelta(days=30)))
        self.assertTrue(term.is_past_lock_date(self.END + timedelta(days=31)))
        self.assertTrue(year.is_past_lock_date(self.END + timedelta(days=31)))

    def test_queryset_cutoff_matches_rule(self):
        year = AcademicYear.objects.create(name="2026/2027")
        term = Term.objects.create(academic_year=year, name="Term 1",
                                   start_date=date(2026, 8, 31), end_date=self.END)
        open_qs = lambda today: Term.objects.filter(pk=term.pk, end_date__gte=lock_cutoff(today))  # noqa: E731
        self.assertTrue(open_qs(self.END).exists())
        self.assertTrue(open_qs(self.END + timedelta(days=30)).exists())
        self.assertFalse(open_qs(self.END + timedelta(days=31)).exists())


class LockCommandTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.year = AcademicYear.objects.create(name="2026/2027")

    def _term(self, name, days_since_end):
        end = self.today - timedelta(days=days_since_end)
        return Term.objects.create(academic_year=self.year, name=name,
                                   start_date=end - timedelta(days=60), end_date=end)

    def _run(self):
        call_command("check_and_advance_term", stdout=io.StringIO())

    def test_term_on_its_end_date_stays_open(self):
        term = self._term("Term 1", 0)
        self._run()
        term.refresh_from_db()
        self.assertFalse(term.is_locked)

    def test_term_on_last_grace_day_stays_open(self):
        term = self._term("Term 1", 30)
        self._run()
        term.refresh_from_db()
        self.assertFalse(term.is_locked)

    def test_term_locks_on_day_31(self):
        term = self._term("Term 1", 31)
        self._run()
        term.refresh_from_db()
        self.assertTrue(term.is_locked)

    def test_overdue_backlog_all_locked_and_recent_kept_open(self):
        old = self._term("Term 1", 200)
        recent = self._term("Term 2", 31)
        still_open = self._term("Term 3", 10)
        self._run()
        for t in (old, recent, still_open):
            t.refresh_from_db()
        self.assertTrue(old.is_locked)
        self.assertTrue(recent.is_locked)
        self.assertFalse(still_open.is_locked)


class ECDGraceTests(TestCase):
    """An ended term can still be evaluated during the grace period, even
    after the next term has started and become the current term."""

    def setUp(self):
        self.today = timezone.localdate()
        self.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        Term.objects.create(academic_year=self.year, name="Current",
                            start_date=self.today - timedelta(days=5),
                            end_date=self.today + timedelta(days=60))

    def _ended(self, name, days_since_end, **kw):
        end = self.today - timedelta(days=days_since_end)
        return Term.objects.create(academic_year=self.year, name=name,
                                   start_date=end - timedelta(days=60), end_date=end, **kw)

    def test_ended_yesterday_is_editable(self):
        self.assertTrue(ecd_term_editable(self._ended("A", 1)))

    def test_ended_30_days_ago_is_editable(self):
        self.assertTrue(ecd_term_editable(self._ended("A", 30)))

    def test_ended_31_days_ago_is_read_only(self):
        self.assertFalse(ecd_term_editable(self._ended("A", 31)))

    def test_locked_term_is_read_only(self):
        self.assertFalse(ecd_term_editable(self._ended("A", 5, is_locked=True)))

    def test_future_term_is_read_only(self):
        future = Term.objects.create(academic_year=self.year, name="Next",
                                     start_date=self.today + timedelta(days=70),
                                     end_date=self.today + timedelta(days=150))
        self.assertFalse(ecd_term_editable(future))


class AcademicYearGraceTests(TestCase):
    def _year(self, days_since_end):
        today = timezone.localdate()
        return AcademicYear(name="2025/2026", is_current=True,
                            start_date=today - timedelta(days=days_since_end + 300),
                            end_date=today - timedelta(days=days_since_end))

    def test_can_stay_current_within_grace(self):
        self._year(30).clean()  # no error

    def test_cannot_be_current_after_grace(self):
        with self.assertRaises(ValidationError):
            self._year(31).clean()


class SettingsPageGraceTests(TestCase):
    """System Settings → Academic Years keeps an ended term editable (with the
    date it locks) during the grace period and read-only after it."""

    @classmethod
    def setUpTestData(cls):
        from users.models import User, UserRole
        cls.admin = User.objects.create_user(username="sa", email="sa@example.test", password="x",
                                             role=UserRole.SUPER_ADMIN, is_superuser=True, is_staff=True)

    def setUp(self):
        self.client.force_login(self.admin)
        self.today = timezone.localdate()
        self.year = AcademicYear.objects.create(name="2026/2027", is_current=True,
                                                start_date=self.today - timedelta(days=300),
                                                end_date=self.today + timedelta(days=60))

    def _page(self):
        from django.urls import reverse
        return self.client.get(reverse("core:school_settings") + "?tab=academic_year").content.decode()

    def test_term_within_grace_is_editable(self):
        term = Term.objects.create(academic_year=self.year, name="Term 1",
                                   start_date=self.today - timedelta(days=90),
                                   end_date=self.today - timedelta(days=30))
        page = self._page()
        self.assertIn(f"Editable until {term.editable_until:%d %b %Y}", page)
        self.assertIn(f"openTermDrawer({{pk:'{term.pk}'", page)  # Edit button
        self.assertNotIn("Read-only", page)

    def test_term_past_grace_is_read_only(self):
        term = Term.objects.create(academic_year=self.year, name="Term 1",
                                   start_date=self.today - timedelta(days=90),
                                   end_date=self.today - timedelta(days=31))
        page = self._page()
        self.assertNotIn("Editable until", page)
        self.assertNotIn(f"openTermDrawer({{pk:'{term.pk}'", page)
        self.assertIn("Read-only", page)


class GradingDeadlineTests(TestCase):
    def test_deadline_may_be_before_marking_window_ends(self):
        year = AcademicYear.objects.create(name="2026/2027")
        term = Term(academic_year=year, name="Term 1", start_date=date(2026, 8, 31),
                    end_date=date(2026, 12, 11), endterm_exam_start_date=date(2026, 11, 23),
                    endterm_exam_end_date=date(2026, 11, 27), grading_deadline=date(2026, 11, 13))
        term.clean()  # no error
