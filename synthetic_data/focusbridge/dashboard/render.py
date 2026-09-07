"""Injecting the payload into the HTML template.

Twenty lines, and one of them is a security fix. That line is the reason this is
its own module rather than three statements buried in the pipeline: it is the
single point where untrusted-shaped data crosses into a document, and it should
be somewhere a reviewer can find.
"""

from __future__ import annotations

import json

#: The marker the template carries where the data belongs. Written as a comment
#: followed by ``null`` so the template is *valid, runnable JavaScript on its
#: own* - it can be opened in a browser before any data exists and will show an
#: empty state rather than a syntax error.
PAYLOAD_MARKER = "/*__PAYLOAD__*/null"


def render(payload, template_path):
    """Return the finished, self-contained HTML page.

    Args:
        payload: the dictionary from :func:`build_payload`.
        template_path: the HTML shell to inject it into.
    """
    template = _read_template(template_path)
    return template.replace(PAYLOAD_MARKER, serialise(payload))


def serialise(payload):
    """The payload as a JavaScript literal safe to embed in a ``<script>`` tag.

    Two details, both deliberate:

    ``separators=(",", ":")``
        No spaces after commas or colons. On a 4.7 MB file that is a few hundred
        kilobytes of nothing.

    ``ensure_ascii=False``
        Emoji and accented names stay as real characters instead of becoming
        ``\\uXXXX`` escapes, which is both smaller and readable in a diff. The
        page is served as UTF-8, so this is safe.

    THE ESCAPE, AND WHY IT MATTERS
    ------------------------------
    An HTML parser looks for the literal text ``</script>`` and ends the script
    block there - it does not know or care that the sequence is inside a JSON
    string. So a student note, a classroom name or a story title containing
    ``</script>`` would terminate the tag early, spilling the rest of the data
    into the page as markup. That is a broken dashboard at best and script
    injection at worst.

    Escaping every ``</`` as ``<\\/`` prevents it. ``\\/`` is a valid JSON
    escape for ``/`` and parses back to exactly the same string, so nothing is
    lost - the data is unchanged, only its spelling inside the document is.
    """
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return blob.replace("</", "<\\/")


def _read_template(template_path):
    with open(template_path, encoding="utf-8") as handle:
        return handle.read()
