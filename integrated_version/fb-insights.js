/* ============================================================================
   Focus Bridge — the insights engine, in the browser
   ============================================================================

   WHAT THIS IS
   ------------
   The `synthetic_data/focusbridge` Python package answers three questions about
   a classroom's emotion check-ins:

       detect     who should I look at?
       recommend  so what do I do Monday?
       notify     what changed since I last looked?

   It answers them offline, over a 153 MB corpus, and renders a static HTML
   file. That is the right shape for a research corpus and the wrong shape for
   a classroom: a teacher using Focus Bridge has a live roster in front of them
   and no Python.

   So the rules are ported here, to plain JavaScript, and run against the app's
   own records the moment a teacher opens the Insights tab. No server, no
   export step, no second tool — the same analysis, on this class, right now.

   WHY A PORT AND NOT A SERVICE
   ----------------------------
   Check-ins are children's emotional records. The app's whole privacy posture
   is that they stay where the teacher put them — on the device in demo mode,
   or in their own row-level-secured Supabase project. Sending them to an
   analysis service to get advice back would quietly undo that. Everything in
   this file runs on data the browser already has.

   FAITHFULNESS TO THE PYTHON
   --------------------------
   This is a port, not a reimplementation. The thresholds, the channel split,
   the priority test, the binomial correlation and all nineteen rules are the
   ones in `focusbridge/`, and `tools/verify_engine.py` checks that this file
   and the Python agree student-for-student on the sample rooms. Two things
   differ deliberately, both because the app knows things the corpus does not:

     * the window is anchored to the room's own latest check-in, and the view
       says so when that is not today;
     * "since your last check" compares against a snapshot this browser saved,
       rather than against a second pinned detector run.

   The ONE rule that matters most, restated because it is the easiest thing in
   here to break: every correlation compares **a rate against that student's
   own rate** — of the check-ins around Reading, how many went badly, versus
   how often this student's check-ins go badly at all. Never a share of flags,
   never a fixed lift threshold. `RECOMMEND.minTaskExcess` in this file and the
   long comment above `min_task_excess` in the Python record the three ways of
   getting that wrong that were tried first.

   LAYOUT
   ------
       vocabulary      the emotion words and what they mean
       time            parsing the two check-in schemas into one
       Timeline        the O(1) window index the rules are built on
       rules           the four detection rules
       summarize       one student's findings -> one sortable verdict
       stats           the binomial test both correlation rules share
       context         the joins and correlations, before any advice
       studentRules    fifteen rules, each one small function
       roomRules       four more, for what only shows up across students
       diff            how a student moved since the last look
       analyseClassroom  the entry point that wires it together
   ========================================================================= */

(function (root) {
"use strict";

/* ===========================================================================
   VOCABULARY
   ---------------------------------------------------------------------------
   Kept in step with `focusbridge/core/vocabulary.py` AND with the `EM` array in
   index.html. Adding an emotion means editing all three; if only one side
   learns the word, check-ins using it are silently dropped as unknown.
   ======================================================================== */

var EMOTION_VALENCE = {
  happy: "positive",
  calm: "positive",
  excited: "positive",
  okay: "neutral",
  frustrated: "negative",
  anxious: "negative",
  overwhelmed: "negative",
  sad: "negative",
  angry: "negative",
  // The app can also emit these three. `confused` counts as negative because
  // the app itself routes it to a help prompt — it already treats it as a call
  // for support. `tired` stays neutral because it is as often a nap signal as
  // distress, and treating it as negative flooded the alert queue.
  scared: "negative",
  confused: "negative",
  tired: "neutral"
};

/* Informal wording a teacher might type, or an older build might emit. Without
   this, "worried" is dropped as unknown and silently breaks a streak that
   should have continued. */
var ALIASES = {
  afraid: "scared", worried: "anxious", nervous: "anxious", mad: "angry",
  upset: "sad", sleepy: "tired", fine: "okay", good: "happy",
  great: "happy", relaxed: "calm"
};

var EMOTION_ORDER = Object.keys(EMOTION_VALENCE);

var NEGATIVE = {};
var POSITIVE = {};
EMOTION_ORDER.forEach(function (name) {
  if (EMOTION_VALENCE[name] === "negative") NEGATIVE[name] = true;
  if (EMOTION_VALENCE[name] === "positive") POSITIVE[name] = true;
});

/* The single gate between the outside world's spelling and this file's
   vocabulary. `null` means "there was a check-in here, but we cannot tell what
   it was" — deliberately different from "there was no check-in", because an
   unreadable check-in still breaks a streak. */
function normalizeEmotion(raw) {
  if (typeof raw !== "string") return null;
  var word = raw.trim().toLowerCase();
  if (ALIASES[word]) word = ALIASES[word];
  return EMOTION_VALENCE[word] ? word : null;
}

function isNegative(emotion) { return !!NEGATIVE[emotion]; }

function valenceOf(emotion) { return EMOTION_VALENCE[emotion] || "unknown"; }

function describeValenceMix(emotions) {
  var seen = {};
  emotions.forEach(function (e) { if (e) seen[EMOTION_VALENCE[e]] = true; });
  var keys = Object.keys(seen);
  if (!keys.length) return "unknown";
  return keys.length > 1 ? "mixed" : keys[0];
}

/* ===========================================================================
   SETTINGS
   ---------------------------------------------------------------------------
   Every number the engine uses, in one place, so that "what would this look
   like with a streak of 5?" is a question you can answer without hunting.
   The values are the Python defaults; the reasoning for each lives in
   `focusbridge/detect/settings.py` and `focusbridge/recommend/settings.py`.
   ======================================================================== */

var DETECT = {
  streakLength: 4,          // four bad check-ins in a row is a pattern; three is a rough day
  streakWindowDays: 7,      // and they have to be close enough together to be one episode
  minDistinctDays: 2,       // ...but spread over more than a single rough morning
  recencyDays: 30,          // how far back a teacher can still remember the context
  repeatedValences: { positive: true },   // four "okay" in a row is not news
  enableDensity: true,
  densityWindowDays: 7,
  densityMinEntries: 6,
  densityMinNegativeShare: 0.7,
  enableSameDayCluster: true,
  sameDayClusterLength: 4,
  sameDayAlertLength: 6,    // at this size a cluster stops being a rough morning
  highPriorityMinLength: 6,
  highPriorityMinDays: 3
};

var RECOMMEND = {
  taskWindowMinutes: 15,    // measured: check-in to nearest task is 5 min median, 10 at p95
  minTaskHits: 3,
  minTaskExposure: 5,       // three out of three is 100% and is also nothing
  minTaskExcess: 0.15,      // a RATE against the student's own rate — never a share
  taskMaxP: 0.05,
  minSkipped: 2,
  timeMinHits: 4,
  timeMaxP: 0.05,
  timeMinExcess: 0.15,
  aacLowRatio: 0.35,
  maxRecommendations: 4,    // per-channel caps: the alert-fatigue argument, one layer down
  watchRecommendations: 2,
  positiveRecommendations: 1,
  roomMinFlagged: 3,
  roomTaskMinStudents: 3,
  roomTaskMinShare: 0.25,
  roomTimeMinShare: 0.50    // only three time bands, so the bar has to be higher
};

var PRIORITY_RANK = { high: 0, medium: 1, watch: 2, info: 3 };
var UNFLAGGED_RANK = 4;
var RANK_LABEL = { 0: "high", 1: "medium", 2: "watch", 3: "info", 4: "not flagged" };

function priorityRank(priority) {
  return PRIORITY_RANK[priority] === undefined ? UNFLAGGED_RANK : PRIORITY_RANK[priority];
}

/* Half-open time bands: a check-in at exactly 11:00 is midday, not morning,
   which guarantees every hour lands in exactly one band. */
var TIME_BUCKETS = [
  ["morning", 0, 11, "before 11am"],
  ["midday", 11, 13, "the 11am–1pm block"],
  ["afternoon", 13, 24, "after 1pm"]
];
var TIME_PHRASES = {};
TIME_BUCKETS.forEach(function (b) { TIME_PHRASES[b[0]] = b[3]; });

function bucketForHour(hour) {
  for (var i = 0; i < TIME_BUCKETS.length; i++) {
    if (hour >= TIME_BUCKETS[i][1] && hour < TIME_BUCKETS[i][2]) return TIME_BUCKETS[i][0];
  }
  return null;
}

/* ===========================================================================
   TIME
   ---------------------------------------------------------------------------
   Two schemas reach this engine and both have to become one timestamp:

     {ts: "2026-07-14T09:12:00"}                  a corpus row, or an import
     {iso: "2026-07-14", time: "09:12 AM"}        what the app itself writes

   Dates are built field by field rather than handed to `new Date(string)`,
   because the browser's parsing of a bare date ("2026-07-14") is UTC while its
   parsing of a date-time without a zone is local — so the two schemas would
   land hours apart and a check-in could cross a day boundary on its way in.
   ======================================================================== */

var ISO_RE = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/;

function parseISO(text) {
  var m = ISO_RE.exec(String(text || ""));
  if (!m) return null;
  var date = new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0), 0);
  return isNaN(date.getTime()) ? null : date;
}

/* "09:41", "09:41 AM" or "9:41:00 PM" -> [hour, minute]; null if unreadable.
   The narrow no-break space is what some browsers put before AM/PM. */
function parseClock(raw) {
  var text = String(raw || "").trim().toUpperCase().replace(/ /g, " ");
  var meridiem = null;
  if (/AM$/.test(text)) { meridiem = "AM"; text = text.slice(0, -2).trim(); }
  else if (/PM$/.test(text)) { meridiem = "PM"; text = text.slice(0, -2).trim(); }

  var parts = text.split(":");
  if (parts.length < 2) return null;
  var hour = parseInt(parts[0], 10), minute = parseInt(parts[1], 10);
  if (isNaN(hour) || isNaN(minute)) return null;

  // The two cases people get wrong: 12 PM is noon, 12 AM is midnight.
  if (meridiem === "PM" && hour !== 12) hour += 12;
  else if (meridiem === "AM" && hour === 12) hour = 0;

  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
  return [hour, minute];
}

/* When a logged row happened, in either schema. A row with a `ts` that will not
   parse returns null rather than falling through to the other fields: a row
   with a broken timestamp is corrupt, and guessing from a second field would
   invent data. */
function parseTimestamp(entry) {
  if (!entry || typeof entry !== "object") return null;

  if (typeof entry.ts === "string" && entry.ts) return parseISO(entry.ts);

  var dayText = entry.iso || entry.date;
  if (typeof dayText !== "string" || !dayText) return null;

  var midnight = parseISO(dayText.slice(0, 10)) || parseLocaleDate(dayText);
  if (!midnight) return null;

  // A missing or unreadable clock degrades to midnight on that date rather than
  // throwing the row away: the day is still usable evidence.
  if (typeof entry.time === "string" && entry.time) {
    var clock = parseClock(entry.time);
    if (clock) { midnight.setHours(clock[0], clock[1], 0, 0); }
  }
  return midnight;
}

