"""
Tests for term exam date fields, calendar sync, and score entry gating.

Covers:
  - Term model: 4 exam date fields exist (1)
  - Term.clean(): midterm dates within term bounds (2)
  - Term.clean(): endterm dates within term bounds (3)
  - Term.clean(): midterm start before end (4)
  - Term.clean(): endterm start before end (5)
  - TermForm: includes all 4 exam date fields (6)
  - CalendarEvent sync: midterm exam creates event (7)
  - CalendarEvent sync: endterm exam creates event (8)
  - CalendarEvent sync: removing dates deletes event (9)
  - CalendarEvent sync: legacy exam fields still sync (10)
  - Auto-generated event blocked from generic edit (11)
  - check_score_entry_allowed: quiz always allowed (12)
  - check_score_entry_allowed: mid_term blocked before window (13)
  - check_score_entry_allowed: end_term blocked before window (14)
  - check_score_entry_allowed: blocked past grading_deadline (15)
  - check_score_entry_allowed: allowed within window (16)
"""
from datetime import date, timedelta

from django.test import TestCase
from django.contrib.auth import get_user_model

from academics.models import AcademicYear, Term
from academics.forms import TermForm
from academics.utils import check_score_entry_allowed
from events.models import CalendarEvent, EventCategory

User = get_user_model()


class TermExamDateFieldsTest(TestCase):
    """Tests 1-5: Term model exam date fields and validation."""

    def setUp(self):
        self.ay = AcademicYear.objects.create(
            name="2026", is_current=True,
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        )

    def test_term_has_four_exam_date_fields(self):
        """Test 1: Term model has midterm and endterm exam date fields."""
        term = Term.objects.create(
            academic_year=self.ay, name="Term 1",
            start_date=date(2026, 1, 5), end_date=date(2026, 3, 30),
            midterm_exam_start_date=date(2026, 2, 9),
            midterm_exam_end_date=date(2026, 2, 13),
            endterm_exam_start_date=date(2026, 3, 23),
            endterm_exam_end_date=date(2026, 3, 27),
        )
        self.assertEqual(term.midterm_exam_start_date, date(2026, 2, 9))
        self.assertEqual(term.midterm_exam_end_date, date(2026, 2, 13))
        self.assertEqual(term.endterm_exam_start_date, date(2026, 3, 23))
        self.assertEqual(term.endterm_exam_end_date, date(2026, 3, 27))

    def test_midterm_dates_within_term_bounds(self):
        """Test 2: Midterm dates must be within term start/end."""
        term = Term(
            academic_year=self.ay, name="Term 1",
            start_date=date(2026, 1, 5), end_date=date(2026, 3, 30),
            midterm_exam_start_date=date(2026, 1, 1),  # before term start
            midterm_exam_end_date=date(2026, 2, 13),
        )
        with self.assertRaises(Exception) as ctx:
            term.full_clean()
        self.assertIn("Mid-Term exam start date is before term start", str(ctx.exception))

    def test_endterm_dates_within_term_bounds(self):
        """Test 3: Endterm dates must be within term start/end."""
        term = Term(
            academic_year=self.ay, name="Term 1",
            start_date=date(2026, 1, 5), end_date=date(2026, 3, 30),
            endterm_exam_start_date=date(2026, 3, 23),
            endterm_exam_end_date=date(2026, 4, 5),  # after term end
        )
        with self.assertRaises(Exception) as ctx:
            term.full_clean()
        self.assertIn("End-Term exam end date is after term end", str(ctx.exception))

    def test_midterm_start_before_end(self):
        """Test 4: Midterm start date must be before end date."""
        term = Term(
            academic_year=self.ay, name="Term 1",
            start_date=date(2026, 1, 5), end_date=date(2026, 3, 30),
            midterm_exam_start_date=date(2026, 2, 13),
            midterm_exam_end_date=date(2026, 2, 9),  # before start
        )
        with self.assertRaises(Exception) as ctx:
            term.full_clean()
        self.assertIn("Mid-Term exam start date must be before end date", str(ctx.exception))

    def test_endterm_start_before_end(self):
        """Test 5: Endterm start date must be before end date."""
        term = Term(
            academic_year=self.ay, name="Term 1",
            start_date=date(2026, 1, 5), end_date=date(2026, 3, 30),
            endterm_exam_start_date=date(2026, 3, 27),
            endterm_exam_end_date=date(2026, 3, 23),  # before start
        )
        with self.assertRaises(Exception) as ctx:
            term.full_clean()
        self.assertIn("End-Term exam start date must be before end date", str(ctx.exception))


