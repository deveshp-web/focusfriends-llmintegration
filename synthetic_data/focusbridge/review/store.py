"""The append-only log of teacher review actions.

WHY APPEND-ONLY
---------------
Every action is written as a new line and nothing is ever rewritten in place, so
the file is a **full history of who did what and when**, not just a
current-status table. "Current status" for a flag is simply its most recent
line.

That costs a little disk and buys three things that matter here:

  * a dismissed flag that is later acted on leaves both facts on the record,
    which is exactly what an IEP meeting or a safeguarding review needs;
  * two tools writing at once cannot corrupt each other's data - appends do not
    overwrite;
  * there is no read-modify-write window in which an action can be lost.

Both the CLI (``review_flags.py``) and the Streamlit viewer use this module, so
the two can never disagree about the file format, or about which review wins
when a flag has been reviewed more than once.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..core.jsonio import open_text_appender, read_jsonl
from ..core.paths import DataPaths
from ..core.triage import priority_rank

#: The three things a teacher can say about a flag.
#:
#:   reviewed   I have seen this
#:   dismissed  I have seen this and I disagree, or it is already handled
#:   acted_on   I have seen this and I did something
#:
#: ``dismissed`` is deliberately distinct from ``reviewed``: it is the only
#: signal in the system that says a rule was **wrong** for this student, which
#: makes it the most valuable feedback the pipeline can collect.
VALID_ACTIONS = ("reviewed", "dismissed", "acted_on")


def default_path():
    """Where reviews live when no path is given."""
    return DataPaths().flag_reviews


def load_reviews(path=None):
    """``flag_id -> the most recent review for that flag``.

    Later lines overwrite earlier ones as the file is read, which is what makes
    "the last line wins" fall out of the append-only design rather than needing
    to be enforced.

    A missing file is normal - it just means nobody has reviewed anything yet -
    so it yields an empty result instead of an error.
    """
    latest = {}
    for review in read_jsonl(path or default_path(), skip_missing=True):
        latest[review["flag_id"]] = review
    return latest


def append_review(flag_id, student_id, classroom_code, action,
                  note="", reviewed_by="", path=None):
    """Record one review action. Returns the record that was written.

    Args:
        flag_id: the detector's id for this student *and this set of findings*.
        action:  one of :data:`VALID_ACTIONS`.
        note:    optional free text - why it was dismissed, what was tried.
        reviewed_by: who did it. Optional, because a shared classroom device
            often has no signed-in user, and a review with no name is still far
            better than no review.
    """
    if action not in VALID_ACTIONS:
        raise ValueError(
            f"action must be one of {list(VALID_ACTIONS)}, got {action!r}")

    review = {
        "flag_id": flag_id,
        "student_id": student_id,
        "classroom_code": classroom_code,
        "action": action,
        "note": note,
        "reviewed_by": reviewed_by,
        "reviewed_at": datetime.now().isoformat(timespec="seconds"),
    }

    path = Path(path or default_path())
    # "a" for append: the file is opened, one line is added at the end, and it
    # is closed. The existing contents are never read, held or rewritten.
    with open_text_appender(path) as handle:
        handle.write(json.dumps(review) + "\n")
    return review


def unreviewed(records, reviews, channel="alert"):
    """Flags nobody has responded to yet, most urgent first.

    Args:
        records: insight records, as read from ``insights.jsonl``.
        reviews: the result of :func:`load_reviews`.
        channel: restrict to one channel, or ``"all"``.
    """
    pending = [record for record in records if record["flag_id"] not in reviews]
    if channel != "all":
        pending = [record for record in pending if record["channel"] == channel]
    # `list.sort` is *stable*: records of equal priority keep the order they had
    # in insights.jsonl, which groups a teacher's own room together rather than
    # scattering it. Sorting is therefore enough - no tie-break is needed to
    # make the output reproducible.
    pending.sort(key=lambda record: priority_rank(record["priority"]))
    return pending


def describe(record):
    """One line summarising a flagged student, for a terminal or a list."""
    summary = record["summary"]
    return (f"{record['student_name']} ({record['classroom_name']}) - "
            f"{record['channel'].upper()}/{record['priority']} - "
            f"{summary['alert_streak_count']} alert, "
            f"{summary['watch_streak_count']} watch, "
            f"{summary['positive_streak_count']} positive streak(s)")
