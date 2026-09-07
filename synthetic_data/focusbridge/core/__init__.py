"""Shared foundations for every pipeline stage.

Nothing in `core/` knows anything about detecting, recommending, notifying or
rendering. It only knows things that are true for the whole project: where the
files live, how to read them, what the emotion words mean, and how a student's
severity is ranked.

That one-way rule is what keeps the four stages independent of each other. If
you find yourself wanting to import `detect` from inside `core`, the thing you
are writing belongs in a stage, not here.
"""
