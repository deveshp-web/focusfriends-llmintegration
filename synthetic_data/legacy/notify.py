"""What changed since the last run - the difference between a report and a feed.

A dashboard that shows the same 2,842 flagged students every morning is read
once. The question a teacher actually has on the second visit is "what is
different from when I last looked", and nothing upstream answers it: the
detector and the recommender each describe a single moment with no memory.

This compares two detector runs and classifies every student's movement between
them. It deliberately reads `insights*.jsonl` rather than `recommendations*.jsonl`
- a notification is about a change in *standing*, not a change in wording, and
recommendation text shifts for reasons (a new note, one more check-in) that are
not news.

Transitions, ranked by how much they deserve a teacher's attention. Rank comes
from PRIORITY_RANK with "not flagged" appended as the best possible state:

  escalated    moved to a worse priority, including into the alert channel
  new          not flagged at all last time
  eased        still flagged, but better than last time
  resolved     was flagged, is not any more
  unchanged    same standing (counted, never listed - it is not news)

Known property of the synthetic corpus, which anyone reading its output needs
before they believe it: **the diff here can only ever improve.** Sliding the
30-day window forward drops flagged students and adds none - measured over four
weekly reference dates on a 1,200-student sample, 910 -> 870 -> 818 -> 751 with
new=0 at every step. The aggregate negative rate is flat (~34%, with a
deliberate ~56% Monday spike), so this is not the data thinning out; the
generator front-loads each student's episodes, so later windows contain fewer
*consecutive* runs. On this corpus "0 newly flagged" is an artefact of how the
data was made, not good news about a classroom, and the dashboard says so
rather than letting a teacher read it as progress.

Because of that, the dashboard leads its alerts panel with within-window
trajectory - who is worse in the second half of their own window than the first
- which does carry movement in both directions here (216 worsening against 229
improving) and needs only one run. Run-over-run change is shown beneath it.

Producing a previous state to compare against is a first-class operation, not a
fixture: run the detector pinned to an earlier date and it emits a genuine
snapshot of what that day would have shown.

    python detect_patterns.py --reference 2026-07-31T13:08:00 --suffix _prev
    python detect_patterns.py
    python recommend.py
    python notify.py

Writes:
  changes.jsonl      one record per classroom, with per-student transitions
  changes_meta.json  run configuration and totals
  digests/<CODE>.txt plain-text per-teacher summary, short enough to be the body
                     of an email or a staff-briefing note
"""

import argparse
import json
import os
import shutil
from collections import Counter
from datetime import datetime

from detect_patterns import DATA_DIR, PRIORITY_RANK, iter_jsonl
from recommend import display_name

# "Not flagged" as a rank one better than every real priority, so a single
# comparison covers arriving, moving and leaving without special cases.
UNFLAGGED_RANK = max(PRIORITY_RANK.values()) + 1
RANK_LABEL = {**{v: k for k, v in PRIORITY_RANK.items()}, UNFLAGGED_RANK: "not flagged"}

# Order the dashboard and the digest both read in.
CHANGE_ORDER = ("escalated", "new", "eased", "resolved")

# How many students a digest names per section before it summarises the rest. A
# digest is meant to be read on a phone between lessons.
DIGEST_NAMES = 8


def rank_of(record):
    return PRIORITY_RANK[record["priority"]] if record else UNFLAGGED_RANK


def classify(prev, now):
    """How this student moved between the two runs."""
    before, after = rank_of(prev), rank_of(now)
    if before == after:
        return "unchanged"
    if after < before:
        return "new" if prev is None else "escalated"
    return "resolved" if now is None else "eased"


def load_run(path):
    if not os.path.exists(path):
        return None
    return {r["student_id"]: r for r in iter_jsonl(path)}


def build_changes(prev_run, now_run, rec_by_id, rooms):
    """Per-classroom change records.

    Every student present in *either* run is considered, so a student who left
    the flagged set entirely is still reported - that is the resolution a
    teacher most wants confirmed, and iterating only the current run would drop
    exactly those.
    """
    by_room = {}
    totals = Counter()

    for sid in set(prev_run) | set(now_run):
        prev, now = prev_run.get(sid), now_run.get(sid)
        change = classify(prev, now)
        totals[change] += 1

        anchor = now or prev
        code = anchor.get("classroom_code")
        entry = {
            "student_id": sid,
            "display_name": display_name(anchor.get("student_name"),
                                         anchor.get("name_mode"), sid),
            "change": change,
            "from": RANK_LABEL[rank_of(prev)],
            "to": RANK_LABEL[rank_of(now)],
            "channel": now["channel"] if now else None,
        }
        rec = rec_by_id.get(sid)
        if rec:
            entry["why"] = rec["why_flagged"]
            if rec["recommendations"]:
                entry["action"] = rec["recommendations"][0]["headline"]
        by_room.setdefault(code, []).append(entry)

    records = []
    for code, students in by_room.items():
        counts = Counter(s["change"] for s in students)
        listed = [s for s in students if s["change"] != "unchanged"]
        # Worst movement first, then alphabetically so the order is stable
        # between runs and a teacher can scan the same list twice.
        listed.sort(key=lambda s: (CHANGE_ORDER.index(s["change"]),
                                   s["display_name"]))
        room = rooms.get(code, {})
        records.append({
            "classroom_code": code,
            "classroom_name": room.get("name") or code,
            "teacher_name": room.get("teacher_name") or "Unassigned",
            "school_name": room.get("school_name") or "",
            "counts": {k: counts.get(k, 0)
                       for k in CHANGE_ORDER + ("unchanged",)},
            "changed_student_count": len(listed),
            "students": listed,
        })

    records.sort(key=lambda r: (-r["counts"]["escalated"], -r["counts"]["new"],
                                r["classroom_code"]))
    return records, totals


