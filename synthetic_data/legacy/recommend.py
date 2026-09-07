"""Turn detector flags into recommendations a teacher can act on.

`detect_patterns.py` answers "who should I look at?". This answers the question
a teacher asks next: "so what do I do on Monday?".

Nothing here re-decides who is flagged. It reads insights.jsonl as given and
adds the context the detector deliberately does not carry, because the detector
only ever sees emotion check-ins:

  task_log      which activity a flagged check-in landed next to, and whether
                that activity was completed or skipped
  notes.jsonl   what the teacher already wrote down - including, in the app's
                own note templates, a task they already named as a trigger
  stories.jsonl which social stories this classroom already owns, so a
                recommendation can point at one that exists rather than
                inventing homework
  student       calm-corner tools already tried, AAC use, token progress

Every recommendation is a rule with a stated trigger and cites the numbers that
fired it, for the same reason the detector is rule-based: a teacher has to be
able to disagree with it. No recommendation is generated for a student the
detector did not flag.

Ranking follows the channel split. Alert-channel students can receive up to
MAX_RECOMMENDATIONS; a watch-only student gets at most WATCH_RECOMMENDATIONS and
a positive-channel student gets exactly one, so the same alert-fatigue logic
that shaped the channels also shapes how much a teacher has to read.

Writes:
  recommendations.jsonl     one record per flagged student, ranked
  classroom_actions.jsonl   one record per classroom: rollup, room-wide patterns
                            that only show up across students, and the ordered
                            student list the dashboard renders
  recommendations_meta.json run configuration and totals

Usage:
    python detect_patterns.py     # must run first - writes insights.jsonl
    python recommend.py
"""

import argparse
import bisect
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timedelta

# Imported rather than restated so the emotion vocabulary has one home. A new
# emotion added to detect_patterns.EMOTION_VALENCE is understood here for free.
from detect_patterns import (
    DATA_DIR,
    EMOTION_VALENCE,
    NEGATIVE_EMOTIONS,
    PRIORITY_RANK,
    iter_jsonl,
    read_log,
)

# ------------------------------------------------------------- task context --

# How close a check-in has to be to a task_log row to be attributed to it.
# Measured on the data: the gap between a check-in and its nearest task is 5
# minutes at the median and 10 at the 95th percentile, so 15 captures
# essentially all of them without reaching across into the next activity.
TASK_WINDOW_MINUTES = 15

# A task has to hold this many of a student's flagged check-ins before it is
# named. Two is a coincidence in a log this dense.
MIN_TASK_HITS = 3

# ...and the check-ins around it have to go wrong more often than this
# student's check-ins go wrong generally.
#
# What is measured here matters more than the threshold, and two earlier
# versions got it wrong in opposite directions:
#
#   `share >= 0.30 OR lift >= 1.4` let an activity through on frequency alone.
#   A student whose flags sat next to Reading exactly as often as everything
#   else sat next to Reading was told "Reading is where this shows up" at 0.99x
#   their own baseline - 23% of clusters were below the intended bar and 29
#   pointed at an activity that was, if anything, going better than average.
#
#   Comparing the *distribution* of flags across activities against the
#   distribution of all check-ins looked more rigorous and was worse. When a
#   student is negative 66% of the time, the flagged set is most of the
#   population, so the two distributions have to agree and no activity can ever
#   stand out. It found clusters for 24 of 2,842 students, which is not
#   restraint, it is a broken instrument.
#
# So the unit is a rate, not a share: of the check-ins around this activity,
# how many went badly - against how often this student's check-ins go badly at
# all. That question is well posed whether a student is flagged twice or forty
# times, and it is the question a teacher is actually asking.
#
# What this finds on the synthetic corpus, stated plainly because it is easy to
# mistake for a broken rule: 52 students out of 2,842. That is not the gates
# being too tight. Across 3,839 (student, activity) pairs the excess rate is
# distributed almost symmetrically about zero - 5th percentile -0.138, 95th
# +0.181 - which is what sampling noise looks like and what real
# activity-linked distress does not. The generator built archetypes that carry
# emotion *streaks*, not activities that trigger them, so there is close to
# nothing here to find and the rule correctly declines to invent it. It exists
# for live app data, where a child who comes apart at every Gym transition is
# an ordinary case.
#
# One caveat that survives into live data: this is a per-student test run over
# every activity, so at alpha 0.05 a room's worth of students generates
# false positives by chance. The effect-size and exposure floors below hold the
# rate well under that, and the recommendation is worded as a lead to check
# against a teacher's own knowledge rather than as a finding.
MIN_TASK_EXPOSURE = 5     # check-ins near the task, before a rate means anything
MIN_TASK_EXCESS = 0.15    # percentage points above the student's own rate
TASK_MAX_P = 0.05

# Skipped tasks in the flagged period, before avoidance is worth mentioning.
MIN_SKIPPED = 2

# --------------------------------------------------------------- time of day --

# Boundaries in hours. "midday" is deliberately narrow: it is the lunch/recess
# band, where a spike means something different from one in a work block.
TIME_BUCKETS = (
    ("morning", 0, 11, "before 11am"),
    ("midday", 11, 13, "the 11am-1pm block"),
    ("afternoon", 13, 24, "after 1pm"),
)
TIME_MIN_HITS = 4

# Share alone is worthless here, and measuring it was a real bug: 83% of every
# check-in in this dataset is logged before 11am, so "most of the flags are in
# the morning" is the timetable talking, not the student.
#
# The obvious fix - a fixed lift threshold, as the task rule uses - is worse,
# and measurably so. When a base rate is extreme, lift has a ceiling: at an 83%
# morning baseline the largest lift a student can possibly reach is 1/0.83 =
# 1.21, so a lift >= 1.5 gate makes "mornings are hard for this child"
# *arithmetically unreportable* no matter how total the concentration is. The
# gate silently encoded "the answer is never morning".
#
# So the test is a binomial one against the student's own baseline: how likely
# is this many flagged check-ins landing in this bucket, if flags fell in the
# same proportions the student's check-ins generally do? It self-adjusts to any
# base rate, which also matters because live app data will not share this
# dataset's timetable. Measured on 3,352 student-bucket observations:
#
#   fixed lift >= 1.5   88 fire, 10 survive p<0.05  - 89% small-sample noise,
#                       and 0 of them can be morning
#   binomial            15 fire, 5 of them morning  - the bias is gone and the
#                       false positives go with it
#
# The effect-size floor is what keeps a statistically-significant-but-trivial
# concentration from firing.
TIME_MAX_P = 0.05
TIME_MIN_EXCESS = 0.15

