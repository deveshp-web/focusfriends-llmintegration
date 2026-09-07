"""The per-student recommendation rules.

HOW A RULE IS SHAPED
--------------------
Every rule is one small function with the same signature::

    def rule_something(context, settings) -> dict | None

It looks at the assembled :class:`~focusbridge.recommend.context.StudentContext`
and either returns one recommendation or returns ``None`` to stay quiet. Rules
never look at each other, never write files, and never mutate the context.

That uniformity is what makes the set extensible: adding a rule means writing a
function and adding its name to :data:`RULES`. Nothing else in the project
changes. The old code was one 250-line function where every rule shared local
variables with every other, so adding a rule meant reading all of them first.

WHAT EVERY RECOMMENDATION MUST CARRY
------------------------------------
    headline  what to do, in a few words
    detail    why, in plain language a teacher can disagree with
    evidence  the specific numbers that fired the rule
    sources   which files the evidence came from, so it can be checked
    rank      where it sits in the reading order (lower = shown first)

The ``evidence`` field is the non-negotiable one. Advice a teacher cannot audit
is advice a teacher eventually learns to skip.

THE RANKING SCALE
-----------------
Ranks are spaced out in tens so a new rule can be slotted between two existing
ones without renumbering anything::

     5  celebrate     good news first - it costs nothing and it is quick
    10  escalate      this is bigger than a classroom strategy
    20  antecedent    the strongest thing a teacher can actually change
    30  regulation    what to hand the student in the moment
    35  communication the flag may be a vocabulary gap, not a mood
    40  story         point at something the classroom already owns
    45  motivation    the reward system may be misconfigured
    48  monitor       watch, do not act yet
    50  document      what is missing from the record
"""

from __future__ import annotations

from .phrasing import CALM_TOOLS, EMOTION_CAVEATS, EMOTION_READS, EMOTION_TOOLS
from .settings import DEFAULT_SETTINGS


def recommendation(rule_id, category, rank, headline, detail, evidence, sources):
    """Build one recommendation record.

    A helper rather than a dataclass because these go straight to JSON and the
    key order is part of the output format; a function keeps that in one place.
    """
    return {"id": rule_id, "category": category, "rank": rank,
            "headline": headline, "detail": detail,
            "evidence": evidence, "sources": sources}


# ---------------------------------------------------------------------------
# escalation - this is bigger than a classroom strategy
# ---------------------------------------------------------------------------

def rule_escalate_repeat_episodes(context, settings):
    """Two or more separate alert episodes: distress that came back.

    The distinction that matters is *separate* episodes rather than one long
    one. Distress that returns after a recovery is the pattern classroom-level
    strategies tend not to reach, so it is the clearest case for asking someone
    else to look.
    """
    alerts = context.alert_streaks
    if len(alerts) < 2:
        return None

    spans = ", ".join(f"{streak['start_ts'][:10]} to {streak['end_ts'][:10]}"
                      for streak in alerts[:3])
    return recommendation(
        "escalate_repeat_episodes", "escalate", 10,
        "Bring this one to your support team",
        f"{context.display_name} has {len(alerts)} separate alert episodes in the "
        f"last 30 days, not one long stretch. Distress that returns after a "
        f"recovery is the pattern classroom-level strategies tend not to reach on "
        f"their own.",
        [f"{len(alerts)} distinct alert episodes: {spans}",
         f"{context.summary['recent_negative_share']:.0%} of recent check-ins negative"],
        ["insights"])


def rule_escalate_acute_cluster(context, settings):
    """A tight same-day cluster: something happened on one specific day.

    A cluster that tight usually has a single cause behind it - an incident,
    an illness, something that changed at home - rather than a pattern that
    built up over weeks. That makes it findable, which is why it is worth its
    own recommendation.
    """
    acute = [streak for streak in context.alert_streaks
             if streak["type"] == "same_day_cluster"]
    if not acute:
        return None

    worst = max(acute, key=lambda streak: streak["length"])
    return recommendation(
        "escalate_acute_cluster", "escalate", 11,
        f"Find out what happened on {worst['start_ts'][:10]}",
        f"{worst['length']} negative check-ins inside that one school day. A "
        f"cluster that tight usually has a single cause behind it - an "
        f"incident, illness, or something that changed at home - rather than a "
        f"pattern that built up over weeks.",
        [f"{worst['length']} negatives between {worst['start_ts'][11:16]} and "
         f"{worst['end_ts'][11:16]}",
         "emotions: " + ", ".join(worst["emotions"])],
        ["insights"])