/* "8/14/2026" — what `toLocaleDateString()` writes in a US locale, which is
   what the app's note rows carry. Only reached when the ISO parse failed. */
function parseLocaleDate(text) {
  var m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})/.exec(String(text).trim());
  if (!m) return null;
  var date = new Date(+m[3], +m[1] - 1, +m[2], 0, 0, 0, 0);
  return isNaN(date.getTime()) ? null : date;
}

function dayKey(date) {
  return date.getFullYear() + "-" +
         String(date.getMonth() + 1).padStart(2, "0") + "-" +
         String(date.getDate()).padStart(2, "0");
}

var DAY_MS = 86400000;

/* Whole days between two dates, counted date-to-date rather than by duration:
   08:00 Monday to 16:00 Tuesday spans "1 day", not "1.3 days", because that is
   how a teacher counts. */
function daysBetween(from, to) {
  var a = new Date(from.getFullYear(), from.getMonth(), from.getDate());
  var b = new Date(to.getFullYear(), to.getMonth(), to.getDate());
  return Math.round((b - a) / DAY_MS);
}

/* Clean, sorted check-ins for one student.

   Two dropping rules, and the difference between them is the most important
   thing in this section:
     * an unreadable TIMESTAMP means the row is dropped — it cannot be placed
       in the sequence at all;
     * an unreadable EMOTION means the row is KEPT, with emotion null. If it
       were dropped, the check-ins on either side would become adjacent and
       "four bad ones in a row" could be manufactured out of a gap we could not
       read. Keeping it breaks the run, which is the honest answer. */
function readCheckIns(log) {
  var out = [];
  (log || []).forEach(function (row) {
    if (!row || typeof row !== "object") return;
    var at = parseTimestamp(row);
    if (!at) return;
    // Two field names for the same thing, because two systems wrote this data.
    out.push({ at: at, emotion: normalizeEmotion(row.emotion || row.label), day: dayKey(at) });
  });
  // Sorting by time is what makes "consecutive" mean anything; the emotion name
  // breaks ties so two exports of the same check-ins in different row orders
  // produce the same answer.
  out.sort(function (a, b) {
    return (a.at - b.at) || (a.emotion || "").localeCompare(b.emotion || "");
  });
  return out;
}

/* ===========================================================================
   TIMELINE — the O(1) window index
   ---------------------------------------------------------------------------
   The rules slide a window along the log and at every position ask "how many
   of positions i..j were bad?" and "how many calendar days does i..j touch?".
   Answering either with a loop is O(w) per position and O(n·w) overall.

   Both are prefix sums instead, computed once: the range answer is a single
   subtraction. Distinct days works because the log is sorted, so dates never
   go backwards and "distinct days in i..j" is 1 + (times the date changed).
   ======================================================================== */

function Timeline(checkIns) {
  this.checkIns = checkIns || [];
  var count = this.checkIns.length;
  this.times = new Array(count);
  this._neg = new Array(count + 1);
  this._dayChange = new Array(count + 1);
  this._neg[0] = 0;
  this._dayChange[0] = 0;

  var previousDay = null;
  for (var i = 0; i < count; i++) {
    var checkIn = this.checkIns[i];
    this.times[i] = checkIn.at;
    this._neg[i + 1] = this._neg[i] + (isNegative(checkIn.emotion) ? 1 : 0);
    this._dayChange[i + 1] = this._dayChange[i] +
      (previousDay !== null && checkIn.day !== previousDay ? 1 : 0);
    previousDay = checkIn.day;
  }
}

Timeline.prototype.length = function () { return this.checkIns.length; };
Timeline.prototype.at = function (index) { return this.checkIns[index]; };

Timeline.prototype.negativeCount = function (start, end) {
  return this._neg[end + 1] - this._neg[start];
};

Timeline.prototype.negativeShare = function (start, end) {
  var width = end - start + 1;
  return width <= 0 ? 0 : this.negativeCount(start, end) / width;
};

Timeline.prototype.distinctDays = function (start, end) {
  if (end < start) return 0;
  return 1 + (this._dayChange[end + 1] - this._dayChange[start + 1]);
};

Timeline.prototype.span = function (start, end) {
  return this.times[end] - this.times[start];
};

Timeline.prototype.spanDays = function (start, end) {
  return daysBetween(this.times[start], this.times[end]);
};

Timeline.prototype.slice = function (start, end) {
  return this.checkIns.slice(start, end + 1);
};

/* Every time-bounded window, as [start, end] index pairs.

   The classic two-pointer scan. The insight that makes it O(n): as `start`
   moves right the furthest valid `end` can only move right as well, never
   back — so `end` is never rewound and each pointer advances at most n times.

   `lo`/`hi` exist because several rules work on one run carved out of the log,
   and slicing that run into its own array would throw away the prefix sums. */
Timeline.prototype.maximalWindows = function (maxSpanMs, lo, hi) {
  if (lo === undefined) lo = 0;
  if (hi === undefined) hi = this.times.length - 1;
  var windows = [], end = lo;
  for (var start = lo; start <= hi; start++) {
    if (end < start) end = start;
    while (end + 1 <= hi && this.times[end + 1] - this.times[start] <= maxSpanMs) end++;
    windows.push([start, end]);
  }
  return windows;
};

/* ===========================================================================
   THE FOUR DETECTION RULES
   ---------------------------------------------------------------------------
   Each returns a list of "streak" objects — despite the name, one finding —
   with a type, a channel, its extent in time and the check-ins behind it, so
   everything above can treat all four identically.

   Two of the rules need the same thing first: the log broken into unbroken runs
   of bad check-ins with their qualifying windows worked out. `scanNegativeRuns`
   computes it once and hands it to both.
   ======================================================================== */

var TRIGGER_CHANNELS = {
  negative: "alert",
  same_day_cluster: "watch",   // promoted to alert by the rule when big enough
  density: "watch",
  repeated: "positive"
};

/* Windows inside start..end satisfying every streak constraint at once. Each
   constraint rules out a different false positive: `streakLength` a
   coincidence, `streakWindowDays` four bad days spread over a term,
   `minDistinctDays` one rough morning read as a rough week.

   A window fully contained in another is dropped: a contained window can never
   touch more calendar days than its parent, so it can only be a weaker
   description of the same episode. */
function qualifyingWindows(timeline, start, end) {
  var out = [];
  var maxSpan = DETECT.streakWindowDays * DAY_MS;
  var furthestEnd = start - 1;

  timeline.maximalWindows(maxSpan, start, end).forEach(function (w) {
    var windowStart = w[0], windowEnd = w[1];
    var longEnough = windowEnd - windowStart + 1 >= DETECT.streakLength;
    if (!longEnough || windowEnd <= furthestEnd) return;
    if (timeline.distinctDays(windowStart, windowEnd) >= DETECT.minDistinctDays) {
      out.push([windowStart, windowEnd]);
    }
    // Advances even when the day-spread check rejected the window: containment
    // is about extent, not about whether the window qualified.
    furthestEnd = windowEnd;
  });
  return out;
}

/* The log broken into unbroken runs of bad check-ins. A run ends at the first
   check-in that is not negative — including an unreadable one, which is the
   whole reason unreadable check-ins are kept rather than dropped. */
function scanNegativeRuns(timeline) {
  var runs = [], runStart = null;

  function close(runEnd) {
    runs.push({ start: runStart, end: runEnd,
                windows: qualifyingWindows(timeline, runStart, runEnd) });
  }

  for (var i = 0; i < timeline.length(); i++) {
    if (isNegative(timeline.at(i).emotion)) {
      if (runStart === null) runStart = i;
      continue;
    }
    if (runStart !== null) { close(i - 1); runStart = null; }
  }
  if (runStart !== null) close(timeline.length() - 1);
  return runs;
}

function makeStreak(timeline, start, end, type, channel) {
  var emotions = timeline.slice(start, end).map(function (c) { return c.emotion; });
  var streak = {
    type: type,
    channel: channel || TRIGGER_CHANNELS[type],
    valence: describeValenceMix(emotions),
    startAt: timeline.times[start],
    endAt: timeline.times[end],
    length: end - start + 1,
    distinctDays: timeline.distinctDays(start, end),
    spanDays: timeline.spanDays(start, end),
    emotions: emotions
  };
  // A repeated streak is by definition all one emotion, so naming it saves
  // every reader from re-deriving it.
  if (type === "repeated") streak.emotion = emotions[0];
  return streak;
}

/* RULE 1 — negative streak (alert). Four or more bad check-ins in a row.

   The reported extent is the WHOLE unbroken run, even when it is longer than
   the window. The window constraint decides whether a run counts; it does not
   chop what gets reported into week-sized pieces. Emitting one record per
   qualifying window split single continuous runs across up to 26 records each,
   which both misdescribed the episode and inflated priority, because the
   summary counts records. What tripped the rule is kept as `triggerLength`. */
function findNegativeStreaks(timeline, runs) {
  var out = [];
  runs.forEach(function (run) {
    if (!run.windows.length) return;
    var streak = makeStreak(timeline, run.start, run.end, "negative");

    // The tightest window = the one packed into the least time, which is the
    // most persuasive evidence the run contained a real episode.
    var tightest = run.windows[0];
    var best = timeline.span(tightest[0], tightest[1]);
    run.windows.forEach(function (w) {
      var span = timeline.span(w[0], w[1]);
      if (span < best) { best = span; tightest = w; }
    });
    streak.triggerLength = tightest[1] - tightest[0] + 1;
    streak.triggerSpanDays = timeline.spanDays(tightest[0], tightest[1]);
    out.push(streak);
  });
  return out;
}

/* RULE 2 — same-day cluster (watch, or alert when big enough).

   Only for runs that raised no alert of their own: a run that already produced
   a negative streak is skipped, because reporting both would tell a teacher
   about the same episode twice under two names. */
function findSameDayClusters(timeline, runs) {
  if (!DETECT.enableSameDayCluster) return [];
  var out = [];

  runs.forEach(function (run) {
    var length = run.end - run.start + 1;
    if (length < DETECT.sameDayClusterLength || run.windows.length) return;

    // The timeline is sorted, so a day's entries are contiguous and first-seen
    // order is chronological. That is what makes it correct to hand makeStreak
    // a range below rather than a list of indices.
    var order = [], byDay = {};
    for (var i = run.start; i <= run.end; i++) {
      var key = timeline.at(i).day;
      if (!byDay[key]) { byDay[key] = []; order.push(key); }
      byDay[key].push(i);
    }
    order.forEach(function (key) {
      var indices = byDay[key];
      if (indices.length < DETECT.sameDayClusterLength) return;
      var channel = indices.length >= DETECT.sameDayAlertLength ? "alert" : "watch";
      out.push(makeStreak(timeline, indices[0], indices[indices.length - 1],
                          "same_day_cluster", channel));
    });
  });
  return out;
}

