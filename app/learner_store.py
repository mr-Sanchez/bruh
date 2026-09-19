"""The learner model on disk: item bank, attempts log and usage log.

Every read and write of the learner model goes through this module, so a
later switch from flat files to SQLite stays local to it. The files:

  data/item_bank.json  derived cache, rebuilt from recordings/*/analysis.json
                       whenever an analysis changes (and on a schema bump);
  data/attempts.jsonl  append-only, AUTHORITATIVE - it cannot be rebuilt from
                       anything, so it is never regenerated or truncated;
  data/usage.jsonl     append-only log of paid API calls with estimated cost,
                       so the real price per exercise is visible.

The rules themselves (item identity, Leitner, topic mastery) live in
app.learner_model as pure functions.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import config, learner_model, progress_store, utils

logger = logging.getLogger(__name__)

BANK_SCHEMA_VERSION = 1
ATTEMPT_RECORD_VERSION = 1
USAGE_RECORD_VERSION = 1

_bank_lock = threading.Lock()
_attempts_lock = threading.Lock()
_usage_lock = threading.Lock()


def _bank_path() -> Path:
    return config.data_dir() / config.ITEM_BANK_FILENAME


def _attempts_path() -> Path:
    return config.data_dir() / config.ATTEMPTS_FILENAME


def _usage_path() -> Path:
    return config.data_dir() / config.USAGE_FILENAME


# ---------------------------------------------------------------- item bank
def rebuild_item_bank(recordings_root: Optional[Path] = None) -> Dict[str, Any]:
    """Regenerate item_bank.json from every analysis.json on disk."""
    sessions = list(utils.iter_analysed_sessions(recordings_root))
    data = {
        "schema_version": BANK_SCHEMA_VERSION,
        "updated_at": dt.datetime.now().isoformat(),
        # Every analysed recording, for the "has not come back in N
        # recordings" rule - kept here so state reads need no directory scan.
        "sessions": [
            {
                "session_id": s.session_id,
                "recorded_at": s.recorded_at.isoformat(),
                "language": s.language,
            }
            for s in sessions
        ],
        "items": learner_model.build_bank(sessions),
    }
    with _bank_lock:
        config.data_dir().mkdir(parents=True, exist_ok=True)
        utils.write_json(_bank_path(), data)
    return data


def load_item_bank() -> Dict[str, Any]:
    """The bank, rebuilt first if it is missing, unreadable or from an older schema."""
    path = _bank_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("schema_version") == BANK_SCHEMA_VERSION:
                return data
        except (OSError, ValueError):
            logger.warning("Could not read %s; rebuilding it", path)
    return rebuild_item_bank()


def refresh_after_analysis() -> None:
    """Re-derive every cache that depends on analysis.json files.

    A full rebuild rather than an incremental update: it is cheap at this
    scale and makes a forced re-analysis *replace* the session's earlier
    items and scores instead of counting them twice.
    """
    progress_store.rebuild_from_sessions()
    rebuild_item_bank()


# ----------------------------------------------------------------- attempts
def append_attempt(
    item_id: Optional[str],
    exercise: str,
    correct: bool,
    *,
    topic: Optional[str] = None,
    score: Optional[float] = None,
    session_id: Optional[str] = None,
    answer: Optional[str] = None,
    context: Optional[str] = None,
    when: Optional[dt.datetime] = None,
) -> Dict[str, Any]:
    """Log one answer. A card attempt names its item; a topic drill (a cloze
    over a whole text) names its topic instead and carries its share of right
    answers as `score`, plus the recording the text came from."""
    if not item_id and not topic:
        raise ValueError("An attempt needs an item_id or a topic.")
    record: Dict[str, Any] = {
        "v": ATTEMPT_RECORD_VERSION,
        "ts": (when or dt.datetime.now()).isoformat(),
        "exercise": exercise,
        "correct": bool(correct),
    }
    optional = {
        "item_id": item_id,
        "topic": topic,
        "score": None if score is None else round(score, 3),
        "session_id": session_id,
        "answer": answer,
        "context": context,
    }
    record.update({key: value for key, value in optional.items() if value is not None})
    with _attempts_lock:
        utils.append_jsonl(_attempts_path(), record)
    return record


def load_attempts() -> List[Dict[str, Any]]:
    return [
        record
        for record in utils.read_jsonl(_attempts_path())
        if isinstance(record.get("ts"), str)
        and (isinstance(record.get("item_id"), str) or isinstance(record.get("topic"), str))
    ]


# ----------------------------------------------------------- derived state
def item_states(
    bank: Optional[Dict[str, Any]] = None, attempts: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, learner_model.ItemState]:
    bank = bank if bank is not None else load_item_bank()
    attempts = attempts if attempts is not None else load_attempts()
    by_item: Dict[str, List[Dict[str, Any]]] = {}
    for attempt in attempts:
        if isinstance(attempt.get("item_id"), str):
            by_item.setdefault(attempt["item_id"], []).append(attempt)
    sessions = bank.get("sessions", [])
    return {
        item_id: learner_model.item_state(item, by_item.get(item_id, []), sessions)
        for item_id, item in bank.get("items", {}).items()
    }


def topic_mastery(now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    now = now or dt.datetime.now()
    bank = load_item_bank()
    attempts = load_attempts()
    states = item_states(bank, attempts)
    weakness = {
        row["key"]: row["weakness_score"]
        for row in progress_store.top_weak_topics(n=len(progress_store.TOPIC_TAXONOMY), now=now)
    }
    rows = learner_model.topic_mastery(weakness, bank["items"], attempts, states, now.date())
    for row in rows:
        row["label"] = progress_store.TOPIC_TAXONOMY.get(row["key"], {}).get("label", row["key"])
    return rows


def daily_queue(now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Today's review queue with full items attached, ready for a drill screen."""
    now = now or dt.datetime.now()
    bank = load_item_bank()
    attempts = load_attempts()
    states = item_states(bank, attempts)
    weakness = {
        row["key"]: row["weakness_score"]
        for row in progress_store.top_weak_topics(n=len(progress_store.TOPIC_TAXONOMY), now=now)
    }
    mastery = learner_model.topic_mastery(weakness, bank["items"], attempts, states, now.date())
    priority = {row["key"]: row["priority"] for row in mastery}
    queue = learner_model.daily_queue(bank["items"], states, priority, now.date())

    def expand(item_id: str) -> Dict[str, Any]:
        return card(bank["items"][item_id], states[item_id], now.date())

    return {
        **queue,
        "today": now.date().isoformat(),
        "reviews": [expand(i) for i in queue["reviews"]],
        "new": [expand(i) for i in queue["new"]],
    }


