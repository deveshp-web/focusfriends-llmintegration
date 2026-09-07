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

DIRECTORY MAP
=============
    core/           shared foundations - no stage-specific knowledge lives here
      paths.py        every filename in the project, in one place
      jsonio.py       the one and only way this project reads/writes JSON
      vocabulary.py   the emotion words and what they mean
      timeline.py     timestamps, check-ins, and fast sliding-window maths
      triage.py       channels, priorities, sort orders, display names

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
      student_rules.py  the 14 per-student rules
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