# --------------------------------------------------------------- calm corner --

# Two vocabularies again, exactly as with emotions. The Focus Bridge app ships
# the tools on the left; the synthetic data records the ids on the right. They
# were built separately and only `breathing` and `bubbles` agree, so the
# registry maps both onto one set and a recommendation always names something a
# student can actually be handed in the app.
CALM_TOOLS = {
    "breathing": {"label": "Deep Breathing", "emoji": "\U0001f32c️",
                  "aliases": ("counting",)},
    "bubbles": {"label": "Pop Bubbles", "emoji": "\U0001fae7", "aliases": ()},
    "fidget": {"label": "Fidget Spinner", "emoji": "\U0001f300",
               "aliases": ("squeeze-ball", "stretch")},
    "ground": {"label": "5-4-3-2-1", "emoji": "\U0001f590️", "aliases": ()},
    "rain": {"label": "Watch Rain", "emoji": "\U0001f327️",
             "aliases": ("music", "weighted-blanket")},
    "stars": {"label": "Starry Night", "emoji": "\U0001f30c",
              "aliases": ("dim-lights",)},
}
CALM_ALIASES = {alias: tool for tool, spec in CALM_TOOLS.items()
                for alias in spec["aliases"]}

# Which tool to reach for first, by what the student is actually feeling. The
# app's own emotion tips make the same pairings: scared -> grounding, angry ->
# breathing. Ordered - the first entry the student has not already exhausted is
# the one offered.
EMOTION_TOOLS = {
    "anxious": ("breathing", "ground", "rain"),
    "scared": ("ground", "breathing", "stars"),
    "overwhelmed": ("stars", "rain", "breathing"),
    "angry": ("breathing", "fidget", "bubbles"),
    "frustrated": ("fidget", "bubbles", "breathing"),
    "sad": ("rain", "bubbles", "stars"),
    "confused": ("ground", "breathing", "fidget"),
}

# What each negative emotion is a signal *of*, in plain words. Used to say why a
# recommendation fits rather than asserting that it does.
EMOTION_READS = {
    "anxious": "anticipating something",
    "scared": "not feeling safe",
    "overwhelmed": "too much input at once",
    "angry": "a demand landing harder than it can be met",
    "frustrated": "a task pitched above where the student is",
    "sad": "something carried in from outside the moment",
    "confused": "an instruction that did not land",
}

# Where the tool is not the whole answer, say so. Sadness is the case that made
# this necessary: the generic copy recommended a calm-corner tool while
# describing sadness as needing a conversation instead, which is advice that
# argues with itself.
EMOTION_CAVEATS = {
    "sad": "Ask before you offer, though - with sadness the conversation does "
           "more than the tool does.",
    "angry": "Offer it early; once anger is at its peak, a screen is another "
             "demand rather than a relief.",
}

# ------------------------------------------------------------------- stories --

# Keywords in a story title -> what it prepares a student for. Matched against
# the correlated task first, then the dominant emotion, so a teacher is pointed
# at a story their classroom already has. Keyword-based so stories a teacher
# writes themselves still match.
STORY_CUES = (
    ("fire drill", ("fire drill",), ("scared", "anxious", "overwhelmed")),
    ("substitute", ("substitute",), ("anxious", "overwhelmed")),
    ("assembly", ("assembly", "gym", "music"), ("overwhelmed", "anxious")),
    ("recess", ("recess",), ("anxious", "sad", "frustrated")),
    ("field trip", ("field trip", "trip"), ("anxious", "overwhelmed")),
    ("bus", ("bus", "arrival", "dismissal"), ("anxious", "scared")),
    ("friend", ("recess", "lunch", "circle time"), ("sad", "anxious")),
    ("picture day", ("picture",), ("anxious", "scared")),
    ("haircut", (), ("scared", "anxious")),
    ("dentist", (), ("scared", "anxious")),
)

# ---------------------------------------------------------------- note mining --

# The app writes notes from templates, and three of them name a task outright.
# That is the teacher's own read of the antecedent, which is worth more than
# anything computed here - so it is extracted and compared against, not
# averaged in.
NOTE_PATTERNS = (
    ("named_trigger", re.compile(r"struggled with the transition to (.+?);", re.I)),
    ("calm_worked", re.compile(r"used the calm corner after (.+?) and returned", re.I)),
    ("needed_prompt", re.compile(r"needed an extra prompt during (.+?) but", re.I)),
)
NOTE_FLAGS = (
    ("tired_after_lunch", re.compile(r"seemed tired after lunch", re.I)),
    ("token_goal_met", re.compile(r"reached the goal today", re.I)),
    ("aac_progress", re.compile(r"practiced two new AAC phrases", re.I)),
    ("parent_contact", re.compile(r"parent check-in", re.I)),
    ("good_morning", re.compile(r"had a great morning", re.I)),
)

# --------------------------------------------------------------------- limits --

# A teacher with 23 flagged students cannot read 23 x 8 recommendations. The
# caps are the same alert-fatigue argument the channel split is built on.
MAX_RECOMMENDATIONS = 4
WATCH_RECOMMENDATIONS = 2
POSITIVE_RECOMMENDATIONS = 1

# AAC use below this share of emotion check-ins, for a student whose flags are
# confusion/overwhelm shaped, reads as a communication gap rather than a mood.
AAC_LOW_RATIO = 0.35

# ---------------------------------------------------------- room-wide signals --

# A task has to hold this many of a room's flagged students - and this share of
# them - before it is called a room problem rather than several student ones.
ROOM_TASK_MIN_STUDENTS = 3
ROOM_TASK_MIN_SHARE = 0.25
ROOM_TIME_MIN_SHARE = 0.50
ROOM_MIN_FLAGGED = 3


# ==============================================================================
# context extraction
# ==============================================================================