/* RULE 3 — repeated emotion (positive). The same positive emotion 4+ times
   running. Kept in its own channel rather than suppressed: a good stretch is
   worth naming to a student, and it is the baseline a future flag is compared
   against. */
function findRepeatedStreaks(timeline) {
  var windows = [], runStart = null;

  function close(runEnd) {
    if (runStart === null) return;
    var emotion = timeline.at(runStart).emotion;
    if (!emotion || !DETECT.repeatedValences[EMOTION_VALENCE[emotion]]) return;
    qualifyingWindows(timeline, runStart, runEnd).forEach(function (w) { windows.push(w); });
  }

  for (var i = 0; i < timeline.length(); i++) {
    var emotion = timeline.at(i).emotion;
    if (emotion === null) {
      // An unreadable check-in breaks the run, exactly as it breaks a negative
      // streak, and for the same reason.
      if (runStart !== null) close(i - 1);
      runStart = null;
      continue;
    }
    if (runStart !== null && emotion === timeline.at(runStart).emotion) continue;
    if (runStart !== null) close(i - 1);
    runStart = i;
  }
  if (runStart !== null) close(timeline.length() - 1);

  return windows.map(function (w) {
    return makeStreak(timeline, w[0], w[1], "repeated");
  });
}

/* RULE 4 — density (watch). A week that is mostly bad but never four in a row.

   The streak rule is precise and therefore brittle: one neutral check-in on the
   Wednesday breaks a run and the whole fortnight goes unreported. This is the
   safety net, in the watch channel because a softer signal deserves softer
   treatment.

   Two subtleties, both easy to misread:

   Overlapping windows are merged ONLY where the union still qualifies. Two
   windows that each reach 70% can union into something that does not, and a
   record must never claim a density it does not have — so when the union would
   fall short, the second window is appended as its own span and the output
   contains two overlapping records. Each honestly describes a period that
   really was dense.

   A period is dropped only when it sits ENTIRELY inside an alert. A run of
   consecutive bad check-ins is by definition also dense, so without this every
   alert would be shadowed by a duplicate watch record — but a density period
   that merely straddles an alert survives, because it describes something
   wider than the alert did. */
function findDensityStreaks(timeline, covered) {
  if (!DETECT.enableDensity) return [];
  var minimum = DETECT.densityMinNegativeShare;
  var spans = [];

  timeline.maximalWindows(DETECT.densityWindowDays * DAY_MS).forEach(function (w) {
    var start = w[0], end = w[1];
    if (end - start + 1 < DETECT.densityMinEntries) return;
    if (timeline.negativeShare(start, end) < minimum) return;
    if (timeline.distinctDays(start, end) < DETECT.minDistinctDays) return;

    if (spans.length && start <= spans[spans.length - 1][1]) {
      var mergedEnd = Math.max(spans[spans.length - 1][1], end);
      if (timeline.negativeShare(spans[spans.length - 1][0], mergedEnd) >= minimum) {
        spans[spans.length - 1][1] = mergedEnd;
        return;
      }
    }
    spans.push([start, end]);
  });

  var out = [];
  spans.forEach(function (span) {
    var windowStart = timeline.times[span[0]], windowEnd = timeline.times[span[1]];
    var inside = covered.some(function (extent) {
      return extent[0] <= windowStart && windowEnd <= extent[1];
    });
    if (inside) return;
    var streak = makeStreak(timeline, span[0], span[1], "density");
    // Recomputed for exactly what is being reported: a merged extent can differ
    // from the individual windows that qualified.
    streak.negativeShare = round(timeline.negativeShare(span[0], span[1]), 3);
    out.push(streak);
  });
  return out;
}

/* All four, in the one order that matters: the alert rules run first so the
   density rule can be told what they already cover and stay quiet about it. */
function findAllStreaks(timeline) {
  var runs = scanNegativeRuns(timeline);
  var negative = findNegativeStreaks(timeline, runs);
  var sameDay = findSameDayClusters(timeline, runs);

  var covered = negative.concat(sameDay)
    .filter(function (s) { return s.channel === "alert"; })
    .map(function (s) { return [s.startAt, s.endAt]; });

  return negative
    .concat(sameDay)
    .concat(findRepeatedStreaks(timeline))
    .concat(findDensityStreaks(timeline, covered));
}

/* ===========================================================================
   SUMMARIZE — one student's findings reduced to one sortable verdict
   ---------------------------------------------------------------------------
   A student can trip four rules at once. A teacher with 23 flagged students
   cannot read four findings each, so everyone gets one channel and one
   priority, and every screen sorts on those instead of re-deriving them.

   THE RULE THAT MATTERS MOST: only alert-channel findings can raise priority.
   A student whose entire case is watch-channel stays at "watch" however many
   soft signals they trip. That is what makes it safe to keep adding gentler
   rules — a new soft signal can add context but never inflate the queue.
   ======================================================================== */

function summarize(streaks, recent) {
  var alerts = streaks.filter(function (s) { return s.channel === "alert"; });
  var watch = streaks.filter(function (s) { return s.channel === "watch"; });

  var negativeEntries = recent.filter(function (c) { return isNegative(c.emotion); }).length;

  var reasons = {};
  watch.forEach(function (s) { reasons[s.type] = true; });

  var alertDays = {};
  alerts.forEach(function (s) {
    alertDays[dayKey(s.startAt)] = true;
    alertDays[dayKey(s.endAt)] = true;
  });

  return {
    channel: alerts.length ? "alert" : (watch.length ? "watch" : "positive"),
    priority: priorityFor(alerts, watch),
    streakCount: streaks.length,
    alertStreakCount: alerts.length,
    watchStreakCount: watch.length,
    watchReasons: Object.keys(reasons).sort(),
    positiveStreakCount: streaks.length - alerts.length - watch.length,
    longestAlertStreak: alerts.reduce(function (m, s) { return Math.max(m, s.length); }, 0),
    alertDays: Object.keys(alertDays).length,
    recentEntryCount: recent.length,
    recentNegativeShare: recent.length ? round(negativeEntries / recent.length, 3) : 0
  };
}

/* How urgent, from the alert-channel findings alone. "high" means one of two
   things, and both are statements about the episode rather than the count:

     * two or more SEPARATE alert episodes — distress that returns after a
       recovery is the pattern classroom strategies tend not to reach;
     * one long, deep episode that survived several overnight resets. */
function priorityFor(alerts, watch) {
  if (!alerts.length) return watch.length ? "watch" : "info";
  var separateEpisodes = alerts.length >= 2;
  var sustained = alerts.some(function (s) {
    return s.length >= DETECT.highPriorityMinLength &&
           s.distinctDays >= DETECT.highPriorityMinDays;
  });
  return (separateEpisodes || sustained) ? "high" : "medium";
}

/* ===========================================================================
   STATS — the binomial test both correlation rules share
   ---------------------------------------------------------------------------
   The two correlation rules ask the SAME question about different groupings:

     "This student's check-ins go badly 30% of the time overall. Around Reading,
      8 of their 12 went badly — that is 67%. Is that a real pattern, or is it
      what you would expect to see sometimes by chance?"

   Three gates, each rejecting a different kind of false alarm:
     1. EXPOSURE  enough check-ins near Reading for a percentage to mean
                  anything (3 out of 3 is 100% and is also nothing);
     2. EFFECT    a gap big enough to act on;
     3. CHANCE    and unlikely enough not to be luck.
   ======================================================================== */

/* log(n!) via Lanczos. Python has math.comb and exact integer arithmetic;
   JavaScript's doubles overflow on C(n, k) well before the exposures this
   engine sees stop being plausible, so the terms are summed in log space
   instead. Accurate to ~1e-13 over the range that matters here. */
var LANCZOS = [
  676.5203681218851, -1259.1392167224028, 771.32342877765313,
  -176.61502916214059, 12.507343278686905, -0.13857109526572012,
  9.9843695780195716e-6, 1.5056327351493116e-7
];

function logGamma(x) {
  if (x < 0.5) {
    // Reflection: Γ(x)Γ(1-x) = π / sin(πx)
    return Math.log(Math.PI / Math.sin(Math.PI * x)) - logGamma(1 - x);
  }
  x -= 1;
  var a = 0.99999999999980993, t = x + 7.5;
  for (var i = 0; i < LANCZOS.length; i++) a += LANCZOS[i] / (x + i + 1);
  return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(a);
}

function logChoose(n, k) {
  return logGamma(n + 1) - logGamma(k + 1) - logGamma(n - k + 1);
}

/* P(X >= k) for X ~ Binomial(n, p), summed exactly term by term.

   In this project's terms: if every check-in independently had a `p` chance of
   going badly, what is the chance that at least k of n did? Exact rather than
   approximated, deliberately — n here is one student's check-ins near one
   activity, typically 5 to 40, which is precisely the range where the normal
   approximation is worst and small enough that exact terms cost nothing. */
function binomialTail(k, n, p) {
  if (k <= 0) return 1;
  if (k > n) return 0;
  if (p <= 0) return 0;
  if (p >= 1) return 1;
  var total = 0;
  for (var i = k; i <= n; i++) {
    total += Math.exp(logChoose(n, i) + i * Math.log(p) + (n - i) * Math.log(1 - p));
  }
  return Math.min(1, total);
}

/* The student's own overall badness rate, or null if it cannot be used.

   A baseline of exactly 0 or exactly 1 breaks the comparison, and not just
   numerically — it is meaningless. If a student's check-ins never go badly
   there is nothing to be above; if they always do, there is nothing to stand
   out from. */
function usableBaseline(hits, exposure) {
  if (!exposure || !hits) return null;
  var baseline = hits / exposure;
  return (baseline > 0 && baseline < 1) ? baseline : null;
}

/* Measure one group against the baseline, or null if it does not qualify.

   The three gates are applied CHEAPEST FIRST, and that ordering is
   load-bearing rather than a micro-optimisation: two integer comparisons and a
   subtraction reject the great majority of groups, so the O(n) binomial sum
   only ever runs for the handful still in the running. This is called for every
   activity of every flagged student. */
function evaluateGroup(hits, exposure, baseline, minHits, minExposure, minExcess, maxP) {
  if (hits < minHits || exposure < minExposure) return null;
  var rate = hits / exposure;
  var excess = rate - baseline;
  if (excess < minExcess) return null;
  var p = binomialTail(hits, exposure, baseline);
  if (p >= maxP) return null;
  return { hits: hits, exposure: exposure, rate: rate, usualRate: baseline,
           excess: excess, lift: rate / baseline, p: p };
}

