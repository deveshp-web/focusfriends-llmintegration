"""The four detection rules, as pure functions.

WHAT "PURE" MEANS HERE AND WHY IT MATTERS
-----------------------------------------
Nothing in this file opens a file, prints, or reads a global. Every function
takes a :class:`~focusbridge.core.timeline.Timeline` and returns a list of
plain dictionaries. That is what makes the rules testable: a test builds a
seven-entry log by hand, calls the rule, and asserts on the result - no
fixtures, no temporary directories, no mocking.

It is also what makes them *reviewable*. A teacher who disagrees with a flag
should be able to be shown one function, twenty lines long, that produced it.

THE SHAPE EVERY RULE RETURNS
----------------------------
Each rule returns a list of "streak" dictionaries. Despite the name a streak is
just *one finding*, with a type, a channel, its extent in time, and the
check-ins behind it, so the stage above can treat all four rules identically::

    {"type": "negative", "channel": "alert", "valence": "negative",
     "start_ts": "...", "end_ts": "...", "length": 5, "distinct_days": 3,
     "span_days": 4, "emotions": ["sad", "angry", ...]}

HOW THE RULES AVOID DUPLICATING EACH OTHER'S WORK
-------------------------------------------------
Two of the four rules (``negative`` and ``same_day_cluster``) both need the
same thing first: the log broken into unbroken runs of bad check-ins, each with
its qualifying time windows worked out. The old code computed that **twice** -
once inside each rule, including a second full pass of the window scan purely to
ask "is this list empty?".

Here it is computed once by :func:`scan_negative_runs` and handed to both. The
saving is a constant factor, not a change of complexity class, but it is a
constant factor on the hottest loop in the project, and it removes the risk of
the two copies of that tricky windowing logic drifting apart.
"""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple, Sequence

from ..core.vocabulary import EMOTION_VALENCE, describe_valence_mix
from .settings import DEFAULT_SETTINGS, TRIGGER_CHANNELS


class NegativeRun(NamedTuple):
    """An unbroken stretch of bad check-ins, plus the windows that qualified.

    Attributes:
        start:   index of the first bad check-in, into the Timeline.
        end:     index of the last one, inclusive.
        windows: every ``(start, end)`` sub-window of this run that satisfies
                 *all* of the streak constraints. Empty means the run exists but
                 does not qualify as a streak - which is itself information the
                 same-day rule depends on.
    """

    start: int
    end: int
    windows: Sequence


# ---------------------------------------------------------------------------
# shared machinery
# ---------------------------------------------------------------------------

def qualifying_windows(timeline, start, end, settings=DEFAULT_SETTINGS):
    """Windows inside ``start..end`` that satisfy every streak constraint.

    All three constraints must hold at once, and each rules out a different
    false positive:

      ``streak_length``      enough check-ins to be a pattern rather than a
                             coincidence;
      ``streak_window_days`` close enough together to be one episode rather
                             than four bad days spread over a term;
      ``min_distinct_days``  spread over enough days to be more than one rough
                             morning.

    A window fully contained inside another is dropped, because a contained
    window can never touch more calendar days than its parent - so it can only
    ever be a weaker description of the same episode.

    Complexity: O(k) for a run of k check-ins. The two-pointer scan is O(k)
    amortised and each window's day count is an O(1) prefix-sum lookup - the
    old version recounted the days inside every window, making it O(k x w).
    """
    windows = []
    # Tracks the right edge of the last window we accepted *or* considered, so
    # a window that ends no later than the previous one is skipped as contained.
    # Note it advances even when the day-spread check rejects the window: the
    # containment argument is about extent, not about whether it qualified.
    furthest_end = start - 1

    for window_start, window_end in timeline.maximal_windows(
            settings.streak_window, lo=start, hi=end):
        long_enough = window_end - window_start + 1 >= settings.streak_length
        if not (long_enough and window_end > furthest_end):
            continue
        if timeline.distinct_days(window_start, window_end) >= settings.min_distinct_days:
            windows.append((window_start, window_end))
        furthest_end = window_end

    return windows


