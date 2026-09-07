"""Small builders shared by the test modules.

Every test in this suite builds its input by hand, from tuples that a reader can
check against the assertion without running anything. That is only practical
because the detection rules are pure functions over a list - which is itself one
of the reasons they are written that way.
"""

from __future__ import annotations

from collections import Counter

from focusbridge.core.timeline import Timeline, read_check_ins


def make_student(entries, student_id="s1", classroom_code="TEST01"):
    """A student row shaped like a real line of ``students.jsonl``.

    Args:
        entries: ``(iso timestamp, emotion)`` tuples.

    Building a *student* rather than a ready-made log means the tests exercise
    the same parsing path production data goes through, so a change to
    :func:`read_check_ins` cannot pass the tests while breaking the pipeline.
    """
    return {
        "id": student_id,
        "classroom_code": classroom_code,
        "emotion_log": [{"ts": timestamp, "emotion": emotion}
                        for timestamp, emotion in entries],
    }


def timeline_for(entries, problems=None):
    """``(iso, emotion)`` tuples -> the :class:`Timeline` the rules take."""
    check_ins = read_check_ins(make_student(entries),
                              problems if problems is not None else Counter())
    return Timeline(check_ins)


def one_day(hours, emotion="sad", date="2026-08-03"):
    """Several check-ins on the same date, one per hour given."""
    return [(f"{date}T{hour:02d}:00:00", emotion) for hour in hours]
