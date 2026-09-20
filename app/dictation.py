"""Listening dictation (Stage 7): subtitles in, checkable sentences out.

A dictation lesson is one YouTube video. Its reference text always comes from
the video's own subtitle track - manual captions first, YouTube's automatic
ones otherwise - so a lesson costs nothing: no Deepgram call, no Claude call.

This module is the pure half of the activity and does no I/O at all:

  * `parse_vtt` turns a WebVTT file into cues, dropping the inline karaoke
    tags and the rolling repetition automatic captions are full of;
  * `sentences_from_cues` glues the cues back into sentences that are worth
    typing (long enough to be a real dictation, short enough to hold in your
    head), each with the timestamps to play;
  * `tokenize` / `normalize_word` / `grade_sentence` decide what "typed it
    right" means - the same rules the browser applies while typing, so the
    live feedback and the stored result never disagree;
  * `tricky_words` and `lesson_progress` read the stored results back.

Like the subtitles themselves, a sentence is never rewritten: it is shown
exactly as the caption track spells it.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app import config

# "00:01:02.500 --> 00:01:05.000 align:start position:0%" - SRT's comma form
# is accepted too, since yt-dlp can hand back either.
_TIMING = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*"
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})"
)
# <00:00:01.000> word timings and <c>/<c.colorE5E5E5> styling inside a cue.
_TAG = re.compile(r"<[^>]*>")
# The word-timing tag on its own: automatic captions stamp when each word starts.
_INLINE_TIME = re.compile(r"<(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})>")
_WHITESPACE = re.compile(r"\s+")
# A word is letters/digits, optionally joined by an apostrophe or a hyphen
# ("don't", "e-mail"); every other character is its own punctuation token.
_TOKEN = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*|\S", re.UNICODE)
_SENTENCE_END = ".!?…"
# How far back to look for the tail an automatic caption cue repeats.
_OVERLAP_LOOKBACK = 40
# A cue stays on screen until the next one replaces it, so its end says little
# about when the last word of a line was finished: cap how long that word lasts.
_LAST_WORD_MAX_SECONDS = 1.5


@dataclass(frozen=True)
class TimedWord:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Cue:
    """One subtitle cue: what is said, and between which seconds.

    `words` carries the per-word timings when the file has them (automatic
    captions do); without them the words are spread over the cue's span.
    """

    start: float
    end: float
    text: str
    words: Tuple[TimedWord, ...] = ()


# ------------------------------------------------------------------ parsing
def _timestamp_seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    seconds = float(parts[-1])
    minutes = int(parts[-2]) if len(parts) > 1 else 0
    hours = int(parts[-3]) if len(parts) > 2 else 0
    return hours * 3600 + minutes * 60 + seconds


def _clean_cue_text(lines: Sequence[str]) -> str:
    text = " ".join(lines)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    return _WHITESPACE.sub(" ", text).strip()


def _spread_words(texts: Sequence[str], start: float, end: float) -> List[TimedWord]:
    """Words of an untimed cue, spread over its span in proportion to length.

    Better than giving every word the whole cue: a sentence that begins or
    ends in the middle of a cue then starts and stops near where it is said.
    """
    weights = [len(text) + 1 for text in texts]
    total = float(sum(weights)) or 1.0
    words: List[TimedWord] = []
    done = 0.0
    for text, weight in zip(texts, weights):
        word_start = start + (end - start) * done / total
        done += weight
        word_end = end if len(words) == len(texts) - 1 else start + (end - start) * done / total
        words.append(TimedWord(start=word_start, end=word_end, text=text))
    return words


def _timed_line_words(line: str, cue_start: float, cue_end: float) -> List[TimedWord]:
    """A line with `<00:00:01.000>` stamps: every word with its own start.

    The text before the first stamp starts with the cue; each stamp starts
    the words after it. A word ends where the next one begins.
    """
    parts = _INLINE_TIME.split(line)
    starts: List[Tuple[float, str]] = []
    for position in range(0, len(parts), 2):
        if position == 0:
            start = cue_start
        else:
            start = min(max(_timestamp_seconds(parts[position - 1]), cue_start), cue_end)
        for word in _clean_cue_text([parts[position]]).split(" "):
            if word:
                starts.append((start, word))
    words: List[TimedWord] = []
    for position, (start, word) in enumerate(starts):
        if position + 1 < len(starts):
            end = max(starts[position + 1][0], start)
        else:
            end = min(cue_end, start + _LAST_WORD_MAX_SECONDS)
        words.append(TimedWord(start=start, end=end, text=word))
    return words


def _is_separator(lines: Sequence[str], index: int) -> bool:
    """Does the cue end before line `index`? Only a truly empty line (or the
    next timing / an SRT counter) does: YouTube's files keep a line of a
    single space *inside* a cue, and the karaoke line comes right after it."""
    line = lines[index]
    if line == "" or _TIMING.search(line):
        return True
    return (
        line.strip().isdigit()
        and index + 1 < len(lines)
        and _TIMING.search(lines[index + 1]) is not None
    )


def parse_vtt(source: str) -> List[Cue]:
    """Every cue of a WebVTT (or SRT) file, in order, with its text cleaned."""
    cues: List[Cue] = []
    lines = (source or "").replace(chr(13) + chr(10), chr(10)).replace(chr(13), chr(10)).split(chr(10))
    index = 0
    while index < len(lines):
        match = _TIMING.search(lines[index])
        if match is None:
            index += 1
            continue
        start, end = (_timestamp_seconds(group) for group in match.groups())
        index += 1
        body: List[str] = []
        while index < len(lines) and not _is_separator(lines, index):
            if lines[index].strip():
                body.append(lines[index].strip())
            index += 1
        text = _clean_cue_text(body)
        if not text or end <= start:
            continue
        words: Tuple[TimedWord, ...] = ()
        if any(_INLINE_TIME.search(line) for line in body):
            collected: List[TimedWord] = []
            for line in body:
                if _INLINE_TIME.search(line):
                    collected.extend(_timed_line_words(line, start, end))
                else:  # the previous line, repeated: it has no stamps of its own
                    collected.extend(
                        TimedWord(start=start, end=end, text=word)
                        for word in _clean_cue_text([line]).split(" ")
                        if word
                    )
            words = tuple(collected)
        cues.append(Cue(start=start, end=end, text=text, words=words))
    return cues


def cue_words(cues: Sequence[Cue]) -> List[TimedWord]:
    """The cues as one word stream, without the repetition of rolling captions.

    YouTube's automatic captions scroll: each cue repeats the tail of the
    previous one and adds a word or two. Every cue is therefore trimmed by
    the longest prefix that is already the end of what we have - which also
    removes the exact duplicate cues the same format produces.
    """
    words: List[TimedWord] = []
    normalized: List[str] = []
    for cue in cues:
        fresh = list(cue.words) or _spread_words(
            [word for word in cue.text.split(" ") if word], cue.start, cue.end
        )
        keys = [normalize_word(word.text) for word in fresh]
        tail = normalized[-_OVERLAP_LOOKBACK:]
        overlap = 0
        for size in range(min(len(keys), len(tail)), 0, -1):
            if keys[:size] == tail[-size:]:
                overlap = size
                break
        for word, key in zip(fresh[overlap:], keys[overlap:]):
            words.append(word)
            normalized.append(key)
    return words


# ---------------------------------------------------------------- sentences
def _flush(
    buffer: Sequence[TimedWord], index: int, pad: float
) -> Dict[str, Any]:
    text = " ".join(word.text for word in buffer)
    return {
        "index": index,
        "text": text,
        "words": sum(1 for token in tokenize(text) if token["word"]),
        "start": round(max(0.0, buffer[0].start - pad), 3),
        "end": round(buffer[-1].end + pad, 3),
    }


def _split_point(buffer: Sequence[TimedWord], min_words: int) -> int:
    """Where to cut an over-long run: after the last comma, else at the cap."""
    for position in range(len(buffer) - 1, min_words - 1, -1):
        if buffer[position - 1].text.endswith((",", ";", ":", "—", "-")):
            return position
    return len(buffer)


def sentences_from_cues(
    cues: Sequence[Cue],
    *,
    min_words: int = config.DICTATION_MIN_WORDS,
    max_words: int = config.DICTATION_MAX_WORDS,
    pad: float = config.DICTATION_PAD_SECONDS,
) -> List[Dict[str, Any]]:
    """Cues merged into dictation-sized sentences with their play window.

    Sentence-ending punctuation decides the break where the captions have
    any (manual tracks usually do); automatic captions rarely do, so a run
    longer than `max_words` is cut at a comma or, failing that, at the cap.
    """
    result: List[Dict[str, Any]] = []
    buffer: List[TimedWord] = []
    for word in cue_words(cues):
        buffer.append(word)
        ends_sentence = word.text.rstrip("\"'»)").endswith(tuple(_SENTENCE_END))
        if ends_sentence and len(buffer) >= min_words:
            result.append(_flush(buffer, len(result), pad))
            buffer = []
        elif len(buffer) >= max_words:
            cut = _split_point(buffer, min_words)
            result.append(_flush(buffer[:cut], len(result), pad))
            buffer = buffer[cut:]
    if buffer:
        if len(buffer) < min_words and result:
            # Too short to stand alone: hand it to the sentence before it.
            previous = result.pop()
            text = f"{previous['text']} {' '.join(word.text for word in buffer)}"
            result.append(
                {
                    "index": previous["index"],
                    "text": text,
                    "words": sum(1 for token in tokenize(text) if token["word"]),
                    "start": previous["start"],
                    "end": round(buffer[-1].end + pad, 3),
                }
            )
        else:
            result.append(_flush(buffer, len(result), pad))
    return result


def lesson_sentences(subtitles: str, **kwargs: Any) -> List[Dict[str, Any]]:
    """`parse_vtt` + `sentences_from_cues`, the way an import uses them."""
    return sentences_from_cues(parse_vtt(subtitles), **kwargs)


# ----------------------------------------------------------------- checking
def normalize_word(value: str) -> str:
    """What a typed word is compared against: letters and digits, lowercased.

    Case and punctuation are ignored on purpose - a dictation trains hearing
    and spelling, not the caption track's typography, so "dont" passes for
    "don't" and "Hello," for "hello".
    """
    return "".join(char for char in (value or "").lower() if char.isalnum())


def tokenize(text: str) -> List[Dict[str, Any]]:
    """The sentence as tokens; only `word` ones are typed, the rest are shown."""
    tokens: List[Dict[str, Any]] = []
    for match in _TOKEN.finditer(text or ""):
        value = match.group(0)
        tokens.append({"text": value, "word": bool(normalize_word(value))})
    return tokens


def sentence_words(text: str) -> List[str]:
    return [token["text"] for token in tokenize(text) if token["word"]]


def is_prefix(answer: str, target: str) -> bool:
    """Is what has been typed so far still on the way to the right word?"""
    return normalize_word(target).startswith(normalize_word(answer))


def grade_sentence(
    text: str, answers: Sequence[str], hinted: Iterable[int] = ()
) -> Dict[str, Any]:
    """One dictated sentence: which words were right, wrong or hinted.

    A hinted word that was then typed still counts as correct - the hint is
    recorded separately, because needing it is what makes a word "tricky".
    """
    words = sentence_words(text)
    hints = {index for index in hinted if 0 <= index < len(words)}
    correct: List[str] = []
    incorrect: List[str] = []
    hinted_words: List[str] = []
    for index, word in enumerate(words):
        answer = answers[index] if index < len(answers) else ""
        if index in hints:
            hinted_words.append(word)
        if normalize_word(answer) == normalize_word(word):
            correct.append(word)
        else:
            incorrect.append(word)
    clean = [word for index, word in enumerate(words) if index not in hints]
    return {
        "total_words": len(words),
        "correct_words": correct,
        "incorrect_words": incorrect,
        "hint_words": hinted_words,
        "accuracy": round(len(correct) / len(words), 3) if words else 0.0,
        "completed": len(correct) == len(words) and bool(words),
        "clean": len(correct) == len(words) and len(clean) == len(words),
    }


# ------------------------------------------------------------------ reading
def _record_day(record: Dict[str, Any]) -> Optional[dt.date]:
    try:
        return dt.datetime.fromisoformat(record["ts"]).date()
    except (KeyError, TypeError, ValueError):
        return None


def latest_by_sentence(records: Sequence[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """The last stored attempt per sentence - what the lesson page shows."""
    latest: Dict[int, Dict[str, Any]] = {}
    for record in records:
        index = record.get("sentence")
        if isinstance(index, int):
            latest[index] = record
    return latest


def lesson_progress(sentence_count: int, records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """How far through a lesson the learner is, and how clean it went."""
    latest = latest_by_sentence(records)
    done = [record for record in latest.values() if record.get("completed")]
    total_words = sum(int(record.get("total_words") or 0) for record in latest.values())
    correct = sum(len(record.get("correct_words") or ()) for record in latest.values())
    hints = sum(len(record.get("hint_words") or ()) for record in latest.values())
    next_index = 0
    for index in range(sentence_count):
        if index not in latest or not latest[index].get("completed"):
            next_index = index
            break
    else:
        next_index = max(0, sentence_count - 1)
    return {
        "sentences": sentence_count,
        "done": len(done),
        "started": len(latest),
        "accuracy": round(correct / total_words, 3) if total_words else None,
        "hints": hints,
        "next_index": next_index,
        "last_at": max((record.get("ts") for record in records), default=None),
    }


def tricky_words(
    records: Sequence[Dict[str, Any]],
    *,
    min_misses: int = config.DICTATION_TRICKY_MIN_MISSES,
    limit: int = config.DICTATION_TRICKY_LIMIT,
) -> List[Dict[str, Any]]:
    """Words that keep going wrong: missed or needing a hint, worst first.

    Aggregated over every stored attempt (not just the last one per
    sentence): needing the same word twice is exactly the signal.
    """
    stats: Dict[str, Dict[str, Any]] = {}

    def entry(word: str) -> Optional[Dict[str, Any]]:
        key = normalize_word(word)
        if not key:
            return None
        return stats.setdefault(
            key, {"word": word, "key": key, "seen": 0, "missed": 0, "hinted": 0}
        )

    for record in records:
        for word in record.get("correct_words") or ():
            row = entry(word)
            if row is not None:
                row["seen"] += 1
        for word in record.get("incorrect_words") or ():
            row = entry(word)
            if row is not None:
                row["seen"] += 1
                row["missed"] += 1
        for word in record.get("hint_words") or ():
            row = entry(word)
            if row is not None:
                row["hinted"] += 1
    rows = [
        {**row, "misses": row["missed"] + row["hinted"]}
        for row in stats.values()
        if row["missed"] + row["hinted"] >= min_misses
    ]
    rows.sort(key=lambda row: (-row["misses"], -row["missed"], row["key"]))
    return rows[:limit]


def daily_counts(records: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Sentences dictated per day (ISO date -> counts), for «История»."""
    by_day: Dict[str, Dict[str, Any]] = {}
    for record in records:
        day = _record_day(record)
        if day is None:
            continue
        entry = by_day.setdefault(
            day.isoformat(), {"date": day.isoformat(), "sentences": 0, "words": 0, "correct": 0}
        )
        entry["sentences"] += 1 if record.get("completed") else 0
        entry["words"] += int(record.get("total_words") or 0)
        entry["correct"] += len(record.get("correct_words") or ())
    for entry in by_day.values():
        entry["accuracy"] = round(entry["correct"] / entry["words"], 3) if entry["words"] else None
    return by_day


def active_days(records: Sequence[Dict[str, Any]]) -> set:
    return {day for day in (_record_day(record) for record in records) if day is not None}


def done_on(records: Sequence[Dict[str, Any]], day: dt.date) -> int:
    """Sentences finished on one day - the daily workout's dictation step."""
    return sum(
        1 for record in records if record.get("completed") and _record_day(record) == day
    )