def flagged_positions(recent, streaks):
    """Positions in `recent` of the check-ins that produced the flag.

    Positions rather than the entries themselves, because the correlations need
    to ask "was *this* check-in one of the flagged ones" while walking the full
    recent log - and a set of positions answers that without relying on object
    identity surviving a list comprehension.

    Positive-channel streaks are excluded: a run of "happy" is not evidence
    about what upsets a student.
    """
    windows = [(s["start_ts"], s["end_ts"]) for s in streaks
               if s["channel"] in ("alert", "watch")]
    if not windows:
        return set()
    hits = set()
    for i, entry in enumerate(recent):
        if entry["emotion"] not in NEGATIVE_EMOTIONS:
            continue
        stamp = entry["ts"].isoformat()
        if any(lo <= stamp <= hi for lo, hi in windows):
            hits.add(i)
    return hits


def flagged_entries(recent, streaks):
    """The flagged check-ins themselves, for the rules that only need the list."""
    return [recent[i] for i in sorted(flagged_positions(recent, streaks))]


def index_tasks(student):
    """(sorted timestamps, rows) for nearest-task lookup."""
    rows = []
    for raw in student.get("task_log") or []:
        if not isinstance(raw, dict):
            continue
        try:
            ts = datetime.fromisoformat(str(raw.get("ts")))
        except (TypeError, ValueError):
            continue
        label = raw.get("task")
        if isinstance(label, str) and label:
            rows.append({"ts": ts, "task": label, "status": raw.get("status")})
    rows.sort(key=lambda r: r["ts"])
    return [r["ts"] for r in rows], rows


def nearest_task(stamps, rows, ts):
    """The task_log row closest to `ts`, if one is within the window."""
    if not rows:
        return None
    limit = timedelta(minutes=TASK_WINDOW_MINUTES)
    i = bisect.bisect_left(stamps, ts)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(rows):
            gap = abs(rows[j]["ts"] - ts)
            if gap <= limit and (best is None or gap < best[0]):
                best = (gap, rows[j])
    return best[1] if best else None


def correlate_tasks(recent, flagged_at, student):
    """Which activity a student's check-ins go wrong around.

    For every activity: how many of this student's check-ins landed near it
    (`exposure`), and how many of those were flagged (`hits`). The rate of the
    second over the first is compared against the student's overall flagged
    rate, so an activity is named for going wrong *more often than this
    student's day goes wrong*, not for being frequent. See MIN_TASK_EXCESS.
    """
    stamps, rows = index_tasks(student)
    if not rows or not flagged_at:
        return [], {"matched": 0, "skipped_total": 0}

    exposure, hits, skipped = Counter(), Counter(), Counter()
    matched = 0
    for i, entry in enumerate(recent):
        row = nearest_task(stamps, rows, entry["ts"])
        if not row:
            continue
        matched += 1
        task = row["task"]
        exposure[task] += 1
        if i in flagged_at:
            hits[task] += 1
            if row["status"] == "skipped":
                skipped[task] += 1

    total_hits = sum(hits.values())
    if not matched or not total_hits:
        return [], {"matched": 0, "skipped_total": 0}
    overall = total_hits / matched
    if not 0 < overall < 1:
        return [], {"matched": matched, "skipped_total": sum(skipped.values())}

    results = []
    for task, count in hits.items():
        seen = exposure[task]
        if count < MIN_TASK_HITS or seen < MIN_TASK_EXPOSURE:
            continue
        rate = count / seen
        if rate - overall < MIN_TASK_EXCESS:
            continue
        p_value = binomial_tail(count, seen, overall)
        if p_value >= TASK_MAX_P:
            continue
        results.append({
            "task": task,
            "hits": count,
            "exposure": seen,
            "of_flagged": total_hits,
            "rate": round(rate, 3),
            "usual_rate": round(overall, 3),
            "lift": round(rate / overall, 2),
            "p_value": round(p_value, 4),
            "skipped": skipped[task],
        })
    # Ordered by strength of the association, not raw count - an activity with 6
    # hits well above the student's rate is a better lead than one with 10 that
    # is merely busy.
    results.sort(key=lambda r: (r["p_value"], -r["hits"]))
    return results, {"matched": matched, "skipped_total": sum(skipped.values())}


def bucket_of(ts):
    for name, lo, hi, _ in TIME_BUCKETS:
        if lo <= ts.hour < hi:
            return name
    return None


def binomial_tail(k, n, p):
    """P(X >= k) for X ~ Binomial(n, p), computed exactly.

    Exact rather than approximated because n here is a single student's flagged
    check-ins - typically 5 to 40 - which is exactly the range where a normal
    approximation is worst, and small enough that the exact sum is free.
    """
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def correlate_time(recent, flagged_at):
    """The part of the day this student's check-ins go wrong in, if any.

    Same test as the activity rule, on the same grounds: the rate of flagged
    check-ins inside a time band, against this student's overall rate. Measuring
    the *share* of flags falling in a band instead would be measuring the
    timetable - see TIME_MAX_P.
    """
    if len(flagged_at) < TIME_MIN_HITS:
        return None
    exposure, hits = Counter(), Counter()
    for i, entry in enumerate(recent):
        name = bucket_of(entry["ts"])
        if not name:
            continue
        exposure[name] += 1
        if i in flagged_at:
            hits[name] += 1
    total_hits = sum(hits.values())
    total_seen = sum(exposure.values())
    if not total_hits or not total_seen:
        return None
    overall = total_hits / total_seen
    if not 0 < overall < 1:
        return None

    best = None
    for name, count in hits.items():
        seen = exposure[name]
        if count < TIME_MIN_HITS or seen < MIN_TASK_EXPOSURE:
            continue
        rate = count / seen
        if rate - overall < TIME_MIN_EXCESS:
            continue
        p_value = binomial_tail(count, seen, overall)
        if p_value >= TIME_MAX_P:
            continue
        if best is None or p_value < best["p_value"]:
            phrase = next(p for n, _, _, p in TIME_BUCKETS if n == name)
            best = {"bucket": name, "phrase": phrase, "hits": count,
                    "exposure": seen, "rate": round(rate, 3),
                    "usual_rate": round(overall, 3),
                    "excess_points": round((rate - overall) * 100),
                    "lift": round(rate / overall, 2),
                    "p_value": round(p_value, 4)}
    return best


