"""Flag emotion patterns in students.jsonl.

Findings go to one of three channels, so softer signals and good news can
exist without crowding the queue a teacher actually reads:

  ALERT channel
    negative        - four or more consecutive negative emotions, in any mix
    same_day_cluster- at SAME_DAY_ALERT_LENGTH or more in one school day

  WATCH channel
    density         - a week that is mostly negative but never four in a row,
                      because a neutral check-in keeps breaking the run
    same_day_cluster- four or five negatives inside one school day

  POSITIVE channel
    repeated        - the same positive emotion four or more times in a row

Only the alert channel sets priority high/medium; a watch-only student stays
at "watch" however many soft signals they trip.

A negative streak is reported once per uninterrupted run. STREAK_WINDOW_DAYS
decides whether a run qualifies, not how much of it gets reported.

All three constraints must hold for a streak to count:
  STREAK_LENGTH     minimum number of consecutive entries
  STREAK_WINDOW_DAYS a streak's first and last entry must be this close together
  MIN_DISTINCT_DAYS a streak must touch this many separate calendar days, so a
                    single rough morning is not reported as a rough week
And only entries within RECENCY_DAYS of the reference time are considered.

Reads two vocabularies so the same detector works on generated data and on
exports from the Focus Bridge app - see EMOTION_VALENCE and ALIASES.

Writes:
  insights.jsonl             one record per flagged student, with the student's
                             classroom/teacher metadata merged in from
                             classrooms.jsonl and a per-student summary
  flagged_by_classroom.jsonl one record per classroom holding flagged students,
                             with its teacher and those students nested inside
  insights_meta.json         run configuration and totals
  judge_cases.jsonl          sampled boundary cases for an LLM-as-a-judge review

Usage:
    python detect_patterns.py
"""

import argparse
import json
import os
import random
from collections import Counter
from datetime import datetime, timedelta

# --------------------------------------------------------------- vocabulary --

# Canonical emotion -> valence. The first nine are what generate_synthetic_data
# emits; scared/confused/tired only appear in Focus Bridge app exports.
# "confused" counts as negative because the app itself routes it to a help
# prompt; "tired" stays neutral because it is as often a nap signal as distress.
EMOTION_VALENCE = {
    "happy": "positive",
    "calm": "positive",
    "excited": "positive",
    "okay": "neutral",
    "frustrated": "negative",
    "anxious": "negative",
    "overwhelmed": "negative",
    "sad": "negative",
    "angry": "negative",
    "scared": "negative",
    "confused": "negative",
    "tired": "neutral",
}

# Wording that shows up in app builds and hand-edited exports.
ALIASES = {
    "afraid": "scared",
    "worried": "anxious",
    "nervous": "anxious",
    "mad": "angry",
    "upset": "sad",
    "sleepy": "tired",
    "fine": "okay",
    "good": "happy",
    "great": "happy",
    "relaxed": "calm",
}

NEGATIVE_EMOTIONS = {e for e, v in EMOTION_VALENCE.items() if v == "negative"}

# ---------------------------------------------------------------- thresholds --

STREAK_LENGTH = 4

RECENCY_DAYS = 30

STREAK_WINDOW_DAYS = 7

MIN_DISTINCT_DAYS = 2

# Valences the "repeated" rule may fire on. Neutral is deliberately excluded:
# four "okay" in a row is the definition of an unremarkable week, and it was
# putting 147 non-events into the same file as children in distress.
REPEATED_VALENCES = {"positive"}

# A week that is mostly negative but never four in a row, because one neutral
# check-in breaks the run. Watch channel: it is a softer signal than a streak.
ENABLE_DENSITY_TRIGGER = True
DENSITY_WINDOW_DAYS = 7
DENSITY_MIN_ENTRIES = 6
DENSITY_MIN_NEGATIVE_SHARE = 0.7

# Four or more negatives inside one school day. MIN_DISTINCT_DAYS keeps an
# ordinary cluster out of the alert channel - one rough morning is not a rough
# week - but a big enough cluster is its own event, not a weaker version of a
# streak, so at SAME_DAY_ALERT_LENGTH it escalates.
ENABLE_SAME_DAY_CLUSTER = True
SAME_DAY_CLUSTER_LENGTH = 4
SAME_DAY_ALERT_LENGTH = 6

