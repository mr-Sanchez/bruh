"""Cross-session progress tracking: which topics keep coming up, and how badly.

Each analysis (see app/analyzer.py) tags every mistake with a topic from the
fixed TOPIC_TAXONOMY below. This module aggregates those tags across every
session into data/progress.json, so the app can answer "what should I
practice?" without re-reading every analysis.json on every request.

data/progress.json is a derived cache, not a second source of truth: the
per-session analysis.json files remain authoritative, and rebuild_from_sessions()
can always regenerate progress.json from them if it is ever lost or corrupted.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import config

logger = logging.getLogger(__name__)

# Fixed, closed set of topics. Changing this later means a coordinated
# prompt/schema/progress.json migration (see schema_version fields).
TOPIC_TAXONOMY: Dict[str, Dict[str, str]] = {
    "verb_tense": {
        "label": "Видо-временные формы глагола",
        "description": "неправильное время или вид глагола (например, went vs go)",
    },
    "articles": {
        "label": "Артикли (a / an / the)",
        "description": "пропущенный, лишний или неверно выбранный артикль",
    },
    "word_order": {
        "label": "Порядок слов",
        "description": "неверный порядок слов в предложении",
    },
    "prepositions": {
        "label": "Предлоги",
        "description": "неверно выбранный или пропущенный предлог",
    },
    "subject_verb_agreement": {
        "label": "Согласование подлежащего и сказуемого",
        "description": "рассогласование числа/лица между подлежащим и глаголом",
    },
    "lexical_choice": {
        "label": "Выбор слов / ложные друзья переводчика",
        "description": "неверно выбранное слово, калька или ложный друг переводчика",
    },
    "sentence_structure": {
        "label": "Структура предложения",
        "description": "незаконченные, рубленые или запутанные предложения",
    },
    "filler_words_fluency": {
        "label": "Слова-паразиты и беглость речи",
        "description": "частые слова-паразиты, паузы и запинки, мешающие беглости",
    },
    "repetition_self_correction": {
        "label": "Повторы и самокоррекции",
        "description": "повторение слов/фраз, частые самокоррекции по ходу речи",
    },
    "register_naturalness": {
        "label": "Естественность речи",
        "description": "грамматически верно, но неестественно звучит для носителя языка",
    },
    "other": {
        "label": "Прочее",
        "description": "любая другая проблема, не подходящая под перечисленные темы",
    },
}

TOPIC_KEYS: tuple = tuple(TOPIC_TAXONOMY.keys())

SCHEMA_VERSION = 1

# Recency half-life for the weakness score: a topic last seen this many days
# ago counts for half as much as one seen today, regardless of its raw count.
_RECENCY_HALF_LIFE_DAYS = 14.0
# Cap on how many contributing session ids are kept per topic (oldest first
# out) - this is a "what's driving this" pointer, not a full audit log.
_MAX_SESSION_IDS_PER_TOPIC = 30

_lock = threading.Lock()


def _empty_progress() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": dt.datetime.now().isoformat(),
        "topics": {},
    }


def load_progress() -> Dict[str, Any]:
    path = config.data_dir() / config.PROGRESS_FILENAME
    if not path.is_file():
        return _empty_progress()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read %s; starting from an empty progress store", path)
        return _empty_progress()
    data.setdefault("topics", {})
    return data


def save_progress(data: Dict[str, Any]) -> Path:
    directory = config.data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / config.PROGRESS_FILENAME
    data["updated_at"] = dt.datetime.now().isoformat()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def record_analysis(
    session_id: str, topic_counts: Dict[str, int], when: Optional[dt.datetime] = None
) -> Dict[str, Any]:
    """Fold one session's topic counts into the aggregate store. Thread-safe."""
    when = when or dt.datetime.now()
    with _lock:
        data = load_progress()
        topics = data["topics"]
        for topic, count in topic_counts.items():
            if not count:
                continue
            entry = topics.setdefault(
                topic,
                {"count": 0, "first_seen": when.isoformat(), "last_seen": when.isoformat(), "session_ids": []},
            )
            entry["count"] += count
            entry["last_seen"] = when.isoformat()
            entry.setdefault("first_seen", when.isoformat())
            if session_id not in entry["session_ids"]:
                entry["session_ids"].append(session_id)
                entry["session_ids"] = entry["session_ids"][-_MAX_SESSION_IDS_PER_TOPIC:]
        save_progress(data)
        return data


