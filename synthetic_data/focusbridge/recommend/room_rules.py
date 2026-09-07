"""The whole-classroom rules: things you change once instead of per student.

WHY THESE ARE SEPARATE FROM THE STUDENT RULES
---------------------------------------------
An activity that upsets one child is that child's antecedent, and the answer is
a plan for that child. The *same* activity upsetting a quarter of the flagged
roster is a schedule problem, and the answer is to change the activity - its
length, its position in the day, the warning students get before it.

That is a different kind of advice with a different audience and a different
cost, and it is only visible by looking across students. A per-student rule
cannot see it however clever it is, which is why these four rules exist and why
they live in their own file.

They share the shape of the student rules exactly - ``(room, students,
settings) -> dict | None`` - so both sets are ranked, rendered and read the
same way.
"""

from __future__ import annotations

from collections import Counter

from .settings import DEFAULT_SETTINGS, TIME_BUCKET_PHRASES
from .student_rules import recommendation


def rule_room_task(room, students, settings):
    """One activity clustering across several students - a schedule problem.

    Returns up to two, because a room can genuinely have two hard activities and
    a teacher can hold two. More than that stops being a finding and becomes a
    timetable review.

    Note the evidence line: each student's cluster was measured against **that
    student's own** baseline, not against the room's. Without that, a room with
    a generally hard afternoon would flag every afternoon activity.
    """
    flagged = len(students)
    found = []

    # How many *students* each activity touches - not how many findings it
    # produced. A `set` per student first, so one student with three clusters
    # around Reading still counts once.
    students_per_task = Counter()
    for context in students:
        for task in {cluster["task"] for cluster in context.tasks}:
            students_per_task[task] += 1

    for task, count in students_per_task.most_common(2):
        share = count / flagged
        if count < settings.room_task_min_students or share < settings.room_task_min_share:
            continue
        found.append(recommendation(
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
    return found


def rule_room_time(room, students, settings):
    """One part of the day that is hard across the room.

    A higher share is demanded here than for an activity
    (``room_time_min_share``), because there are only three time bands: any two
    students land in the same one far more easily than they land on the same
    activity, so the bar has to be higher to mean the same thing.
    """
    flagged = len(students)
    buckets = Counter(context.time["bucket"] for context in students if context.time)
    if not buckets:
        return []

    bucket, count = buckets.most_common(1)[0]
    if (count / flagged < settings.room_time_min_share
            or count < settings.room_task_min_students):
        return []

    phrase = TIME_BUCKET_PHRASES[bucket]
    return [recommendation(
        "room_time", "room", 20,
        f"The room's hard stretch is {phrase}",
        f"{count} of {flagged} flagged students concentrate {phrase}. That "
        f"points at the shape of the day rather than at any one child - "
        f"check what the schedule asks of them in that window and where the "
        f"nearest break sits.",
        [f"{count} of {flagged} flagged students concentrate {phrase}"],
        ["insights", "task_log"])]


def rule_room_trend(room, students, settings):
    """Several students declining at once - usually something the room shares.

    Simultaneous decline is the signal here, and it is worth more than any one
    student's decline: a schedule change, a staffing change or a run of
    disrupted days shows up as several children moving together.
    """
    flagged = len(students)
    worsening = [context for context in students
                 if context.trajectory["direction"] == "worsening"]
    if (len(worsening) < settings.room_task_min_students
            or len(worsening) / flagged < settings.room_task_min_share):
        return []

    return [recommendation(
        "room_trend", "room", 30,
        "Several students are trending down together",
        f"{len(worsening)} of {flagged} flagged students are more negative in "
        f"the second half of the window than the first. Simultaneous decline "
        f"usually traces to something the room shares - a schedule change, a "
        f"staffing change, or a run of disrupted days.",
        [f"{len(worsening)} of {flagged} students worsening",
         "compares each student's own first half against their second"],
        ["insights"])]


def rule_room_documentation(room, students, settings):
    """Alert-channel students with nothing written down.

    Aimed at the meeting that has not happened yet: these are the students most
    likely to come up in one, and the ones with no record of what was tried.
    """
    undocumented = [context for context in students
                    if context.note_count == 0 and context.channel == "alert"]
    if len(undocumented) < settings.room_task_min_students:
        return []

    # Six names, then a count. A list of forty names is not read.
    names = ", ".join(context.display_name for context in undocumented[:6])
    if len(undocumented) > 6:
        names += "..."

    return [recommendation(
        "room_documentation", "room", 40,
        f"{len(undocumented)} alert students have no notes",
        "These are the students most likely to come up in a meeting, and the "
        "ones with nothing written down. The check-in history will show the "
        "pattern but not what you did about it.",
        [names],
        ["notes"])]


#: Every room rule, in evaluation order. As with the student rules, the order
#: shown to a teacher comes from ``rank``, not from this tuple.
ROOM_RULES = (
    rule_room_task,
    rule_room_time,
    rule_room_trend,
    rule_room_documentation,
)


def build_room_recommendations(room, students, settings=DEFAULT_SETTINGS):
    """Every room-wide pattern for one classroom, ranked.

    A room below ``room_min_flagged`` is skipped entirely: with two flagged
    students, "2 of 2 cluster around Reading" is a 100% share of nothing, and
    every share-based rule below would fire on noise.
    """
    if len(students) < settings.room_min_flagged:
        return []

    found = []
    for rule in ROOM_RULES:
        found.extend(rule(room, students, settings))

    found.sort(key=lambda item: item["rank"])
    return found
