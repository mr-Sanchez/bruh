"""The learner model's pure logic: item bank, Leitner state, topic mastery.

Every activity in the app feeds one shared model of what the learner knows.
Its inputs are three kinds of records:

  * analysed recordings (recordings/*/analysis.json) - the mistakes, patterns
    and phrases the learner actually produced, turned into bank *items*;
  * attempts (data/attempts.jsonl) - every answer given in an exercise;
  * the analysed recordings again, as evidence: a pattern or phrase that
    shows up in *new* speech after it was practised was not learned yet.

This module holds no I/O and no clock: every function takes its data and
"now" explicitly, so the rules below are easy to test and to change.
app.learner_store does the reading and writing.

Item identity is content-based (a hash of the kind plus the normalised key
text), not positional. That way re-analysing a session keeps attempts
linked to the same items, and the same pattern or phrase coming up in two
recordings merges into one item with two occurrences - which is exactly
the "the mistake came back" signal the Leitner rules use.

Word cards (2026-09-27) are the one kind the learner chooses: after a set run
they pick words and phrases from the set's vocabulary, and each pick becomes
a Russian -> English flashcard (items_from_word_picks). They have their own
daily allowance of new cards and never depend on speech.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app import config, curriculum
from app.utils import AnalysedSession

KIND_FIX = "fix"  # one concrete mistake: quote -> correction
KIND_PATTERN = "pattern"  # the reusable construction behind a fix
KIND_PHRASE = "phrase"  # a word or construction to remember (vocabulary/takeaways)
KIND_WORD = "word"  # a word or phrase the learner picked from a set: RU -> EN flashcard

_ID_PREFIX: Dict[str, str] = {
    KIND_FIX: "fix", KIND_PATTERN: "pat", KIND_PHRASE: "phr", KIND_WORD: "wrd",
}

# Fixes on delivery topics (fillers, restarts) are not knowledge that can be
# recalled on a card: "uh, I, I think" -> "I think". They still count towards
# topic weakness; spoken drills train them instead.
NON_RECALL_TOPICS: frozenset = curriculum.NON_RECALL_TOPICS

_QUOTE_CHARS = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})
_EDGE_PUNCTUATION = " \t\n.,!?;:\"'«»…-–—()"
_WHITESPACE = re.compile(r"\s+")


# ------------------------------------------------------------------ identity
def normalize_text(text: str) -> str:
    """Lowercase, unify quotes, collapse whitespace, trim edge punctuation."""
    value = _WHITESPACE.sub(" ", (text or "").translate(_QUOTE_CHARS).lower())
    return value.strip(_EDGE_PUNCTUATION)


def item_id(kind: str, key_text: str) -> str:
    digest = hashlib.sha1(f"{kind}\n{normalize_text(key_text)}".encode("utf-8")).hexdigest()
    return f"{_ID_PREFIX[kind]}-{digest[:12]}"


# ---------------------------------------------------------------- item bank
def items_from_analysis(session: AnalysedSession) -> List[Dict[str, Any]]:
    """The bank items one analysed recording contributes, each with one occurrence."""
    analysis = session.analysis
    occurrence = {"session_id": session.session_id, "at": session.recorded_at.isoformat()}
    items: List[Dict[str, Any]] = []

    def add(kind: str, key_text: str, **fields: Any) -> Optional[str]:
        if not normalize_text(key_text):
            return None
        identifier = item_id(kind, key_text)
        items.append(
            {
                "id": identifier,
                "kind": kind,
                "language": session.language,
                **fields,
                "occurrences": [dict(occurrence)],
            }
        )
        return identifier

    for issue in _dicts(analysis.get("issues")):
        topic = issue.get("topic")
        pattern_id = None
        pattern = issue.get("pattern")
        if isinstance(pattern, dict) and pattern.get("rule"):
            pattern_id = add(
                KIND_PATTERN,
                pattern["rule"],
                topic=topic,
                content={"rule": pattern["rule"], "examples": list(pattern.get("examples") or [])},
            )
        correction = issue.get("correction") or ""
        quote = issue.get("quote") or ""
        if topic in NON_RECALL_TOPICS or normalize_text(correction) == normalize_text(quote):
            continue
        add(
            KIND_FIX,
            correction,
            topic=topic,
            severity=issue.get("severity"),
            pattern_id=pattern_id,
            content={
                "quote": quote,
                "correction": correction,
                "better_versions": list(issue.get("better_versions") or []),
                "explanation": issue.get("explanation") or "",
                "focus": pattern["rule"] if pattern_id else "",
                "focus_examples": list(pattern.get("examples") or []) if pattern_id else [],
                "drills": clean_drills(issue.get("drills")),
            },
        )

    # scene_vocabulary: words for a described picture (picture descriptions only).
    for source in ("vocabulary", "takeaways", "scene_vocabulary"):
        for phrase in _dicts(analysis.get(source)):
            add(
                KIND_PHRASE,
                phrase.get("phrase") or "",
                topic=None,
                sources=[source],
                content={
                    "phrase": phrase.get("phrase") or "",
                    "meaning": phrase.get("meaning") or "",
                    "example": phrase.get("example") or "",
                },
            )
    return items


def build_bank(
    sessions: Iterable[AnalysedSession],
    set_items: Iterable[Dict[str, Any]] = (),
    word_items: Iterable[Dict[str, Any]] = (),
) -> Dict[str, Dict[str, Any]]:
    """Merge every recording's items by id; the latest recording's wording wins.

    Sessions must come oldest first (utils.iter_analysed_sessions does that).
    `set_items` are mistakes made in AI exercise sets (items_from_set_run):
    they add cards and occurrences, but never reword an item that came from
    speech - the learner's own sentence is the better card. `word_items` are
    the learner's word picks (items_from_word_picks); the first pick's wording
    stays, so a card does not change under the learner's feet.
    """
    bank: Dict[str, Dict[str, Any]] = {}
    for session in sessions:
        for item in items_from_analysis(session):
            _merge_item(bank, item, reword=True)
    for item in sorted(set_items, key=lambda i: i["occurrences"][0]["at"]):
        _merge_item(bank, item, reword=False)
    for item in sorted(word_items, key=lambda i: i["occurrences"][0]["at"]):
        _merge_item(bank, item, reword=False)
    return bank


def items_from_word_picks(picks: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Word cards from the pick log (data/word_picks.jsonl), one per live pick.

    A record is {"ts", "set_id", "vocab_id", "action": "add"|"remove", "word":
    {"english", "russian", "example", "example_russian", "note"}, "lesson_id"}.
    The newest record per (set, word) wins, so «убрать» after «добавить» takes
    the card away. The same English picked from two sets is one card (the id is
    a hash of the English) with two occurrences.
    """
    latest: Dict[tuple, Dict[str, Any]] = {}
    for pick in sorted(_dicts(list(picks)), key=lambda p: str(p.get("ts", ""))):
        latest[(pick.get("set_id"), pick.get("vocab_id"))] = pick
    items: List[Dict[str, Any]] = []
    for (set_id, vocab_id), pick in latest.items():
        word = pick.get("word") if isinstance(pick.get("word"), dict) else {}
        english = (word.get("english") or "").strip()
        if pick.get("action") != "add" or not normalize_text(english):
            continue
        items.append(
            {
                "id": item_id(KIND_WORD, english),
                "kind": KIND_WORD,
                "language": "en",
                "topic": None,
                "lesson_id": pick.get("lesson_id"),
                "content": {
                    field: (word.get(field) or "").strip()
                    for field in ("english", "russian", "example", "example_russian", "note")
                },
                "occurrences": [
                    {"pick": f"{set_id}#{vocab_id}", "set_id": set_id, "at": pick["ts"]}
                ],
            }
        )
    return items


