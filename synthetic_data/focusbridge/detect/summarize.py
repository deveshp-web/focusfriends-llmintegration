"""Rolling one student's findings up into a single, sortable verdict.

WHY A SUMMARY AND NOT JUST THE FINDINGS
---------------------------------------
A student can trip four rules at once. A dashboard cannot sort on "four rules",
and a teacher with 23 flagged students cannot read four findings each and work
out who to see first. So every student gets exactly one channel, one priority
and a handful of pre-computed counts, and every screen in the project sorts on
those instead of re-deriving them - which is also what stops two screens
disagreeing about who is at the top of the list.

THE ONE RULE THAT MATTERS MOST
------------------------------
**Only alert-channel findings can raise priority.** A student whose entire case
is watch-channel stays at "watch" no matter how many soft signals they trip.
That is what makes it safe to keep adding gentler rules: a new soft signal can
add context, but it can never inflate the queue a teacher is expected to read.
"""

from __future__ import annotations

import hashlib

from ..core.vocabulary import NEGATIVE_EMOTIONS
from .settings import DEFAULT_SETTINGS


def summarize(streaks, recent, settings=DEFAULT_SETTINGS):
    """One student's findings reduced to a verdict a dashboard can sort on.

    Args:
        streaks: every finding for this student, from all four rules.
        recent:  the student's check-ins inside the recency window - needed for
                 the denominators, since "40% of check-ins were bad" requires
                 knowing how many there were, including the good ones.
        settings: the thresholds; see :class:`DetectionSettings`.

    Returns:
        A dict whose keys are stable and written straight to ``insights.jsonl``.
    """
    alerts = [streak for streak in streaks if streak["channel"] == "alert"]
    watch = [streak for streak in streaks if streak["channel"] == "watch"]

    negative_entries = sum(1 for check_in in recent
                           if check_in.emotion in NEGATIVE_EMOTIONS)

    return {
        "channel": _channel_for(alerts, watch),
        "priority": _priority_for(alerts, watch, settings),
        "streak_count": len(streaks),
        "alert_streak_count": len(alerts),
        "watch_streak_count": len(watch),
        # Sorted so the dashboard's "why is this on the watch list?" text is in
        # a stable order between runs.
        "watch_reasons": sorted({streak["type"] for streak in watch}),
        "positive_streak_count": len(streaks) - len(alerts) - len(watch),
        "longest_alert_streak": max((streak["length"] for streak in alerts), default=0),
        # How many separate days the alerts touch, counted from the first and
        # last date of each - enough for an at-a-glance sense of scale without
        # walking every check-in again.
        "alert_days": len({date for streak in alerts
                           for date in (streak["start_ts"][:10], streak["end_ts"][:10])}),
        "first_flag_ts": min(streak["start_ts"] for streak in streaks),
        "last_flag_ts": max(streak["end_ts"] for streak in streaks),
        "recent_entry_count": len(recent),
        "recent_negative_share": (round(negative_entries / len(recent), 3)
                                  if recent else 0.0),
    }


def _channel_for(alerts, watch):
    """The single channel this student's whole case belongs to.

    Worst-first: one alert finding puts the student in the alert channel however
    much else is going well.
    """
    if alerts:
        return "alert"
    if watch:
        return "watch"
    return "positive"


def _priority_for(alerts, watch, settings):
    """How urgent this student is, from the alert-channel findings alone.

    ``high`` means one of two things, and both are statements about the episode
    rather than about the count of findings:

      * **Two or more separate alert episodes.** Distress that returns after a
        recovery is a different, harder problem than one long stretch, and it is
        the one classroom-level strategies tend not to reach.
        (This test is only meaningful because the negative rule reports one
        record per episode. When it reported one record per *window*, "two
        alerts" could just mean "one long run seen through two windows", and
        priority was inflated accordingly.)
      * **One long, deep episode** - long enough and spread over enough days
        that it survived several overnight resets.
    """
    if not alerts:
        return "watch" if watch else "info"

    separate_episodes = len(alerts) >= 2
    sustained = any(streak["length"] >= settings.high_priority_min_length
                    and streak["distinct_days"] >= settings.high_priority_min_days
                    for streak in alerts)
    return "high" if separate_episodes or sustained else "medium"


def compute_flag_id(student_id, streaks):
    """A stable id for "this student, with exactly this set of findings".

    WHAT IT IS FOR
    --------------
    Teachers mark flags as reviewed or dismissed (see ``review/store.py``). Those
    marks live in their own file and are joined back on this id, so re-running
    the detector never erases review history and reviewing a flag never touches
    detection output - two concerns kept deliberately apart.

    WHY IT DEPENDS ON THE FINDINGS AND NOT JUST THE STUDENT
    -------------------------------------------------------
    Two runs produce the same id only when the findings are identical. That is
    the point: a genuinely **new** episode gets a **new** id, so dismissing last
    week's streak can never silently dismiss a fresh one that started today. The
    cost is that a flag re-appears for review when its extent shifts by one
    check-in, which is the right trade - a repeated question is a nuisance, a
    silently swallowed alert is a failure.

    The fingerprint is sorted before hashing so that the order the rules
    happened to run in cannot change the id.
    """
    fingerprint = "|".join(sorted(
        f"{streak['type']}:{streak['start_ts']}:{streak['end_ts']}:{streak['length']}"
        for streak in streaks
    ))
    digest = hashlib.sha256(f"{student_id}::{fingerprint}".encode()).hexdigest()
    # 12 hex characters = 48 bits. Short enough to read out loud or type into a
    # CLI, and with only a few thousand flags per run the chance of a collision
    # is negligible.
    return digest[:12]
