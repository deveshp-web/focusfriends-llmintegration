"""Every threshold the recommender uses, and the reasoning that put it there.

The comments in this file are longer than the code, and that is the right ratio.
Each of these numbers is the residue of an experiment that failed a different
way, and a future reader who does not know which experiments were already run
will re-run them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Mapping


@dataclass(frozen=True)
class RecommendationSettings:
    """Thresholds for turning a flag into advice."""

    # -- attributing a check-in to an activity --------------------------------

    task_window_minutes: int = 15
    """How close a check-in must be to a ``task_log`` row to be attributed to it.

    Measured on the data rather than guessed: the gap between a check-in and its
    nearest task is 5 minutes at the median and 10 at the 95th percentile, so 15
    captures essentially all of them without reaching across into the next
    activity.
    """

    min_task_hits: int = 3
    """How many of a student's flagged check-ins an activity must hold before it
    is named at all. Two is a coincidence in a log this dense."""

    min_task_exposure: int = 5
    """How many check-ins must have happened near an activity before a *rate*
    computed over them means anything. Three out of three is 100% and is also
    nothing."""

    min_task_excess: float = 0.15
    """How far above the student's own baseline the activity's rate must sit.

    WHAT IS MEASURED HERE MATTERS MORE THAN THE THRESHOLD
    -----------------------------------------------------
    Two earlier versions got this wrong in opposite directions, and both are
    worth knowing about because both looked reasonable at the time.

    **Attempt 1 - share, or lift, whichever passes.** ``share >= 0.30 OR
    lift >= 1.4`` let an activity through on frequency alone. A student whose
    flags sat next to Reading exactly as often as *everything else* sat next to
    Reading was told "Reading is where this shows up" at 0.99x their own
    baseline. 23% of clusters were below the intended bar, and 29 pointed at an
    activity that was, if anything, going *better* than average.

    **Attempt 2 - compare distributions.** Comparing the distribution of flags
    across activities against the distribution of all check-ins looked more
    rigorous and was much worse. When a student is negative 66% of the time, the
    flagged set *is* most of the population, so the two distributions are forced
    to agree and no activity can ever stand out. It found clusters for 24 of
    2,842 students - not restraint, a broken instrument.

    **What works - a rate, not a share.** Of the check-ins around this activity,
    how many went badly, against how often this student's check-ins go badly at
    all. That question is well posed whether a student is flagged twice or forty
    times, and it is the question a teacher is actually asking.

    WHAT THIS FINDS, STATED PLAINLY
    -------------------------------
    52 students out of 2,842 on the synthetic corpus. That is *not* the gates
    being too tight. Across 3,839 (student, activity) pairs the excess rate is
    distributed almost symmetrically about zero - 5th percentile -0.138, 95th
    +0.181 - which is what sampling noise looks like and what real
    activity-linked distress does not. The generator built archetypes that carry
    emotion *streaks*, not activities that trigger them, so there is close to
    nothing here to find and the rule correctly declines to invent it. It exists
    for live app data, where a child who comes apart at every Gym transition is
    an ordinary case.

    ONE CAVEAT THAT SURVIVES INTO LIVE DATA
    ---------------------------------------
    This is a per-student test run over every activity, so at alpha 0.05 a
    room's worth of students generates false positives by chance. The
    effect-size and exposure floors hold the rate well under that, and the
    recommendation is worded as *a lead to check against a teacher's own
    knowledge* rather than as a finding.
    """

    task_max_p: float = 0.05
    """Statistical significance floor for the activity test."""

    min_skipped: int = 2
    """Skipped tasks in the flagged period before avoidance is worth mentioning."""

    # -- time of day ----------------------------------------------------------

    time_min_hits: int = 4
    """Flagged check-ins needed before a time-of-day pattern is even considered."""

    time_max_p: float = 0.05
    time_min_excess: float = 0.15
    """Why the time-of-day rule is a binomial test and not a share or a lift.

    **Share is worthless here, and measuring it was a real bug.** 83% of every
    check-in in this dataset is logged before 11am, so "most of the flags are in
    the morning" is the timetable talking, not the student.

    **A fixed lift threshold is worse, and measurably so.** When a base rate is
    extreme, lift has a ceiling: at an 83% morning baseline the largest lift a
    student can possibly reach is 1/0.83 = 1.21. So a ``lift >= 1.5`` gate makes
    "mornings are hard for this child" *arithmetically unreportable*, however
    total the concentration. The gate silently encoded "the answer is never
    morning".

    **So the test is binomial, against the student's own baseline**: how likely
    is this many flagged check-ins landing in this band, if flags fell in the
    same proportions the student's check-ins generally do? It self-adjusts to
    any base rate, which also matters because live app data will not share this
    dataset's timetable. Measured on 3,352 student-bucket observations:

        fixed lift >= 1.5   88 fire, 10 survive p<0.05 - 89% small-sample noise,
                            and 0 of them can be morning
        binomial            15 fire, 5 of them morning - the bias is gone, and
                            the false positives go with it

    ``time_min_excess`` is the effect-size floor that stops a
    statistically-significant-but-trivial concentration from firing.
    """

    # -- communication --------------------------------------------------------

    aac_low_ratio: float = 0.35
    """AAC use below this share of emotion check-ins, for a student whose flags
    are confusion- or overwhelm-shaped, reads as a communication gap rather than
    a mood. (AAC = augmentative and alternative communication: the symbol board
    or device a non-speaking student uses to say something.)"""

    # -- how much a teacher has to read --------------------------------------

    max_recommendations: int = 4
    watch_recommendations: int = 2
    positive_recommendations: int = 1
    """Per-channel caps. A teacher with 23 flagged students cannot read 23 x 8
    recommendations - the same alert-fatigue argument the channel split itself
    is built on, applied one layer further down."""

    # -- room-wide patterns ---------------------------------------------------

    room_min_flagged: int = 3
    room_task_min_students: int = 3
    room_task_min_share: float = 0.25
    room_time_min_share: float = 0.50
    """How much of a room's flagged roster an activity (or a time of day) must
    touch before it is called a room problem rather than several student ones.

    Both a count *and* a share: a count alone would fire on 3 of 40 students,
    and a share alone would fire on 1 of 2."""

    @property
    def task_window(self) -> timedelta:
        return timedelta(minutes=self.task_window_minutes)

    def cap_for_channel(self, channel: str) -> int:
        """How many recommendations a student in this channel may receive."""
        return {
            "alert": self.max_recommendations,
            "watch": self.watch_recommendations,
            "positive": self.positive_recommendations,
        }[channel]

    def as_metadata(self) -> Mapping:
        """The subset recorded in ``recommendations_meta.json``."""
        return {
            "task_window_minutes": self.task_window_minutes,
            "min_task_hits": self.min_task_hits,
            "min_task_excess": self.min_task_excess,
            "task_max_p": self.task_max_p,
            "min_task_exposure": self.min_task_exposure,
            "time_max_p": self.time_max_p,
            "time_min_excess": self.time_min_excess,
            "aac_low_ratio": self.aac_low_ratio,
            "caps": {"alert": self.max_recommendations,
                     "watch": self.watch_recommendations,
                     "positive": self.positive_recommendations},
            "room_task_min_students": self.room_task_min_students,
            "room_task_min_share": self.room_task_min_share,
        }

    def replace(self, **changes) -> "RecommendationSettings":
        current = asdict(self)
        current.update(changes)
        return RecommendationSettings(**current)


#: The configuration used unless a caller supplies its own.
DEFAULT_SETTINGS = RecommendationSettings()


# ---------------------------------------------------------------------------
# time-of-day bands
# ---------------------------------------------------------------------------
#: ``(name, first hour, first hour after, how to say it in a sentence)``.
#:
#: "midday" is deliberately narrow: it is the lunch and recess band, and a spike
#: there means something quite different from a spike inside a work block.
#: Boundaries are half-open - a check-in at exactly 11:00 is midday, not morning
#: - which is the convention that guarantees every hour lands in exactly one
#: band with no gaps and no overlaps.
TIME_BUCKETS = (
    ("morning", 0, 11, "before 11am"),
    ("midday", 11, 13, "the 11am-1pm block"),
    ("afternoon", 13, 24, "after 1pm"),
)

#: bucket name -> the phrase for it. Built once here so the rules can look a
#: phrase up in O(1) instead of scanning the tuple above at every call site,
#: which is what the old code did in three separate places.
TIME_BUCKET_PHRASES = {name: phrase for name, _, _, phrase in TIME_BUCKETS}


def bucket_for_hour(hour):
    """Which time band an hour falls in. ``None`` if somehow outside all of them."""
    for name, first, limit, _ in TIME_BUCKETS:
        if first <= hour < limit:
            return name
    return None
