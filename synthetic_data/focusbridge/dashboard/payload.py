"""Compressing the pipeline's output into the blob the page reads.

WHY THE KEYS ARE ONE AND TWO LETTERS LONG
-----------------------------------------
This is the one module in the project where terse names are correct, and it is
worth being explicit about why, because it looks like exactly the kind of thing
the rest of this codebase argues against.

The payload is embedded in the HTML file itself. Every key name is repeated once
per student - 2,842 times - so ``"display_name"`` costs about 39 KB across the
file where ``"n"`` costs about 8 KB. Summed over every field, the *key names
alone* were a large fraction of the payload.

The mapping is therefore treated as a wire format rather than as code, and the
tables below are its documentation. The page's JavaScript reads exactly these
keys, so **changing one here means changing it in the template too.**

    STUDENT                       ROOM
    n   display name             c   classroom code
    p   priority                 n   classroom name
    ch  channel                  t   teacher name
    w   why flagged              s   school name
    tr  trajectory direction     k   the counts block
    td  trajectory delta         rr  room recommendations
    fh  first-half rate          tk  top activities, by student count
    sh  second-half rate         st  student order
    nc  note count               cg  what changed since the last run
    em  check-in strip, emotions
    ed  check-in strip, dates
    mx  emotion mix
    rc  recommendations
    tc  activity clusters
    tm  time cluster
    ns  recent notes
    cg  what changed since the last run
"""

from __future__ import annotations

from collections import Counter

from ..core.vocabulary import EMOTION_INDEX, EMOTION_ORDER, EMOTION_VALENCE

#: Recent check-ins drawn per student. Enough to show the shape of a month
#: without turning the row into a hairline.
STRIP_LENGTH = 28

#: How many activities to chart per room, and how many changed students to list.
ROOM_TOP_TASKS = 6
ROOM_CHANGE_LIMIT = 40

#: How many recent notes and activity clusters to carry per student.
STUDENT_NOTE_LIMIT = 3
STUDENT_TASK_LIMIT = 2


def encode_strip(strip):
    """Pack a student's recent check-ins into two flat strings.

    Returns ``(emotion characters, packed timestamps)``:

      * each check-in becomes **one character** - ``'a'`` for the first emotion
        in :data:`EMOTION_ORDER`, ``'b'`` for the second, ``'.'`` for one that
        could not be read;
      * each timestamp becomes **eight digits**, ``MMDDhhmm``.

    Two flat strings rather than a list of objects because, at 2,842 students,
    the object form was most of the payload: one
    ``{"ts":"2026-08-03T09:12:00","emotion":"frustrated"},`` entry is 52 bytes
    against this format's 9, for exactly the same information - and there are up
    to 28 of them per student.

    The emotion's index is a dict lookup rather than ``EMOTION_ORDER.index()``,
    which scanned the list from the start on every check-in - O(1) instead of
    O(number of emotions), for free.
    """
    characters, stamps = [], []
    for entry in strip[-STRIP_LENGTH:]:
        position = EMOTION_INDEX.get(entry.get("emotion"), -1)
        # chr(97) is 'a'. An unreadable check-in becomes '.', which the page
        # renders as a gap rather than as a neutral reading.
        characters.append("." if position < 0 else chr(97 + position))

        timestamp = entry.get("ts") or ""
        # Slice out MM, DD, hh, mm from "YYYY-MM-DDThh:mm:ss" without parsing:
        # the string is already in a fixed format and this runs once per
        # check-in per student. `ljust` covers a truncated timestamp.
        stamps.append((timestamp[5:7] + timestamp[8:10]
                       + timestamp[11:13] + timestamp[14:16]).ljust(8, "0"))

    return "".join(characters), "".join(stamps)


def compact_recommendation(item):
    """One recommendation, trimmed to what the page draws."""
    return {"h": item["headline"], "d": item["detail"],
            "e": item["evidence"], "c": item["category"]}


def _student_entry(record, change):
    """One student's payload block."""
    emotions, dates = encode_strip(record.get("recent_strip") or [])
    entry = {
        "n": record["display_name"],
        "p": record["priority"],
        "ch": record["channel"],
        "w": record["why_flagged"],
        "tr": record["trajectory"]["direction"],
        "td": record["trajectory"].get("delta", 0),
        "fh": record["trajectory"].get("first_half", 0),
        "sh": record["trajectory"].get("second_half", 0),
        "nc": record["note_count"],
        "em": emotions,
        "ed": dates,
        "mx": record["emotion_mix"],
        "rc": [compact_recommendation(item) for item in record["recommendations"]],
    }

    # The remaining fields are omitted entirely when empty rather than written
    # as null. Across thousands of students, absent keys are meaningfully
    # smaller than present-but-empty ones, and the page already has to handle
    # "this student has no activity cluster".
    if record["task_clusters"]:
        entry["tc"] = [{"t": cluster["task"], "h": cluster["hits"],
                        "x": cluster["exposure"], "r": cluster["rate"],
                        "u": cluster["usual_rate"], "s": cluster["skipped"]}
                       for cluster in record["task_clusters"][:STUDENT_TASK_LIMIT]]
    if record["time_cluster"]:
        entry["tm"] = {"p": record["time_cluster"]["phrase"],
                       "r": record["time_cluster"]["rate"],
                       "u": record["time_cluster"]["usual_rate"]}
    if record["recent_notes"]:
        entry["ns"] = [{"t": note["ts"][:10], "x": note["text"]}
                       for note in record["recent_notes"][:STUDENT_NOTE_LIMIT]]
    if change:
        entry["cg"] = {"k": change["change"], "f": change["from"],
                       "t": change["to"]}
    return entry


