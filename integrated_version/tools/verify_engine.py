#!/usr/bin/env python3
"""Compute what the Python pipeline says about the sample rooms, for comparison.

WHY THIS EXISTS
---------------
``fb-insights.js`` is a port of the ``focusbridge`` Python package, and a port
is only worth anything if somebody checks it. The interesting failures are not
crashes - those announce themselves - but a threshold typed as ``0.5`` instead
of ``0.05``, or a tie broken the other way, which silently moves one child up
or down a teacher's list and looks completely normal on screen.

So this script runs the **real Python stages** over the **same records the
browser sees** - it reads ``demo-data.js``, converts each room back into the
corpus shapes, and calls ``focusbridge.detect`` and ``focusbridge.recommend``
directly - and writes ``expected.json``. ``verify.html`` then loads that file
next to ``fb-insights.js`` and diffs the two, student by student.

Reading ``demo-data.js`` rather than the corpus is the point: if the exporter's
translation lost something, both sides see the loss and the comparison still
means what it claims. And the reference time is pinned per room to that room's
own latest check-in, which is exactly what the engine does when no reference is
supplied, so the two are looking at the same 30 days.

Usage::

    python tools/verify_engine.py           # writes tools/expected.json
    python tools/verify_engine.py --summary # ...and prints what it found

Then open ``tools/verify.html`` in a browser for the actual comparison.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.normpath(os.path.join(HERE, "..", "..", "synthetic_data"))
sys.path.insert(0, PIPELINE)

try:
    from focusbridge.core.timeline import Timeline, read_check_ins
    from focusbridge.detect.pipeline import analyse_student
    from focusbridge.detect.summarize import summarize
    from focusbridge.recommend.context import StoryIndex, build_context, read_notes
    from focusbridge.recommend.room_rules import build_room_recommendations
    from focusbridge.recommend.student_rules import (build_recommendations,
                                                     headline_reason, rank_and_cap)
except ImportError as error:                                   # pragma: no cover
    sys.exit(f"could not import the focusbridge package from {PIPELINE}\n  {error}\n"
             "Pass --pipeline to point at the directory containing focusbridge/.")


# --------------------------------------------------------------------------
# reading demo-data.js back
# --------------------------------------------------------------------------

def load_demo_data(path):
    """Pull the JSON object out of ``demo-data.js``.

    The file is a script, not JSON: a comment, then ``window.FB_DEMO_ROOMS =``,
    then the object, then a semicolon. Sliced between the first ``{`` and the
    last ``}`` rather than evaluated, because a verification tool that executes
    the thing it is verifying is not verifying much.
    """
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        sys.exit(f"{path} does not look like demo-data.js")
    return json.loads(text[start:end + 1])


# --------------------------------------------------------------------------
# app shapes -> corpus shapes
# --------------------------------------------------------------------------
# The engine reads the app's records directly. The Python stages read the
# corpus's. Rather than teach either one about the other, the app records are
# translated here - the inverse of what the exporter did - so both sides are
# genuinely analysing the same check-ins.

def to_corpus_task_log(rows):
    """App task rows -> the corpus's ``{ts, task, status}`` shape.

    .. warning::
       **This conversion is doing more than it looks like it is.**

       ``emotion_log`` below is deliberately left in the app's ``{iso, time}``
       schema, because the pipeline's ``parse_timestamp`` reads both schemas and
       leaving it alone keeps that path under test. ``task_log`` cannot be left
       alone, because ``focusbridge.recommend.context.index_tasks`` does **not**
       go through ``parse_timestamp`` - it calls
       ``datetime.fromisoformat(str(raw.get("ts")))`` directly. An app task row
       has no ``ts``, so every one of them is dropped, and the activity
       correlation - the rule the recommender itself calls "the strongest thing
       a teacher can actually change" - silently finds nothing on live app data.

       Verified against ``stu_3432bd2e6cf4``: identical timeline, identical
       streaks, identical flagged positions, and the cluster (Transition to Gym,
       8 of 12 flagged against a 36% baseline, p=0.029) appears from the corpus
       rows and vanishes from the app rows.

       ``fb-insights.js`` does not share the bug - its ``indexTasks`` uses the
       same tolerant parser as its check-in reader. Converting here is therefore
       not papering over a difference between the two engines; it is removing a
       Python-side defect from the comparison so that everything else in it
       still means something. The one-line upstream fix is to replace that
       ``fromisoformat`` call with ``parse_timestamp(raw)``.
    """
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        stamp = row.get("ts")
        if not stamp:
            day, clock = row.get("iso"), parse_clock_text(row.get("time"))
            if not day or clock is None:
                continue
            stamp = f"{day}T{clock[0]:02d}:{clock[1]:02d}:00"
        out.append({"ts": stamp, "task": row.get("task"), "status": row.get("status")})
    return out


def parse_clock_text(raw):
    """``"09:12 AM"`` -> ``(9, 12)``; ``None`` if unreadable.

    The pipeline's own ``parse_clock`` would do, but importing a private helper
    of the thing under test into the harness that tests it is how a harness ends
    up agreeing with a bug. Twelve lines, written independently.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().upper().replace(" ", " ")
    meridiem = None
    for marker in ("AM", "PM"):
        if text.endswith(marker):
            meridiem, text = marker, text[:-2].strip()
            break
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if meridiem == "PM" and hour != 12:
        hour += 12
    elif meridiem == "AM" and hour == 12:
        hour = 0
    return (hour, minute) if 0 <= hour < 24 and 0 <= minute < 60 else None


