# Focus Bridge — with the insights dashboard built in

This folder is the Focus Bridge app with the `synthetic_data` pipeline's teacher
insights folded into it as a sixth tab on the teacher dashboard.

    integrated_version/
      index.html            the app — now with an Insights tab
      fb-insights.js        the detection + recommendation engine, in the browser
      demo-data.js          three sample classrooms from the synthetic corpus
      sw.js  manifest  icons  privacy.html  delete-account.html
      tools/                the exporter, and three test pages

Everything else in the app is unchanged. Nothing was removed.

## Try it in two minutes

Open `index.html` in a browser (a double-click is enough — it needs no server),
then:

1. **Teachers** on the landing page
2. **Load a sample classroom** → pick one
3. **Insights** in the green sidebar

That installs a generated classroom into this browser's offline storage and
opens the teacher dashboard on it. The Insights tab then analyses those
check-ins live, in the page, and shows what it found.

To remove it afterwards: Settings → *Delete this classroom*, or just clear the
site data.

## What the Insights tab does

It answers the three questions the Python pipeline answers, for the classroom
currently open:

| | |
|---|---|
| **who should I look at?** | four detection rules over the last 30 days of check-ins |
| **so what do I do?** | nineteen recommendation rules, each citing the numbers that fired it |
| **what changed?** | a comparison against the last time you pressed *Mark as read* |

On screen, in reading order:

* **six stat tiles** — flagged, high priority, alert, watch, going well, and
  alert students with nothing written down;
* **two panels** — who is declining *inside* this window, and what has moved
  since you last looked. Two different questions from two different kinds of
  evidence, which is why they are side by side rather than merged;
* **room-wide advice** — the patterns you change once instead of per student;
* **an activity chart** — how many of the flagged students cluster around each
  activity, drawn against the flagged count so one child does not look like a
  schedule problem;
* **three channels** — the alert queue open, watch and going-well folded. The
  split exists to fight alert fatigue: if soft signals and good news shared a
  list with children in real distress, the list stops being read;
* **a card per student** — why they are flagged, a strip of their last 28
  check-ins (hover one for the date and feeling), and the ranked advice with
  its evidence underneath.

Plus a search, filters for trending-down / changed / no-notes, a table view, and
print styling so a card can go into a meeting folder.

### The rule that matters most

Every correlation compares **a rate against that student's own rate**. "Reading
is where this shows up" means the check-ins around Reading went badly more often
than that student's day goes badly overall — not that most of their flags happen
to fall near Reading, which would only be measuring the timetable. A pattern
must clear an exposure floor, an effect-size floor and an exact binomial test
before it is named.

The three ways of getting that wrong that were tried first are recorded in
`focusbridge/recommend/settings.py` upstream and summarised in `fb-insights.js`.

## Where the analysis runs

In the browser, over the records the dashboard already loaded. Nothing is
uploaded, no key is needed, and it works offline — which matters, because these
are children's emotional records and the app's whole privacy posture is that
they stay where the teacher put them. Sending them to an analysis service to get
advice back would quietly undo that.

The cost is that `fb-insights.js` is a **port** of the Python package rather
than a call into it, and a port is only worth something if somebody checks it.
See *Testing* below.

## What changed in the app

Six edits to `index.html`, and one to `sw.js`. Everything else is byte-identical
to `focusbridge_sourcecode/`.

1. **`fb-insights.js` is loaded** in `<head>`, as a classic script so the app
   still works opened straight off a USB stick.
2. **The emotion picker went from 8 words to 12** — Calm, Frustrated, Anxious
   and Overwhelmed were **appended**, never reordered. The detector could
   already read all four; the app had no way to emit them, so a child who was
   overwhelmed had to log "Sad" or "Confused" and the flag described the wrong
   feeling. Appending rather than inserting is deliberate: children who rely on
   button position have learned where their feeling lives on that grid, and
   moving one is a regression for them. `tools/smoke.html` asserts the first
   eight positions never move.
3. **`DB.getAllNotes(code, ids)`** — one query for the room's notes instead of
   one per child. Two rules turn on whether a note exists at all, so the whole
   room has to be in hand before anything is analysed.
4. **An `InsightsView` component**, and an `Insights` entry in the dashboard
   nav, placed second — directly after the roster it reads.
5. **A callout on the Students tab** linking into it, so the feature is
   reachable without knowing the sidebar has a sixth item.
6. **A sample-data loader** on the teacher sign-in screen, offline mode only.
   It writes a generated classroom into the app's ordinary demo storage, so
   every screen runs on it exactly as it would on a real class.
