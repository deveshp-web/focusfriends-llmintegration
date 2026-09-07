#!/usr/bin/env python3
"""A Streamlit viewer for the detector's output.

Turns ``insights.jsonl`` - plus, when they exist, the AI summaries and the
review log - into something a teacher can actually look at: pick a classroom,
see who is flagged and why, read the AI suggestion if one has been generated,
and mark a flag reviewed or dismissed right from the page.

It writes through the **same** :mod:`focusbridge.review.store` the CLI uses, so
a status set here shows up there and vice versa. That is the whole reason the
store is a shared module rather than a function in each tool.

Run with::

    streamlit run viewer.py

This is a viewer, not a stage: it reads what the pipeline produced and writes
only review actions. If you are looking for where flags are *decided*, that is
``focusbridge/detect/``.
"""

from __future__ import annotations

import streamlit as st

from focusbridge.core.jsonio import read_jsonl_list
from focusbridge.core.paths import DataPaths
from focusbridge.core.triage import priority_rank
from focusbridge.review.store import append_review, load_reviews

#: Channel -> the dot shown beside a student. Colour is never the only cue: the
#: channel word is always printed next to it, for the same reason the dashboard
#: pairs every status colour with an icon and a label.
CHANNEL_BADGE = {"alert": "🔴", "watch": "🟡", "positive": "🟢"}

PATHS = DataPaths()


@st.cache_data
def load_records(path_text):
    """Read a JSONL file, cached by Streamlit between reruns.

    Streamlit re-executes this whole script on every interaction, so without
    the cache a click would re-read insights.jsonl from disk. The argument is
    the path *as text* because Streamlit's cache keys on the arguments, and
    those have to be hashable and stable.

    The cache is cleared explicitly after a review is written - see
    :func:`render_student` - because that is the one action that makes the file
    on disk newer than the cache.
    """
    return read_jsonl_list(path_text, skip_missing=True)


def render_streak(streak):
    """One finding, in the words the detector used for it."""
    if streak["type"] == "repeated":
        st.write(f"**{streak['type']}** — '{streak['emotion']}' x {streak['length']} "
                 f"({streak['distinct_days']} day(s))")
        return
    st.write(f"**{streak['type']}** ({streak['valence']}) — {streak['length']} entries, "
             f"{streak['distinct_days']} day(s), span {streak['span_days']}d")
    st.caption(", ".join(streak["emotions"]))


def render_student(record, reviews, suggestions, reviewer_name):
    """One collapsible card: the findings, any AI summary, and the review buttons."""
    flag_id = record["flag_id"]
    review = reviews.get(flag_id)

    header = (f"{CHANNEL_BADGE.get(record['channel'], '')} {record['student_name']} "
              f"— {record['channel']}/{record['priority']}")
    if review:
        header += f"   ✅ {review['action']}"

    with st.expander(header):
        for streak in record["streaks"]:
            render_streak(streak)

        suggestion = suggestions.get(flag_id)
        if suggestion:
            st.markdown(f"**AI summary:** {suggestion['llm_summary']}")
            for line in suggestion["llm_suggestions"]:
                st.markdown(f"- {line}")
        else:
            st.caption("No AI suggestion generated yet for this flag "
                       "(run generate_insights.py).")

        if review:
            note = f' — "{review["note"]}"' if review["note"] else ""
            st.info(f"Already **{review['action']}** by "
                    f"{review['reviewed_by'] or 'someone'} on "
                    f"{review['reviewed_at']}{note}")

        columns = st.columns(3)
        buttons = [("Mark reviewed", "reviewed"),
                   ("Dismiss", "dismissed"),
                   ("Mark acted on", "acted_on")]
        for column, (label, action) in zip(columns, buttons):
            # The key must be unique across the whole page, hence flag_id+action.
            if column.button(label, key=f"{flag_id}-{action}"):
                append_review(flag_id, record["student_id"],
                              record["classroom_code"], action,
                              reviewed_by=reviewer_name, path=PATHS.flag_reviews)
                st.cache_data.clear()  # the file on disk is now newer than the cache
                st.rerun()


def main():
    st.set_page_config(page_title="Focus Bridge - Flagged Students", layout="wide")
    st.title("Flagged Students")

    insights = load_records(str(PATHS.insights))
    if not insights:
        st.warning(f"No data in {PATHS.insights.name} - run detect_patterns.py first.")
        return

    reviews = load_reviews(PATHS.flag_reviews)
    suggestions = {row["flag_id"]: row
                   for row in load_records(str(PATHS.llm_suggestions))}

    reviewer_name = st.sidebar.text_input("Your name (for review attribution)")
    channel = st.sidebar.selectbox("Channel",
                                   ["alert", "watch", "positive", "all"], index=0)
    hide_reviewed = st.sidebar.checkbox("Hide already-reviewed flags", value=True)

    by_classroom = {}
    for record in insights:
        by_classroom.setdefault(record["classroom_code"], []).append(record)

    def high_priority_count(code):
        return sum(1 for record in by_classroom[code] if record["priority"] == "high")

    # Rooms that need the most attention first - the same ordering rule the
    # dashboard and the digests use.
    codes = sorted(by_classroom, key=lambda code: -high_priority_count(code))
    labels = {code: f"{by_classroom[code][0]['classroom_name'] or code} "
                    f"({high_priority_count(code)} high priority)"
              for code in codes}

    chosen = st.sidebar.selectbox("Classroom", codes,
                                  format_func=lambda code: labels[code])

    students = by_classroom[chosen]
    if channel != "all":
        students = [record for record in students if record["channel"] == channel]
    if hide_reviewed:
        students = [record for record in students
                    if record["flag_id"] not in reviews]
    students.sort(key=lambda record: priority_rank(record["priority"]))

    st.subheader(labels[chosen])
    st.caption(f"{len(students)} student(s) shown")

    for record in students:
        render_student(record, reviews, suggestions, reviewer_name)


if __name__ == "__main__":
    main()