def to_corpus_student(student_id, record, code):
    """One app student record -> the row ``students.jsonl`` would have held."""
    return {
        "id": student_id,
        "classroom_code": code,
        "name": record.get("name"),
        "avatar": record.get("avatar"),
        "stars": record.get("stars", 0),
        "coins": record.get("coins", 0),
        "tokens": record.get("tokens", 0),
        "badges": record.get("badges") or [],
        "emotion_log": record.get("emotionLog") or [],
        "task_log": to_corpus_task_log(record.get("taskLog")),
        "schedule_state": record.get("schedule") or [],
        "aac_count": record.get("aacCount", 0),
        "emo_count": record.get("emoCount", 0),
        "sentence_count": record.get("sentenceCount", 0),
        "stories_read": record.get("storiesRead", 0),
        "calm_done": record.get("calmDone") or [],
        "streak": record.get("streak", 0),
        "last_seen": record.get("lastSeen") or "",
    }


def to_corpus_notes(rows):
    """App note rows -> the ``{created_at, text}`` shape the miner expects."""
    return [{"created_at": row.get("created_at"), "text": row.get("text") or ""}
            for row in rows or [] if row.get("created_at")]


def to_corpus_stories(stories):
    """App stories -> the corpus story shape (only title and pages are read)."""
    return [{"title": story.get("title"), "pages": story.get("pages") or []}
            for story in stories or []]


# --------------------------------------------------------------------------
# running the real stages over one room
# --------------------------------------------------------------------------

def latest_check_in(students):
    """The room's most recent check-in - what the engine anchors its window to."""
    latest = None
    problems = Counter()
    for student in students:
        for check_in in read_check_ins(student, problems):
            if latest is None or check_in.at > latest:
                latest = check_in.at
    return latest