# Where each trigger's findings go.
#   alert    the queue a teacher is expected to read
#   watch    dashboard context, concern-shaped but not paging
#   positive good news, kept out of both of the above
TRIGGER_CHANNELS = {
    "negative": "alert",
    "same_day_cluster": "watch",   # overridden to alert at SAME_DAY_ALERT_LENGTH
    "density": "watch",
    "repeated": "positive",
}
ALERT_TRIGGERS = {t for t, c in TRIGGER_CHANNELS.items() if c == "alert"}
WATCH_TRIGGERS = {t for t, c in TRIGGER_CHANNELS.items() if c == "watch"}

# Anchor for the recency cutoff. None means "the latest timestamp in the data",
# which keeps runs reproducible as the dataset ages. Set a datetime to pin it,
# or use datetime.now() for a live pipeline.
REFERENCE_TIME = None

JUDGE_CASES_PER_TYPE = 12
JUDGE_SEED = 42

DATA_DIR = os.path.dirname(os.path.abspath(__file__))


# ------------------------------------------------------------------ parsing --

def normalize_emotion(raw):
    """Lowercase and de-alias an emotion label. None if unusable/unknown."""
    if not isinstance(raw, str):
        return None
    key = raw.strip().lower()
    key = ALIASES.get(key, key)
    return key if key in EMOTION_VALENCE else None


def _parse_clock(raw):
    """'09:41', '09:41 AM' or '9:41:00 PM' -> (hour, minute). None if unusable."""
    text = raw.strip().upper().replace(" ", " ")
    meridiem = None
    for suffix in ("AM", "PM"):
        if text.endswith(suffix):
            meridiem = suffix
            text = text[: -len(suffix)].strip()
            break
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if meridiem == "PM" and hour != 12:
        hour += 12
    elif meridiem == "AM" and hour == 12:
        hour = 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return hour, minute


def parse_ts(entry):
    """Timestamp of an entry in either schema. None if unusable.

    Generated data uses {"ts": "2026-07-14T09:12:00"}.
    Focus Bridge exports use {"iso": "2026-07-14", "time": "09:12 AM", ...}.
    """
    raw = entry.get("ts")
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

    day = entry.get("iso") or entry.get("date")
    if not isinstance(day, str) or not day:
        return None
    try:
        base = datetime.fromisoformat(day[:10])
    except ValueError:
        return None
    clock = entry.get("time")
    if isinstance(clock, str) and clock:
        parsed = _parse_clock(clock)
        if parsed:
            return base.replace(hour=parsed[0], minute=parsed[1])
    return base


def read_log(student, problems):
    """Clean, sorted (ts, emotion) entries for one student.

    A row with an unreadable timestamp cannot be placed in the sequence, so it
    is dropped. A row that has a usable timestamp but an emotion we do not
    recognize is kept with emotion None: it still breaks a run. Dropping it
    outright would make the check-ins on either side look consecutive and
    manufacture a streak out of a gap we could not read.
    """
    entries = []
    for raw in student.get("emotion_log") or []:
        if not isinstance(raw, dict):
            problems["not_an_object"] += 1
            continue
        ts = parse_ts(raw)
        if ts is None:
            problems["bad_timestamp"] += 1
            continue
        emotion = normalize_emotion(raw.get("emotion") or raw.get("label"))
        if emotion is None:
            problems["unknown_emotion"] += 1
        entries.append({"ts": ts, "emotion": emotion})
    # Tie-break on the label so two exports of the same check-ins in different
    # row orders produce the same streaks. "Consecutive" therefore means
    # adjacent after sorting by timestamp, ties broken by emotion name.
    entries.sort(key=lambda e: (e["ts"], e["emotion"] or ""))
    return entries


# ----------------------------------------------------------------- detection --

def distinct_days(run):
    return len({e["ts"].date() for e in run})