def card(item: Dict[str, Any], state: learner_model.ItemState, today: dt.date) -> Dict[str, Any]:
    """An item as the drill screen needs it: content, state and exercise format."""
    return {
        **item,
        "state": {**state.to_dict(), "is_due": state.is_due(today)},
        "exercise": learner_model.card_exercise(item, state),
    }


# ------------------------------------------------------------ topic drills
def practice_texts(topic: Optional[str] = None) -> List[Dict[str, Any]]:
    """English improved_version texts for cloze drills, newest recording first.

    Cloze topics are English function words, so Russian recordings are left
    out; mixed ("multi") ones are kept since their retelling is English.
    With `topic`, each text also carries its gaps and past results.
    """
    attempts = load_attempts() if topic else []
    texts = []
    for session in utils.iter_analysed_sessions():
        text = session.analysis.get("improved_version")
        if not isinstance(text, str) or not text.strip():
            continue
        if not learner_model.languages_compatible(session.language, "en"):
            continue
        entry: Dict[str, Any] = {
            "session_id": session.session_id,
            "recorded_at": session.recorded_at.isoformat(),
            "language": session.language,
            "text": text,
        }
        if topic:
            segments = learner_model.cloze_segments(text, topic)
            done = [
                a
                for a in attempts
                if a.get("topic") == topic and a.get("session_id") == session.session_id
            ]
            entry.update(
                {
                    "segments": segments,
                    "gaps": sum(1 for s in segments if "gap" in s),
                    "attempts": len(done),
                    "best_score": max((learner_model.attempt_score(a) for a in done), default=None),
                }
            )
        texts.append(entry)
    return texts[::-1]


