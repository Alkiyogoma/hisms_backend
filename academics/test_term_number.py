"""
Terms carry a term number (1, 2, 3) set when the term is created; per-term
configuration such as the ECD report templates is keyed by it. The ECD
templates from the school's Word files are seeded into the database.
"""
import importlib
from datetime import date

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics.models import AcademicYear, ECDItem, ECDReportLayout, ECDSection, Term
from academics.tests import assign_role_group
from users.models import User, UserRole

SETTINGS_URL = reverse("core:school_settings") + "?tab=academic_year"


class TermNumberModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = AcademicYear.objects.create(name="2026/2027")

    def test_number_is_taken_from_the_name_when_not_given(self):
        term = Term.objects.create(academic_year=self.year, name="Term 2")
        self.assertEqual(term.term_number, 2)

    def test_explicit_number_wins_over_the_name(self):
        term = Term.objects.create(academic_year=self.year, name="Easter term", term_number=3)
        self.assertEqual(term.term_number, 3)

    def test_name_guess_never_clashes_with_a_used_number(self):
        Term.objects.create(academic_year=self.year, name="First", term_number=1)
        term = Term.objects.create(academic_year=self.year, name="Term 1 (repeat)")
        self.assertIsNone(term.term_number)

    def test_number_is_unique_within_a_year(self):
        Term.objects.create(academic_year=self.year, name="Term 1", term_number=1)
        dup = Term(academic_year=self.year, name="Another", term_number=1)
        with self.assertRaises(ValidationError):
            dup.full_clean()

    def test_same_number_allowed_in_another_year(self):
        Term.objects.create(academic_year=self.year, name="Term 1", term_number=1)
        other = AcademicYear.objects.create(name="2027/2028")
        Term(academic_year=other, name="Term 1", term_number=1).full_clean()

    def test_backfill_numbers_existing_terms(self):
        backfill = importlib.import_module("academics.migrations.0068_term_number").backfill_term_numbers
        a = Term.objects.create(academic_year=self.year, name="Opening", start_date=date(2026, 9, 1))
        b = Term.objects.create(academic_year=self.year, name="Term 3", start_date=date(2027, 4, 1))
        c = Term.objects.create(academic_year=self.year, name="Middle", start_date=date(2027, 1, 5))
        Term.objects.filter(pk__in=[a.pk, b.pk, c.pk]).update(term_number=None)
        backfill(apps, None)
        numbers = dict(Term.objects.values_list("name", "term_number"))
        self.assertEqual(numbers, {"Term 3": 3, "Opening": 1, "Middle": 2})


class TermNumberSettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="sa", email="sa@example.test", password="x",
                                             role=UserRole.SUPER_ADMIN, is_superuser=True, is_staff=True)
        assign_role_group(cls.admin)
        cls.year = AcademicYear.objects.create(
            name="2026/2027", start_date=date(2026, 9, 1), end_date=date(2027, 7, 31),
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def _add(self, **extra):
        data = {"action": "add_term", "academic_year": self.year.pk,
                "start_date": "2026-09-01", "end_date": "2026-12-15", **extra}
        return self.client.post(SETTINGS_URL, data)

    def test_add_term_saves_number_and_defaults_the_name(self):
        self._add(term_number="1")
        term = Term.objects.get(academic_year=self.year)
        self.assertEqual((term.term_number, term.name), (1, "Term 1"))

    def test_add_term_requires_a_number(self):
        self._add(term_name="Term 1")
        self.assertFalse(Term.objects.filter(academic_year=self.year).exists())

    def test_add_term_rejects_a_number_already_used(self):
        self._add(term_number="1")
        self._add(term_number="1", term_name="Autumn", start_date="2027-01-05", end_date="2027-03-30")
        self.assertEqual(Term.objects.filter(academic_year=self.year).count(), 1)

    def test_edit_term_changes_the_number(self):
        self._add(term_number="1", term_name="Autumn")
        term = Term.objects.get(academic_year=self.year)
        self.client.post(SETTINGS_URL, {"action": "edit_term", "pk": term.pk, "term_number": "2",
                                        "start_date": "2026-09-01", "end_date": "2026-12-15"})
        term.refresh_from_db()
        self.assertEqual(term.term_number, 2)

    def test_settings_page_shows_the_term_number(self):
        self._add(term_number="3", term_name="Summer")
        html = self.client.get(SETTINGS_URL).content.decode()
        self.assertIn('name="term_number"', html)
        self.assertIn(">T3<", html)


class ECDTemplateSeedTests(TestCase):
    def test_all_classes_and_terms_are_seeded(self):
        self.assertEqual(ECDReportLayout.objects.count(), 12)
        self.assertEqual(
            set(ECDSection.objects.values_list("class_type", flat=True)),
            {"pre_k", "kindergarten", "pre_school", "abc"},
        )

    def test_items_follow_their_term(self):
        numeracy = ECDSection.objects.get(class_type="kindergarten", title="NUMERACY")
        labels = {n: set(numeracy.items.for_term(n).values_list("label", flat=True)) for n in (1, 2, 3)}
        self.assertIn("Number counting 0-10", labels[1])
        self.assertIn("Number counting 0-15", labels[2])
        self.assertIn("Number counting 0-20", labels[3])
        self.assertNotIn("Number counting 0-10", labels[3])

    def test_term_specific_keys(self):
        kg1 = ECDReportLayout.objects.get(class_type="kindergarten", term_number=1)
        kg2 = ECDReportLayout.objects.get(class_type="kindergarten", term_number=2)
        self.assertIn(["S", "Satisfactory"], kg1.traits_key)
        self.assertIn(["M", "Making Progress"], kg2.traits_key)
        abc = ECDReportLayout.objects.get(class_type="abc", term_number=1)
        self.assertIn(["1", "Not Yet"], abc.academic_key)
        self.assertEqual(abc.accent, "yellow")

    def test_bible_memory_is_a_full_width_header_for_the_verses(self):
        for class_type in ("abc", "pre_school"):
            verse = ECDSection.objects.get(class_type=class_type, style="verse")
            self.assertEqual(verse.column, "full")
            # No "Bible Memory" line to rate: the verses are added in Report Setup.
            self.assertFalse(verse.items.filter(is_active=True).exists())

    def test_abc_term_3_has_pace_and_marks_only_in_term_3(self):
        pace = ECDItem.objects.filter(section__class_type="abc", section__title="PACE")
        self.assertEqual(pace.for_term(3).count(), 3)
        self.assertEqual(pace.for_term(1).count(), 0)
        marks = ECDSection.objects.get(class_type="abc", style="marks")
        self.assertFalse(marks.counts_in_average)

    def test_seed_command_keeps_edits_unless_reset(self):
        ECDItem.objects.filter(label="Counting").update(label="Counting (edited)")
        call_command("seed_ecd_templates", stdout=open("/dev/null", "w"))
        self.assertTrue(ECDItem.objects.filter(label="Counting (edited)").exists())
        call_command("seed_ecd_templates", "--reset", stdout=open("/dev/null", "w"))
        self.assertFalse(ECDItem.objects.filter(label="Counting (edited)").exists())
        self.assertEqual(ECDReportLayout.objects.count(), 12)
