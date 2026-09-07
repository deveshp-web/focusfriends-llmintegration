# Pre-refactor scripts, kept for reference

These are the nine standalone scripts as they were before the v2 refactor moved
the code into the `focusbridge/` package. Nothing imports them and nothing runs
them; they are here because this directory is not under version control, and
deleting the only copy of working code is not a thing to do on someone's behalf.

They are useful for one thing: reading a rule as it was originally written next
to its refactored version. If you want to *run* them, copy them into a
directory holding a corpus - they resolve every path relative to their own
location, which is one of the things the refactor fixed.

Safe to delete once this directory is in git.

| file | where its code lives now |
|---|---|
| `detect_patterns.py` | `focusbridge/detect/` and `focusbridge/core/` |
| `recommend.py` | `focusbridge/recommend/` |
| `notify.py` | `focusbridge/notify/` |
| `dashboard.py` | `focusbridge/dashboard/` |
| `review_flags.py` | `focusbridge/review/cli.py` |
| `flag_review_store.py` | `focusbridge/review/store.py` |
| `generate_insights.py` | `generate_insights.py` (rewritten in place) |
| `viewer.py` | `viewer.py` (rewritten in place) |
| `test_detect_patterns.py` | `tests/test_detect_rules.py`, `tests/test_timeline.py` |
