"""Tests for the statistics, the triage vocabulary and the change classifier.

These cover the parts of stages 2 and 3 where being subtly wrong would be
invisible: a p-value that is off, a priority that sorts backwards, a transition
that is labelled "eased" when a student actually got worse.
"""

from __future__ import annotations

import math
import re
import unittest
from pathlib import Path

from focusbridge.core.triage import (PRIORITY_RANK, UNFLAGGED_RANK, display_name,
                                     priority_rank, student_sort_key)
from focusbridge.notify.diff import classify
from focusbridge.recommend.settings import DEFAULT_SETTINGS, bucket_for_hour
from focusbridge.recommend.stats import (binomial_tail, evaluate_group,
                                         usable_baseline)

R = DEFAULT_SETTINGS


class BinomialTests(unittest.TestCase):

    def test_known_values(self):
        # "at least zero successes" is certain
        self.assertAlmostEqual(binomial_tail(0, 10, 0.5), 1.0)
        # ten heads from ten fair flips
        self.assertAlmostEqual(binomial_tail(10, 10, 0.5), 0.5 ** 10)
        # exactly one trial
        self.assertAlmostEqual(binomial_tail(1, 1, 0.3), 0.3)

    def test_it_is_a_tail_not_a_point_probability(self):
        """P(X >= k) must equal the sum of the individual terms from k upward."""
        n, p, k = 12, 0.4, 7
        by_hand = sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
                      for i in range(k, n + 1))
        self.assertAlmostEqual(binomial_tail(k, n, p), by_hand)

    def test_more_extreme_results_are_less_likely(self):
        """Monotonicity - a sanity property that catches an off-by-one in the range."""
        previous = 1.0
        for k in range(0, 11):
            current = binomial_tail(k, 10, 0.3)
            self.assertLessEqual(current, previous)
            previous = current


class BaselineTests(unittest.TestCase):

    def test_a_degenerate_baseline_is_refused(self):
        """0 and 1 are not just numerically awkward, they are meaningless.

        With no bad check-ins there is nothing to be above; with nothing but bad
        check-ins there is nothing to stand out from.
        """
        self.assertIsNone(usable_baseline(0, 10))    # never goes badly
        self.assertIsNone(usable_baseline(10, 10))   # always goes badly
        self.assertIsNone(usable_baseline(5, 0))     # no exposure at all
        self.assertAlmostEqual(usable_baseline(3, 10), 0.3)


class GateTests(unittest.TestCase):
    """Each gate must be able to reject on its own."""

    def _evaluate(self, hits, exposure, baseline, **overrides):
        options = dict(min_hits=3, min_exposure=5, min_excess=0.15, max_p=0.05)
        options.update(overrides)
        return evaluate_group(hits, exposure, baseline, **options)

    def test_too_few_hits_is_rejected(self):
        self.assertIsNone(self._evaluate(2, 20, 0.05))

    def test_too_little_exposure_is_rejected(self):
        """Three out of three is 100% and is also nothing."""
        self.assertIsNone(self._evaluate(3, 3, 0.05))

    def test_a_real_but_trivial_effect_is_rejected(self):
        """Statistically detectable is not the same as worth a teacher's Monday."""
        self.assertIsNone(self._evaluate(52, 100, 0.50, min_hits=3, min_exposure=5))

    def test_a_large_effect_on_thin_evidence_is_rejected_by_chance(self):
        """Passes the effect-size gate, fails the significance gate."""
        result = self._evaluate(4, 6, 0.40)
        self.assertIsNone(result)

    def test_a_strong_pattern_passes_and_reports_its_numbers(self):
        result = self._evaluate(12, 15, 0.25)
        self.assertIsNotNone(result)
        self.assertEqual(result.hits, 12)
        self.assertEqual(result.exposure, 15)
        self.assertAlmostEqual(result.rate, 0.8)
        self.assertAlmostEqual(result.excess, 0.8 - 0.25)
        self.assertAlmostEqual(result.lift, 0.8 / 0.25)
        self.assertLess(result.p_value, 0.05)

    def test_the_rule_measures_a_rate_and_not_a_share(self):
        """The distinction three earlier versions got wrong.

        A student flagged around Reading exactly as often as their day goes
        wrong generally must NOT be told "Reading is where this shows up",
        however many of their flags happen to sit there.
        """
        # 30 of 100 check-ins near Reading were flagged; the student's overall
        # rate is also 30%. Reading holds a large *share* of the flags and is
        # nonetheless completely unremarkable.
        self.assertIsNone(self._evaluate(30, 100, 0.30))


class TimeBucketTests(unittest.TestCase):

    def test_every_hour_belongs_to_exactly_one_band(self):
        """Half-open boundaries: no gaps, no overlaps, no hour left out."""
        for hour in range(24):
            self.assertIsNotNone(bucket_for_hour(hour), hour)

    def test_the_boundaries_land_where_the_phrases_claim(self):
        self.assertEqual(bucket_for_hour(10), "morning")
        self.assertEqual(bucket_for_hour(11), "midday")     # not morning
        self.assertEqual(bucket_for_hour(12), "midday")
        self.assertEqual(bucket_for_hour(13), "afternoon")  # not midday


