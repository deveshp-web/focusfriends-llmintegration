"""STAGE 3 - the change feed: "what is different from when I last looked?"

WHY THIS STAGE EXISTS
---------------------
A dashboard that shows the same 2,842 flagged students every morning gets read
once. On the second visit the question is not "who is flagged" - it is "what
changed". Nothing upstream can answer that: the detector and the recommender
each describe a single moment and have no memory.

This stage compares two detector runs and classifies every student's movement.

WHY IT DIFFS ``insights``, NOT ``recommendations``
--------------------------------------------------
A notification is about a change in **standing**, not a change in wording.
Recommendation text shifts for reasons that are not news - a new note was
written, one more check-in came in - and a feed that reported those would be
noise within a week.

HOW A PREVIOUS STATE IS PRODUCED
--------------------------------
Not with a fixture. The detector is re-run pinned to an earlier date, which
produces a genuine snapshot of what that day would have shown::

    python detect_patterns.py --reference 2026-07-31T13:08:00 --suffix _prev
    python detect_patterns.py
    python recommend.py
    python notify.py

.. warning::
   **On this synthetic corpus the diff can only ever improve.** Sliding the
   30-day window forward drops flagged students and adds none - measured over
   four weekly reference dates on a 1,200-student sample: 910 -> 870 -> 818 ->
   751, with ``new = 0`` at every step. The aggregate negative rate is flat
   (~34%, with a deliberate ~56% Monday spike), so this is not the data thinning
   out; the generator front-loads each student's episodes, so later windows
   contain fewer *consecutive* runs.

   "0 newly flagged" here is an artefact of how the data was made, not good news
   about a classroom. The dashboard says so in its own footer rather than
   letting a teacher read it as progress.

   Because of that, the dashboard leads with **within-window trajectory** - who
   is worse in the second half of their own window than the first - which does
   move in both directions on this corpus (216 worsening against 229 improving)
   and needs only one run. Run-over-run change is shown beneath it.

Module map:
    diff.py      classifying each student's movement between two runs
    digest.py    the plain-text per-teacher summary
    pipeline.py  the wiring
"""