def bounded_windows(run):
    """Maximal sub-runs of `run` that satisfy every constraint: at least
    STREAK_LENGTH entries, spanning no more than STREAK_WINDOW_DAYS and touching
    at least MIN_DISTINCT_DAYS calendar days. Windows fully contained in another
    are dropped - a contained window can never beat its parent on day count."""
    limit = timedelta(days=STREAK_WINDOW_DAYS)
    windows = []
    n = len(run)
    end = 0
    last_end = -1
    for start in range(n):
        if end < start:
            end = start
        while end + 1 < n and run[end + 1]["ts"] - run[start]["ts"] <= limit:
            end += 1
        if end - start + 1 >= STREAK_LENGTH and end > last_end:
            window = run[start:end + 1]
            if distinct_days(window) >= MIN_DISTINCT_DAYS:
                windows.append(window)
            last_end = end
    return windows


def as_streak(run, streak_type, channel=None):
    emotions = [e["emotion"] for e in run]
    valences = {EMOTION_VALENCE[e] for e in emotions if e is not None} or {"unknown"}
    streak = {
        "type": streak_type,
        "channel": channel or TRIGGER_CHANNELS[streak_type],
        "valence": "negative" if valences == {"negative"} else (
            "positive" if valences == {"positive"} else "mixed" if len(valences) > 1
            else valences.pop()),
        "start_ts": run[0]["ts"].isoformat(),
        "end_ts": run[-1]["ts"].isoformat(),
        "length": len(run),
        "distinct_days": distinct_days(run),
        "span_days": (run[-1]["ts"].date() - run[0]["ts"].date()).days,
        "emotions": emotions,
    }
    if streak_type == "repeated":
        streak["emotion"] = emotions[0]
    return streak


def find_negative_streaks(emotion_log):
    """One record per uninterrupted negative run that contains a qualifying
    window.

    The window bound decides *whether* a run counts; it does not chop the
    reported extent into pieces. Emitting each maximal window separately split
    523 students' single continuous run across up to 26 records, which both
    misdescribed the episode and inflated priority, since two records read as
    two separate episodes.
    """
    streaks = []
    run = []

    def flush(run):
        windows = bounded_windows(run)
        if not windows:
            return
        streak = as_streak(run, "negative")
        tight = min(windows, key=lambda w: w[-1]["ts"] - w[0]["ts"])
        # What actually tripped the rule, kept alongside the full episode.
        streak["trigger_length"] = len(tight)
        streak["trigger_span_days"] = (tight[-1]["ts"].date() - tight[0]["ts"].date()).days
        streaks.append(streak)

    for entry in emotion_log:
        if entry["emotion"] in NEGATIVE_EMOTIONS:
            run.append(entry)
            continue
        flush(run)
        run = []
    flush(run)
    return streaks


def _repeatable(run):
    """True if this run is of an emotion the repeated rule fires on."""
    return bool(run) and run[0]["emotion"] is not None \
        and EMOTION_VALENCE[run[0]["emotion"]] in REPEATED_VALENCES


def find_repeated_streaks(emotion_log):
    """The same positive emotion repeated, within the streak window.

    Neutral emotions are excluded by REPEATED_VALENCES - see the note there.
    """
    streaks = []
    run = []
    for entry in emotion_log:
        if entry["emotion"] is None:  # unreadable check-in, breaks the run
            if _repeatable(run):
                streaks.extend(bounded_windows(run))
            run = []
            continue
        if run and entry["emotion"] == run[0]["emotion"]:
            run.append(entry)
            continue
        if _repeatable(run):
            streaks.extend(bounded_windows(run))
        run = [entry]
    if _repeatable(run):
        streaks.extend(bounded_windows(run))
    return [as_streak(w, "repeated") for w in streaks]


def find_same_day_clusters(emotion_log):
    """4+ negatives inside one school day, for runs that raised no alert.

    Guarded on `bounded_windows(run)` being empty so a run that already
    produced a negative streak is not also reported here - the alert covers it.
    """
    if not ENABLE_SAME_DAY_CLUSTER:
        return []
    clusters = []

    def flush(run):
        if len(run) < SAME_DAY_CLUSTER_LENGTH or bounded_windows(run):
            return
        by_day = {}
        for entry in run:
            by_day.setdefault(entry["ts"].date(), []).append(entry)
        for _, entries in sorted(by_day.items()):
            if len(entries) >= SAME_DAY_CLUSTER_LENGTH:
                channel = "alert" if len(entries) >= SAME_DAY_ALERT_LENGTH else "watch"
                clusters.append(as_streak(entries, "same_day_cluster", channel))

    run = []
    for entry in emotion_log:
        if entry["emotion"] in NEGATIVE_EMOTIONS:
            run.append(entry)
            continue
        flush(run)
        run = []
    flush(run)
    return clusters


