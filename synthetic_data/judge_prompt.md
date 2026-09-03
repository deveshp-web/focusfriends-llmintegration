# LLM-as-a-judge: reviewing the emotion-pattern detector

`detect_patterns.py` decides which students a teacher gets alerted about. This
file is the prompt for having ChatGPT or Gemini audit those decisions against
real boundary cases pulled from the data.

Run `python detect_patterns.py` first — it writes `judge_cases.jsonl`, a
stratified sample (12 per bucket, seed 42) of the cases the rules find hardest.
Each line carries `case_type`, `population` (how many exist in the full dataset),
the student and classroom id, and either the flagged `streak` or the raw
`entries` that make the case interesting.

**Both rounds are complete and the external audit loop is closed.** Round 1 and
Round 2 findings, and what shipped in response to each, are recorded below. The
prompt and buckets are kept current so the loop can be reopened later, but no
further round is planned. Treat this file as the system of record for why the
rules are what they are.

## The prompt

> You are auditing a rule-based alerting system used in special-education
> classrooms. It reads emotion check-ins that students log on a tablet and
> surfaces students whose recent pattern a teacher should look at. A false
> negative means a struggling child goes unnoticed. A false positive means a
> teacher's list fills with noise and they stop reading it. Both are real
> costs; neither is automatically worse.
>
> Findings go to one of two channels:
>
> **ALERT** — the queue a teacher is expected to read.
> - `negative` — 4+ consecutive negative check-ins, in any mix.
>
> **WATCH** — dashboard context, never paged.
> - `density` — a rolling 7-day window of 6+ check-ins that is ≥70% negative
>   without ever reaching 4 in a row.
> - `same_day_cluster` — 4+ consecutive negatives inside one school day, for
>   runs that raised no alert.
> - `repeated` — the same **positive** emotion 4+ times in a row.
>
> Negative = sad, angry, frustrated, anxious, overwhelmed, scared, confused.
> Positive = happy, calm, excited. Neutral = okay, tired; neutrals break a
> negative run without being one, and never fire the repeated rule.
>
> Every streak must additionally span no more than **7 days** end to end, touch
> at least **2 distinct calendar days**, and fall within the last **30 days**
> (boundary inclusive). Same-day clusters are the deliberate exception to the
> 2-day rule and are why they sit on WATCH. A check-in whose emotion the system
> cannot read breaks a run rather than being skipped over. Only ALERT-channel
> streaks set priority high/medium; a watch-only student stays at "watch"
> however many soft signals they trip.
>
> Context that matters: students check in 2–3 times per school day, weekdays
> only. "Consecutive" means adjacent after sorting by timestamp, ties broken by
> emotion name.
>
> I will give you JSONL cases. For each, answer:
> 1. Should this student be surfaced? If so, on ALERT or WATCH? yes / no /
>    not enough information.
> 2. Which specific rule or threshold produced the disagreement, if any?
> 3. What minimal change would fix this case **without** breaking the others?
>
> Then, across all cases: name the single change you would make first, and say
> what it would cost. Do not propose adding machine learning; these rules must
> stay explainable to a teacher.

## What is in each bucket, and the open question it probes

| `case_type` | Population | The question |
|---|---|---|
| `at_window_edge` | 3,242 | ALERT streaks whose span is exactly the 7-day limit. Is 7 days cutting long slow declines into pieces? |
| `barely_multi_day` | 1,716 | ALERT streaks spanning under 24 hours — an afternoon plus the next morning. Is 2 calendar days the right floor, or should it be 3? |
| `one_short` | 1,762 | Exactly 3 negatives across multiple days, in a period that is **not** otherwise negative. Is 4 the right length? |
| `one_short_corroborated` | 221 | The same near-miss, but the surrounding period is ≥70% negative. Round 1 proposed promoting only these to WATCH. Should they be? |
| `same_day_only` | 828 | 4+ negatives in one morning. Now on WATCH via `same_day_cluster`. Is WATCH the right home, or should a strong cluster (6+) reach ALERT? |
| `repeated_positive` | 877 | Four "happy" in a row. Now on WATCH. Is it worth surfacing at all? |
| `watch_only` | 795 | Students whose entire case is WATCH. Round 1 warned not to assume this tier is correct — does each one earn its place? |
| `dense_but_unflagged` | 0 | Was 39. The gap Round 1 called the strongest false-negative risk; the density trigger closed it. Should stay 0. |

## Round 1 outcome (ChatGPT, audited 72 cases)

Confirmed and fixed:

1. **The repeated rule fired on neutral emotions.** `okay ×4` and `tired ×4`
   were treated the same as `happy ×4` — 147 streaks across 83 students whose
   *only* finding was "this child felt okay four times". Fixed via
   `REPEATED_VALENCES = {"positive"}`.
2. **Sustained-but-broken negative weeks were invisible** (`negative ×3 → okay
   → negative ×3`). Fixed by enabling the density trigger on WATCH.
3. **Same-day clusters were fully invisible.** Fixed by adding
   `same_day_cluster` on WATCH; the 2-day floor still keeps them off ALERT.
4. **Alert fatigue**, raised as the risk of doing 2 and 3. Addressed by adding
   the channel split so nothing new can reach a teacher's alert queue.

Found independently during verification, not in the audit:

5. **Duplicate timestamps sorted unstably.** The same check-ins in a different
   row order produced different streaks. Fixed with a deterministic tie-break.

Corrections to the round-1 handoff:

- `barely_multi_day` was 8 negative + 4 repeated cases, not 9 + 3.
- "Dense cases are 70–75% negative" is a selection artifact — the bucket
  *filters* at ≥70%, so the range is a property of the filter, not a finding.
