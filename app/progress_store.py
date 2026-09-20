"""Cross-session progress tracking: which topics keep coming up, and how badly.

Each analysis (see app/analyzer.py) tags every mistake with a topic from the
fixed TOPIC_TAXONOMY below. This module aggregates those tags across every
session into data/progress.json, so the app can answer "what should I
practice?" without re-reading every analysis.json on every request.

It also keeps the per-skill score history (grammar / vocabulary / fluency /
naturalness) of every analysed recording, for the score charts.

data/progress.json is a derived cache, not a second source of truth: the
per-session analysis.json files remain authoritative, and rebuild_from_sessions()
regenerates progress.json from them after every analysis (and whenever it is
lost, corrupted or from an older schema).
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

# v2: score_history added; topic dates are when the speech was recorded, and
# the whole file is rebuilt from analysis.json files on every analysis.
SCHEMA_VERSION = 2

# Recency half-life for the weakness score: a topic last seen this many days
# ago counts for half as much as one seen today, regardless of its raw count.
_RECENCY_HALF_LIFE_DAYS = 14.0
# Cap on how many contributing session ids are kept per topic (oldest first
# out) - this is a "what's driving this" pointer, not a full audit log.
_MAX_SESSION_IDS_PER_TOPIC = 30

_SKILLS = ("grammar", "vocabulary", "fluency", "naturalness")

_lock = threading.Lock()


def _empty_progress() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": dt.datetime.now().isoformat(),
        "topics": {},
        "score_history": [],
    }


def load_progress() -> Dict[str, Any]:
    """The aggregate store; rebuilt first if missing, unreadable or from an older schema."""
    path = config.data_dir() / config.PROGRESS_FILENAME
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("schema_version") == SCHEMA_VERSION:
                return data
        except (OSError, ValueError):
            logger.warning("Could not read %s; rebuilding it", path)
    return rebuild_from_sessions()


def save_progress(data: Dict[str, Any]) -> Path:
    directory = config.data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = dt.datetime.now().isoformat()
    return utils.write_json(directory / config.PROGRESS_FILENAME, data)


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

    This is how progress.json is always produced: the per-session
    analysis.json files are the real source of truth, and a full rebuild
    makes a forced re-analysis replace that session's counts and scores
    instead of adding to them.
    """
    data = _empty_progress()
    for session in utils.iter_analysed_sessions(recordings_root):
        when = session.recorded_at.isoformat()
        for topic, count in (session.analysis.get("topic_counts") or {}).items():
            if not count:
                continue
            entry = data["topics"].setdefault(
                topic, {"count": 0, "first_seen": when, "last_seen": when, "session_ids": []}
            )
            entry["count"] += count
            entry["last_seen"] = max(entry["last_seen"], when)
            entry["first_seen"] = min(entry["first_seen"], when)
            if session.session_id not in entry["session_ids"]:
                entry["session_ids"].append(session.session_id)
                entry["session_ids"] = entry["session_ids"][-_MAX_SESSION_IDS_PER_TOPIC:]
        history_entry = _score_history_entry(session)
        if history_entry is not None:
            data["score_history"].append(history_entry)

    with _lock:
        save_progress(data)
    return data


def _score_history_entry(session: utils.AnalysedSession) -> Optional[Dict[str, Any]]:
    """Per-skill scores of one recording; None for v1 analyses, which have none."""
    scores = session.analysis.get("scores")
    if not isinstance(scores, dict):
        return None
    entry: Dict[str, Any] = {
        "session_id": session.session_id,
        "at": session.recorded_at.isoformat(),
        "language": session.language,
    }
    # A typed text has no delivery, so its fluency score is not comparable
    # with spoken takes and stays out of the history.
    typed = session.analysis.get("input_mode") == config.INPUT_TEXT
    for skill in _SKILLS:
        value = scores.get(skill)
        skipped = typed and skill == "fluency"
        entry[skill] = value.get("score") if isinstance(value, dict) and not skipped else None
    entry["overall"] = session.analysis.get("overall_score")
    return entry


if __name__ == "__main__":  # pragma: no cover - operational tool, not a test path
    import sys

    if "--rebuild" not in sys.argv[1:]:
        print("Usage: python -m app.progress_store --rebuild")
        sys.exit(1)
    result = rebuild_from_sessions()
    print(
        f"Rebuilt progress.json with {len(result['topics'])} topic(s) and "
        f"{len(result['score_history'])} scored session(s)."
    )
