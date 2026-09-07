#!/usr/bin/env python3
"""
Unit tests for the pure detection functions in detect_patterns.py.

These functions never touch a file - they take a small list of
{"ts": datetime, "emotion": str} entries and return a list of "streak"
dicts - which makes them easy to test in isolation: build a tiny fake
emotion log by hand, run it through the real function, and assert exactly
what should (or should not) fire.

Run with:
    python3 -m unittest test_detect_patterns -v
or just:
    python3 test_detect_patterns.py
"""

import unittest
from collections import Counter

from detect_patterns import (
    DENSITY_MIN_ENTRIES,
    MIN_DISTINCT_DAYS,
    SAME_DAY_ALERT_LENGTH,
    SAME_DAY_CLUSTER_LENGTH,
    STREAK_LENGTH,
    STREAK_WINDOW_DAYS,
    find_density_streaks,
    find_negative_streaks,
    find_repeated_streaks,
    find_same_day_clusters,
    normalize_emotion,
    parse_ts,
    read_log,
)


def make_student(entries, student_id="s1"):
    """entries: list of (iso_timestamp_string, emotion_string) tuples.

    Returns a student dict shaped like a real row from students.jsonl, so
    tests exercise the same parsing (read_log) that production data goes
    through, not just the detector functions in isolation.
    """
    return {
        "id": student_id,
        "classroom_code": "TEST01",
        "emotion_log": [{"ts": ts, "emotion": emo} for ts, emo in entries],
    }


def log_for(entries):
    """Shortcut: raw (ts, emotion) tuples -> the cleaned, sorted log that
    find_*_streaks functions expect as input."""
    return read_log(make_student(entries), Counter())


class NegativeStreakTests(unittest.TestCase):

    def test_four_negatives_across_two_days_flags(self):
        self.assertEqual(STREAK_LENGTH, 4, "test assumes the default streak length")
        log = log_for([
            ("2026-08-03T09:00:00", "frustrated"),
            ("2026-08-03T13:00:00", "anxious"),
            ("2026-08-04T09:00:00", "angry"),
            ("2026-08-04T13:00:00", "sad"),
        ])
        streaks = find_negative_streaks(log)
        self.assertEqual(len(streaks), 1)
        self.assertEqual(streaks[0]["type"], "negative")
        self.assertEqual(streaks[0]["length"], 4)

    def test_three_negatives_does_not_flag(self):
        log = log_for([
            ("2026-08-03T09:00:00", "frustrated"),
            ("2026-08-03T13:00:00", "anxious"),
            ("2026-08-04T09:00:00", "angry"),
        ])
        self.assertEqual(find_negative_streaks(log), [])

    def test_run_broken_by_a_positive_does_not_merge(self):
        """2 negatives, then a happy, then 2 more negatives is two runs of
        2 - neither reaches STREAK_LENGTH, so nothing should fire, even
        though there are 4 negative entries total."""
        log = log_for([
            ("2026-08-03T09:00:00", "sad"),
            ("2026-08-03T10:00:00", "angry"),
            ("2026-08-03T11:00:00", "happy"),
            ("2026-08-04T09:00:00", "sad"),
            ("2026-08-04T10:00:00", "angry"),
        ])
        self.assertEqual(find_negative_streaks(log), [])

    def test_same_day_four_negatives_does_not_count_as_a_streak(self):
        """MIN_DISTINCT_DAYS requires the run to touch more than one
        calendar day - four negatives all on one day is same_day_cluster's
        job, not negative streak's (see that trigger's own test below)."""
        self.assertEqual(MIN_DISTINCT_DAYS, 2, "test assumes the default")
        log = log_for([
            ("2026-08-03T08:00:00", "sad"),
            ("2026-08-03T09:30:00", "angry"),
            ("2026-08-03T11:00:00", "frustrated"),
            ("2026-08-03T13:00:00", "anxious"),
        ])
        self.assertEqual(find_negative_streaks(log), [])

    def test_negatives_spread_past_the_window_do_not_count(self):
        """Four negative entries, no interruption, but spaced 3 days apart
        each (9 days start-to-end). No 7-day window ever contains 4 of
        them, so this should not flag despite being one long "run"."""
        self.assertEqual(STREAK_WINDOW_DAYS, 7, "test assumes the default")
        log = log_for([
            ("2026-08-01T09:00:00", "sad"),
            ("2026-08-04T09:00:00", "angry"),
            ("2026-08-07T09:00:00", "anxious"),
            ("2026-08-10T09:00:00", "frustrated"),
        ])
        self.assertEqual(find_negative_streaks(log), [])


