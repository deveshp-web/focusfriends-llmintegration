"""STAGE 2 - recommendation: "so what do I do on Monday?"

Stage 1 answered "who should I look at?". A teacher's very next question is what
to actually *do*, and this stage answers it by joining the flags against
everything the detector deliberately never sees - because the detector only ever
looks at emotion check-ins:

    task_log        which activity a flagged check-in sat next to, and whether
                    that activity was completed or skipped
    notes.jsonl     what the teacher already wrote down - including, in the
                    app's own note templates, a task they already named as a
                    trigger
    stories.jsonl   which social stories this classroom already owns, so advice
                    can point at something that exists rather than inventing
                    homework
    the student row calm-corner tools already tried, AAC use, token progress

WHAT MAKES A GOOD RECOMMENDATION HERE
-------------------------------------
Every recommendation is a rule with a stated trigger, and every one cites the
numbers that fired it - for the same reason the detector is rule-based rather
than a model: **a teacher has to be able to disagree with it.** Advice a teacher
cannot audit is advice a teacher will eventually ignore.

Nothing in this stage re-decides who is flagged. It reads ``insights.jsonl`` as
given, and a student the detector did not flag gets no recommendations at all.

THE ONE IDEA WORTH READING TWICE
--------------------------------
**Every correlation compares a rate against that student's own rate.** Of the
check-ins around Reading, how many went badly - versus how often this student's
check-ins go badly at all. Not "what share of the flags happened at Reading",
which just measures the timetable. ``settings.py`` records the three ways of
getting this wrong that were tried first, and why each one failed.

HOW MUCH A TEACHER HAS TO READ
------------------------------
Caps mirror the channel split upstream: 4 recommendations for an alert student,
2 for a watch student, 1 for a positive one. A teacher with 23 flagged students
cannot read 23 x 8 pieces of advice, and advice nobody reads is worse than none.

Module map:
    settings.py       thresholds, with the statistics reasoning behind them
    stats.py          the maths, as pure functions
    phrasing.py       every word, sentence and emoji a teacher sees
    context.py        assembling everything known about one flagged student
    student_rules.py  the 14 per-student rules
    room_rules.py     the 4 whole-classroom rules
    pipeline.py       the wiring
"""
