"""The terminal interface for reviewing flags.

Three ways in, all writing through :mod:`focusbridge.review.store`:

    python review_flags.py --list
    python review_flags.py --list --channel all
    python review_flags.py --review <flag_id> --action dismissed --note "..." --by "ms_nguyen"
    python review_flags.py                     # interactive walkthrough

The interactive mode is the one that actually gets used: it prints one flag,
takes a single keystroke, and moves on. Anything slower than that does not get
done between lessons.
"""

from __future__ import annotations

from ..core import cli as shared_cli
from ..core.jsonio import read_jsonl_list
from .store import append_review, describe, load_reviews, unreviewed

#: What each keystroke means in the interactive walkthrough.
KEY_ACTIONS = {"r": "reviewed", "d": "dismissed", "a": "acted_on"}


def command_list(paths, channel):
    """Print every flag nobody has responded to yet, and stop."""
    records = read_jsonl_list(paths.insights)
    reviews = load_reviews(paths.flag_reviews)
    pending = unreviewed(records, reviews, channel)

    print(f"{len(pending)} unreviewed flag(s) (channel={channel}, "
          f"{len(reviews)} already reviewed)\n")
    for record in pending:
        print(f"[{record['flag_id']}] {describe(record)}")


def command_record(paths, args):
    """Record a single review passed entirely on the command line.

    This is the scriptable path - it is what a bulk import or another tool would
    call, so it takes every field as an argument and asks nothing.
    """
    review = append_review(args.flag_id, args.student_id or "",
                           args.classroom_code or "", args.action,
                           note=args.note or "", reviewed_by=args.by or "",
                           path=paths.flag_reviews)
    print(f"recorded: {review}")


def command_walkthrough(paths, channel):
    """Step through the pending flags one at a time."""
    records = read_jsonl_list(paths.insights)
    reviews = load_reviews(paths.flag_reviews)
    pending = unreviewed(records, reviews, channel)

    if not pending:
        print("nothing to review")
        return

    print(f"{len(pending)} flag(s) to review. For each: "
          f"[r]eviewed  [d]ismissed  [a]cted on  [s]kip  [q]uit\n")

    for record in pending:
        print(describe(record))
        # The findings are printed in full before the prompt: a teacher is being
        # asked to agree or disagree with a rule, and cannot do that from a
        # summary line alone.
        for streak in record["streaks"]:
            print(f"    {streak['type']} ({streak['channel']}): "
                  f"{streak['length']} entries {streak['emotions']}")

        choice = input("action> ").strip().lower()
        if choice == "q":
            break
        if choice in ("", "s"):
            continue

        action = KEY_ACTIONS.get(choice)
        if action is None:
            # Skip rather than re-prompt. A mistyped key should not trap someone
            # in a loop, and the flag simply stays pending for the next run.
            print("  not understood, skipping\n")
            continue

        note = input("  note (optional)> ").strip()
        append_review(record["flag_id"], record["student_id"],
                      record["classroom_code"], action, note=note,
                      path=paths.flag_reviews)
        print("  recorded\n")


@shared_cli.friendly_data_errors
def main(argv=None):
    """Entry point for ``python review_flags.py``."""
    parser = shared_cli.build_parser(
        "Mark flagged students as reviewed, dismissed or acted on.")
    parser.add_argument("--channel", default="alert",
                        choices=["alert", "watch", "positive", "all"],
                        help="which channel to work through (default: alert)")
    parser.add_argument("--list", action="store_true",
                        help="print unreviewed flags and exit")
    parser.add_argument("--review", dest="flag_id",
                        help="flag_id to record a review for")
    parser.add_argument("--action", choices=["reviewed", "dismissed", "acted_on"])
    parser.add_argument("--student-id")
    parser.add_argument("--classroom-code")
    parser.add_argument("--note")
    parser.add_argument("--by", help="who reviewed it, e.g. a teacher id or name")
    shared_cli.add_data_dir_argument(parser)
    args = parser.parse_args(argv)

    paths = shared_cli.paths_from_args(args)
    shared_cli.require(paths.insights, "run detect_patterns.py first")

    if args.flag_id:
        if not args.action:
            parser.error("--review requires --action")
        command_record(paths, args)
    elif args.list:
        command_list(paths, args.channel)
    else:
        command_walkthrough(paths, args.channel)


if __name__ == "__main__":
    main()
