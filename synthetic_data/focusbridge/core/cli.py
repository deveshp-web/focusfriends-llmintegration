"""Command-line pieces shared by every stage.

WHY THIS FILE EXISTS
--------------------
All four stages take the same two options - which directory the data is in, and
which run suffix to use - and all four used to define them separately, with
slightly different help text. Defining them once means the four stages cannot
drift apart, and a new stage gets consistent flags for free.

Everything here is deliberately thin. Argument parsing should be boring; if a
stage needs something clever, that belongs in the stage.
"""

from __future__ import annotations

import argparse
import functools

from .jsonio import JsonlError
from .paths import DataPaths


def build_parser(module_docstring):
    """An ``ArgumentParser`` whose ``--help`` line is the module's own summary.

    Passing ``__doc__`` in keeps the CLI description and the module docstring
    from ever disagreeing: there is only one sentence, written once, at the top
    of the file where a reader meets it first.
    """
    summary = (module_docstring or "").strip().splitlines()
    return argparse.ArgumentParser(description=summary[0] if summary else None)


def add_data_dir_argument(parser):
    """``--data-dir``: run the stage against a different corpus.

    The old code pinned the data directory to wherever the script file happened
    to live, which meant the pipeline could only ever be run against the one
    real dataset - no fixtures, no second corpus, and tests that had to copy the
    scripts somewhere else to run at all.
    """
    parser.add_argument(
        "--data-dir", metavar="DIR", default=None,
        help="directory holding the corpus (default: the synthetic_data folder "
             "next to this package, or $FOCUSBRIDGE_DATA_DIR)")


def add_suffix_argument(parser, help_text=None):
    """``--suffix``: write (and read) a parallel set of output files.

    ``--suffix _prev`` turns ``insights.jsonl`` into ``insights_prev.jsonl``.
    That is how an earlier snapshot is produced without disturbing the current
    one, which is what gives the change feed a *real* previous state to compare
    against instead of a fabricated fixture.
    """
    parser.add_argument(
        "--suffix", default="",
        help=help_text or "append to every output filename, e.g. _prev")


def paths_from_args(args):
    """Build the :class:`DataPaths` a stage should use from parsed arguments."""
    return DataPaths(root=getattr(args, "data_dir", None),
                     suffix=getattr(args, "suffix", ""))


def fail(message):
    """Stop the program with a message and a non-zero exit code.

    ``SystemExit`` rather than ``sys.exit`` so this reads as an expression and
    can be used as ``raise fail(...)``-style flow control at a call site; and a
    plain message rather than a traceback, because a missing input file is a
    user mistake, not a crash, and a wall of stack trace hides the one line that
    says what to do about it.
    """
    return SystemExit(message)


def friendly_data_errors(entry_point):
    """Decorator: report a corrupt data file as one line, not a stack trace.

    A malformed line in ``students.jsonl`` is a problem with the *data*, not a
    bug in this code, and the useful part of the report is the one line naming
    the file and the line number - :class:`JsonlError` already carries both.
    Wrapping thirty lines of traceback around it buries that under machinery the
    reader cannot act on and did not ask about.

    Deliberately narrow: it catches only ``JsonlError``. A genuine bug in this
    package still gets its full traceback, because that one *is* for us to read.

    ``functools.wraps`` copies the wrapped function's name and docstring onto
    the wrapper, so ``--help`` and tracebacks still say ``main``.
    """
    @functools.wraps(entry_point)
    def wrapper(argv=None):
        try:
            return entry_point(argv)
        except JsonlError as error:
            # `from None` suppresses the "during handling of the above
            # exception" chain - there is nothing above worth showing.
            raise fail(str(error)) from None
    return wrapper


def require(path, hint):
    """Check that a required input exists, or exit with a message saying how to make it.

    Every stage in this pipeline consumes the previous stage's output, so "file
    not found" almost always means "you skipped a step". Saying which step turns
    a dead end into an instruction.
    """
    if not path.exists():
        raise fail(f"{path.name} not found - {hint}")
    return path
