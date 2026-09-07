"""The statistics behind the correlation rules, as pure functions.

WHY THE MATHS IS ITS OWN FILE
-----------------------------
The two correlation rules - "which activity does this show up around?" and
"which part of the day?" - ask the *same* statistical question about different
groupings. Written inline they were two near-copies of the same twenty lines,
which is how two supposedly identical tests end up disagreeing.

Pulling the maths out means the question is stated once, tested once, and both
rules provably ask it the same way.

THE QUESTION, IN PLAIN ENGLISH
------------------------------
    "This student's check-ins go badly 30% of the time overall. Around Reading,
     8 of their 12 check-ins went badly - that is 67%. Is that a real pattern,
     or is it what you would expect to see sometimes by chance?"

Three separate gates have to be passed, and each one rejects a different kind of
false alarm:

    1. EXPOSURE   Were there enough check-ins near Reading for a percentage to
                  mean anything at all? (3 out of 3 is 100% and is also nothing.)
    2. EFFECT     Is the gap big enough to act on? A rate 2 points above
                  baseline may be perfectly real and still not worth a
                  teacher's Monday.
    3. CHANCE     Could this gap have come up by luck? That is the binomial test
                  below.

A pattern must pass all three. Any one of them alone produces advice that
wastes a teacher's time in a different way.
"""

from __future__ import annotations

import math
from typing import NamedTuple


class RateComparison(NamedTuple):
    """One group's badness rate, measured against the student's own baseline.

    Attributes:
        hits:       flagged check-ins inside this group (e.g. around Reading).
        exposure:   all check-ins inside this group, flagged or not.
        rate:       ``hits / exposure`` - how often it goes badly *here*.
        usual_rate: the student's overall rate - how often it goes badly at all.
        excess:     ``rate - usual_rate``, in plain proportion points.
        lift:       ``rate / usual_rate`` - "this many times more likely here".
        p_value:    the chance of seeing at least this many hits if this group
                    were no different from the student's average.
    """

    hits: int
    exposure: int
    rate: float
    usual_rate: float
    excess: float
    lift: float
    p_value: float


def binomial_tail(k, n, p):
    """``P(X >= k)`` for ``X ~ Binomial(n, p)``, computed exactly.

    In this project's terms: if every check-in independently had a ``p`` chance
    of going badly, what is the chance that **at least ``k``** of ``n`` did? A
    small answer means "this many is surprising", which is what makes the
    pattern worth showing to a teacher.

    Exact rather than approximated, deliberately. ``n`` here is one student's
    check-ins near one activity - typically 5 to 40 - which is precisely the
    range where the usual normal approximation is worst, and small enough that
    summing the exact terms costs nothing.

    Complexity: O(n) terms, each an O(1) ``math.comb``.

    >>> round(binomial_tail(10, 10, 0.5), 4)   # ten out of ten fair coin flips
    0.001
    >>> binomial_tail(0, 10, 0.5)              # "at least zero" is certain
    1.0
    """
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def evaluate_group(hits, exposure, baseline,
                   min_hits, min_exposure, min_excess, max_p):
    """Measure one group against the baseline, or return ``None`` if it does not qualify.

    Both correlation rules call exactly this, which is what guarantees they ask
    the same question the same way.

    The three gates are applied **cheapest first**, and that ordering is
    load-bearing rather than a micro-optimisation: the two integer comparisons
    and one subtraction reject the great majority of groups, so the O(n)
    binomial sum only ever runs for the handful that are still candidates. This
    runs for every activity of every flagged student, so the difference is the
    difference between a rule that is free and one that dominates the stage.

    Args:
        hits:        flagged check-ins in this group.
        exposure:    all check-ins in this group.
        baseline:    the student's own overall rate, from :func:`usable_baseline`.
        min_hits:    gate 1a - enough flagged check-ins to be a pattern.
        min_exposure: gate 1b - enough check-ins for a rate to mean anything.
        min_excess:  gate 2 - the effect must be big enough to act on.
        max_p:       gate 3 - and unlikely enough to be chance.

    Returns:
        A :class:`RateComparison`, or ``None`` if any gate rejected the group.
    """
    # Gate 1: exposure. Cheap, and rejects most groups.
    if hits < min_hits or exposure < min_exposure:
        return None

    # Gate 2: effect size. Still cheap.
    rate = hits / exposure
    excess = rate - baseline
    if excess < min_excess:
        return None

    # Gate 3: chance. The only expensive test, so it runs last.
    p_value = binomial_tail(hits, exposure, baseline)
    if p_value >= max_p:
        return None

    return RateComparison(
        hits=hits,
        exposure=exposure,
        rate=rate,
        usual_rate=baseline,
        excess=excess,
        lift=rate / baseline,
        p_value=p_value,
    )


def usable_baseline(hits_total, exposure_total):
    """The student's own overall badness rate, or ``None`` if it cannot be used.

    A baseline of exactly 0 or exactly 1 breaks the comparison, and not just
    numerically - it is *meaningless*. If a student's check-ins never go badly
    there is nothing to be above; if they always do, there is nothing to stand
    out from. Both cases return ``None`` so the caller reports no pattern rather
    than dividing by zero or claiming an infinite lift.
    """
    if not exposure_total or not hits_total:
        return None
    baseline = hits_total / exposure_total
    return baseline if 0 < baseline < 1 else None
