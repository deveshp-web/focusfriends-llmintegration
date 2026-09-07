#!/usr/bin/env python3
"""Optional extra: ask a local LLM to write a short summary of each flag.

WHERE THIS SITS
---------------
This is **not** one of the four pipeline stages, and nothing depends on it. The
detector and the recommender are deliberately rule-based, because a teacher has
to be able to disagree with a flag and see exactly which threshold produced it.
This script adds a plain-English gloss on top of that - useful, but never the
thing that decided anything.

Each result carries the record's ``flag_id``, so it joins back to
``insights.jsonl`` and to the review log: a viewer can show "here is the AI
summary" next to "here is whether a teacher already dismissed this".

By default it asks only about ALERT-channel students - the ones a teacher is
meant to act on. WATCH is dashboard context and POSITIVE is good news; neither
needs a generated "here is what to do". ``--channel`` overrides that.

Runs against a **local** Ollama model, so no student data leaves the machine.
That is not a performance choice - it is the reason this design is acceptable
at all for classroom data.

Usage::

    python generate_insights.py --limit 5
    python generate_insights.py --channel watch --limit 3
"""

from __future__ import annotations

import argparse
import json

import ollama

from focusbridge.core import cli
from focusbridge.core.jsonio import read_jsonl_list, write_jsonl

DEFAULT_MODEL = "llama3.1:8b"

#: The safety rules, sent with every request regardless of channel.
#:
#: The constraints are not decoration. This text is about children, read by a
#: busy adult who may act on it, and a model that offers a clinical-sounding
#: cause does real harm - so the boundary is stated explicitly rather than
#: hoped for.
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

#: Appended to the system prompt so the tone matches the channel. Good news
#: written in the register of an alert is worse than no good news at all.
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

#: Keys the reply must contain before it is accepted.
REQUIRED_KEYS = {"summary", "suggestions", "severity"}


# ---------------------------------------------------------------------------
# building the prompt
# ---------------------------------------------------------------------------

def describe_streak(streak):
    """One plain-text line describing a single finding.

    The model is given the detector's own vocabulary rather than raw check-ins,
    which keeps it summarising a decision that has already been made instead of
    quietly making a different one.
    """
    days = f"{streak['distinct_days']} day(s), span {streak['span_days']}"
    if streak["type"] == "repeated":
        return (f"- repeated '{streak['emotion']}' {streak['length']} times "
                f"in a row ({days})")
    emotions = ", ".join(streak["emotions"])
    return (f"- {streak['type']} ({streak['valence']}): {streak['length']} "
            f"entries [{emotions}] ({days})")


def build_user_prompt(record):
    """The per-student half of the prompt."""
    lines = [
        f"Student first name: {record['student_name']}",
        f"Channel: {record['channel']}  Priority: {record['priority']}",
        f"Recent negative-emotion share: "
        f"{record['summary']['recent_negative_share']:.0%}",
        "",
        "Findings:",
    ]
    lines.extend(describe_streak(streak) for streak in record["streaks"])
    lines.append("\nWrite the JSON response now.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# talking to the model
# ---------------------------------------------------------------------------

def ask_model(model, system_prompt, user_prompt):
    """One request. Returns the parsed reply, or ``None`` if it was unusable.

    Two validation steps, and both matter: a model asked for JSON does not
    always return JSON, and one that does may still omit a field. Returning
    ``None`` rather than raising means one bad reply costs one student's summary
    instead of the whole run.
    """
    response = ollama.chat(
        model=model,
        messages=[{"role": "system", "content": system_prompt},
                  {"role": "user", "content": user_prompt}],
        format="json",  # asks Ollama to constrain the output to valid JSON
    )

    try:
        parsed = json.loads(response["message"]["content"])
    except json.JSONDecodeError:
        print("  [warning] model did not return valid JSON, skipping")
        return None

    missing = REQUIRED_KEYS - parsed.keys()
    if missing:
        print(f"  [warning] response missing keys {missing}, skipping")
        return None
    return parsed


def build_output_record(record, reply, model):
    """Join the model's answer back onto the identifiers it belongs to.

    ``flag_id`` first, because that is the key everything else joins on.
    """
    return {
        "flag_id": record["flag_id"],
        "student_id": record["student_id"],
        "student_name": record["student_name"],
        "classroom_code": record["classroom_code"],
        "classroom_name": record["classroom_name"],
        "teacher_id": record["teacher_id"],
        "channel": record["channel"],
        "priority": record["priority"],
        "llm_model": model,
        "llm_summary": reply["summary"],
        "llm_suggestions": reply["suggestions"],
        "llm_severity": reply["severity"],
    }


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--out", default=None,
                        help="where to write the summaries (default: "
                             "insights_with_suggestions.jsonl beside the data)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=5,
                        help="how many students to process; each call takes "
                             "~15-25s on a laptop CPU")
    parser.add_argument("--channel", default="alert",
                        choices=["alert", "watch", "positive", "all"])
    cli.add_data_dir_argument(parser)
    args = parser.parse_args(argv)

    paths = cli.paths_from_args(args)
    cli.require(paths.insights, "run detect_patterns.py first")

    records = read_jsonl_list(paths.insights)
    if args.channel != "all":
        records = [record for record in records if record["channel"] == args.channel]

    batch = records[:args.limit]
    print(f"{len(records)} record(s) match channel={args.channel}, "
          f"processing {len(batch)} (use --limit to change this).")

    results = []
    for position, record in enumerate(batch, start=1):
        print(f"[{position}/{len(batch)}] {record['student_name']} "
              f"({record['channel']}/{record['priority']})...")

        system_prompt = (BASE_SYSTEM_PROMPT + "\n"
                         + CHANNEL_GUIDANCE[record["channel"]])
        reply = ask_model(args.model, system_prompt, build_user_prompt(record))
        if reply is None:
            continue

        results.append(build_output_record(record, reply, args.model))
        print(f"    {reply['summary']}")
        for suggestion in reply["suggestions"]:
            print(f"    - {suggestion}")

    out_path = args.out or paths.llm_suggestions
    write_jsonl(out_path, results)
    print(f"\nwrote {len(results)} AI-generated insights to {out_path}")


if __name__ == "__main__":
    main()
