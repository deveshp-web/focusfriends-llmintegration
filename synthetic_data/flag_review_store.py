"""Backwards-compatible alias for the review store.

The implementation moved to ``focusbridge/review/store.py`` during the v2
refactor, so that it sits next to the CLI that uses it and can share the
project's single JSONL reader.

This file stays behind because ``from flag_review_store import append_review``
may exist in a notebook, a script or someone's muscle memory, and breaking that
buys nothing. Prefer the new import in new code::

    from focusbridge.review.store import append_review, load_reviews
"""

from focusbridge.review.store import (  # noqa: F401  (re-exported on purpose)
    VALID_ACTIONS,
    append_review,
    default_path,
    load_reviews,
)

#: The old module exposed the path as a constant rather than a function.
REVIEWS_PATH = default_path()
