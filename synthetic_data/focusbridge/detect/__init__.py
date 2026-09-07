"""STAGE 1 - detection: "who should I look at?"

Reads ``students.jsonl`` and flags students whose recent emotion check-ins form
a pattern. Four rules, sorted into three channels so that soft signals and good
news never crowd the queue a teacher is actually expected to read:

    CHANNEL   RULE               FIRES WHEN
    --------  -----------------  ----------------------------------------------
    alert     negative           4+ bad check-ins in a row
    alert     same_day_cluster   6+ bad check-ins inside one school day
    watch     same_day_cluster   4-5 bad check-ins inside one school day
    watch     density            a mostly-bad week that never reaches 4 in a row
    positive  repeated           the same good emotion 4+ times running

The rules stay simple and explainable on purpose. A teacher has to be able to
look at a flag, disagree with it, and see exactly which threshold produced it -
which is also why every threshold in ``settings.py`` carries the reasoning that
put it there, and why every finding cites the numbers that fired it.

Module map:
    settings.py   the thresholds and the arguments behind them
    rules.py      the four rules as pure functions - no files, easy to test
    summarize.py  per-student rollup, priority, and the stable flag id
    judge.py      sampling borderline cases for an external LLM audit
    pipeline.py   the wiring that runs all of the above over the corpus
"""