def trajectory(recent):
    """Whether the recent window is getting better or worse.

    Split at the midpoint of the window in time rather than by entry count, so a
    student who checked in more often in one half does not read as a trend.
    """
    blank = {"direction": "unknown", "first_half": 0.0, "second_half": 0.0,
             "delta": 0.0}
    if len(recent) < 6:
        return blank
    start, end = recent[0]["ts"], recent[-1]["ts"]
    mid = start + (end - start) / 2

    def share(entries):
        if not entries:
            return None
        return sum(1 for e in entries
                   if e["emotion"] in NEGATIVE_EMOTIONS) / len(entries)

    first = share([e for e in recent if e["ts"] < mid])
    second = share([e for e in recent if e["ts"] >= mid])
    if first is None or second is None:
        return blank
    delta = second - first
    direction = "worsening" if delta >= 0.15 else \
        "improving" if delta <= -0.15 else "steady"
    return {"direction": direction,
            "first_half": round(first, 3),
            "second_half": round(second, 3),
            "delta": round(delta, 3)}


def read_notes(notes, cutoff):
    """Structured signals from the teacher's own notes in the recent window."""
    signals = {"named_trigger": [], "calm_worked": [], "needed_prompt": [],
               "flags": set(), "recent": [], "count": 0}
    for note in notes:
        try:
            created = datetime.fromisoformat(str(note.get("created_at")))
        except (TypeError, ValueError):
            continue
        if created < cutoff:
            continue
        text = note.get("text") or ""
        signals["count"] += 1
        signals["recent"].append({"ts": created.isoformat(), "text": text})
        for key, pattern in NOTE_PATTERNS:
            found = pattern.search(text)
            if found:
                signals[key].append({"task": found.group(1).strip(),
                                     "ts": created.isoformat()})
        for key, pattern in NOTE_FLAGS:
            if pattern.search(text):
                signals["flags"].add(key)
    signals["recent"].sort(key=lambda n: n["ts"], reverse=True)
    signals["recent"] = signals["recent"][:5]
    return signals


def canonical_calm(raw):
    """Map either calm-tool vocabulary onto one id. None if unrecognised."""
    key = str(raw).strip().lower()
    key = CALM_ALIASES.get(key, key)
    return key if key in CALM_TOOLS else None


def pick_story(stories, task, dominant):
    """A story the classroom already owns that fits the trigger, if any.

    Task match is checked across every story before falling back to emotion,
    because "read this before the activity that sets them off" is advice and
    "read a story about being scared to a scared child" is barely more than a
    restatement. The emotion fallback matches the *dominant* emotion only -
    matching anything in the mix fired on 1,691 of 2,842 students, which is the
    definition of a recommendation nobody reads.
    """
    task_key = (task or "").lower()
    if task_key:
        for story in stories:
            title = (story.get("title") or "").lower()
            for cue, task_words, _ in STORY_CUES:
                if cue in title and any(word in task_key for word in task_words):
                    return story, "task"
    if dominant:
        for story in stories:
            title = (story.get("title") or "").lower()
            for cue, _, cue_emotions in STORY_CUES:
                if cue in title and dominant in cue_emotions:
                    return story, "emotion"
    return None, None


# ==============================================================================
# recommendation rules
# ==============================================================================

def rec(rule_id, category, rank, headline, detail, evidence, sources):
    return {"id": rule_id, "category": category, "rank": rank,
            "headline": headline, "detail": detail,
            "evidence": evidence, "sources": sources}


