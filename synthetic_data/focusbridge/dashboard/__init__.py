"""STAGE 4 - the teacher view: one self-contained HTML file.

No server, no network, no build step. Everything the page needs is embedded in
it, so it opens from a USB stick in a classroom with no wifi - which is a real
constraint in the schools this is built for, not a hypothetical one.

    python dashboard.py                      # every classroom (~4.7 MB)
    python dashboard.py --classroom WAJHZX   # one room (~50 KB)
    python dashboard.py --teacher "Ahmed"    # every room for one teacher

SCOPED TO ONE CLASSROOM AT A TIME
---------------------------------
A room is the unit a teacher owns, so the page renders one at a time and a
picker switches between them. That is also what keeps a 220-room file
responsive: the data for every room is present, but only the selected room is
ever laid out.

READING ORDER INSIDE A ROOM
---------------------------
It follows the channel split the detector established, because the entire point
of that split is that not everything deserves equal attention:

    1. room-wide recommendations   things to change once, not per student
    2. the ALERT queue             the students a teacher is expected to read
    3. WATCH                       context, collapsed by default
    4. POSITIVE                    good news, collapsed by default

COLOUR
------
Priority uses the fixed status palette and **always** ships an icon and a word
alongside the colour, so nothing is carried by hue alone - which matters for
colour-blind readers and for the projector in the corner of a classroom.

The check-in strip is a diverging encoding: positive and negative are the poles,
neutral is the midpoint and is meant to recede, because an "okay" check-in is
the absence of a signal rather than a weak one.

Module map:
    payload.py   compressing the data the page needs
    render.py    injecting it into the HTML template
    pipeline.py  the wiring
"""