def compute_weakness_score(entry: Dict[str, Any], now: Optional[dt.datetime] = None) -> float:
    """Recency-decayed frequency: recent, frequent topics score highest."""
    now = now or dt.datetime.now()
    try:
        last_seen = dt.datetime.fromisoformat(entry["last_seen"])
    except (KeyError, ValueError):
        return 0.0
    days_since = max(0.0, (now - last_seen).total_seconds() / 86400.0)
    decay = 0.5 ** (days_since / _RECENCY_HALF_LIFE_DAYS)
    return float(entry.get("count", 0)) * decay


def top_weak_topics(n: int = 10, now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    """Topics ranked by weakness score, richest first - the Progress view read-model."""
    now = now or dt.datetime.now()
    data = load_progress()
    ranked = []
    for key, entry in data["topics"].items():
        taxonomy_entry = TOPIC_TAXONOMY.get(key, {"label": key, "description": ""})
        ranked.append(
            {
                "key": key,
                "label": taxonomy_entry["label"],
                "count": entry.get("count", 0),
                "last_seen": entry.get("last_seen"),
                "first_seen": entry.get("first_seen"),
                "weakness_score": round(compute_weakness_score(entry, now), 4),
                "session_ids": entry.get("session_ids", []),
            }
        )
    ranked.sort(key=lambda item: item["weakness_score"], reverse=True)
    return ranked[:n]


def rebuild_from_sessions(recordings_root: Optional[Path] = None) -> Dict[str, Any]:
    """Recompute progress.json from every recordings/*/analysis.json on disk.

    Safety net if progress.json is ever lost or gets out of sync - the
    per-session analysis.json files are the real source of truth.
    """
    root = recordings_root or config.recordings_dir()
    data = _empty_progress()
    if not root.is_dir():
        with _lock:
            save_progress(data)
        return data

    for directory in sorted(root.iterdir()):
        analysis_path = directory / config.ANALYSIS_FILENAME
        if not analysis_path.is_file():
            continue
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Skipping unreadable analysis file: %s", analysis_path)
            continue
        topic_counts = analysis.get("topic_counts") or {}
        created_at = analysis.get("created_at")
        try:
            when = dt.datetime.fromisoformat(created_at) if created_at else dt.datetime.now()
        except ValueError:
            when = dt.datetime.now()
        for topic, count in topic_counts.items():
            if not count:
                continue
            entry = data["topics"].setdefault(
                topic,
                {"count": 0, "first_seen": when.isoformat(), "last_seen": when.isoformat(), "session_ids": []},
            )
            entry["count"] += count
            entry["last_seen"] = max(entry["last_seen"], when.isoformat())
            entry["first_seen"] = min(entry["first_seen"], when.isoformat())
            if directory.name not in entry["session_ids"]:
                entry["session_ids"].append(directory.name)
                entry["session_ids"] = entry["session_ids"][-_MAX_SESSION_IDS_PER_TOPIC:]

    with _lock:
        save_progress(data)
    return data


if __name__ == "__main__":  # pragma: no cover - operational tool, not a test path
    import sys

    if "--rebuild" not in sys.argv[1:]:
        print("Usage: python -m app.progress_store --rebuild")
        sys.exit(1)
    result = rebuild_from_sessions()
    print(f"Rebuilt progress.json with {len(result['topics'])} topic(s).")