def _room_task_counts(room, students):
    """Activities in this room, counted by how many students each one touches.

    Recomputed here rather than reused from ``classroom_actions.jsonl`` because
    the chart asks a different question from the room *rule*: the rule asks "is
    this a room-wide problem worth acting on", and applies thresholds; the chart
    just shows the shape of the room, including activities below those bars.
    """
    per_task = Counter()
    for student_id in room["student_order"]:
        record = students.get(student_id)
        if record:
            # A set first, so one student with two clusters on the same activity
            # still counts once.
            for task in {cluster["task"] for cluster in record["task_clusters"]}:
                per_task[task] += 1
    return per_task.most_common(ROOM_TOP_TASKS)


def _room_entry(room, students, change):
    """One classroom's payload block."""
    entry = {
        "c": room["classroom_code"],
        "n": room["classroom_name"] or room["classroom_code"],
        "t": room["teacher_name"] or "Unassigned",
        "s": room["school_name"] or "",
        "k": {
            "flagged": room["flagged_student_count"],
            "alert": room["alert_student_count"],
            "watch": room["watch_student_count"],
            "positive": room["positive_student_count"],
            "high": room["high_priority_count"],
            "undoc": room["undocumented_alert_count"],
        },
        "rr": [compact_recommendation(item)
               for item in room["room_recommendations"]],
        "tk": _room_task_counts(room, students),
        "st": room["student_order"],
    }
    if change and change["changed_student_count"]:
        entry["cg"] = {
            "n": change["counts"],
            "s": [{"i": student["student_id"], "n": student["display_name"],
                   "k": student["change"], "f": student["from"],
                   "t": student["to"]}
                  for student in change["students"][:ROOM_CHANGE_LIMIT]],
        }
    return entry


def build_payload(students, rooms, detector_meta, recommender_meta,
                  changes=None, changes_meta=None, only=None):
    """Everything the page renders, keyed and trimmed for size.

    Args:
        students: ``student id -> recommendation record``.
        rooms:    the classroom action records.
        only:     restrict to these classroom codes, or ``None`` for all of them.
                  Filtering here rather than in the page is what makes a
                  single-room file ~50 KB instead of ~4.7 MB.
    """
    if only:
        rooms = [room for room in rooms if room["classroom_code"] in only]
        # Keep only the students those rooms actually reference - otherwise a
        # single-room file would still carry all 2,842 students' data.
        kept = {student_id for room in rooms for student_id in room["student_order"]}
        students = {key: value for key, value in students.items() if key in kept}

    # Indexed once so the per-student and per-room loops below are O(1) lookups
    # rather than scans of the change feed.
    change_by_room = {record["classroom_code"]: record for record in (changes or [])}
    change_by_student = {student["student_id"]: student
                         for record in (changes or [])
                         for student in record["students"]}

    payload_students = {
        student_id: _student_entry(record, change_by_student.get(student_id))
        for student_id, record in students.items()
    }

    payload_rooms = [
        _room_entry(room, students, change_by_room.get(room["classroom_code"]))
        for room in rooms
    ]
    # Worst room first, so the picker opens on the room that most needs reading.
    payload_rooms.sort(key=lambda room: (-room["k"]["high"], -room["k"]["alert"],
                                         room["c"]))

    return {
        "meta": _meta_block(detector_meta, recommender_meta, changes_meta),
        "emotions": [{"n": emotion, "v": EMOTION_VALENCE[emotion]}
                     for emotion in EMOTION_ORDER],
        "rooms": payload_rooms,
        "students": payload_students,
    }


def _meta_block(detector_meta, recommender_meta, changes_meta):
    """The run provenance the page shows in its footer.

    Present so that a teacher looking at a flag can always answer "how old is
    this, and what settings produced it?" without leaving the page.
    """
    return {
        "window_start": detector_meta["cutoff"][:10],
        "window_end": detector_meta["reference_time"][:10],
        "detector_run": detector_meta["generated_at"],
        "advice_run": recommender_meta["generated_at"],
        "streak_length": detector_meta["config"]["streak_length"],
        "recency_days": detector_meta["config"]["recency_days"],
        "students_scanned": detector_meta["totals"]["students_scanned"],
        "students_flagged": detector_meta["totals"]["students_flagged"],
        "task_window": recommender_meta["config"]["task_window_minutes"],
        "changes": ({"prev": changes_meta["window"]["prev_end"],
                     "now": changes_meta["window"]["now_end"],
                     "totals": changes_meta["totals"]}
                    if changes_meta else None),
    }
