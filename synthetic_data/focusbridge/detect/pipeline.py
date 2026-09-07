"""Wiring for stage 1: run the rules over the corpus and write the output files.

WHAT "WIRING" MEANS
-------------------
Everything interesting already happened in ``rules.py``, ``summarize.py`` and
``judge.py``. This module does the boring, necessary work those files
deliberately refuse to do: opening files, deciding what "recent" means today,
merging in classroom metadata, counting things for the report, and printing.

Keeping that separate is the point. The rules can be unit-tested with a
seven-entry list because they never touch a file; this module can be read as a
plain sequence of steps because it never does any arithmetic about emotions.

WHY THE CORPUS IS STREAMED
--------------------------
``students.jsonl`` is about 153 MB. It is read line by line and each student's
record is written out before the next one is read, so peak memory stays flat
regardless of corpus size. The only things held for the whole run are the
per-classroom index and the judge-case buckets, both small.

The cost of that choice is a *second* pass when ``--reference`` is not given,
because "the latest timestamp in the data" cannot be known until the data has
been read once.

Pinning ``--reference`` skips that pass. It does not halve the runtime - the
extra pass only parses timestamps and never applies a rule, so it is the
cheaper of the two - but measured on this corpus it takes about a third off
the stage (10.0s to 6.9s).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from ..core import cli
from ..core.jsonio import (dumps_line, open_jsonl_writer, read_jsonl, write_json,
                           write_jsonl)
from ..core.timeline import Timeline, parse_timestamp, read_check_ins
from ..core.triage import classroom_sort_key, student_sort_key
from .judge import collect_judge_cases, new_case_collection, write_judge_cases
from .rules import (find_density_streaks, find_negative_streaks,
                    find_repeated_streaks, find_same_day_clusters,
                    scan_negative_runs)
from .settings import DEFAULT_SETTINGS, TRIGGER_CHANNELS
from .summarize import compute_flag_id, summarize


# ---------------------------------------------------------------------------
# working out the window
# ---------------------------------------------------------------------------

def latest_check_in_time(students_path):
    """The most recent check-in anywhere in the corpus.

    Used as the default end of the recency window. Anchoring to the *data*
    rather than to ``datetime.now()`` is what makes a run reproducible: this
    corpus was generated once, and re-running the detector next month should
    produce the same flags, not an empty file because every check-in fell out of
    a 30-day window measured from today.

    A live deployment reading real check-ins would pass ``datetime.now()``
    instead, which is exactly what ``--reference`` is for.

    Complexity: O(total check-ins), and it is a whole extra pass over the
    corpus - see the module docstring.
    """
    latest = None
    for student in read_jsonl(students_path):
        for row in student.get("emotion_log") or []:
            if not isinstance(row, dict):
                continue
            at = parse_timestamp(row)
            if at is not None and (latest is None or at > latest):
                latest = at
    return latest


def resolve_reference(students_path, reference_text=None):
    """Decide where the recency window ends, from the CLI argument or the data."""
    if reference_text:
        try:
            return datetime.fromisoformat(reference_text)
        except ValueError:
            raise cli.fail(f"--reference is not an ISO datetime: {reference_text!r}")

    reference = latest_check_in_time(students_path)
    if reference is None:
        raise cli.fail("no readable timestamps in students.jsonl")
    return reference


def load_classrooms(classrooms_path):
    """``classroom code -> the teacher and school context for that room``.

    Read into memory in full because it is small (220 rows) and because every
    flagged student needs a lookup against it. Building the dict once turns
    what would be an O(students x classrooms) scan into O(students).
    """
    return {
        room.get("code"): {
            "classroom_name": room.get("name"),
            "teacher_id": room.get("teacher_id"),
            "teacher_name": room.get("teacher_name"),
            "school_name": room.get("school_name"),
            "school_zip": room.get("zip"),
            "name_mode": room.get("name_mode"),
        }
        for room in read_jsonl(classrooms_path)
    }


# ---------------------------------------------------------------------------
# the per-student work
# ---------------------------------------------------------------------------

def analyse_student(student, cutoff, problems, settings=DEFAULT_SETTINGS):
    """Everything the detector has to say about one student.

    Returns ``(timeline, streaks, runs)``, where ``timeline`` holds only the
    check-ins inside the recency window.

    **Rule order is load-bearing here**, which is why it is spelled out in this
    one place rather than hidden behind a single ``find_all_streaks`` call: the
    alert-channel rules run first so the density rule can be told what they
    already cover and stay quiet about it. Everything else is independent.
    """
    check_ins = read_check_ins(student, problems)

    # `read_check_ins` sorts, so the recent slice is a suffix of the list. Kept
    # as a list comprehension rather than a bisect for clarity - the cost is
    # linear either way once the entries have been parsed.
    recent = Timeline([check_in for check_in in check_ins if check_in.at >= cutoff])

    runs = scan_negative_runs(recent, settings)
    negative = find_negative_streaks(recent, runs, settings)
    same_day = find_same_day_clusters(recent, runs, settings)

    # Extents of the alert-channel findings, so density does not duplicate them.
    covered = _alert_extents(negative + same_day)

    streaks = (negative
               + same_day
               + find_repeated_streaks(recent, settings)
               + find_density_streaks(recent, covered, settings))

    return recent, streaks, runs


def _alert_extents(streaks):
    """``(start, end)`` datetimes of the alert-channel findings."""
    return [(datetime.fromisoformat(streak["start_ts"]),
             datetime.fromisoformat(streak["end_ts"]))
            for streak in streaks if streak["channel"] == "alert"]


def build_insight_record(student, flag_id, summary, streaks, room):
    """One line of ``insights.jsonl``.

    The classroom's metadata is **copied in** rather than left as a code to join
    on. That is denormalisation, and it is deliberate: it means
    ``insights.jsonl`` stands alone, and routing a flag to the right teacher
    never requires a second file. The corpus is read-only between runs, so the
    usual argument against copying - that the two can drift apart - does not
    apply here.
    """
    return {
        "student_id": student.get("id"),
        "flag_id": flag_id,
        "student_name": student.get("name"),
        "classroom_code": student.get("classroom_code"),
        "classroom_name": room.get("classroom_name"),
        "teacher_id": room.get("teacher_id"),
        "teacher_name": room.get("teacher_name"),
        "school_name": room.get("school_name"),
        "school_zip": room.get("school_zip"),
        "name_mode": room.get("name_mode"),
        "channel": summary["channel"],
        "priority": summary["priority"],
        "summary": summary,
        "streaks": streaks,
    }


def build_classroom_records(classrooms, flagged_by_code):
    """Regroup the flagged students by classroom, worst room first.

    The detector produces a flat list; a teacher needs their own room. Same data,
    a second shape, written once here rather than re-derived by every consumer.
    """
    records = []
    for code, students in flagged_by_code.items():
        room = classrooms.get(code, {})
        students.sort(key=lambda entry: student_sort_key(
            entry["summary"]["priority"],
            entry["summary"]["alert_streak_count"],
            entry["student_id"]))
        records.append({
            "classroom_code": code,
            **room,
            "flagged_student_count": len(students),
            "alert_student_count": sum(1 for entry in students
                                       if entry["summary"]["channel"] == "alert"),
            "high_priority_count": sum(1 for entry in students
                                       if entry["summary"]["priority"] == "high"),
            "students": students,
        })

    records.sort(key=lambda record: classroom_sort_key(
        record["high_priority_count"],
        record["alert_student_count"],
        record["classroom_code"]))
    return records


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

class RunTotals:
    """The counters a run reports at the end.

    A small class instead of six loose variables threaded through the loop: it
    keeps the counting in one place, makes the report function's input obvious,
    and means adding a counter does not change any function signature.
    """

    def __init__(self):
        self.scanned = 0
        self.flagged = 0
        self.priorities = Counter()
        self.channels = Counter()
        self.trigger_students = Counter()
        self.emotion_counts = Counter()
        self.problems = Counter()  # rows dropped during parsing, and why

    def record(self, summary, streaks):
        self.flagged += 1
        self.priorities[summary["priority"]] += 1
        self.channels[summary["channel"]] += 1
        # A *set* of trigger names, so a student who trips the same rule three
        # times counts once: this counter answers "how many students does this
        # rule affect", not "how many findings did it make".
        for trigger in {streak["type"] for streak in streaks}:
            self.trigger_students[trigger] += 1

    def as_metadata(self, classroom_count, unmatched_codes):
        return {
            "students_scanned": self.scanned,
            "students_flagged": self.flagged,
            "classrooms_with_flags": classroom_count,
            "by_channel": dict(self.channels),
            "by_priority": dict(self.priorities),
            "students_by_trigger": dict(self.trigger_students),
            "recent_emotion_counts": dict(self.emotion_counts.most_common()),
            "dropped_entries": dict(self.problems),
            "unmatched_classroom_codes": unmatched_codes,
        }


def run(paths, reference, settings=DEFAULT_SETTINGS, report=print):
    """Detect patterns across the whole corpus and write every output file.

    Args:
        paths:     where to read and write; see :class:`DataPaths`.
        reference: the end of the recency window.
        settings:  the thresholds to apply.
        report:    where progress lines go. Injected so tests can silence it, and
                   so a future web front end could collect the lines instead of
                   printing them.

    Returns:
        The metadata dictionary that was written to ``insights_meta.json``.
    """
    cutoff = reference - timedelta(days=settings.recency_days)
    report(f"reference {reference.isoformat()}  cutoff {cutoff.isoformat()}")

    classrooms = load_classrooms(paths.classrooms)
    totals = RunTotals()
    cases = new_case_collection()
    flagged_by_code = {}

    # Read and write in the same pass: a flagged student's record is written
    # immediately and then forgotten, so memory does not grow with the corpus.
    with open_jsonl_writer(paths.insights) as insights_file:
        for student in read_jsonl(paths.students):
            totals.scanned += 1

            recent, streaks, runs = analyse_student(
                student, cutoff, totals.problems, settings)

            totals.emotion_counts.update(
                check_in.emotion for check_in in recent.check_ins
                if check_in.emotion is not None)

            # Judge cases are collected for *every* student, flagged or not -
            # the most valuable bucket is the one about students we missed.
            collect_judge_cases(student, recent, streaks, cases, runs, settings)

            if not streaks:
                continue

            # Chronological order, so a reader meets the episodes in the order
            # they happened rather than the order the rules ran.
            streaks.sort(key=lambda streak: streak["start_ts"])
            summary = summarize(streaks, recent.check_ins, settings)
            totals.record(summary, streaks)

            code = student.get("classroom_code")
            flag_id = compute_flag_id(student.get("id"), streaks)
            record = build_insight_record(
                student, flag_id, summary, streaks, classrooms.get(code, {}))
            insights_file.write(dumps_line(record))

            # The per-classroom view carries only the fields a room view needs;
            # everything else is already in insights.jsonl.
            flagged_by_code.setdefault(code, []).append({
                "student_id": record["student_id"],
                "flag_id": flag_id,
                "student_name": record["student_name"],
                "summary": summary,
                "streaks": streaks,
            })

    classroom_records = build_classroom_records(classrooms, flagged_by_code)
    write_jsonl(paths.flagged_by_classroom, classroom_records)

    # Classroom codes on students that no classroom row explains. Worth shouting
    # about: those flags cannot be routed to a teacher, so they are invisible.
    unmatched = sorted({record["classroom_code"] for record in classroom_records
                        if record.get("teacher_id") is None})
    case_populations = write_judge_cases(paths.judge_cases, cases, settings)

    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "reference_time": reference.isoformat(),
        "cutoff": cutoff.isoformat(),
        "config": settings.as_metadata(),
        "totals": totals.as_metadata(len(classroom_records), unmatched),
        "judge_case_populations": case_populations,
    }
    write_json(paths.insights_meta, meta)

    _report_run(report, paths, totals, classroom_records, case_populations, unmatched)
    return meta


def _report_run(report, paths, totals, classroom_records, case_populations, unmatched):
    """The end-of-run console summary. Presentation only - no logic lives here."""
    report(f"scanned {totals.scanned} students")
    report(f"flagged {totals.flagged} students -> {paths.insights}")
    report(f"  ALERT    channel: {totals.channels['alert']} students "
           f"(high {totals.priorities['high']}, medium {totals.priorities['medium']})")
    report(f"  WATCH    channel: {totals.channels['watch']} students")
    report(f"  POSITIVE channel: {totals.channels['positive']} students")
    for trigger, count in sorted(totals.trigger_students.items()):
        report(f"    {trigger} trigger -> {TRIGGER_CHANNELS[trigger]}: {count} students")
    report(f"{len(classroom_records)} classrooms -> {paths.flagged_by_classroom}")
    report(f"judge cases -> {paths.judge_cases}  {case_populations}")
    report(f"run metadata -> {paths.insights_meta}")
    if totals.problems:
        report(f"  dropped entries: {dict(totals.problems)}")
    if unmatched:
        report(f"  WARNING {len(unmatched)} classroom codes not in "
               f"classrooms.jsonl: {unmatched[:5]}")


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

@cli.friendly_data_errors
def main(argv=None):
    """Entry point for ``python detect_patterns.py``."""
    parser = cli.build_parser(
        "Flag emotion patterns in students.jsonl.")
    parser.add_argument(
        "--reference", metavar="ISO",
        help="pin the end of the recency window, e.g. 2026-07-31T13:08:00 "
             "(default: the latest timestamp in students.jsonl). Pinning it is "
             "how a genuine earlier snapshot is produced for the change feed - "
             "and it also skips a whole pass over the corpus.")
    cli.add_suffix_argument(parser)
    cli.add_data_dir_argument(parser)
    args = parser.parse_args(argv)

    paths = cli.paths_from_args(args)
    cli.require(paths.students, "the corpus is missing from this data directory")

    reference = resolve_reference(paths.students, args.reference)
    run(paths, reference, DEFAULT_SETTINGS)


if __name__ == "__main__":
    main()