/* ===========================================================================
   CONTEXT — the joins and correlations, before any advice
   ---------------------------------------------------------------------------
   The fifteen student rules below are each a short `if` plus a sentence. They
   read that way only because every hard question has been answered by the time
   they run: which activity this student comes apart around, which part of the
   day, whether they are getting better or worse, what the teacher already
   wrote down, which calm tools they have tried.

   This section answers those questions.
   ======================================================================== */

/* Positions of the check-ins that produced the flag.

   Positions rather than the check-ins themselves, because every correlation
   below walks the whole log asking "was this one of the flagged ones?" and a
   set of indices answers that in O(1).

   Positive-channel findings are excluded: a run of "happy" is not evidence
   about what upsets a student, and including it would dilute every rate. */
function flaggedPositions(timeline, streaks) {
  var flagged = {};
  streaks.forEach(function (streak) {
    if (streak.channel !== "alert" && streak.channel !== "watch") return;
    for (var i = 0; i < timeline.length(); i++) {
      var at = timeline.times[i];
      if (at < streak.startAt) continue;
      if (at > streak.endAt) break;
      if (isNegative(timeline.at(i).emotion)) flagged[i] = true;
    }
  });
  return flagged;
}

/* Task rows sorted and ready for nearest-task lookup. The app writes
   {emoji, task, time, iso}; the corpus writes {ts, task, status}. Both land
   here as {at, task, status}. */
function indexTasks(student) {
  var rows = [];
  (student.taskLog || student.task_log || []).forEach(function (raw) {
    if (!raw || typeof raw !== "object") return;
    var at = parseTimestamp(raw);
    var label = raw.task;
    if (!at || typeof label !== "string" || !label) return;
    rows.push({ at: at, task: label, status: raw.status });
  });
  rows.sort(function (a, b) { return a.at - b.at; });
  return rows;
}

/* The task row closest to `at`, if one falls inside `window`.

   Binary search finds where `at` would sit in the sorted task list; the nearest
   row is then necessarily one of the two neighbours, so only two candidates
   ever need checking. O(log t) per lookup against O(t) for a scan — and this
   runs once per check-in per student. */
function nearestTask(rows, at, windowMs) {
  if (!rows.length) return null;
  var lo = 0, hi = rows.length;
  while (lo < hi) {
    var mid = (lo + hi) >> 1;
    if (rows[mid].at < at) lo = mid + 1; else hi = mid;
  }
  var best = null, bestGap = Infinity;
  [lo - 1, lo].forEach(function (candidate) {
    if (candidate < 0 || candidate >= rows.length) return;
    var gap = Math.abs(rows[candidate].at - at);
    if (gap <= windowMs && gap < bestGap) { bestGap = gap; best = rows[candidate]; }
  });
  return best;
}

/* Which activity this student's check-ins go wrong around.

   For every activity two counts are gathered — exposure (how many check-ins
   landed near it) and hits (how many of THOSE were flagged) — and the rate is
   compared against the student's overall flagged rate. An activity is named for
   going wrong more often than this student's day goes wrong, never for merely
   being frequent. See RECOMMEND.minTaskExcess. */
function correlateTasks(timeline, flaggedAt, student) {
  var rows = indexTasks(student);
  var flaggedCount = Object.keys(flaggedAt).length;
  if (!rows.length || !flaggedCount) return [];

  var windowMs = RECOMMEND.taskWindowMinutes * 60000;
  var exposure = {}, hits = {}, skipped = {}, matched = 0;

  for (var i = 0; i < timeline.length(); i++) {
    var row = nearestTask(rows, timeline.times[i], windowMs);
    if (!row) continue;
    matched++;
    exposure[row.task] = (exposure[row.task] || 0) + 1;
    if (flaggedAt[i]) {
      hits[row.task] = (hits[row.task] || 0) + 1;
      if (row.status === "skipped") skipped[row.task] = (skipped[row.task] || 0) + 1;
    }
  }

  var totalHits = 0;
  Object.keys(hits).forEach(function (task) { totalHits += hits[task]; });
  var baseline = usableBaseline(totalHits, matched);
  if (baseline === null) return [];

  var clusters = [];
  Object.keys(hits).forEach(function (task) {
    var comparison = evaluateGroup(
      hits[task], exposure[task], baseline,
      RECOMMEND.minTaskHits, RECOMMEND.minTaskExposure,
      RECOMMEND.minTaskExcess, RECOMMEND.taskMaxP);
    if (!comparison) return;
    clusters.push({
      task: task,
      hits: comparison.hits,
      exposure: comparison.exposure,
      ofFlagged: totalHits,
      rate: round(comparison.rate, 3),
      usualRate: round(comparison.usualRate, 3),
      lift: round(comparison.lift, 2),
      p: round(comparison.p, 4),
      skipped: skipped[task] || 0
    });
  });
  // Strength of association first: 6 hits well above baseline is a better lead
  // than 10 that are merely busy.
  clusters.sort(function (a, b) { return (a.p - b.p) || (b.hits - a.hits); });
  return clusters;
}

/* The part of the day this student's check-ins go wrong in, if any.

   Exactly the same test as the activity rule, on exactly the same grounds.
   Measuring the SHARE of flags falling in a band would be measuring the
   timetable: 83% of every check-in in the sample corpus is logged before 11am,
   so "most of the flags are in the morning" says nothing about the student. A
   fixed lift threshold is worse — at an 83% baseline the largest lift possible
   is 1.21, so `lift >= 1.5` makes "mornings are hard for this child"
   arithmetically unreportable. */
function correlateTime(timeline, flaggedAt) {
  if (Object.keys(flaggedAt).length < RECOMMEND.timeMinHits) return null;

  var exposure = {}, hits = {};
  for (var i = 0; i < timeline.length(); i++) {
    var bucket = bucketForHour(timeline.times[i].getHours());
    if (!bucket) continue;
    exposure[bucket] = (exposure[bucket] || 0) + 1;
    if (flaggedAt[i]) hits[bucket] = (hits[bucket] || 0) + 1;
  }

  var totalHits = 0, totalExposure = 0;
  Object.keys(hits).forEach(function (b) { totalHits += hits[b]; });
  Object.keys(exposure).forEach(function (b) { totalExposure += exposure[b]; });
  var baseline = usableBaseline(totalHits, totalExposure);
  if (baseline === null) return null;

  var best = null;
  Object.keys(hits).forEach(function (bucket) {
    var comparison = evaluateGroup(
      hits[bucket], exposure[bucket], baseline,
      RECOMMEND.timeMinHits, RECOMMEND.minTaskExposure,
      RECOMMEND.timeMinExcess, RECOMMEND.timeMaxP);
    if (!comparison) return;
    if (best && comparison.p >= best.p) return;
    best = {
      bucket: bucket,
      phrase: TIME_PHRASES[bucket],
      hits: comparison.hits,
      exposure: comparison.exposure,
      rate: round(comparison.rate, 3),
      usualRate: round(comparison.usualRate, 3),
      excessPoints: roundHalfEven(comparison.excess * 100),
      lift: round(comparison.lift, 2),
      p: round(comparison.p, 4)
    };
  });
  return best;
}

/* Whether the recent window is getting better or worse.

   Split at the MIDPOINT IN TIME, not at the midpoint of the entry count. That
   matters: a student who happened to check in more often during one half would
   otherwise read as a trend when nothing about their week changed. Both halves'
   rates are returned so the view can show the working rather than asking a
   teacher to trust the word "worsening". */
function trajectory(timeline) {
  var unknown = { direction: "unknown", firstHalf: 0, secondHalf: 0, delta: 0 };
  if (timeline.length() < 6) return unknown;   // too few points for two halves

  var start = timeline.times[0], end = timeline.times[timeline.length() - 1];
  var midpoint = new Date(start.getTime() + (end - start) / 2);

  function share(checkIns) {
    if (!checkIns.length) return null;
    var bad = checkIns.filter(function (c) { return isNegative(c.emotion); }).length;
    return bad / checkIns.length;
  }

  var first = share(timeline.checkIns.filter(function (c) { return c.at < midpoint; }));
  var second = share(timeline.checkIns.filter(function (c) { return c.at >= midpoint; }));
  if (first === null || second === null) return unknown;

  var delta = second - first;
  return {
    direction: delta >= 0.15 ? "worsening" : (delta <= -0.15 ? "improving" : "steady"),
    firstHalf: round(first, 3),
    secondHalf: round(second, 3),
    delta: round(delta, 3)
  };
}

/* ---------------------------------------------------------------------------
   Note mining. The app writes notes from templates, so three of them can be
   parsed for the task the teacher THEMSELVES named as the trigger. That is a
   human judgement about causation, worth more than any correlation in this
   file — so the rules use it to corroborate a computed finding rather than to
   replace or average with it.
   ------------------------------------------------------------------------ */

var NOTE_PATTERNS = [
  ["namedTrigger", /struggled with the transition to (.+?);/i],
  ["calmWorked", /used the calm corner after (.+?) and returned/i],
  ["neededPrompt", /needed an extra prompt during (.+?) but/i]
];

var NOTE_FLAGS = [
  ["tired_after_lunch", /seemed tired after lunch/i],
  ["token_goal_met", /reached the goal today/i],
  ["aac_progress", /practiced two new AAC phrases/i],
  ["parent_contact", /parent check-in/i],
  ["good_morning", /had a great morning/i]
];

var MAX_RECENT_NOTES = 5;

function readNotes(notes, cutoff) {
  var signals = { namedTrigger: [], calmWorked: [], neededPrompt: [],
                  flags: {}, recent: [], count: 0 };

  (notes || []).forEach(function (note) {
    if (!note) return;
    // `created_at` when the row carries one (server rows, and sample data);
    // otherwise the app's own {date, time} pair.
    var created = note.created_at ? parseISO(note.created_at) : parseTimestamp(note);
    if (!created || created < cutoff) return;   // older than the window these flags describe

    var text = note.text || "";
    signals.count++;
    signals.recent.push({ at: created, text: text });

    NOTE_PATTERNS.forEach(function (pair) {
      var found = pair[1].exec(text);
      if (found) signals[pair[0]].push({ task: found[1].trim(), at: created });
    });
    NOTE_FLAGS.forEach(function (pair) {
      if (pair[1].test(text)) signals.flags[pair[0]] = true;
    });
  });

  signals.recent.sort(function (a, b) { return b.at - a.at; });
  signals.recent = signals.recent.slice(0, MAX_RECENT_NOTES);
  return signals;
}

/* ---------------------------------------------------------------------------
   Calm-corner tools and the words the rules use about them. Content, not
   logic: a teacher or speech therapist may well want to reword these, and none
   of that should require reading a statistics function.
   ------------------------------------------------------------------------ */

