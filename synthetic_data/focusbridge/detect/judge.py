"""Sampling the borderline cases, so an outside reviewer can argue with the rules.

WHY THIS EXISTS
---------------
Every threshold in ``settings.py`` is a judgement call, and a judgement call
that nobody ever revisits quietly becomes a fact. This module collects the cases
that sit *right on* a threshold - the ones where the rule was closest to
deciding the other way - and writes a sample of them to ``judge_cases.jsonl``
for an external LLM-as-a-judge review.

Two audit rounds run this way shaped the current rules. ``judge_prompt.md`` is
the system of record for what was proposed, what shipped and what was declined.

THE CASE TYPES, AND THE QUESTION EACH ONE ASKS
----------------------------------------------
    at_window_edge          "is a 7-day window the right width?"
    barely_multi_day        "is 2 distinct days really enough to be a pattern?"
    repeated_positive       "is celebrating a good streak useful or noise?"
    same_day_only           "should a rough morning ever reach the alert queue?"
    one_short               "is 4 in a row the right bar, or should 3 count?"
    one_short_corroborated  "...and does a bad wider week change that answer?"
    dense_but_unflagged     "what are we missing entirely?"
    watch_only              "is the watch tier pitched right at all?"

A NOTE ON SAMPLING
------------------
Every matching case is *counted*, but only ``judge_cases_per_type`` of each are
written out. The count is the honest population size; the sample is what a human
or a model can actually read. Both are recorded, so nobody mistakes "we looked
at 12" for "there were 12".
"""

from __future__ import annotations

import random

from ..core.jsonio import dumps_line, open_text_writer
from ..core.vocabulary import EMOTION_VALENCE
from .rules import scan_negative_runs
from .settings import DEFAULT_SETTINGS

#: The buckets, in the order they are created. Named here rather than inline so
#: the pipeline and this module cannot disagree about which buckets exist.
CASE_TYPES = (
    "at_window_edge",
    "barely_multi_day",
    "repeated_positive",
    "same_day_only",
    "one_short",
    "one_short_corroborated",
    "dense_but_unflagged",
    "watch_only",
)


def new_case_collection():
    """A fresh empty bucket per case type."""
    return {name: [] for name in CASE_TYPES}


def _describe(timeline, start, end):
    """Human-readable check-ins for a range, for a reviewer to read directly."""
    return [f"{check_in.at.isoformat()} {check_in.emotion or 'unreadable'}"
            for check_in in timeline.slice(start, end)]


def collect_judge_cases(student, timeline, streaks, cases,
                        runs=None, settings=DEFAULT_SETTINGS):
    """Add this student's borderline cases to the shared collection.

    Args:
        student:  the raw row, for the identifying fields.
        timeline: this student's recent check-ins.
        streaks:  the findings the rules produced for them.
        cases:    the shared collection to append into, owned by the caller so
                  one collection accumulates across the whole corpus.
        runs:     negative runs, if the caller already computed them. Passed in
                  to avoid a third full scan of the same log.
        settings: the thresholds being probed.
    """
    if runs is None:
        runs = scan_negative_runs(timeline, settings)

    identity = {"student_id": student.get("id"),
                "classroom_code": student.get("classroom_code")}

    _collect_threshold_edges(identity, streaks, cases, settings)
    _collect_near_misses(identity, timeline, runs, cases, settings)
    _collect_watch_only(identity, timeline, streaks, cases)
    _collect_dense_but_unflagged(identity, timeline, streaks, cases, settings)


def _collect_threshold_edges(identity, streaks, cases, settings):
    """Findings that only just cleared a bar - "was this right to fire?"."""
    for streak in streaks:
        # These two buckets probe the *alert* thresholds specifically, so watch-
        # and positive-channel findings are skipped. Density windows in
        # particular are 7 days wide by construction, so including them would
        # swamp `at_window_edge` with cases that are not evidence of anything.
        if streak["channel"] == "alert":
            if streak["span_days"] >= settings.streak_window_days:
                cases["at_window_edge"].append({**identity, "streak": streak})
            if (streak["distinct_days"] == settings.min_distinct_days
                    and streak["span_days"] <= 1):
                cases["barely_multi_day"].append({**identity, "streak": streak})

        if (streak["type"] == "repeated"
                and EMOTION_VALENCE[streak["emotion"]] == "positive"):
            cases["repeated_positive"].append({**identity, "streak": streak})


