"""Spoken drills (Stage 6): what a take sounded like, from Deepgram's word timings.

Both drills cost only the Deepgram call - nothing here goes to Claude:

  * «60 секунд» (talk): a minute on one prompt, three times in a row. Measured
    are words per minute, fillers and long pauses per minute - all counted from
    the verbatim transcript's words and their start/end times, so the app still
    never decodes audio itself.
  * shadowing: a passage of the learner's own improved_version read aloud,
    then aligned word by word with what Deepgram heard - missed, misheard and
    unclear (low-confidence) words are marked.

Everything is derived on read from deepgram_response.json; the transcript
itself is never touched. Like app.learner_model, this module does no I/O.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Optional, Sequence

from app import config

_UNIT = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)*")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_NOT_WORD = re.compile(r"[^a-z0-9']")


def _norm(word: str) -> str:
    return _NOT_WORD.sub("", (word or "").replace("’", "'").lower())


def _bare(word: Dict[str, Any]) -> str:
    """A heard word as said, without the sentence punctuation Deepgram added."""
    return word["text"].strip(".,!?;:…\"")


# ------------------------------------------------------------------ words
def response_words(payload: Any) -> List[Dict[str, Any]]:
    """The timed words of a Deepgram response, or [] if it has none."""
    try:
        words = payload["results"]["channels"][0]["alternatives"][0]["words"]
    except (KeyError, IndexError, TypeError):
        return []
    result = []
    for word in words if isinstance(words, list) else []:
        try:
            result.append(
                {
                    "word": str(word["word"]),
                    "text": str(word.get("punctuated_word") or word["word"]),
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                    "confidence": float(word.get("confidence", 1.0)),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return result


def is_filler(word: Dict[str, Any]) -> bool:
    return _norm(word["word"]) in config.FILLER_TOKENS


# ---------------------------------------------------------------- metrics
def speech_metrics(words: Sequence[Dict[str, Any]], fillers_detected: bool) -> Dict[str, Any]:
    """Pace and hesitations of one take.

    `fillers_detected` is whether the take was transcribed with filler_words
    (English only): without it Deepgram drops "uh"/"um", so a filler count
    would read as a perfect 0 - it is reported as None instead, and no
    fluency score is given.

    Speaking time runs from the first word to the last, so silence before
    the first word and after the last one does not lower the pace.
    """
    fillers = [w for w in words if is_filler(w)] if fillers_detected else []
    content = [w for w in words if not (fillers_detected and is_filler(w))]
    pauses = [
        round(nxt["start"] - prev["end"], 2)
        for prev, nxt in zip(words, words[1:])
        if nxt["start"] - prev["end"] >= config.LONG_PAUSE_SECONDS
    ]
    repeats = sum(
        1
        for prev, nxt in zip(content, content[1:])
        if _norm(prev["word"]) and _norm(prev["word"]) == _norm(nxt["word"])
    )
    seconds = max(0.0, words[-1]["end"] - words[0]["start"]) if words else 0.0
    minutes = seconds / 60.0

    def per_minute(count: int) -> Optional[float]:
        return round(count / minutes, 1) if minutes >= 0.05 else None

    breakdown: Dict[str, int] = {}
    for word in fillers:
        key = _norm(word["word"])
        breakdown[key] = breakdown.get(key, 0) + 1

    hesitations = per_minute(len(fillers) + len(pauses))
    return {
        "speaking_seconds": round(seconds, 1),
        "words": len(content),
        "wpm": per_minute(len(content)),
        "fillers": len(fillers) if fillers_detected else None,
        "fillers_per_min": per_minute(len(fillers)) if fillers_detected else None,
        "filler_breakdown": breakdown if fillers_detected else None,
        "long_pauses": len(pauses),
        "longest_pause": max(pauses) if pauses else None,
        "pauses_per_min": per_minute(len(pauses)),
        "repeats": repeats,
        "hesitations_per_min": hesitations,
        "fluency_score": (
            fluency_score(hesitations) if fillers_detected and hesitations is not None else None
        ),
    }


def fluency_score(hesitations_per_min: float) -> float:
    """0..1 from fillers + long pauses per minute (see config for the scale)."""
    target, zero = config.FLUENCY_TARGET_PER_MIN, config.FLUENCY_ZERO_PER_MIN
    return round(min(1.0, max(0.0, 1.0 - (hesitations_per_min - target) / (zero - target))), 3)


def timeline(words: Sequence[Dict[str, Any]], fillers_detected: bool) -> List[Dict[str, Any]]:
    """The transcript as tokens for display: fillers flagged, long pauses in place."""
    tokens: List[Dict[str, Any]] = []
    for index, word in enumerate(words):
        if index:
            gap = word["start"] - words[index - 1]["end"]
            if gap >= config.LONG_PAUSE_SECONDS:
                tokens.append({"pause": round(gap, 1)})
        token: Dict[str, Any] = {"text": word["text"]}
        if fillers_detected and is_filler(word):
            token["filler"] = True
        tokens.append(token)
    return tokens


# -------------------------------------------------------------- shadowing
def _word_count(text: str) -> int:
    return len(_UNIT.findall(text or ""))


def split_passages(text: str) -> List[str]:
    """Whole sentences grouped into passages of about MIN..MAX words.

    A sentence longer than MAX stays whole (it is never cut mid-sentence);
    a short tail is merged into the passage before it.
    """
    sentences = [s.strip() for s in _SENTENCE_END.split((text or "").strip()) if s.strip()]
    passages: List[List[str]] = []
    current: List[str] = []
    for sentence in sentences:
        size = sum(_word_count(s) for s in current)
        if current and size >= config.SHADOWING_MIN_WORDS and (
            size + _word_count(sentence) > config.SHADOWING_MAX_WORDS
        ):
            passages.append(current)
            current = []
        current.append(sentence)
    if current:
        if passages and sum(_word_count(s) for s in current) < config.SHADOWING_MIN_WORDS:
            passages[-1].extend(current)
        else:
            passages.append(current)
    return [" ".join(p) for p in passages]


def align_reading(reference: str, words: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Compare a passage with what was heard when it was read aloud.

    Returns display segments - the reference text with each word marked
    ok / unclear / wrong (with what was heard) / missed, plus extra words the
    reader added - and `score`, the share of reference words read right
    (unclear ones count as right: they were heard, just not surely).
    Words with digits are not scored: without smart_format, "2024" comes back
    as "twenty twenty four".
    """
    units = list(_UNIT.finditer(reference or ""))
    scored = [i for i, m in enumerate(units) if not any(ch.isdigit() for ch in m.group(0))]
    heard = [w for w in words if not is_filler(w) and _norm(w["word"])]
    matcher = difflib.SequenceMatcher(
        None,
        [_norm(units[i].group(0)) for i in scored],
        [_norm(w["word"]) for w in heard],
        autojunk=False,
    )

    status: Dict[int, Dict[str, Any]] = {}
    extras_before: Dict[int, List[str]] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                word = heard[j1 + k]
                unclear = word["confidence"] < config.UNCLEAR_CONFIDENCE
                status[scored[i1 + k]] = {"status": "unclear" if unclear else "ok"}
        elif tag in ("replace", "delete"):
            for k in range(i2 - i1):
                if tag == "replace" and j1 + k < j2:
                    status[scored[i1 + k]] = {"status": "wrong", "heard": _bare(heard[j1 + k])}
                else:
                    status[scored[i1 + k]] = {"status": "missed"}
            surplus = [_bare(w) for w in heard[j1 + (i2 - i1) : j2]] if tag == "replace" else []
            if surplus:
                extras_before.setdefault(scored[i2 - 1] + 1, []).extend(surplus)
        else:  # insert
            anchor = scored[i1] if i1 < len(scored) else len(units)
            extras_before.setdefault(anchor, []).extend(_bare(w) for w in heard[j1:j2])

    segments: List[Dict[str, Any]] = []
    position = 0
    for index, match in enumerate(units):
        if index in extras_before:
            segments.append({"extra": " ".join(extras_before[index])})
        if match.start() > position:
            segments.append({"text": reference[position : match.start()]})
        segments.append({"word": match.group(0), **status.get(index, {"status": "skip"})})
        position = match.end()
    if len(units) in extras_before:
        segments.append({"extra": " ".join(extras_before[len(units)])})
    if position < len(reference or ""):
        segments.append({"text": reference[position:]})

    counts = {key: 0 for key in ("ok", "unclear", "wrong", "missed")}
    for entry in status.values():
        counts[entry["status"]] += 1
    right = counts["ok"] + counts["unclear"]
    return {
        "segments": segments,
        "scored_words": len(scored),
        **counts,
        "extra": sum(len(v) for v in extras_before.values()),
        "score": round(right / len(scored), 3) if scored else 0.0,
    }


def pick_passage(passages: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The passage to shadow next: never read ones first (in the given order,
    newest recording first), then the one with the lowest best score."""
    fresh = [p for p in passages if not p.get("attempts")]
    if fresh:
        return fresh[0]
    if not passages:
        return None
    return min(passages, key=lambda p: (p.get("best_score") or 0.0, p.get("attempts", 0)))
