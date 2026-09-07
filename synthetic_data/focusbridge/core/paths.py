"""Every filename this project reads or writes, in exactly one place.

WHY THIS FILE EXISTS
--------------------
The old code built paths inline, all over the place::

    out_path = os.path.join(DATA_DIR, f"insights{tag}.jsonl")

...repeated in four different modules. Three problems with that:

  1. Renaming a file meant hunting through every module for the string.
  2. ``DATA_DIR`` was pinned to wherever the *script* happened to live, so the
     pipeline could not be pointed at a test fixture or a second dataset.
  3. The ``_prev`` suffix trick (used to produce an earlier state to diff
     against) had to be re-applied by hand in every stage, and it was easy to
     forget it on one file - which silently mixes two runs together.

``DataPaths`` solves all three: one object holds the directory and the suffix,
and every filename is a single named property on it.

HOW TO USE IT
-------------
::

    paths = DataPaths()                     # the default synthetic_data/ folder
    paths = DataPaths(suffix="_prev")       # the "previous run" variant
    paths = DataPaths(root="/tmp/fixture")  # a test fixture

    paths.students        -> .../students.jsonl        (never suffixed: input)
    paths.insights        -> .../insights_prev.jsonl   (suffixed: output)
"""

from __future__ import annotations

import os
from pathlib import Path

# The directory holding the corpus. `Path(__file__)` is
# .../focusbridge/core/paths.py, so two `.parent` hops land on `focusbridge/`,
# and a third lands on the `synthetic_data/` folder that holds the data files.
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent

# Environment override, so the pipeline can be pointed at another corpus without
# editing code or passing --data-dir to every single stage. Handy in CI.
DATA_DIR_ENV_VAR = "FOCUSBRIDGE_DATA_DIR"


class DataPaths:
    """Resolves every project filename against one directory and one suffix.

    Args:
        root:   the directory holding the corpus. Defaults to the environment
                variable ``FOCUSBRIDGE_DATA_DIR``, then to the
                ``synthetic_data/`` folder that ships with this package.
        suffix: appended to the *generated* filenames only, e.g. ``"_prev"``
                turns ``insights.jsonl`` into ``insights_prev.jsonl``. Source
                data files (students, classrooms, notes, stories) are never
                suffixed - there is only ever one copy of the corpus.
    """

    def __init__(self, root=None, suffix=""):
        if root is None:
            root = os.environ.get(DATA_DIR_ENV_VAR) or DEFAULT_DATA_DIR
        self.root = Path(root)
        self.suffix = suffix

    # -- internal helpers ----------------------------------------------------

    def _fixed(self, name):
        """A file whose name never changes between runs (the source corpus)."""
        return self.root / name

    def _tagged(self, stem, extension):
        """A generated file, which carries the run suffix.

        ``_tagged("insights", "jsonl")`` with suffix ``"_prev"`` gives
        ``insights_prev.jsonl``.
        """
        return self.root / f"{stem}{self.suffix}.{extension}"

    # -- source corpus (read-only inputs, never suffixed) ---------------------

    @property
    def students(self):
        """4,437 students with their emotion and task logs. ~153 MB, streamed."""
        return self._fixed("students.jsonl")

    @property
    def classrooms(self):
        """One row per classroom: teacher, school, token goal, name display mode."""
        return self._fixed("classrooms.jsonl")

    @property
    def notes(self):
        """Free-text notes a teacher wrote about a student."""
        return self._fixed("notes.jsonl")

    @property
    def stories(self):
        """Social stories each classroom already owns."""
        return self._fixed("stories.jsonl")

    @property
    def dashboard_template(self):
        """The HTML shell the dashboard payload is injected into."""
        return self._fixed("dashboard_template.html")

    # -- stage 1: detect ------------------------------------------------------

    @property
    def insights(self):
        """One record per flagged student."""
        return self._tagged("insights", "jsonl")

    @property
    def flagged_by_classroom(self):
        """The same flags, regrouped so a teacher sees their own room."""
        return self._tagged("flagged_by_classroom", "jsonl")

    @property
    def insights_meta(self):
        """Thresholds used and totals produced, so a run can be explained later."""
        return self._tagged("insights_meta", "json")

    @property
    def judge_cases(self):
        """Borderline cases sampled for an external LLM-as-a-judge audit."""
        return self._tagged("judge_cases", "jsonl")

    # -- stage 2: recommend ---------------------------------------------------

    @property
    def recommendations(self):
        """Ranked, evidence-citing advice, one record per flagged student."""
        return self._tagged("recommendations", "jsonl")

    @property
    def classroom_actions(self):
        """Room-level rollup plus the recommendations that apply room-wide."""
        return self._tagged("classroom_actions", "jsonl")

    @property
    def recommendations_meta(self):
        return self._tagged("recommendations_meta", "json")

    # -- stage 3: notify ------------------------------------------------------
    # These are never suffixed: a change feed is by definition about exactly one
    # pair of runs, so a "_prev change feed" would be meaningless.

    @property
    def changes(self):
        return self._fixed("changes.jsonl")

    @property
    def changes_meta(self):
        return self._fixed("changes_meta.json")

    @property
    def digests(self):
        """Directory holding one plain-text summary per teacher."""
        return self._fixed("digests")

    # -- stage 4: dashboard ---------------------------------------------------

    @property
    def dashboard(self):
        return self._fixed("dashboard.html")

    # -- teacher review loop (side channel, not one of the four stages) -------

    @property
    def flag_reviews(self):
        """Append-only log of teachers marking flags reviewed/dismissed."""
        return self._fixed("flag_reviews.jsonl")

    @property
    def llm_suggestions(self):
        """Optional AI-written summaries, joined back on flag_id."""
        return self._fixed("insights_with_suggestions.jsonl")

    # -- convenience ----------------------------------------------------------

    def with_suffix(self, suffix):
        """A second view of the same directory under a different run suffix.

        Used by `notify`, which needs to read the current run and the pinned
        earlier run at the same time.
        """
        return DataPaths(self.root, suffix)

    def __repr__(self):  # shown in tracebacks, so make it useful
        return f"DataPaths(root={str(self.root)!r}, suffix={self.suffix!r})"
