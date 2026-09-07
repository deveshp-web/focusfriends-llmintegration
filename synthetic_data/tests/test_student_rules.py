"""Tests for the per-student recommendation rules.

These build a :class:`StudentContext` directly rather than going through the
pipeline, because a rule's job is "given everything known about this student,
do I fire?" - and that question needs no files.

The first test in :class:`AntecedentFatigueTests` is a **regression test**: the
rule it covers crashed the whole stage before the refactor, and never fired on
the synthetic corpus, which is exactly why nobody noticed. A test is the only
thing that keeps a bug like that from coming back the next time someone edits
the sentence.
"""

from __future__ import annotations

import unittest
from collections import Counter

from focusbridge.recommend.context import StoryIndex, StudentContext
from focusbridge.recommend.settings import DEFAULT_SETTINGS
from focusbridge.recommend.student_rules import (RULES, build_recommendations,
                                                 headline_reason, rank_and_cap,
                                                 rule_antecedent_fatigue,
                                                 rule_celebrate,
                                                 rule_monitor_watch)

R = DEFAULT_SETTINGS


def make_context(**overrides):
    """A minimal but complete context, with sensible empty defaults.

    Every rule reads several fields, so a builder with defaults keeps each test
    to the two or three fields it is actually about.
    """
    base = dict(
        student_id="s1",
        student_name="Sam",
        display_name="Sam",
        classroom_code="R1",
        summary={"recent_negative_share": 0.4, "streak_count": 1,
                 "alert_streak_count": 0, "watch_reasons": ["density"]},
        channel="alert",
        priority="medium",
        streaks=[],
        tasks=[],
        time=None,
        trajectory={"direction": "steady", "first_half": 0.4,
                    "second_half": 0.4, "delta": 0.0},
        emotion_mix=Counter(),
        dominant_emotion=None,
        notes={"named_trigger": [], "calm_worked": [], "needed_prompt": [],
               "flags": set(), "recent": [], "count": 1},
        calm_used=set(),
        stories=StoryIndex([]),
        aac_count=5,
        emo_count=10,
        tokens=None,
        token_goal=None,
        token_reward="the class reward",
    )
    base.update(overrides)
    return StudentContext(**base)


class AntecedentFatigueTests(unittest.TestCase):
    """Regression cover for the rule that used to take the stage down."""

    def _armed_context(self):
        """Both conditions satisfied: afternoon cluster + a post-lunch note."""
        return make_context(
            time={"bucket": "afternoon", "phrase": "after 1pm", "hits": 8,
                  "exposure": 8, "rate": 1.0, "usual_rate": 0.4,
                  "excess_points": 60, "lift": 2.5, "p_value": 0.0007},
            notes={"named_trigger": [], "calm_worked": [], "needed_prompt": [],
                   "flags": {"tired_after_lunch"}, "recent": [], "count": 1})

    def test_it_fires_without_raising(self):
        """The bug: this quoted a field ``correlate_time`` never produces.

        Before the refactor the f-string read ``ctx["time"]["share"]``, so the
        first student to satisfy both conditions raised ``KeyError`` and took
        down the whole recommendation stage. It had never fired on the synthetic
        corpus, so the crash sat there undetected.
        """
        found = rule_antecedent_fatigue(self._armed_context(), R)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], "antecedent_fatigue")

    def test_the_evidence_quotes_a_field_that_exists(self):
        """Guards the *specific* failure: every field used must be real.

        ``correlate_time`` is the sole producer of this dict, so its keys are
        the contract. If a future edit reaches for another name that is not
        there, this fails rather than shipping.
        """
        context = self._armed_context()
        found = rule_antecedent_fatigue(context, R)
        self.assertIn("100% of check-ins after 1pm were flagged", found["evidence"])
        # And the number quoted really is the rate, not some other statistic.
        self.assertEqual(f"{context.time['rate']:.0%}", "100%")

    def test_it_stays_quiet_without_the_note(self):
        context = self._armed_context()
        context.notes["flags"] = set()
        self.assertIsNone(rule_antecedent_fatigue(context, R))

    def test_it_stays_quiet_when_the_cluster_is_not_in_the_afternoon(self):
        context = self._armed_context()
        context.time["bucket"] = "morning"
        self.assertIsNone(rule_antecedent_fatigue(context, R))

    def test_it_stays_quiet_with_no_time_cluster_at_all(self):
        context = self._armed_context()
        context.time = None
        self.assertIsNone(rule_antecedent_fatigue(context, R))