class TermFormExamDateFieldsTest(TestCase):
    """Test 6: TermForm includes all 4 exam date fields."""

    def test_form_has_exam_date_fields(self):
        """Test 6: TermForm fields include midterm and endterm exam dates."""
        form = TermForm()
        fields = list(form.fields.keys())
        self.assertIn("midterm_exam_start_date", fields)
        self.assertIn("midterm_exam_end_date", fields)
        self.assertIn("endterm_exam_start_date", fields)
        self.assertIn("endterm_exam_end_date", fields)


class CalendarEventSyncTest(TestCase):
    """Tests 7-10: CalendarEvent auto-sync for exam dates."""

    def setUp(self):
        self.ay = AcademicYear.objects.create(
            name="2026", is_current=True,
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        )

    def test_midterm_exam_creates_calendar_event(self):
        """Test 7: Setting midterm exam dates creates a CalendarEvent."""
        term = Term.objects.create(
            academic_year=self.ay, name="Term 1",
            start_date=date(2026, 1, 5), end_date=date(2026, 3, 30),
            midterm_exam_start_date=date(2026, 2, 9),
            midterm_exam_end_date=date(2026, 2, 13),
        )
        event = CalendarEvent.objects.filter(
            source_model="term_midterm_exam", source_id=term.id,
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.title, "Mid-Term Exams - Term 1")
        self.assertEqual(event.start_date, date(2026, 2, 9))
        self.assertEqual(event.end_date, date(2026, 2, 13))
        self.assertTrue(event.auto_generated)

    def test_endterm_exam_creates_calendar_event(self):
        """Test 8: Setting endterm exam dates creates a CalendarEvent."""
        term = Term.objects.create(
            academic_year=self.ay, name="Term 1",
            start_date=date(2026, 1, 5), end_date=date(2026, 3, 30),
            endterm_exam_start_date=date(2026, 3, 23),
            endterm_exam_end_date=date(2026, 3, 27),
        )
        event = CalendarEvent.objects.filter(
            source_model="term_endterm_exam", source_id=term.id,
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.title, "End-Term Exams - Term 1")
        self.assertEqual(event.start_date, date(2026, 3, 23))
        self.assertEqual(event.end_date, date(2026, 3, 27))
        self.assertTrue(event.auto_generated)

    def test_removing_dates_deletes_event(self):
        """Test 9: Clearing exam dates deletes the auto-generated event."""
        term = Term.objects.create(
            academic_year=self.ay, name="Term 1",
            start_date=date(2026, 1, 5), end_date=date(2026, 3, 30),
            midterm_exam_start_date=date(2026, 2, 9),
            midterm_exam_end_date=date(2026, 2, 13),
        )
        self.assertTrue(
            CalendarEvent.objects.filter(
                source_model="term_midterm_exam", source_id=term.id,
            ).exists()
        )
        term.midterm_exam_start_date = None
        term.midterm_exam_end_date = None
        term.save()
        self.assertFalse(
            CalendarEvent.objects.filter(
                source_model="term_midterm_exam", source_id=term.id,
            ).exists()
        )

    def test_legacy_exam_fields_still_sync(self):
        """Test 10: Legacy exam_start_date/exam_end_date still create events."""
        term = Term.objects.create(
            academic_year=self.ay, name="Term 1",
            start_date=date(2026, 1, 5), end_date=date(2026, 3, 30),
            exam_start_date=date(2026, 3, 23),
            exam_end_date=date(2026, 3, 27),
        )
        event = CalendarEvent.objects.filter(
            source_model="term_exam", source_id=term.id,
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.title, "Exams - Term 1")


