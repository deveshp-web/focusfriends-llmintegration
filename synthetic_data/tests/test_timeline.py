"""Tests for parsing and for the fast window index.

The :class:`Timeline` tests are the important ones here. Prefix sums are a
performance optimisation, and the failure mode of a wrong optimisation is
*silently different answers* rather than a crash - so they are checked against
the obvious, slow, obviously-correct implementation over randomised input.
"""

from __future__ import annotations

import random
import unittest
from collections import Counter
from datetime import datetime, timedelta

from focusbridge.core.timeline import (CheckIn, Timeline, parse_clock,
                                       parse_timestamp, read_check_ins)
from focusbridge.core.vocabulary import (NEGATIVE_EMOTIONS, EMOTION_VALENCE,
                                         normalize_emotion)

from .helpers import make_student, timeline_for


class VocabularyTests(unittest.TestCase):

    def test_aliases_and_case_are_normalised(self):
        self.assertEqual(normalize_emotion("Worried"), "anxious")
        self.assertEqual(normalize_emotion("MAD"), "angry")
        self.assertEqual(normalize_emotion("  great "), "happy")

    def test_unusable_values_become_none(self):
        """Every one of these is something a real export has produced."""
        for value in ("banana", None, 123, "", "   "):
            self.assertIsNone(normalize_emotion(value), value)

    def test_derived_sets_match_the_table(self):
        """The derived lookups must never drift from EMOTION_VALENCE."""
        self.assertEqual(
            NEGATIVE_EMOTIONS,
            {e for e, v in EMOTION_VALENCE.items() if v == "negative"})


class TimestampTests(unittest.TestCase):

    def test_both_schemas_parse(self):
        # the generated corpus: one "ts" field
        self.assertEqual(parse_timestamp({"ts": "2026-08-03T09:12:00"}).isoformat(),
                         "2026-08-03T09:12:00")
        # app exports: separate date and clock fields
        self.assertEqual(
            parse_timestamp({"iso": "2026-08-03", "time": "09:12 AM"}).isoformat(),
            "2026-08-03T09:12:00")
        self.assertEqual(
            parse_timestamp({"iso": "2026-08-03", "time": "9:12:00 PM"}).isoformat(),
            "2026-08-03T21:12:00")

    def test_unreadable_rows_return_none(self):
        self.assertIsNone(parse_timestamp({}))
        self.assertIsNone(parse_timestamp({"ts": "not-a-timestamp"}))

    def test_a_broken_ts_does_not_fall_through_to_the_date_field(self):
        """A row with both a broken ``ts`` and a good ``iso`` is corrupt.

        Guessing from the second field would invent data, so the answer is
        ``None``.
        """
        self.assertIsNone(parse_timestamp({"ts": "garbage", "iso": "2026-08-03"}))

    def test_midnight_and_noon_are_the_cases_people_get_wrong(self):
        self.assertEqual(parse_clock("12:30 AM"), (0, 30))    # midnight
        self.assertEqual(parse_clock("12:30 PM"), (12, 30))   # noon
        self.assertIsNone(parse_clock("25:99"))

    def test_narrow_no_break_space_before_the_meridiem(self):
        """Some platforms emit U+202F, not a space. It is invisible in a diff."""
        self.assertEqual(parse_clock("9:41 PM"), (21, 41))