def scan_negative_runs(timeline, settings=DEFAULT_SETTINGS):
    """Break the log into unbroken runs of bad check-ins, with their windows.

    A run ends at the first check-in that is not negative - including an
    *unreadable* one, which is the whole reason unreadable check-ins are kept in
    the log rather than dropped (see
    :func:`~focusbridge.core.timeline.read_check_ins`).

    Complexity: O(n) over the whole log.
    """
    runs = []
    negatives = settings.negative_emotions
    run_start = None

    def close_run(run_end):
        """Finish the run that started at `run_start` and ends at `run_end`."""
        runs.append(NegativeRun(
            run_start, run_end,
            qualifying_windows(timeline, run_start, run_end, settings)))

    for index, check_in in enumerate(timeline.check_ins):
        if check_in.emotion in negatives:
            if run_start is None:
                run_start = index
            continue
        if run_start is not None:
            close_run(index - 1)
            run_start = None
    if run_start is not None:  # a run that reaches the end of the log
        close_run(len(timeline) - 1)

    return runs


def make_streak(timeline, start, end, streak_type, channel=None):
    """Build the finding dictionary for the range ``start..end``.

    The key order here is deliberate and load-bearing: these dictionaries are
    written straight to JSON, and keeping the order stable keeps the output
    files diffable between runs and between versions of this code.
    """
    emotions = [check_in.emotion for check_in in timeline.slice(start, end)]
    streak = {
        "type": streak_type,
        "channel": channel or TRIGGER_CHANNELS[streak_type],
        "valence": describe_valence_mix(emotions),
        "start_ts": timeline.times[start].isoformat(),
        "end_ts": timeline.times[end].isoformat(),
        "length": end - start + 1,
        "distinct_days": timeline.distinct_days(start, end),
        "span_days": timeline.span_days(start, end),
        "emotions": emotions,
    }
    if streak_type == "repeated":
        # A repeated streak is by definition all one emotion, so naming it makes
        # every downstream consumer's job easier than re-deriving it.
        streak["emotion"] = emotions[0]
    return streak


# ---------------------------------------------------------------------------
# RULE 1 - negative streak (alert channel)
# ---------------------------------------------------------------------------

def find_negative_streaks(timeline, runs=None, settings=DEFAULT_SETTINGS):
    """Four or more bad check-ins in a row. The core alert.

    ONE RECORD PER EPISODE, NOT PER WINDOW
    --------------------------------------
    The reported extent is the **whole unbroken run**, even when the run is
    longer than ``streak_window_days``. The window constraint decides *whether*
    a run counts; it does not chop what gets reported into week-sized pieces.

    This distinction was learned the hard way. Emitting each qualifying window
    as its own record split 523 students' single continuous run across up to 26
    records each. That misdescribed the episode - a fortnight of distress is one
    thing that happened, not twenty-six things - and it also inflated priority,
    because the summary counts records and two records read as two separate
    episodes.

    What actually tripped the rule is not thrown away: the *tightest* qualifying
    window is reported alongside as ``trigger_length`` / ``trigger_span_days``,
    so the evidence is still there without pretending to be the whole story.
    """
    if runs is None:
        runs = scan_negative_runs(timeline, settings)

    streaks = []
    for run in runs:
        if not run.windows:
            continue
        streak = make_streak(timeline, run.start, run.end, "negative")

        # The tightest window = the one packed into the least time, which is the
        # most persuasive evidence the run contained a real episode. `min` keeps
        # the first on a tie, which makes the choice deterministic.
        tightest = min(run.windows,
                       key=lambda window: timeline.span(window[0], window[1]))
        streak["trigger_length"] = tightest[1] - tightest[0] + 1
        streak["trigger_span_days"] = timeline.span_days(tightest[0], tightest[1])
        streaks.append(streak)
    return streaks


# ---------------------------------------------------------------------------
# RULE 2 - same-day cluster (watch channel, or alert when big enough)
# ---------------------------------------------------------------------------

