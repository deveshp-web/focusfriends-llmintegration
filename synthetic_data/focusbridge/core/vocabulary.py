"""The emotion words the whole system is built on, and what they mean.

WHY THIS FILE EXISTS
--------------------
Every stage needs to answer "is this check-in a bad one?". If each stage
answered it from its own copy of the word list, the detector could flag a
student on a word the dashboard did not know how to colour. So the vocabulary
lives here, once, and every stage imports it.

TWO VOCABULARIES, ONE MEANING
-----------------------------
Check-ins arrive from two places that were built separately:

  * the **synthetic corpus**, which emits the first nine emotions below;
  * **exports from the Focus Bridge app**, which can also emit ``scared``,
    ``confused`` and ``tired``, plus informal spellings a teacher typed.

Rather than making every stage handle both, everything is normalised to one
canonical set on the way in - see :func:`normalize_emotion`. That is the only
place the two vocabularies meet.

.. important::
   The app ships its own copy of this word list. **Adding an emotion means
   editing two places**: this file and the app's emotion picker. If only one
   side learns the word, check-ins using it are silently dropped as unknown.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# the canonical vocabulary
# ---------------------------------------------------------------------------

#: Canonical emotion -> valence. "Valence" is the technical word for which side
#: of good/bad a feeling sits on, and it is what every detection rule is
#: actually written against - the rules care about "four bad ones in a row",
#: not about which four.
#:
#: Two judgement calls worth knowing about, because they are not obvious:
#:
#:   * ``confused`` counts as **negative** because the app itself routes it to
#:     a help prompt - the app already treats it as a call for support.
#:   * ``tired`` stays **neutral** because it is as often a nap signal as it is
#:     distress, and treating it as negative flooded the alert queue.
EMOTION_VALENCE = {
    # from the synthetic corpus and the app
    "happy": "positive",
    "calm": "positive",
    "excited": "positive",
    "okay": "neutral",
    "frustrated": "negative",
    "anxious": "negative",
    "overwhelmed": "negative",
    "sad": "negative",
    "angry": "negative",
    # app exports only
    "scared": "negative",
    "confused": "negative",
    "tired": "neutral",
}

#: Informal wording that shows up in app builds and hand-edited exports, mapped
#: onto the canonical word. Without this, "worried" would be dropped as unknown
#: and would silently break a streak that should have continued.
ALIASES = {
    "afraid": "scared",
    "worried": "anxious",
    "nervous": "anxious",
    "mad": "angry",
    "upset": "sad",
    "sleepy": "tired",
    "fine": "okay",
    "good": "happy",
    "great": "happy",
    "relaxed": "calm",
}

# ---------------------------------------------------------------------------
# derived lookups
# ---------------------------------------------------------------------------
# These are all computed from EMOTION_VALENCE rather than typed out again, so a
# new emotion added above is automatically understood everywhere. Typing the
# lists out by hand is how two lists drift apart.

#: The emotions a detection rule treats as "a bad check-in".
#: A `set` and not a list on purpose: membership testing (`emotion in
#: NEGATIVE_EMOTIONS`) is O(1) for a set and O(n) for a list, and this test runs
#: once per check-in per rule - millions of times over a full corpus.
NEGATIVE_EMOTIONS = frozenset(
    emotion for emotion, valence in EMOTION_VALENCE.items() if valence == "negative"
)

#: The good ones. Used by the "repeated" rule and by the dashboard's palette.
POSITIVE_EMOTIONS = frozenset(
    emotion for emotion, valence in EMOTION_VALENCE.items() if valence == "positive"
)

#: A stable ordering of the emotions. The dashboard encodes each check-in as
#: its *position* in this list, so the order must not change within a released
#: file - it is written into the payload and read back by the page.
EMOTION_ORDER = tuple(EMOTION_VALENCE)

#: emotion -> its position in EMOTION_ORDER.
#: The dashboard used to call ``EMOTION_ORDER.index(emotion)``, which scans the
#: list from the start every time: O(v) per check-in. This dict makes it O(1).
#: With 12 emotions the saving is small in absolute terms, but it is free, and
#: "look it up in a dict" is the habit worth having.
EMOTION_INDEX = {emotion: position for position, emotion in enumerate(EMOTION_ORDER)}


# ---------------------------------------------------------------------------
# the one entry point
# ---------------------------------------------------------------------------

def normalize_emotion(raw):
    """Turn whatever was logged into a canonical emotion, or ``None``.

    This is the single gate between the outside world's spelling and this
    project's vocabulary. Everything downstream may assume it is looking at a
    key of :data:`EMOTION_VALENCE`.

    Handles, in order: a non-string (a null, a number, a malformed row),
    surrounding whitespace, capitalisation, and informal synonyms.

    ``None`` means "there was a check-in here, but we cannot tell what it was".
    That is deliberately different from "there was no check-in": an unreadable
    check-in still *breaks a streak*, because pretending it was not there would
    join the check-ins on either side into a run that never happened.

    >>> normalize_emotion("  Worried ")
    'anxious'
    >>> normalize_emotion("banana") is None
    True
    """
    if not isinstance(raw, str):
        return None
    word = raw.strip().lower()
    word = ALIASES.get(word, word)  # resolve a synonym if there is one
    return word if word in EMOTION_VALENCE else None


def valence_of(emotion, default="unknown"):
    """The valence of a canonical emotion; ``default`` for ``None``/unknown."""
    return EMOTION_VALENCE.get(emotion, default)


def is_negative(emotion):
    """True if this emotion counts as a bad check-in. ``None`` is not negative."""
    return emotion in NEGATIVE_EMOTIONS


def describe_valence_mix(emotions):
    """Summarise a run of emotions as ``negative``/``positive``/``mixed``/etc.

    Used to label a detected pattern. ``None`` entries (unreadable check-ins)
    are ignored here rather than counted as their own valence, because a run is
    described by what we *could* read; if nothing was readable the answer is
    ``"unknown"``.
    """
    valences = {EMOTION_VALENCE[e] for e in emotions if e is not None}
    if not valences:
        return "unknown"
    if len(valences) > 1:
        return "mixed"
    # Exactly one valence present - unwrap it without mutating the set.
    return next(iter(valences))