def rule_escalate_long_streak(context, settings):
    """One long, sustained run - but only when nothing sharper already fired.

    Suppressed when there is an acute cluster or repeated episodes, because
    those are more specific descriptions of the same student and a teacher
    should not be told the same thing three ways.
    """
    alerts = context.alert_streaks
    acute = any(streak["type"] == "same_day_cluster" for streak in alerts)
    if acute or len(alerts) >= 2:
        return None

    sustained = [streak for streak in alerts
                 if streak["length"] >= 6 and streak["distinct_days"] >= 3]
    if not sustained:
        return None

    worst = max(sustained, key=lambda streak: streak["length"])
    return recommendation(
        "escalate_long_streak", "escalate", 12,
        "Sustained run - worth a second pair of eyes",
        f"{worst['length']} negative check-ins in a row across "
        f"{worst['distinct_days']} days, with nothing positive in between. It "
        f"has persisted across overnight resets, which is what separates a "
        f"rough patch from a bad day.",
        [f"{worst['length']} consecutive negatives, {worst['start_ts'][:10]} "
         f"to {worst['end_ts'][:10]}"],
        ["insights"])


# ---------------------------------------------------------------------------
# antecedent - the strongest thing a teacher can actually change
# ---------------------------------------------------------------------------

def _corroborating_notes(context, task_name):
    """Notes where the teacher already named this task themselves.

    Matched loosely in both directions ("Reading" against "Guided Reading"),
    because a teacher typing a note is not filling in a controlled vocabulary.
    """
    lowered = task_name.lower()
    return [note for note in context.notes["named_trigger"] + context.notes["needed_prompt"]
            if note["task"].lower() in lowered or lowered in note["task"].lower()]


def rule_antecedent_task(context, settings):
    """The activity this student comes apart around - the most actionable finding.

    Ranked *above* everything else when a teacher's own note names the same
    task, because agreement between a computed correlation and a human
    judgement is much stronger evidence than either alone.
    """
    top_task = context.top_task
    if not top_task:
        return None

    name = context.display_name
    corroborated = _corroborating_notes(context, top_task["task"])

    evidence = [
        f"{top_task['hits']} of the {top_task['exposure']} check-ins "
        f"around {top_task['task']} were flagged ({top_task['rate']:.0%})",
        f"{name}'s check-ins go badly {top_task['usual_rate']:.0%} of "
        f"the time overall - {top_task['lift']}x more likely here",
    ]
    if top_task["skipped"] >= settings.min_skipped:
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

    return recommendation(
        "antecedent_task", "antecedent", 20 if corroborated else 21,
        f"{top_task['task']} is where this shows up",
        detail, evidence,
        ["task_log", "notes"] if corroborated else ["task_log"])


def rule_antecedent_second_task(context, settings):
    """A secondary activity cluster - deliberately framed as "not yet".

    The advice is to wait rather than to act, and the reason is stated: change
    two things at once and you cannot tell which one worked.
    """
    if len(context.tasks) < 2:
        return None

    top_task, second = context.tasks[0], context.tasks[1]
    return recommendation(
        "antecedent_second_task", "antecedent", 26,
        f"{second['task']} is a secondary cluster",
        f"Smaller than {top_task['task']} but still above {context.display_name}'s "
        f"baseline. Worth watching once the first one is addressed rather than "
        f"changing both at once - two changes at a time makes it impossible to tell "
        f"which one worked.",
        [f"{second['hits']} of {second['exposure']} check-ins around "
         f"{second['task']} flagged ({second['rate']:.0%} against a usual "
         f"{second['usual_rate']:.0%})"],
        ["task_log"])


def rule_antecedent_time(context, settings):
    """A time-of-day concentration, when no single activity explains it.

    Only fires when the activity rule found nothing: if there *is* a named
    activity, that is the more useful version of the same advice.
    """
    if not context.time or context.top_task:
        return None

    window = context.time
    name = context.display_name
    return recommendation(
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
        ["insights", "task_log"])


