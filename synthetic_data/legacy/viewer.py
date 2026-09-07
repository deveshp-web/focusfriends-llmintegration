#!/usr/bin/env python3
"""
Minimal Streamlit viewer for detect_patterns.py's output.

This turns insights.jsonl / flagged_by_classroom.jsonl / an optional
insights_with_suggestions.jsonl / flag_reviews.jsonl from raw JSON files
into something a teacher could actually look at: pick a classroom, see
who's flagged and why, read the AI suggestion if one's been generated,
and mark a flag reviewed/dismissed right from the page - using the exact
same flag_review_store.py the CLI tool (review_flags.py) uses, so a
status set here shows up there too, and vice versa.

Run with:
    .venv/bin/streamlit run viewer.py
"""

import json
from pathlib import Path

import streamlit as st

from flag_review_store import append_review, load_reviews

INSIGHTS_PATH = "insights.jsonl"
SUGGESTIONS_PATH = "insights_with_suggestions.jsonl"

PRIORITY_ORDER = {"high": 0, "medium": 1, "watch": 2, "info": 3}
CHANNEL_BADGE = {"alert": "🔴", "watch": "🟡", "positive": "🟢"}


@st.cache_data
def load_jsonl(path):
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def render_streak(streak):
    if streak["type"] == "repeated":
        st.write(f"**{streak['type']}** — '{streak['emotion']}' x {streak['length']} "
                 f"({streak['distinct_days']} day(s))")
    else:
        st.write(f"**{streak['type']}** ({streak['valence']}) — {streak['length']} entries, "
                 f"{streak['distinct_days']} day(s), span {streak['span_days']}d")
        st.caption(", ".join(streak["emotions"]))


def render_student(record, reviews, suggestions, reviewer_name):
    flag_id = record["flag_id"]
    review = reviews.get(flag_id)
    badge = CHANNEL_BADGE.get(record["channel"], "")

    header = f"{badge} {record['student_name']} — {record['channel']}/{record['priority']}"
    if review:
        header += f"   ✅ {review['action']}"

    with st.expander(header):
        for streak in record["streaks"]:
            render_streak(streak)

        suggestion = suggestions.get(flag_id)
        if suggestion:
            st.markdown(f"**AI summary:** {suggestion['llm_summary']}")
            for s in suggestion["llm_suggestions"]:
                st.markdown(f"- {s}")
        else:
            st.caption("No AI suggestion generated yet for this flag "
                      "(run generate_insights.py).")

        if review:
            note = f' — "{review["note"]}"' if review["note"] else ""
            st.info(f"Already **{review['action']}** by "
                    f"{review['reviewed_by'] or 'someone'} on {review['reviewed_at']}{note}")

        cols = st.columns(3)
        actions = [("Mark reviewed", "reviewed"), ("Dismiss", "dismissed"), ("Mark acted on", "acted_on")]
        for col, (label, action) in zip(cols, actions):
            if col.button(label, key=f"{flag_id}-{action}"):
                append_review(flag_id, record["student_id"], record["classroom_code"],
                             action, reviewed_by=reviewer_name)
                st.cache_data.clear()
                st.rerun()


def main():
    st.set_page_config(page_title="Focus Friends - Flagged Students", layout="wide")
    st.title("Flagged Students")

    insights = load_jsonl(INSIGHTS_PATH)
    if not insights:
        st.warning(f"No data in {INSIGHTS_PATH} - run detect_patterns.py first.")
        return

    reviews = load_reviews()
    suggestions = {r["flag_id"]: r for r in load_jsonl(SUGGESTIONS_PATH)}

    reviewer_name = st.sidebar.text_input("Your name (for review attribution)")
    channel_filter = st.sidebar.selectbox("Channel", ["alert", "watch", "positive", "all"], index=0)
    hide_reviewed = st.sidebar.checkbox("Hide already-reviewed flags", value=True)

    by_classroom = {}
    for r in insights:
        by_classroom.setdefault(r["classroom_code"], []).append(r)

    classroom_codes = sorted(
        by_classroom.keys(),
        key=lambda code: -sum(1 for r in by_classroom[code] if r["priority"] == "high"),
    )
    labels = {
        code: f"{by_classroom[code][0]['classroom_name'] or code} "
              f"({sum(1 for r in by_classroom[code] if r['priority'] == 'high')} high priority)"
        for code in classroom_codes
    }
    chosen = st.sidebar.selectbox("Classroom", classroom_codes, format_func=lambda c: labels[c])

    students = by_classroom[chosen]
    if channel_filter != "all":
        students = [r for r in students if r["channel"] == channel_filter]
    if hide_reviewed:
        students = [r for r in students if r["flag_id"] not in reviews]
    students.sort(key=lambda r: PRIORITY_ORDER.get(r["priority"], 9))

    st.subheader(labels[chosen])
    st.caption(f"{len(students)} student(s) shown")

    for record in students:
        render_student(record, reviews, suggestions, reviewer_name)


if __name__ == "__main__":
    main()
