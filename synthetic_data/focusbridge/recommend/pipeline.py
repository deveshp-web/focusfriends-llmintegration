"""Wiring for stage 2: join the flags against everything else and write the advice.

THE SHAPE OF THIS STAGE
-----------------------
1. Read what the detector decided (``insights.jsonl``) - never re-decide it.
2. Stream the corpus again, building a full context for the flagged students
   only. Everyone else is skipped after one dictionary lookup.
3. Run the per-student rules, rank them, cap them, write them out.
4. Group the contexts by classroom and run the room-wide rules.

WHY THE CONTEXTS ARE HELD IN MEMORY
-----------------------------------
Unlike stage 1, this stage cannot write and forget: the room rules need every
student in a room at once, and a room's students are scattered throughout the
corpus. So the contexts are held until the streaming pass finishes.

That is a deliberate, bounded cost - the contexts are built for **flagged**
students only, and each carries a trimmed check-in strip rather than the whole
log. It is also why two long-dead fields were removed from the context during
this refactor (a task-statistics dict and a ``last_active`` timestamp, neither
of which anything ever read): in a structure held for thousands of students,
dead fields are not free.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from ..core import cli
from ..core.jsonio import (dumps_line, group_by, index_by, open_jsonl_writer,
                           read_json, read_jsonl, write_json, write_jsonl)
from ..core.timeline import Timeline, read_check_ins
from ..core.triage import classroom_sort_key, student_sort_key
from .context import StoryIndex, build_context, read_notes
from .room_rules import build_room_recommendations
from .settings import DEFAULT_SETTINGS
from .student_rules import build_recommendations, headline_reason, rank_and_cap


def build_student_record(context, recommendations):
    """One line of ``recommendations.jsonl``.

    Carries both the advice and the evidence behind it, because the dashboard
    renders them together and a teacher reading only the headline is exactly the
    reader this project is trying not to create.
    """
    return {
        "student_id": context.student_id,
        "student_name": context.student_name,
        "display_name": context.display_name,
        "classroom_code": context.classroom_code,
        "channel": context.channel,
        "priority": context.priority,
        "why_flagged": headline_reason(context),
        "trajectory": context.trajectory,
        "task_clusters": context.tasks,
        "time_cluster": context.time,
        "emotion_mix": dict(context.emotion_mix.most_common()),
        "note_count": context.note_count,
        "recent_notes": context.notes["recent"],
        "calm_tools_used": sorted(context.calm_used),
        "recent_strip": context.recent_strip,
        "summary": context.summary,
        "recommendations": recommendations,
    }


def build_room_record(code, room, students, room_recommendations):
    """One line of ``classroom_actions.jsonl``: the rollup a room view needs.

    ``student_order`` is stored rather than the students themselves. The
    dashboard already has every student keyed by id, so repeating them here
    would duplicate the largest part of the payload for no gain - the room only
    needs to say *which* students it has and in what order.
    """
    priorities = Counter(context.priority for context in students)
    return {
        "classroom_code": code,
        "classroom_name": room.get("name"),
        "teacher_id": room.get("teacher_id"),
        "teacher_name": room.get("teacher_name"),
        "school_name": room.get("school_name"),
        "name_mode": room.get("name_mode"),
        "token_reward": room.get("token_reward"),
        "token_goal": room.get("token_goal"),
        "flagged_student_count": len(students),
        "alert_student_count": sum(1 for c in students if c.channel == "alert"),
        "watch_student_count": sum(1 for c in students if c.channel == "watch"),
        "positive_student_count": sum(1 for c in students if c.channel == "positive"),
        "by_priority": dict(priorities),
        "high_priority_count": priorities["high"],
        "undocumented_alert_count": sum(1 for c in students
                                        if c.channel == "alert" and c.note_count == 0),
        "room_recommendations": room_recommendations,
        "student_order": [context.student_id for context in students],
    }


class RunTotals:
    """The counters reported at the end of a run."""

    def __init__(self):
        self.scanned = 0
        self.categories = Counter()
        self.by_rule = Counter()
        self.by_room_rule = Counter()
        self.with_task_cluster = 0
        self.corroborated = 0
        self.problems = Counter()

    def record_student(self, context, recommendations):
        for item in recommendations:
            self.categories[item["category"]] += 1
            self.by_rule[item["id"]] += 1
            # "Corroborated" means the computed activity cluster and a teacher's
            # own note agreed. Counted because it is the single best indicator
            # that the correlation rule is finding real things.
            if item["id"] == "antecedent_task" and "notes" in item["sources"]:
                self.corroborated += 1
        if context.tasks:
            self.with_task_cluster += 1


def build_contexts(paths, insights, cutoff, settings, report):
    """Stream the corpus and build a context for every flagged student.

    The joins against notes, stories and classrooms are all done through
    dictionaries built once up front. Scanning those files per student instead
    would turn an O(students) pass into O(students x notes), which on this
    corpus is the difference between seconds and hours.
    """
    notes_by_student = group_by(paths.notes, "student_id")
    stories_by_room = group_by(paths.stories, "classroom_code")
    rooms = index_by(paths.classrooms, "code")

    # One StoryIndex per classroom rather than per student - see StoryIndex.
    story_indexes = {code: StoryIndex(stories)
                     for code, stories in stories_by_room.items()}
    empty_stories = StoryIndex([])

    contexts = {}
    problems = Counter()
    scanned = 0

    for student in read_jsonl(paths.students):
        scanned += 1
        insight = insights.get(student.get("id"))
        if insight is None:
            continue  # not flagged: there is nothing to recommend

        code = insight.get("classroom_code")
        check_ins = read_check_ins(student, problems)
        timeline = Timeline([c for c in check_ins if c.at >= cutoff])

        contexts[student["id"]] = build_context(
            student=student,
            insight=insight,
            room=rooms.get(code, {}),
            timeline=timeline,
            notes=read_notes(notes_by_student.get(student.get("id"), []), cutoff),
            stories=story_indexes.get(code, empty_stories),
            settings=settings)

    report(f"scanned {scanned} students, built context for {len(contexts)}")
    return contexts, rooms, problems, scanned


def run(paths, settings=DEFAULT_SETTINGS, report=print):
    """Turn every flag into ranked advice and write all three output files."""
    cli.require(paths.insights,
                f"run detect_patterns.py"
                f"{' --suffix ' + paths.suffix if paths.suffix else ''} first")

    detector_meta = read_json(paths.insights_meta)
    reference = datetime.fromisoformat(detector_meta["reference_time"])
    cutoff = datetime.fromisoformat(detector_meta["cutoff"])
    report(f"detector run {detector_meta['generated_at']}  "
           f"cutoff {cutoff.isoformat()}")

    insights = index_by(paths.insights, "student_id")
    report(f"{len(insights)} flagged students to advise")

    contexts, rooms, problems, _ = build_contexts(
        paths, insights, cutoff, settings, report)

    totals = RunTotals()
    totals.problems = problems

    # -- per-student advice ---------------------------------------------------
    by_room = {}
    with open_jsonl_writer(paths.recommendations) as out:
        for context in contexts.values():
            recommendations = rank_and_cap(
                build_recommendations(context, settings), context.channel, settings)
            totals.record_student(context, recommendations)
            out.write(dumps_line(build_student_record(context, recommendations)))
            by_room.setdefault(context.classroom_code, []).append(context)

    # -- room-wide advice -----------------------------------------------------
    room_records = []
    for code, students in by_room.items():
        room = rooms.get(code, {})
        room_recommendations = build_room_recommendations(room, students, settings)
        for item in room_recommendations:
            totals.by_room_rule[item["id"]] += 1

        students.sort(key=lambda context: student_sort_key(
            context.priority,
            context.summary["alert_streak_count"],
            context.student_id))
        room_records.append(
            build_room_record(code, room, students, room_recommendations))

    room_records.sort(key=lambda record: classroom_sort_key(
        record["high_priority_count"],
        record["alert_student_count"],
        record["classroom_code"]))
    write_jsonl(paths.classroom_actions, room_records)

    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "detector_run": detector_meta["generated_at"],
        "reference_time": reference.isoformat(),
        "cutoff": cutoff.isoformat(),
        "config": settings.as_metadata(),
        "totals": {
            "students_advised": len(contexts),
            "classrooms": len(room_records),
            "recommendations": sum(totals.by_rule.values()),
            "students_with_task_cluster": totals.with_task_cluster,
            "task_clusters_corroborated_by_notes": totals.corroborated,
            "by_category": dict(totals.categories.most_common()),
            "by_rule": dict(totals.by_rule.most_common()),
            "room_rules": dict(totals.by_room_rule.most_common()),
            "dropped_entries": dict(totals.problems),
        },
    }
    write_json(paths.recommendations_meta, meta)

    _report_run(report, paths, totals, room_records)
    return meta


def _report_run(report, paths, totals, room_records):
    """The end-of-run console summary. Presentation only."""
    report(f"{sum(totals.by_rule.values())} recommendations -> {paths.recommendations}")
    for rule_id, count in totals.by_rule.most_common():
        report(f"    {rule_id}: {count}")
    report(f"  {totals.with_task_cluster} students have an activity cluster "
           f"({totals.corroborated} corroborated by a teacher note)")
    report(f"{len(room_records)} classrooms -> {paths.classroom_actions}")
    for rule_id, count in totals.by_room_rule.most_common():
        report(f"    {rule_id}: {count} rooms")
    report(f"run metadata -> {paths.recommendations_meta}")
    if totals.problems:
        report(f"  dropped entries: {dict(totals.problems)}")


@cli.friendly_data_errors
def main(argv=None):
    """Entry point for ``python recommend.py``."""
    parser = cli.build_parser(
        "Turn detector flags into recommendations a teacher can act on.")
    cli.add_suffix_argument(
        parser,
        "read insights<suffix>.jsonl and append <suffix> to every output "
        "filename, e.g. _prev")
    cli.add_data_dir_argument(parser)
    args = parser.parse_args(argv)

    run(cli.paths_from_args(args))


if __name__ == "__main__":
    main()