def _occurrence_key(occurrence: Dict[str, Any]) -> Any:
    return occurrence.get("session_id") or occurrence.get("set_run") or occurrence.get("pick")


def _merge_item(bank: Dict[str, Dict[str, Any]], item: Dict[str, Any], *, reword: bool) -> None:
    existing = bank.get(item["id"])
    if existing is None:
        bank[item["id"]] = item
        return
    occurrences = existing["occurrences"]
    if reword:
        sources = sorted(set(existing.get("sources", [])) | set(item.get("sources", [])))
        existing.update({k: v for k, v in item.items() if k not in ("occurrences", "language")})
        if sources:
            existing["sources"] = sources
    new = item["occurrences"][0]
    if _occurrence_key(new) not in {_occurrence_key(o) for o in occurrences}:
        occurrences.append(new)
        occurrences.sort(key=lambda o: o["at"])


def _dicts(value: Any) -> List[Dict[str, Any]]:
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def clean_drills(value: Any) -> List[Dict[str, str]]:
    """A mistake's practice sentences, without any that lack either side."""
    drills = []
    for drill in _dicts(value):
        russian = str(drill.get("russian") or "").strip()
        english = str(drill.get("english") or "").strip()
        if russian and english:
            drills.append({"russian": russian, "english": english})
    return drills