def build_recommendations(ctx):
    """Every rule that fires for one student, unranked and uncapped."""
    out = []
    summary = ctx["summary"]
    name = ctx["display_name"]
    tasks = ctx["tasks"]
    top_task = tasks[0] if tasks else None
    emotions = ctx["emotion_mix"]
    dominant = ctx["dominant_emotion"]
    notes = ctx["notes"]
    flagged_total = sum(emotions.values())

    # -- escalation: the case is bigger than a classroom strategy -------------
    alerts = [s for s in ctx["streaks"] if s["channel"] == "alert"]
    acute = [s for s in alerts if s["type"] == "same_day_cluster"]

    if len(alerts) >= 2:
        spans = ", ".join(f"{s['start_ts'][:10]} to {s['end_ts'][:10]}"
                          for s in alerts[:3])
        out.append(rec(
            "escalate_repeat_episodes", "escalate", 10,
            "Bring this one to your support team",
            f"{name} has {len(alerts)} separate alert episodes in the last 30 days, "
            f"not one long stretch. Distress that returns after a recovery is the "
            f"pattern classroom-level strategies tend not to reach on their own.",
            [f"{len(alerts)} distinct alert episodes: {spans}",
             f"{summary['recent_negative_share']:.0%} of recent check-ins negative"],
            ["insights"]))

    if acute:
        worst = max(acute, key=lambda s: s["length"])
        out.append(rec(
            "escalate_acute_cluster", "escalate", 11,
            f"Find out what happened on {worst['start_ts'][:10]}",
            f"{worst['length']} negative check-ins inside that one school day. A "
            f"cluster that tight usually has a single cause behind it - an "
            f"incident, illness, or something that changed at home - rather than a "
            f"pattern that built up over weeks.",
            [f"{worst['length']} negatives between {worst['start_ts'][11:16]} and "
             f"{worst['end_ts'][11:16]}",
             "emotions: " + ", ".join(worst["emotions"])],
            ["insights"]))

    long_run = [s for s in alerts if s["length"] >= 6 and s["distinct_days"] >= 3]
    if long_run and not acute and len(alerts) < 2:
        worst = max(long_run, key=lambda s: s["length"])
        out.append(rec(
            "escalate_long_streak", "escalate", 12,
            "Sustained run - worth a second pair of eyes",
            f"{worst['length']} negative check-ins in a row across "
            f"{worst['distinct_days']} days, with nothing positive in between. It "
            f"has persisted across overnight resets, which is what separates a "
            f"rough patch from a bad day.",
            [f"{worst['length']} consecutive negatives, {worst['start_ts'][:10]} "
             f"to {worst['end_ts'][:10]}"],
            ["insights"]))

    # -- antecedent: the strongest thing a teacher can actually change --------
    if top_task:
        corroborated = [n for n in notes["named_trigger"] + notes["needed_prompt"]
                        if n["task"].lower() in top_task["task"].lower()
                        or top_task["task"].lower() in n["task"].lower()]
        evidence = [f"{top_task['hits']} of the {top_task['exposure']} check-ins "
                    f"around {top_task['task']} were flagged "
                    f"({top_task['rate']:.0%})",
                    f"{name}'s check-ins go badly {top_task['usual_rate']:.0%} of "
                    f"the time overall - {top_task['lift']}x more likely here"]
        if top_task["skipped"] >= MIN_SKIPPED:
            evidence.append(f"{top_task['skipped']} {top_task['task']} tasks skipped "
                            f"in the same period")
        if corroborated:
            evidence.append(f"your note of {corroborated[0]['ts'][:10]} names "
                            f"{corroborated[0]['task']} too")
        detail = (f"{top_task['rate']:.0%} of {name}'s check-ins around "
                  f"{top_task['task']} were flagged, against "
                  f"{top_task['usual_rate']:.0%} across their day as a whole. "
                  f"Preview it on the schedule before it starts, or move it next "
                  f"to something they finish well.")
        if corroborated:
            detail += " You already wrote this down once - the data agrees with you."
        out.append(rec(
            "antecedent_task", "antecedent", 20 if corroborated else 21,
            f"{top_task['task']} is where this shows up",
            detail, evidence,
            ["task_log", "notes"] if corroborated else ["task_log"]))

    if len(tasks) > 1:
        second = tasks[1]
        out.append(rec(
            "antecedent_second_task", "antecedent", 26,
            f"{second['task']} is a secondary cluster",
            f"Smaller than {top_task['task']} but still above {name}'s baseline. "
            f"Worth watching once the first one is addressed rather than changing "
            f"both at once - two changes at a time makes it impossible to tell "
            f"which one worked.",
            [f"{second['hits']} of {second['exposure']} check-ins around "
             f"{second['task']} flagged ({second['rate']:.0%} against a usual "
             f"{second['usual_rate']:.0%})"],
            ["task_log"]))

    if ctx["time"] and not top_task:
        window = ctx["time"]
        out.append(rec(
            "antecedent_time", "antecedent", 24,
            f"It is concentrated {window['phrase']}",
            f"{window['rate']:.0%} of {name}'s check-ins {window['phrase']} were "
            f"flagged, against {window['usual_rate']:.0%} across the whole day - "
            f"so this is the time of day talking, not the timetable. No single "
            f"activity accounts for them. Look at what that stretch has in "
            f"common: length, noise level, staffing, or how long it has been "
            f"since a break.",
            [f"{window['hits']} of {window['exposure']} check-ins "
             f"{window['phrase']} flagged ({window['rate']:.0%})",
             f"{window['excess_points']} points above {name}'s own overall rate "
             f"of {window['usual_rate']:.0%}"],
            ["insights", "task_log"]))

    if "tired_after_lunch" in notes["flags"] and ctx["time"] \
            and ctx["time"]["bucket"] == "afternoon":
        out.append(rec(
            "antecedent_fatigue", "antecedent", 25,
            "Fatigue, not just mood",
            f"The flags cluster in the afternoon and your notes already record "
            f"{name} being tired after lunch. Shortening the afternoon schedule is "
            f"a lever you have already found works for this student.",
            [f"{ctx['time']['share']:.0%} of flagged check-ins after 1pm",
             "notes record post-lunch tiredness"],
            ["notes", "task_log"]))

    # -- regulation: what to hand the student in the moment -------------------
    if dominant:
        used = ctx["calm_used"]
        worked_after = [n["task"] for n in notes["calm_worked"]]
        options = EMOTION_TOOLS.get(dominant, ())
        untried = [t for t in options if t not in used]
        choice = untried[0] if untried else (options[0] if options else None)
        if choice:
            spec = CALM_TOOLS[choice]
            read = EMOTION_READS.get(dominant, "distress")
            if untried:
                lead = f"{name} has not opened {spec['label']} yet"
                why = (f"{dominant.capitalize()} usually reads as {read}, and "
                       f"{spec['label']} targets that directly.")
            else:
                lead = f"Go back to {spec['label']}"
                why = (f"{name} has already tried every calm tool that fits "
                       f"{dominant}, so the gap is not which tool - it is reaching "
                       f"one before the check-in rather than after it.")
            evidence = [f"{emotions[dominant]} of {flagged_total} flagged check-ins "
                        f"are '{dominant}'",
                        f"calm corner: {len(used)} of {len(CALM_TOOLS)} tools tried"]
            if worked_after:
                evidence.append(f"notes record the calm corner working after "
                                f"{worked_after[0]}")
            timing = (f" Offer it when {top_task['task']} is coming up, not after it "
                      f"has gone wrong." if top_task else
                      " Offer it before the check-in, not as a repair afterwards.")
            caveat = EMOTION_CAVEATS.get(dominant)
            out.append(rec(
                "regulation_tool", "regulation", 30,
                f"{spec['emoji']} {lead}",
                why + timing + (f" {caveat}" if caveat else ""),
                evidence, ["student", "insights"]))

    # -- communication: the flag may be a vocabulary gap ----------------------
    emo_count = ctx["emo_count"]
    aac_count = ctx["aac_count"]
    gap_emotions = emotions.get("confused", 0) + emotions.get("overwhelmed", 0)
    if emo_count and gap_emotions and aac_count / emo_count < AAC_LOW_RATIO:
        out.append(rec(
            "communication_aac", "communication", 35,
            "This may be a communication gap, not a mood",
            f"{name} logs feelings far more often than they use AAC to say anything "
            f"about them. Confusion and overwhelm are the two flags most likely to "
            f"be an instruction that did not land. Try modelling two AAC phrases for "
            f"asking for help"
            + (f" before {top_task['task']}." if top_task else "."),
            [f"{aac_count} AAC uses against {emo_count} emotion check-ins "
             f"({aac_count / emo_count:.0%})",
             f"{gap_emotions} flagged check-ins are confused or overwhelmed"],
            ["student"]))

    # -- story: point at something the classroom already owns -----------------
    story, matched_on = pick_story(
        ctx["stories"], top_task["task"] if top_task else None, dominant)
    if story:
        target = top_task["task"] if matched_on == "task" else dominant
        out.append(rec(
            "story_assign", "story", 40,
            f"Assign \"{story['title']}\"",
            f"Your classroom already has this story. Reading it ahead of time is the "
            f"cheapest version of previewing "
            + (f"{target}." if matched_on == "task"
               else f"what tends to set {name} off."),
            [f"matched on {'the correlated activity' if matched_on == 'task' else dominant}",
             f"{len(story.get('pages') or [])} pages, already in this classroom"],
            ["stories"]))

    # -- motivation -----------------------------------------------------------
    goal = ctx["token_goal"]
    if goal and ctx["tokens"] is not None and ctx["tokens"] < goal \
            and "token_goal_met" not in notes["flags"]:
        out.append(rec(
            "motivation_tokens", "motivation", 45,
            "The token board is out of reach right now",
            f"{name} is at {ctx['tokens']} of {goal} tokens and has not hit the goal "
            f"in the last 30 days of notes. A reward that never arrives stops being "
            f"a reward - consider a smaller interim goal until the pattern settles.",
            [f"{ctx['tokens']}/{goal} tokens toward \"{ctx['token_reward']}\"",
             "no token-goal note in the last 30 days"],
            ["student", "notes"]))

    # -- documentation --------------------------------------------------------
    if notes["count"] == 0:
        out.append(rec(
            "document_note", "document", 50,
            "Nothing on file for this period",
            f"There are no notes for {name} in the window these flags cover. If this "
            f"goes to a support team or an IEP meeting, the check-ins alone will not "
            f"carry the context you have in your head.",
            ["0 notes in the last 30 days",
             f"{summary['streak_count']} flagged patterns over the same window"],
            ["notes"]))
    elif notes["recent"]:
        out.append(rec(
            "document_context", "document", 52,
            "Your notes already have context on this",
            f"{notes['count']} note{'' if notes['count'] == 1 else 's'} on {name} in "
            f"this window. Check whether the most recent one still describes what "
            f"you are seeing.",
            [n["text"] for n in notes["recent"][:2]],
            ["notes"]))

    # -- positive channel -----------------------------------------------------
    if summary["channel"] == "positive":
        best = max((s for s in ctx["streaks"] if s["type"] == "repeated"),
                   key=lambda s: s["length"], default=None)
        if best:
            out.append(rec(
                "celebrate", "celebrate", 5,
                "Say this one out loud",
                f"{name} logged \"{best['emotion']}\" {best['length']} check-ins in a "
                f"row across {best['distinct_days']} days. It is here because good "
                f"stretches are worth naming to a student, and because it is the "
                f"baseline you compare a future flag against.",
                [f"{best['length']}x {best['emotion']}, {best['start_ts'][:10]} to "
                 f"{best['end_ts'][:10]}"],
                ["insights"]))

    # -- watch channel default ------------------------------------------------
    if summary["channel"] == "watch" and not tasks:
        reasons = ", ".join(summary["watch_reasons"])
        out.append(rec(
            "monitor_watch", "monitor", 48,
            "Watch, do not act yet",
            f"{name} tripped {reasons} but never four negatives in a row. That is a "
            f"pattern worth knowing about, not one worth intervening on - the soft "
            f"signal is here so that if it does turn into a streak, you already know "
            f"the history.",
            [f"trigger: {reasons}",
             f"{summary['recent_negative_share']:.0%} of recent check-ins negative"],
            ["insights"]))

    return out