var CALM_TOOLS = {
  breathing: { label: "Deep Breathing", emoji: "🌬️", aliases: ["counting"] },
  bubbles:   { label: "Pop Bubbles", emoji: "🫧", aliases: [] },
  fidget:    { label: "Fidget Spinner", emoji: "🌀", aliases: ["squeeze-ball", "stretch"] },
  ground:    { label: "5-4-3-2-1", emoji: "🖐️", aliases: [] },
  rain:      { label: "Watch Rain", emoji: "🌧️", aliases: ["music", "weighted-blanket"] },
  stars:     { label: "Starry Night", emoji: "🌌", aliases: ["dim-lights"] }
};

var CALM_ALIASES = {};
Object.keys(CALM_TOOLS).forEach(function (tool) {
  CALM_TOOLS[tool].aliases.forEach(function (alias) { CALM_ALIASES[alias] = tool; });
});

var CALM_TOOL_COUNT = Object.keys(CALM_TOOLS).length;

function canonicalCalm(raw) {
  var key = String(raw).trim().toLowerCase();
  if (CALM_ALIASES[key]) key = CALM_ALIASES[key];
  return CALM_TOOLS[key] ? key : null;
}

var EMOTION_TOOLS = {
  anxious: ["breathing", "ground", "rain"],
  scared: ["ground", "breathing", "stars"],
  overwhelmed: ["stars", "rain", "breathing"],
  angry: ["breathing", "fidget", "bubbles"],
  frustrated: ["fidget", "bubbles", "breathing"],
  sad: ["rain", "bubbles", "stars"],
  confused: ["ground", "breathing", "fidget"]
};

var EMOTION_READS = {
  anxious: "anticipating something",
  scared: "not feeling safe",
  overwhelmed: "too much input at once",
  angry: "a demand landing harder than it can be met",
  frustrated: "a task pitched above where the student is",
  sad: "something carried in from outside the moment",
  confused: "an instruction that did not land"
};

var EMOTION_CAVEATS = {
  sad: "Ask before you offer, though — with sadness the conversation does more than the tool does.",
  angry: "Offer it early; once anger is at its peak, a screen is another demand rather than a relief."
};

var STORY_CUES = [
  ["fire drill", ["fire drill"], ["scared", "anxious", "overwhelmed"]],
  ["substitute", ["substitute"], ["anxious", "overwhelmed"]],
  ["assembly", ["assembly", "gym", "music"], ["overwhelmed", "anxious"]],
  ["recess", ["recess"], ["anxious", "sad", "frustrated"]],
  ["field trip", ["field trip", "trip"], ["anxious", "overwhelmed"]],
  ["bus", ["bus", "arrival", "dismissal"], ["anxious", "scared"]],
  ["friend", ["recess", "lunch", "circle time"], ["sad", "anxious"]],
  ["picture day", ["picture"], ["anxious", "scared"]],
  ["haircut", [], ["scared", "anxious"]],
  ["dentist", [], ["scared", "anxious"]]
];

/* The social stories one classroom owns, pre-matched against the cue list.
   Built once per classroom rather than once per student: lower-casing every
   title and re-running ten substring searches per student answers a question
   whose answer could not have changed. */
function StoryIndex(stories) {
  this.matches = [];
  var self = this;
  (stories || []).forEach(function (story) {
    var title = String(story.title || "").toLowerCase();
    STORY_CUES.forEach(function (cue) {
      if (title.indexOf(cue[0]) !== -1) self.matches.push([story, cue[1], cue[2]]);
    });
  });
}

/* A story that fits, and what it was matched on.

   Activity match is tried across EVERY story before emotion is considered at
   all. "Read this before the activity that sets them off" is advice; "read a
   story about being scared to a scared child" is barely more than a
   restatement. The emotion fallback matches only the dominant emotion —
   matching anything in the mix fired for 1,691 of 2,842 students on the sample
   corpus, the definition of a recommendation nobody reads. */
StoryIndex.prototype.pick = function (task, dominantEmotion) {
  var taskKey = String(task || "").toLowerCase();
  var i;
  if (taskKey) {
    for (i = 0; i < this.matches.length; i++) {
      var activities = this.matches[i][1];
      for (var j = 0; j < activities.length; j++) {
        if (taskKey.indexOf(activities[j]) !== -1) return [this.matches[i][0], "task"];
      }
    }
  }
  if (dominantEmotion) {
    for (i = 0; i < this.matches.length; i++) {
      if (this.matches[i][2].indexOf(dominantEmotion) !== -1) return [this.matches[i][0], "emotion"];
    }
  }
  return [null, null];
};

/* ---------------------------------------------------------------------------
   The context object the rules read.
   ------------------------------------------------------------------------ */

//: How many recent check-ins the card's strip draws. Enough to show the shape
//: of a month without turning the row into a hairline.
var STRIP_LENGTH = 28;

function buildContext(options) {
  var timeline = options.timeline;
  var streaks = options.streaks;
  var flaggedAt = flaggedPositions(timeline, streaks);

  var emotionMix = {};
  Object.keys(flaggedAt).forEach(function (index) {
    var emotion = timeline.at(+index).emotion;
    if (emotion) emotionMix[emotion] = (emotionMix[emotion] || 0) + 1;
  });

  // The most common flagged emotion. A tie goes to whichever was seen first,
  // which is what Python's `Counter.most_common` does and what keeps this
  // engine's answer identical to the pipeline's — object keys hold insertion
  // order, so iterating them unsorted is the match.
  var dominant = null, bestCount = 0;
  Object.keys(emotionMix).forEach(function (emotion) {
    if (emotionMix[emotion] > bestCount) { bestCount = emotionMix[emotion]; dominant = emotion; }
  });

  var calmUsed = {};
  (options.record.calmDone || options.record.calm_done || []).forEach(function (raw) {
    var tool = canonicalCalm(raw);
    if (tool) calmUsed[tool] = true;
  });

  var context = {
    studentId: options.studentId,
    displayName: options.displayName,
    summary: options.summary,
    channel: options.summary.channel,
    priority: options.summary.priority,
    streaks: streaks,
    alertStreaks: streaks.filter(function (s) { return s.channel === "alert"; }),
    tasks: correlateTasks(timeline, flaggedAt, options.record),
    time: correlateTime(timeline, flaggedAt),
    trajectory: trajectory(timeline),
    emotionMix: emotionMix,
    dominantEmotion: dominant,
    notes: options.notes,
    noteCount: options.notes.count,
    calmUsed: calmUsed,
    stories: options.stories,
    aacCount: options.record.aacCount || 0,
    emoCount: options.record.emoCount || 0,
    tokens: options.record.tokens,
    tokenGoal: options.tokenGoal,
    tokenReward: options.tokenReward || "the class reward",
    // The check-in strip the card draws. Kept to the tail of the window: enough
    // to show the shape of a month without turning the row into a hairline.
    strip: timeline.checkIns.slice(-STRIP_LENGTH).map(function (c) {
      return { at: c.at, emotion: c.emotion, valence: valenceOf(c.emotion) };
    })
  };
  context.topTask = context.tasks.length ? context.tasks[0] : null;
  // Counted over the emotion mix rather than over the flagged positions, so the
  // "3 of 7 flagged check-ins are 'angry'" evidence line always has a
  // denominator its numerator actually came out of.
  context.flaggedCheckInCount = Object.keys(emotionMix)
    .reduce(function (total, emotion) { return total + emotionMix[emotion]; }, 0);
  return context;
}

/* ===========================================================================
   THE STUDENT RULES
   ---------------------------------------------------------------------------
   Every rule is one small function: (context) -> recommendation | null. Rules
   never look at each other, never touch storage, never mutate the context.
   Adding one means writing a function and adding its name to STUDENT_RULES.

   What every recommendation must carry:
       headline  what to do, in a few words
       detail    why, in plain language a teacher can disagree with
       evidence  the specific numbers that fired the rule
       rank      where it sits in the reading order (lower = shown first)

   The evidence field is the non-negotiable one. Advice a teacher cannot audit
   is advice a teacher eventually learns to skip.

   Ranks are spaced in tens so a new rule can slot between two without
   renumbering: 5 celebrate, 10 escalate, 20 antecedent, 30 regulation,
   35 communication, 40 story, 45 motivation, 48 monitor, 50 document.
   ======================================================================== */

function recommendation(id, category, rank, headline, detail, evidence) {
  return { id: id, category: category, rank: rank, headline: headline,
           detail: detail, evidence: evidence };
}

/* Round half to EVEN, not half up.

   This looks like fussiness and is not. Python's `round()` and its `"%"`
   formatting both round a halfway value to the nearest even integer, while
   JavaScript's `Math.round` rounds halves up. So a student whose check-ins go
   badly 0.405 of the time is "40% negative" in the pipeline and would be "41%
   negative" here — the same child, described with two different numbers by two
   tools a teacher is told are the same analysis. Caught by tools/verify.html,
   which is the kind of difference it exists to catch. */
function roundHalfEven(value) {
  var floor = Math.floor(value);
  var fraction = value - floor;
  if (fraction > 0.5) return floor + 1;
  if (fraction < 0.5) return floor;
  return floor % 2 === 0 ? floor : floor + 1;
}

function pct(value) { return roundHalfEven(value * 100) + "%"; }

function shortDate(date) {
  return date.getFullYear() + "-" +
         String(date.getMonth() + 1).padStart(2, "0") + "-" +
         String(date.getDate()).padStart(2, "0");
}

function shortTime(date) {
  return String(date.getHours()).padStart(2, "0") + ":" +
         String(date.getMinutes()).padStart(2, "0");
}

/* --- escalation: this is bigger than a classroom strategy ---------------- */

function ruleEscalateRepeatEpisodes(context) {
  var alerts = context.alertStreaks;
  if (alerts.length < 2) return null;

  var spans = alerts.slice(0, 3).map(function (s) {
    return shortDate(s.startAt) + " to " + shortDate(s.endAt);
  }).join(", ");

  return recommendation(
    "escalate_repeat_episodes", "escalate", 10,
    "Bring this one to your support team",
    context.displayName + " has " + alerts.length + " separate alert episodes in " +
    "the last 30 days, not one long stretch. Distress that returns after a " +
    "recovery is the pattern classroom-level strategies tend not to reach on their own.",
    [alerts.length + " distinct alert episodes: " + spans,
     pct(context.summary.recentNegativeShare) + " of recent check-ins negative"]);
}

function ruleEscalateAcuteCluster(context) {
  var acute = context.alertStreaks.filter(function (s) { return s.type === "same_day_cluster"; });
  if (!acute.length) return null;

  var worst = acute.reduce(function (a, b) { return b.length > a.length ? b : a; });
  return recommendation(
    "escalate_acute_cluster", "escalate", 11,
    "Find out what happened on " + shortDate(worst.startAt),
    worst.length + " negative check-ins inside that one school day. A cluster " +
    "that tight usually has a single cause behind it — an incident, illness, or " +
    "something that changed at home — rather than a pattern that built up over weeks.",
    [worst.length + " negatives between " + shortTime(worst.startAt) + " and " +
     shortTime(worst.endAt),
     "emotions: " + worst.emotions.join(", ")]);
}