def is_retired(item: Dict[str, Any]) -> bool:
    """A rule card, or a mistake card with no practice sentences, is no longer shown.

    Decided 2026-09-26: a card that shows the verbatim quote ("resource the
    code base") loses its context within days, and a long quote hides which
    of its problems is meant. Mistakes analysed before practice sentences
    existed - and non-English ones, which get none - stay in the bank as
    history (their attempts still count towards topic accuracy) but never
    reach a queue.

    Rule cards ("make up your own example") went the same way: nothing checked
    the example, and the mistake card behind the rule already drills it with
    new sentences - the rule is its hint, the rule's examples show after the
    answer. Rules stay in the bank as history and as AI-set seeds.

    So did grammar notes among phrases ("If + Present Simple, will + verb",
    "parallel structure in lists") that cannot be blanked out of their
    example: "recall the phrase" from a paraphrased rule is not a task. A plain
    word with no gap ("windowsill") is still recalled from its meaning.
    """
    kind = item.get("kind")
    content = item.get("content") or {}
    if kind == KIND_PATTERN:
        return True
    if kind == KIND_WORD:
        return False
    if kind == KIND_PHRASE:
        phrase = content.get("phrase") or ""
        if phrase_gap(phrase, content.get("example") or "") is not None:
            return False
        return _is_formula(phrase) or item.get("sources") == ["takeaways"]
    return kind == KIND_FIX and not content.get("drills")


def _is_formula(phrase: str) -> bool:
    """A phrase written as notation ("help + verb-ing", "a / an"), not as text."""
    return "+" in phrase or "/" in phrase


# ------------------------------------------------------------------ Leitner
@dataclass
class ItemState:
    item_id: str
    box: int
    due: Optional[str]  # ISO date; None once closed
    is_new: bool  # never practised
    attempts: int
    correct: int
    first_attempt_at: Optional[str]
    last_attempt_at: Optional[str]
    reset_by_speech_at: Optional[str]  # last time new speech sent it back to box 1
    closed: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def is_due(self, today: dt.date) -> bool:
        return not self.closed and self.due is not None and self.due <= today.isoformat()


def languages_compatible(a: str, b: str) -> bool:
    """en-US and en match; "multi" (mixed RU+EN speech) matches anything."""
    if "multi" in (a, b):
        return True
    return a.split("-")[0].lower() == b.split("-")[0].lower()


