#!/usr/bin/env python3
"""Turn classrooms from the synthetic corpus into sample rooms the app can load.

WHY THIS EXISTS
---------------
The insights view inside the app computes everything in the browser, from the
app's *own* data shapes - the same roster and student records a real classroom
produces. So the synthetic corpus cannot be handed to it as-is: the corpus
speaks ``{"ts": ..., "emotion": ...}`` and snake_case columns, and the app
speaks ``{"iso": ..., "time": ..., "label": ...}`` and camelCase fields.

This script is the translation, run once, offline. It writes ``demo-data.js``,
a plain script that defines ``window.FB_DEMO_ROOMS``. Loading a sample room in
the app writes those records into the app's ordinary demo storage, so every
screen - roster table, stories, schedule, insights - runs on them exactly as it
would on a real class. Nothing in the app knows the data was synthetic.

WHAT IS TRIMMED, AND WHY
------------------------
A full room is about a megabyte of JSON, most of it check-ins from months the
insights window will never look at. Each log is cut to ``WINDOW_DAYS`` back
from the corpus's own reference time, which keeps the file small enough to ship
beside the app while leaving the 30-day window fully intact plus a margin.

Usage::

    python tools/export_demo_rooms.py                    # the three default rooms
    python tools/export_demo_rooms.py --rooms DYAHV5 W39DTP
    python tools/export_demo_rooms.py --data-dir ../synthetic_data
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

# --------------------------------------------------------------------------
# what gets exported
# --------------------------------------------------------------------------

#: The default sample rooms, chosen so that between them every part of the
#: insights view has something to show:
#:
#:   DYAHV5  the rich one - 21 students, all three channels populated, two
#:           students whose distress clusters around a named activity (the
#:           antecedent rule is rare on this corpus, so a room that fires it is
#:           worth picking deliberately).
#:   W39DTP  the only shape of room where the "several students trending down
#:           together" room rule fires.
#:   XDZYTL  a lighter room, so the view can be seen when a class is not in
#:           crisis.
DEFAULT_ROOMS = ("DYAHV5", "W39DTP", "XDZYTL")

#: How far back to keep check-ins, task rows and notes. The insights window is
#: 30 days; 60 leaves room for the "previous run" comparison to have somewhere
#: to stand, and for a reader to scroll back past the window's edge.
WINDOW_DAYS = 60

#: Corpus columns that exist only for the pipeline and would be dead weight in
#: the browser.
DROP_STUDENT_FIELDS = ("access_token", "joined_at")


# --------------------------------------------------------------------------
# reading the corpus
# --------------------------------------------------------------------------

def read_jsonl(path):
    """Yield each row of a JSON Lines file, skipping blank lines."""
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def latest_check_in(students):
    """The most recent check-in across the selected rooms.

    The window is anchored to the *data* rather than to today, for the same
    reason the detector is: this corpus was generated once, and a window
    measured from today would be empty. The app does the same thing when it
    analyses a room, so the two agree about which check-ins are "recent".
    """
    latest = None
    for student in students:
        for row in student.get("emotion_log") or []:
            stamp = _parse(row.get("ts"))
            if stamp and (latest is None or stamp > latest):
                latest = stamp
    return latest


def _parse(text):
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# corpus shapes -> app shapes
# --------------------------------------------------------------------------

def clock(stamp):
    """``datetime`` -> the ``"09:12 AM"`` string the app writes into a log row.

    Written out by hand rather than with ``strftime("%I:%M %p")`` because
    ``%I`` is zero-padded on Linux and space-padded on some platforms, and the
    app's own ``toLocaleTimeString`` produces neither reliably. The app's
    parser accepts all of these; matching its most common output keeps the
    sample data indistinguishable from data a browser wrote.
    """
    hour = stamp.hour % 12 or 12
    return f"{hour:02d}:{stamp.minute:02d} {'AM' if stamp.hour < 12 else 'PM'}"


def emotion_log(rows, cutoff, emoji_for):
    """Corpus check-ins -> the app's ``{emoji, label, time, date, iso}`` rows.

    ``iso`` is the date alone and ``time`` the clock string, which is exactly
    what the app's own check-in handler writes. The insights engine reads the
    pair back into a timestamp, so nothing is lost by not carrying ``ts``.
    """
    out = []
    for row in rows or []:
        stamp = _parse(row.get("ts"))
        if stamp is None or stamp < cutoff:
            continue
        label = str(row.get("emotion") or "").strip()
        out.append({
            "emoji": emoji_for(label),
            "label": label.capitalize(),
            "time": clock(stamp),
            "date": stamp.strftime("%m/%d/%Y"),
            "iso": stamp.date().isoformat(),
        })
    return out


def task_log(rows, cutoff, icon_for):
    """Corpus task rows -> the app's ``{emoji, task, time, iso}`` rows.

    ``status`` is carried through even though the app never writes it: the
    recommender's avoidance evidence ("3 Reading tasks skipped in the same
    period") is only available when it survives the translation, and an extra
    key costs nothing to a reader of the app's own logs.
    """
    out = []
    for row in rows or []:
        stamp = _parse(row.get("ts"))
        if stamp is None or stamp < cutoff:
            continue
        task = row.get("task")
        if not task:
            continue
        entry = {
            "emoji": icon_for(task),
            "task": task,
            "time": clock(stamp),
            "iso": stamp.date().isoformat(),
        }
        if row.get("status"):
            entry["status"] = row["status"]
        out.append(entry)
    return out


#: Corpus activity names -> a schedule emoji. The corpus stores a single
#: clipboard icon for every task, which would make the schedule unreadable, so
#: the label is matched against the app's own task emoji vocabulary instead.
TASK_EMOJI = (
    ("circle", "\U0001f4cb"), ("reading", "\U0001f4da"), ("snack", "\U0001f34e"),
    ("lunch", "\U0001f37d️"), ("clean", "\U0001f9f9"), ("math", "\U0001f9ee"),
    ("gym", "\U0001f3c3"), ("music", "\U0001f3b5"), ("art", "\U0001f3a8"),
    ("recess", "⚽"), ("speech", "\U0001f5e3️"), ("ot ", "✋"),
    ("sensory", "\U0001f9f8"), ("writing", "✏️"), ("science", "\U0001f52c"),
    ("library", "\U0001f4d6"), ("computer", "\U0001f4bb"), ("bus", "\U0001f68c"),
    ("arrival", "\U0001f44b"), ("dismissal", "\U0001f6aa"), ("assembly", "\U0001f3a4"),
    ("transition", "➡️"),
)


def icon_for_task(name):
    lowered = f" {str(name).lower()} "
    for cue, emoji in TASK_EMOJI:
        if cue in lowered:
            return emoji
    return "⭐"


#: Canonical emotion -> the emoji the app's picker uses for it. Kept in step
#: with the ``EM`` array in ``index.html``; an emotion missing here would show
#: as a blank square in the roster's "last emotion" column.
EMOTION_EMOJI = {
    "happy": "\U0001f60a", "sad": "\U0001f622", "angry": "\U0001f620",
    "scared": "\U0001f628", "tired": "\U0001f634", "okay": "\U0001f610",
    "excited": "\U0001f929", "confused": "\U0001f615", "calm": "\U0001f60c",
    "frustrated": "\U0001f624", "anxious": "\U0001f630",
    "overwhelmed": "\U0001f635‍\U0001f4ab",
}


def emoji_for_emotion(label):
    return EMOTION_EMOJI.get(str(label).strip().lower(), "\U0001f610")


def schedule_tasks(raw):
    """A classroom's ``schedule_json`` -> the app's editable schedule rows.

    The corpus stores only ``{id, label, icon}``. The app's schedule board also
    wants a clock time and a duration, so the day is laid out from 9am in
    twenty-minute steps - a plausible morning rather than an invented one, and
    a teacher can edit it on the Schedule tab like any other.
    """
    out = []
    start = 9 * 60
    for index, task in enumerate(raw or []):
        minutes = start + index * 20
        hour, minute = divmod(minutes, 60)
        suffix = "AM" if hour < 12 else "PM"
        out.append({
            "id": index + 1,
            "emoji": icon_for_task(task.get("label")),
            "label": task.get("label") or f"Task {index + 1}",
            "time": f"{hour % 12 or 12}:{minute:02d} {suffix}",
            "mins": 15,
        })
    return out


def student_record(student, cutoff, schedule):
    """One corpus student -> the record the app stores under ``bs-student-*``.

    Field names are the app's, not the corpus's: this record is written
    straight into the app's own storage and read back by code that has never
    heard of ``students.jsonl``.
    """
    by_label = {task["label"]: task["id"] for task in schedule}
    done = {row.get("id"): row.get("done") for row in student.get("schedule_state") or []}

    # The corpus keys schedule state by its own task ids ("t1"); the app keys it
    # by position. They are in the same order, so zip them rather than trying to
    # match ids that were never meant to line up.
    state = [{"id": task["id"], "done": bool(value)}
             for task, value in zip(schedule, done.values())]

    return {
        "name": student.get("name") or "Student",
        "avatar": student.get("avatar") or "\U0001f98a",
        "stars": student.get("stars") or 0,
        "coins": student.get("coins") or 0,
        "tokens": student.get("tokens") or 0,
        "badges": [],           # corpus badge names are its own, not the app's ids
        "emotionLog": emotion_log(student.get("emotion_log"), cutoff, emoji_for_emotion),
        "taskLog": task_log(student.get("task_log"), cutoff, icon_for_task),
        "schedule": state or [{"id": tid, "done": False} for tid in by_label.values()],
        "aacCount": student.get("aac_count") or 0,
        "emoCount": student.get("emo_count") or 0,
        "sentenceCount": student.get("sentence_count") or 0,
        "storiesRead": student.get("stories_read") or 0,
        "calmDone": student.get("calm_done") or [],
        "streak": student.get("streak") or 0,
        "lastActive": student.get("last_active") or "",
        "lastSeen": student.get("last_seen") or "",
    }


def note_rows(notes, cutoff):
    """Corpus notes -> the app's ``{text, time, date}`` rows, plus ``created_at``.

    The app's own note list shows ``date`` and ``time`` and nothing else, so
    those are what it is given. ``created_at`` rides along because the insights
    engine needs a real timestamp to decide whether a note falls inside the
    window - a locale date string cannot be parsed reliably, and a note wrongly
    read as out-of-window turns into "nothing on file for this period" advice
    that is simply false.
    """
    out = []
    for note in notes:
        stamp = _parse(note.get("created_at"))
        if stamp is None or stamp < cutoff:
            continue
        out.append({
            "text": note.get("text") or "",
            "time": clock(stamp),
            "date": stamp.strftime("%m/%d/%Y"),
            "created_at": stamp.isoformat(),
        })
    out.sort(key=lambda row: row["created_at"])
    return out


def story_rows(stories):
    """Corpus stories -> the app's story shape (``image`` becomes ``emoji``)."""
    return [{
        "id": story.get("id"),
        "title": story.get("title") or "Story",
        "emoji": story.get("emoji") or "\U0001f4d6",
        "pages": [{"emoji": page.get("image") or "\U0001f4d6",
                   "text": page.get("text") or ""}
                  for page in story.get("pages") or []],
        "created": story.get("created_at") or "",
    } for story in stories]