def analyse_room(room):
    """Everything the Python pipeline says about one sample room."""
    code = room["code"]
    classroom = room["classroom"]
    students = [to_corpus_student(entry["id"], room["students"][entry["id"]], code)
                for entry in room["roster"] if entry["id"] in room["students"]]

    reference = latest_check_in(students)
    if reference is None:
        return None
    cutoff = reference - timedelta(days=30)

    stories = StoryIndex(to_corpus_stories(room["stories"]))
    room_meta = {"token_goal": classroom.get("tokenGoal"),
                 "token_reward": classroom.get("tokenReward")}

    contexts, problems = [], Counter()
    for student in students:
        # Stage 1, the real function, not a re-implementation of it.
        timeline, streaks, _runs = analyse_student(student, cutoff, problems)
        if not streaks:
            continue
        summary = summarize(streaks, timeline.check_ins)

        insight = {
            "student_id": student["id"],
            "student_name": student.get("name"),
            "classroom_code": code,
            "name_mode": classroom.get("nameMode") or "full",
            "channel": summary["channel"],
            "priority": summary["priority"],
            "summary": summary,
            "streaks": streaks,
        }
        # Stage 2, likewise.
        contexts.append(build_context(
            student, insight, room_meta, timeline,
            read_notes(to_corpus_notes(room["notes"].get(student["id"])), cutoff),
            stories))

    # The project-wide ordering: most urgent first, then most separate alert
    # episodes, then by id so two runs agree.
    contexts.sort(key=lambda context: (
        {"high": 0, "medium": 1, "watch": 2, "info": 3}.get(context.priority, 4),
        -context.summary["alert_streak_count"],
        context.student_id))

    per_student = {}
    for context in contexts:
        advice = rank_and_cap(build_recommendations(context), context.channel)
        per_student[context.student_id] = {
            "name": context.display_name,
            "channel": context.channel,
            "priority": context.priority,
            "why": headline_reason(context),
            "trajectory": context.trajectory["direction"],
            "delta": context.trajectory["delta"],
            "noteCount": context.note_count,
            "dominantEmotion": (context.emotion_mix.most_common(1)[0][0]
                                if context.emotion_mix else None),
            "taskClusters": [cluster["task"] for cluster in context.tasks],
            "timeCluster": context.time["bucket"] if context.time else None,
            "recommendations": [item["id"] for item in advice],
            "headlines": [item["headline"] for item in advice],
        }

    counts = Counter(context.channel for context in contexts)
    return {
        "code": code,
        "reference": reference.isoformat(),
        "windowStart": cutoff.isoformat(),
        "counts": {
            "flagged": len(contexts),
            "alert": counts["alert"],
            "watch": counts["watch"],
            "positive": counts["positive"],
            "high": sum(1 for c in contexts if c.priority == "high"),
            "undoc": sum(1 for c in contexts
                         if c.channel == "alert" and c.note_count == 0),
        },
        "roomRecommendations": [item["id"] for item
                                in build_room_recommendations(room_meta, contexts)],
        "order": [context.student_id for context in contexts],
        "students": per_student,
    }


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--demo-data", default=os.path.join(HERE, "..", "demo-data.js"))
    parser.add_argument("--out", default=os.path.join(HERE, "expected.json"))
    parser.add_argument("--summary", action="store_true",
                        help="print what the pipeline found in each room")
    args = parser.parse_args(argv)

    data = load_demo_data(args.demo_data)
    rooms = [analyse_room(room) for room in data["rooms"]]
    rooms = [room for room in rooms if room]

    expected = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "note": ("Produced by the Python focusbridge package over the records in "
                 "demo-data.js. verify.html diffs fb-insights.js against this."),
        "rooms": {room["code"]: room for room in rooms},
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(expected, handle, ensure_ascii=False, indent=1)

    # The same content again as a plain script. `verify.html` has to work from
    # file://, where fetching a .json sitting next to it is blocked as a
    # cross-origin request but a <script src> is not. The .json stays for
    # anything that wants to read it as data.
    script_path = os.path.splitext(args.out)[0] + ".js"
    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write("/* Generated by tools/verify_engine.py — do not edit. */\n"
                     "window.FB_EXPECTED = ")
        json.dump(expected, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write(";\n")

    print(f"wrote {os.path.abspath(args.out)}")
    print(f"wrote {os.path.abspath(script_path)}")
    for room in rooms:
        counts = room["counts"]
        print(f"  {room['code']}  window ends {room['reference'][:10]}  "
              f"{counts['flagged']:>3} flagged  "
              f"{counts['alert']} alert / {counts['watch']} watch / "
              f"{counts['positive']} positive  "
              f"{counts['high']} high  room rules: "
              f"{', '.join(room['roomRecommendations']) or 'none'}")

    if args.summary:
        rules = Counter(rule for room in rooms
                        for student in room["students"].values()
                        for rule in student["recommendations"])
        print("\n  recommendation rules fired across all sample rooms:")
        for rule, count in rules.most_common():
            print(f"    {count:>4}  {rule}")

    print("\nNow open tools/verify.html in a browser to compare fb-insights.js "
          "against these results.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