def find_density_streaks(emotion_log, covered=()):
    """Sustained mostly-negative periods, merged into one record each.

    Overlapping qualifying windows describe the same period, so they are
    unioned rather than emitted separately - the same fix applied to negative
    runs, and for the same reason: one student's rough fortnight was arriving
    as up to 62 records.

    `covered` holds (start, end) extents of the alert-channel streaks already
    found for this student. A density period sitting entirely inside one of
    them is dropped: the trigger exists to catch what the streak rule misses,
    and a run of consecutive negatives is by definition also dense, so without
    this every alert would be shadowed by a duplicate watch record.
    """
    if not ENABLE_DENSITY_TRIGGER:
        return []
    limit = timedelta(days=DENSITY_WINDOW_DAYS)

    def share(a, b):
        window = emotion_log[a:b + 1]
        return sum(1 for e in window if e["emotion"] in NEGATIVE_EMOTIONS) / len(window)

    spans = []
    n = len(emotion_log)
    end = 0
    for start in range(n):
        if end < start:
            end = start
        while end + 1 < n and emotion_log[end + 1]["ts"] - emotion_log[start]["ts"] <= limit:
            end += 1
        window = emotion_log[start:end + 1]
        if len(window) < DENSITY_MIN_ENTRIES:
            continue
        if share(start, end) >= DENSITY_MIN_NEGATIVE_SHARE \
                and distinct_days(window) >= MIN_DISTINCT_DAYS:
            # Merge into the previous span only where the two overlap AND the
            # union still clears the bar. Two qualifying windows can union into
            # something that does not qualify, and a record must not claim a
            # density it does not have.
            if spans and start <= spans[-1][1]:
                merged_end = max(spans[-1][1], end)
                if share(spans[-1][0], merged_end) >= DENSITY_MIN_NEGATIVE_SHARE:
                    spans[-1][1] = merged_end
                    continue
            spans.append([start, end])

    hits = []
    for start, end in spans:
        window = emotion_log[start:end + 1]
        lo_iso, hi_iso = window[0]["ts"].isoformat(), window[-1]["ts"].isoformat()
        if any(lo <= lo_iso and hi_iso <= hi for lo, hi in covered):
            continue
        negative = sum(1 for e in window if e["emotion"] in NEGATIVE_EMOTIONS)
        streak = as_streak(window, "density")
        # Reported on the merged extent, which can differ from the individual
        # windows that qualified, so it is stated rather than assumed.
        streak["negative_share"] = round(negative / len(window), 3)
        hits.append(streak)
    return hits


def find_all_streaks(emotion_log):
    """Every trigger for one student. Order matters: the alert-channel results
    are found first so the density trigger can skip what they already cover."""
    negative = find_negative_streaks(emotion_log)
    same_day = find_same_day_clusters(emotion_log)
    covered = [(s["start_ts"], s["end_ts"]) for s in negative + same_day
               if s["channel"] == "alert"]
    return (negative + same_day + find_repeated_streaks(emotion_log)
            + find_density_streaks(emotion_log, covered))


def summarize(streaks, recent):
    """Per-student rollup that a teacher dashboard can sort on.

    Only alert-channel streaks set priority. A student whose whole case is
    watch-channel stays at "watch" no matter how many soft signals they trip,
    so adding softer triggers cannot inflate the alert queue.
    """
    alerts = [s for s in streaks if s["channel"] == "alert"]
    watch = [s for s in streaks if s["channel"] == "watch"]
    negative_entries = sum(1 for e in recent if e["emotion"] in NEGATIVE_EMOTIONS)

    # Two alerts now means two genuinely separate episodes - before runs were
    # merged, it could mean one long run seen through two windows.
    if len(alerts) >= 2 or any(s["length"] >= 6 and s["distinct_days"] >= 3
                               for s in alerts):
        priority = "high"
    elif alerts:
        priority = "medium"
    elif watch:
        priority = "watch"
    else:
        priority = "info"

    return {
        "channel": "alert" if alerts else "watch" if watch else "positive",
        "priority": priority,
        "streak_count": len(streaks),
        "alert_streak_count": len(alerts),
        "watch_streak_count": len(watch),
        "watch_reasons": sorted({s["type"] for s in watch}),
        "positive_streak_count": len(streaks) - len(alerts) - len(watch),
        "longest_alert_streak": max((s["length"] for s in alerts), default=0),
        "alert_days": len({d for s in alerts
                           for d in (s["start_ts"][:10], s["end_ts"][:10])}),
        "first_flag_ts": min(s["start_ts"] for s in streaks),
        "last_flag_ts": max(s["end_ts"] for s in streaks),
        "recent_entry_count": len(recent),
        "recent_negative_share": round(negative_entries / len(recent), 3) if recent else 0.0,
    }