def rule_antecedent_fatigue(context, settings):
    """Afternoon concentration plus a note about post-lunch tiredness.

    Two independent signals agreeing, which is why this is worth saying
    separately from the plain time-of-day rule: shortening the afternoon is a
    lever this particular teacher has already found works.

    .. note::
       This rule contained a latent crash before the refactor - it quoted a
       ``share`` field that :func:`correlate_time` has never produced, so the
       first student to satisfy both conditions would have taken the whole stage
       down with a ``KeyError``. It has never fired on this corpus, which is
       exactly why the bug survived. It now quotes ``rate``, the field that
       carries the meaning the sentence is claiming.
    """
    if "tired_after_lunch" not in context.notes["flags"]:
        return None
    if not context.time or context.time["bucket"] != "afternoon":
        return None

    return recommendation(
        "antecedent_fatigue", "antecedent", 25,
        "Fatigue, not just mood",
        f"The flags cluster in the afternoon and your notes already record "
        f"{context.display_name} being tired after lunch. Shortening the afternoon "
        f"schedule is a lever you have already found works for this student.",
        [f"{context.time['rate']:.0%} of check-ins after 1pm were flagged",
         "notes record post-lunch tiredness"],
        ["notes", "task_log"])


# ---------------------------------------------------------------------------
# regulation - what to hand the student in the moment
# ---------------------------------------------------------------------------

def rule_regulation_tool(context, settings):
    """Which calm-corner tool to offer, and - just as important - when.

    Prefers a tool the student has not exhausted. When they have tried them all,
    the advice changes rather than repeating: the gap is no longer *which* tool
    but reaching one *before* the check-in instead of after it.
    """
    dominant = context.dominant_emotion
    if not dominant:
        return None

    options = EMOTION_TOOLS.get(dominant, ())
    untried = [tool for tool in options if tool not in context.calm_used]
    choice = untried[0] if untried else (options[0] if options else None)
    if not choice:
        return None

    name = context.display_name
    spec = CALM_TOOLS[choice]
    top_task = context.top_task

    if untried:
        lead = f"{name} has not opened {spec['label']} yet"
        why = (f"{dominant.capitalize()} usually reads as "
               f"{EMOTION_READS.get(dominant, 'distress')}, and "
               f"{spec['label']} targets that directly.")
    else:
        lead = f"Go back to {spec['label']}"
        why = (f"{name} has already tried every calm tool that fits "
               f"{dominant}, so the gap is not which tool - it is reaching "
               f"one before the check-in rather than after it.")

    evidence = [
        f"{context.emotion_mix[dominant]} of {context.flagged_check_in_count} "
        f"flagged check-ins are '{dominant}'",
        f"calm corner: {len(context.calm_used)} of {len(CALM_TOOLS)} tools tried",
    ]
    worked_after = [note["task"] for note in context.notes["calm_worked"]]
    if worked_after:
        evidence.append(f"notes record the calm corner working after {worked_after[0]}")

    timing = (f" Offer it when {top_task['task']} is coming up, not after it "
              f"has gone wrong." if top_task else
              " Offer it before the check-in, not as a repair afterwards.")
    caveat = EMOTION_CAVEATS.get(dominant)

    return recommendation(
        "regulation_tool", "regulation", 30,
        f"{spec['emoji']} {lead}",
        why + timing + (f" {caveat}" if caveat else ""),
        evidence, ["student", "insights"])


# ---------------------------------------------------------------------------
# communication - the flag may be a vocabulary gap, not a mood
# ---------------------------------------------------------------------------

def rule_communication_aac(context, settings):
    """Low AAC use alongside confusion/overwhelm flags.

    Confusion and overwhelm are the two flags most likely to be an instruction
    that did not land rather than a feeling that arrived. If the student logs
    feelings far more often than they use AAC to say anything about them, the
    gap may be in what they can express, not in how they feel.
    """
    gap_emotions = (context.emotion_mix.get("confused", 0)
                    + context.emotion_mix.get("overwhelmed", 0))
    if not context.emo_count or not gap_emotions:
        return None
    if context.aac_count / context.emo_count >= settings.aac_low_ratio:
        return None

    top_task = context.top_task
    return recommendation(
        "communication_aac", "communication", 35,
        "This may be a communication gap, not a mood",
        f"{context.display_name} logs feelings far more often than they use AAC to "
        f"say anything about them. Confusion and overwhelm are the two flags most "
        f"likely to be an instruction that did not land. Try modelling two AAC "
        f"phrases for asking for help"
        + (f" before {top_task['task']}." if top_task else "."),
        [f"{context.aac_count} AAC uses against {context.emo_count} emotion "
         f"check-ins ({context.aac_count / context.emo_count:.0%})",
         f"{gap_emotions} flagged check-ins are confused or overwhelmed"],
        ["student"])