def _collect_near_misses(identity, timeline, runs, cases, settings):
    """Runs that failed by exactly one check-in or one day - "should this fire?".

    Two different near misses, deliberately kept apart:

      * ``same_day_only`` - long enough, but all on one day. Clears every bar
        except the day-spread rule.
      * ``one_short`` - one check-in short of a streak. Split by whether the
        wider week corroborates it, because an audit proposed promoting only the
        corroborated ones and that proposal needs its own case count to be
        judged on.
    """
    # The student's overall bad-check-in rate over the recency window, used to
    # decide whether a near miss is corroborated by the week around it.
    overall_share = (timeline.negative_share(0, len(timeline) - 1)
                     if len(timeline) else 0.0)

    for run in runs:
        length = run.end - run.start + 1
        distinct_days = timeline.distinct_days(run.start, run.end)

        if (length >= settings.streak_length
                and distinct_days < settings.min_distinct_days):
            cases["same_day_only"].append({
                **identity, "entries": _describe(timeline, run.start, run.end)})

        if (length == settings.streak_length - 1
                and distinct_days >= settings.min_distinct_days):
            corroborated = overall_share >= settings.density_min_negative_share
            bucket = "one_short_corroborated" if corroborated else "one_short"
            cases[bucket].append({
                **identity,
                "entries": _describe(timeline, run.start, run.end),
                "recent_negative_share": round(overall_share, 3),
            })


def _collect_watch_only(identity, timeline, streaks, cases):
    """Students whose entire case is watch-channel.

    The audit specifically asked us not to assume this tier is pitched
    correctly, so these students are sampled as a group rather than only when
    they sit near some individual threshold.
    """
    if streaks and all(streak["channel"] == "watch" for streak in streaks):
        overall_share = (timeline.negative_share(0, len(timeline) - 1)
                         if len(timeline) else 0.0)
        cases["watch_only"].append({
            **identity,
            "reasons": sorted({streak["type"] for streak in streaks}),
            "recent_negative_share": round(overall_share, 3),
            "streaks": streaks,
        })


def _collect_dense_but_unflagged(identity, timeline, streaks, cases, settings):
    """Mostly-bad weeks that produced no finding at all - the false negatives.

    This is the most valuable bucket, because it is the only one that asks what
    the rules *miss* rather than what they get marginally wrong.

    The `if streaks: return` guard is not just tidiness. In the old code this
    condition was tested inside the window loop, so a flagged student still paid
    for a full sliding-window scan whose result could not possibly be used - and
    most students in this corpus are flagged.
    """
    if streaks:
        return

    for start, end in timeline.maximal_windows(settings.density_window):
        if end - start + 1 < settings.density_min_entries:
            continue
        if timeline.negative_share(start, end) >= settings.density_min_negative_share:
            cases["dense_but_unflagged"].append({
                **identity, "entries": _describe(timeline, start, end)})
            # One example per student is plenty: a second window from the same
            # fortnight would be the same evidence twice, and would crowd out
            # another student's case in the sample.
            break


def write_judge_cases(path, cases, settings=DEFAULT_SETTINGS):
    """Write a reproducible sample of each bucket. Returns the true population sizes.

    The random number generator is seeded from settings and created **once**,
    outside the loop, so the whole file is a single deterministic draw: run this
    twice on the same corpus and two reviewers see exactly the same cases and
    can argue about the same evidence.
    """
    rng = random.Random(settings.judge_seed)
    limit = settings.judge_cases_per_type

    with open_text_writer(path) as handle:
        # `sorted` so bucket order does not depend on dict insertion order,
        # which would make the sample depend on the order the buckets happened
        # to be created in.
        for case_type in sorted(cases):
            pool = cases[case_type]
            sample = pool if len(pool) <= limit else rng.sample(pool, limit)
            for case in sample:
                handle.write(dumps_line({"case_type": case_type,
                                         "population": len(pool), **case}))

    return {name: len(pool) for name, pool in sorted(cases.items())}