# --------------------------------------------------------------------- files --

def iter_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def latest_timestamp(path):
    latest = None
    for student in iter_jsonl(path):
        for entry in student.get("emotion_log") or []:
            if not isinstance(entry, dict):
                continue
            ts = parse_ts(entry)
            if ts is not None and (latest is None or ts > latest):
                latest = ts
    return latest


def load_classrooms(path):
    """Map classroom code -> teacher and school context."""
    classrooms = {}
    for room in iter_jsonl(path):
        classrooms[room.get("code")] = {
            "classroom_name": room.get("name"),
            "teacher_id": room.get("teacher_id"),
            "teacher_name": room.get("teacher_name"),
            "school_name": room.get("school_name"),
            "school_zip": room.get("zip"),
            "name_mode": room.get("name_mode"),
        }
    return classrooms


PRIORITY_RANK = {"high": 0, "medium": 1, "watch": 2, "info": 3}


def write_by_classroom(path, classrooms, by_code):
    """One record per classroom that holds at least one flagged student."""
    records = []
    for code, students in by_code.items():
        room = classrooms.get(code, {})
        students.sort(key=lambda s: (PRIORITY_RANK[s["summary"]["priority"]],
                                     -s["summary"]["alert_streak_count"],
                                     s["student_id"]))
        records.append({
            "classroom_code": code,
            **room,
            "flagged_student_count": len(students),
            "alert_student_count": sum(1 for s in students
                                       if s["summary"]["channel"] == "alert"),
            "high_priority_count": sum(1 for s in students
                                       if s["summary"]["priority"] == "high"),
            "students": students,
        })

    records.sort(key=lambda r: (-r["high_priority_count"],
                                -r["alert_student_count"],
                                r["classroom_code"]))
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    return records


# ---------------------------------------------------------------- judge prep --

