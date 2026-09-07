"""Every word, list and emoji a teacher actually sees.

WHY THE WORDS LIVE APART FROM THE LOGIC
---------------------------------------
Content and code change for completely different reasons and by completely
different people. A teacher, a school lead or a speech therapist may well want
to reword "Go back to Deep Breathing" or add a story cue - and none of that
should require reading a statistics module, or risk breaking one.

Separating them is the cohesion argument at its most practical:

  * ``student_rules.py`` decides **whether** to say something;
  * this file decides **how it is said**.

So this module holds only data - dictionaries and tuples. It has no functions
that decide anything, and it imports nothing from the rest of the package. It
can be read, and edited, by someone who does not write Python.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# the calm corner
# ---------------------------------------------------------------------------
# Two vocabularies again, exactly as with emotions. The Focus Bridge app ships
# the tools under the labels on the left; the synthetic data records the ids on
# the right. They were built separately and only `breathing` and `bubbles`
# agree, so this registry maps both onto one set - which is what lets a
# recommendation always name something a student can actually be handed in the
# app, rather than a tool id that exists only in a data file.
CALM_TOOLS = {
    "breathing": {"label": "Deep Breathing", "emoji": "\U0001f32c️",
                  "aliases": ("counting",)},
    "bubbles": {"label": "Pop Bubbles", "emoji": "\U0001fae7", "aliases": ()},
    "fidget": {"label": "Fidget Spinner", "emoji": "\U0001f300",
               "aliases": ("squeeze-ball", "stretch")},
    "ground": {"label": "5-4-3-2-1", "emoji": "\U0001f590️", "aliases": ()},
    "rain": {"label": "Watch Rain", "emoji": "\U0001f327️",
             "aliases": ("music", "weighted-blanket")},
    "stars": {"label": "Starry Night", "emoji": "\U0001f30c",
              "aliases": ("dim-lights",)},
}

#: alias -> canonical tool id. Derived from the table above rather than typed
#: out again, so adding an alias means editing one line, not two.
CALM_ALIASES = {alias: tool
                for tool, spec in CALM_TOOLS.items()
                for alias in spec["aliases"]}

#: Which tool to reach for first, given what the student is actually feeling.
#: The app's own emotion tips make the same pairings - scared goes to grounding,
#: angry goes to breathing - so a recommendation never contradicts what the
#: child already sees on screen.
#:
#: **Order matters**: the first tool the student has not already exhausted is
#: the one offered, so the most-fitting untried option comes first.
EMOTION_TOOLS = {
    "anxious": ("breathing", "ground", "rain"),
    "scared": ("ground", "breathing", "stars"),
    "overwhelmed": ("stars", "rain", "breathing"),
    "angry": ("breathing", "fidget", "bubbles"),
    "frustrated": ("fidget", "bubbles", "breathing"),
    "sad": ("rain", "bubbles", "stars"),
    "confused": ("ground", "breathing", "fidget"),
}

#: What each negative emotion is a signal *of*, in plain words.
#: Used to say **why** a recommendation fits rather than merely asserting that
#: it does - the difference between advice a teacher can evaluate and advice
#: they have to take on trust.
EMOTION_READS = {
    "anxious": "anticipating something",
    "scared": "not feeling safe",
    "overwhelmed": "too much input at once",
    "angry": "a demand landing harder than it can be met",
    "frustrated": "a task pitched above where the student is",
    "sad": "something carried in from outside the moment",
    "confused": "an instruction that did not land",
}

#: Where the tool is not the whole answer, say so.
#:
#: Sadness is the case that made this necessary: the generic copy recommended a
#: calm-corner tool while simultaneously describing sadness as needing a
#: conversation instead - advice that argues with itself, which is worse than
#: no advice because it teaches a teacher not to trust the rest.
EMOTION_CAVEATS = {
    "sad": "Ask before you offer, though - with sadness the conversation does "
           "more than the tool does.",
    "angry": "Offer it early; once anger is at its peak, a screen is another "
             "demand rather than a relief.",
}


# ---------------------------------------------------------------------------
# social stories
# ---------------------------------------------------------------------------
#: ``(keyword in the story title, activities it prepares for, emotions it fits)``
#:
#: Matched against the correlated activity first and the dominant emotion
#: second, so a teacher is pointed at a story their classroom **already owns**
#: rather than being given homework.
#:
#: Keyword-based rather than id-based on purpose: stories a teacher writes
#: themselves still match, which is most of them after the first term.
STORY_CUES = (
    ("fire drill", ("fire drill",), ("scared", "anxious", "overwhelmed")),
    ("substitute", ("substitute",), ("anxious", "overwhelmed")),
    ("assembly", ("assembly", "gym", "music"), ("overwhelmed", "anxious")),
    ("recess", ("recess",), ("anxious", "sad", "frustrated")),
    ("field trip", ("field trip", "trip"), ("anxious", "overwhelmed")),
    ("bus", ("bus", "arrival", "dismissal"), ("anxious", "scared")),
    ("friend", ("recess", "lunch", "circle time"), ("sad", "anxious")),
    ("picture day", ("picture",), ("anxious", "scared")),
    ("haircut", (), ("scared", "anxious")),
    ("dentist", (), ("scared", "anxious")),
)


# ---------------------------------------------------------------------------
# mining the teacher's own notes
# ---------------------------------------------------------------------------
# The app writes notes from templates, and three of those templates name a task
# outright. That is the teacher's own read of what set the student off, which is
# worth more than anything computed in this package - so it is extracted and
# *compared against*, never averaged in.
#
# Each pattern captures the task name in group 1.
NOTE_PATTERNS = (
    ("named_trigger", r"struggled with the transition to (.+?);"),
    ("calm_worked", r"used the calm corner after (.+?) and returned"),
    ("needed_prompt", r"needed an extra prompt during (.+?) but"),
)

#: Notes that carry a signal but no task name - present-or-absent flags.
NOTE_FLAGS = (
    ("tired_after_lunch", r"seemed tired after lunch"),
    ("token_goal_met", r"reached the goal today"),
    ("aac_progress", r"practiced two new AAC phrases"),
    ("parent_contact", r"parent check-in"),
    ("good_morning", r"had a great morning"),
)

#: How many recent notes to keep per student for display. Enough to show the
#: teacher what they last wrote; few enough that the dashboard payload does not
#: balloon with text nobody scrolls to.
MAX_RECENT_NOTES = 5