class ReadCheckInsTests(unittest.TestCase):

    def test_bad_timestamps_are_dropped_and_unknown_emotions_are_kept(self):
        """The single most important behaviour in the parsing layer.

        An unreadable timestamp cannot be placed in the sequence, so the row
        goes. An unrecognised *emotion* still has a usable timestamp, so it is
        kept with ``emotion=None``: it occupies a slot and breaks a run.
        """
        problems = Counter()
        log = read_check_ins(make_student([
            ("not-a-timestamp", "happy"),
            ("2026-08-03T09:00:00", "grumpy"),   # not in the vocabulary
        ]), problems)

        self.assertEqual(len(log), 1)
        self.assertIsNone(log[0].emotion)
        self.assertEqual(problems["bad_timestamp"], 1)
        self.assertEqual(problems["unknown_emotion"], 1)

    def test_rows_are_sorted_and_ties_are_broken_deterministically(self):
        """Two exports of the same check-ins in different row orders must agree."""
        entries = [("2026-08-03T09:00:00", "sad"),
                   ("2026-08-03T09:00:00", "angry"),
                   ("2026-08-01T09:00:00", "happy")]
        forwards = read_check_ins(make_student(entries), Counter())
        backwards = read_check_ins(make_student(list(reversed(entries))), Counter())
        self.assertEqual(forwards, backwards)
        self.assertEqual([c.emotion for c in forwards], ["happy", "angry", "sad"])


class TimelineIndexTests(unittest.TestCase):
    """The prefix-sum index, checked against the slow-but-obvious version."""

    @staticmethod
    def _naive_negatives(check_ins, start, end):
        return sum(1 for c in check_ins[start:end + 1]
                   if c.emotion in NEGATIVE_EMOTIONS)

    @staticmethod
    def _naive_distinct_days(check_ins, start, end):
        return len({c.at.date() for c in check_ins[start:end + 1]})

    def test_range_queries_match_the_obvious_implementation(self):
        """Randomised, over every possible range of many random logs.

        This is the test that makes the optimisation safe to keep: it does not
        check that the prefix sums are *built* a particular way, it checks that
        they give the same answers as counting by hand, for every range.
        """
        rng = random.Random(20260904)
        emotions = list(EMOTION_VALENCE) + [None]

        for trial in range(60):
            count = rng.randint(1, 25)
            at = datetime(2026, 8, 1, 8, 0)
            check_ins = []
            for _ in range(count):
                at += timedelta(minutes=rng.choice([30, 240, 1440, 4320]))
                check_ins.append(CheckIn(at, rng.choice(emotions)))

            timeline = Timeline(check_ins)
            for start in range(count):
                for end in range(start, count):
                    self.assertEqual(
                        timeline.negative_count(start, end),
                        self._naive_negatives(check_ins, start, end),
                        f"negatives trial={trial} range={start}..{end}")
                    self.assertEqual(
                        timeline.distinct_days(start, end),
                        self._naive_distinct_days(check_ins, start, end),
                        f"days trial={trial} range={start}..{end}")

    def test_empty_timeline_is_safe(self):
        timeline = Timeline([])
        self.assertEqual(len(timeline), 0)
        self.assertEqual(list(timeline.maximal_windows(timedelta(days=7))), [])

    def test_negative_share_of_an_empty_range_is_zero_not_an_error(self):
        timeline = timeline_for([("2026-08-01T09:00:00", "sad")])
        self.assertEqual(timeline.negative_share(1, 0), 0.0)

    def test_maximal_windows_respect_the_span_limit(self):
        timeline = timeline_for([(f"2026-08-{day:02d}T09:00:00", "sad")
                                 for day in range(1, 21)])
        for start, end in timeline.maximal_windows(timedelta(days=7)):
            self.assertLessEqual(timeline.span(start, end), timedelta(days=7))
            # And it really is *maximal*: one more entry would break the limit.
            if end + 1 < len(timeline):
                self.assertGreater(timeline.span(start, end + 1), timedelta(days=7))

    def test_sub_range_scanning_matches_scanning_a_copy(self):
        """``lo``/``hi`` must behave exactly like slicing out the run first."""
        entries = [(f"2026-08-{day:02d}T09:00:00", "sad") for day in range(1, 16)]
        timeline = timeline_for(entries)
        window = timedelta(days=7)

        scoped = list(timeline.maximal_windows(window, lo=3, hi=9))
        standalone = Timeline(timeline.slice(3, 9))
        expected = [(start + 3, end + 3)
                    for start, end in standalone.maximal_windows(window)]
        self.assertEqual(scoped, expected)


if __name__ == "__main__":
    unittest.main()
