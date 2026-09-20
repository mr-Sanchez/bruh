"""The learner model on disk: item bank, attempts log and usage log.

Every read and write of the learner model goes through this module, so a
later switch from flat files to SQLite stays local to it. The files:

  data/item_bank.json  derived cache, rebuilt from recordings/*/analysis.json
                       whenever an analysis changes (and on a schema bump);
  data/attempts.jsonl  append-only, AUTHORITATIVE - it cannot be rebuilt from
                       anything, so it is never regenerated or truncated;
  data/usage.jsonl     append-only log of paid API calls with estimated cost,
                       so the real price per exercise is visible;
  data/practice/*.json AI exercise sets and every run of them. Paid for and
                       not rebuildable, like analysis.json; their wrong
                       answers are a second source of bank items.

Spoken drills (talk, shadowing) are ordinary recordings; their results are
derived on read from deepgram_response.json and logged as topic attempts.

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

from app import config, learner_model, progress_store, speech_drills, utils

logger = logging.getLogger(__name__)

# v2: cards from mistakes made in AI exercise sets (origin "ai_set").
BANK_SCHEMA_VERSION = 2
SET_SCHEMA_VERSION = 1
ATTEMPT_RECORD_VERSION = 1
USAGE_RECORD_VERSION = 1

_bank_lock = threading.Lock()
_attempts_lock = threading.Lock()
_usage_lock = threading.Lock()
_sets_lock = threading.Lock()


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
        "items": learner_model.build_bank(sessions, _set_mistake_items()),
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
    set_id: Optional[str] = None,
    answer: Optional[str] = None,
    context: Optional[str] = None,
    when: Optional[dt.datetime] = None,
) -> Dict[str, Any]:
    """Log one answer. A card attempt names its item; a topic drill (a cloze
    over a whole text, or a run of an AI exercise set) names its topic
    instead and carries its share of right answers as `score`, plus the
    recording the text came from or the set."""
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
        "set_id": set_id,
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
    rows = _snapshot(now or dt.datetime.now())["mastery"]
    for row in rows:
        row["label"] = progress_store.TOPIC_TAXONOMY.get(row["key"], {}).get("label", row["key"])
    return rows


def _snapshot(now: dt.datetime) -> Dict[str, Any]:
    """Bank, attempts, Leitner states and topic mastery, read once."""
    bank = load_item_bank()
    attempts = load_attempts()
    states = item_states(bank, attempts)
    weakness = {
        row["key"]: row["weakness_score"]
        for row in progress_store.top_weak_topics(n=len(progress_store.TOPIC_TAXONOMY), now=now)
    }
    mastery = learner_model.topic_mastery(weakness, bank["items"], attempts, states, now.date())
    return {"bank": bank, "attempts": attempts, "states": states, "mastery": mastery}


def daily_queue(
    now: Optional[dt.datetime] = None, snapshot: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Today's review queue with full items attached, ready for a drill screen."""
    now = now or dt.datetime.now()
    snap = snapshot or _snapshot(now)
    bank, states = snap["bank"], snap["states"]
    priority = {row["key"]: row["priority"] for row in snap["mastery"]}
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


# ------------------------------------------------------------ exercise sets
def _set_path(set_id: str) -> Path:
    return config.practice_dir() / f"{set_id}.json"


def new_set_id(now: Optional[dt.datetime] = None) -> str:
    """set-YYYYMMDD-HHMMSS, with a suffix if two sets land in one second."""
    base = f"set-{(now or dt.datetime.now()).strftime('%Y%m%d-%H%M%S')}"
    candidate, suffix = base, 1
    while _set_path(candidate).exists():
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def save_set(exercise_set: Dict[str, Any]) -> Dict[str, Any]:
    with _sets_lock:
        config.practice_dir().mkdir(parents=True, exist_ok=True)
        utils.write_json(_set_path(exercise_set["id"]), exercise_set)
    return exercise_set