class SameDayClusterTests(unittest.TestCase):

    def test_four_same_day_negatives_is_a_watch_cluster(self):
        self.assertEqual(SAME_DAY_CLUSTER_LENGTH, 4, "test assumes the default")
        log = log_for([
            ("2026-08-03T08:00:00", "sad"),
            ("2026-08-03T09:30:00", "angry"),
            ("2026-08-03T11:00:00", "frustrated"),
            ("2026-08-03T13:00:00", "anxious"),
        ])
        clusters = find_same_day_clusters(log)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["channel"], "watch")

    def test_six_same_day_negatives_escalates_to_alert(self):
        self.assertEqual(SAME_DAY_ALERT_LENGTH, 6, "test assumes the default")
        log = log_for([(f"2026-08-03T{h:02d}:00:00", "sad") for h in range(8, 14)])
        clusters = find_same_day_clusters(log)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["channel"], "alert")


class RepeatedStreakTests(unittest.TestCase):

    def test_four_repeated_positive_flags(self):
        log = log_for([
            ("2026-08-03T09:00:00", "happy"),
            ("2026-08-03T10:00:00", "happy"),
            ("2026-08-04T09:00:00", "happy"),
            ("2026-08-04T10:00:00", "happy"),
        ])
        streaks = find_repeated_streaks(log)
        self.assertEqual(len(streaks), 1)
        self.assertEqual(streaks[0]["emotion"], "happy")

    def test_four_repeated_negative_is_not_a_repeated_trigger(self):
        """Repeated only fires on positive valence (see REPEATED_VALENCES
        in detect_patterns.py) - a repeated negative emotion is the
        negative-streak trigger's job instead."""
        log = log_for([
            ("2026-08-03T09:00:00", "sad"),
            ("2026-08-03T10:00:00", "sad"),
            ("2026-08-04T09:00:00", "sad"),
            ("2026-08-04T10:00:00", "sad"),
        ])
        self.assertEqual(find_repeated_streaks(log), [])

    def test_four_repeated_neutral_does_not_flag(self):
        """'okay' x4 is an unremarkable week, deliberately excluded."""
        log = log_for([
            ("2026-08-03T09:00:00", "okay"),
            ("2026-08-03T10:00:00", "okay"),
            ("2026-08-04T09:00:00", "okay"),
            ("2026-08-04T10:00:00", "okay"),
        ])
        self.assertEqual(find_repeated_streaks(log), [])


class DensityStreakTests(unittest.TestCase):

    def test_mostly_negative_week_broken_up_still_flags_density(self):
        """6 entries, 5/6 negative, spread across 6 distinct days, but the
        longest unbroken negative run is only 3 - too short for
        find_negative_streaks. Density exists exactly to catch this."""
        self.assertEqual(DENSITY_MIN_ENTRIES, 6, "test assumes the default")
        log = log_for([
            ("2026-08-01T09:00:00", "sad"),
            ("2026-08-02T09:00:00", "angry"),
            ("2026-08-03T09:00:00", "happy"),
            ("2026-08-04T09:00:00", "anxious"),
            ("2026-08-05T09:00:00", "frustrated"),
            ("2026-08-06T09:00:00", "overwhelmed"),
        ])
        self.assertEqual(find_negative_streaks(log), [], "should not also be caught as a streak")
        hits = find_density_streaks(log)
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0]["negative_share"], 5 / 6, places=3)


class ParsingTests(unittest.TestCase):

    def test_normalize_emotion_handles_aliases_and_case(self):
        self.assertEqual(normalize_emotion("Worried"), "anxious")
        self.assertEqual(normalize_emotion("MAD"), "angry")
        self.assertEqual(normalize_emotion("great"), "happy")
        self.assertIsNone(normalize_emotion("banana"))
        self.assertIsNone(normalize_emotion(None))
        self.assertIsNone(normalize_emotion(123))

    def test_parse_ts_handles_both_schemas(self):
        # generated data: a single "ts" field
        self.assertEqual(parse_ts({"ts": "2026-08-03T09:12:00"}).isoformat(),
                         "2026-08-03T09:12:00")
        # Focus Bridge app exports: separate "iso" date + "time" fields
        self.assertEqual(parse_ts({"iso": "2026-08-03", "time": "09:12 AM"}).isoformat(),
                         "2026-08-03T09:12:00")
        self.assertEqual(parse_ts({"iso": "2026-08-03", "time": "9:12:00 PM"}).isoformat(),
                         "2026-08-03T21:12:00")
        self.assertIsNone(parse_ts({}))

    def test_read_log_drops_bad_timestamps_but_keeps_unknown_emotions(self):
        """Documented behavior: an unreadable timestamp can't be placed in
        the sequence, so it's dropped. An unrecognized emotion still has a
        usable timestamp, so it's kept with emotion=None - it still
        occupies a slot and breaks a run, it just isn't itself negative
        or positive."""
        student = make_student([
            ("not-a-timestamp", "happy"),
            ("2026-08-03T09:00:00", "grumpy"),  # not in our emotion vocabulary
        ])
        problems = Counter()
        log = read_log(student, problems)
        self.assertEqual(len(log), 1)
        self.assertIsNone(log[0]["emotion"])
        self.assertEqual(problems["bad_timestamp"], 1)
        self.assertEqual(problems["unknown_emotion"], 1)


if __name__ == "__main__":
    unittest.main()
