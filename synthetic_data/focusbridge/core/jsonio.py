"""The one and only way this project reads and writes JSON.

WHY THIS FILE EXISTS
--------------------
Before this refactor there were **five** separate implementations of "read a
JSONL file" scattered across the codebase: ``iter_jsonl`` in the detector,
``load_jsonl`` in the LLM step, another ``load_jsonl`` in the Streamlit viewer,
and - worst of the set - two different functions both named ``load_insights``,
one in the recommender returning a **dict** and one in the review CLI returning
a **list**.

They had already drifted: the viewer's cached its result and returned empty for
a missing file, while the others re-read from disk and crashed. That is exactly
the kind of divergence that produces a bug reproducible in one tool and nowhere
else.

Consolidating them costs nothing and buys three things:

  * one place to add a feature - the line-numbered error messages below now
    help every caller, not just the one that was being debugged;
  * one place to fix a bug;
  * a clear vocabulary - callers now say what they *want* (``index_by``,
    ``group_by``) instead of writing the same loop again.

ABOUT JSONL
-----------
"JSONL" (also called NDJSON) is a text file with **one complete JSON object per
line**. It is used here instead of one big JSON array for one reason: a 153 MB
array must be fully loaded into memory before you can look at the first record,
whereas a JSONL file can be *streamed* one line at a time in constant memory.
That is why :func:`read_jsonl` is a generator - see its docstring.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


# The line ending every file this project writes uses, on every platform.
#
# Python's text mode otherwise translates a newline into the *host platform's*
# line ending on the way out. That means the same pipeline, over the same
# corpus, produces byte-different files on Windows and on macOS: checksums stop
# matching, `diff` between two machines becomes useless, and "did anything
# actually change?" turns into a question nobody can answer.
#
# These are data files with a defined format, not platform-native documents, so
# LF is correct everywhere. Every writer below passes this explicitly, which is
# only practical because there is exactly one place that opens a file to write.
NEWLINE = "\n"


class JsonlError(ValueError):
    """A JSONL file contained a line that is not valid JSON.

    Carries the file and line number, because "Expecting ',' delimiter" on its
    own tells you nothing when the file has four million lines.
    """


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def read_jsonl(path, skip_missing=False):
    """Yield each record in a JSONL file, one at a time.

    This is a **generator**: it hands back one record, waits for the caller to
    finish with it, then reads the next line. Only one record is ever in memory,
    so this works on the 153 MB ``students.jsonl`` on a laptop.

    Complexity: O(n) time in the number of lines, O(1) additional memory.

    Args:
        path: the file to read.
        skip_missing: if True, a missing file yields nothing instead of raising.
            Use it for genuinely optional inputs (the change feed, the AI
            suggestions), never for inputs a stage depends on - a silent empty
            result is much harder to debug than a clear error.

    Raises:
        JsonlError: a line was not valid JSON, naming the file and line number.
    """
    path = Path(path)
    if skip_missing and not path.exists():
        return
    with open(path, encoding="utf-8") as handle:
        # `enumerate(..., 1)` numbers lines the way a text editor does, from 1,
        # so the number in an error message can be typed straight into "go to
        # line" rather than being off by one.
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue  # blank lines are padding, not data
            try:
                yield json.loads(line)
            except json.JSONDecodeError as error:
                raise JsonlError(
                    f"{path}:{line_number}: not valid JSON ({error.msg})"
                ) from error


def read_jsonl_list(path, skip_missing=False):
    """The whole file as a list.

    Use this only when the file is known to be small (classrooms, insights,
    recommendations) or when you genuinely need random access. For
    ``students.jsonl`` use :func:`read_jsonl` and stay streaming.
    """
    return list(read_jsonl(path, skip_missing=skip_missing))


def index_by(path, key, skip_missing=False):
    """Read a JSONL file into a ``{record[key]: record}`` lookup table.

    Used wherever one stage needs to join against another's output - "given a
    student id, what did the detector say about them?". A dict turns that from
    an O(n) scan per lookup into O(1), which matters because these joins happen
    inside loops over every student.

    If two records share a key the later one wins, matching the "last write
    wins" convention of the append-only files in this project.

    Complexity: O(n) to build, O(1) per lookup afterwards.
    """
    return {record.get(key): record
            for record in read_jsonl(path, skip_missing=skip_missing)}


def group_by(path, key, skip_missing=False):
    """Read a JSONL file into a ``{record[key]: [records...]}`` lookup table.

    The one-to-many sibling of :func:`index_by`: a student has many notes, a
    classroom has many stories. Same reasoning - one O(n) pass up front instead
    of an O(n) filter inside a per-student loop, which would be O(n**2) overall.
    """
    grouped = {}
    for record in read_jsonl(path, skip_missing=skip_missing):
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def read_json(path):
    """Read a whole-file JSON document (the ``*_meta.json`` run summaries)."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def write_text(path, text):
    """Write a whole text file with consistent, platform-independent newlines."""
    with open_text_writer(path) as handle:
        handle.write(text)


def open_text_writer(path):
    """Open a file for writing text, for use in a ``with`` block.

    The single place this project opens a file for writing, so the newline
    policy above is impossible to forget at a call site.
    """
    return open(path, "w", encoding="utf-8", newline=NEWLINE)


def open_text_appender(path):
    """Open a file for *appending* text - used by the append-only review log."""
    return open(path, "a", encoding="utf-8", newline=NEWLINE)


def write_jsonl(path, records):
    """Write an iterable of records as JSONL. Returns how many were written.

    Kept as a plain function taking an iterable so callers can hand it a
    generator and never build the full list in memory.
    """
    written = 0
    with open_text_writer(path) as handle:
        for record in records:
            handle.write(dumps_line(record))
            written += 1
    return written


def write_json(path, document):
    """Write a whole-file JSON document, indented so a human can read the diff."""
    with open_text_writer(path) as handle:
        json.dump(document, handle, indent=2)


def dumps_line(record):
    """One record serialised as a JSONL line, newline included.

    Split out from :func:`write_jsonl` because the streaming stages write
    records as they are produced rather than collecting them first, and they
    should not have to remember the trailing newline.
    """
    return json.dumps(record) + "\n"


def open_jsonl_writer(path):
    """Open a file for streamed JSONL writing, for use in a ``with`` block.

    Stage 1 cannot use :func:`write_jsonl`: it reads a 153 MB input and writes
    its output in the same pass, so it needs the file handle open across the
    loop rather than a finished list of records.
    """
    return open_text_writer(path)


# ---------------------------------------------------------------------------
# small filesystem helpers
# ---------------------------------------------------------------------------

def exists(path):
    """True if the path exists. Wrapped so callers need not import `os` too."""
    return os.path.exists(path)


def file_size_kb(path):
    """File size in kilobytes, for the "how big is the dashboard?" report line."""
    return os.path.getsize(path) / 1024