7. **`sw.js`** caches `fb-insights.js` (cache bumped to v31), so the Insights
   tab still works on a classroom iPad with no wifi.

## Testing

Three suites, all in a real browser, because the thing under test is a single
HTML file that compiles its own JSX at load time — there is no build step to
hook into and no module graph to import.

```
cd integrated_version
python tools/smoke_test.py
```

That starts a local server, drives headless Chrome (or Edge), and prints:

```
[ok  ] tools/verify.html    All 411 checks agree with the Python pipeline.
[ok  ] tools/smoke.html     All 20 smoke checks passed
[ok  ] tools/e2e.html       All 19 end-to-end checks passed
```

Each page is also just a page — open it and read it.

| | |
|---|---|
| **`tools/verify.html`** | Is the port faithful? Runs `fb-insights.js` over the sample rooms and diffs it against `expected.js`, which `tools/verify_engine.py` produced by running the **real Python stages** over **the same records**. Compares channel, priority, the why-flagged line, trajectory, note counts, dominant emotion, both correlations, room rules, the reading order, and the exact ordered list of recommendation rules for every student. |
| **`tools/smoke.html`** | Does the screen work? Pulls the app's script out of `index.html`, compiles it, and renders the real `InsightsView` and `Dashboard` against a real sample room, asserting on what actually appears. |
| **`tools/e2e.html`** | Does the journey work? Clicks through landing → Teachers → sample classroom → dashboard → Insights → filter → table view → Mark as read → Open in Students, finding controls by the words on them. |

`verify.html` earned its keep twice. It caught the engine reporting a student as
"41% of recent check-ins negative" where the pipeline said 40% — JavaScript
rounds halves up and Python rounds them to even, so the same child was being
described with two different numbers by two tools a teacher is told are the same
analysis. And `e2e.html` caught the Students-tab callout being a clickable
`<div>`, unreachable by keyboard.

### Regenerating the sample data

```
python tools/export_demo_rooms.py            # -> demo-data.js  (1.4 MB)
python tools/verify_engine.py                # -> tools/expected.js + .json
```

The exporter reads the corpus in `../synthetic_data/` and translates three
classrooms into the app's own shapes, trimmed to a 40-day window. Pass
`--rooms CODE CODE` for different ones. The three defaults were chosen so that
between them every part of the view has something to show: `DYAHV5` populates
all three channels and has two students whose distress clusters around a named
activity; `W39DTP` is the shape of room where the "several students trending
down together" rule fires; `XDZYTL` is lighter.

## Two things to know about the numbers

**The window is anchored to the room's own latest check-in, not to today.**
Anchoring to `now` would show an empty page for any class that has not checked
in this month — including every sample room — and "no flags" and "no data" are
very different answers. When the newest check-in is more than two days old the
tab says so in a banner rather than presenting a stale window as current. The
sample rooms are ~40 days behind, so that banner is expected there.

**The corpus has no activity-linked distress to speak of.** Across 3,839
(student, activity) pairs in the full dataset the excess rate is near-symmetric
about zero — sampling noise. So the activity rule firing for a handful of
students is correct behaviour, not a broken gate, and the room-level activity
and time rules never fire at all. The archetypes carry emotion *streaks*, not
activities that trigger them. That rule exists for live app data, where a child
who comes apart at every Gym transition is an ordinary case.

## One bug found upstream, not fixed here

`focusbridge/recommend/context.py::index_tasks` reads task timestamps with
`datetime.fromisoformat(str(raw.get("ts")))` instead of going through
`parse_timestamp`, the tolerant reader every other log path in the project uses.

App task rows are `{emoji, task, time, iso}` and carry no `ts`, so **every task
row from an app export is silently dropped**, and the activity correlation — the
rule the recommender itself calls "the strongest thing a teacher can actually
change" — can never fire on live data. It only ever worked on the generated
corpus.

Demonstrated on `stu_3432bd2e6cf4`: identical timeline, identical streaks,
identical flagged positions, and the cluster (Transition to Gym, 8 of 12 flagged
against a 36% baseline, p=0.029) present from corpus rows and absent from app
rows.

`fb-insights.js` does not share the bug — its `indexTasks` uses the same
tolerant parser as its check-in reader. The upstream fix is one line:

```python
# focusbridge/recommend/context.py, in index_tasks()
at = parse_timestamp(raw)          # was: datetime.fromisoformat(str(raw.get("ts")))
if at is None:
    continue
```

Left unapplied because it is outside this folder; `tools/verify_engine.py`
converts task rows to the corpus schema so the comparison is not distorted by
it, and says so in a comment where it does.
