"""Tests for the Focus Bridge pipeline.

Run them all from the ``synthetic_data`` directory::

    python -m unittest discover -s tests -t .

or one file::

    python -m unittest tests.test_detect_rules -v

``unittest`` rather than pytest on purpose: it is in the standard library, and
the whole pipeline's promise is "Python 3.9+, standard library only". A test
suite that needs an install is a test suite that gets skipped.

WHAT IS TESTED, AND WHY THAT SPLIT
----------------------------------
The tests concentrate on the **pure** functions - the rules, the statistics,
the parsing, the diff - because those are where the decisions live and they can
be checked with a seven-entry list instead of a 153 MB file.

The wiring modules (``*/pipeline.py``) are deliberately thin for exactly this
reason: they contain no arithmetic worth asserting on, so covering them would
mean building fixture corpora to test ``open()``.
"""