# --------------------------------------------------------------------------
# assembling one room
# --------------------------------------------------------------------------

def build_room(classroom, students, notes, stories, cutoff):
    """Everything the app needs to materialise one sample classroom."""
    schedule = schedule_tasks(classroom.get("schedule_json"))
    roster, records = [], {}

    for student in students:
        student_id = student["id"]
        roster.append({
            "id": student_id,
            "name": student.get("name") or "Student",
            "avatar": student.get("avatar") or "\U0001f98a",
            "joined": student.get("joined_at") or "",
            "lastSeen": student.get("last_seen") or "",
        })
        records[student_id] = student_record(student, cutoff, schedule)

    return {
        "code": classroom["code"],
        "classroom": {
            "code": classroom["code"],
            "name": classroom.get("name") or classroom["code"],
            "teacherName": classroom.get("teacher_name") or "Teacher",
            "tokenReward": classroom.get("token_reward") or "Special Reward!",
            "tokenGoal": classroom.get("token_goal") or 5,
            "nameMode": classroom.get("name_mode") or "full",
            "schoolName": classroom.get("school_name") or "",
            "zip": classroom.get("zip") or "",
            "rosterPick": bool(classroom.get("roster_pick")),
            # Sample rooms are opened through the "sample data" button, which
            # knows this PIN; it is set so that signing out and coming back in
            # through the ordinary Access Dashboard form also works.
            "pin": "0000",
        },
        "schedule": schedule,
        "roster": roster,
        "students": records,
        "notes": {student_id: note_rows(rows, cutoff)
                  for student_id, rows in notes.items()},
        "stories": story_rows(stories),
    }


