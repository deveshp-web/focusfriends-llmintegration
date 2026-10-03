"""Tests for the analysis window - which check-ins a run is allowed to see.

Two bugs lived here, and both were invisible in normal output:

  * **No upper bound.** A run pinned to an earlier date still read every
    check-in after it, so the "as of 31 July" snapshot the change feed compares
    against quietly included August. On the corpus, 1,698 students' previous-run
    findings used check-ins from after the snapshot date.
  * **No guard on the anchor.** The window ends at the latest check-in, and one
    check-in dated in the future - a tablet with a wrong clock - moved the
    window past everyone's real data. The detector then flagged nobody.

Each test below fails on the code as it was before those fixes.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from focusbridge.core.timeline import (FUTURE_TOLERANCE, CheckIn, is_future_dated,
                                       within_window)
from focusbridge.detect.pipeline import analyse_student, latest_check_in_time

NOW = datetime(2026, 10, 2, 12, 0)


def student(rows, student_id="s1"):
    return {"id": student_id, "classroom_code": "R1",
            "emotion_log": [{"ts": ts, "emotion": emotion} for ts, emotion in rows]}


def seven_sad_days(month="08"):
    return [(f"2026-{month}-{day:02d}T09:00:00", "sad") for day in range(1, 8)]


class WithinWindowTests(unittest.TestCase):

    def test_both_ends_are_inclusive(self):
        start, end = datetime(2026, 8, 1), datetime(2026, 8, 7)
        rows = [CheckIn(start, "sad"), CheckIn(end, "sad")]
        self.assertEqual(within_window(rows, start, end), rows)

    def test_a_check_in_after_the_end_is_excluded(self):
        """The missing upper bound, at its smallest."""
        start, end = datetime(2026, 8, 1), datetime(2026, 8, 7)
        late = CheckIn(end + timedelta(seconds=1), "sad")
        self.assertEqual(within_window([late], start, end), [])

    def test_future_tolerance_is_one_day(self):
        """The JS engine uses the same constant; changing one means changing both."""
        self.assertEqual(FUTURE_TOLERANCE, timedelta(days=1))
        self.assertFalse(is_future_dated(NOW + timedelta(hours=23), NOW))
        self.assertTrue(is_future_dated(NOW + timedelta(days=1, minutes=1), NOW))


class AnchorTests(unittest.TestCase):
    """``latest_check_in_time`` - where an unpinned run's window ends."""

    def _corpus(self, *students):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "students.jsonl"
        path.write_text("".join(json.dumps(s) + "\n" for s in students), encoding="utf-8")
        return path

    def test_a_future_dated_check_in_does_not_move_the_window(self):
        """Regression: one wrong clock used to silence every classroom.

        The anchor is the latest check-in *anywhere in the corpus*, so a single
        tablet a year ahead moved every room's window into next year.
        """
        path = self._corpus(student(seven_sad_days(), "distressed"),
                            student([("2027-08-01T09:00:00", "happy")], "clock_wrong"))
        self.assertEqual(latest_check_in_time(path, now=NOW),
                         datetime(2026, 8, 7, 9, 0))

    def test_a_check_in_within_the_tolerance_still_counts(self):
        """A device in a timezone a few hours ahead is not a clock error."""
        tomorrow_morning = (NOW + timedelta(hours=20)).isoformat(timespec="seconds")
        path = self._corpus(student([(tomorrow_morning, "happy")]))
        self.assertEqual(latest_check_in_time(path, now=NOW),
                         NOW + timedelta(hours=20))


class AnalyseStudentWindowTests(unittest.TestCase):
    """``analyse_student`` - what one student's analysis is allowed to see."""

    def test_a_pinned_run_does_not_see_later_check_ins(self):
        """Regression: the "as of" snapshot used to include later data.

        The child is sad every morning from 1 to 7 August. Pinned to 4 August,
        the run is four days long and ends on the 4th. Without an upper bound
        the snapshot reported all seven days - a "previous state" that already
        knew how the following week went.
        """
        reference = datetime(2026, 8, 4, 12, 0)
        cutoff = reference - timedelta(days=30)
        timeline, streaks, _ = analyse_student(
            student(seven_sad_days()), cutoff, reference, Counter())

        self.assertTrue(all(c.at <= reference for c in timeline.check_ins))
        self.assertEqual(len(timeline), 4)    # 1st-4th, 09:00 each
        self.assertEqual(len(streaks), 1)     # four in a row across four days...
        self.assertEqual(streaks[0]["end_ts"], "2026-08-04T09:00:00")  # ...ending at the pin

    def test_future_dated_check_ins_are_counted_not_silently_dropped(self):
        """A clock error is reported, so someone can go and fix the device."""
        rows = seven_sad_days() + [("2027-08-01T09:00:00", "happy")]
        problems = Counter()
        reference = datetime(2026, 8, 7, 9, 0)
        timeline, streaks, _ = analyse_student(
            student(rows), reference - timedelta(days=30), reference, problems, now=NOW)

        self.assertEqual(problems["future_dated"], 1)
        self.assertEqual(len(timeline), 7, "the future row is outside the window")
        self.assertTrue(streaks, "and the real streak is still found")

    def test_reference_is_required(self):
        """An optional window end is how the unbounded window would come back."""
        with self.assertRaises(TypeError):
            analyse_student(student(seven_sad_days()), datetime(2026, 7, 1), Counter())


if __name__ == "__main__":
    unittest.main()