def write_digests(directory, records, window):
    """One short plain-text file per classroom.

    Plain text on purpose: it can be pasted into an email, a staff message or a
    handover note without anything having to render it, which is the difference
    between a notification that reaches a teacher and one that waits in a tab.
    """
    # Rebuilt each run so a classroom that no longer has changes does not keep a
    # stale digest around looking current.
    if os.path.isdir(directory):
        shutil.rmtree(directory)
    os.makedirs(directory, exist_ok=True)

    written = 0
    for record in records:
        if not record["changed_student_count"]:
            continue
        lines = [
            f"Focus Bridge - {record['classroom_name']}",
            f"{record['teacher_name']}"
            + (f" - {record['school_name']}" if record["school_name"] else ""),
            f"Changes between {window['prev_end']} and {window['now_end']}",
            "",
        ]
        counts = record["counts"]
        headline = ", ".join(
            f"{counts[k]} {k}" for k in CHANGE_ORDER if counts[k]) or "no changes"
        lines.append(headline.capitalize() + ".")
        lines.append(f"{counts['unchanged']} students unchanged.")
        lines.append("")

        for change in CHANGE_ORDER:
            group = [s for s in record["students"] if s["change"] == change]
            if not group:
                continue
            lines.append(f"{change.upper()} ({len(group)})")
            for student in group[:DIGEST_NAMES]:
                lines.append(f"  {student['display_name']}: "
                             f"{student['from']} -> {student['to']}")
                if student.get("why"):
                    lines.append(f"      {student['why']}")
                if student.get("action"):
                    lines.append(f"      Suggested: {student['action']}")
            if len(group) > DIGEST_NAMES:
                lines.append(f"  ...and {len(group) - DIGEST_NAMES} more")
            lines.append("")

        lines.append("Every line above is a rule with a stated trigger. Open the "
                     "dashboard for the evidence behind each one.")
        path = os.path.join(directory, f"{record['classroom_code']}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        written += 1
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--previous", default="_prev", metavar="SUFFIX",
                        help="suffix of the earlier detector run (default: _prev)")
    args = parser.parse_args()

    now_path = os.path.join(DATA_DIR, "insights.jsonl")
    prev_path = os.path.join(DATA_DIR, f"insights{args.previous}.jsonl")
    now_meta_path = os.path.join(DATA_DIR, "insights_meta.json")
    prev_meta_path = os.path.join(DATA_DIR, f"insights_meta{args.previous}.json")

    now_run = load_run(now_path)
    if now_run is None:
        raise SystemExit("insights.jsonl not found - run detect_patterns.py first")
    prev_run = load_run(prev_path)
    if prev_run is None:
        raise SystemExit(
            f"{os.path.basename(prev_path)} not found. Create a previous state "
            f"with:\n  python detect_patterns.py --reference <ISO> "
            f"--suffix {args.previous}")

    with open(now_meta_path, encoding="utf-8") as f:
        now_meta = json.load(f)
    with open(prev_meta_path, encoding="utf-8") as f:
        prev_meta = json.load(f)
    window = {"prev_end": prev_meta["reference_time"][:10],
              "now_end": now_meta["reference_time"][:10]}
    if prev_meta["reference_time"] >= now_meta["reference_time"]:
        print(f"  WARNING previous run ({window['prev_end']}) is not earlier than "
              f"the current one ({window['now_end']}) - the diff will read backwards")

    rooms = {r.get("code"): r for r in iter_jsonl(
        os.path.join(DATA_DIR, "classrooms.jsonl"))}
    rec_path = os.path.join(DATA_DIR, "recommendations.jsonl")
    rec_by_id = {r["student_id"]: r for r in iter_jsonl(rec_path)} \
        if os.path.exists(rec_path) else {}

    records, totals = build_changes(prev_run, now_run, rec_by_id, rooms)
    changed_rooms = sum(1 for r in records if r["changed_student_count"])

    out_path = os.path.join(DATA_DIR, "changes.jsonl")
    with open(out_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")

    digest_dir = os.path.join(DATA_DIR, "digests")
    written = write_digests(digest_dir, records, window)

    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window": window,
        "previous_suffix": args.previous,
        "previous_flagged": len(prev_run),
        "current_flagged": len(now_run),
        "totals": dict(totals),
        "classrooms": len(records),
        "classrooms_with_changes": changed_rooms,
    }
    with open(os.path.join(DATA_DIR, "changes_meta.json"), "w",
              encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"comparing {window['prev_end']} -> {window['now_end']}")
    print(f"  {len(prev_run)} flagged before, {len(now_run)} now")
    for change in CHANGE_ORDER:
        print(f"    {change}: {totals.get(change, 0)}")
    print(f"    unchanged: {totals.get('unchanged', 0)}")
    print(f"{changed_rooms} of {len(records)} classrooms have changes -> {out_path}")
    print(f"{written} teacher digests -> {digest_dir}")


if __name__ == "__main__":
    main()
