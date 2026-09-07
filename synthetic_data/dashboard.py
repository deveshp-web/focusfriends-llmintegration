#!/usr/bin/env python3
"""STAGE 4 - the teacher dashboard - command-line entry point.

This file is deliberately tiny. All of the work lives in ``focusbridge.dashboard.pipeline``;
this exists so the documented commands keep working exactly as before:

    python dashboard.py                      # every classroom
    python dashboard.py --classroom WAJHZX   # one room, much smaller file
    python dashboard.py --teacher "Ahmed"    # every room for one teacher

Read the code in ``focusbridge/dashboard/`` - start with its ``__init__.py``, which
explains what the stage does and which file to open next.

Why an entry point at all, rather than just running the package module? Because
``python detect_patterns.py`` is what every README, script and habit already
says, and a refactor that quietly breaks everyone's muscle memory has spent
goodwill it did not need to spend. ``python -m focusbridge.dashboard.pipeline`` does
exactly the same thing for anyone who prefers it.
"""

from focusbridge.dashboard.pipeline import main

if __name__ == "__main__":
    main()
