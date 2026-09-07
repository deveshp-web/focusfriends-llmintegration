"""Wiring for stage 3: compare two detector runs and write the change feed."""

from __future__ import annotations

from datetime import datetime

from ..core import cli
from ..core.jsonio import index_by, read_json, write_json, write_jsonl
from .diff import CHANGE_ORDER, build_changes
from .digest import write_digests


def run(current_paths, previous_paths, report=print):
    """Diff the two runs, write ``changes.jsonl``, the digests and the metadata.

    Args:
        current_paths:  paths for the run being reported on.
        previous_paths: paths for the earlier snapshot to compare against - the
                        same directory under a different suffix.
    """
    cli.require(current_paths.insights, "run detect_patterns.py first")
    if not previous_paths.insights.exists():
        raise cli.fail(
            f"{previous_paths.insights.name} not found. Create a previous state "
            f"with:\n  python detect_patterns.py --reference <ISO> "
            f"--suffix {previous_paths.suffix}")

    current_run = index_by(current_paths.insights, "student_id")
    previous_run = index_by(previous_paths.insights, "student_id")

    current_meta = read_json(current_paths.insights_meta)
    previous_meta = read_json(previous_paths.insights_meta)
    window = {"prev_end": previous_meta["reference_time"][:10],
              "now_end": current_meta["reference_time"][:10]}

    # A backwards comparison still produces a valid file, so this warns rather
    # than refusing - but silently reporting "12 escalated" when the runs are
    # the wrong way round would be actively misleading.
    if previous_meta["reference_time"] >= current_meta["reference_time"]:
        report(f"  WARNING previous run ({window['prev_end']}) is not earlier than "
               f"the current one ({window['now_end']}) - the diff will read backwards")

    rooms = index_by(current_paths.classrooms, "code")
    # Recommendations are optional: the feed is more useful with them, and still
    # correct without them, so a missing file degrades rather than fails.
    recommendations = index_by(current_paths.recommendations, "student_id",
                               skip_missing=True)

    records, totals = build_changes(previous_run, current_run, recommendations, rooms)
    write_jsonl(current_paths.changes, records)
    written = write_digests(current_paths.digests, records, window)

    changed_rooms = sum(1 for record in records if record["changed_student_count"])
    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window": window,
        "previous_suffix": previous_paths.suffix,
        "previous_flagged": len(previous_run),
        "current_flagged": len(current_run),
        "totals": dict(totals),
        "classrooms": len(records),
        "classrooms_with_changes": changed_rooms,
    }
    write_json(current_paths.changes_meta, meta)

    report(f"comparing {window['prev_end']} -> {window['now_end']}")
    report(f"  {len(previous_run)} flagged before, {len(current_run)} now")
    for change in CHANGE_ORDER:
        report(f"    {change}: {totals.get(change, 0)}")
    report(f"    unchanged: {totals.get('unchanged', 0)}")
    report(f"{changed_rooms} of {len(records)} classrooms have changes "
           f"-> {current_paths.changes}")
    report(f"{written} teacher digests -> {current_paths.digests}")
    return meta


@cli.friendly_data_errors
def main(argv=None):
    """Entry point for ``python notify.py``."""
    parser = cli.build_parser(
        "Compare two detector runs and report what changed.")
    parser.add_argument(
        "--previous", default="_prev", metavar="SUFFIX",
        help="suffix of the earlier detector run (default: _prev)")
    cli.add_data_dir_argument(parser)
    args = parser.parse_args(argv)

    # Both views point at the same directory; only the suffix differs. That is
    # exactly the case DataPaths.with_suffix exists for.
    current = cli.paths_from_args(args)
    run(current, current.with_suffix(args.previous))


if __name__ == "__main__":
    main()
