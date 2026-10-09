# Focus Bridge — synthetic data, emotion detection, and the teacher dashboard

Six things live here:

    focusbridge_sourcecode/   the Focus Bridge app (single-file React/Babel build)
    synthetic_data/           a synthetic corpus and the pipeline that reads it
    integrated_version/       the app with the pipeline's insights built into it
    supabase/                 the database both apps run against, and its security tests
    deploy/                   the build that turns the app into the site Netlify serves
    android/                  the Play Store app: a WebView around that site

To put it online, read [DEPLOY.md](DEPLOY.md).

Focus Bridge is a classroom app for special-education students: they check in
with how they feel, work through a picture schedule, use a calm corner and read
social stories. `synthetic_data/` holds a generated corpus of 4,437 students
across 220 classrooms, and a four-stage pipeline that turns their emotion
check-ins into something a teacher can act on.

`integrated_version/` is the third thing, and the newest: the app with those
same rules ported into the browser, so a teacher sees the analysis of their own
class on a tab of their own dashboard instead of in a separately generated HTML
file. It has its own README.

## The pipeline

    python detect_patterns.py    # who should I look at?      -> insights.jsonl
    python recommend.py          # so what do I do Monday?    -> recommendations.jsonl
    python notify.py             # what changed since I last looked?
    python dashboard.py          # render it                  -> dashboard.html

Run them in that order from inside `synthetic_data/`. Each stage reads the
previous stage's output and writes its own, so any of them can be re-run alone
after a threshold change. Nothing needs a network, a database or a build step.

Every stage also accepts `--data-dir DIR` to run against a different corpus
(a test fixture, a second dataset), or `$FOCUSBRIDGE_DATA_DIR` to set it once.

### 1. `detect_patterns.py` — detection

Flags students whose recent emotion check-ins form a pattern. Findings go to one
of three channels so that soft signals and good news never crowd the queue a
teacher is actually expected to read:

| Channel | Trigger | Meaning |
|---|---|---|
| **alert** | 4+ consecutive negative check-ins; 6+ negatives in one day | the queue a teacher reads |
| **watch** | a mostly-negative week never reaching 4 in a row; 4–5 negatives in one day | context, never paging |
| **positive** | the same positive emotion 4+ times running | good news, kept separate |

Two external LLM audit rounds shaped these rules; `judge_prompt.md` is the
system of record for what was proposed, what shipped, and what was declined.
The rules stay explainable on purpose — a teacher has to be able to disagree
with one.

### 2. `recommend.py` — recommendations

Turns each flag into ranked, evidence-cited advice by joining on what the
detector never sees: `task_log` (which activity a flagged check-in sat next to,
and whether it was skipped), `notes.jsonl` (what the teacher already wrote —
including, in the app's own note templates, a task they already named as a
trigger), `stories.jsonl` (which social stories this classroom already owns) and
the student's calm-corner history, AAC use and token progress.

Fifteen rules across escalation, antecedent, regulation, communication, story,
motivation and documentation, plus four room-level rules for things you change
once instead of per student. Channel caps mirror the alert-fatigue logic
upstream: 4 recommendations for an alert student, 2 for watch, 1 for positive.

**Every correlation compares a rate against that student's own rate** — of the
check-ins around Reading, how many went badly, versus how often their check-ins
go badly at all. This matters more than any threshold in the file, and the long
comment above `min_task_excess` in `focusbridge/recommend/settings.py` records
the three ways of getting it wrong that were tried first.

### 3. `notify.py` — what changed

Compares two detector runs and classifies each student's movement: escalated,
new, eased, resolved. Writes `changes.jsonl` and a short plain-text digest per
teacher under `digests/`, each one short enough to be the body of an email.

A previous state is produced by pinning the detector to an earlier date, so the
comparison is real data rather than a fixture:

    python detect_patterns.py --reference 2026-07-31T13:08:00 --suffix _prev

