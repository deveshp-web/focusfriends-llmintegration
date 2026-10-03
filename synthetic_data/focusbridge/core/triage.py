"""How urgent is this, and what do we call the student on screen?

WHY THIS FILE EXISTS
--------------------
"Triage" is the medical word for sorting patients by who needs attention first,
and it is exactly what this project does with flagged students. Two vocabularies
carry that idea, and *every* stage needs both:

  * **Channel** - which pile a finding goes in (alert / watch / positive).
  * **Priority** - how urgent it is within the alert pile (high / medium /
    watch / info).

Before this refactor the priority ordering was typed out **three times**, in
three files - and the copies had already drifted on what to do with a priority
they did not recognise. The detector indexed the table directly and would raise
``KeyError``; the review CLI and the viewer each fell back to a sort rank of
``9``; and the change feed used yet another sentinel, ``max(ranks) + 1``, which
is ``4``. So an unknown priority sorted differently in three places and crashed
in a fourth.

Three copies of a sort order is three chances for two screens to disagree about
who is at the top of the list, which for this project means disagreeing about
which child a teacher looks at first.

:func:`display_name` lives here for the same reason. It used to live inside the
recommender, which meant the *notifier* had to import from the *recommender*
just to format a name - a stage depending on another stage for something
neither of them owns.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# channels: which pile does this finding belong in?
# ---------------------------------------------------------------------------
# The channel split exists to fight alert fatigue. If soft signals and good news
# went into the same list as children in real distress, the list stops being
# read, and then nothing in this project matters. So:
#
#   alert    the queue a teacher is expected to read today
#   watch    real context, but never worth interrupting anyone for
#   positive good news, kept deliberately separate so it cannot crowd the queue
#
# The tuple order is also the *reading* order used by the dashboard and the
# digests, so a teacher always meets the same three sections in the same order.
CHANNELS = ("alert", "watch", "positive")

ALERT, WATCH, POSITIVE = CHANNELS


# ---------------------------------------------------------------------------
# priorities: how urgent, within a channel?
# ---------------------------------------------------------------------------
#: Priority -> sort rank. **Lower means more urgent**, which is the convention
#: throughout this project because Python sorts ascending by default, so
#: `sorted(students, key=priority_rank)` puts the most urgent first with no
#: `reverse=True` to remember.
PRIORITY_RANK = {
    "high": 0,    # several separate episodes, or one long, deep one
    "medium": 1,  # one alert-channel episode
    "watch": 2,   # only soft signals
    "info": 3,    # flagged, but nothing in the alert or watch channels
}

#: Rank given to a student who is not flagged at all. One better than every real
#: priority, so "arrived", "got worse", "got better" and "left the list
#: entirely" are all a single numeric comparison instead of four special cases.
#: Derived from the table above rather than hard-coded, so adding a priority
#: cannot leave this stale.
UNFLAGGED_RANK = max(PRIORITY_RANK.values()) + 1

#: The reverse lookup: rank -> the word for it. Used by the change feed to say
#: "medium -> not flagged" in a teacher-readable way.
RANK_LABEL = {rank: name for name, rank in PRIORITY_RANK.items()}
RANK_LABEL[UNFLAGGED_RANK] = "not flagged"


def priority_rank(priority):
    """Sort rank for a priority word; unknown words sort last, never crash.

    The tolerant `.get` matters: this is read from files written by an earlier
    run of the pipeline, and a display tool should not blow up because a record
    was written by a version that knew a priority word this one does not.
    """
    return PRIORITY_RANK.get(priority, UNFLAGGED_RANK)


def rank_of_record(record):
    """Rank of a flagged-student record, or :data:`UNFLAGGED_RANK` for ``None``.

    ``None`` is a real, expected input here: the change feed asks for the rank
    of a student who may not have existed in one of the two runs.
    """
    return priority_rank(record["priority"]) if record else UNFLAGGED_RANK


def student_sort_key(priority, alert_streak_count, student_id):
    """The project-wide ordering for a list of flagged students.

    Most urgent first, then whoever has the most separate alert episodes, then
    by id purely so the order is *stable* - two runs over the same data produce
    the same list, which is what lets a teacher scan the same page twice and
    trust that nothing moved under them.

    Returned as a tuple because Python compares tuples element by element: it
    tries the first, and only looks at the second if the first ties.
    """
    return (priority_rank(priority), -alert_streak_count, student_id)


def classroom_sort_key(high_priority_count, alert_student_count, classroom_code):
    """The project-wide ordering for a list of classrooms.

    Same shape as :func:`student_sort_key`. The negations put the *biggest*
    counts first while still sorting ascending overall.
    """
    return (-high_priority_count, -alert_student_count, classroom_code)


# ---------------------------------------------------------------------------
# names on screen
# ---------------------------------------------------------------------------

#: The name modes, in the vocabulary the APP writes. The app's privacy picker
#: (``NameModePicker`` in index.html) offers exactly two: ``full`` ("First
#: name") and ``id`` ("ID only - no name shown, most private"). ``anon`` and
#: ``initials`` are accepted too because the browser engine
#: (``fb-insights.js``) accepts them, and the two engines must agree; the app
#: itself never writes either. Anything else - including ``initial``, which
#: only the synthetic corpus contains - falls back to a first name, which is
#: what the app's own screens show for it.
ID_ONLY_MODES = frozenset({"id", "anon"})


def display_name(student_name, name_mode, student_id):
    """What to call this student on screen, honouring the classroom's setting.

    A classroom sets ``name_mode`` for a reason - most often that the dashboard
    gets shown on a projector where a visitor could read it. Code that spells
    the name out anyway quietly undoes a privacy decision a teacher made
    deliberately, so every screen and every file in this project routes names
    through here.

    This function mirrors the app's ``dispName`` and the browser engine's
    ``displayName`` exactly, and it has to: a teacher who chooses "ID only"
    sees "Student B64D" in the app, and a digest generated from the same data
    must not print "Yusuf". Before this was aligned, it did - this function
    only recognised ``anon``, so all 391 flagged students in the corpus's 32
    "ID only" rooms were named in recommendations, the dashboard file and 23
    email-ready digests. ``tests/test_recommend.py`` now reads the app's
    picker to keep the two from drifting again.

    Modes:
        ``"id"`` or ``"anon"``
            "Student 7497", from the tail of the student id - stable between
            runs, so a teacher can follow one student across a week without a
            name ever appearing. Uppercased, as the app does.
        ``"initials"``
            "Sana Malik" -> "SM".
        anything else, including ``"full"``
            the first name only, at most 20 characters - the app asks for
            "first name only", and this keeps that true even if more was typed.

    >>> display_name("Sana Malik", "full", "stu_e0c2762d7497")
    'Sana'
    >>> display_name("Sana Malik", "id", "stu_e0c2762d7497")
    'Student 7497'
    >>> display_name("Yusuf", "id", "stu_9f2ab64d")
    'Student B64D'
    """
    text = (student_name or "").strip()
    if name_mode in ID_ONLY_MODES:
        return f"Student {str(student_id or '')[-4:].upper()}"
    if name_mode == "initials":
        # `or "S"` covers a name that is only whitespace, which would otherwise
        # produce an empty label.
        return "".join(part[0].upper() for part in text.split()) or "S"
    words = text.split()
    return (words[0][:20] if words else "") or "Student"