# ---------------------------------------------------------------------------
# story - point at something the classroom already owns
# ---------------------------------------------------------------------------

def rule_story_assign(context, settings):
    """A social story the classroom already has, matched to the trigger.

    Never invents a story. Pointing a teacher at a resource they already own is
    advice; pointing them at one they would have to write is homework.
    """
    top_task = context.top_task
    story, matched_on = context.stories.pick(
        top_task["task"] if top_task else None, context.dominant_emotion)
    if not story:
        return None

    target = top_task["task"] if matched_on == "task" else context.dominant_emotion
    return recommendation(
        "story_assign", "story", 40,
        f"Assign \"{story['title']}\"",
        "Your classroom already has this story. Reading it ahead of time is the "
        "cheapest version of previewing "
        + (f"{target}." if matched_on == "task"
           else f"what tends to set {context.display_name} off."),
        [f"matched on "
         f"{'the correlated activity' if matched_on == 'task' else context.dominant_emotion}",
         f"{len(story.get('pages') or [])} pages, already in this classroom"],
        ["stories"])


# ---------------------------------------------------------------------------
# motivation - the reward system may be misconfigured
# ---------------------------------------------------------------------------

def rule_motivation_tokens(context, settings):
    """A token goal the student is not reaching.

    A reward that never arrives stops being a reward, and in a rough patch an
    unreachable goal is one more thing going wrong rather than a motivator.
    """
    goal = context.token_goal
    if not goal or context.tokens is None or context.tokens >= goal:
        return None
    if "token_goal_met" in context.notes["flags"]:
        return None

    return recommendation(
        "motivation_tokens", "motivation", 45,
        "The token board is out of reach right now",
        f"{context.display_name} is at {context.tokens} of {goal} tokens and has "
        f"not hit the goal in the last 30 days of notes. A reward that never "
        f"arrives stops being a reward - consider a smaller interim goal until the "
        f"pattern settles.",
        [f"{context.tokens}/{goal} tokens toward \"{context.token_reward}\"",
         "no token-goal note in the last 30 days"],
        ["student", "notes"])


# ---------------------------------------------------------------------------
# documentation - what is missing from the record
# ---------------------------------------------------------------------------

def rule_document_note(context, settings):
    """Nothing written down for the window these flags cover.

    Aimed squarely at what happens later: check-ins alone will show a support
    team or an IEP meeting the pattern, but not the context the teacher is
    carrying in their head.
    """
    if context.note_count != 0:
        return None

    return recommendation(
        "document_note", "document", 50,
        "Nothing on file for this period",
        f"There are no notes for {context.display_name} in the window these flags "
        f"cover. If this goes to a support team or an IEP meeting, the check-ins "
        f"alone will not carry the context you have in your head.",
        ["0 notes in the last 30 days",
         f"{context.summary['streak_count']} flagged patterns over the same window"],
        ["notes"])


def rule_document_context(context, settings):
    """There *are* notes - so prompt a check that the most recent one still holds."""
    if context.note_count == 0 or not context.notes["recent"]:
        return None

    count = context.note_count
    return recommendation(
        "document_context", "document", 52,
        "Your notes already have context on this",
        f"{count} note{'' if count == 1 else 's'} on {context.display_name} in "
        f"this window. Check whether the most recent one still describes what "
        f"you are seeing.",
        [note["text"] for note in context.notes["recent"][:2]],
        ["notes"])


# ---------------------------------------------------------------------------
# the two channel-specific rules
# ---------------------------------------------------------------------------