> **A correction.** This section used to say the change feed could only ever
> improve on this corpus — "0 newly flagged" at every step — and blamed how the
> data was generated. That was a bug: the detector's window had no upper bound,
> so the pinned "previous" run also read every check-in after its own date and
> nothing could be new against it. With the window bounded at both ends, the
> same comparison gives 163 escalated, 219 newly flagged, 158 eased and 235
> resolved. The two windows still overlap, so a change is a reason to look, not
> a verdict — the dashboard's footer says so.

### 4. `dashboard.py` — the teacher view

Renders one self-contained HTML file — no server, no network, no build — so it
opens from a USB stick in a classroom with no wifi.

    python dashboard.py                      # all 220 rooms (~4.7 MB)
    python dashboard.py --classroom WAJHZX   # one room (~50 KB)
    python dashboard.py --teacher "Ahmed"    # every room for one teacher

Each room leads with an alerts panel answering two questions from different
evidence — who is trending down inside this window, and what changed since the
last run — then room-wide recommendations, then the alert queue, with watch and
positive collapsed. Filters for trending-down / changed / undocumented, a table
view, print styling, and light and dark themes.

### Side channels

Neither is part of the four stages, and nothing depends on either:

    python review_flags.py               # mark flags reviewed / dismissed / acted on
    streamlit run viewer.py              # the same, with a UI
    python generate_insights.py --limit 5  # optional LLM gloss, via a local Ollama model

## How the code is organised

The four entry-point scripts are thin; the code lives in the `focusbridge`
package, laid out so that each module has one job and the four stages never
import each other:

    focusbridge/
      core/           shared foundations — depends on nothing but the stdlib
        paths.py        every filename in the project, in one place
        jsonio.py       the one way this project reads and writes JSON
        vocabulary.py   the emotion words and what they mean
        timeline.py     timestamps, check-ins, and the O(1) window index
        triage.py       channels, priorities, sort orders, display names
        cli.py          the argparse options every stage shares

      detect/         stage 1   settings, rules, summarize, judge, pipeline
      recommend/      stage 2   settings, stats, phrasing, context,
                                student_rules, room_rules, pipeline
      notify/         stage 3   diff, digest, pipeline
      dashboard/      stage 4   payload, render, pipeline
      review/         the teacher response loop (store, cli)

    tests/            unittest suite over the pure functions

Every dependency arrow points inward, toward `core/`. Anything two stages both
need lives there, which is what lets you change one stage without reading the
other three.

**Where to start reading**, if the code is new to you:

1. `focusbridge/__init__.py` — the map, and why it is shaped this way
2. `focusbridge/core/vocabulary.py` — the words everything else is built on
3. `focusbridge/core/timeline.py` — how a raw check-in becomes comparable
4. `focusbridge/detect/rules.py` — the four rules, heavily commented

Each package's `__init__.py` explains what that stage does and which file to
open next.

## Tests

    cd synthetic_data
    python -m unittest discover -s tests -t .

97 tests, standard library only, no fixtures on disk. They cover the pure
functions — the rules, the parsing, the statistics, the diff — because that is
where the decisions live and they can be checked against a seven-entry list
instead of a 153 MB file. The window index is additionally checked against a
slow, obviously-correct implementation over randomised input, because the
failure mode of a wrong optimisation is silently different answers rather than
a crash.

## Regenerating everything

    cd synthetic_data
    python detect_patterns.py --reference 2026-07-31T13:08:00 --suffix _prev
    python detect_patterns.py
    python recommend.py
    python notify.py
    python dashboard.py

Roughly four minutes end to end, dominated by the passes over the 153 MB
`students.jsonl`. Passing `--reference` to the second command skips one of them.
Python 3.9+, standard library only.

## Files

