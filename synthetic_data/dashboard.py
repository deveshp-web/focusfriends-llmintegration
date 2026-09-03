"""Render the teacher dashboard from the detector and recommendation output.

Writes one self-contained HTML file - no server, no network, no build step, so
it opens from a USB stick in a classroom that has no wifi. Everything the page
needs is embedded in it.

The page is scoped to one classroom at a time, because that is the unit a
teacher owns. A room picker switches between them; only the selected room is
rendered, so a 220-room file stays responsive.

Reading order inside a room follows the channel split the detector already
established, since the whole point of that split is that not everything
deserves equal attention:

  1. room-wide recommendations   things to change once, not per student
  2. the ALERT queue             the students a teacher is expected to read
  3. WATCH                       context, collapsed by default
  4. POSITIVE                    good news, collapsed by default

Colour follows the data-viz reference palette. Priority uses the fixed status
palette and always ships an icon and a word alongside the colour, so nothing is
carried by hue alone. The check-in strip is a diverging encoding - positive and
negative are the poles, neutral is the midpoint and is meant to recede, because
an "okay" check-in is the absence of signal.

Usage:
    python detect_patterns.py
    python recommend.py
    python dashboard.py                      # every classroom
    python dashboard.py --classroom WAJHZX   # one room, much smaller file
    python dashboard.py --out teacher.html
"""

import argparse
import json
import os
from collections import Counter

from detect_patterns import DATA_DIR, EMOTION_VALENCE, PRIORITY_RANK, iter_jsonl

# Stable emotion order. The index is what the check-in strip is encoded with, so
# it has to stay stable within a file - it is written into the payload and read
# back by the page.
EMOTION_ORDER = list(EMOTION_VALENCE)

# Recent check-ins drawn per student. Enough to show the shape of a month
# without making the row a hairline.
STRIP_LENGTH = 28

CHANNEL_ORDER = ("alert", "watch", "positive")


def encode_strip(strip):
    """(emotion chars, MMDDhhmm timestamps) for the check-in strip.

    Packed into two flat strings rather than a list of objects: at 2,842
    students the object form was most of the payload, and this carries the same
    information in about a tenth of the bytes.
    """
    chars, stamps = [], []
    for entry in strip[-STRIP_LENGTH:]:
        emotion = entry.get("emotion")
        index = EMOTION_ORDER.index(emotion) if emotion in EMOTION_VALENCE else -1
        chars.append("." if index < 0 else chr(97 + index))
        ts = entry.get("ts") or ""
        stamps.append((ts[5:7] + ts[8:10] + ts[11:13] + ts[14:16]).ljust(8, "0"))
    return "".join(chars), "".join(stamps)


def compact_rec(item):
    return {"h": item["headline"], "d": item["detail"],
            "e": item["evidence"], "c": item["category"]}