def rule_celebrate(context, settings):
    """Good news, for positive-channel students. Rank 5: first, and quick.

    It is here for two reasons, and the second is the less obvious one: a good
    stretch is worth naming out loud to a student, *and* it is the baseline a
    future flag gets compared against.
    """
    if context.channel != "positive":
        return None

    best = max((streak for streak in context.streaks if streak["type"] == "repeated"),
               key=lambda streak: streak["length"], default=None)
    if not best:
        return None

    return recommendation(
        "celebrate", "celebrate", 5,
        "Say this one out loud",
        f"{context.display_name} logged \"{best['emotion']}\" {best['length']} "
        f"check-ins in a row across {best['distinct_days']} days. It is here "
        f"because good stretches are worth naming to a student, and because it is "
        f"the baseline you compare a future flag against.",
        [f"{best['length']}x {best['emotion']}, {best['start_ts'][:10]} to "
         f"{best['end_ts'][:10]}"],
        ["insights"])


def rule_monitor_watch(context, settings):
    """The watch channel's default: explicitly advise *not* acting yet.

    A recommendation that says "do nothing" is doing real work. Without it a
    watch student either gets no card at all - and looks like an oversight - or
    gets advice pitched at an alert they have not reached.
    """
    if context.channel != "watch" or context.tasks:
        return None

    reasons = ", ".join(context.summary["watch_reasons"])
    return recommendation(
        "monitor_watch", "monitor", 48,
        "Watch, do not act yet",
        f"{context.display_name} tripped {reasons} but never four negatives in a "
        f"row. That is a pattern worth knowing about, not one worth intervening "
        f"on - the soft signal is here so that if it does turn into a streak, you "
        f"already know the history.",
        [f"trigger: {reasons}",
         f"{context.summary['recent_negative_share']:.0%} of recent check-ins negative"],
        ["insights"])


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------

#: Every student rule, in the order they are evaluated.
#:
#: The order here does **not** decide what a teacher sees - that is
#: :func:`rank_and_cap`, which sorts by ``rank``. This tuple exists so that
#: adding a rule is a one-line change with no other file touched.
RULES = (
    rule_celebrate,
    rule_escalate_repeat_episodes,
    rule_escalate_acute_cluster,
    rule_escalate_long_streak,
    rule_antecedent_task,
    rule_antecedent_second_task,
    rule_antecedent_time,
    rule_antecedent_fatigue,
    rule_regulation_tool,
    rule_communication_aac,
    rule_story_assign,
    rule_motivation_tokens,
    rule_document_note,
    rule_document_context,
    rule_monitor_watch,
)


def build_recommendations(context, settings=DEFAULT_SETTINGS):
    """Every rule that fires for one student - unranked and uncapped."""
    results = []
    for rule in RULES:
        found = rule(context, settings)
        if found is not None:
            results.append(found)
    return results


def rank_and_cap(recommendations, channel, settings=DEFAULT_SETTINGS):
    """Order by rank and cut to the channel's cap.

    The tie-break on ``id`` is what makes the output stable: two rules at the
    same rank always come out in the same order, so re-running the stage never
    silently reshuffles a teacher's list.
    """
    recommendations.sort(key=lambda item: (item["rank"], item["id"]))
    return recommendations[:settings.cap_for_channel(channel)]


def headline_reason(context):
    """One line stating why this student is on the list at all.

    Shown above the recommendations, because "here is what to do" is unreadable
    without "here is what I am reacting to" directly above it.
    """
    alerts = context.alert_streaks
    if alerts:
        worst = max(alerts, key=lambda streak: streak["length"])
        if worst["type"] == "same_day_cluster":
            reason = (f"{worst['length']} negative check-ins in one day "
                      f"({worst['start_ts'][:10]})")
        else:
            reason = (f"{worst['length']} negative check-ins in a row over "
                      f"{worst['distinct_days']} days")
        if len(alerts) > 1:
            reason += f", across {len(alerts)} separate episodes"
    elif context.channel == "watch":
        reason = (f"{context.summary['recent_negative_share']:.0%} of recent "
                  f"check-ins negative, never four in a row")
    else:
        best = max((s for s in context.streaks if s["type"] == "repeated"),
                   key=lambda streak: streak["length"], default=None)
        reason = (f"{best['length']} \"{best['emotion']}\" check-ins in a row"
                  if best else "positive pattern")

    if context.tasks:
        reason += f" - clustered around {context.tasks[0]['task']}"
    return reason