def rank_and_cap(recs, channel):
    """Order by rank and cut to the channel's cap."""
    limit = {"alert": MAX_RECOMMENDATIONS,
             "watch": WATCH_RECOMMENDATIONS,
             "positive": POSITIVE_RECOMMENDATIONS}[channel]
    recs.sort(key=lambda r: (r["rank"], r["id"]))
    return recs[:limit]


def headline_reason(summary, streaks, tasks):
    """One line stating why this student is on the list at all."""
    alerts = [s for s in streaks if s["channel"] == "alert"]
    if alerts:
        worst = max(alerts, key=lambda s: s["length"])
        if worst["type"] == "same_day_cluster":
            base = (f"{worst['length']} negative check-ins in one day "
                    f"({worst['start_ts'][:10]})")
        else:
            base = (f"{worst['length']} negative check-ins in a row over "
                    f"{worst['distinct_days']} days")
        if len(alerts) > 1:
            base += f", across {len(alerts)} separate episodes"
    elif summary["channel"] == "watch":
        base = (f"{summary['recent_negative_share']:.0%} of recent check-ins "
                f"negative, never four in a row")
    else:
        best = max((s for s in streaks if s["type"] == "repeated"),
                   key=lambda s: s["length"], default=None)
        base = (f"{best['length']} \"{best['emotion']}\" check-ins in a row"
                if best else "positive pattern")
    if tasks:
        base += f" - clustered around {tasks[0]['task']}"
    return base


# ==============================================================================
# room-wide rules
# ==============================================================================