/* Suppressed when there is an acute cluster or repeated episodes: those are
   more specific descriptions of the same student, and a teacher should not be
   told the same thing three ways. */
function ruleEscalateLongStreak(context) {
  var alerts = context.alertStreaks;
  if (alerts.length >= 2) return null;
  if (alerts.some(function (s) { return s.type === "same_day_cluster"; })) return null;

  var sustained = alerts.filter(function (s) { return s.length >= 6 && s.distinctDays >= 3; });
  if (!sustained.length) return null;

  var worst = sustained.reduce(function (a, b) { return b.length > a.length ? b : a; });
  return recommendation(
    "escalate_long_streak", "escalate", 12,
    "Sustained run — worth a second pair of eyes",
    worst.length + " negative check-ins in a row across " + worst.distinctDays +
    " days, with nothing positive in between. It has persisted across overnight " +
    "resets, which is what separates a rough patch from a bad day.",
    [worst.length + " consecutive negatives, " + shortDate(worst.startAt) +
     " to " + shortDate(worst.endAt)]);
}

/* --- antecedent: the strongest thing a teacher can actually change ------- */

/* Notes where the teacher already named this task themselves. Matched loosely
   in both directions ("Reading" against "Guided Reading"), because a teacher
   typing a note is not filling in a controlled vocabulary. */
function corroboratingNotes(context, taskName) {
  var lowered = taskName.toLowerCase();
  return context.notes.namedTrigger.concat(context.notes.neededPrompt)
    .filter(function (note) {
      var task = note.task.toLowerCase();
      return lowered.indexOf(task) !== -1 || task.indexOf(lowered) !== -1;
    });
}

/* Ranked ABOVE everything else when a teacher's own note names the same task:
   agreement between a computed correlation and a human judgement is much
   stronger evidence than either alone. */
function ruleAntecedentTask(context) {
  var top = context.topTask;
  if (!top) return null;

  var corroborated = corroboratingNotes(context, top.task);
  var evidence = [
    top.hits + " of the " + top.exposure + " check-ins around " + top.task +
      " were flagged (" + pct(top.rate) + ")",
    context.displayName + "'s check-ins go badly " + pct(top.usualRate) +
      " of the time overall — " + top.lift + "x more likely here"
  ];
  if (top.skipped >= RECOMMEND.minSkipped) {
    evidence.push(top.skipped + " " + top.task + " tasks skipped in the same period");
  }
  if (corroborated.length) {
    evidence.push("your note of " + shortDate(corroborated[0].at) + " names " +
                  corroborated[0].task + " too");
  }

  var detail = pct(top.rate) + " of " + context.displayName + "'s check-ins around " +
    top.task + " were flagged, against " + pct(top.usualRate) + " across their day " +
    "as a whole. Preview it on the schedule before it starts, or move it next to " +
    "something they finish well.";
  if (corroborated.length) {
    detail += " You already wrote this down once — the data agrees with you.";
  }

  return recommendation("antecedent_task", "antecedent", corroborated.length ? 20 : 21,
    top.task + " is where this shows up", detail, evidence);
}

/* Deliberately framed as "not yet": change two things at once and you cannot
   tell which one worked. */
function ruleAntecedentSecondTask(context) {
  if (context.tasks.length < 2) return null;
  var top = context.tasks[0], second = context.tasks[1];

  return recommendation("antecedent_second_task", "antecedent", 26,
    second.task + " is a secondary cluster",
    "Smaller than " + top.task + " but still above " + context.displayName +
    "'s baseline. Worth watching once the first one is addressed rather than " +
    "changing both at once — two changes at a time makes it impossible to tell " +
    "which one worked.",
    [second.hits + " of " + second.exposure + " check-ins around " + second.task +
     " flagged (" + pct(second.rate) + " against a usual " + pct(second.usualRate) + ")"]);
}

/* Only fires when the activity rule found nothing: if there IS a named
   activity, that is the more useful version of the same advice. */
function ruleAntecedentTime(context) {
  if (!context.time || context.topTask) return null;
  var window = context.time;

  return recommendation("antecedent_time", "antecedent", 24,
    "It is concentrated " + window.phrase,
    pct(window.rate) + " of " + context.displayName + "'s check-ins " + window.phrase +
    " were flagged, against " + pct(window.usualRate) + " across the whole day — " +
    "so this is the time of day talking, not the timetable. No single activity " +
    "accounts for them. Look at what that stretch has in common: length, noise " +
    "level, staffing, or how long it has been since a break.",
    [window.hits + " of " + window.exposure + " check-ins " + window.phrase +
      " flagged (" + pct(window.rate) + ")",
     window.excessPoints + " points above " + context.displayName +
      "'s own overall rate of " + pct(window.usualRate)]);
}

/* Two independent signals agreeing, which is why this is worth saying
   separately from the plain time-of-day rule. */
function ruleAntecedentFatigue(context) {
  if (!context.notes.flags.tired_after_lunch) return null;
  if (!context.time || context.time.bucket !== "afternoon") return null;

  return recommendation("antecedent_fatigue", "antecedent", 25,
    "Fatigue, not just mood",
    "The flags cluster in the afternoon and your notes already record " +
    context.displayName + " being tired after lunch. Shortening the afternoon " +
    "schedule is a lever you have already found works for this student.",
    [pct(context.time.rate) + " of check-ins after 1pm were flagged",
     "notes record post-lunch tiredness"]);
}

/* --- regulation: what to hand the student in the moment ------------------ */

/* Prefers a tool the student has not exhausted. When they have tried them all
   the advice CHANGES rather than repeating: the gap is no longer which tool but
   reaching one BEFORE the check-in instead of after it. */
function ruleRegulationTool(context) {
  var dominant = context.dominantEmotion;
  if (!dominant) return null;

  var options = EMOTION_TOOLS[dominant] || [];
  var untried = options.filter(function (tool) { return !context.calmUsed[tool]; });
  var choice = untried.length ? untried[0] : (options.length ? options[0] : null);
  if (!choice) return null;

  var spec = CALM_TOOLS[choice];
  var lead, why;
  if (untried.length) {
    lead = context.displayName + " has not opened " + spec.label + " yet";
    why = dominant.charAt(0).toUpperCase() + dominant.slice(1) + " usually reads as " +
          (EMOTION_READS[dominant] || "distress") + ", and " + spec.label +
          " targets that directly.";
  } else {
    lead = "Go back to " + spec.label;
    why = context.displayName + " has already tried every calm tool that fits " +
          dominant + ", so the gap is not which tool — it is reaching one before " +
          "the check-in rather than after it.";
  }

  var evidence = [
    context.emotionMix[dominant] + " of " + context.flaggedCheckInCount +
      " flagged check-ins are '" + dominant + "'",
    "calm corner: " + Object.keys(context.calmUsed).length + " of " +
      CALM_TOOL_COUNT + " tools tried"
  ];
  if (context.notes.calmWorked.length) {
    evidence.push("notes record the calm corner working after " +
                  context.notes.calmWorked[0].task);
  }

  var timing = context.topTask
    ? " Offer it when " + context.topTask.task + " is coming up, not after it has gone wrong."
    : " Offer it before the check-in, not as a repair afterwards.";
  var caveat = EMOTION_CAVEATS[dominant];

  return recommendation("regulation_tool", "regulation", 30,
    spec.emoji + " " + lead, why + timing + (caveat ? " " + caveat : ""), evidence);
}

/* --- communication: the flag may be a vocabulary gap, not a mood --------- */

/* Confusion and overwhelm are the two flags most likely to be an instruction
   that did not land rather than a feeling that arrived. */
function ruleCommunicationAac(context) {
  var gap = (context.emotionMix.confused || 0) + (context.emotionMix.overwhelmed || 0);
  if (!context.emoCount || !gap) return null;
  if (context.aacCount / context.emoCount >= RECOMMEND.aacLowRatio) return null;

  return recommendation("communication_aac", "communication", 35,
    "This may be a communication gap, not a mood",
    context.displayName + " logs feelings far more often than they use AAC to say " +
    "anything about them. Confusion and overwhelm are the two flags most likely to " +
    "be an instruction that did not land. Try modelling two AAC phrases for asking " +
    "for help" + (context.topTask ? " before " + context.topTask.task + "." : "."),
    [context.aacCount + " AAC uses against " + context.emoCount + " emotion check-ins (" +
      pct(context.aacCount / context.emoCount) + ")",
     gap + " flagged check-ins are confused or overwhelmed"]);
}

/* --- story: point at something the classroom already owns ---------------- */

/* Never invents a story. Pointing a teacher at a resource they already own is
   advice; pointing them at one they would have to write is homework. */
function ruleStoryAssign(context) {
  var picked = context.stories.pick(
    context.topTask ? context.topTask.task : null, context.dominantEmotion);
  var story = picked[0], matchedOn = picked[1];
  if (!story) return null;

  var target = matchedOn === "task" ? context.topTask.task : context.dominantEmotion;
  return recommendation("story_assign", "story", 40,
    'Assign "' + story.title + '"',
    "Your classroom already has this story. Reading it ahead of time is the " +
    "cheapest version of previewing " +
    (matchedOn === "task" ? target + "."
      : "what tends to set " + context.displayName + " off."),
    ["matched on " + (matchedOn === "task" ? "the correlated activity" : context.dominantEmotion),
     (story.pages || []).length + " pages, already in this classroom"]);
}

/* --- motivation: the reward system may be misconfigured ------------------ */

/* A reward that never arrives stops being a reward, and in a rough patch an
   unreachable goal is one more thing going wrong rather than a motivator. */
function ruleMotivationTokens(context) {
  var goal = context.tokenGoal;
  if (!goal || context.tokens === undefined || context.tokens === null) return null;
  if (context.tokens >= goal) return null;
  if (context.notes.flags.token_goal_met) return null;

  return recommendation("motivation_tokens", "motivation", 45,
    "The token board is out of reach right now",
    context.displayName + " is at " + context.tokens + " of " + goal + " tokens and " +
    "has not hit the goal in the last 30 days of notes. A reward that never arrives " +
    "stops being a reward — consider a smaller interim goal until the pattern settles.",
    [context.tokens + "/" + goal + ' tokens toward "' + context.tokenReward + '"',
     "no token-goal note in the last 30 days"]);
}

/* --- documentation: what is missing from the record --------------------- */

/* Aimed squarely at what happens later: check-ins alone will show a support
   team or an IEP meeting the pattern, but not the context the teacher is
   carrying in their head. */
