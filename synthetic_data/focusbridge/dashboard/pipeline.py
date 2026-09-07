"""Wiring for stage 4: read the pipeline's output and write one HTML file."""

from __future__ import annotations

from ..core import cli
from ..core.jsonio import (file_size_kb, index_by, read_json,
                           read_jsonl_list, write_text)
from .payload import build_payload
from .render import render


def select_classrooms(rooms, codes=None, teacher=None):
    """Which classroom codes to render, from the two filter options.

    Returns ``None`` for "all of them", which is what
    :func:`build_payload` expects when no filter applies. An empty set would
    mean "render nothing", so the two cases must stay distinguishable.
    """
    selected = set(codes or ())

    if teacher:
        needle = teacher.lower()
        # Substring match, case-insensitive: a teacher typing "ahmed" should not
        # have to know whether the file says "Jennifer Ahmed" or "AHMED, J".
        matched = {room["classroom_code"] for room in rooms
                   if needle in (room["teacher_name"] or "").lower()}
        if not matched:
            # A typo that silently rendered all 220 rooms would look like the
            # filter had been ignored, so this fails loudly instead.
            raise cli.fail(f"no classroom matches teacher {teacher!r}")
        selected |= matched

    return selected or None


def run(paths, output_path=None, codes=None, teacher=None, report=print):
    """Build the dashboard and write it. Returns the path written."""
    cli.require(paths.recommendations, "run recommend.py first")

    students = index_by(paths.recommendations, "student_id")
    rooms = read_jsonl_list(paths.classroom_actions)
    detector_meta = read_json(paths.insights_meta)
    recommender_meta = read_json(paths.recommendations_meta)

    # The change feed is optional - the dashboard is complete without it, just
    # missing its "since you last looked" panel. Optional inputs degrade; the
    # required ones above fail with an instruction.
    changes = read_jsonl_list(paths.changes, skip_missing=True) or None
    changes_meta = read_json(paths.changes_meta) if paths.changes_meta.exists() else None
    if changes is None:
        report("  no changes.jsonl - run notify.py to add the "
               "'since last check' panel")

    only = select_classrooms(rooms, codes, teacher)
    payload = build_payload(students, rooms, detector_meta, recommender_meta,
                            changes, changes_meta, only)

    output_path = output_path or paths.dashboard
    write_text(output_path, render(payload, paths.dashboard_template))

    report(f"{len(payload['rooms'])} classrooms, "
           f"{len(payload['students'])} students -> {output_path}")
    report(f"  {file_size_kb(output_path):,.0f} KB, self-contained")
    if only:
        for room in payload["rooms"]:
            report(f"  {room['c']}  {room['n']} - {room['t']} "
                   f"({room['k']['flagged']} flagged, {room['k']['high']} high)")
    return output_path


@cli.friendly_data_errors
def main(argv=None):
    """Entry point for ``python dashboard.py``."""
    parser = cli.build_parser(
        "Render the teacher dashboard as one self-contained HTML file.")
    parser.add_argument("--classroom", action="append", metavar="CODE",
                        help="only this classroom (repeatable)")
    parser.add_argument("--teacher", metavar="NAME",
                        help="only classrooms whose teacher name contains NAME")
    parser.add_argument("--out", default=None,
                        help="where to write the HTML (default: dashboard.html "
                             "in the data directory)")
    cli.add_data_dir_argument(parser)
    args = parser.parse_args(argv)

    run(cli.paths_from_args(args), output_path=args.out,
        codes=args.classroom, teacher=args.teacher)


if __name__ == "__main__":
    main()