def build_room_recommendations(room, students):
    """Patterns that are only visible across a classroom, not within a student.

    A task that upsets one child is that child's antecedent. The same task
    upsetting a quarter of the flagged roster is a schedule problem, and it is
    the one recommendation here a teacher can act on once instead of per student.
    """
    out = []
    flagged = len(students)
    if flagged < ROOM_MIN_FLAGGED:
        return out

    task_students = Counter()
    for entry in students:
        for task in {t["task"] for t in entry["tasks"]}:
            task_students[task] += 1
    for task, count in task_students.most_common(2):
        share = count / flagged
        if count >= ROOM_TASK_MIN_STUDENTS and share >= ROOM_TASK_MIN_SHARE:
            out.append(rec(
                "room_task", "room", 10,
                f"{task} is a room-wide pattern, not a student one",
                f"{count} of {flagged} flagged students in this room cluster around "
                f"{task}. When the same activity shows up across that many "
                f"children, the activity is the thing to change - its length, its "
                f"position in the day, or the warning they get before it - rather "
                f"than each student's plan.",
                [f"{count} of {flagged} flagged students ({share:.0%})",
                 "measured against each student's own baseline, not the room's"],
                ["task_log"]))

    buckets = Counter(entry["time"]["bucket"] for entry in students if entry["time"])
    if buckets:
        bucket, count = buckets.most_common(1)[0]
        if count / flagged >= ROOM_TIME_MIN_SHARE and count >= ROOM_TASK_MIN_STUDENTS:
            phrase = next(p for n, _, _, p in TIME_BUCKETS if n == bucket)
            out.append(rec(
                "room_time", "room", 20,
                f"The room's hard stretch is {phrase}",
                f"{count} of {flagged} flagged students concentrate {phrase}. That "
                f"points at the shape of the day rather than at any one child - "
                f"check what the schedule asks of them in that window and where the "
                f"nearest break sits.",
                [f"{count} of {flagged} flagged students concentrate {phrase}"],
                ["insights", "task_log"]))

    worsening = [e for e in students if e["trajectory"]["direction"] == "worsening"]
    if len(worsening) >= ROOM_TASK_MIN_STUDENTS \
            and len(worsening) / flagged >= ROOM_TASK_MIN_SHARE:
        out.append(rec(
            "room_trend", "room", 30,
            "Several students are trending down together",
            f"{len(worsening)} of {flagged} flagged students are more negative in "
            f"the second half of the window than the first. Simultaneous decline "
            f"usually traces to something the room shares - a schedule change, a "
            f"staffing change, or a run of disrupted days.",
            [f"{len(worsening)} of {flagged} students worsening",
             "compares each student's own first half against their second"],
            ["insights"]))

    undocumented = [e for e in students
                    if e["note_count"] == 0 and e["channel"] == "alert"]
    if len(undocumented) >= ROOM_TASK_MIN_STUDENTS:
        out.append(rec(
            "room_documentation", "room", 40,
            f"{len(undocumented)} alert students have no notes",
            f"These are the students most likely to come up in a meeting, and the "
            f"ones with nothing written down. The check-in history will show the "
            f"pattern but not what you did about it.",
            [", ".join(e["display_name"] for e in undocumented[:6])
             + ("..." if len(undocumented) > 6 else "")],
            ["notes"]))

    out.sort(key=lambda r: r["rank"])
    return out


# ==============================================================================
# main
# ==============================================================================

def display_name(student_name, name_mode, student_id):
    """Respect the classroom's name mode, the way the app's dispName does.

    A room set to initials-only chose that for a reason - most often that the
    dashboard gets shown on a projector - and a recommendation that spells the
    name out anyway would quietly undo it.
    """
    name = student_name or "Student"
    if name_mode == "initials":
        return "".join(part[0].upper() for part in name.split() if part) or "S"
    if name_mode == "anon":
        return f"Student {str(student_id)[-4:]}"
    return name


def load_insights(path):
    """Flagged students keyed by id, plus their classroom codes."""
    records = {}
    for record in iter_jsonl(path):
        records[record["student_id"]] = record
    return records