- The handoff took the 39-student density estimate from a run with the other
  changes off; with the shipped configuration, density alone newly surfaces 28.

Declined for now: lowering `STREAK_LENGTH` to 3 globally (agreed — noise), and
promoting `one_short_corroborated` to WATCH (221 cases, deferred to round 2
rather than shipped on one audit's say-so).

## Measured effect of the round-1 changes

| | before | after | delta |
|---|---|---|---|
| Students flagged | 2,737 | 2,842 | +105 |
| **ALERT channel** | **2,047** | **2,047** | **±0** — identical student set |
| WATCH channel | 690 | 795 | +105 |
| `okay`/`tired` repeated streaks | 147 | 0 | −147 |
| Students dropped entirely | — | 83 | all were `okay ×4` only |
| Students newly surfaced | — | 188 | all WATCH, none ALERT |

`MIN_DISTINCT_DAYS` sensitivity (unchanged by round 1):

| value | students flagged | negative-trigger | total streaks |
|---|---|---|---|
| 1 | 3,012 | 2,246 | 11,077 |
| **2 (current)** | 2,737 | 2,047 | 10,004 |
| 3 | 1,439 | 1,267 | 6,542 |

## The knobs

All at the top of `detect_patterns.py`:

- `STREAK_LENGTH` (4) — `one_short`
- `STREAK_WINDOW_DAYS` (7) — `at_window_edge`
- `MIN_DISTINCT_DAYS` (2) — `barely_multi_day`, `same_day_only`
- `REPEATED_VALENCES` (`{"positive"}`) — `repeated_positive`
- `ENABLE_DENSITY_TRIGGER` (True) + `DENSITY_*` — `dense_but_unflagged`
- `ENABLE_SAME_DAY_CLUSTER` (True), `SAME_DAY_CLUSTER_LENGTH` (4) — `same_day_only`
- `ALERT_TRIGGERS` / `WATCH_TRIGGERS` — which findings page a teacher
- `EMOTION_VALENCE` — whether confused/scared/tired are scored right

Re-run `python detect_patterns.py` after any change; `insights_meta.json`
records the exact configuration each run used, so two runs can be compared.

## Round 2 outcome (ChatGPT, final external round)

| Recommendation | Decision | Evidence |
|---|---|---|
| Widen `STREAK_WINDOW_DAYS` 7 → 10 to stop fracturing long declines | **Declined, root cause fixed instead** | Widening changes the alert population by **+0 students** — it never fixed detection, only symptoms. Fracturing came from emitting one window per streak; runs are now merged into one record per episode, which works at any length. |
| Keep `MIN_DISTINCT_DAYS = 2` | **Adopted** (no change) | Agreed. |
| Leave `one_short` alone, keep `STREAK_LENGTH = 4` | **Adopted** (no change) | Agreed. |
| New trigger: 3 negatives + ≥75% 7-day negative share → WATCH | **Declined** | 238 runs qualify, but only **12** are not already inside a density window. A new trigger and threshold for 12 cases is complexity without coverage. |
| `same_day_cluster` of 6+ escalates to ALERT | **Adopted** | 48 students have a 6+ cluster; only **8** were not already alerted. Small, targeted, and it closes the acute end of the calendar artifact. |
| Delete repeated-positive (`REPEATED_VALENCES` → empty) | **Modified** | The bloat was real — it was 565 of 795 watch students. Moved to its own POSITIVE channel rather than deleted: watch drops to 222 and the praise/progress signal survives. |
| Global: replace calendar days with elapsed time (24h) | **Declined** | Round 2 argues both sides: it justifies `MIN_DISTINCT_DAYS = 2` on the grounds that *"overnight persistence of negative emotions is a strong indicator of distress"*, then calls the same boundary a dangerous artifact. Persistence across an overnight reset is a real distinction worth keeping, and the acute-cluster gap it worried about is closed by the 6+ escalation above. Adopting both would double-cover one problem. |

### Found during Round 2 verification, raised by neither round

Chasing Round 2's "streaks artificially hit the 7-day limit" turned up the
actual cause and two further consequences:

- **One uninterrupted negative run was reported as many streaks** — 523
  students affected, worst case a single run split across **26 records**.
- **The same bug sat in the density trigger**, unnoticed because nobody was
  counting: up to **62 records** for one student's rough fortnight.
- **Density shadowed every alert.** A run of consecutive negatives is by
  definition also dense, so each alert came with a duplicate watch record for
  the same period. Density now skips periods an alert already covers.

Together these cut streak records from 34,001 to 9,398 without changing who is
flagged.

## Final state

| | original | after round 1 | after round 2 |
|---|---|---|---|
| Students flagged | 2,737 | 2,842 | 2,842 |
| ALERT channel | 2,047 | 2,047 | 2,055 |
| WATCH channel | 690 | 795 | 222 |
| POSITIVE channel | — | — | 565 |
| Streak records | — | 34,001 | 9,398 |
| Neutral repeated streaks | 147 | 0 | 0 |

## Known limitations, accepted

1. **The calendar-day artifact persists by choice.** 4 negatives spanning 20
   hours across midnight alerts; 4 negatives across 3 hours in one morning
   reaches WATCH, and only at 6+ reaches ALERT. The overnight boundary is
   treated as meaningful rather than incidental.
2. **A Friday–Monday pair spends 3 of the 7 window days on the weekend.** The
   window is calendar-based, not school-day-based.
3. **The WATCH tier's value is still unmeasured.** It is now 222 students
   rather than 795, so it is at least plausibly readable — but whether anyone
   reads it is a product question that no amount of auditing this dataset can
   answer.
4. **Everything here is measured on synthetic data.** The archetypes that
   generated it were designed to carry detectable patterns, so the flag rates
   are not a forecast of real classroom rates.