# -------------------------------------------------------------------- usage
def estimate_claude_cost(model: str, usage: Dict[str, int]) -> Optional[float]:
    prices = config.CLAUDE_PRICE_PER_MTOK.get(model)
    if prices is None:
        return None
    input_price, output_price = prices
    cost = (
        usage.get("input_tokens", 0) * input_price
        + usage.get("cache_creation_input_tokens", 0)
        * input_price
        * config.CLAUDE_CACHE_WRITE_MULTIPLIER
        + usage.get("cache_read_input_tokens", 0)
        * input_price
        * config.CLAUDE_CACHE_READ_MULTIPLIER
        + usage.get("output_tokens", 0) * output_price
    ) / 1_000_000
    return round(cost, 6)


def estimate_deepgram_cost(language_key: str, audio_seconds: float) -> Optional[float]:
    price = config.DEEPGRAM_PRICE_PER_MINUTE.get(language_key)
    if price is None:
        return None
    return round(audio_seconds / 60.0 * price, 6)


def record_claude_usage(
    purpose: str, model: str, usage: Dict[str, int], *, session_id: Optional[str] = None
) -> Dict[str, Any]:
    record = {
        "v": USAGE_RECORD_VERSION,
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "service": "anthropic",
        "purpose": purpose,
        "session_id": session_id,
        "model": model,
        **{key: int(value) for key, value in usage.items()},
        "cost_usd": estimate_claude_cost(model, usage),
    }
    _append_usage(record)
    return record


def record_deepgram_usage(
    purpose: str, language_key: str, audio_seconds: float, *, session_id: Optional[str] = None
) -> Dict[str, Any]:
    record = {
        "v": USAGE_RECORD_VERSION,
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "service": "deepgram",
        "purpose": purpose,
        "session_id": session_id,
        "language": language_key,
        "audio_seconds": round(audio_seconds, 2),
        "cost_usd": estimate_deepgram_cost(language_key, audio_seconds),
    }
    _append_usage(record)
    return record


def _append_usage(record: Dict[str, Any]) -> None:
    # Cost tracking must never break the feature it is tracking.
    try:
        with _usage_lock:
            utils.append_jsonl(_usage_path(), record)
    except OSError:
        logger.exception("Could not append to the usage log")


def load_usage() -> List[Dict[str, Any]]:
    return utils.read_jsonl(_usage_path())


def usage_summary(recent: int = 50) -> Dict[str, Any]:
    records = load_usage()
    by_purpose: Dict[str, Dict[str, Any]] = {}
    totals = {"anthropic": 0.0, "deepgram": 0.0}
    for record in records:
        cost = record.get("cost_usd") or 0.0
        service = record.get("service", "other")
        totals[service] = totals.get(service, 0.0) + cost
        key = f"{service}:{record.get('purpose', 'other')}"
        bucket = by_purpose.setdefault(key, {"calls": 0, "cost_usd": 0.0})
        bucket["calls"] += 1
        bucket["cost_usd"] += cost
    for bucket in by_purpose.values():
        bucket["cost_usd"] = round(bucket["cost_usd"], 4)
        bucket["avg_cost_usd"] = round(bucket["cost_usd"] / bucket["calls"], 4)
    return {
        "total_usd": round(sum(totals.values()), 4),
        "by_service_usd": {key: round(value, 4) for key, value in totals.items()},
        "by_purpose": by_purpose,
        "recent": records[-recent:][::-1],
    }
