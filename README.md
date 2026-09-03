# Focus Bridge — synthetic data, emotion detection, and the teacher dashboard

Two things live here:

    focusbridge_sourcecode/   the Focus Bridge app (single-file React/Babel build)
    synthetic_data/           a synthetic corpus and the pipeline that reads it

Focus Bridge is a classroom app for special-education students: they check in
with how they feel, work through a picture schedule, use a calm corner and read
social stories. `synthetic_data/` holds a generated corpus of 4,437 students
across 220 classrooms, and a four-stage pipeline that turns their emotion
check-ins into something a teacher can act on.

## The pipeline

    python detect_patterns.py    # who should I look at?      -> insights.jsonl
    python recommend.py          # so what do I do Monday?    -> recommendations.jsonl
    python notify.py             # what changed since I last looked?
    python dashboard.py          # render it                  -> dashboard.html

Run them in that order from inside `synthetic_data/`. Each stage reads the
previous stage's output and writes its own, so any of them can be re-run alone
after a threshold change. Nothing needs a network, a database or a build step.

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

Fourteen rules across escalation, antecedent, regulation, communication, story,
motivation and documentation, plus four room-level rules for things you change
once instead of per student. Channel caps mirror the alert-fatigue logic
upstream: 4 recommendations for an alert student, 2 for watch, 1 for positive.

**Every correlation compares a rate against that student's own rate** — of the
check-ins around Reading, how many went badly, versus how often their check-ins
go badly at all. This matters more than any threshold in the file, and the long
comment above `MIN_TASK_EXCESS` records the three ways of getting it wrong that
were tried first.

### 3. `notify.py` — what changed

Compares two detector runs and classifies each student's movement: escalated,
new, eased, resolved. Writes `changes.jsonl` and a short plain-text digest per
teacher under `digests/`, each one short enough to be the body of an email.

A previous state is produced by pinning the detector to an earlier date, so the
comparison is real data rather than a fixture:

    python detect_patterns.py --reference 2026-07-31T13:08:00 --suffix _prev

> **Read the change counts with care.** On this synthetic corpus the diff can
> only ever improve — sliding the 30-day window forward drops flagged students
> and adds none (910 → 870 → 818 → 751 over four weekly reference dates, with
> zero new at every step). The generator front-loads each student's episodes, so
> "0 newly flagged" is an artefact of how the data was made, not good news about
> a classroom. The dashboard says so in its own footer.

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

## Regenerating everything

    cd synthetic_data
    python detect_patterns.py --reference 2026-07-31T13:08:00 --suffix _prev
    python detect_patterns.py
    python recommend.py
    python notify.py
    python dashboard.py

Roughly four minutes end to end, dominated by two passes over the 153 MB
`students.jsonl`. Python 3.9+, standard library only.

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

`students.jsonl` is tracked with Git LFS — it is over GitHub's 100 MB per-file
limit. Clone with `git lfs install` first, or the file arrives as a pointer.

## A caveat that applies to all of it

Every number here is measured on synthetic data whose archetypes were designed
to carry detectable patterns. The flag rates are not a forecast of real
classroom rates, and two of the rules find almost nothing here by design —
activity clustering in particular, because the generator built episodes that
carry emotion streaks rather than activities that trigger them.