def find_same_day_clusters(timeline, runs=None, settings=DEFAULT_SETTINGS):
    """Four or more bad check-ins inside one school day.

    Only for runs that raised no alert of their own. A run that already produced
    a negative streak is skipped, because the alert covers it and reporting both
    would tell a teacher about the same episode twice under two names.

    A cluster of ``same_day_alert_length`` or more is promoted from watch to
    alert: at that size it stops being a rough morning and becomes an incident,
    which usually has a single cause behind it that a teacher can go and find.
    """
    if not settings.enable_same_day_cluster:
        return []
    if runs is None:
        runs = scan_negative_runs(timeline, settings)

    clusters = []
    for run in runs:
        length = run.end - run.start + 1
        if length < settings.same_day_cluster_length or run.windows:
            continue

        # Group this run's check-ins by calendar day. Two things follow from the
        # timeline being sorted, and both are load-bearing:
        #
        #   1. Dates never go backwards, so a day's entries are *contiguous* and
        #      first-seen order is chronological order. A dict preserves
        #      insertion order, so the clusters come out in date order with no
        #      `sorted()` call.
        #   2. Because a day's entries are contiguous, the first and last index
        #      of a day fully describe it - which is what makes it correct to
        #      hand `make_streak` a *range* below rather than a list of indices.
        #      If the log were ever unsorted, that range would silently swallow
        #      check-ins from other days.
        by_day = {}
        for index in range(run.start, run.end + 1):
            by_day.setdefault(timeline[index].day, []).append(index)

        for indices in by_day.values():
            if len(indices) < settings.same_day_cluster_length:
                continue
            channel = ("alert" if len(indices) >= settings.same_day_alert_length
                       else "watch")
            clusters.append(make_streak(timeline, indices[0], indices[-1],
                                        "same_day_cluster", channel))
    return clusters


# ---------------------------------------------------------------------------
# RULE 3 - repeated emotion (positive channel)
# ---------------------------------------------------------------------------

def _is_reportable_repeat(timeline, start, settings):
    """True if a run starting here is of an emotion the repeat rule fires on."""
    if start is None:
        return False
    emotion = timeline[start].emotion
    return emotion is not None and EMOTION_VALENCE[emotion] in settings.repeated_valences


def find_repeated_streaks(timeline, settings=DEFAULT_SETTINGS):
    """The same *positive* emotion four or more times running. Good news.

    Kept in its own channel rather than suppressed, for two reasons: a good
    stretch is worth naming out loud to a student, and it is the baseline a
    future flag gets compared against.

    Neutral emotions are excluded by ``repeated_valences`` - four "okay" in a
    row is the definition of an unremarkable week.
    """
    windows = []
    run_start = None

    def close_run(run_end):
        if _is_reportable_repeat(timeline, run_start, settings):
            windows.extend(qualifying_windows(timeline, run_start, run_end, settings))

    for index, check_in in enumerate(timeline.check_ins):
        if check_in.emotion is None:
            # An unreadable check-in breaks the run, exactly as it breaks a
            # negative streak, and for the same reason: we cannot claim a run
            # continued through something we could not read.
            if run_start is not None:
                close_run(index - 1)
            run_start = None
            continue
        if run_start is not None and check_in.emotion == timeline[run_start].emotion:
            continue  # the run continues
        if run_start is not None:
            close_run(index - 1)
        run_start = index

    if run_start is not None:
        close_run(len(timeline) - 1)

    return [make_streak(timeline, start, end, "repeated") for start, end in windows]


# ---------------------------------------------------------------------------
# RULE 4 - density (watch channel)
# ---------------------------------------------------------------------------

