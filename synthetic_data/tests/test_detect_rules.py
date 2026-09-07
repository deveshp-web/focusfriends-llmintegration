"""Tests for the four detection rules.

Each test names the *behaviour* it protects rather than the function it calls,
because the value of these tests is that they record decisions - "four negatives
on one morning is not a rough week" is a judgement someone made, and a test is
where a judgement goes so that a future change has to argue with it.
"""

from __future__ import annotations

import unittest
from datetime import datetime

from focusbridge.detect.rules import (find_density_streaks, find_negative_streaks,
                                      find_repeated_streaks,
                                      find_same_day_clusters, find_all_streaks)
from focusbridge.detect.settings import DEFAULT_SETTINGS

from .helpers import one_day, timeline_for

S = DEFAULT_SETTINGS


class NegativeStreakTests(unittest.TestCase):
    """The core alert: four or more bad check-ins in a row."""

    def test_four_negatives_across_two_days_flags(self):
        self.assertEqual(S.streak_length, 4, "test assumes the default")
        timeline = timeline_for([
            ("2026-08-03T09:00:00", "frustrated"),
            ("2026-08-03T13:00:00", "anxious"),
            ("2026-08-04T09:00:00", "angry"),
            ("2026-08-04T13:00:00", "sad"),
        ])
        streaks = find_negative_streaks(timeline)
        self.assertEqual(len(streaks), 1)
        self.assertEqual(streaks[0]["type"], "negative")
        self.assertEqual(streaks[0]["channel"], "alert")
        self.assertEqual(streaks[0]["length"], 4)

    def test_three_negatives_does_not_flag(self):
        timeline = timeline_for([
            ("2026-08-03T09:00:00", "frustrated"),
            ("2026-08-03T13:00:00", "anxious"),
            ("2026-08-04T09:00:00", "angry"),
        ])
        self.assertEqual(find_negative_streaks(timeline), [])

    def test_a_positive_check_in_breaks_the_run(self):
        """Two negatives, a happy, then two more is two runs of two.

        Four negative entries in total, and nothing should fire. This is the
        rule's whole point: it counts *consecutive* bad check-ins, not bad
        check-ins.
        """
        timeline = timeline_for([
            ("2026-08-03T09:00:00", "sad"),
            ("2026-08-03T10:00:00", "angry"),
            ("2026-08-03T11:00:00", "happy"),
            ("2026-08-04T09:00:00", "sad"),
            ("2026-08-04T10:00:00", "angry"),
        ])
        self.assertEqual(find_negative_streaks(timeline), [])

    def test_an_unreadable_check_in_also_breaks_the_run(self):
        """The behaviour that justifies keeping unreadable check-ins at all.

        If the unrecognised emotion were dropped instead of kept as ``None``,
        the four negatives around it would become adjacent and this would flag -
        a streak manufactured out of a gap we could not read.
        """
        timeline = timeline_for([
            ("2026-08-03T09:00:00", "sad"),
            ("2026-08-03T10:00:00", "angry"),
            ("2026-08-03T11:00:00", "blorp"),   # not in the vocabulary
            ("2026-08-04T09:00:00", "sad"),
            ("2026-08-04T10:00:00", "angry"),
        ])
        self.assertEqual(len(timeline), 5, "the unreadable row is kept")
        self.assertIsNone(timeline[2].emotion)
        self.assertEqual(find_negative_streaks(timeline), [])

    def test_same_day_negatives_are_not_a_streak(self):
        """``min_distinct_days`` - one rough morning is not a rough week.

        Four negatives on a single day is a real event, but it belongs to the
        same-day cluster rule, which reports it as what it is.
        """
        self.assertEqual(S.min_distinct_days, 2, "test assumes the default")
        timeline = timeline_for(one_day([8, 9, 11, 13]))
        self.assertEqual(find_negative_streaks(timeline), [])

    def test_negatives_spread_past_the_window_do_not_count(self):
        """``streak_window_days`` - four bad days over nine days is not an episode."""
        self.assertEqual(S.streak_window_days, 7, "test assumes the default")
        timeline = timeline_for([
            ("2026-08-01T09:00:00", "sad"),
            ("2026-08-04T09:00:00", "angry"),
            ("2026-08-07T09:00:00", "anxious"),
            ("2026-08-10T09:00:00", "frustrated"),
        ])
        self.assertEqual(find_negative_streaks(timeline), [])

    def test_one_long_run_is_reported_as_one_episode(self):
        """The expensive lesson: a fortnight of distress is one thing, not many.

        Twelve consecutive negatives spanning more than the 7-day window still
        produce exactly **one** record covering the whole run, with the tightest
        qualifying window reported alongside as the trigger.
        """
        entries = [(f"2026-08-{day:02d}T09:00:00", "sad") for day in range(1, 13)]
        streaks = find_negative_streaks(timeline_for(entries))
        self.assertEqual(len(streaks), 1, "one episode, not one record per window")
        self.assertEqual(streaks[0]["length"], 12)
        self.assertIn("trigger_length", streaks[0])
        self.assertLessEqual(streaks[0]["trigger_span_days"], S.streak_window_days)


