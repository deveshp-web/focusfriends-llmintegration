"""The per-teacher plain-text digest.

WHY PLAIN TEXT
--------------
It can be pasted into an email, a staff message or a handover note without
anything having to render it. That is the difference between a notification that
reaches a teacher and one that waits in a browser tab nobody opens.

The whole file is meant to be read on a phone between lessons, which is the
constraint every decision here answers to: name a handful of students, then
count the rest.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core.jsonio import write_text
from .diff import CHANGE_ORDER

#: How many students a digest names per section before it summarises the rest.
DIGEST_NAMES = 8


def write_digests(directory, records, window):
    """Write one digest per classroom that has changes. Returns how many.

    The directory is **rebuilt** every run rather than written over. A classroom
    whose changes have all resolved would otherwise keep a stale digest sitting
    there looking current, which is a worse failure than having no digest: it
    tells a teacher something that is no longer true.
    """
    directory = Path(directory)
    if directory.is_dir():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)

    written = 0
    for record in records:
        if not record["changed_student_count"]:
            continue
        write_text(directory / f"{record['classroom_code']}.txt",
                   render_digest(record, window))
        written += 1
    return written


def render_digest(record, window):
    """One classroom's digest as a string.

    Separated from the file writing so it can be tested, previewed, or sent
    somewhere other than a file - by an email job, for instance - without any of
    the logic being duplicated.
    """
    lines = [
        f"Focus Bridge - {record['classroom_name']}",
        record["teacher_name"]
        + (f" - {record['school_name']}" if record["school_name"] else ""),
        f"Changes between {window['prev_end']} and {window['now_end']}",
        "",
    ]

    # The one-line summary, so a teacher who reads nothing else still knows
    # whether to keep reading.
    counts = record["counts"]
    headline = ", ".join(f"{counts[name]} {name}"
                         for name in CHANGE_ORDER if counts[name]) or "no changes"
    lines.append(headline.capitalize() + ".")
    lines.append(f"{counts['unchanged']} students unchanged.")
    lines.append("")

    for change in CHANGE_ORDER:
        group = [entry for entry in record["students"] if entry["change"] == change]
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

    # The closing line is not filler: it tells a teacher that everything above
    # is a rule with a stated trigger, and where to go to see the evidence.
    lines.append("Every line above is a rule with a stated trigger. Open the "
                 "dashboard for the evidence behind each one.")
    return "\n".join(lines) + "\n"