function ruleDocumentNote(context) {
  if (context.noteCount !== 0) return null;

  return recommendation("document_note", "document", 50,
    "Nothing on file for this period",
    "There are no notes for " + context.displayName + " in the window these flags " +
    "cover. If this goes to a support team or an IEP meeting, the check-ins alone " +
    "will not carry the context you have in your head.",
    ["0 notes in the last 30 days",
     context.summary.streakCount + " flagged patterns over the same window"]);
}

function ruleDocumentContext(context) {
  if (context.noteCount === 0 || !context.notes.recent.length) return null;

  return recommendation("document_context", "document", 52,
    "Your notes already have context on this",
    context.noteCount + " note" + (context.noteCount === 1 ? "" : "s") + " on " +
    context.displayName + " in this window. Check whether the most recent one still " +
    "describes what you are seeing.",
    context.notes.recent.slice(0, 2).map(function (note) { return note.text; }));
}

/* --- the two channel-specific rules ------------------------------------- */

/* Good news, first and quick. Here for two reasons, the second less obvious: a
   good stretch is worth naming out loud to a student, AND it is the baseline a
   future flag gets compared against. */
function ruleCelebrate(context) {
  if (context.channel !== "positive") return null;

  var repeats = context.streaks.filter(function (s) { return s.type === "repeated"; });
  if (!repeats.length) return null;
  var best = repeats.reduce(function (a, b) { return b.length > a.length ? b : a; });

  return recommendation("celebrate", "celebrate", 5,
    "Say this one out loud",
    context.displayName + ' logged "' + best.emotion + '" ' + best.length +
    " check-ins in a row across " + best.distinctDays + " days. It is here because " +
    "good stretches are worth naming to a student, and because it is the baseline " +
    "you compare a future flag against.",
    [best.length + "x " + best.emotion + ", " + shortDate(best.startAt) +
     " to " + shortDate(best.endAt)]);
}

/* A recommendation that says "do nothing" is doing real work. Without it a
   watch student either gets no card at all — and looks like an oversight — or
   gets advice pitched at an alert they have not reached. */
function ruleMonitorWatch(context) {
  if (context.channel !== "watch" || context.tasks.length) return null;

  var reasons = context.summary.watchReasons.join(", ");
  return recommendation("monitor_watch", "monitor", 48,
    "Watch, do not act yet",
    context.displayName + " tripped " + reasons + " but never four negatives in a row. " +
    "That is a pattern worth knowing about, not one worth intervening on — the soft " +
    "signal is here so that if it does turn into a streak, you already know the history.",
    ["trigger: " + reasons,
     pct(context.summary.recentNegativeShare) + " of recent check-ins negative"]);
}

/* The registry. The order here does NOT decide what a teacher sees — that is
   rankAndCap, which sorts by rank. This list exists so adding a rule is a
   one-line change. */
var STUDENT_RULES = [
  ruleCelebrate,
  ruleEscalateRepeatEpisodes,
  ruleEscalateAcuteCluster,
  ruleEscalateLongStreak,
  ruleAntecedentTask,
  ruleAntecedentSecondTask,
  ruleAntecedentTime,
  ruleAntecedentFatigue,
  ruleRegulationTool,
  ruleCommunicationAac,
  ruleStoryAssign,
  ruleMotivationTokens,
  ruleDocumentNote,
  ruleDocumentContext,
  ruleMonitorWatch
];

function capForChannel(channel) {
  if (channel === "alert") return RECOMMEND.maxRecommendations;
  if (channel === "watch") return RECOMMEND.watchRecommendations;
  return RECOMMEND.positiveRecommendations;
}

/* Order by rank and cut to the channel's cap. The tie-break on id is what makes
   the output stable: two rules at the same rank always come out in the same
   order, so re-opening the tab never silently reshuffles a teacher's list. */
function rankAndCap(recommendations, channel) {
  recommendations.sort(function (a, b) {
    return (a.rank - b.rank) || a.id.localeCompare(b.id);
  });
  return recommendations.slice(0, capForChannel(channel));
}

/* One line stating why this student is on the list at all. Shown above the
   recommendations, because "here is what to do" is unreadable without "here is
   what I am reacting to" directly above it. */
function headlineReason(context) {
  var reason;
  var alerts = context.alertStreaks;

  if (alerts.length) {
    var worst = alerts.reduce(function (a, b) { return b.length > a.length ? b : a; });
    reason = worst.type === "same_day_cluster"
      ? worst.length + " negative check-ins in one day (" + shortDate(worst.startAt) + ")"
      : worst.length + " negative check-ins in a row over " + worst.distinctDays + " days";
    if (alerts.length > 1) reason += ", across " + alerts.length + " separate episodes";
  } else if (context.channel === "watch") {
    reason = pct(context.summary.recentNegativeShare) +
             " of recent check-ins negative, never four in a row";
  } else {
    var repeats = context.streaks.filter(function (s) { return s.type === "repeated"; });
    if (repeats.length) {
      var best = repeats.reduce(function (a, b) { return b.length > a.length ? b : a; });
      reason = best.length + ' "' + best.emotion + '" check-ins in a row';
    } else {
      reason = "positive pattern";
    }
  }

  if (context.tasks.length) reason += " — clustered around " + context.tasks[0].task;
  return reason;
}

/* ===========================================================================
   THE ROOM RULES
   ---------------------------------------------------------------------------
   An activity that upsets one child is that child's antecedent, and the answer
   is a plan for that child. The SAME activity upsetting a quarter of the
   flagged roster is a schedule problem, and the answer is to change the
   activity. That is a different kind of advice with a different audience, and
   it is only visible by looking across students.
   ======================================================================== */

function ruleRoomTask(contexts) {
  var flagged = contexts.length, perTask = {};
  contexts.forEach(function (context) {
    var seen = {};
    context.tasks.forEach(function (cluster) { seen[cluster.task] = true; });
    Object.keys(seen).forEach(function (task) { perTask[task] = (perTask[task] || 0) + 1; });
  });

  return Object.keys(perTask)
    .sort(function (a, b) { return (perTask[b] - perTask[a]) || a.localeCompare(b); })
    .slice(0, 2)   // a room can genuinely have two hard activities; more is a timetable review
    .filter(function (task) {
      return perTask[task] >= RECOMMEND.roomTaskMinStudents &&
             perTask[task] / flagged >= RECOMMEND.roomTaskMinShare;
    })
    .map(function (task) {
      var count = perTask[task];
      return recommendation("room_task", "room", 10,
        task + " is a room-wide pattern, not a student one",
        count + " of " + flagged + " flagged students in this room cluster around " +
        task + ". When the same activity shows up across that many children, the " +
        "activity is the thing to change — its length, its position in the day, or " +
        "the warning they get before it — rather than each student's plan.",
        [count + " of " + flagged + " flagged students (" + pct(count / flagged) + ")",
         "measured against each student's own baseline, not the room's"]);
    });
}

/* A higher share is demanded than for an activity: there are only three time
   bands, so any two students land in the same one far more easily than they
   land on the same activity. */
function ruleRoomTime(contexts) {
  var flagged = contexts.length, buckets = {};
  contexts.forEach(function (context) {
    if (context.time) buckets[context.time.bucket] = (buckets[context.time.bucket] || 0) + 1;
  });

  var keys = Object.keys(buckets);
  if (!keys.length) return [];
  keys.sort(function (a, b) { return (buckets[b] - buckets[a]) || a.localeCompare(b); });

  var bucket = keys[0], count = buckets[bucket];
  if (count / flagged < RECOMMEND.roomTimeMinShare ||
      count < RECOMMEND.roomTaskMinStudents) return [];

  var phrase = TIME_PHRASES[bucket];
  return [recommendation("room_time", "room", 20,
    "The room's hard stretch is " + phrase,
    count + " of " + flagged + " flagged students concentrate " + phrase + ". That " +
    "points at the shape of the day rather than at any one child — check what the " +
    "schedule asks of them in that window and where the nearest break sits.",
    [count + " of " + flagged + " flagged students concentrate " + phrase])];
}

/* Simultaneous decline is worth more than any one student's decline: a schedule
   change, a staffing change or a run of disrupted days shows up as several
   children moving together. */
function ruleRoomTrend(contexts) {
  var flagged = contexts.length;
  var worsening = contexts.filter(function (c) { return c.trajectory.direction === "worsening"; });
  if (worsening.length < RECOMMEND.roomTaskMinStudents ||
      worsening.length / flagged < RECOMMEND.roomTaskMinShare) return [];

  return [recommendation("room_trend", "room", 30,
    "Several students are trending down together",
    worsening.length + " of " + flagged + " flagged students are more negative in the " +
    "second half of the window than the first. Simultaneous decline usually traces to " +
    "something the room shares — a schedule change, a staffing change, or a run of " +
    "disrupted days.",
    [worsening.length + " of " + flagged + " students worsening",
     "compares each student's own first half against their second"])];
}

/* Aimed at the meeting that has not happened yet: these are the students most
   likely to come up in one, and the ones with no record of what was tried. */
function ruleRoomDocumentation(contexts) {
  var undocumented = contexts.filter(function (c) {
    return c.noteCount === 0 && c.channel === "alert";
  });
  if (undocumented.length < RECOMMEND.roomTaskMinStudents) return [];

  var names = undocumented.slice(0, 6).map(function (c) { return c.displayName; }).join(", ");
  if (undocumented.length > 6) names += "…";

  return [recommendation("room_documentation", "room", 40,
    undocumented.length + " alert students have no notes",
    "These are the students most likely to come up in a meeting, and the ones with " +
    "nothing written down. The check-in history will show the pattern but not what " +
    "you did about it.",
    [names])];
}

var ROOM_RULES = [ruleRoomTask, ruleRoomTime, ruleRoomTrend, ruleRoomDocumentation];

/* A room below roomMinFlagged is skipped entirely: with two flagged students,
   "2 of 2 cluster around Reading" is a 100% share of nothing. */
function buildRoomRecommendations(contexts) {
  if (contexts.length < RECOMMEND.roomMinFlagged) return [];
  var found = [];
  ROOM_RULES.forEach(function (rule) { found = found.concat(rule(contexts)); });
  found.sort(function (a, b) { return a.rank - b.rank; });
  return found;
}

/* ===========================================================================
   DIFF — how a student moved since the last look
   ---------------------------------------------------------------------------
   Four transitions look like four cases and collapse into one numeric
   comparison, once "not flagged" is given a rank one worse than every real
   priority:

       rank worse, no previous record  -> new
       rank worse                      -> escalated
       rank better, no current record  -> resolved
       rank better                     -> eased
       rank unchanged                  -> unchanged

   In the Python this compares two pinned detector runs. Here it compares
   against a snapshot this browser saved the last time the tab was opened,
   which is the honest version of the same question for live data.
   ======================================================================== */