def find_density_streaks(timeline, covered=(), settings=DEFAULT_SETTINGS):
    """A week that is mostly bad but never four in a row.

    The streak rule is precise and therefore brittle: one neutral check-in on
    the Wednesday breaks a run and the whole fortnight goes unreported. This
    rule is the safety net, and it sits in the watch channel because a softer
    signal deserves softer treatment.

    TWO SUBTLETIES, BOTH EASY TO MISREAD
    ------------------------------------
    **Overlapping windows are merged where the union still qualifies.** The same
    reasoning as the negative rule: one student's rough fortnight was arriving
    as up to 62 records. But merging is only allowed when the union *itself*
    clears the bar - two windows that each reach 70% can union into something
    that does not, and a record must never claim a density it does not have.

    So the merge is conditional, and the fallback is not a drop: when two
    windows overlap but their union would fall below the threshold, the second
    is appended as its own span and **the output contains two overlapping
    records**. That is deliberate - each one honestly describes a period that
    really was dense - but it does mean "density spans never overlap" is not a
    property you can rely on. On the current corpus 416 students have a pair
    like this.

    **A period is dropped only when it sits *entirely* inside an alert.**
    ``covered`` holds the time extents of this student's alert-channel findings,
    and the test is full containment, not any overlap. A run of consecutive bad
    check-ins is by definition also dense, so without this filter every alert
    would be shadowed by a duplicate watch record saying the same thing - but a
    density period that merely *straddles* an alert survives, because it is
    describing something wider than the alert did. On the current corpus 1,887
    density spans partially overlap an alert and are kept.

    Complexity: O(n) for the scan - the share of a window is a prefix-sum
    subtraction, where the old version summed the window on every step and made
    this the slowest rule in the detector at O(n x w). The ``covered`` filter
    afterwards is O(spans x alerts), and both are single digits per student.
    """
    if not settings.enable_density_trigger:
        return []

    minimum_share = settings.density_min_negative_share
    spans = []

    for start, end in timeline.maximal_windows(settings.density_window):
        if end - start + 1 < settings.density_min_entries:
            continue
        if timeline.negative_share(start, end) < minimum_share:
            continue
        if timeline.distinct_days(start, end) < settings.min_distinct_days:
            continue

        # Merge into the previous span when the two overlap AND the union is
        # still dense enough to be worth reporting.
        if spans and start <= spans[-1][1]:
            merged_end = max(spans[-1][1], end)
            if timeline.negative_share(spans[-1][0], merged_end) >= minimum_share:
                spans[-1][1] = merged_end
                continue
        spans.append([start, end])

    streaks = []
    for start, end in spans:
        window_start, window_end = timeline.times[start], timeline.times[end]
        if any(alert_start <= window_start and window_end <= alert_end
               for alert_start, alert_end in covered):
            continue
        streak = make_streak(timeline, start, end, "density")
        # Stated rather than left implicit: the merged extent can differ from
        # the individual windows that qualified, so the share is recomputed for
        # exactly what is being reported.
        streak["negative_share"] = round(timeline.negative_share(start, end), 3)
        streaks.append(streak)
    return streaks


# ---------------------------------------------------------------------------
# running all four
# ---------------------------------------------------------------------------

def find_all_streaks(timeline, settings=DEFAULT_SETTINGS):
    """Every finding for one student, from all four rules.

    **Order matters.** The alert-channel rules run first so that the density
    rule can be told what they already cover and stay quiet about it. This is
    the one place in the detector where the rules are not independent, so it is
    concentrated in four lines rather than hidden inside a rule.
    """
    runs = scan_negative_runs(timeline, settings)

    negative = find_negative_streaks(timeline, runs, settings)
    same_day = find_same_day_clusters(timeline, runs, settings)

    covered = [(_as_datetime(streak["start_ts"]), _as_datetime(streak["end_ts"]))
               for streak in negative + same_day if streak["channel"] == "alert"]

    return (negative
            + same_day
            + find_repeated_streaks(timeline, settings)
            + find_density_streaks(timeline, covered, settings))


def _as_datetime(iso_text):
    """Parse an ISO timestamp a rule just wrote back into a ``datetime``.

    Comparing extents as datetimes rather than as strings is a little more work
    and considerably harder to get wrong: string comparison only happens to
    agree with chronological order while every timestamp has identical
    formatting, and nothing enforces that.
    """
    return datetime.fromisoformat(iso_text)
