"""
The pass mark is set once (School Settings, 50% by default) and every
pass/fail decision reads it: report card and progress report bands, the
performance report, the at-risk list, dashboards and the PTC
"Meets Academic Expectations" check.
"""
from django.test import TestCase

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics import performance
from academics.grading_utils import (
    clear_pass_mark_cache, get_grade_from_score, get_pass_mark, is_at_risk, is_pass, pass_grade,
)
from core.models import SchoolSettings
from ptc.services import calculate_meets_expectations


class PassMarkTests(TestCase):
    def setUp(self):
        clear_pass_mark_cache()
        self.addCleanup(clear_pass_mark_cache)

    def _set(self, value):
        s = SchoolSettings.get_settings()
        s.pass_mark = value
        s.save()

    def test_defaults_to_fifty_without_a_settings_row(self):
        SchoolSettings.objects.all().delete()
        clear_pass_mark_cache()
        self.assertEqual(get_pass_mark(), 50)

    def test_fifty_to_fifty_nine_passes(self):
        self._set(50)
        for mark in (50, 55, 59.9):
            self.assertTrue(is_pass(mark), mark)
            self.assertFalse(is_at_risk(mark), mark)
        self.assertFalse(is_pass(49.9))
        self.assertTrue(is_at_risk(49.9))
        self.assertEqual(pass_grade(), "D")

    def test_grade_bands_and_labels_unchanged(self):
        self.assertEqual([get_grade_from_score(m) for m in (95, 85, 75, 65, 55, 45)],
                         ["A+", "A", "B", "C", "D", "E"])

    def test_meets_expectations_uses_the_pass_mark(self):
        self._set(50)
        self.assertEqual(calculate_meets_expectations(55.0), "Yes")
        self.assertEqual(calculate_meets_expectations(50.0), "Yes")
        self.assertEqual(calculate_meets_expectations(49.9), "No")

    def test_performance_bands_follow_the_pass_mark(self):
        self._set(50)
        self.assertEqual(performance._mark_band(55), "pass")
        self.assertEqual(performance._mark_band(45), "critical")
        self.assertEqual(performance.grade_bands()["D"], "pass")
        ctx = performance.pass_mark_context()
        self.assertEqual((ctx["pass_mark"], ctx["pass_grade"], ctx["has_support_band"]), (50, "D", False))
        summary = performance.summarise([
            {"student_id": 1, "mark": 55.0, "band": performance._mark_band(55.0)},
            {"student_id": 2, "mark": 45.0, "band": performance._mark_band(45.0)},
        ])
        self.assertEqual((summary["passed"], summary["below_pass"], summary["flagged"]), (1, 1, 0))

    def test_changing_the_setting_applies_everywhere(self):
        self._set(60)
        self.assertEqual(get_pass_mark(), 60)
        self.assertFalse(is_pass(55))
        self.assertEqual(calculate_meets_expectations(55.0), "No")
        self.assertEqual(performance._mark_band(55), "support")
        self.assertTrue(performance.pass_mark_context()["has_support_band"])


from academics import test_performance_report  # noqa: E402


class PerformanceReportAtFiftyTests(test_performance_report.PerformanceReportTests):
    """The performance report fixture at the school default pass mark.
    Amani's Mathematics (58) and average (51.5) are grade D and now pass."""
    PASS_MARK = 50

    def test_d_results_pass_and_are_not_flagged(self):
        resp = self._get(self.hos, term=self.term1.pk)
        self.assertEqual(set(self._rows(resp)), {("Caren", "Mathematics"), ("Amani", "Science")})
        s = resp.context["summary"]
        self.assertEqual((s["flagged"], s["critical"]), (0, 2))
        self.assertFalse(resp.context["has_support_band"])
        self.assertNotIn("support", [k for k, _, _ in resp.context["show_options"]])
        self.assertContains(resp, "at 50% (grade D) or above")

    def test_student_averaging_fifty_to_fifty_nine_is_on_track(self):
        resp = self._get(self.hos, term=self.term1.pk, group="learner", show="all")
        rows = {r["name"]: r for r in resp.context["help_rows"]}
        self.assertEqual((rows["Amani Learner"]["grade"], rows["Amani Learner"]["status"]), ("D", "On track"))

    # Inherited tests assume a 60% pass mark; don't rerun them here.
    test_hos_sees_whole_school_worst_first_from_approved_marks = None
    test_default_list_shows_only_learners_below_pass = None
    test_band_filters_and_search = None
    test_group_by_learner_lists_weak_subjects = None
    test_by_student_status_follows_average_not_weakest_subject = None
    test_by_student_sorts_by_subjects_below_pass = None
