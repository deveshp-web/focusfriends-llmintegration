"""Tests that the pipeline reads data in the shapes the app and Supabase produce.

Every test elsewhere builds its input in the synthetic corpus's shape, which is
exactly why two bugs survived: the corpus writes ``{"ts": ...}`` and naive
timestamps, while the app writes ``{"iso", "time"}`` and Supabase returns
timezone-aware ones. Both bugs only fired on real data.

The rows below are copied from what the app and Supabase actually send - see
``toRow`` and the note mapping in ``integrated_version/index.html``.
"""

from __future__ import annotations

import unittest
from collections import Counter
from datetime import datetime

from focusbridge.core.timeline import read_check_ins
from focusbridge.recommend.context import index_tasks, nearest_task, read_notes
from focusbridge.recommend.settings import DEFAULT_SETTINGS

#: A note row exactly as Supabase's REST API returns it: `created_at` is a
#: `timestamptz`, so it always carries an offset, and usually microseconds.
SUPABASE_NOTE = {"text": "Struggled with the transition to Gym; needed support.",
                 "created_at": "2026-08-06T23:30:00.123456+00:00"}

#: Task and check-in rows exactly as the app writes them.
APP_TASK = {"emoji": "⚽", "task": "Recess", "time": "11:45 AM",
            "iso": "2026-08-07", "status": "skipped"}
APP_CHECK_IN = {"emoji": "😢", "label": "Sad", "time": "11:46 AM",
                "date": "08/07/2026", "iso": "2026-08-07"}


class SupabaseNoteTests(unittest.TestCase):

    def test_a_timezone_aware_note_does_not_crash(self):
        """Regression: stage 2 died on the first note from a real database.

        Comparing an aware ``created_at`` with the naive window start raised
        ``TypeError: can't compare offset-naive and offset-aware datetimes``.
        """
        signals = read_notes([SUPABASE_NOTE], cutoff=datetime(2026, 8, 1))
        self.assertEqual(signals["count"], 1)

    def test_its_wall_clock_reading_is_kept(self):
        """The offset is dropped, not converted - the same choice the browser
        engine makes, so the two agree on which notes fall in the window."""
        signals = read_notes([SUPABASE_NOTE], cutoff=datetime(2026, 8, 1))
        self.assertTrue(signals["recent"][0]["ts"].startswith("2026-08-06T23:30:00"))

    def test_it_is_still_mined_for_the_named_trigger(self):
        signals = read_notes([SUPABASE_NOTE], cutoff=datetime(2026, 8, 1))
        self.assertEqual(signals["named_trigger"][0]["task"], "Gym")

    def test_notes_before_the_window_are_still_excluded(self):
        signals = read_notes([SUPABASE_NOTE], cutoff=datetime(2026, 8, 7))
        self.assertEqual(signals["count"], 0)


class AppTaskRowTests(unittest.TestCase):

    def test_app_task_rows_are_read(self):
        """Regression: every app task row used to be dropped.

        ``index_tasks`` only understood ``{"ts": ...}``, and the app's rows have
        no ``ts``. So the activity correlation could never fire on real data.
        """
        stamps, rows = index_tasks({"task_log": [APP_TASK]})
        self.assertEqual(len(rows), 1)
        self.assertEqual(stamps[0], datetime(2026, 8, 7, 11, 45))
        self.assertEqual(rows[0]["status"], "skipped")

    def test_an_app_check_in_finds_its_app_task(self):
        """The join the activity rule depends on, end to end in app shapes."""
        check_in = read_check_ins({"emotion_log": [APP_CHECK_IN]}, Counter())[0]
        stamps, rows = index_tasks({"task_log": [APP_TASK]})
        row = nearest_task(stamps, rows, check_in.at, DEFAULT_SETTINGS.task_window)
        self.assertIsNotNone(row)
        self.assertEqual(row["task"], "Recess")

    def test_corpus_task_rows_still_work(self):
        stamps, rows = index_tasks({"task_log": [
            {"ts": "2026-08-07T11:45:00", "task": "Recess", "status": "done"}]})
        self.assertEqual(stamps, [datetime(2026, 8, 7, 11, 45)])

    def test_unreadable_task_rows_are_skipped_not_fatal(self):
        stamps, rows = index_tasks({"task_log": [
            {"task": "Recess"}, {"iso": "not-a-date", "task": "Gym"}, "junk", None]})
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
