"""Every number the detector uses, with the argument that put it there.

WHY A DATACLASS AND NOT LOOSE CONSTANTS
---------------------------------------
These used to be bare module-level globals. That works right up until you want
to ask "what would this run look like with a streak length of 5?" - at which
point the only way to find out is to edit the file, run it, and edit it back.

Bundling them into one frozen dataclass gets three things:

  * **Testability.** A test can pass its own settings object instead of
    monkey-patching a global and hoping to restore it afterwards.
  * **Honest reporting.** The whole object is written into
    ``insights_meta.json``, so any output file can be traced back to the exact
    configuration that produced it.
  * **Safety.** ``frozen=True`` means nothing can quietly reassign a threshold
    half way through a run and make the first thousand students incomparable
    with the last thousand.

Call sites stay short because every rule defaults to :data:`DEFAULT_SETTINGS`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import FrozenSet, Mapping

from ..core.vocabulary import NEGATIVE_EMOTIONS


# ---------------------------------------------------------------------------
# where each rule's findings go
# ---------------------------------------------------------------------------
#: rule name -> channel. ``same_day_cluster`` is listed as ``watch`` because
#: that is where an ordinary cluster goes; a big enough one is promoted to
#: ``alert`` by the rule itself (see ``same_day_alert_length``).
TRIGGER_CHANNELS = {
    "negative": "alert",
    "same_day_cluster": "watch",
    "density": "watch",
    "repeated": "positive",
}


@dataclass(frozen=True)
class DetectionSettings:
    """The thresholds that decide what counts as a pattern.

    Read this class top to bottom and you know everything the detector believes.
    """

    # -- the negative streak rule (the core alert) ---------------------------

    streak_length: int = 4
    """How many bad check-ins in a row before it is a streak.

    Four is the number a teacher recognises as "this is not just a bad morning".
    Three fires on ordinary rough days; five misses episodes that are already
    worth a conversation.
    """

    streak_window_days: int = 7
    """A streak's first and last check-in must be within this many days.

    Without it, four bad check-ins spread over a month would read as a streak.
    This constraint decides *whether* a run qualifies - importantly, it does
    **not** chop the reported run into week-sized pieces. See
    :func:`~focusbridge.detect.rules.find_negative_streaks` for why that
    distinction was expensive to get wrong.
    """

    min_distinct_days: int = 2
    """A streak must touch at least this many separate calendar days.

    This is what stops "a single rough morning" being reported as "a rough
    week". Four bad check-ins all before lunch is a real event, but it is a
    different event - it gets caught by the same-day cluster rule instead.
    """

    recency_days: int = 30
    """Only check-ins this recent are considered at all.

    A month is roughly what a teacher can still remember the context of, and it
    matches the review cycle most of these classrooms work to.
    """

    # -- the repeated (good news) rule ---------------------------------------

    repeated_valences: FrozenSet[str] = frozenset({"positive"})
    """Which valences the "same emotion N times running" rule may fire on.

    Neutral is deliberately excluded. Four "okay" check-ins in a row is the
    definition of an unremarkable week, and including it put 147 non-events into
    the same file as children in distress.
    """

    # -- the density (soft signal) rule --------------------------------------

    enable_density_trigger: bool = True
    density_window_days: int = 7
    density_min_entries: int = 6
    density_min_negative_share: float = 0.7
    """A week that is mostly bad but never four in a row, because a single
    neutral check-in keeps breaking the run.

    This is the watch channel's reason for existing: the streak rule is precise
    but brittle, and a child having a genuinely bad fortnight should not escape
    notice because they had one "okay" on the Wednesday.
    """

    # -- the same-day cluster rule -------------------------------------------

    enable_same_day_cluster: bool = True
    same_day_cluster_length: int = 4
    same_day_alert_length: int = 6
    """Bad check-ins packed into a single school day.

    ``min_distinct_days`` deliberately keeps an ordinary cluster out of the
    alert channel - one rough morning is not a rough week. But a *big* enough
    cluster is its own kind of event rather than a weaker version of a streak,
    so at ``same_day_alert_length`` it is promoted to alert.
    """

    # -- turning findings into a priority -------------------------------------

    high_priority_min_length: int = 6
    high_priority_min_days: int = 3
    """When a *single* alert episode is enough to make a student high priority.

    Six or more bad check-ins in a row, spread over three or more days, means
    the episode survived several overnight resets - which is exactly what
    separates a rough patch from a bad day. (Two separate alert episodes also
    make a student high priority; that test needs no threshold.)

    These lived as bare numbers inside the summarising code before, where no
    reader could tell they were tunable at all.
    """

    # -- LLM-as-a-judge sampling ---------------------------------------------

    judge_cases_per_type: int = 12
    judge_seed: int = 42
    """How many borderline cases of each kind to sample for external review, and
    the random seed that makes that sample reproducible. A fixed seed means two
    people auditing the same corpus argue about the same twelve cases.
    """

    # -- derived values -------------------------------------------------------
    # Computed once here so no rule has to build a `timedelta` inside a loop.

    negative_emotions: FrozenSet[str] = field(default=NEGATIVE_EMOTIONS)

    @property
    def streak_window(self) -> timedelta:
        return timedelta(days=self.streak_window_days)

    @property
    def density_window(self) -> timedelta:
        return timedelta(days=self.density_window_days)

    @property
    def alert_triggers(self):
        """Rule names whose findings land in the alert channel."""
        return {name for name, channel in TRIGGER_CHANNELS.items()
                if channel == "alert"}

    @property
    def watch_triggers(self):
        return {name for name, channel in TRIGGER_CHANNELS.items()
                if channel == "watch"}

    def as_metadata(self) -> Mapping:
        """The subset of settings recorded in ``insights_meta.json``.

        Hand-listed rather than dumped wholesale so the metadata file stays a
        deliberate, stable contract for downstream readers: adding a private
        tuning knob here should not silently change a published output format.
        """
        return {
            "streak_length": self.streak_length,
            "recency_days": self.recency_days,
            "streak_window_days": self.streak_window_days,
            "min_distinct_days": self.min_distinct_days,
            "repeated_valences": sorted(self.repeated_valences),
            "density_trigger_enabled": self.enable_density_trigger,
            "same_day_cluster_enabled": self.enable_same_day_cluster,
            "same_day_alert_length": self.same_day_alert_length,
            "high_priority_min_length": self.high_priority_min_length,
            "high_priority_min_days": self.high_priority_min_days,
            "trigger_channels": dict(sorted(TRIGGER_CHANNELS.items())),
            "negative_emotions": sorted(self.negative_emotions),
        }

    def replace(self, **changes) -> "DetectionSettings":
        """A copy with some thresholds changed - the frozen-object way to edit.

        >>> DEFAULT_SETTINGS.replace(streak_length=5).streak_length
        5
        >>> DEFAULT_SETTINGS.streak_length   # the original is untouched
        4
        """
        current = asdict(self)
        current.update(changes)
        return DetectionSettings(**current)


#: The configuration every rule uses unless told otherwise.
DEFAULT_SETTINGS = DetectionSettings()