class SameDayClusterTests(unittest.TestCase):

    def test_four_same_day_negatives_is_a_watch_cluster(self):
        self.assertEqual(S.same_day_cluster_length, 4, "test assumes the default")
        clusters = find_same_day_clusters(timeline_for(one_day([8, 9, 11, 13])))
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["channel"], "watch")

    def test_six_same_day_negatives_escalates_to_alert(self):
        """Big enough stops being a weaker streak and becomes its own incident."""
        self.assertEqual(S.same_day_alert_length, 6, "test assumes the default")
        clusters = find_same_day_clusters(timeline_for(one_day(range(8, 14))))
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["channel"], "alert")

    def test_a_run_that_already_alerted_is_not_also_a_cluster(self):
        """No double-reporting: the streak already told the teacher about this."""
        entries = one_day([8, 9, 10, 11]) + one_day([8, 9], date="2026-08-04")
        timeline = timeline_for(entries)
        self.assertTrue(find_negative_streaks(timeline), "precondition: it alerts")
        self.assertEqual(find_same_day_clusters(timeline), [])


class RepeatedStreakTests(unittest.TestCase):

    def test_four_repeated_positives_flag(self):
        timeline = timeline_for([
            ("2026-08-03T09:00:00", "happy"),
            ("2026-08-03T10:00:00", "happy"),
            ("2026-08-04T09:00:00", "happy"),
            ("2026-08-04T10:00:00", "happy"),
        ])
        streaks = find_repeated_streaks(timeline)
        self.assertEqual(len(streaks), 1)
        self.assertEqual(streaks[0]["emotion"], "happy")
        self.assertEqual(streaks[0]["channel"], "positive")

    def test_repeated_negatives_are_the_other_rule_s_job(self):
        """``repeated_valences`` excludes negative - the streak rule owns that."""
        timeline = timeline_for([
            ("2026-08-03T09:00:00", "sad"),
            ("2026-08-03T10:00:00", "sad"),
            ("2026-08-04T09:00:00", "sad"),
            ("2026-08-04T10:00:00", "sad"),
        ])
        self.assertEqual(find_repeated_streaks(timeline), [])

    def test_four_neutral_check_ins_are_not_news(self):
        """"okay" x4 is the definition of an unremarkable week."""
        timeline = timeline_for([
            ("2026-08-03T09:00:00", "okay"),
            ("2026-08-03T10:00:00", "okay"),
            ("2026-08-04T09:00:00", "okay"),
            ("2026-08-04T10:00:00", "okay"),
        ])
        self.assertEqual(find_repeated_streaks(timeline), [])