class CapTests(unittest.TestCase):

    def test_caps_shrink_with_urgency(self):
        """The alert-fatigue argument, expressed as an ordering."""
        self.assertGreater(R.cap_for_channel("alert"), R.cap_for_channel("watch"))
        self.assertGreater(R.cap_for_channel("watch"), R.cap_for_channel("positive"))
        self.assertGreaterEqual(R.cap_for_channel("positive"), 1)


class TriageTests(unittest.TestCase):

    def test_lower_rank_means_more_urgent(self):
        self.assertLess(PRIORITY_RANK["high"], PRIORITY_RANK["medium"])
        self.assertLess(PRIORITY_RANK["medium"], PRIORITY_RANK["watch"])
        self.assertLess(max(PRIORITY_RANK.values()), UNFLAGGED_RANK)

    def test_an_unknown_priority_sorts_last_rather_than_crashing(self):
        """A display tool must survive a file written by a newer pipeline."""
        self.assertEqual(priority_rank("some-future-tier"), UNFLAGGED_RANK)

    def test_students_sort_most_urgent_first(self):
        students = [("watch", 0, "c"), ("high", 1, "b"), ("medium", 3, "a")]
        ordered = sorted(students, key=lambda s: student_sort_key(*s))
        self.assertEqual([s[0] for s in ordered], ["high", "medium", "watch"])

    def test_name_modes_are_honoured(self):
        """The app's two modes, plus the two the browser engine also accepts."""
        self.assertEqual(display_name("Sana Malik", "full", "stu_x"), "Sana")
        self.assertEqual(display_name("Sana Malik", "id", "stu_e0c27497"),
                         "Student 7497")
        self.assertEqual(display_name("Sana Malik", "anon", "stu_e0c27497"),
                         "Student 7497")
        self.assertEqual(display_name("Sana Malik", "initials", "stu_x"), "SM")

    def test_id_only_never_shows_the_stored_name(self):
        """Regression: "ID only" rooms used to be printed by name.

        The app's "ID only - no name shown" setting writes ``name_mode="id"``,
        which this function did not recognise, so every flagged child in such a
        room was named in recommendations, the dashboard and the email digests.
        Uppercased, because that is what the app shows.
        """
        label = display_name("Yusuf", "id", "stu_9f2ab64d")
        self.assertEqual(label, "Student B64D")
        self.assertNotIn("Yusuf", label)

    def test_full_mode_shows_a_first_name_only(self):
        """The app collects "first name only"; a longer entry is cut back to it."""
        self.assertEqual(display_name("  Sana   Malik ", "full", "stu_x"), "Sana")
        self.assertEqual(display_name("A" * 30, "full", "stu_x"), "A" * 20)

    def test_every_mode_the_app_can_write_is_handled_as_the_app_handles_it(self):
        """A contract test against the app itself, not against a copy of it.

        The leak above happened because the app and this function used
        different words for the same setting, and nothing compared them. This
        reads the privacy picker's option values straight out of each app's
        index.html, so if the app ever adds a mode, this fails until the
        pipeline knows what to do with it.
        """
        repo = Path(__file__).resolve().parents[2]
        apps = [repo / "integrated_version" / "index.html",
                repo / "focusbridge_sourcecode" / "index.html"]
        found = [app for app in apps if app.exists()]
        if not found:
            self.skipTest("no app index.html next to this checkout")

        expected = {"full": "Sana", "id": "Student 7497"}
        for app in found:
            source = app.read_text(encoding="utf-8")
            picker = re.search(r"function NameModePicker.*?const opts=\[(.*?)\];",
                               source, re.S)
            self.assertIsNotNone(picker, f"no NameModePicker in {app}")
            modes = re.findall(r'\["([a-z]+)",', picker.group(1))
            self.assertTrue(modes, f"no modes parsed from {app}")
            for mode in modes:
                with self.subTest(app=app.parent.name, mode=mode):
                    self.assertIn(mode, expected,
                                  f"the app offers name mode {mode!r}, which the "
                                  f"pipeline does not know how to honour")
                    self.assertEqual(display_name("Sana Malik", mode, "stu_e0c27497"),
                                     expected[mode])

    def test_an_unknown_name_mode_does_not_leak_more_than_full(self):
        """An unrecognised mode falls back to the app's own default, not a crash."""
        self.assertEqual(display_name("Sana", "hieroglyphs", "stu_x"), "Sana")

    def test_a_missing_name_still_produces_a_label(self):
        self.assertEqual(display_name(None, "full", "stu_x"), "Student")
        self.assertEqual(display_name("   ", "initials", "stu_x"), "S")


class ChangeClassificationTests(unittest.TestCase):

    def test_the_four_transitions(self):
        high = {"priority": "high"}
        medium = {"priority": "medium"}
        self.assertEqual(classify(None, high), "new")
        self.assertEqual(classify(high, None), "resolved")
        self.assertEqual(classify(medium, high), "escalated")
        self.assertEqual(classify(high, medium), "eased")
        self.assertEqual(classify(high, high), "unchanged")

    def test_a_student_absent_from_both_runs_is_unchanged(self):
        """Cannot happen through the pipeline, but the function must not crash."""
        self.assertEqual(classify(None, None), "unchanged")


if __name__ == "__main__":
    unittest.main()
