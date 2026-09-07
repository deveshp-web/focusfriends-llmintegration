#!/usr/bin/env python3
"""
STEP 2 of the teacher-insights feature, RECONNECTED to the new
detect_patterns.py output.

The original version of this file (v1/generate_insights.py at the project
root) was written against a flatter insights.jsonl - one flag per line, a
single "evidence" list, no channel/priority. detect_patterns.py now groups
ALL of a student's findings into one record with a channel
(alert/watch/positive), a priority, and a list of "streaks" (each finding,
with its own type, valence, length, and emotions). This file reads THAT
shape.

By default it only asks the AI about ALERT-channel students - the ones a
teacher is actually meant to act on. WATCH is dashboard context, and
POSITIVE is good news; neither needs a generated "here's what to do"
suggestion the same way alert does. Use --channel to override.

Each result carries the student's flag_id, so it can be joined back to
insights.jsonl and to flag_reviews.jsonl (see review_flags.py) later -
e.g. a viewer can show "here's the AI summary" next to "here's whether a
teacher already dismissed this."

Usage:
    .venv/bin/python3 generate_insights.py --limit 5
    .venv/bin/python3 generate_insights.py --channel watch --limit 3
"""

import argparse
import json

import ollama

DEFAULT_MODEL = "llama3.1:8b"

# One shared opening (the non-negotiable safety rules), then a channel-
# specific paragraph appended below it at run time. Same JSON-only
# requirement as the original tutorial version.
BASE_SYSTEM_PROMPT = """You are a supportive assistant helping teachers in a \
special-needs classroom understand patterns in a student's logged \
emotions. You are NOT a doctor or therapist.

Rules you must follow:
- Never diagnose, guess at a medical/psychological cause, or use clinical
  language (no "anxiety disorder", "depression", etc).
- Only describe what the data shows and suggest simple, supportive,
  classroom-appropriate next steps.
- Keep it warm and practical - this is for a busy teacher to read in a
  few seconds.

You must reply with ONLY valid JSON in exactly this shape, nothing else:
{
  "summary": "one or two plain-English sentences describing the pattern",
  "suggestions": ["short actionable suggestion 1", "short actionable suggestion 2"],
  "severity": "low" or "medium" or "high"
}
"""

CHANNEL_GUIDANCE = {
    "alert": "This student tripped an ALERT-level pattern - a real streak "
             "of negative check-ins. The teacher is expected to act on this "
             "today, so be direct about what's happening.",
    "watch": "This student tripped a WATCH-level pattern - a softer signal "
             "(a rough week, or a rough single day) that didn't reach alert "
             "level. Frame this as 'worth keeping an eye on', not urgent.",
    "positive": "This student repeated the SAME POSITIVE emotion several "
                "times in a row. This is good news, not a concern - write "
                "the summary as a small celebration, and keep suggestions "
                "to 'what's working, keep doing it' rather than an "
                "intervention.",
}


def load_jsonl(path):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def describe_streak(streak):
    """One line of plain text for one finding inside a student's record."""
    days = f"{streak['distinct_days']} day(s), span {streak['span_days']}"
    if streak["type"] == "repeated":
        return (f"- repeated '{streak['emotion']}' {streak['length']} times "
                f"in a row ({days})")
    emotions = ", ".join(streak["emotions"])
    return (f"- {streak['type']} ({streak['valence']}): {streak['length']} "
            f"entries [{emotions}] ({days})")


def build_user_prompt(record):
    lines = [
        f"Student first name: {record['student_name']}",
        f"Channel: {record['channel']}  Priority: {record['priority']}",
        f"Recent negative-emotion share: {record['summary']['recent_negative_share']:.0%}",
        "",
        "Findings:",
    ]
    for streak in record["streaks"]:
        lines.append(describe_streak(streak))
    lines.append("\nWrite the JSON response now.")
    return "\n".join(lines)


def ask_ollama(model, system_prompt, user_prompt):
    response = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        format="json",
    )
    raw_text = response["message"]["content"]
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        print("  [warning] model did not return valid JSON, skipping")
        return None
    required = {"summary", "suggestions", "severity"}
    if not required.issubset(parsed.keys()):
        print(f"  [warning] response missing keys {required - parsed.keys()}, skipping")
        return None
    return parsed


def main():
    parser = argparse.ArgumentParser(description="Generate AI summaries for detect_patterns.py output.")
    parser.add_argument("--insights-file", default="insights.jsonl")
    parser.add_argument("--out", default="insights_with_suggestions.jsonl")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=5,
                         help="each call takes ~15-25s on a laptop CPU")
    parser.add_argument("--channel", default="alert", choices=["alert", "watch", "positive", "all"])
    args = parser.parse_args()

    all_records = load_jsonl(args.insights_file)
    if args.channel != "all":
        all_records = [r for r in all_records if r["channel"] == args.channel]

    to_process = all_records[:args.limit]
    print(f"{len(all_records)} record(s) match channel={args.channel}, "
          f"processing {len(to_process)} (use --limit to change this).")

    results = []
    for i, record in enumerate(to_process, start=1):
        print(f"[{i}/{len(to_process)}] {record['student_name']} "
              f"({record['channel']}/{record['priority']})...")

        system_prompt = BASE_SYSTEM_PROMPT + "\n" + CHANNEL_GUIDANCE[record["channel"]]
        user_prompt = build_user_prompt(record)
        ai_response = ask_ollama(args.model, system_prompt, user_prompt)
        if ai_response is None:
            continue

        combined = {
            "flag_id": record["flag_id"],
            "student_id": record["student_id"],
            "student_name": record["student_name"],
            "classroom_code": record["classroom_code"],
            "classroom_name": record["classroom_name"],
            "teacher_id": record["teacher_id"],
            "channel": record["channel"],
            "priority": record["priority"],
            "llm_model": args.model,
            "llm_summary": ai_response["summary"],
            "llm_suggestions": ai_response["suggestions"],
            "llm_severity": ai_response["severity"],
        }
        results.append(combined)
        print(f"    {ai_response['summary']}")
        for s in ai_response["suggestions"]:
            print(f"    - {s}")

    with open(args.out, "w") as f:
        for row in results:
            f.write(json.dumps(row) + "\n")
    print(f"\nwrote {len(results)} AI-generated insights to {args.out}")


if __name__ == "__main__":
    main()