def group_by_student(path, key):
    grouped = {}
    for record in iter_jsonl(path):
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def parse_args():
    """`--suffix` mirrors the detector's, so a run "as of" an earlier date reads
    that run's insights and writes beside it rather than over the current one."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suffix", default="",
                        help="read insights<suffix>.jsonl and append <suffix> to "
                             "every output filename, e.g. _prev")
    return parser.parse_args()


def main():
    args = parse_args()
    tag = args.suffix

    insights_path = os.path.join(DATA_DIR, f"insights{tag}.jsonl")
    meta_path = os.path.join(DATA_DIR, f"insights_meta{tag}.json")
    students_path = os.path.join(DATA_DIR, "students.jsonl")
    classrooms_path = os.path.join(DATA_DIR, "classrooms.jsonl")
    notes_path = os.path.join(DATA_DIR, "notes.jsonl")
    stories_path = os.path.join(DATA_DIR, "stories.jsonl")
    out_path = os.path.join(DATA_DIR, f"recommendations{tag}.jsonl")
    rooms_path = os.path.join(DATA_DIR, f"classroom_actions{tag}.jsonl")
    out_meta_path = os.path.join(DATA_DIR, f"recommendations_meta{tag}.json")

    if not os.path.exists(insights_path):
        raise SystemExit(f"{os.path.basename(insights_path)} not found - run "
                         f"detect_patterns.py{' --suffix ' + tag if tag else ''} first")

    with open(meta_path, encoding="utf-8") as f:
        detector_meta = json.load(f)
    reference = datetime.fromisoformat(detector_meta["reference_time"])
    cutoff = datetime.fromisoformat(detector_meta["cutoff"])
    print(f"detector run {detector_meta['generated_at']}  cutoff {cutoff.isoformat()}")

    insights = load_insights(insights_path)
    print(f"{len(insights)} flagged students to advise")

    notes_by_student = group_by_student(notes_path, "student_id")
    stories_by_room = group_by_student(stories_path, "classroom_code")
    rooms = {}
    for room in iter_jsonl(classrooms_path):
        rooms[room.get("code")] = room

    contexts = {}
    problems = Counter()
    scanned = 0
    with open(students_path, encoding="utf-8") as src:
        for line in src:
            line = line.strip()
            if not line:
                continue
            student = json.loads(line)
            scanned += 1
            record = insights.get(student.get("id"))
            if record is None:      # not flagged - nothing to recommend
                continue

            code = record.get("classroom_code")
            room = rooms.get(code, {})
            log = read_log(student, problems)
            recent = [e for e in log if e["ts"] >= cutoff]
            streaks = record["streaks"]
            flagged_at = flagged_positions(recent, streaks)
            tasks, task_stats = correlate_tasks(recent, flagged_at, student)
            mix = Counter(recent[i]["emotion"] for i in flagged_at
                          if recent[i]["emotion"])
            notes = read_notes(notes_by_student.get(student.get("id"), []), cutoff)
            calm_used = {c for c in (canonical_calm(t)
                                     for t in student.get("calm_done") or []) if c}

            contexts[student["id"]] = {
                "student_id": student["id"],
                "student_name": record.get("student_name"),
                "display_name": display_name(record.get("student_name"),
                                             record.get("name_mode"), student["id"]),
                "classroom_code": code,
                "summary": record["summary"],
                "channel": record["channel"],
                "priority": record["priority"],
                "streaks": streaks,
                "tasks": tasks,
                "task_stats": task_stats,
                "time": correlate_time(recent, flagged_at),
                "trajectory": trajectory(recent),
                "emotion_mix": mix,
                "dominant_emotion": mix.most_common(1)[0][0] if mix else None,
                "notes": notes,
                "note_count": notes["count"],
                "calm_used": calm_used,
                "stories": stories_by_room.get(code, []),
                "aac_count": student.get("aac_count") or 0,
                "emo_count": student.get("emo_count") or 0,
                "tokens": student.get("tokens"),
                "token_goal": room.get("token_goal"),
                "token_reward": room.get("token_reward") or "the class reward",
                "last_active": student.get("last_active"),
                # Recent check-ins, trimmed - the dashboard draws these as the
                # evidence strip under each student.
                "recent_strip": [{"ts": e["ts"].isoformat(),
                                  "emotion": e["emotion"]} for e in recent[-40:]],
            }

    print(f"scanned {scanned} students, built context for {len(contexts)}")

    by_room = {}
    categories = Counter()
    rule_counts = Counter()
    with_task = 0
    corroborated_total = 0
    with open(out_path, "w", encoding="utf-8") as dst:
        for ctx in contexts.values():
            recs = rank_and_cap(build_recommendations(ctx), ctx["channel"])
            for item in recs:
                categories[item["category"]] += 1
                rule_counts[item["id"]] += 1
                if item["id"] == "antecedent_task" and "notes" in item["sources"]:
                    corroborated_total += 1
            if ctx["tasks"]:
                with_task += 1

            record = {
                "student_id": ctx["student_id"],
                "student_name": ctx["student_name"],
                "display_name": ctx["display_name"],
                "classroom_code": ctx["classroom_code"],
                "channel": ctx["channel"],
                "priority": ctx["priority"],
                "why_flagged": headline_reason(ctx["summary"], ctx["streaks"],
                                               ctx["tasks"]),
                "trajectory": ctx["trajectory"],
                "task_clusters": ctx["tasks"],
                "time_cluster": ctx["time"],
                "emotion_mix": dict(ctx["emotion_mix"].most_common()),
                "note_count": ctx["note_count"],
                "recent_notes": ctx["notes"]["recent"],
                "calm_tools_used": sorted(ctx["calm_used"]),
                "recent_strip": ctx["recent_strip"],
                "summary": ctx["summary"],
                "recommendations": recs,
            }
            dst.write(json.dumps(record) + "\n")
            by_room.setdefault(ctx["classroom_code"], []).append(ctx)

    room_records = []
    room_rule_counts = Counter()
    for code, members in by_room.items():
        room = rooms.get(code, {})
        room_recs = build_room_recommendations(room, members)
        for item in room_recs:
            room_rule_counts[item["id"]] += 1
        members.sort(key=lambda c: (PRIORITY_RANK[c["priority"]],
                                    -c["summary"]["alert_streak_count"],
                                    c["student_id"]))
        priorities = Counter(c["priority"] for c in members)
        room_records.append({
            "classroom_code": code,
            "classroom_name": room.get("name"),
            "teacher_id": room.get("teacher_id"),
            "teacher_name": room.get("teacher_name"),
            "school_name": room.get("school_name"),
            "name_mode": room.get("name_mode"),
            "token_reward": room.get("token_reward"),
            "token_goal": room.get("token_goal"),
            "flagged_student_count": len(members),
            "alert_student_count": sum(1 for c in members if c["channel"] == "alert"),
            "watch_student_count": sum(1 for c in members if c["channel"] == "watch"),
            "positive_student_count": sum(1 for c in members
                                          if c["channel"] == "positive"),
            "by_priority": dict(priorities),
            "high_priority_count": priorities["high"],
            "undocumented_alert_count": sum(1 for c in members
                                            if c["channel"] == "alert"
                                            and c["note_count"] == 0),
            "room_recommendations": room_recs,
            "student_order": [c["student_id"] for c in members],
        })

    room_records.sort(key=lambda r: (-r["high_priority_count"],
                                     -r["alert_student_count"],
                                     r["classroom_code"]))
    with open(rooms_path, "w", encoding="utf-8") as f:
        for record in room_records:
            f.write(json.dumps(record) + "\n")

    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "detector_run": detector_meta["generated_at"],
        "reference_time": reference.isoformat(),
        "cutoff": cutoff.isoformat(),
        "config": {
            "task_window_minutes": TASK_WINDOW_MINUTES,
            "min_task_hits": MIN_TASK_HITS,
            "min_task_excess": MIN_TASK_EXCESS,
            "task_max_p": TASK_MAX_P,
            "min_task_exposure": MIN_TASK_EXPOSURE,
            "time_max_p": TIME_MAX_P,
            "time_min_excess": TIME_MIN_EXCESS,
            "aac_low_ratio": AAC_LOW_RATIO,
            "caps": {"alert": MAX_RECOMMENDATIONS,
                     "watch": WATCH_RECOMMENDATIONS,
                     "positive": POSITIVE_RECOMMENDATIONS},
            "room_task_min_students": ROOM_TASK_MIN_STUDENTS,
            "room_task_min_share": ROOM_TASK_MIN_SHARE,
        },
        "totals": {
            "students_advised": len(contexts),
            "classrooms": len(room_records),
            "recommendations": sum(rule_counts.values()),
            "students_with_task_cluster": with_task,
            "task_clusters_corroborated_by_notes": corroborated_total,
            "by_category": dict(categories.most_common()),
            "by_rule": dict(rule_counts.most_common()),
            "room_rules": dict(room_rule_counts.most_common()),
            "dropped_entries": dict(problems),
        },
    }
    with open(out_meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"{sum(rule_counts.values())} recommendations -> {out_path}")
    for name, count in rule_counts.most_common():
        print(f"    {name}: {count}")
    print(f"  {with_task} students have an activity cluster "
          f"({corroborated_total} corroborated by a teacher note)")
    print(f"{len(room_records)} classrooms -> {rooms_path}")
    for name, count in room_rule_counts.most_common():
        print(f"    {name}: {count} rooms")
    print(f"run metadata -> {out_meta_path}")
    if problems:
        print(f"  dropped entries: {dict(problems)}")


if __name__ == "__main__":
    main()