var CHANGE_ORDER = ["escalated", "new", "eased", "resolved"];

function classifyChange(previous, current) {
  var before = previous ? priorityRank(previous.priority) : UNFLAGGED_RANK;
  var after = current ? priorityRank(current.priority) : UNFLAGGED_RANK;
  if (before === after) return "unchanged";
  if (after < before) return previous ? "escalated" : "new";   // lower rank = more urgent
  return current ? "eased" : "resolved";
}

/* A snapshot small enough to keep in localStorage indefinitely: just what the
   comparison needs, never the check-ins themselves. */
function snapshotOf(analysis) {
  var students = {};
  Object.keys(analysis.students).forEach(function (id) {
    students[id] = { priority: analysis.students[id].priority,
                     name: analysis.students[id].name };
  });
  return { takenAt: new Date().toISOString(), code: analysis.room.code, students: students };
}

function diffAgainst(snapshot, analysis) {
  if (!snapshot || !snapshot.students) return null;

  var counts = { escalated: 0, "new": 0, eased: 0, resolved: 0, unchanged: 0 };
  var listed = [];
  var seen = {};

  // Every student present in EITHER run. Iterating only the current one would
  // silently drop everyone who left the flagged set — precisely the resolution
  // a teacher most wants confirmed.
  Object.keys(snapshot.students).forEach(function (id) { seen[id] = true; });
  Object.keys(analysis.students).forEach(function (id) { seen[id] = true; });

  Object.keys(seen).forEach(function (id) {
    var previous = snapshot.students[id] || null;
    var current = analysis.students[id] || null;
    var change = classifyChange(previous, current);
    counts[change]++;
    if (change === "unchanged") return;
    listed.push({
      id: id,
      name: (current && current.name) || (previous && previous.name) || "Student",
      change: change,
      from: RANK_LABEL[previous ? priorityRank(previous.priority) : UNFLAGGED_RANK],
      to: RANK_LABEL[current ? priorityRank(current.priority) : UNFLAGGED_RANK]
    });
  });

  listed.sort(function (a, b) {
    return (CHANGE_ORDER.indexOf(a.change) - CHANGE_ORDER.indexOf(b.change)) ||
           a.name.localeCompare(b.name);
  });

  return { takenAt: snapshot.takenAt, counts: counts, students: listed };
}

/* ===========================================================================
   THE ENTRY POINT
   ======================================================================== */

function round(value, places) {
  var factor = Math.pow(10, places);
  return Math.round(value * factor) / factor;
}

/* What to call this student on screen, honouring the classroom's setting.

   A classroom sets nameMode for a reason — most often that the dashboard gets
   shown on a projector where a visitor could read it. Code that spells the name
   out anyway quietly undoes a privacy decision a teacher made deliberately, so
   every screen routes names through here. The app's own two modes are "full"
   and "id"; "initials" and "anon" are accepted because imported data may carry
   them. */
function displayName(name, mode, id) {
  var text = String(name || "").trim();
  if (mode === "id" || mode === "anon") {
    return "Student " + String(id || "").slice(-4).toUpperCase();
  }
  if (mode === "initials") {
    var initials = text.split(/\s+/).filter(Boolean)
      .map(function (part) { return part[0].toUpperCase(); }).join("");
    return initials || "S";
  }
  return text.split(/\s+/)[0].slice(0, 20) || "Student";
}

/* Everything the Insights view draws, for one classroom.

   Args (all of them shapes the app already has in hand):
       classroom  the classroom record — tokenGoal, nameMode, name, teacherName
       roster     [{id, name, avatar, lastSeen}]
       students   {id: the app's student record}
       notes      {id: [note rows]}  — may be partial; missing means "none loaded"
       stories    the classroom's social stories
       reference  optional Date to end the window at; defaults to the room's
                  own latest check-in

   The window is anchored to the LATEST CHECK-IN rather than to `new Date()`.
   Anchoring to today would show an empty page for any class that has not
   checked in this month — including every sample room — and "no flags" and "no
   data" are very different answers. The view states which date it used and how
   stale it is, so the difference is never hidden. */
function analyseClassroom(options) {
  var classroom = options.classroom || {};
  var roster = options.roster || [];
  var records = options.students || {};
  var notesByStudent = options.notes || {};
  var nameMode = classroom.nameMode || "full";
  var stories = new StoryIndex(options.stories || []);

  // Pass 1: read every student's check-ins, and find the room's latest.
  var read = [], latest = null;
  roster.forEach(function (entry) {
    var record = records[entry.id];
    if (!record) return;
    var checkIns = readCheckIns(record.emotionLog || record.emotion_log);
    if (checkIns.length) {
      var last = checkIns[checkIns.length - 1].at;
      if (!latest || last > latest) latest = last;
    }
    read.push({ entry: entry, record: record, checkIns: checkIns });
  });

  var now = new Date();
  var reference = options.reference || latest || now;
  var cutoff = new Date(reference.getTime() - DETECT.recencyDays * DAY_MS);

  // Pass 2: run the rules over each student's recent window.
  var contexts = [], students = {}, order = [];
  var counts = { flagged: 0, alert: 0, watch: 0, positive: 0, high: 0, undoc: 0 };

  read.forEach(function (item) {
    var recent = item.checkIns.filter(function (c) { return c.at >= cutoff; });
    if (!recent.length) return;

    var timeline = new Timeline(recent);
    var streaks = findAllStreaks(timeline);
    if (!streaks.length) return;    // nothing to say about this student

    var summary = summarize(streaks, recent);
    var context = buildContext({
      studentId: item.entry.id,
      displayName: displayName(item.entry.name || item.record.name, nameMode, item.entry.id),
      record: item.record,
      summary: summary,
      streaks: streaks,
      timeline: timeline,
      notes: readNotes(notesByStudent[item.entry.id], cutoff),
      stories: stories,
      tokenGoal: classroom.tokenGoal,
      tokenReward: classroom.tokenReward
    });
    contexts.push(context);
  });

  // Worst first, then whoever has the most separate alert episodes, then by id
  // purely so the order is stable — a teacher can scan the same page twice and
  // trust that nothing moved under them.
  contexts.sort(function (a, b) {
    return (priorityRank(a.priority) - priorityRank(b.priority)) ||
           (b.summary.alertStreakCount - a.summary.alertStreakCount) ||
           String(a.studentId).localeCompare(String(b.studentId));
  });

  contexts.forEach(function (context) {
    var found = [];
    STUDENT_RULES.forEach(function (rule) {
      var result = rule(context);
      if (result) found.push(result);
    });

    counts.flagged++;
    counts[context.channel]++;
    if (context.priority === "high") counts.high++;
    if (context.channel === "alert" && context.noteCount === 0) counts.undoc++;

    order.push(context.studentId);
    students[context.studentId] = {
      id: context.studentId,
      name: context.displayName,
      avatar: (records[context.studentId] || {}).avatar || "",
      channel: context.channel,
      priority: context.priority,
      why: headlineReason(context),
      trajectory: context.trajectory,
      noteCount: context.noteCount,
      strip: context.strip,
      emotionMix: context.emotionMix,
      dominantEmotion: context.dominantEmotion,
      recommendations: rankAndCap(found, context.channel),
      taskClusters: context.tasks.slice(0, 2),
      timeCluster: context.time,
      recentNotes: context.notes.recent.slice(0, 3),
      recentNegativeShare: context.summary.recentNegativeShare,
      checkInCount: context.summary.recentEntryCount,
      streaks: context.streaks
    };
  });

  // Activities across the room, counted by how many students each touches.
  // Recomputed here rather than reused from the room rule because the chart
  // asks a different question: the rule asks "is this worth acting on" and
  // applies thresholds; the chart shows the shape of the room, bars included.
  var perTask = {};
  contexts.forEach(function (context) {
    var seen = {};
    context.tasks.forEach(function (cluster) { seen[cluster.task] = true; });
    Object.keys(seen).forEach(function (task) { perTask[task] = (perTask[task] || 0) + 1; });
  });
  var taskChart = Object.keys(perTask)
    .sort(function (a, b) { return (perTask[b] - perTask[a]) || a.localeCompare(b); })
    .slice(0, 6)
    .map(function (task) { return [task, perTask[task]]; });

  return {
    meta: {
      reference: reference,
      windowStart: cutoff,
      windowEnd: reference,
      recencyDays: DETECT.recencyDays,
      streakLength: DETECT.streakLength,
      taskWindowMinutes: RECOMMEND.taskWindowMinutes,
      studentsScanned: read.length,
      studentsFlagged: counts.flagged,
      latestCheckIn: latest,
      // How far behind today the data is. The view says so rather than quietly
      // presenting a month-old window as current.
      staleDays: latest ? Math.max(0, daysBetween(latest, now)) : null,
      anchoredToData: !options.reference && !!latest
    },
    room: {
      code: classroom.code || options.code || "",
      name: classroom.name || "My Classroom",
      teacher: classroom.teacherName || "",
      school: classroom.schoolName || "",
      counts: counts,
      recommendations: buildRoomRecommendations(contexts),
      taskChart: taskChart,
      order: order
    },
    students: students
  };
}

/* ======================================================================== */

var FBI = {
  analyseClassroom: analyseClassroom,
  snapshotOf: snapshotOf,
  diffAgainst: diffAgainst,
  classifyChange: classifyChange,
  priorityRank: priorityRank,
  displayName: displayName,

  // Exposed for the view, so it never restates a word or a threshold this file
  // already owns.
  EMOTION_VALENCE: EMOTION_VALENCE,
  EMOTION_ORDER: EMOTION_ORDER,
  CALM_TOOLS: CALM_TOOLS,
  DETECT: DETECT,
  RECOMMEND: RECOMMEND,
  CHANGE_ORDER: CHANGE_ORDER,

  // Exposed for tools/verify_engine.py, which checks this file against the
  // Python stage by stage rather than only on the final answer.
  _internals: {
    normalizeEmotion: normalizeEmotion,
    parseTimestamp: parseTimestamp,
    parseClock: parseClock,
    readCheckIns: readCheckIns,
    Timeline: Timeline,
    findAllStreaks: findAllStreaks,
    summarize: summarize,
    binomialTail: binomialTail,
    evaluateGroup: evaluateGroup,
    usableBaseline: usableBaseline,
    correlateTasks: correlateTasks,
    correlateTime: correlateTime,
    trajectory: trajectory,
    buildContext: buildContext,
    headlineReason: headlineReason,
    STUDENT_RULES: STUDENT_RULES,
    rankAndCap: rankAndCap
  }
};

if (typeof module === "object" && module.exports) module.exports = FBI;
root.FBI = FBI;

})(typeof globalThis !== "undefined" ? globalThis : this);
