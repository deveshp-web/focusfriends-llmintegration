"""Turning raw logged check-ins into a clean, ordered, fast-to-scan timeline.

WHY THIS FILE EXISTS
--------------------
Every detection rule in this project is a question about *a run of check-ins in
time order*: "four bad ones in a row", "mostly bad over a week", "six bad ones
in one day". None of those questions can be asked until three messy problems
are solved:

  1. **Two timestamp formats.** The generated corpus writes
     ``{"ts": "2026-07-14T09:12:00"}``; exports from the Focus Bridge app write
     ``{"iso": "2026-07-14", "time": "09:12 AM"}``. Rules should never have to
     know that.
  2. **Order.** "Consecutive" is meaningless until the rows are sorted, and
     rows do not arrive sorted.
  3. **Speed.** The rules ask "how many bad check-ins between position 40 and
     position 61?" thousands of times per student. Counting them by hand each
     time is what made the old detector slow; :class:`Timeline` answers it in
     constant time. See the class docstring for the arithmetic.

WHAT LIVES HERE AND WHAT DOES NOT
---------------------------------
This module knows *nothing* about alerts, streaks or thresholds - it is the
layer underneath all of that. It answers only mechanical questions: when did
this happen, was it a bad one, how many days does this stretch touch. The rules
that turn those answers into a flag live in ``detect/rules.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import NamedTuple, Optional

from .vocabulary import NEGATIVE_EMOTIONS, normalize_emotion

# Some systems (and Python's own `%p` on several platforms) put a NARROW
# NO-BREAK SPACE, not an ordinary space, in front of "AM"/"PM". It looks
# identical on screen and is invisible in a diff, so a timestamp such as
# "9:41 PM" silently failed to parse before this was handled.
NARROW_NO_BREAK_SPACE = " "


class CheckIn(NamedTuple):
    """One moment a student said how they felt.

    A ``NamedTuple`` rather than a plain dict for three reasons:

      * **Memory.** A full run holds millions of these. A two-field tuple costs
        roughly a third of what the equivalent dict costs, and this pipeline's
        peak memory is dominated by exactly this object.
      * **Typos become errors.** ``entry["emtoion"]`` on a dict returns a
        ``KeyError`` at runtime, deep inside a rule; ``entry.emtoion`` is an
        ``AttributeError`` immediately and is caught by any linter.
      * **Immutability.** No rule can accidentally rewrite the log it is
        scanning, so rules can be reasoned about independently.

    Attributes:
        at:      when the check-in happened.
        emotion: the canonical emotion, or ``None`` when the logged word could
                 not be recognised. ``None`` is meaningful - see
                 :func:`read_check_ins`.
    """

    at: datetime
    emotion: Optional[str]

    @property
    def day(self):
        """The calendar date, for rules that count "how many separate days"."""
        return self.at.date()


# ---------------------------------------------------------------------------
# parsing: the messy outside world -> a datetime
# ---------------------------------------------------------------------------

def parse_clock(raw):
    """``'09:41'``, ``'09:41 AM'`` or ``'9:41:00 PM'`` -> ``(hour, minute)``.

    Returns ``None`` if the text cannot be read, so the caller can decide what
    to do rather than being handed a wrong-but-plausible time.

    >>> parse_clock("9:12:00 PM")
    (21, 12)
    >>> parse_clock("12:30 AM")
    (0, 30)
    >>> parse_clock("nonsense") is None
    True
    """
    text = raw.strip().upper().replace(NARROW_NO_BREAK_SPACE, " ")

    # Pull off an AM/PM marker if there is one, remembering which it was.
    meridiem = None
    for marker in ("AM", "PM"):
        if text.endswith(marker):
            meridiem = marker
            text = text[: -len(marker)].strip()
            break

    parts = text.split(":")
    if len(parts) < 2:
        return None  # no minutes component: not a clock time
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None  # e.g. "ab:cd"

    # 12-hour to 24-hour. The two special cases are the ones people get wrong:
    # 12 PM is noon (stays 12), and 12 AM is midnight (becomes 0).
    if meridiem == "PM" and hour != 12:
        hour += 12
    elif meridiem == "AM" and hour == 12:
        hour = 0

    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None  # e.g. "25:99"
    return hour, minute


def parse_timestamp(entry):
    """When a logged row happened, in either supported schema. ``None`` if unreadable.

    Tries, in order:

      1. ``{"ts": "2026-07-14T09:12:00"}`` - the generated corpus. If a ``ts``
         field is present but unparseable the answer is ``None``; we do *not*
         fall through to the other fields, because a row that has a ``ts`` and a
         broken one is corrupt, and guessing from a second field would invent
         data.
      2. ``{"iso": "2026-07-14", "time": "09:12 AM"}`` - app exports. A missing
         or unreadable ``time`` degrades to midnight on that date rather than
         throwing the row away: the day is still usable evidence.
    """
    raw = entry.get("ts")
    if isinstance(raw, str) and raw:
        try:
            # "Z" is valid ISO-8601 for UTC but `fromisoformat` only learned it
            # in Python 3.11, so it is translated for older interpreters. The
            # timezone is then dropped: everything in this project is local
            # classroom time, and mixing aware and naive datetimes raises.
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

    day_text = entry.get("iso") or entry.get("date")
    if not isinstance(day_text, str) or not day_text:
        return None
    try:
        # `[:10]` keeps just "YYYY-MM-DD" if a full timestamp was put in the
        # date field, which some exports do.
        midnight = datetime.fromisoformat(day_text[:10])
    except ValueError:
        return None

    clock_text = entry.get("time")
    if isinstance(clock_text, str) and clock_text:
        clock = parse_clock(clock_text)
        if clock:
            return midnight.replace(hour=clock[0], minute=clock[1])
    return midnight


def read_check_ins(student, problems):
    """Clean, sorted :class:`CheckIn` list for one student.

    Two dropping rules, and the difference between them is the single most
    important thing in this file:

      * **An unreadable timestamp means the row is dropped.** It cannot be
        placed in the sequence at all, so there is nothing sensible to do
        with it.
      * **An unreadable *emotion* means the row is kept, with ``emotion=None``.**
        This looks like a bug and is not. If such a row were dropped, the
        check-ins on either side of it would become adjacent, and "four bad
        ones in a row" could be manufactured out of a gap we could not read.
        Keeping it as ``None`` makes it break the run, which is the honest
        answer: we do not know what happened here, so we will not claim a
        streak ran through it.

    Args:
        student: one parsed row from ``students.jsonl``.
        problems: a ``Counter`` the caller owns, tallying why rows were dropped.
            Passed in rather than returned so one counter can accumulate across
            every student in a run and be reported once at the end.

    Complexity: O(k log k) for k rows, dominated by the sort.
    """
    check_ins = []
    for row in student.get("emotion_log") or []:
        if not isinstance(row, dict):
            problems["not_an_object"] += 1
            continue

        at = parse_timestamp(row)
        if at is None:
            problems["bad_timestamp"] += 1
            continue

        # Two field names for the same thing, again because two systems wrote
        # this data. `or` picks whichever is present.
        emotion = normalize_emotion(row.get("emotion") or row.get("label"))
        if emotion is None:
            problems["unknown_emotion"] += 1

        check_ins.append(CheckIn(at, emotion))

    # Sorting by time is what makes "consecutive" mean anything. The emotion
    # name is a tie-breaker so that two exports of the same check-ins in
    # different row orders produce byte-identical output - without it, results
    # would depend on the order rows happened to be written to disk.
    check_ins.sort(key=lambda check_in: (check_in.at, check_in.emotion or ""))
    return check_ins


# ---------------------------------------------------------------------------
# the fast window index
# ---------------------------------------------------------------------------

class Timeline:
    """A sorted run of check-ins, with O(1) answers to the questions rules ask.

    THE PROBLEM THIS SOLVES
    -----------------------
    The detection rules slide a window along a student's log and, at every
    position, ask two questions:

        "how many of the check-ins from position i to position j were bad?"
        "how many separate calendar days does i..j touch?"

    Answering either by looping over the window costs O(w) for a window of
    width w. Doing that at every one of n positions costs **O(n x w)**. On a
    student with 200 check-ins and a 7-day window that is tens of thousands of
    redundant comparisons, repeated for 4,437 students.

    THE FIX: PREFIX SUMS
    --------------------
    A prefix sum is a running total computed once, up front, so that the total
    for any range is a single subtraction.

    Say the check-ins are ``bad, good, bad, bad`` (1 = bad)::

        values        1     0     1     1
        prefix   0    1     1     2     3
                 ^ index 0 of the prefix array is always 0, meaning
                   "the total before the first element"

    The number of bad check-ins in positions 1..3 is then
    ``prefix[4] - prefix[1]`` = ``3 - 1`` = 2. One subtraction, no loop, however
    wide the range.

    Distinct days uses the same trick on a different signal. Because the log is
    sorted, the dates never go backwards, so::

        distinct days in i..j  ==  1 + (how many times the date changed inside i..j)

    and "the date changed here" is a 0/1 value per position that can be
    prefix-summed exactly like the bad-check-in flag.

    Result: building the index is one O(n) pass, and every range query
    afterwards is O(1). The rules go from **O(n x w)** to **O(n)**.
    """

    __slots__ = ("check_ins", "times", "_negative_prefix", "_day_change_prefix")

    def __init__(self, check_ins):
        #: The check-ins themselves, in time order.
        self.check_ins = list(check_ins)

        #: Just the timestamps, so the sliding-window scan can compare times
        #: without dereferencing a whole object each step.
        self.times = [check_in.at for check_in in self.check_ins]

        count = len(self.check_ins)

        # prefix[i] = number of bad check-ins strictly before position i.
        # Length is count + 1 so that prefix[count] is the grand total and no
        # range query ever needs a bounds check.
        negative_prefix = [0] * (count + 1)

        # prefix[i] = number of date changes strictly before position i.
        day_change_prefix = [0] * (count + 1)

        previous_day = None
        for index, check_in in enumerate(self.check_ins):
            is_bad = 1 if check_in.emotion in NEGATIVE_EMOTIONS else 0
            negative_prefix[index + 1] = negative_prefix[index] + is_bad

            today = check_in.at.date()
            changed = 1 if previous_day is not None and today != previous_day else 0
            day_change_prefix[index + 1] = day_change_prefix[index] + changed
            previous_day = today

        self._negative_prefix = negative_prefix
        self._day_change_prefix = day_change_prefix

    # -- the queries the rules make -----------------------------------------

    def __len__(self):
        return len(self.check_ins)

    def __getitem__(self, index):
        """Indexing and slicing pass straight through to the check-in list."""
        return self.check_ins[index]

    def negative_count(self, start, end):
        """How many bad check-ins in positions ``start..end`` (both inclusive). O(1)."""
        return self._negative_prefix[end + 1] - self._negative_prefix[start]

    def negative_share(self, start, end):
        """The fraction of ``start..end`` that were bad check-ins. O(1).

        Returns 0.0 for an empty range rather than dividing by zero, so callers
        do not each need their own guard.
        """
        width = end - start + 1
        if width <= 0:
            return 0.0
        return self.negative_count(start, end) / width

    def distinct_days(self, start, end):
        """How many separate calendar days ``start..end`` touches. O(1).

        Relies on the log being sorted, which :func:`read_check_ins`
        guarantees: dates never go backwards, so a date change is always the
        start of a new day rather than a return to an old one.
        """
        if end < start:
            return 0
        changes = self._day_change_prefix[end + 1] - self._day_change_prefix[start + 1]
        return 1 + changes

    def span(self, start, end):
        """Elapsed time between the first and last check-in of a range."""
        return self.times[end] - self.times[start]

    def span_days(self, start, end):
        """Whole days between the first and last *dates* of a range.

        Deliberately date-to-date rather than duration-based: a check-in at
        08:00 Monday and one at 16:00 Tuesday span "1 day", not "1.3 days",
        because that is how a teacher counts.
        """
        return (self.times[end].date() - self.times[start].date()).days

    def slice(self, start, end):
        """The check-ins in ``start..end`` as a plain list (both inclusive)."""
        return self.check_ins[start:end + 1]

    def maximal_windows(self, max_span, lo=0, hi=None):
        """Yield ``(start, end)`` index pairs for every time-bounded window.

        For each starting position, ``end`` is pushed as far right as it can go
        while keeping the window inside ``max_span``. Every rule that asks "what
        happened within N days of here" is built on this, which is why it lives
        here once instead of being re-written inside each rule - it appeared
        three times in the old code, in three subtly different spellings.

        This is the classic **two-pointer** (or "sliding window") technique. The
        key insight that makes it fast: as ``start`` moves right, the furthest
        valid ``end`` can only ever move right as well - never back. So ``end``
        is never rewound, and across the whole scan each pointer advances at
        most n times, giving **O(n) total** rather than the O(n x w) of
        recomputing each window from scratch.

        Args:
            max_span: a ``timedelta``; the widest a window may be.
            lo: first index to consider (default: the start of the timeline).
            hi: last index to consider, inclusive (default: the end). Scanning a
                sub-range matters because several rules work on one *run* of
                check-ins carved out of the log, and slicing a copy of that run
                out just to scan it would undo the O(1) range queries above -
                a slice has its own indices and no prefix sums.

        Yields:
            ``(start, end)`` with both indices inclusive. One pair per starting
            position in ``lo..hi``.
        """
        if hi is None:
            hi = len(self.times) - 1
        end = lo
        for start in range(lo, hi + 1):
            # `start` may have overtaken `end` if the previous window was a
            # single entry; keep the window non-empty.
            if end < start:
                end = start
            while end + 1 <= hi and self.times[end + 1] - self.times[start] <= max_span:
                end += 1
            yield start, end


def days(count):
    """``days(7)`` reads better than ``timedelta(days=7)`` at every call site."""
    return timedelta(days=count)