def item_state(
    item: Dict[str, Any],
    attempts: Sequence[Dict[str, Any]],
    analysed_sessions: Sequence[Dict[str, Any]],
) -> ItemState:
    """Replay an item's history into its Leitner state.

    Rules:
      * every item starts in box 1, due on the day it was first spoken;
      * a correct answer on or after the due date moves it up a box (early
        correct answers are fine but do not advance it - no cramming);
      * a wrong answer sends it back to box 1;
      * the item turning up again in a recording made *after* it was first
        practised sends it back to box 1 - speech is the strongest evidence;
      * it is closed once it sits in box 5 and the next
        LEITNER_CLOSE_AFTER_RECORDINGS analysed recordings in its language
        came and went without it reappearing.

    A word card knows nothing of speech: a second pick of the same word does
    not send it back, and it is closed by a correct review in box 5 (it was
    remembered after the longest interval).

    `attempts` are this item's attempts (any order); `analysed_sessions` are
    {"recorded_at", "language"} for every analysed recording.
    """
    intervals = config.LEITNER_INTERVALS_DAYS
    max_box = len(intervals)
    is_word = item.get("kind") == KIND_WORD
    graduated = False

    events: List[tuple] = []
    for occurrence in item.get("occurrences", []):
        events.append((_parse(occurrence["at"]), 1, None))
    for attempt in attempts:
        events.append((_parse(attempt["ts"]), 0, bool(attempt.get("correct"))))
    # Attempts sort before occurrences with the same timestamp.
    events.sort(key=lambda event: (event[0], event[1]))

    first_seen = min((when for when, kind, _ in events if kind == 1), default=None)
    box = 1
    due: Optional[dt.date] = first_seen.date() if first_seen else None
    practised = False
    total = correct = 0
    first_attempt_at: Optional[dt.datetime] = None
    last_attempt_at: Optional[dt.datetime] = None
    reset_at: Optional[dt.datetime] = None
    top_box_since: Optional[dt.datetime] = None

    for when, kind, was_correct in events:
        if kind == 1:  # occurrence in speech (for a word: another pick)
            if practised and not is_word:
                box, due, reset_at, top_box_since = 1, when.date(), when, None
            continue
        practised = True
        total += 1
        first_attempt_at = first_attempt_at or when
        last_attempt_at = when
        day = when.date()
        if was_correct:
            correct += 1
            if due is None or day >= due:
                graduated = graduated or (is_word and box == max_box)
                box = min(max_box, box + 1)
                due = day + dt.timedelta(days=intervals[box - 1])
        else:
            box = 1
            due = day + dt.timedelta(days=intervals[0])
        if box == max_box:
            top_box_since = top_box_since or when
        else:
            top_box_since = None

    closed = graduated
    if top_box_since is not None and not is_word:
        later = [
            s
            for s in analysed_sessions
            if _parse(s["recorded_at"]) > top_box_since
            and languages_compatible(s["language"], item.get("language", ""))
        ]
        closed = len(later) >= config.LEITNER_CLOSE_AFTER_RECORDINGS

    return ItemState(
        item_id=item["id"],
        box=box,
        due=None if closed or due is None else due.isoformat(),
        is_new=not practised,
        attempts=total,
        correct=correct,
        first_attempt_at=first_attempt_at.isoformat() if first_attempt_at else None,
        last_attempt_at=last_attempt_at.isoformat() if last_attempt_at else None,
        reset_by_speech_at=reset_at.isoformat() if reset_at else None,
        closed=closed,
    )


