"""Focus Bridge - turning classroom emotion check-ins into something a teacher can act on.

WHAT THIS PACKAGE IS
====================
Focus Bridge is an app for special-education classrooms. Students tap how they
feel ("happy", "frustrated", ...) several times a day. That produces a lot of
raw check-ins and almost no answers, so this package runs a four-stage pipeline
that turns the raw log into a short, evidence-backed list a teacher can read.

Each stage answers exactly one question, and each stage's output is the next
stage's input:

    STAGE          QUESTION IT ANSWERS              MAIN OUTPUT
    -------------  ------------------------------  ---------------------------
    1. detect      "Who should I look at?"          insights.jsonl
    2. recommend   "So what do I do on Monday?"     recommendations.jsonl
    3. notify      "What changed since last time?"  changes.jsonl + digests/
    4. dashboard   "Show me all of it."             dashboard.html

Run them in order from the `synthetic_data/` directory:

    python detect_patterns.py
    python recommend.py
    python notify.py
    python dashboard.py

THIS FILE IS THE ONE ARCHITECTURE DOCUMENT
==========================================
Every stage used to explain itself in its own ``__init__.py``, and the overlap
between those seven files and this one had already started to drift - the same
channel table written twice with different wording is two things a reader has
to reconcile, and eventually one of them is wrong. So all of it lives here, and
each subpackage's ``__init__.py`` is a one-line marker that points back.

Those markers are still real files on purpose. Deleting them would turn every
subpackage into an implicit namespace package, which works right up until
something else on ``sys.path`` is also called ``core`` - and the shims in
``synthetic_data/`` do put that directory on the path. A one-line file is a
cheap way not to find that out later.

HOW THE CODE IS ORGANISED (and why)
===================================
The guiding rule is **high cohesion, low coupling**:

  * HIGH COHESION - everything in one module is about one job. `core/vocabulary.py`
    knows about emotions and nothing else. `recommend/phrasing.py` holds the
    English sentences and nothing else. If you want to add an emotion you edit
    exactly one file.

  * LOW COUPLING - a module never reaches into another module's internals, and
    the four stages never import each other. Anything two stages both need
    (how to read a JSONL file, what "high priority" means, how to display a
    student's name) lives in `core/`, which depends on nothing but the Python
    standard library.

    Before this refactor the stages imported helpers *from each other* -
    `dashboard.py` asked the *detector* how to read a JSON file, and `notify.py`
    asked the *recommender* how to format a name. That meant you could not touch
    the detector without risking the dashboard. Now the dependency arrows all
    point one way, inward, toward `core/`:

        detect ─┐
        recommend ─┤
        notify ─┤──> core  (and core imports nobody)
        dashboard ─┘

    ``tests/test_architecture.py`` enforces that mechanically, by reading the
    import statements out of the source with :mod:`ast`. It exists because the
    invariant is easy to state and easy to lose: the next person who needs
    ``display_name`` inside the notifier can restore the old tangle with one
    plausible-looking import, and no behavioural test would notice.

DIRECTORY MAP
=============
    core/           shared foundations - no stage-specific knowledge lives here
      paths.py        every filename in the project, in one place
      jsonio.py       the one and only way this project reads/writes JSON
      vocabulary.py   the emotion words and what they mean
      timeline.py     timestamps, check-ins, and fast sliding-window maths
      triage.py       channels, priorities, sort orders, display names
      cli.py          the argparse options every stage shares

    detect/         STAGE 1 - pattern detection
      settings.py     the thresholds, each with the reasoning behind it
      rules.py        the four detection rules (pure functions, easy to test)
      summarize.py    per-student rollup + the stable flag id
      judge.py        sampling borderline cases for an external LLM audit
      pipeline.py     wiring: read students -> apply rules -> write files

    recommend/      STAGE 2 - advice
      settings.py     thresholds, with the statistics reasoning behind them
      stats.py        the maths (exact binomial test, rate-excess test)
      phrasing.py     every sentence, emoji and word list shown to a teacher
      context.py      assembling everything known about one flagged student
      student_rules.py  the 15 per-student rules
      room_rules.py     the 4 whole-classroom rules
      pipeline.py     wiring

    notify/         STAGE 3 - what changed between two runs
      diff.py         classifying each student's movement
      digest.py       the plain-text per-teacher email body
      pipeline.py     wiring

    dashboard/      STAGE 4 - the single self-contained HTML file
      payload.py      compressing the data the page needs
      render.py       injecting that data into the HTML template
      pipeline.py     wiring

    review/         teacher responses to flags (used by the CLI and the viewer)
      store.py        the append-only review log
      cli.py          the command-line front end

If you find yourself wanting to import a stage from inside ``core/``, the thing
you are writing belongs in a stage, not in core.


================================================================================
 STAGE 1 - detect: "who should I look at?"
================================================================================
Reads ``students.jsonl`` and flags students whose recent emotion check-ins form
a pattern. Four rules, sorted into three channels so that soft signals and good
news never crowd the queue a teacher is actually expected to read:

    CHANNEL   RULE               FIRES WHEN
    --------  -----------------  ----------------------------------------------
    alert     negative           4+ bad check-ins in a row
    alert     same_day_cluster   6+ bad check-ins inside one school day
    watch     same_day_cluster   4-5 bad check-ins inside one school day
    watch     density            a mostly-bad week that never reaches 4 in a row
    positive  repeated           the same good emotion 4+ times running

The rules stay simple and explainable on purpose. A teacher has to be able to
look at a flag, disagree with it, and see exactly which threshold produced it -
which is also why every threshold in ``detect/settings.py`` carries the
reasoning that put it there, and why every finding cites the numbers that fired
it.

**Only alert-channel findings can raise priority.** A student whose entire case
is watch-channel stays at "watch" however many soft signals they trip. That is
what makes it safe to keep adding gentler rules: a new soft signal can add
context, but it can never inflate the queue.


================================================================================
 STAGE 2 - recommend: "so what do I do on Monday?"
================================================================================
Stage 1 answered "who should I look at?". A teacher's very next question is what
to actually *do*, and this stage answers it by joining the flags against
everything the detector deliberately never sees - because the detector only ever
looks at emotion check-ins:

    task_log        which activity a flagged check-in sat next to, and whether
                    that activity was completed or skipped
    notes.jsonl     what the teacher already wrote down - including, in the
                    app's own note templates, a task they already named as a
                    trigger
    stories.jsonl   which social stories this classroom already owns, so advice
                    can point at something that exists rather than inventing
                    homework
    the student row calm-corner tools already tried, AAC use, token progress

Every recommendation is a rule with a stated trigger, and every one cites the
numbers that fired it - for the same reason the detector is rule-based rather
than a model: **a teacher has to be able to disagree with it.** Advice a teacher
cannot audit is advice a teacher will eventually ignore.

Nothing in this stage re-decides who is flagged. It reads ``insights.jsonl`` as
given, and a student the detector did not flag gets no recommendations at all.

THE ONE IDEA WORTH READING TWICE
--------------------------------
**Every correlation compares a rate against that student's own rate.** Of the
check-ins around Reading, how many went badly - versus how often this student's
check-ins go badly at all. Not "what share of the flags happened at Reading",
which just measures the timetable. ``recommend/settings.py`` records the three
ways of getting this wrong that were tried first, and why each one failed.

Caps mirror the channel split upstream: 4 recommendations for an alert student,
2 for a watch student, 1 for a positive one. A teacher with 23 flagged students
cannot read 23 x 8 pieces of advice, and advice nobody reads is worse than none.


================================================================================
 STAGE 3 - notify: "what is different from when I last looked?"
================================================================================
A dashboard that shows the same 2,842 flagged students every morning gets read
once. On the second visit the question is not "who is flagged" - it is "what
changed". Nothing upstream can answer that: the detector and the recommender
each describe a single moment and have no memory.

This stage compares two detector runs and classifies every student's movement.

It diffs ``insights``, not ``recommendations``, because a notification is about
a change in **standing**, not a change in wording. Recommendation text shifts
for reasons that are not news - a new note was written, one more check-in came
in - and a feed that reported those would be noise within a week.

A previous state is produced by re-running the detector pinned to an earlier
date, not from a fixture, so the comparison is real data::

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


================================================================================
 STAGE 4 - dashboard: one self-contained HTML file
================================================================================
No server, no network, no build step. Everything the page needs is embedded in
it, so it opens from a USB stick in a classroom with no wifi - which is a real
constraint in the schools this is built for, not a hypothetical one.

    python dashboard.py                      # every classroom (~4.7 MB)
    python dashboard.py --classroom WAJHZX   # one room (~50 KB)
    python dashboard.py --teacher "Ahmed"    # every room for one teacher

A room is the unit a teacher owns, so the page renders one at a time and a
picker switches between them. That is also what keeps a 220-room file
responsive: the data for every room is present, but only the selected room is
ever laid out.

Reading order inside a room follows the channel split the detector established,
because the entire point of that split is that not everything deserves equal
attention:

    1. room-wide recommendations   things to change once, not per student
    2. the ALERT queue             the students a teacher is expected to read
    3. WATCH                       context, collapsed by default
    4. POSITIVE                    good news, collapsed by default

COLOUR
------
Priority uses the fixed status palette and **always** ships an icon and a word
alongside the colour, so nothing is carried by hue alone - which matters for
colour-blind readers and for the projector in the corner of a classroom.

The check-in strip is a diverging encoding: positive and negative are the poles,
neutral is the midpoint and is meant to recede, because an "okay" check-in is
the absence of a signal rather than a weak one.


================================================================================
 review/ - the teacher response loop
================================================================================
Marking flags reviewed, dismissed or acted on. Not one of the four stages, and
nothing depends on it.

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


================================================================================
 THE SAME RULES ALSO RUN IN THE BROWSER
================================================================================
``integrated_version/fb-insights.js`` is a port of ``detect/`` and
``recommend/`` into JavaScript, so the Focus Bridge app can show a teacher the
same analysis over their own live classroom without the check-ins ever leaving
the device.

**That means the rules live in two places.** A threshold, a rule or an emotion
changed here must be changed there too, or the app and this pipeline will
quietly disagree about which child a teacher sees first.
``integrated_version/tools/verify_engine.py`` runs these stages over the same
records the browser sees and diffs the two, student by student; run it after
any change to either side.

WHERE TO START READING
======================
If you are new to this code, read in this order:
    1. core/vocabulary.py   - the words the whole system is built on
    2. core/timeline.py     - how a raw check-in becomes something comparable
    3. detect/rules.py      - the four rules, heavily commented
    4. detect/pipeline.py   - how those rules get applied to 4,437 students
"""

# The public version of the pipeline's output format. Bump the minor number when
# a stage starts writing a new field; bump the major number when it stops
# writing an old one, because that is what breaks a downstream reader.
__version__ = "2.0.0"
