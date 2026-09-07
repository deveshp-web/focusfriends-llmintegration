"""Assembling everything known about one flagged student, before any advice.

WHY CONTEXT IS BUILT SEPARATELY FROM THE RULES
----------------------------------------------
The 14 recommendation rules in ``student_rules.py`` are each a short
``if`` statement plus a sentence. They read that way only because every hard
question has already been answered by the time they run: which activity this
student comes apart around, which part of the day, whether they are getting
better or worse, what the teacher has already written down, which calm-corner
tools they have already tried.

This module answers those questions. It does the joins, the statistics and the
text mining, and hands the rules one tidy :class:`StudentContext`.

The separation also means each half can be checked on its own terms: the
correlations can be argued with as statistics, without wading through prose,
and the prose can be reviewed by a teacher without reading a binomial test.
"""

from __future__ import annotations

import bisect
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.triage import display_name
from ..core.vocabulary import NEGATIVE_EMOTIONS
from .phrasing import (CALM_ALIASES, CALM_TOOLS, MAX_RECENT_NOTES, NOTE_FLAGS,
                       NOTE_PATTERNS, STORY_CUES)
from .settings import DEFAULT_SETTINGS, TIME_BUCKET_PHRASES, bucket_for_hour
from .stats import evaluate_group, usable_baseline

# Regexes are compiled **once**, at import, not per note. `re` does cache
# compiled patterns internally, but relying on a cache for something this hot is
# how a cache eviction turns into a mysterious slowdown.
_NOTE_PATTERNS = tuple((key, re.compile(pattern, re.I))
                       for key, pattern in NOTE_PATTERNS)
_NOTE_FLAGS = tuple((key, re.compile(pattern, re.I))
                    for key, pattern in NOTE_FLAGS)

#: How many recent check-ins to keep for the dashboard's evidence strip.
RECENT_STRIP_LENGTH = 40


# ---------------------------------------------------------------------------
# which check-ins actually produced the flag
# ---------------------------------------------------------------------------

def flagged_positions(timeline, streaks):
    """Positions in the timeline of the check-ins that produced the flag.

    Positions rather than the check-ins themselves, because every correlation
    below needs to walk the *whole* recent log asking "was this one of the
    flagged ones?" - and a set of indices answers that in O(1) without relying
    on object identity surviving a list comprehension.

    Positive-channel findings are excluded: a run of "happy" is not evidence
    about what upsets a student, and including it would dilute every rate this
    module computes.

    Complexity: O(m log n + total window width) for m findings over n check-ins.
    The old version tested every check-in against every window, which is
    O(n x m); binary-searching each window's bounds instead means the scan only
    ever touches check-ins that are actually inside a window.
    """
    windows = [(datetime.fromisoformat(streak["start_ts"]),
                datetime.fromisoformat(streak["end_ts"]))
               for streak in streaks if streak["channel"] in ("alert", "watch")]
    if not windows:
        return set()

    times = timeline.times
    flagged = set()
    for window_start, window_end in windows:
        # The timeline is sorted, so the window's check-ins are one contiguous
        # slice and its bounds can be found by binary search.
        first = bisect.bisect_left(times, window_start)
        past_last = bisect.bisect_right(times, window_end)
        for index in range(first, past_last):
            if timeline[index].emotion in NEGATIVE_EMOTIONS:
                flagged.add(index)
    return flagged


# ---------------------------------------------------------------------------
# what the student was doing at the time
# ---------------------------------------------------------------------------

def index_tasks(student):
    """``(sorted timestamps, task rows)`` ready for nearest-task lookup.

    The timestamps are pulled into their own list so :func:`nearest_task` can
    binary-search them directly - ``bisect`` needs a plain sorted sequence, and
    building it once per student beats rebuilding a key list per lookup.
    """
    rows = []
    for raw in student.get("task_log") or []:
        if not isinstance(raw, dict):
            continue
        try:
            at = datetime.fromisoformat(str(raw.get("ts")))
        except (TypeError, ValueError):
            continue  # an unreadable task row simply cannot be placed in time
        label = raw.get("task")
        if isinstance(label, str) and label:
            rows.append({"ts": at, "task": label, "status": raw.get("status")})

    rows.sort(key=lambda row: row["ts"])
    return [row["ts"] for row in rows], rows


