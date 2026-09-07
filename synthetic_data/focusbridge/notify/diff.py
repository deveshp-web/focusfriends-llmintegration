"""Classifying how each student moved between two detector runs.

THE TRICK THAT MAKES THIS SIMPLE
--------------------------------
There look to be four separate cases to handle - a student arriving on the
list, getting worse, getting better, leaving the list - plus the awkward fact
that a student may be absent from one run entirely.

They collapse into **one numeric comparison** once "not flagged" is given a
rank of its own, one better than every real priority (see
:data:`~focusbridge.core.triage.UNFLAGGED_RANK`). Then:

    rank went down (worse)  and there was no previous record   -> new
    rank went down (worse)                                     -> escalated
    rank went up (better)   and there is no current record     -> resolved
    rank went up (better)                                      -> eased
    rank unchanged                                             -> unchanged

Four transitions, no special cases, and adding a priority tier later cannot
break it.
"""

from __future__ import annotations

from collections import Counter

from ..core.triage import RANK_LABEL, rank_of_record, display_name

#: The order the dashboard and the digest both read in: worst movement first.
#: ``unchanged`` is counted but never listed - it is not news.
CHANGE_ORDER = ("escalated", "new", "eased", "resolved")


def classify(previous, current):
    """How one student moved between the two runs.

    Args:
        previous: their record in the earlier run, or ``None`` if not flagged then.
        current:  their record in the later run, or ``None`` if not flagged now.

    >>> classify(None, {"priority": "high"})
    'new'
    >>> classify({"priority": "high"}, None)
    'resolved'
    >>> classify({"priority": "medium"}, {"priority": "high"})
    'escalated'
    """
    before, after = rank_of_record(previous), rank_of_record(current)
    if before == after:
        return "unchanged"
    if after < before:  # lower rank number = more urgent = worse
        return "new" if previous is None else "escalated"
    return "resolved" if current is None else "eased"


def build_changes(previous_run, current_run, recommendations, rooms):
    """Per-classroom change records, plus the run-wide totals.

    Every student present in **either** run is considered. Iterating only the
    current run would silently drop everyone who left the flagged set - and that
    is precisely the resolution a teacher most wants confirmed.
    """
    by_room = {}
    totals = Counter()

    # The union of both runs' student ids. A set union is O(n + m) and, unlike
    # a nested scan, does not care which run is larger.
    for student_id in set(previous_run) | set(current_run):
        previous = previous_run.get(student_id)
        current = current_run.get(student_id)
        change = classify(previous, current)
        totals[change] += 1

        # Whichever run has this student - preferring the current one, since a
        # student who is still flagged has fresher metadata.
        anchor = current or previous
        entry = {
            "student_id": student_id,
            "display_name": display_name(anchor.get("student_name"),
                                         anchor.get("name_mode"), student_id),
            "change": change,
            "from": RANK_LABEL[rank_of_record(previous)],
            "to": RANK_LABEL[rank_of_record(current)],
            "channel": current["channel"] if current else None,
        }

        # Advice, when stage 2 has produced any. A change entry that can say
        # what to do about the change is worth far more than one that only says
        # it happened.
        advice = recommendations.get(student_id)
        if advice:
            entry["why"] = advice["why_flagged"]
            if advice["recommendations"]:
                entry["action"] = advice["recommendations"][0]["headline"]

        by_room.setdefault(anchor.get("classroom_code"), []).append(entry)

    records = [_room_record(code, students, rooms.get(code, {}))
               for code, students in by_room.items()]

    # Rooms with the most escalations first: the feed is ordered by how much it
    # deserves someone's attention, exactly like every other list in this project.
    records.sort(key=lambda record: (-record["counts"]["escalated"],
                                     -record["counts"]["new"],
                                     record["classroom_code"]))
    return records, totals


def _room_record(code, students, room):
    """One classroom's change record."""
    counts = Counter(entry["change"] for entry in students)
    listed = [entry for entry in students if entry["change"] != "unchanged"]

    # Worst movement first, then alphabetically - so a teacher can scan the same
    # list twice without it reshuffling.
    #
    # The trailing `student_id` is what makes that promise actually hold. Two
    # students in the same room can share a display name ("Sana" and "Sana", or
    # any two students in a room set to initials-only), and with only
    # (change, name) in the key their relative order fell back to insertion
    # order - which comes from iterating a *set* of student ids, and Python
    # randomises string hashing per process. Measured on this corpus, 20 runs of
    # the old code over identical inputs produced two different digest files.
    # A digest that reshuffles for no reason teaches a teacher that the ordering
    # means nothing, and it makes two runs impossible to diff.
    listed.sort(key=lambda entry: (CHANGE_ORDER.index(entry["change"]),
                                   entry["display_name"],
                                   entry["student_id"]))

    return {
        "classroom_code": code,
        "classroom_name": room.get("name") or code,
        "teacher_name": room.get("teacher_name") or "Unassigned",
        "school_name": room.get("school_name") or "",
        "counts": {name: counts.get(name, 0)
                   for name in CHANGE_ORDER + ("unchanged",)},
        "changed_student_count": len(listed),
        "students": listed,
    }