class RuleContractTests(unittest.TestCase):
    """Properties every rule must hold, checked across the whole registry."""

    def test_every_rule_returns_none_for_an_empty_case(self):
        """A context with nothing in it must produce no advice and no crash.

        This is the cheapest possible guard against a new rule indexing into a
        field that can legitimately be empty.
        """
        context = make_context(channel="alert")
        for rule in RULES:
            with self.subTest(rule=rule.__name__):
                found = rule(context, R)
                if found is not None:
                    # Only the documentation rules may fire on an empty case.
                    self.assertIn(found["id"], {"document_note", "document_context"})

    def test_rule_ids_are_unique(self):
        """Two rules sharing an id would make the rank tie-break unstable."""
        context = make_context()
        ids = [rule(context, R)["id"] for rule in RULES if rule(context, R)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_recommendation_carries_evidence(self):
        """The project's central promise: a teacher can audit any line of advice."""
        context = make_context(
            channel="positive",
            streaks=[{"type": "repeated", "channel": "positive", "emotion": "happy",
                      "length": 5, "distinct_days": 3, "start_ts": "2026-08-01T09:00:00",
                      "end_ts": "2026-08-03T09:00:00"}],
            notes={"named_trigger": [], "calm_worked": [], "needed_prompt": [],
                   "flags": set(), "recent": [], "count": 0})
        for item in build_recommendations(context, R):
            with self.subTest(rule=item["id"]):
                self.assertTrue(item["evidence"], "evidence must never be empty")
                self.assertTrue(item["sources"], "sources must never be empty")
                self.assertTrue(item["headline"] and item["detail"])


class RankAndCapTests(unittest.TestCase):

    def test_the_cap_is_applied_per_channel(self):
        items = [{"rank": r, "id": f"r{r}"} for r in range(10)]
        self.assertEqual(len(rank_and_cap(list(items), "alert", R)),
                         R.max_recommendations)
        self.assertEqual(len(rank_and_cap(list(items), "watch", R)),
                         R.watch_recommendations)
        self.assertEqual(len(rank_and_cap(list(items), "positive", R)),
                         R.positive_recommendations)

    def test_lower_rank_survives_the_cap(self):
        items = [{"rank": 50, "id": "late"}, {"rank": 5, "id": "early"}]
        kept = rank_and_cap(items, "positive", R)
        self.assertEqual([i["id"] for i in kept], ["early"])

    def test_ties_break_on_id_so_the_order_is_reproducible(self):
        items = [{"rank": 10, "id": "b"}, {"rank": 10, "id": "a"}]
        self.assertEqual([i["id"] for i in rank_and_cap(items, "alert", R)],
                         ["a", "b"])


class ChannelSpecificRuleTests(unittest.TestCase):

    def test_celebrate_only_fires_on_the_positive_channel(self):
        streak = {"type": "repeated", "channel": "positive", "emotion": "happy",
                  "length": 5, "distinct_days": 3,
                  "start_ts": "2026-08-01T09:00:00", "end_ts": "2026-08-03T09:00:00"}
        self.assertIsNotNone(
            rule_celebrate(make_context(channel="positive", streaks=[streak]), R))
        self.assertIsNone(
            rule_celebrate(make_context(channel="alert", streaks=[streak]), R))

    def test_monitor_watch_steps_aside_once_there_is_something_to_act_on(self):
        """"Watch, do not act yet" would contradict a named activity."""
        self.assertIsNotNone(rule_monitor_watch(make_context(channel="watch"), R))
        with_task = make_context(channel="watch", tasks=[{"task": "Reading"}])
        self.assertIsNone(rule_monitor_watch(with_task, R))


class HeadlineReasonTests(unittest.TestCase):

    def test_it_describes_the_worst_alert(self):
        context = make_context(streaks=[
            {"type": "negative", "channel": "alert", "length": 6,
             "distinct_days": 3, "start_ts": "2026-08-01T09:00:00",
             "end_ts": "2026-08-03T09:00:00"}])
        self.assertIn("6 negative check-ins in a row", headline_reason(context))

    def test_it_names_the_activity_when_there_is_one(self):
        context = make_context(channel="watch",
                               tasks=[{"task": "Reading"}])
        self.assertIn("clustered around Reading", headline_reason(context))

    def test_a_positive_student_gets_a_positive_reason(self):
        context = make_context(channel="positive", streaks=[
            {"type": "repeated", "channel": "positive", "emotion": "calm",
             "length": 4, "distinct_days": 2, "start_ts": "2026-08-01T09:00:00",
             "end_ts": "2026-08-02T09:00:00"}])
        self.assertIn('"calm"', headline_reason(context))


if __name__ == "__main__":
    unittest.main()