def collect_judge_cases(student, recent, streaks, cases):
    """Boundary cases worth a second opinion, bucketed by what makes them hard."""
    stub = {"student_id": student.get("id"),
            "classroom_code": student.get("classroom_code")}

    def window_text(entries):
        return [f"{e['ts'].isoformat()} {e['emotion'] or 'unreadable'}"
                for e in entries]

    for streak in streaks:
        # These two buckets probe the alert thresholds, so they only look at
        # alert-channel streaks. Density windows are 7 days by construction and
        # would otherwise swamp at_window_edge with non-evidence.
        if streak["channel"] == "alert":
            if streak["span_days"] >= STREAK_WINDOW_DAYS:
                cases["at_window_edge"].append({**stub, "streak": streak})
            if streak["distinct_days"] == MIN_DISTINCT_DAYS and streak["span_days"] <= 1:
                cases["barely_multi_day"].append({**stub, "streak": streak})
        if streak["type"] == "repeated" and \
                EMOTION_VALENCE[streak["emotion"]] == "positive":
            cases["repeated_positive"].append({**stub, "streak": streak})

    # Runs that clear every rule except the day-spread rule.
    run = []
    for entry in recent + [None]:
        if entry is not None and entry["emotion"] in NEGATIVE_EMOTIONS:
            run.append(entry)
            continue
        if len(run) >= STREAK_LENGTH and distinct_days(run) < MIN_DISTINCT_DAYS:
            cases["same_day_only"].append({**stub, "entries": window_text(run)})
        run = []

    # Three negatives in a row: one check-in short of a flag. Split by whether
    # the wider period corroborates it - the audit proposed promoting only the
    # corroborated ones, so the two groups need separate case counts.
    share = (sum(1 for e in recent if e["emotion"] in NEGATIVE_EMOTIONS) / len(recent)
             if recent else 0.0)
    run = []
    for entry in recent + [None]:
        if entry is not None and entry["emotion"] in NEGATIVE_EMOTIONS:
            run.append(entry)
            continue
        if len(run) == STREAK_LENGTH - 1 and distinct_days(run) >= MIN_DISTINCT_DAYS:
            bucket = "one_short_corroborated" \
                if share >= DENSITY_MIN_NEGATIVE_SHARE else "one_short"
            cases[bucket].append({**stub, "entries": window_text(run),
                                  "recent_negative_share": round(share, 3)})
        run = []

    # Students whose entire case is watch-channel: the tier the audit asked us
    # not to assume is correct.
    if streaks and all(s["channel"] == "watch" for s in streaks):
        cases["watch_only"].append({
            **stub,
            "reasons": sorted({s["type"] for s in streaks}),
            "recent_negative_share": round(share, 3),
            "streaks": streaks,
        })

    # Mostly-negative week broken up by a single neutral or positive check-in.
    limit = timedelta(days=DENSITY_WINDOW_DAYS)
    n = len(recent)
    end = 0
    for start in range(n):
        if end < start:
            end = start
        while end + 1 < n and recent[end + 1]["ts"] - recent[start]["ts"] <= limit:
            end += 1
        window = recent[start:end + 1]
        if len(window) < DENSITY_MIN_ENTRIES:
            continue
        negative = sum(1 for e in window if e["emotion"] in NEGATIVE_EMOTIONS)
        if negative / len(window) >= DENSITY_MIN_NEGATIVE_SHARE and not streaks:
            cases["dense_but_unflagged"].append({**stub, "entries": window_text(window)})
            break


def write_judge_cases(path, cases):
    rng = random.Random(JUDGE_SEED)
    with open(path, "w", encoding="utf-8") as f:
        for case_type in sorted(cases):
            pool = cases[case_type]
            sample = pool if len(pool) <= JUDGE_CASES_PER_TYPE \
                else rng.sample(pool, JUDGE_CASES_PER_TYPE)
            for case in sample:
                f.write(json.dumps({"case_type": case_type,
                                    "population": len(pool), **case}) + "\n")
    return {k: len(v) for k, v in sorted(cases.items())}


# ---------------------------------------------------------------------- main --