def _parse(timestamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(timestamp)


# ------------------------------------------------------------ topic mastery
def attempt_score(attempt: Dict[str, Any]) -> float:
    """0..1: a drill's share of right answers, else 1/0 for a card."""
    score = attempt.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        return min(1.0, max(0.0, float(score)))
    return 1.0 if attempt.get("correct") else 0.0


def topic_mastery(
    weakness_by_topic: Dict[str, float],
    bank: Dict[str, Dict[str, Any]],
    attempts: Sequence[Dict[str, Any]],
    states: Dict[str, ItemState],
    today: dt.date,
) -> List[Dict[str, Any]]:
    """Combine how often a topic shows up in speech with how well it is drilled.

    `priority` is what «Сегодня» trains first: the recency-decayed weakness
    score from real speech, scaled by exercise accuracy - x1.5 at 0% right,
    x0.5 at 100% right, unchanged while there are no attempts yet. Speech
    stays the main driver: perfect drill answers only halve a topic that
    keeps coming up when talking.

    Accuracy counts card attempts (via their item's topic) and topic drill
    attempts (their own `topic`, weighted by `score`) alike.
    """
    topic_of = {item_id_: item.get("topic") for item_id_, item in bank.items()}
    recent_by_topic: Dict[str, List[float]] = {}
    for attempt in sorted(attempts, key=lambda a: _parse(a["ts"]), reverse=True):
        topic = topic_of.get(attempt.get("item_id")) or attempt.get("topic")
        if not topic:
            continue
        results = recent_by_topic.setdefault(topic, [])
        if len(results) < config.TOPIC_ACCURACY_WINDOW:
            results.append(attempt_score(attempt))

    item_counts: Dict[str, Dict[str, int]] = {}
    for item_id_, item in bank.items():
        topic = item.get("topic")
        if not topic or is_retired(item):
            continue
        counts = item_counts.setdefault(topic, {"items": 0, "due": 0, "closed": 0})
        counts["items"] += 1
        state = states.get(item_id_)
        if state is not None and state.closed:
            counts["closed"] += 1
        elif state is not None and state.is_due(today):
            counts["due"] += 1

    rows = []
    for topic in set(weakness_by_topic) | set(item_counts) | set(recent_by_topic):
        weakness = weakness_by_topic.get(topic, 0.0)
        results = recent_by_topic.get(topic, [])
        accuracy = sum(results) / len(results) if results else None
        priority = weakness if accuracy is None else weakness * (1.5 - accuracy)
        counts = item_counts.get(topic, {"items": 0, "due": 0, "closed": 0})
        rows.append(
            {
                "key": topic,
                "weakness_score": round(weakness, 4),
                "recent_attempts": len(results),
                "accuracy": None if accuracy is None else round(accuracy, 3),
                "priority": round(priority, 4),
                "items": counts["items"],
                "due_items": counts["due"],
                "closed_items": counts["closed"],
            }
        )
    rows.sort(key=lambda row: (row["priority"], row["due_items"]), reverse=True)
    return rows


def area_mastery(topic_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Topic mastery rolled up to areas (taxonomy v2's coarse level).

    Weakness, priority and card counts add up; accuracy is the mean over the
    topics' recent answers, so a topic drilled 20 times weighs more than one
    drilled once. Highest priority first, like the topic rows.
    """
    areas: Dict[str, Dict[str, Any]] = {}
    for row in topic_rows:
        key = curriculum.area_of(row["key"])
        area = areas.setdefault(
            key,
            {
                "key": key,
                "topics": [],
                "weakness_score": 0.0,
                "priority": 0.0,
                "recent_attempts": 0,
                "items": 0,
                "due_items": 0,
                "closed_items": 0,
                "_answered": 0.0,
            },
        )
        area["topics"].append(row["key"])
        for field_ in ("weakness_score", "priority", "items", "due_items", "closed_items"):
            area[field_] += row[field_]
        if row["accuracy"] is not None:
            area["recent_attempts"] += row["recent_attempts"]
            area["_answered"] += row["accuracy"] * row["recent_attempts"]
    result = []
    for area in areas.values():
        answered = area.pop("_answered")
        count = area["recent_attempts"]
        area["accuracy"] = round(answered / count, 3) if count else None
        area["weakness_score"] = round(area["weakness_score"], 4)
        area["priority"] = round(area["priority"], 4)
        result.append(area)
    result.sort(key=lambda row: (row["priority"], row["due_items"]), reverse=True)
    return result


# -------------------------------------------------------------- daily queue
_SEVERITY_RANK: Dict[Optional[str], int] = {"major": 2, "moderate": 1, "minor": 0}
# New cards are interleaved: this many mistake cards,
# then one phrase, so vocabulary keeps flowing without crowding out errors.
_ISSUE_CARDS_PER_PHRASE = 2


def new_items_limit(due_reviews: int) -> int:
    """How many never-seen cards to introduce today (decided: 7-10 a day).

    The full allowance while reviews are manageable; the lower end once a
    review backlog builds up, so the daily load does not snowball.
    """
    if due_reviews > config.REVIEW_BACKLOG_THRESHOLD:
        return config.NEW_ITEMS_PER_DAY_MIN
    return config.NEW_ITEMS_PER_DAY_MAX


def daily_queue(
    bank: Dict[str, Dict[str, Any]],
    states: Dict[str, ItemState],
    topic_priority: Dict[str, float],
    today: dt.date,
) -> Dict[str, Any]:
    """Today's cards: every due review, plus a capped number of new cards.

    Reviews come weakest box first, then most overdue. New cards come from
    the topics that matter most right now (speech weakness x drill accuracy),
    major mistakes first, interleaved with phrases oldest first. New cards
    already started today count against the day's allowance.

    Word cards the learner picked have an allowance of their own
    (NEW_WORDS_PER_DAY), oldest pick first; they follow the other new cards.
    """
    live = {item_id_ for item_id_, item in bank.items() if not is_retired(item)}
    reviews = [
        item_id_
        for item_id_, state in states.items()
        if item_id_ in live and not state.is_new and state.is_due(today)
    ]
    reviews.sort(key=lambda i: (states[i].box, states[i].due or "", i))

    def is_word(item_id_: str) -> bool:
        return item_id_ in bank and bank[item_id_].get("kind") == KIND_WORD

    started = [
        item_id_
        for item_id_, state in states.items()
        if state.first_attempt_at and _parse(state.first_attempt_at).date() == today
    ]
    started_today = sum(1 for i in started if not is_word(i))
    words_started_today = len(started) - started_today
    limit = new_items_limit(len(reviews))
    remaining = max(0, limit - started_today)

    all_fresh = [
        item_id_
        for item_id_, state in states.items()
        if item_id_ in live and state.is_new and state.is_due(today)
    ]
    fresh = [i for i in all_fresh if not is_word(i)]

    def first_seen(item_id_: str) -> str:
        return min(o["at"] for o in bank[item_id_]["occurrences"])

    issue_cards = sorted(
        (i for i in fresh if bank[i]["kind"] != KIND_PHRASE),
        key=lambda i: (
            -topic_priority.get(bank[i].get("topic") or "", 0.0),
            -_SEVERITY_RANK.get(bank[i].get("severity"), 1),
            first_seen(i),
            i,
        ),
    )
    phrase_cards = sorted((i for i in fresh if bank[i]["kind"] == KIND_PHRASE), key=first_seen)

    new: List[str] = []
    while len(new) < remaining and (issue_cards or phrase_cards):
        for _ in range(_ISSUE_CARDS_PER_PHRASE):
            if issue_cards and len(new) < remaining:
                new.append(issue_cards.pop(0))
        if phrase_cards and len(new) < remaining:
            new.append(phrase_cards.pop(0))

    # Oldest pick first; picks saved together keep the set's order (v1, v2...).
    fresh_words = sorted(
        (i for i in all_fresh if is_word(i)),
        key=lambda i: (first_seen(i), bank[i]["occurrences"][0].get("pick", ""), i),
    )
    words_remaining = max(0, config.NEW_WORDS_PER_DAY - words_started_today)
    new_words = fresh_words[:words_remaining]

    return {
        "reviews": reviews,
        "new": new + new_words,
        "new_limit": limit,
        "new_started_today": started_today,
        "new_words_limit": config.NEW_WORDS_PER_DAY,
        "new_words_started_today": words_started_today,
        "new_waiting": len(all_fresh) - len(new) - len(new_words),
    }


# ---------------------------------------------------------- card exercises
EXERCISE_TRANSLATE = "translate"  # say a new Russian sentence in English; Claude checks
EXERCISE_GAP = "gap"  # type the phrase into the gap in its own example
EXERCISE_SELF = "self"  # recall, reveal, grade yourself
EXERCISE_FLIP = "flip"  # word card: Russian front, recall, flip to the English, grade yourself

_PARENTHESES = re.compile(r"\s*\([^)]*\)")


def card_exercise(item: Dict[str, Any], state: ItemState) -> Dict[str, Any]:
    """How a card is drilled, picked from its content and history.

    A mistake is a Russian sentence to say in English with the construction
    that went wrong; every attempt moves on to the next of its sentences, so
    a review is never the same sentence twice in a row. The answer is free
    text, so Claude checks it (POST /api/learner/cards/<id>/check). Anything
    code cannot check fairly - phrases that are really grammar notes
    ("help + verb-ing") - is a self-graded flashcard, and so is a word card
    (decided 2026-09-27, Anki-style: synonyms make a typed check unfair).
    """
    content = item.get("content") or {}
    kind = item.get("kind")
    if kind == KIND_FIX:
        drills = content.get("drills") or []
        if not drills:
            return {"type": EXERCISE_SELF}
        index = state.attempts % len(drills)
        return {
            "type": EXERCISE_TRANSLATE,
            "drill": index,
            "russian": drills[index]["russian"],
            "reference": drills[index]["english"],
            "focus": content.get("focus") or "",
        }
    if kind == KIND_PHRASE:
        gap = phrase_gap(content.get("phrase") or "", content.get("example") or "")
        if gap is not None:
            return {"type": EXERCISE_GAP, **gap}
    if kind == KIND_WORD:
        return {"type": EXERCISE_FLIP}
    return {"type": EXERCISE_SELF}


def phrase_gap(phrase: str, example: str) -> Optional[Dict[str, Any]]:
    """Blank the phrase out of its example, or None if it is not literally there.

    "research (verb)" is looked up as "research"; phrases with "+" or "/" are
    formulas, not text. An inflected form in the example ("business analysts"
    for "business analyst") is blanked whole and both spellings are accepted.
    """
    base = _PARENTHESES.sub("", phrase).strip()
    if not base or "+" in base or "/" in base or not example:
        return None
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(word) for word in base.split())
    pattern += r"(?:s|es|d|ed|ing)?(?!\w)"
    match = re.search(pattern, example, flags=re.IGNORECASE)
    if match is None:
        return None
    accept = [match.group(0)]
    if normalize_text(base) != normalize_text(match.group(0)):
        accept.append(base)
    return {"before": example[: match.start()], "after": example[match.end():], "accept": accept}


# ------------------------------------------------------------ exercise sets
SET_EXERCISE = "ai_set"  # attempts-log `exercise` of a finished set
# Topics a set can train: not delivery (spoken drills do that), and not the
# "other" catch-all, which is no single thing to write exercises about.
SET_EXCLUDED_TOPICS: frozenset = NON_RECALL_TOPICS | {curriculum.OTHER_TOPIC}


def set_topic_allowed(topic: str) -> bool:
    return curriculum.is_topic(topic) and topic not in SET_EXCLUDED_TOPICS


_NON_WORD = re.compile(r"[^\w'\s]", flags=re.UNICODE)


def normalize_answer(text: str) -> str:
    """Case, quotes, punctuation and spacing do not count; apostrophes do.

    The server-side twin of Drill.normalize in drill.js.
    """
    value = (text or "").translate(_QUOTE_CHARS).lower().replace("_", " ")
    return _WHITESPACE.sub(" ", _NON_WORD.sub(" ", value)).strip()


def answer_matches(answer: str, accepted: Iterable[str]) -> bool:
    value = normalize_answer(answer)
    return bool(value) and any(normalize_answer(candidate) == value for candidate in accepted)


def set_run_score(results: Sequence[Dict[str, Any]]) -> float:
    """Share of right answers in one run of a set (0..1)."""
    if not results:
        return 0.0
    return sum(1 for r in results if r.get("correct")) / len(results)


def items_from_set_run(
    exercise_set: Dict[str, Any], run: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Every wrong answer of a set run as a fix card (decided 2026-09-19).

    The card is the learner's own wrong sentence -> the right one, the same
    shape as a mistake from speech, so it goes through the same Leitner
    boxes and the same daily allowance of new cards:

      gap        the sentence with the learner's filler -> with the right one;
      fix        the set's faulty sentence -> its correction;
      translate  the learner's translation -> Claude's minimal correction of
                 it, with the reference as a "more natural" version, filed
                 under the topic Claude tagged the mistake with (2026-09-26;
                 the set's topic for runs graded before that).

    Only a translation has a Russian sentence to practise with, so only it
    becomes a live card (its drill is the set's own sentence); gap and fix
    mistakes stay in the bank as retired history, like pre-2026-09-26 fixes.
    Skipped (left blank) translations make no card: there is no sentence of
    the learner's to correct.
    """
    exercises = {e["id"]: e for e in _dicts(exercise_set.get("exercises"))}
    occurrence = {
        "set_id": exercise_set.get("id"),
        "set_run": f"{exercise_set.get('id')}@{run.get('at')}",
        "at": run.get("at"),
    }
    items: List[Dict[str, Any]] = []
    for result in _dicts(run.get("results")):
        exercise = exercises.get(result.get("exercise_id"))
        if exercise is None or result.get("correct"):
            continue
        answer = (result.get("answer") or "").strip()
        accept = [a for a in exercise.get("accept") or [] if a]
        explanation = exercise.get("explanation") or ""
        better: List[str] = []
        drills: List[Dict[str, str]] = []
        topic = exercise_set.get("topic")
        if exercise.get("type") == "gap" and accept:
            before, after = exercise.get("before", ""), exercise.get("after", "")
            quote = f"{before}{answer or '___'}{after}"
            correction = f"{before}{accept[0]}{after}"
        elif exercise.get("type") == "fix" and accept:
            quote, correction, better = exercise.get("sentence", ""), accept[0], accept[1:]
        elif exercise.get("type") == "translate" and answer:
            reference = exercise.get("reference") or ""
            corrected = (result.get("corrected") or "").strip()
            use_fix = corrected and normalize_answer(corrected) != normalize_answer(answer)
            quote, correction = answer, corrected if use_fix else reference
            if normalize_answer(reference) != normalize_answer(correction):
                better = [reference]
            explanation = result.get("comment") or ""
            drills = clean_drills([{"russian": exercise.get("russian"), "english": reference}])
            if curriculum.is_topic(result.get("topic")):
                topic = result["topic"]
        else:
            continue
        if not normalize_text(correction) or normalize_answer(quote) == normalize_answer(correction):
            continue
        items.append(
            {
                "id": item_id(KIND_FIX, correction),
                "kind": KIND_FIX,
                "language": exercise_set.get("language", "en"),
                "topic": topic,
                "severity": "moderate",
                "pattern_id": None,
                "origin": SET_EXERCISE,
                "content": {
                    "quote": quote,
                    "correction": correction,
                    "better_versions": better,
                    "explanation": explanation,
                    "focus": exercise.get("focus") or "",
                    "drills": drills,
                },
                "occurrences": [dict(occurrence)],
            }
        )
    return items


# ------------------------------------------------------------ daily workout
def activity_streak(active_days: Iterable[dt.date], today: dt.date) -> int:
    """Consecutive active days up to today.

    A day not yet active does not break the streak until it is over:
    in the morning the streak still counts up to yesterday.
    """
    days = set(active_days)
    day = today if today in days else today - dt.timedelta(days=1)
    streak = 0
    while day in days:
        streak += 1
        day -= dt.timedelta(days=1)
    return streak


def attempts_on(attempts: Sequence[Dict[str, Any]], day: dt.date) -> List[Dict[str, Any]]:
    return [a for a in attempts if _parse(a["ts"]).date() == day]


def daily_activity(attempts: Sequence[Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    """Exercise history per day, newest first, for the «История» tab.

    Cards are summed up (how many, how many right); topic drills are listed
    one by one with their score, since each is a whole text.
    """
    by_day: Dict[str, Dict[str, Any]] = {}
    for attempt in attempts:
        day = _parse(attempt["ts"]).date().isoformat()
        entry = by_day.setdefault(day, {"date": day, "cards": 0, "cards_correct": 0, "drills": []})
        if isinstance(attempt.get("item_id"), str):
            entry["cards"] += 1
            entry["cards_correct"] += 1 if attempt.get("correct") else 0
        else:
            entry["drills"].append(
                {
                    "topic": attempt.get("topic"),
                    "exercise": attempt.get("exercise"),
                    "score": round(attempt_score(attempt), 3),
                    "session_id": attempt.get("session_id"),
                    "set_id": attempt.get("set_id"),
                    "at": attempt["ts"],
                }
            )
    return sorted(by_day.values(), key=lambda entry: entry["date"], reverse=True)[:days]