| | |
|---|---|
| `students.jsonl` | 4,437 students with emotion and task logs (**Git LFS**, 153 MB) |
| `classrooms.jsonl` `notes.jsonl` `stories.jsonl` `consent.jsonl` `audit_log.jsonl` | the rest of the corpus |
| `insights.jsonl` `flagged_by_classroom.jsonl` `insights_meta.json` | detector output |
| `recommendations.jsonl` `classroom_actions.jsonl` | recommendation output |
| `changes.jsonl` `digests/` | change feed and per-teacher digests |
| `dashboard.html` `dashboard_template.html` | the rendered dashboard and its template |
| `judge_prompt.md` `judge_cases.jsonl` | the LLM-as-a-judge audit trail |
| `flag_reviews.jsonl` | append-only log of teacher review actions |

`students.jsonl` is tracked with Git LFS — it is over GitHub's 100 MB per-file
limit. Clone with `git lfs install` first, or the file arrives as a pointer.

## The app with the insights built in

`integrated_version/` is the Focus Bridge app plus an **Insights** tab on the
teacher dashboard, showing the same analysis this pipeline produces — but for
the classroom the teacher currently has open, computed in the browser, live.

    cd integrated_version
    # open index.html, then: Teachers -> Load a sample classroom -> Insights

The rules are ported into `fb-insights.js` rather than called over a network,
because check-ins are children's emotional records and the app's whole privacy
posture is that they stay where the teacher put them. The cost of that choice is
that the detection and recommendation rules now exist **twice** — here in Python
and there in JavaScript — so a threshold changed in one has to be changed in the
other.

That is checked rather than remembered:

    cd integrated_version
    python tools/smoke_test.py

`tools/verify_engine.py` runs *these* stages over the same records the browser
sees and writes the result out; `tools/verify.html` then diffs the port against
it, student by student, across channel, priority, both correlations, room rules,
reading order and every recommendation id. Two more pages check that the screen
renders and that the journey through it works. All three run headless from that
one command.

See `integrated_version/README.md` for what changed in the app.

## The database

`supabase/focus-bridge-schema-secure.sql` is the schema both apps are written
against: six tables behind row-level security, and the database functions
students call without logging in. Run it in the Supabase SQL Editor, then run
`supabase/security_tests.sql` the same way — it must finish without an error
before any real student data goes in. `supabase/SECURITY_TESTS.md` explains
what the tests prove and what to check by hand.

The trust model, in one paragraph: anyone with the page and a class code can
look up the classroom and join it as a **new** student, and nothing more. A
returning child picks up their own record only on a device that proves it —
the device they used before, or a classroom tablet the teacher has marked as
trusted in Settings. Teachers reach only their own classrooms. Every secret is
stored as a hash.

A project that ran the earlier (v3) schema is upgraded in place by the same
file, keeping every record. It signs every child out once, and the new page
and the new database have to go live together. [DEPLOY.md](DEPLOY.md) has the
order.

To run the same tests on a local Postgres instead of a Supabase project:

    python supabase/tests/run_local.py

That runs them on a fresh install and on an upgraded copy of the v3 schema
holding data, and checks the two end up identical. It needs PostgreSQL 15 or
newer; the file's docstring shows how to get one without installing anything
system-wide.

## Publishing the app

    python deploy/build_site.py --supabase-url https://YOUR-PROJECT.supabase.co --supabase-key sb_publishable_...
    python deploy/check_site.py --site deploy/site

The first command builds `deploy/site/`, the folder Netlify serves. It holds
the app, your server's address and public key, a fresh service-worker cache
name, and a `_headers` file with a Content-Security-Policy. The second loads
that folder in Chrome under those headers, as a new visitor and as a returning
one, and fails if anything is blocked. `netlify.toml` runs the same build when
Netlify deploys from GitHub. [DEPLOY.md](DEPLOY.md) covers both routes.

## A caveat that applies to all of it

Every number here is measured on synthetic data whose archetypes were designed
to carry detectable patterns. The flag rates are not a forecast of real
classroom rates, and two of the rules find almost nothing here by design —
activity clustering in particular, because the generator built episodes that
carry emotion streaks rather than activities that trigger them.
