"""The ONLY reader/writer of «Перевод текста» files: data/translate/<id>.json.

One file per text: the text itself (Claude's, or pasted by the learner),
where it came from, and every translation the learner submitted with its
review. Reviews are paid for and cannot be rebuilt, so a file is only ever
added to - a new attempt is appended, nothing is rewritten away. The text of
a translation is stored before Claude is asked, so a failed call never loses
it.

Useful phrases of a review are word card candidates; which ones became cards
lives in the shared pick log (learner_store.save_word_picks, keyed by the
text id), not here. Translations stay out of attempts.jsonl and the item
bank otherwise - like dictation, they are not taxonomy mistakes.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import config, utils

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
ORIGIN_GENERATED = "generated"
ORIGIN_CUSTOM = "custom"
ID_PATTERN = r"^txt-[0-9]{8}-[0-9]{6}(-[0-9]{1,3})?$"

_lock = threading.Lock()


def _path(text_id: str) -> Path:
    return config.texts_dir() / f"{text_id}.json"


def new_text_id(now: Optional[dt.datetime] = None) -> str:
    """txt-YYYYMMDD-HHMMSS, with a suffix if two texts land in one second."""
    base = f"txt-{(now or dt.datetime.now()).strftime('%Y%m%d-%H%M%S')}"
    candidate, suffix = base, 1
    while _path(candidate).exists():
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def save_text(document: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        config.texts_dir().mkdir(parents=True, exist_ok=True)
        utils.write_json(_path(document["id"]), document)
    return document


def load_text(text_id: str) -> Optional[Dict[str, Any]]:
    """A text by id (the caller validates it against ID_PATTERN), or None."""
    path = _path(text_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read translation text %s", path)
        return None
    return data if isinstance(data, dict) and data.get("id") == text_id else None


def list_texts() -> List[Dict[str, Any]]:
    """Every readable text, newest first."""
    directory = config.texts_dir()
    if not directory.is_dir():
        return []
    texts = []
    for path in sorted(directory.glob("txt-*.json"), reverse=True):
        data = load_text(path.stem)
        if data is not None:
            texts.append(data)
    return texts


def add_attempt(text_id: str, attempt: Dict[str, Any]) -> Dict[str, Any]:
    """Append a translation (with its review, or None) and return it numbered."""
    with _lock:
        document = load_text(text_id)
        if document is None:
            raise KeyError(text_id)
        attempts = document.setdefault("attempts", [])
        record = {
            "n": len(attempts) + 1,
            "at": dt.datetime.now().isoformat(timespec="seconds"),
            **attempt,
        }
        attempts.append(record)
        utils.write_json(_path(text_id), document)
    return record


def next_attempt_number(document: Dict[str, Any]) -> int:
    return len(document.get("attempts") or []) + 1


def latest_attempt(document: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    attempts = document.get("attempts") or []
    return attempts[-1] if attempts else None


def latest_review(document: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The newest attempt that has a review."""
    return next((a for a in reversed(document.get("attempts") or []) if a.get("review")), None)


def vocabulary(document: Dict[str, Any]) -> List[Dict[str, str]]:
    """Every phrase any review of the text suggested (ids are unique per attempt)."""
    return [
        phrase
        for attempt in document.get("attempts") or []
        for phrase in attempt.get("phrases") or []
    ]


def avoid_titles(theme: Optional[Dict[str, Optional[str]]]) -> List[str]:
    """Titles of earlier generated texts in the same context, newest first."""
    key = (theme or {}).get("key")
    label = (theme or {}).get("label")
    titles = []
    for document in list_texts():
        other = document.get("theme") or {}
        if document.get("origin") != ORIGIN_GENERATED:
            continue
        if (key and other.get("key") == key) or (not key and other.get("label") == label):
            titles.append(document.get("title", ""))
    return [t for t in titles if t][: config.TEXT_AVOID_TITLES]


def text_summary(document: Dict[str, Any]) -> Dict[str, Any]:
    """A text without its body and attempts, for the list."""
    reviewed = latest_review(document)
    attempts = document.get("attempts") or []
    return {
        "id": document["id"],
        "title": document.get("title", ""),
        "origin": document.get("origin"),
        "created_at": document.get("created_at"),
        "theme": document.get("theme"),
        "size": document.get("size"),
        "level": document.get("level"),
        "words": document.get("words"),
        "minutes": document.get("minutes"),
        "attempts": len(attempts),
        "last_accuracy": (reviewed or {}).get("review", {}).get("accuracy"),
        "last_at": attempts[-1].get("at") if attempts else None,
    }
