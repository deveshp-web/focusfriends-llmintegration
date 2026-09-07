#!/usr/bin/env python3
"""Teacher review loop - command-line entry point.

This file is deliberately tiny. All of the work lives in ``focusbridge.review.cli``;
this exists so the documented commands keep working exactly as before:

    python review_flags.py --list
    python review_flags.py                   # interactive walkthrough

Read the code in ``focusbridge/review/`` - start with its ``__init__.py``, which
explains what the stage does and which file to open next.

Why an entry point at all, rather than just running the package module? Because
``python detect_patterns.py`` is what every README, script and habit already
says, and a refactor that quietly breaks everyone's muscle memory has spent
goodwill it did not need to spend. ``python -m focusbridge.review.cli`` does
exactly the same thing for anyone who prefers it.
"""

from focusbridge.review.cli import main

if __name__ == "__main__":
    main()