def nearest_task(stamps, rows, at, window):
    """The task row closest to ``at``, if one falls inside ``window``.

    Binary search finds where ``at`` *would* sit in the sorted task list; the
    nearest row is then necessarily one of the two neighbours on either side of
    that position, so only two candidates ever need checking.

    Complexity: O(log t) per lookup, against O(t) for scanning every task row -
    and this is called once per check-in per student.
    """
    if not rows:
        return None
    position = bisect.bisect_left(stamps, at)
    best = None
    for candidate in (position - 1, position):
        if 0 <= candidate < len(rows):
            gap = abs(rows[candidate]["ts"] - at)
            if gap <= window and (best is None or gap < best[0]):
                best = (gap, rows[candidate])
    return best[1] if best else None


def correlate_tasks(timeline, flagged_at, student, settings=DEFAULT_SETTINGS):
    """Which activity this student's check-ins go wrong around.

    For every activity, two counts are gathered:

        exposure - how many of this student's check-ins landed near it
        hits     - how many of *those* were flagged

    The rate of hits over exposure is then compared against the student's
    overall flagged rate. An activity is named for going wrong **more often
    than this student's day goes wrong**, never for merely being frequent.
    ``settings.min_task_excess`` records why that distinction took three
    attempts to get right.

    Returns a list ordered by strength of association - an activity with 6 hits
    well above the student's baseline is a better lead than one with 10 that is
    merely busy.
    """
    stamps, rows = index_tasks(student)
    if not rows or not flagged_at:
        return []

    exposure, hits, skipped = Counter(), Counter(), Counter()
    matched = 0
    for index, check_in in enumerate(timeline.check_ins):
        row = nearest_task(stamps, rows, check_in.at, settings.task_window)
        if not row:
            continue
        matched += 1
        task = row["task"]
        exposure[task] += 1
        if index in flagged_at:
            hits[task] += 1
            if row["status"] == "skipped":
                skipped[task] += 1

    baseline = usable_baseline(sum(hits.values()), matched)
    if baseline is None:
        return []
    total_hits = sum(hits.values())

    clusters = []
    for task, hit_count in hits.items():
        comparison = evaluate_group(
            hit_count, exposure[task], baseline,
            min_hits=settings.min_task_hits,
            min_exposure=settings.min_task_exposure,
            min_excess=settings.min_task_excess,
            max_p=settings.task_max_p)
        if comparison is None:
            continue
        # Key order here is part of the output contract - these dictionaries go
        # straight into recommendations.jsonl.
        clusters.append({
            "task": task,
            "hits": comparison.hits,
            "exposure": comparison.exposure,
            "of_flagged": total_hits,
            "rate": round(comparison.rate, 3),
            "usual_rate": round(comparison.usual_rate, 3),
            "lift": round(comparison.lift, 2),
            "p_value": round(comparison.p_value, 4),
            "skipped": skipped[task],
        })

    clusters.sort(key=lambda cluster: (cluster["p_value"], -cluster["hits"]))
    return clusters


def correlate_time(timeline, flagged_at, settings=DEFAULT_SETTINGS):
    """The part of the day this student's check-ins go wrong in, if any.

    Exactly the same test as the activity rule, on exactly the same grounds:
    the *rate* of flagged check-ins inside a time band, against this student's
    own overall rate. Measuring the **share** of flags falling in a band instead
    would be measuring the timetable - see ``settings.time_max_p`` for the
    numbers that make that concrete.
    """
    if len(flagged_at) < settings.time_min_hits:
        return None

    exposure, hits = Counter(), Counter()
    for index, check_in in enumerate(timeline.check_ins):
        bucket = bucket_for_hour(check_in.at.hour)
        if not bucket:
            continue
        exposure[bucket] += 1
        if index in flagged_at:
            hits[bucket] += 1

    baseline = usable_baseline(sum(hits.values()), sum(exposure.values()))
    if baseline is None:
        return None

    best = None
    for bucket, hit_count in hits.items():
        comparison = evaluate_group(
            hit_count, exposure[bucket], baseline,
            min_hits=settings.time_min_hits,
            min_exposure=settings.min_task_exposure,
            min_excess=settings.time_min_excess,
            max_p=settings.time_max_p)
        if comparison is None:
            continue
        # Only the single strongest band is reported. Two time-of-day findings
        # for one student would be two ways of saying the same thing.
        if best is None or comparison.p_value < best["p_value"]:
            best = {
                "bucket": bucket,
                "phrase": TIME_BUCKET_PHRASES[bucket],
                "hits": comparison.hits,
                "exposure": comparison.exposure,
                "rate": round(comparison.rate, 3),
                "usual_rate": round(comparison.usual_rate, 3),
                "excess_points": round(comparison.excess * 100),
                "lift": round(comparison.lift, 2),
                "p_value": round(comparison.p_value, 4),
            }
    return best