class AutoGeneratedEventEditBlockTest(TestCase):
    """Test 11: Auto-generated events are blocked from generic edit."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin", email="admin@test.com", password="test1234",
        )
        self.event = CalendarEvent.objects.create(
            title="Term 1 Begins",
            category=EventCategory.ACADEMIC,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
            auto_generated=True,
            source_model="term_start",
            source_id=1,
        )
        self.manual_event = CalendarEvent.objects.create(
            title="Sports Day",
            category=EventCategory.SOCIAL,
            start_date=date(2026, 2, 15),
            end_date=date(2026, 2, 15),
            auto_generated=False,
        )

    def test_auto_generated_event_not_in_update_queryset(self):
        """Test 11: Auto-generated events filtered out of update view queryset."""
        from django.test import RequestFactory
        from events.views import EventUpdateView

        factory = RequestFactory()
        request = factory.get(f"/events/{self.event.pk}/edit/")
        request.user = self.admin

        view = EventUpdateView()
        view.request = request
        view.kwargs = {"pk": self.event.pk}

        qs = view.get_queryset()
        self.assertFalse(qs.filter(pk=self.event.pk).exists())
        self.assertTrue(qs.filter(pk=self.manual_event.pk).exists())


class ScoreEntryGatingTest(TestCase):
    """Tests 12-16: check_score_entry_allowed utility."""

    def setUp(self):
        self.ay = AcademicYear.objects.create(
            name="2026", is_current=True,
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        )
        self.term = Term.objects.create(
            academic_year=self.ay, name="Term 1",
            start_date=date(2026, 1, 5), end_date=date(2026, 3, 30),
            midterm_exam_start_date=date(2026, 2, 9),
            midterm_exam_end_date=date(2026, 2, 13),
            endterm_exam_start_date=date(2026, 3, 23),
            endterm_exam_end_date=date(2026, 3, 27),
            grading_deadline=date(2026, 4, 10),
        )

    def test_quiz_always_allowed_during_term(self):
        """Test 12: Quiz scores are always allowed once term has started."""
        allowed, msg = check_score_entry_allowed(self.term, "quiz", date(2026, 1, 10))
        self.assertTrue(allowed)
        self.assertIsNone(msg)

    def test_mid_term_blocked_before_window(self):
        """Test 13: Mid-Term scores blocked before exam window opens."""
        allowed, msg = check_score_entry_allowed(self.term, "mid_term", date(2026, 1, 20))
        self.assertFalse(allowed)
        self.assertIn("Opens on 2026-02-09", msg)

    def test_end_term_blocked_before_window(self):
        """Test 14: End-Term scores blocked before exam window opens."""
        allowed, msg = check_score_entry_allowed(self.term, "end_of_term", date(2026, 2, 15))
        self.assertFalse(allowed)
        self.assertIn("Opens on 2026-03-23", msg)

    def test_blocked_past_grading_deadline(self):
        """Test 15: All score types blocked past grading deadline."""
        allowed, msg = check_score_entry_allowed(self.term, "quiz", date(2026, 4, 15))
        self.assertFalse(allowed)
        self.assertIn("Grading deadline has passed", msg)

        allowed, msg = check_score_entry_allowed(self.term, "mid_term", date(2026, 4, 15))
        self.assertFalse(allowed)
        self.assertIn("Grading deadline has passed", msg)

    def test_allowed_within_window(self):
        """Test 16: Scores allowed within their exam windows."""
        allowed, msg = check_score_entry_allowed(self.term, "mid_term", date(2026, 2, 10))
        self.assertTrue(allowed)
        self.assertIsNone(msg)

        allowed, msg = check_score_entry_allowed(self.term, "end_of_term", date(2026, 3, 25))
        self.assertTrue(allowed)
        self.assertIsNone(msg)
