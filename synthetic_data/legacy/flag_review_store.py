"""Shared helpers for recording teacher review actions on flagged students.

Review actions are appended to flag_reviews.jsonl - one line per action,
never rewritten in place - so the file is a full history of who did what,
not just a current-status table. "Current status" for a given flag_id is
just its most recent line.

Used by both review_flags.py (CLI) and viewer.py (Streamlit) so the two
tools can never disagree about the file format or about which review wins
when a flag has been reviewed more than once.
"""

import json
from datetime import datetime
from pathlib import Path

REVIEWS_PATH = Path(__file__).parent / "flag_reviews.jsonl"

VALID_ACTIONS = {"reviewed", "dismissed", "acted_on"}


def load_reviews(path=REVIEWS_PATH):
    """flag_id -> most recent review record for that flag_id."""
    latest = {}
    path = Path(path)
    if not path.exists():
        return latest
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            review = json.loads(line)
            latest[review["flag_id"]] = review  # later lines overwrite earlier ones
    return latest


def append_review(flag_id, student_id, classroom_code, action,
                   note="", reviewed_by="", path=REVIEWS_PATH):
    if action not in VALID_ACTIONS:
        raise ValueError(f"action must be one of {sorted(VALID_ACTIONS)}, got {action!r}")
    review = {
        "flag_id": flag_id,
        "student_id": student_id,
        "classroom_code": classroom_code,
        "action": action,
        "note": note,
        "reviewed_by": reviewed_by,
        "reviewed_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(path, "a") as f:
        f.write(json.dumps(review) + "\n")
    return review