def trajectory(timeline):
    """Whether the recent window is getting better or worse.

    Split at the **midpoint in time**, not at the midpoint of the entry count.
    That matters: a student who happened to check in more often during one half
    would otherwise read as a trend when nothing about their week changed.

    Returns ``direction`` plus both halves' rates, so the dashboard can show the
    working rather than asking a teacher to trust the word "worsening".
    """
    unknown = {"direction": "unknown", "first_half": 0.0, "second_half": 0.0,
               "delta": 0.0}
    if len(timeline) < 6:  # too few points for two halves to mean anything
        return unknown

    start, end = timeline.times[0], timeline.times[-1]
    midpoint = start + (end - start) / 2

    def negative_share(check_ins):
        if not check_ins:
            return None
        bad = sum(1 for check_in in check_ins
                  if check_in.emotion in NEGATIVE_EMOTIONS)
        return bad / len(check_ins)

    first = negative_share([c for c in timeline.check_ins if c.at < midpoint])
    second = negative_share([c for c in timeline.check_ins if c.at >= midpoint])
    if first is None or second is None:
        return unknown

    delta = second - first
    # A 15-point swing. Below that, the movement is inside the noise a handful
    # of check-ins produces, and calling it a trend would cry wolf.
    if delta >= 0.15:
        direction = "worsening"
    elif delta <= -0.15:
        direction = "improving"
    else:
        direction = "steady"

    return {"direction": direction,
            "first_half": round(first, 3),
            "second_half": round(second, 3),
            "delta": round(delta, 3)}


# ---------------------------------------------------------------------------
# what the teacher already knows
# ---------------------------------------------------------------------------

def read_notes(notes, cutoff):
    """Structured signals mined from the teacher's own notes in the window.

    The app writes notes from templates, so three of them can be parsed for the
    task the teacher themselves named as the trigger. That is a human judgement
    about causation, which is worth more than any correlation in this file - so
    the rules use it to *corroborate* a computed finding rather than to replace
    or average with it.
    """
    signals = {"named_trigger": [], "calm_worked": [], "needed_prompt": [],
               "flags": set(), "recent": [], "count": 0}

    for note in notes:
        try:
            created = datetime.fromisoformat(str(note.get("created_at")))
        except (TypeError, ValueError):
            continue
        if created < cutoff:
            continue  # older than the window these flags describe

        text = note.get("text") or ""
        signals["count"] += 1
        signals["recent"].append({"ts": created.isoformat(), "text": text})

        for key, pattern in _NOTE_PATTERNS:
            found = pattern.search(text)
            if found:
                signals[key].append({"task": found.group(1).strip(),
                                     "ts": created.isoformat()})
        for key, pattern in _NOTE_FLAGS:
            if pattern.search(text):
                signals["flags"].add(key)

    signals["recent"].sort(key=lambda note: note["ts"], reverse=True)
    signals["recent"] = signals["recent"][:MAX_RECENT_NOTES]
    return signals


def canonical_calm(raw):
    """Map either calm-tool vocabulary onto one id. ``None`` if unrecognised."""
    key = str(raw).strip().lower()
    key = CALM_ALIASES.get(key, key)
    return key if key in CALM_TOOLS else None


class StoryIndex:
    """The social stories one classroom owns, pre-matched against the cue list.

    Built **once per classroom** rather than once per student. The old code
    lower-cased every story title and re-ran all ten cue substring searches for
    every student in the room; in a room with 20 flagged students and 12
    stories that is 2,400 substring searches to answer a question whose answer
    could not have changed. Here the (story, cue) matches are computed once and
    only the student-specific part is done per student.
    """

    __slots__ = ("matches",)

    def __init__(self, stories):
        # Flattened to (story, activities, emotions) in story-then-cue order,
        # which is the order the original nested loops visited them in - and
        # therefore which story wins when several match.
        self.matches = [
            (story, activities, emotions)
            for story in stories
            for cue, activities, emotions in STORY_CUES
            if cue in (story.get("title") or "").lower()
        ]

    def pick(self, task, dominant_emotion):
        """A story that fits, and what it was matched on. ``(None, None)`` if none.

        **Activity match is tried across every story before emotion is
        considered at all.** "Read this before the activity that sets them off"
        is advice; "read a story about being scared to a scared child" is barely
        more than a restatement.

        The emotion fallback deliberately matches only the *dominant* emotion.
        Matching anything in the mix fired for 1,691 of 2,842 students - the
        definition of a recommendation nobody reads.
        """
        task_key = (task or "").lower()
        if task_key:
            for story, activities, _ in self.matches:
                if any(word in task_key for word in activities):
                    return story, "task"
        if dominant_emotion:
            for story, _, emotions in self.matches:
                if dominant_emotion in emotions:
                    return story, "emotion"
        return None, None