def summarise(room):
    """One line per room on stdout, so a run can be sanity-checked at a glance."""
    check_ins = sum(len(record["emotionLog"]) for record in room["students"].values())
    tasks = sum(len(record["taskLog"]) for record in room["students"].values())
    notes = sum(len(rows) for rows in room["notes"].values())
    return (f"  {room['code']}  {room['classroom']['name'][:28]:<28} "
            f"{len(room['roster']):>3} students  {check_ins:>5} check-ins  "
            f"{tasks:>5} tasks  {notes:>3} notes  {len(room['stories'])} stories")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rooms", nargs="+", default=list(DEFAULT_ROOMS),
                        help="classroom codes to export")
    parser.add_argument("--data-dir", default=None,
                        help="the corpus directory (default: ../../synthetic_data)")
    parser.add_argument("--out", default=None,
                        help="where to write demo-data.js (default: ../demo-data.js)")
    parser.add_argument("--window-days", type=int, default=WINDOW_DAYS,
                        help=f"how far back to keep logs (default: {WINDOW_DAYS})")
    args = parser.parse_args(argv)

    here = os.path.dirname(os.path.abspath(__file__))
    data_dir = args.data_dir or os.path.join(here, "..", "..", "synthetic_data")
    out_path = args.out or os.path.join(here, "..", "demo-data.js")

    def corpus(name):
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):
            sys.exit(f"not found: {path}\nPass --data-dir to point at the corpus.")
        return path

    wanted = {code.upper() for code in args.rooms}

    classrooms = {room["code"]: room for room in read_jsonl(corpus("classrooms.jsonl"))
                  if room.get("code") in wanted}
    missing = wanted - set(classrooms)
    if missing:
        sys.exit(f"no such classroom(s) in classrooms.jsonl: {', '.join(sorted(missing))}")

    # One pass over the 153 MB corpus, keeping only the rooms asked for.
    by_room = {code: [] for code in wanted}
    for student in read_jsonl(corpus("students.jsonl")):
        if student.get("classroom_code") in wanted:
            for field in DROP_STUDENT_FIELDS:
                student.pop(field, None)
            by_room[student["classroom_code"]].append(student)

    reference = latest_check_in(s for rows in by_room.values() for s in rows)
    if reference is None:
        sys.exit("no readable check-ins in the selected rooms")
    cutoff = reference - timedelta(days=args.window_days)

    notes_by_room = {code: {} for code in wanted}
    for note in read_jsonl(corpus("notes.jsonl")):
        code = note.get("classroom_code")
        if code in wanted:
            notes_by_room[code].setdefault(note.get("student_id"), []).append(note)

    stories_by_room = {code: [] for code in wanted}
    for story in read_jsonl(corpus("stories.jsonl")):
        code = story.get("classroom_code")
        if code in wanted:
            stories_by_room[code].append(story)

    # Ordered as the caller listed them, so the first --rooms argument is the
    # one the app offers first.
    ordered = [code.upper() for code in args.rooms]
    rooms = [build_room(classrooms[code], by_room[code],
                        notes_by_room[code], stories_by_room[code], cutoff)
             for code in ordered if code in classrooms]

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "synthetic_data corpus",
        "reference": reference.isoformat(),
        "window_days": args.window_days,
        "rooms": rooms,
    }

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(
            "/* Sample classrooms for Focus Bridge, generated from the synthetic\n"
            "   corpus by tools/export_demo_rooms.py. Loading one writes these\n"
            "   records into the app's ordinary demo storage; nothing downstream\n"
            "   knows they were not typed in by a class.\n"
            f"   Generated {payload['generated_at']} - do not edit by hand. */\n"
            "window.FB_DEMO_ROOMS = ")
        handle.write(body)
        handle.write(";\n")

    size = os.path.getsize(out_path)
    print(f"wrote {os.path.abspath(out_path)}  ({size / 1024:.0f} KB)")
    print(f"window: {cutoff.date()} to {reference.date()} ({args.window_days} days)")
    for room in rooms:
        print(summarise(room))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
