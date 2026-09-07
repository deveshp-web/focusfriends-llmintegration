#!/usr/bin/env python3
"""
Teacher response loop: mark flagged students as reviewed / dismissed /
acted_on, so a future run of this tool (or the viewer) can tell which
flags a teacher has already looked at instead of showing the same list
forever.

Nothing here changes insights.jsonl. Review status lives in its own file,
flag_reviews.jsonl (see flag_review_store.py), keyed by each record's
flag_id (added by detect_patterns.py). Detection and review are kept as
two separate concerns on purpose: re-running the detector never erases
review history, and reviewing a flag never touches detection output.

Usage:
    python3 review_flags.py --list
    python3 review_flags.py --list --channel all
    python3 review_flags.py --review <flag_id> --action dismissed --note "..." --by "ms_nguyen"
    python3 review_flags.py                     # interactive walkthrough
"""

import argparse
import json

from flag_review_store import append_review, load_reviews

INSIGHTS_PATH = "insights.jsonl"
PRIORITY_RANK = {"high": 0, "medium": 1, "watch": 2, "info": 3}


def load_insights(path):
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def describe(record):
    s = record["summary"]
    return (f"{record['student_name']} ({record['classroom_name']}) - "
            f"{record['channel'].upper()}/{record['priority']} - "
            f"{s['alert_streak_count']} alert, {s['watch_streak_count']} watch, "
            f"{s['positive_streak_count']} positive streak(s)")


def unreviewed(records, reviews, channel):
    out = [r for r in records if r["flag_id"] not in reviews]
    if channel != "all":
        out = [r for r in out if r["channel"] == channel]
    out.sort(key=lambda r: PRIORITY_RANK.get(r["priority"], 9))
    return out


def cmd_list(args):
    records = load_insights(args.insights_file)
    reviews = load_reviews()
    pending = unreviewed(records, reviews, args.channel)
    print(f"{len(pending)} unreviewed flag(s) (channel={args.channel}, "
          f"{len(reviews)} already reviewed)\n")
    for r in pending:
        print(f"[{r['flag_id']}] {describe(r)}")


def cmd_review(args):
    review = append_review(args.flag_id, args.student_id or "", args.classroom_code or "",
                           args.action, note=args.note or "", reviewed_by=args.by or "")
    print(f"recorded: {review}")


def cmd_interactive(args):
    records = load_insights(args.insights_file)
    reviews = load_reviews()
    pending = unreviewed(records, reviews, args.channel)
    if not pending:
        print("nothing to review")
        return
    print(f"{len(pending)} flag(s) to review. For each: "
          f"[r]eviewed  [d]ismissed  [a]cted on  [s]kip  [q]uit\n")
    for record in pending:
        print(describe(record))
        for streak in record["streaks"]:
            print(f"    {streak['type']} ({streak['channel']}): "
                  f"{streak['length']} entries {streak['emotions']}")
        choice = input("action> ").strip().lower()
        if choice == "q":
            break
        if choice == "s" or choice == "":
            continue
        action = {"r": "reviewed", "d": "dismissed", "a": "acted_on"}.get(choice)
        if action is None:
            print("  not understood, skipping\n")
            continue
        note = input("  note (optional)> ").strip()
        append_review(record["flag_id"], record["student_id"], record["classroom_code"],
                     action, note=note)
        print("  recorded\n")


def main():
    ap = argparse.ArgumentParser(description="Mark flagged students as reviewed/dismissed.")
    ap.add_argument("--insights-file", default=INSIGHTS_PATH)
    ap.add_argument("--channel", default="alert", choices=["alert", "watch", "positive", "all"])
    ap.add_argument("--list", action="store_true", help="print unreviewed flags and exit")
    ap.add_argument("--review", dest="flag_id", help="flag_id to record a review for")
    ap.add_argument("--action", choices=["reviewed", "dismissed", "acted_on"])
    ap.add_argument("--student-id")
    ap.add_argument("--classroom-code")
    ap.add_argument("--note")
    ap.add_argument("--by", help="who reviewed it, e.g. a teacher id/name")
    args = ap.parse_args()

    if args.flag_id:
        if not args.action:
            ap.error("--review requires --action")
        cmd_review(args)
    elif args.list:
        cmd_list(args)
    else:
        cmd_interactive(args)


if __name__ == "__main__":
    main()