# ---------------------------------------------------------------------------
# the assembled context
# ---------------------------------------------------------------------------

@dataclass
class StudentContext:
    """Everything the rules need about one flagged student, already computed.

    A dataclass rather than a dict so that every field is declared in one place
    with a name a reader can search for, and so ``context.dominant_emotion`` is
    an ``AttributeError`` when misspelled rather than a silent ``None``.
    """

    student_id: str
    student_name: Optional[str]
    display_name: str
    classroom_code: Optional[str]

    # -- what the detector said ----------------------------------------------
    summary: Dict[str, Any]
    channel: str
    priority: str
    streaks: List[Dict[str, Any]]

    # -- what this stage worked out ------------------------------------------
    tasks: List[Dict[str, Any]]
    time: Optional[Dict[str, Any]]
    trajectory: Dict[str, Any]
    emotion_mix: Counter
    dominant_emotion: Optional[str]

    # -- what was already on file --------------------------------------------
    notes: Dict[str, Any]
    calm_used: set
    stories: StoryIndex

    # -- the student's own numbers -------------------------------------------
    aac_count: int
    emo_count: int
    tokens: Optional[int]
    token_goal: Optional[int]
    token_reward: str

    # -- for the dashboard ----------------------------------------------------
    recent_strip: List[Dict[str, Any]] = field(default_factory=list)

    @cached_property
    def alert_streaks(self):
        """This student's alert-channel findings.

        A ``cached_property``: several rules need it, it never changes once the
        context is built, and computing it once rather than per rule keeps the
        rules free to ask for it without anyone worrying about the cost.
        """
        return [streak for streak in self.streaks if streak["channel"] == "alert"]

    @property
    def note_count(self):
        return self.notes["count"]

    @property
    def top_task(self):
        """The strongest activity cluster, or ``None``. Used by half the rules."""
        return self.tasks[0] if self.tasks else None

    @property
    def flagged_check_in_count(self):
        """How many flagged check-ins the emotion mix is counted over."""
        return sum(self.emotion_mix.values())


def build_context(student, insight, room, timeline, notes, stories,
                  settings=DEFAULT_SETTINGS):
    """Compute everything about one flagged student, ready for the rules.

    Args:
        student:  the raw row from ``students.jsonl``.
        insight:  what the detector said about them.
        room:     the raw classroom row, for token goals and the name mode.
        timeline: their check-ins inside the recency window.
        notes:    already-mined note signals for this student.
        stories:  the :class:`StoryIndex` for their classroom.
    """
    streaks = insight["streaks"]
    flagged_at = flagged_positions(timeline, streaks)

    # The mix of emotions among the *flagged* check-ins only - what this student
    # is flagged for feeling, not what they generally feel.
    emotion_mix = Counter(timeline[index].emotion for index in flagged_at
                          if timeline[index].emotion)

    calm_used = {tool for tool in
                 (canonical_calm(raw) for raw in student.get("calm_done") or [])
                 if tool}

    return StudentContext(
        student_id=student["id"],
        student_name=insight.get("student_name"),
        display_name=display_name(insight.get("student_name"),
                                  insight.get("name_mode"), student["id"]),
        classroom_code=insight.get("classroom_code"),
        summary=insight["summary"],
        channel=insight["channel"],
        priority=insight["priority"],
        streaks=streaks,
        tasks=correlate_tasks(timeline, flagged_at, student, settings),
        time=correlate_time(timeline, flagged_at, settings),
        trajectory=trajectory(timeline),
        emotion_mix=emotion_mix,
        dominant_emotion=(emotion_mix.most_common(1)[0][0] if emotion_mix else None),
        notes=notes,
        calm_used=calm_used,
        stories=stories,
        aac_count=student.get("aac_count") or 0,
        emo_count=student.get("emo_count") or 0,
        tokens=student.get("tokens"),
        token_goal=room.get("token_goal"),
        token_reward=room.get("token_reward") or "the class reward",
        # Trimmed to the last N: the dashboard draws these as the evidence strip
        # under each student, and the full window would dominate the payload.
        recent_strip=[{"ts": check_in.at.isoformat(), "emotion": check_in.emotion}
                      for check_in in timeline.check_ins[-RECENT_STRIP_LENGTH:]],
    )