def load_set(set_id: str) -> Optional[Dict[str, Any]]:
    """A set by id (the caller validates it as a plain file name), or None."""
    path = _set_path(set_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read exercise set %s", path)
        return None
    return data if isinstance(data, dict) and data.get("id") == set_id else None


def list_sets(topic: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every readable set, newest first; only one topic's with `topic`."""
    directory = config.practice_dir()
    if not directory.is_dir():
        return []
    sets = []
    for path in sorted(directory.glob("set-*.json"), reverse=True):
        data = load_set(path.stem)
        if data is not None and (topic is None or data.get("topic") == topic):
            sets.append(data)
    return sets


def set_summary(exercise_set: Dict[str, Any]) -> Dict[str, Any]:
    """A set without its exercises, for lists."""
    runs = exercise_set.get("runs") or []
    scores = [run.get("score", 0.0) for run in runs]
    return {
        "id": exercise_set["id"],
        "topic": exercise_set.get("topic"),
        "created_at": exercise_set.get("created_at"),
        "intro": exercise_set.get("intro", ""),
        "exercises": len(exercise_set.get("exercises") or []),
        "runs": len(runs),
        "last_score": scores[-1] if scores else None,
        "best_score": max(scores) if scores else None,
        "last_run_at": runs[-1].get("at") if runs else None,
        "cost_usd": _set_total_cost(exercise_set),
    }


def _set_total_cost(exercise_set: Dict[str, Any]) -> float:
    calls = [exercise_set.get("generation")]
    calls += [run.get("grading") for run in exercise_set.get("runs") or []]
    costs = [((call or {}).get("usage") or {}).get("cost_usd") for call in calls]
    return round(sum(c for c in costs if isinstance(c, (int, float))), 6)


def unstarted_set(topic: str) -> Optional[Dict[str, Any]]:
    """The newest set on a topic that was generated but never done.

    Offered instead of generating a new one, so a double click or a change of
    mind never pays for a second set.
    """
    return next((s for s in list_sets(topic) if not s.get("runs")), None)


def set_seeds(topic: str, bank: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The learner's own mistakes and rules on a topic, most recent first.

    Only English (or mixed) items from speech: sets are English exercises,
    and a set's own mistakes would just echo the set back.
    """
    bank = bank if bank is not None else load_item_bank()
    items = [
        item
        for item in bank["items"].values()
        if item.get("topic") == topic
        and item.get("kind") in (learner_model.KIND_FIX, learner_model.KIND_PATTERN)
        and item.get("origin") != learner_model.SET_EXERCISE
        and learner_model.languages_compatible(item.get("language", ""), "en")
    ]
    items.sort(key=lambda i: max(o["at"] for o in i["occurrences"]), reverse=True)
    return items[: config.SET_SEED_ITEMS]


def set_avoid_sentences(topic: str) -> List[str]:
    """Sentences of the latest sets on a topic, so a new set does not repeat them."""
    sentences: List[str] = []
    for exercise_set in list_sets(topic):
        for exercise in exercise_set.get("exercises") or []:
            text = exercise.get("russian") or exercise.get("sentence")
            if not text:
                text = f"{exercise.get('before', '')}___{exercise.get('after', '')}"
            sentences.append(text.strip())
            if len(sentences) >= config.SET_AVOID_SENTENCES:
                return sentences
    return sentences


def cached_verdict(
    exercise_set: Dict[str, Any], exercise_id: str, answer: str
) -> Optional[Dict[str, Any]]:
    """Claude's earlier grading of the same answer: a redo with it is free."""
    cache = (exercise_set.get("verdicts") or {}).get(exercise_id) or {}
    return cache.get(learner_model.normalize_answer(answer))


def record_set_run(
    set_id: str,
    results: List[Dict[str, Any]],
    *,
    context: Optional[str] = None,
    grading: Optional[Dict[str, Any]] = None,
    now: Optional[dt.datetime] = None,
) -> Dict[str, Any]:
    """Store one finished run of a set and feed it into the learner model.

    The run and Claude's verdicts go into the set file, the score into the
    attempts log (a topic attempt, like a cloze) and the wrong answers into
    the item bank as new cards.
    """
    now = now or dt.datetime.now()
    with _sets_lock:
        exercise_set = load_set(set_id)
        if exercise_set is None:
            raise KeyError(set_id)
        score = learner_model.set_run_score(results)
        run = {
            "at": now.isoformat(timespec="seconds"),
            "context": context,
            "score": round(score, 3),
            "correct": sum(1 for r in results if r.get("correct")),
            "total": len(results),
            "results": results,
            "grading": grading,
        }
        exercise_set.setdefault("runs", []).append(run)
        verdicts = exercise_set.setdefault("verdicts", {})
        for result in results:
            if result.get("graded_by") == "claude":
                key = learner_model.normalize_answer(result.get("answer", ""))
                verdicts.setdefault(result["exercise_id"], {})[key] = {
                    field: result.get(field) for field in ("correct", "comment", "corrected")
                }
        utils.write_json(_set_path(set_id), exercise_set)

    before = set(load_item_bank()["items"])
    append_attempt(
        None,
        learner_model.SET_EXERCISE,
        score >= config.DRILL_PASS_SCORE,
        topic=exercise_set.get("topic"),
        score=score,
        set_id=set_id,
        context=context,
        when=now,
    )
    after = rebuild_item_bank()["items"]
    new_cards = {
        item["id"]
        for item in learner_model.items_from_set_run(exercise_set, run)
        if item["id"] not in before and item["id"] in after
    }
    return {"run": run, "new_cards": len(new_cards), "set": set_summary(exercise_set)}


def _set_mistake_items() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for exercise_set in list_sets():
        for run in exercise_set.get("runs") or []:
            items.extend(learner_model.items_from_set_run(exercise_set, run))
    return items


def set_cost_estimate() -> float:
    """What one more set costs: the average generation plus the average
    grading call from the usage log, or the configured estimate until the
    log has a set in it."""
    by_purpose = usage_summary(recent=0)["by_purpose"]
    generation = by_purpose.get("anthropic:exercise_set", {}).get("avg_cost_usd")
    if not generation:
        return config.SET_COST_ESTIMATE_USD
    grading = by_purpose.get("anthropic:exercise_grading", {}).get("avg_cost_usd") or 0.0
    return round(generation + grading, 4)


def topic_info(key: str) -> Dict[str, Any]:
    """A topic as the screens show it: label, description, theory links."""
    info = progress_store.TOPIC_TAXONOMY.get(key, {})
    return {
        "key": key,
        "label": info.get("label", key),
        "description": info.get("description", ""),
        "resources": [
            {"title": title, "url": url} for title, url in config.TOPIC_RESOURCES.get(key, ())
        ],
    }


def _set_step(
    mastery: List[Dict[str, Any]], attempts_today: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """The optional «Сегодня» step: an AI set on the topic that needs it most.

    Optional because it costs money: it never counts towards "all done" or
    the minutes left, and nothing is generated until the learner clicks.
    """
    done = [a for a in attempts_today if a.get("exercise") == learner_model.SET_EXERCISE]
    if done:
        last = done[-1]
        return {
            "kind": "ai_set",
            "optional": True,
            "status": "done",
            "topic": topic_info(last["topic"]),
            "score": learner_model.attempt_score(last),
            "minutes": 0,
        }
    topic = next(
        (
            row["key"]
            for row in mastery
            if row["priority"] > 0 and learner_model.set_topic_allowed(row["key"])
        ),
        None,
    )
    if topic is None:
        return None
    waiting = unstarted_set(topic)
    if waiting is None and config.get_anthropic_api_key() is None:
        return None
    return {
        "kind": "ai_set",
        "optional": True,
        "status": "todo",
        "topic": topic_info(topic),
        "set_id": waiting["id"] if waiting else None,
        "cost_usd": 0.0 if waiting else set_cost_estimate(),
        "minutes": round(config.WORKOUT_MINUTES_SET),
    }


# ------------------------------------------------------------ spoken drills
def speech_report(session: utils.Session) -> Optional[Dict[str, Any]]:
    """Pace and hesitations of a spoken take, plus the reading check of a
    shadowing take - derived from deepgram_response.json on every read.

    None for a typed take or one without a readable Deepgram response.
    """
    if session.input_mode != config.INPUT_VOICE or not session.response_path.is_file():
        return None
    try:
        payload = json.loads(session.response_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read %s", session.response_path)
        return None
    words = speech_drills.response_words(payload)
    fillers = config.profile_by_key(session.language_key).filler_words
    report: Dict[str, Any] = {
        "metrics": speech_drills.speech_metrics(words, fillers),
        "timeline": speech_drills.timeline(words, fillers),
    }
    if session.kind == config.KIND_SHADOWING and session.drill:
        report["reading"] = speech_drills.align_reading(
            str(session.drill.get("reference", "")), words
        )
    return report


def drill_score(session: utils.Session, report: Dict[str, Any]) -> Optional[float]:
    """The attempt score of a drill take, or None when it is not logged:
    talk rounds after the first (practice), and talks in a language without
    filler detection, whose fluency cannot be measured the same way."""
    if session.kind == config.KIND_SHADOWING:
        return report.get("reading", {}).get("score")
    if session.kind == config.KIND_TALK and (session.drill or {}).get("round", 1) == 1:
        return report["metrics"].get("fluency_score")
    return None


def record_speech_drill(session: utils.Session) -> Optional[Dict[str, Any]]:
    """Log a transcribed drill take as a topic attempt on the fluency topic.

    Runs after transcription; a take is never logged twice.
    """
    report = speech_report(session)
    score = drill_score(session, report) if report else None
    if score is None or not report or not report["metrics"]["words"]:
        return None
    if any(
        a.get("session_id") == session.id and a.get("exercise") == session.kind
        for a in load_attempts()
    ):
        return None
    return append_attempt(
        None,
        session.kind,
        score >= config.DRILL_PASS_SCORE,
        topic=config.FLUENCY_TOPIC,
        score=score,
        session_id=session.id,
    )


def _drill_sessions(kind: str) -> List[utils.Session]:
    return [s for s in utils.list_sessions() if s.kind == kind]


def shadowing_passages(source_session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Passages of every English improved_version, newest recording first,
    with how often and how well each was read.

    A passage is matched to earlier takes by its text, so a re-analysis that
    rewrites the retelling starts its passages fresh.
    """
    scores: Dict[str, List[float]] = {}
    attempts = {
        a["session_id"]: learner_model.attempt_score(a)
        for a in load_attempts()
        if a.get("exercise") == config.KIND_SHADOWING and isinstance(a.get("session_id"), str)
    }
    for take in _drill_sessions(config.KIND_SHADOWING):
        key = learner_model.normalize_text(str((take.drill or {}).get("reference", "")))
        if take.id in attempts:
            scores.setdefault(key, []).append(attempts[take.id])

    passages = []
    for session in reversed(list(utils.iter_analysed_sessions())):
        if source_session_id is not None and session.session_id != source_session_id:
            continue
        text = session.analysis.get("improved_version")
        if not isinstance(text, str) or not text.strip():
            continue
        if not learner_model.languages_compatible(session.language, "en"):
            continue
        for index, passage in enumerate(speech_drills.split_passages(text)):
            done = scores.get(learner_model.normalize_text(passage), [])
            passages.append(
                {
                    "session_id": session.session_id,
                    "recorded_at": session.recorded_at.isoformat(),
                    "index": index,
                    "text": passage,
                    "attempts": len(done),
                    "best_score": max(done) if done else None,
                }
            )
    return passages


def talk_series() -> List[Dict[str, Any]]:
    """«60 секунд» takes grouped into series (same prompt, rounds 1..3),
    newest first, each round with its measurements."""
    series: Dict[str, Dict[str, Any]] = {}
    for take in reversed(_drill_sessions(config.KIND_TALK)):
        drill = take.drill or {}
        key = str(drill.get("series") or take.id)
        entry = series.setdefault(
            key,
            {
                "series": key,
                "started_at": take.started_at.isoformat(timespec="seconds"),
                "prompt_index": drill.get("prompt_index"),
                "language": take.language_key,
                "rounds": [],
            },
        )
        report = speech_report(take) if take.status == utils.STATUS_DONE else None
        entry["rounds"].append(
            {
                "session_id": take.id,
                "round": drill.get("round", len(entry["rounds"]) + 1),
                "status": take.status,
                "metrics": report["metrics"] if report else None,
            }
        )
    return sorted(series.values(), key=lambda e: e["started_at"], reverse=True)


def talk_round(series_id: str) -> int:
    """The round number the next take of a series gets."""
    return 1 + sum(
        1
        for take in _drill_sessions(config.KIND_TALK)
        if (take.drill or {}).get("series") == series_id
    )


def _speech_step(today: dt.date, sessions: List[utils.Session]) -> Optional[Dict[str, Any]]:
    """The optional «Сегодня» step: a spoken warm-up - «60 секунд», or
    shadowing a passage. Done once either drill was transcribed today."""
    done = [
        s
        for s in sessions
        if s.is_drill and s.status == utils.STATUS_DONE and s.started_at.date() == today
    ]
    if done:
        return {
            "kind": "speech",
            "optional": True,
            "status": "done",
            "session_id": done[0].id,
            "activity": done[0].kind,
            "minutes": 0,
        }
    if config.get_api_key() is None:
        return None
    index = speech_drills.talk_prompt_index(today)
    question, hint = config.SPEAKING_PROMPTS[index]
    return {
        "kind": "speech",
        "optional": True,
        "status": "todo",
        "prompt": {"index": index, "question": question, "hint": hint},
        "passage": speech_drills.pick_passage(shadowing_passages()),
        "minutes": round(config.WORKOUT_MINUTES_SPEECH),
    }


# ------------------------------------------------------------ daily workout
def today_workout(now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """The «Сегодня» workout, assembled by code (no LLM): cards, then a topic
    drill, then one live activity. Each step says whether it is done today,
    judged from the attempts log and today's recordings."""
    now = now or dt.datetime.now()
    today = now.date()
    snap = _snapshot(now)
    attempts_today = learner_model.attempts_on(snap["attempts"], today)

    # 1. Cards: the daily queue, capped to fit the workout.
    queue = daily_queue(now, snap)
    cards = queue["reviews"] + queue["new"]
    cards_answered = sum(1 for a in attempts_today if isinstance(a.get("item_id"), str))
    if cards:
        cards_status = "todo"
    else:
        cards_status = "done" if cards_answered else "empty"
    cards_step = {
        "kind": "cards",
        "status": cards_status,
        "items": cards[: config.WORKOUT_MAX_CARDS],
        "queue_total": len(cards),
        "reviews": len(queue["reviews"]),
        "new": len(queue["new"]),
        "new_waiting": queue["new_waiting"],
        "answered_today": cards_answered,
        "correct_today": sum(
            1 for a in attempts_today if isinstance(a.get("item_id"), str) and a.get("correct")
        ),
        "minutes": round(
            min(len(cards), config.WORKOUT_MAX_CARDS) * config.WORKOUT_MINUTES_PER_CARD
        ),
    }

    # 2. Topic drill: one cloze, on the cloze topic that needs it most.
    drill_step: Optional[Dict[str, Any]] = None
    done_drills = [a for a in attempts_today if a.get("exercise") == "cloze"]
    if done_drills:
        last = done_drills[-1]
        drill_step = {
            "kind": "cloze",
            "status": "done",
            "topic": topic_info(last["topic"]),
            "score": learner_model.attempt_score(last),
            "minutes": 0,
        }
    else:
        for topic in learner_model.cloze_topic_order(snap["mastery"]):
            text = learner_model.pick_cloze_text(practice_texts(topic))
            if text is not None:
                drill_step = {
                    "kind": "cloze",
                    "status": "todo",
                    "topic": topic_info(topic),
                    "text": text,
                    "minutes": round(config.WORKOUT_MINUTES_CLOZE),
                }
                break

    # 3. Live activity: a monologue on today's prompt, recorded and analysed.
    # A picture description done today counts too: the slot is "speak once".
    # Spoken drills are not the live step: they are never analysed.
    sessions = utils.list_sessions()
    recorded = [s for s in sessions if s.status == utils.STATUS_DONE]
    recorded_today = [
        s
        for s in recorded
        if s.started_at.date() == today and s.transcript and not s.is_drill
    ]
    analysed = [s for s in recorded_today if s.analysis_path.is_file()]
    live = analysed[0] if analysed else recorded_today[0] if recorded_today else None
    live_status = "done" if analysed else "analyze" if recorded_today else "todo"
    prompt_index = learner_model.speaking_prompt_index(today)
    question, hint = config.SPEAKING_PROMPTS[prompt_index]
    live_step = {
        "kind": "monologue",
        "status": live_status,
        "session_id": live.id if live else None,
        "activity": live.kind if live else config.KIND_MONOLOGUE,
        "prompt": {"index": prompt_index, "question": question, "hint": hint},
        "minutes": 0 if live_status == "done" else round(config.WORKOUT_MINUTES_MONOLOGUE),
    }

    # 4. Optional and paid: an AI exercise set on the main topic.
    set_step = _set_step(snap["mastery"], attempts_today)
    # 5. Optional, Deepgram only: a spoken warm-up.
    speech_step = _speech_step(today, sessions)

    focus = next((row for row in snap["mastery"] if row["priority"] > 0), None)
    steps = [cards_step] + ([drill_step] if drill_step else []) + [live_step]
    steps += [set_step] if set_step else []
    steps += [speech_step] if speech_step else []

    active_days = {dt.datetime.fromisoformat(a["ts"]).date() for a in snap["attempts"]}
    active_days |= {s.started_at.date() for s in recorded}
    return {
        "today": today.isoformat(),
        "steps": steps,
        "focus_topic": (
            {**topic_info(focus["key"]), "accuracy": focus["accuracy"]} if focus else None
        ),
        "minutes_left": sum(
            step["minutes"]
            for step in steps
            if step["status"] != "done" and not step.get("optional")
        ),
        "streak_days": learner_model.activity_streak(active_days, today),
        "active_today": today in active_days,
    }


def activity_history(days: int = config.ACTIVITY_HISTORY_DAYS) -> List[Dict[str, Any]]:
    """Exercise results per day, newest first, with topic labels."""
    history = learner_model.daily_activity(load_attempts(), days)
    for entry in history:
        for drill in entry["drills"]:
            drill["label"] = (
                progress_store.TOPIC_TAXONOMY.get(drill["topic"], {}).get("label", drill["topic"])
            )
    return history


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
    purpose: str,
    model: str,
    usage: Dict[str, int],
    *,
    session_id: Optional[str] = None,
    set_id: Optional[str] = None,
) -> Dict[str, Any]:
    record = {
        "v": USAGE_RECORD_VERSION,
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "service": "anthropic",
        "purpose": purpose,
        "session_id": session_id,
        **({"set_id": set_id} if set_id else {}),
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
