"""The teacher response loop: marking flags reviewed, dismissed or acted on.

WHY THIS IS NOT PART OF THE FOUR STAGES
---------------------------------------
Detection and review are kept as two separate concerns, on purpose and in both
directions:

  * re-running the detector never erases review history;
  * reviewing a flag never touches detection output.

Nothing here writes to ``insights.jsonl``. Review status lives in its own
append-only file and is joined back on ``flag_id`` - the id the detector
computes from "this student **plus this exact set of findings**", so that
dismissing last week's streak can never silently dismiss a fresh one that
started today.

Without this loop a teacher sees the same list every morning, including the
students they have already dealt with, and the list stops being read.

Module map:
    store.py  the append-only review log, shared by the CLI and the viewer
"""