def parse_args():
    """CLI overrides for the two knobs a re-run needs.

    Detection is untouched by these - `--reference` only moves where the recency
    window ends, which REFERENCE_TIME already exists to do, and `--suffix` only
    renames the outputs. Together they let the pipeline be run "as of" an
    earlier date without disturbing the current run's files, which is how
    notify.py gets a real previous state to compare against instead of a
    fabricated one.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reference", metavar="ISO",
                        help="pin the end of the recency window, e.g. "
                             "2026-07-31T13:08:00 (default: latest timestamp "
                             "in students.jsonl)")
    parser.add_argument("--suffix", default="",
                        help="append to every output filename, e.g. _prev")
    return parser.parse_args()


def main():
    args = parse_args()
    tag = args.suffix

    students_path = os.path.join(DATA_DIR, "students.jsonl")
    classrooms_path = os.path.join(DATA_DIR, "classrooms.jsonl")
    out_path = os.path.join(DATA_DIR, f"insights{tag}.jsonl")
    by_classroom_path = os.path.join(DATA_DIR, f"flagged_by_classroom{tag}.jsonl")
    meta_path = os.path.join(DATA_DIR, f"insights_meta{tag}.json")
    judge_path = os.path.join(DATA_DIR, f"judge_cases{tag}.jsonl")

    problems = Counter()  # entries dropped during parsing, reported at the end
    if args.reference:
        try:
            reference = datetime.fromisoformat(args.reference)
        except ValueError:
            raise SystemExit(f"--reference is not an ISO datetime: {args.reference!r}")
    else:
        reference = REFERENCE_TIME or latest_timestamp(students_path)
    if reference is None:
        raise SystemExit("no readable timestamps in students.jsonl")
    cutoff = reference - timedelta(days=RECENCY_DAYS)
    print(f"reference {reference.isoformat()}  cutoff {cutoff.isoformat()}")

    classrooms = load_classrooms(classrooms_path)
    by_code = {}
    cases = {name: [] for name in ("at_window_edge", "barely_multi_day",
                                   "repeated_positive", "same_day_only",
                                   "one_short", "one_short_corroborated",
                                   "dense_but_unflagged", "watch_only")}

    scanned = 0
    flagged = 0
    priorities = Counter()
    channels = Counter()
    trigger_students = Counter()
    emotion_counts = Counter()
    with open(students_path, encoding="utf-8") as src, \
            open(out_path, "w", encoding="utf-8") as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            student = json.loads(line)
            scanned += 1

            log = read_log(student, problems)
            recent = [e for e in log if e["ts"] >= cutoff]
            emotion_counts.update(e["emotion"] for e in recent
                                  if e["emotion"] is not None)

            streaks = find_all_streaks(recent)
            collect_judge_cases(student, recent, streaks, cases)
            if not streaks:
                continue

            flagged += 1
            streaks.sort(key=lambda s: s["start_ts"])
            summary = summarize(streaks, recent)
            priorities[summary["priority"]] += 1
            channels[summary["channel"]] += 1
            for name in {s["type"] for s in streaks}:
                trigger_students[name] += 1

            code = student.get("classroom_code")
            room = classrooms.get(code, {})
            record = {
                "student_id": student.get("id"),
                "student_name": student.get("name"),
                "classroom_code": code,
                # classrooms.jsonl metadata merged in so insights.jsonl stands
                # alone - no join needed to route a flag to a teacher.
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
            dst.write(json.dumps(record) + "\n")

            by_code.setdefault(code, []).append({
                "student_id": record["student_id"],
                "student_name": record["student_name"],
                "summary": summary,
                "streaks": streaks,
            })

    rooms = write_by_classroom(by_classroom_path, classrooms, by_code)
    unknown = sorted({r["classroom_code"] for r in rooms
                      if r.get("teacher_id") is None})
    case_totals = write_judge_cases(judge_path, cases)

    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "reference_time": reference.isoformat(),
        "cutoff": cutoff.isoformat(),
        "config": {
            "streak_length": STREAK_LENGTH,
            "recency_days": RECENCY_DAYS,
            "streak_window_days": STREAK_WINDOW_DAYS,
            "min_distinct_days": MIN_DISTINCT_DAYS,
            "repeated_valences": sorted(REPEATED_VALENCES),
            "density_trigger_enabled": ENABLE_DENSITY_TRIGGER,
            "same_day_cluster_enabled": ENABLE_SAME_DAY_CLUSTER,
            "same_day_alert_length": SAME_DAY_ALERT_LENGTH,
            "trigger_channels": dict(sorted(TRIGGER_CHANNELS.items())),
            "negative_emotions": sorted(NEGATIVE_EMOTIONS),
        },
        "totals": {
            "students_scanned": scanned,
            "students_flagged": flagged,
            "classrooms_with_flags": len(rooms),
            "by_channel": dict(channels),
            "by_priority": dict(priorities),
            "students_by_trigger": dict(trigger_students),
            "recent_emotion_counts": dict(emotion_counts.most_common()),
            "dropped_entries": dict(problems),
            "unmatched_classroom_codes": unknown,
        },
        "judge_case_populations": case_totals,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"scanned {scanned} students")
    print(f"flagged {flagged} students -> {out_path}")
    print(f"  ALERT    channel: {channels['alert']} students "
          f"(high {priorities['high']}, medium {priorities['medium']})")
    print(f"  WATCH    channel: {channels['watch']} students")
    print(f"  POSITIVE channel: {channels['positive']} students")
    for name, count in sorted(trigger_students.items()):
        print(f"    {name} trigger -> {TRIGGER_CHANNELS[name]}: {count} students")
    print(f"{len(rooms)} classrooms -> {by_classroom_path}")
    print(f"judge cases -> {judge_path}  {case_totals}")
    print(f"run metadata -> {meta_path}")
    if problems:
        print(f"  dropped entries: {dict(problems)}")
    if unknown:
        print(f"  WARNING {len(unknown)} classroom codes not in classrooms.jsonl: "
              f"{unknown[:5]}")


if __name__ == "__main__":
    main()