def build_payload(students, rooms, detector_meta, rec_meta, changes=None,
                  changes_meta=None, only=None):
    """Everything the page renders, keyed and trimmed for size."""
    if only:
        rooms = [r for r in rooms if r["classroom_code"] in only]
        keep = {sid for r in rooms for sid in r["student_order"]}
        students = {k: v for k, v in students.items() if k in keep}

    change_by_room = {c["classroom_code"]: c for c in (changes or [])}
    change_by_student = {st["student_id"]: st
                         for c in (changes or []) for st in c["students"]}

    payload_students = {}
    for sid, record in students.items():
        chars, stamps = encode_strip(record.get("recent_strip") or [])
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
            "em": chars,
            "ed": stamps,
            "mx": record["emotion_mix"],
            "rc": [compact_rec(r) for r in record["recommendations"]],
        }
        if record["task_clusters"]:
            entry["tc"] = [{"t": t["task"], "h": t["hits"], "x": t["exposure"],
                            "r": t["rate"], "u": t["usual_rate"],
                            "s": t["skipped"]}
                           for t in record["task_clusters"][:2]]
        if record["time_cluster"]:
            entry["tm"] = {"p": record["time_cluster"]["phrase"],
                           "r": record["time_cluster"]["rate"],
                           "u": record["time_cluster"]["usual_rate"]}
        if record["recent_notes"]:
            entry["ns"] = [{"t": n["ts"][:10], "x": n["text"]}
                           for n in record["recent_notes"][:3]]
        moved = change_by_student.get(sid)
        if moved:
            entry["cg"] = {"k": moved["change"], "f": moved["from"],
                           "t": moved["to"]}
        payload_students[sid] = entry

    payload_rooms = []
    for room in rooms:
        # Room-level activity clusters, recomputed here because the chart wants
        # them ordered by how many students each activity touches - which is a
        # different question from any one student's ranking.
        task_students = Counter()
        for sid in room["student_order"]:
            record = students.get(sid)
            if record:
                for task in {t["task"] for t in record["task_clusters"]}:
                    task_students[task] += 1
        payload_rooms.append({
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
            "rr": [compact_rec(r) for r in room["room_recommendations"]],
            "tk": task_students.most_common(6),
            "st": room["student_order"],
        })
        moved = change_by_room.get(room["classroom_code"])
        if moved and moved["changed_student_count"]:
            payload_rooms[-1]["cg"] = {
                "n": moved["counts"],
                "s": [{"i": st["student_id"], "n": st["display_name"],
                       "k": st["change"], "f": st["from"], "t": st["to"]}
                      for st in moved["students"][:40]],
            }

    payload_rooms.sort(key=lambda r: (-r["k"]["high"], -r["k"]["alert"], r["c"]))
    return {
        "meta": {
            "window_start": detector_meta["cutoff"][:10],
            "window_end": detector_meta["reference_time"][:10],
            "detector_run": detector_meta["generated_at"],
            "advice_run": rec_meta["generated_at"],
            "streak_length": detector_meta["config"]["streak_length"],
            "recency_days": detector_meta["config"]["recency_days"],
            "students_scanned": detector_meta["totals"]["students_scanned"],
            "students_flagged": detector_meta["totals"]["students_flagged"],
            "task_window": rec_meta["config"]["task_window_minutes"],
            "changes": ({"prev": changes_meta["window"]["prev_end"],
                         "now": changes_meta["window"]["now_end"],
                         "totals": changes_meta["totals"]}
                        if changes_meta else None),
        },
        "emotions": [{"n": e, "v": EMOTION_VALENCE[e]} for e in EMOTION_ORDER],
        "rooms": payload_rooms,
        "students": payload_students,
    }


def render(payload, template_path):
    with open(template_path, encoding="utf-8") as f:
        template = f.read()
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    # A literal "</script>" anywhere in the data would close the tag early.
    blob = blob.replace("</", "<\\/")
    return template.replace("/*__PAYLOAD__*/null", blob)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--classroom", action="append", metavar="CODE",
                        help="only this classroom (repeatable)")
    parser.add_argument("--teacher", metavar="NAME",
                        help="only classrooms whose teacher name contains NAME")
    parser.add_argument("--out", default=os.path.join(DATA_DIR, "dashboard.html"))
    args = parser.parse_args()

    rec_path = os.path.join(DATA_DIR, "recommendations.jsonl")
    rooms_path = os.path.join(DATA_DIR, "classroom_actions.jsonl")
    if not os.path.exists(rec_path):
        raise SystemExit("recommendations.jsonl not found - run recommend.py first")

    students = {r["student_id"]: r for r in iter_jsonl(rec_path)}
    rooms = list(iter_jsonl(rooms_path))
    with open(os.path.join(DATA_DIR, "insights_meta.json"), encoding="utf-8") as f:
        detector_meta = json.load(f)
    with open(os.path.join(DATA_DIR, "recommendations_meta.json"), encoding="utf-8") as f:
        rec_meta = json.load(f)

    only = set(args.classroom or [])
    if args.teacher:
        needle = args.teacher.lower()
        only |= {r["classroom_code"] for r in rooms
                 if needle in (r["teacher_name"] or "").lower()}
        if not only:
            raise SystemExit(f"no classroom matches teacher {args.teacher!r}")

    changes_path = os.path.join(DATA_DIR, "changes.jsonl")
    changes_meta_path = os.path.join(DATA_DIR, "changes_meta.json")
    changes = list(iter_jsonl(changes_path)) if os.path.exists(changes_path) else None
    changes_meta = None
    if os.path.exists(changes_meta_path):
        with open(changes_meta_path, encoding="utf-8") as f:
            changes_meta = json.load(f)
    if changes is None:
        print("  no changes.jsonl - run notify.py to add the 'since last check' "
              "panel")

    payload = build_payload(students, rooms, detector_meta, rec_meta,
                            changes, changes_meta, only or None)
    html = render(payload, os.path.join(DATA_DIR, "dashboard_template.html"))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    size = os.path.getsize(args.out) / 1024
    print(f"{len(payload['rooms'])} classrooms, "
          f"{len(payload['students'])} students -> {args.out}")
    print(f"  {size:,.0f} KB, self-contained")
    if only:
        for room in payload["rooms"]:
            print(f"  {room['c']}  {room['n']} - {room['t']} "
                  f"({room['k']['flagged']} flagged, {room['k']['high']} high)")


if __name__ == "__main__":
    main()