class DensityStreakTests(unittest.TestCase):

    def test_a_mostly_bad_week_flags_even_with_no_run_of_four(self):
        """The reason this rule exists at all.

        Six entries, five of them bad, across six days - but the longest
        unbroken run is three, so the streak rule stays silent. A child having a
        genuinely bad week should not escape notice because of one good Tuesday.
        """
        self.assertEqual(S.density_min_entries, 6, "test assumes the default")
        timeline = timeline_for([
            ("2026-08-01T09:00:00", "sad"),
            ("2026-08-02T09:00:00", "angry"),
            ("2026-08-03T09:00:00", "happy"),
            ("2026-08-04T09:00:00", "anxious"),
            ("2026-08-05T09:00:00", "frustrated"),
            ("2026-08-06T09:00:00", "overwhelmed"),
        ])
        self.assertEqual(find_negative_streaks(timeline), [],
                         "precondition: the streak rule does not catch this")
        hits = find_density_streaks(timeline)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["channel"], "watch")
        self.assertAlmostEqual(hits[0]["negative_share"], 5 / 6, places=3)

    def test_two_overlapping_spans_are_emitted_when_the_union_would_not_qualify(self):
        """Documents a behaviour that is easy to assume away.

        Merging is *conditional*: two overlapping windows that each clear the
        density bar are merged only if their union clears it too. Here it does
        not - the union dilutes to 6/9 = 0.67 - so the second window is kept as
        its own span and the output contains two overlapping records.

        "Density spans never overlap" is therefore not a property of this rule,
        and anything downstream that assumes it would be wrong for 416 students
        on the current corpus.
        """
        # day 1 good, days 2-7 bad, days 8-9 good.
        emotions = ["happy"] + ["sad"] * 6 + ["happy", "happy"]
        timeline = timeline_for([(f"2026-08-{day:02d}T09:00:00", emotion)
                                 for day, emotion in enumerate(emotions, start=1)])

        spans = find_density_streaks(timeline)
        self.assertEqual(len(spans), 2)
        self.assertAlmostEqual(spans[0]["negative_share"], 0.75)
        self.assertAlmostEqual(spans[1]["negative_share"], 0.75)
        self.assertLessEqual(spans[1]["start_ts"], spans[0]["end_ts"],
                             "the two spans really do overlap")

    def test_a_span_that_only_straddles_an_alert_survives(self):
        """The ``covered`` test is full containment, not any overlap.

        A density period wider than the alert inside it is describing something
        the alert did not, so it is kept. Only a period sitting *entirely*
        within an alert is dropped as a duplicate.
        """
        emotions = ["happy"] + ["sad"] * 6 + ["happy", "happy"]
        timeline = timeline_for([(f"2026-08-{day:02d}T09:00:00", emotion)
                                 for day, emotion in enumerate(emotions, start=1)])

        alert = find_negative_streaks(timeline)[0]
        straddled = [(datetime.fromisoformat(alert["start_ts"]),
                      datetime.fromisoformat(alert["end_ts"]))]
        self.assertEqual(len(find_density_streaks(timeline, straddled)), 2,
                         "an alert narrower than the density period drops nothing")

        spans = find_density_streaks(timeline)
        containing = [(datetime.fromisoformat(spans[0]["start_ts"]),
                       datetime.fromisoformat(spans[-1]["end_ts"]))]
        self.assertEqual(find_density_streaks(timeline, containing), [],
                         "an alert that fully contains them drops both")

    def test_a_period_already_covered_by_an_alert_is_dropped(self):
        """A run of consecutive negatives is dense by definition.

        Without the ``covered`` filter every alert would be shadowed by a
        duplicate watch record describing the same days.
        """
        entries = [(f"2026-08-{day:02d}T09:00:00", "sad") for day in range(1, 8)]
        timeline = timeline_for(entries)
        alerts = find_negative_streaks(timeline)
        self.assertTrue(alerts, "precondition: this alerts")

        covered = [(datetime.fromisoformat(alerts[0]["start_ts"]),
                    datetime.fromisoformat(alerts[0]["end_ts"]))]
        self.assertEqual(find_density_streaks(timeline, covered), [])


class FindAllStreaksTests(unittest.TestCase):

    def test_empty_log_produces_nothing_and_does_not_raise(self):
        self.assertEqual(find_all_streaks(timeline_for([])), [])

    def test_every_finding_carries_the_fields_downstream_stages_read(self):
        """A contract test: stages 2, 3 and 4 all index into these keys."""
        entries = [(f"2026-08-{day:02d}T09:00:00", "sad") for day in range(1, 6)]
        for streak in find_all_streaks(timeline_for(entries)):
            for key in ("type", "channel", "valence", "start_ts", "end_ts",
                        "length", "distinct_days", "span_days", "emotions"):
                self.assertIn(key, streak)
            self.assertIn(streak["channel"], ("alert", "watch", "positive"))


if __name__ == "__main__":
    unittest.main()
