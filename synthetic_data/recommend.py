#!/usr/bin/env python3
"""STAGE 2 - recommendations - command-line entry point.

This file is deliberately tiny. All of the work lives in ``focusbridge.recommend.pipeline``;
this exists so the documented commands keep working exactly as before:

    python detect_patterns.py     # must run first - writes insights.jsonl
    python recommend.py

Read the code in ``focusbridge/recommend/`` - start with its ``__init__.py``, which
explains what the stage does and which file to open next.

Why an entry point at all, rather than just running the package module? Because
``python detect_patterns.py`` is what every README, script and habit already
says, and a refactor that quietly breaks everyone's muscle memory has spent
goodwill it did not need to spend. ``python -m focusbridge.recommend.pipeline`` does
exactly the same thing for anyone who prefers it.
"""

from focusbridge.recommend.pipeline import main

if __name__ == "__main__":
    main()
