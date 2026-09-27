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
                       answers are a second source of bank items;
  data/card_verdicts.jsonl
                       append-only cache of Claude's checks of card answers,
                       so the same answer to the same sentence is paid once;
  data/roadmap_marks.jsonl
                       append-only, AUTHORITATIVE - the learner's «Пропустить» /
                       «Уже знаю» marks on roadmap lessons, the newest wins;
  data/word_picks.jsonl
                       append-only, AUTHORITATIVE - words and phrases the learner
                       picked from a set's vocabulary as word cards (or took
                       back, or deleted the card: "deleted": true), the newest
                       record per set + word wins;
  data/theory/*.json   every version of a lesson's theory (paid, not rebuildable);
  data/lesson_tasks/*.json
                       spoken tasks Claude wrote for a lesson (paid, append-only);
  data/module_tests/*.json
                       module entry tests and every run of them (paid, append-only).

Spoken drills (talk, shadowing) are ordinary recordings; their results are
derived on read from deepgram_response.json and logged as topic attempts.
Dictation is the exception: it keeps its own files (app.dictation_store) and
writes no attempts at all, but its finished sentences still feed the daily
workout, the day streak and the history shown here. The irregular-verb drill
(app.verb_store) is the same kind of exception; it only feeds the streak.

The rules themselves (item identity, Leitner, topic mastery) live in
app.learner_model as pure functions.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import (
    config,
    curriculum,
    dictation_store,
    exercise_sets,
    learner_model,
    progress_store,
    roadmap,
    speech_drills,
    text_store,
    theme_store,
    utils,
    verb_store,
)

logger = logging.getLogger(__name__)

# v2: cards from mistakes made in AI exercise sets (origin "ai_set").
# v3: fix items carry `drills` + `focus`; fixes without drills are retired.
# v4: fix items carry `focus_examples` (the rule's examples); rule items are retired.
# v5: topics are taxonomy v2 (app/curriculum.py).
# v6: word cards picked from a set's vocabulary (kind "word").
BANK_SCHEMA_VERSION = 6
SET_SCHEMA_VERSION = 1
ATTEMPT_RECORD_VERSION = 1
USAGE_RECORD_VERSION = 1

_bank_lock = threading.Lock()
_attempts_lock = threading.Lock()
_usage_lock = threading.Lock()
_sets_lock = threading.Lock()
_verdicts_lock = threading.Lock()
_marks_lock = threading.Lock()
_theory_lock = threading.Lock()
_tasks_lock = threading.Lock()
_module_tests_lock = threading.Lock()
_picks_lock = threading.Lock()


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
        "items": learner_model.build_bank(
            sessions,
            _set_mistake_items(),
            learner_model.items_from_word_picks(load_word_picks()),
        ),
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
    lesson_id: Optional[str] = None,
    answer: Optional[str] = None,
    context: Optional[str] = None,
    when: Optional[dt.datetime] = None,
) -> Dict[str, Any]:
    """Log one answer. A card attempt names its item; a topic drill (a run of
    an AI exercise set, a spoken drill) names its topic instead and carries
    its score, plus the recording or the set it came from."""
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
        "lesson_id": lesson_id,
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
        row["label"] = curriculum.topic_label(row["key"])
        row["area"] = curriculum.area_of(row["key"])
        row["level"] = curriculum.level_of(row["key"])
    return rows


def area_mastery(topic_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The same numbers rolled up to areas, labelled for the screens."""
    rows = learner_model.area_mastery(topic_rows)
    for row in rows:
        row["label"] = curriculum.AREA_BY_KEY[row["key"]].label
    return rows


def _snapshot(now: dt.datetime) -> Dict[str, Any]:
    """Bank, attempts, Leitner states and topic mastery, read once."""
    bank = load_item_bank()
    attempts = load_attempts()
    states = item_states(bank, attempts)
    weakness = {
        row["key"]: row["weakness_score"]
        for row in progress_store.top_weak_topics(n=len(curriculum.TOPICS), now=now)
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


# ------------------------------------------------------------------ roadmap
def _marks_path() -> Path:
    return config.data_dir() / config.ROADMAP_MARKS_FILENAME


def append_roadmap_mark(
    lesson: str, mark: Optional[str], when: Optional[dt.datetime] = None
) -> Dict[str, Any]:
    """Mark a lesson «пропущен» / «уже знаю», or clear its mark (None)."""
    if lesson not in curriculum.MODULE_OF_LESSON:
        raise ValueError(f"Unknown lesson {lesson!r}.")
    if mark is not None and mark not in roadmap.MARKS:
        raise ValueError(f"Unknown mark {mark!r}.")
    record = {
        "ts": (when or dt.datetime.now()).isoformat(timespec="seconds"),
        "lesson": lesson,
        "mark": mark,
    }
    with _marks_lock:
        utils.append_jsonl(_marks_path(), record)
    return record


def load_roadmap_marks() -> List[Dict[str, Any]]:
    return [
        record
        for record in utils.read_jsonl(_marks_path())
        if isinstance(record.get("ts"), str) and isinstance(record.get("lesson"), str)
    ]


def roadmap_view(priority: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """The course with every lesson's status, for GET /api/roadmap, plus the
    lessons recommended by the learner's own mistakes (`priority`: topic ->
    learner_model priority; computed here when not given)."""
    speech = {
        row["key"]: row["count"]
        for row in progress_store.top_weak_topics(n=len(curriculum.TOPICS))
    }
    view = roadmap.course(
        load_attempts(), load_roadmap_marks(), theory_dates=theory_dates(), speech_mistakes=speech
    )
    if priority is None:
        priority = {row["key"]: row["priority"] for row in topic_mastery()}
    view["recommended"] = roadmap.recommend(view, priority)
    return view


def roadmap_lesson(lesson: str) -> Optional[Dict[str, Any]]:
    """One lesson's roadmap row, with its level and module - the lesson page."""
    view = roadmap_view()
    for level in view["levels"]:
        for module in level["modules"]:
            for row in module["lessons"]:
                if row["id"] == lesson:
                    return {
                        **row,
                        "level": {"key": level["key"], "label": level["label"]},
                        "module": {"key": module["key"], "title": module["title"]},
                        "mastery_runs": view["mastery_runs"],
                        "pass_score": view["pass_score"],
                    }
    return None


# ------------------------------------------------------------------- theory
# data/theory/<lesson>.json: {"lesson", "versions": [{"created_at", "model",
# "effort", "request_id", "usage", "own_mistakes", "content"}]}, oldest first.
# Paid for and not rebuildable: a new version is appended, never replacing one.
def _theory_path(lesson: str) -> Path:
    return config.theory_dir() / f"{lesson}.json"


def load_theory(lesson: str) -> Optional[Dict[str, Any]]:
    """A lesson's theory file (the caller checks the lesson id), or None."""
    path = _theory_path(lesson)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read theory %s", path)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("versions"), list):
        return None
    return data


def add_theory_version(lesson: str, version: Dict[str, Any]) -> Dict[str, Any]:
    with _theory_lock:
        data = load_theory(lesson) or {"lesson": lesson, "versions": []}
        data["versions"].append(version)
        config.theory_dir().mkdir(parents=True, exist_ok=True)
        utils.write_json(_theory_path(lesson), data)
    return data


def theory_dates() -> Dict[str, str]:
    """Lesson -> when its latest theory was written (the roadmap's «теория»)."""
    directory = config.theory_dir()
    if not directory.is_dir():
        return {}
    dates = {}
    for path in directory.glob("*.json"):
        data = load_theory(path.stem)
        versions = (data or {}).get("versions") or []
        if path.stem in curriculum.MODULE_OF_LESSON and versions:
            dates[path.stem] = versions[-1].get("created_at")
    return {lesson: when for lesson, when in dates.items() if isinstance(when, str)}


def theory_own_mistakes(topic: str) -> List[Dict[str, Any]]:
    """The learner's own recorded mistakes on a topic, newest first - the
    same English speech items a set is seeded with, fixes only."""
    return [
        item for item in set_seeds(topic) if item.get("kind") == learner_model.KIND_FIX
    ][: config.THEORY_OWN_MISTAKES]


def theory_cost_estimate() -> float:
    entry = usage_summary(recent=0)["by_purpose"].get("anthropic:theory", {})
    return entry.get("avg_cost_usd") or config.THEORY_COST_ESTIMATE_USD


# -------------------------------------------------------- lesson spoken task
# data/lesson_tasks/<lesson>.json: {"lesson", "tasks": [{"id": "t1", "theme",
# "question", "hint", "use", "created_at", "model", "cost_usd"}]}. Written by
# Claude a few at a time per context; paid for, so only ever appended to.
def _tasks_path(lesson: str) -> Path:
    return config.lesson_tasks_dir() / f"{lesson}.json"


def load_lesson_tasks(lesson: str) -> List[Dict[str, Any]]:
    path = _tasks_path(lesson)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read lesson tasks %s", path)
        return []
    tasks = data.get("tasks") if isinstance(data, dict) else None
    return [t for t in tasks or [] if isinstance(t, dict) and t.get("id") and t.get("question")]


def add_lesson_tasks(
    lesson: str,
    theme: Dict[str, Optional[str]],
    tasks: List[Dict[str, str]],
    *,
    model: str,
    cost_usd: Optional[float],
) -> List[Dict[str, Any]]:
    """Append freshly written tasks (ids t1, t2, ... across the lesson)."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    with _tasks_lock:
        existing = load_lesson_tasks(lesson)
        added = [
            {
                "id": f"t{len(existing) + number}",
                "theme": theme,
                "question": task["question"],
                "hint": task["hint"],
                "use": task.get("use", ""),
                "created_at": now,
                "model": model,
                "cost_usd": cost_usd,
            }
            for number, task in enumerate(tasks, start=1)
        ]
        config.lesson_tasks_dir().mkdir(parents=True, exist_ok=True)
        utils.write_json(_tasks_path(lesson), {"lesson": lesson, "tasks": existing + added})
    return added


def lesson_task(lesson: str, task_id: str) -> Optional[Dict[str, Any]]:
    return next((t for t in load_lesson_tasks(lesson) if t["id"] == task_id), None)


# --------------------------------------------------------- module entry test
# data/module_tests/<module>.json: {"module", "tests": [{"id": "test-1",
# "created_at", "questions", "generation", "runs": [{"at", "answers",
# "results", "by_lesson", "score"}]}]}. Paid for, so only ever appended to.
def _module_tests_path(module: str) -> Path:
    return config.module_tests_dir() / f"{module}.json"


def load_module_tests(module: str) -> List[Dict[str, Any]]:
    path = _module_tests_path(module)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read module tests %s", path)
        return []
    tests = data.get("tests") if isinstance(data, dict) else None
    return [t for t in tests or [] if isinstance(t, dict) and t.get("id") and t.get("questions")]


def _save_module_tests(module: str, tests: List[Dict[str, Any]]) -> None:
    config.module_tests_dir().mkdir(parents=True, exist_ok=True)
    utils.write_json(_module_tests_path(module), {"module": module, "tests": tests})


def add_module_test(
    module: str, questions: List[Dict[str, Any]], generation: Dict[str, Any]
) -> Dict[str, Any]:
    with _module_tests_lock:
        tests = load_module_tests(module)
        test = {
            "id": f"test-{len(tests) + 1}",
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "questions": questions,
            "generation": generation,
            "runs": [],
        }
        _save_module_tests(module, tests + [test])
    return test


def record_module_test_run(
    module: str,
    test_id: str,
    answers: Dict[str, str],
    graded: Dict[str, Any],
    now: Optional[dt.datetime] = None,
) -> Dict[str, Any]:
    """Store a run in the test file and log one topic attempt per lesson:
    the test is evidence of what the learner knows, like any drill."""
    now = now or dt.datetime.now()
    run = {"at": now.isoformat(timespec="seconds"), "answers": answers, **graded}
    with _module_tests_lock:
        tests = load_module_tests(module)
        test = next((t for t in tests if t["id"] == test_id), None)
        if test is None:
            raise KeyError(test_id)
        test.setdefault("runs", []).append(run)
        _save_module_tests(module, tests)
    for lesson, entry in graded["by_lesson"].items():
        append_attempt(
            None,
            config.MODULE_TEST_EXERCISE,
            entry["known"],
            topic=lesson,
            score=entry["score"],
            lesson_id=lesson,
            when=now,
        )
    return run


def module_test_cost_estimate() -> float:
    entry = usage_summary(recent=0)["by_purpose"].get("anthropic:module_test", {})
    return entry.get("avg_cost_usd") or config.MODULE_TEST_COST_ESTIMATE_USD


def record_lesson_task(session: utils.Session, check: Optional[Dict[str, Any]]) -> None:
    """Log a lesson's spoken task once, as a topic attempt scored by how well
    the lesson's rule was used. A forced re-analysis never logs it again: the
    attempts log is append-only, and one take is one attempt."""
    lesson = (session.lesson or {}).get("id")
    if not lesson or not check:
        return
    exercise = config.LESSON_TASK_EXERCISE
    if any(
        a.get("session_id") == session.id and a.get("exercise") == exercise
        for a in load_attempts()
    ):
        return
    score = (check["score"] - 1) / 9  # 1..10 -> 0..1
    append_attempt(
        None,
        exercise,
        score >= config.DRILL_PASS_SCORE,
        topic=lesson,
        score=score,
        session_id=session.id,
        lesson_id=lesson,
    )


# ------------------------------------------------------------ card verdicts
def _verdicts_path() -> Path:
    return config.data_dir() / config.CARD_VERDICTS_FILENAME


def _verdict_key(russian: str, answer: str) -> tuple:
    return (learner_model.normalize_text(russian), learner_model.normalize_answer(answer))


def cached_card_verdict(russian: str, answer: str) -> Optional[Dict[str, Any]]:
    """Claude's earlier check of the same answer to the same sentence, if any.

    Keyed by the sentence rather than the card: a re-analysis that rewords a
    card keeps whatever verdicts still apply.
    """
    key = _verdict_key(russian, answer)
    found = None
    for record in utils.read_jsonl(_verdicts_path()):
        if _verdict_key(record.get("russian", ""), record.get("answer", "")) == key:
            found = record
    if found is None:
        return None
    return {field: found.get(field) for field in ("correct", "comment", "corrected")}


def store_card_verdict(
    item_id: str, russian: str, answer: str, verdict: Dict[str, Any], *, model: str, cost: Any
) -> None:
    record = {
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "item_id": item_id,
        "russian": russian,
        "answer": answer,
        **{field: verdict.get(field) for field in ("correct", "comment", "corrected")},
        "model": model,
        "cost_usd": cost,
    }
    # A lost cache entry only means a later identical answer is paid again.
    try:
        with _verdicts_lock:
            utils.append_jsonl(_verdicts_path(), record)
    except OSError:
        logger.exception("Could not append to the card verdict cache")


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
        # The context («уклон») it was written in; sets before R3 have none.
        "theme": exercise_set.get("theme"),
        # A roadmap lesson's set (R5) and the theory version it followed.
        "lesson_id": exercise_set.get("lesson_id"),
        "theory_version": exercise_set.get("theory_version"),
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


def set_sentences(topic: str) -> List[str]:
    """Every exercise sentence of every set on a topic, newest set first."""
    return [
        exercise_sets.exercise_text(exercise)
        for exercise_set in list_sets(topic)
        for exercise in exercise_set.get("exercises") or []
    ]


def set_avoid_sentences(topic: str) -> List[str]:
    """Sentences of the latest sets on a topic, so a new set does not repeat them."""
    return set_sentences(topic)[: config.SET_AVOID_SENTENCES]


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
    attempts log (a topic attempt) and the wrong answers into
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
                    field: result.get(field)
                    for field in ("correct", "comment", "corrected", "topic")
                    if field in result
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
        lesson_id=exercise_set.get("lesson_id"),
        context=context,
        when=now,
    )
    after = rebuild_item_bank()["items"]
    new_cards = {
        item["id"]
        for item in learner_model.items_from_set_run(exercise_set, run)
        if item["id"] not in before
        and item["id"] in after
        and not learner_model.is_retired(after[item["id"]])
    }
    return {"run": run, "new_cards": len(new_cards), "set": set_summary(exercise_set)}


# -------------------------------------------------------------- word cards
def _picks_path() -> Path:
    return config.data_dir() / config.WORD_PICKS_FILENAME


def load_word_picks() -> List[Dict[str, Any]]:
    return [
        record
        for record in utils.read_jsonl(_picks_path())
        if isinstance(record.get("ts"), str) and isinstance(record.get("set_id"), str)
    ]


def picked_vocabulary(set_id: str) -> List[str]:
    """Ids of the set's vocabulary entries that are word cards right now."""
    latest: Dict[str, str] = {}
    for record in sorted(load_word_picks(), key=lambda r: r["ts"]):
        if record["set_id"] == set_id:
            latest[record.get("vocab_id")] = record.get("action")
    return [vocab_id for vocab_id, action in latest.items() if action == "add"]


def set_word_picks(
    set_id: str, chosen: List[str], now: Optional[dt.datetime] = None
) -> Dict[str, Any]:
    """Make exactly `chosen` (vocabulary ids of the set) its word cards.

    Only the difference is logged: an "add" for each newly chosen entry, a
    "remove" for each one no longer chosen. The word itself goes into the
    record, so the bank is rebuilt from the log alone. A removed card keeps
    its attempts in the log; picking it again brings its history back.
    """
    exercise_set = load_set(set_id)
    if exercise_set is None:
        raise KeyError(set_id)
    return save_word_picks(
        set_id,
        exercise_set.get("vocabulary") or [],
        chosen,
        lesson_id=exercise_set.get("lesson_id"),
        now=now,
    )


def save_word_picks(
    source_id: str,
    entries: List[Dict[str, Any]],
    chosen: List[str],
    *,
    lesson_id: Optional[str] = None,
    now: Optional[dt.datetime] = None,
) -> Dict[str, Any]:
    """The pick log behind set_word_picks, for any source of candidates - a
    set, or a translated text (app.text_store). `source_id` goes into the
    record's `set_id` field, which has always meant "where the word came
    from"; `entries` are the candidates with their ids."""
    vocabulary = {entry["id"]: entry for entry in entries}
    unknown = [vocab_id for vocab_id in chosen if vocab_id not in vocabulary]
    if unknown:
        raise ValueError(f"Unknown vocabulary ids: {', '.join(unknown)}")
    ts = (now or dt.datetime.now()).isoformat(timespec="seconds")
    with _picks_lock:
        current = set(picked_vocabulary(source_id))
        wanted = set(chosen)
        changes = [(v, "add") for v in vocabulary if v in wanted - current]
        changes += [(v, "remove") for v in vocabulary if v in current - wanted]
        for vocab_id, action in changes:
            entry = vocabulary[vocab_id]
            utils.append_jsonl(
                _picks_path(),
                {
                    "ts": ts,
                    "set_id": source_id,
                    "vocab_id": vocab_id,
                    "action": action,
                    "lesson_id": lesson_id,
                    "word": {k: v for k, v in entry.items() if k != "id"},
                },
            )
    if changes:
        rebuild_item_bank()
    return {
        "picked": sorted(wanted, key=list(vocabulary).index),
        "added": sum(1 for _, action in changes if action == "add"),
        "removed": sum(1 for _, action in changes if action == "remove"),
    }


def word_cards() -> List[Dict[str, Any]]:
    """The learner's word cards (bank items of kind "word"), newest pick first."""
    items = [
        item
        for item in load_item_bank()["items"].values()
        if item.get("kind") == learner_model.KIND_WORD
    ]
    items.sort(key=lambda i: max(o["at"] for o in i["occurrences"]), reverse=True)
    return items


def _theme_key(theme: Any) -> Optional[str]:
    if not isinstance(theme, dict):
        return None
    return theme.get("key") or (theme.get("label") or "").strip().lower() or None


def known_words(
    limit: int = config.SET_KNOWN_WORDS,
    *,
    topic: Optional[str] = None,
    theme: Optional[Dict[str, Optional[str]]] = None,
) -> List[str]:
    """The English of the word cards a new set's vocabulary should avoid.

    Only a hint for Claude (repeats are dropped in code anyway, see
    fresh_vocabulary), so the list is short and picks the cards a set on
    `topic` in `theme` would most likely repeat: those picked from sets on the
    same topic, then from sets and texts in the same context, then the newest.
    """
    sources: Dict[str, tuple] = {
        s["id"]: (s.get("topic"), _theme_key(s.get("theme"))) for s in list_sets()
    }
    sources.update(
        {t["id"]: (None, _theme_key(t.get("theme"))) for t in text_store.list_texts()}
    )
    wanted_theme = _theme_key(theme)

    def closeness(item: Dict[str, Any]) -> int:
        best = 0
        for occurrence in item["occurrences"]:
            source_topic, source_theme = sources.get(occurrence.get("set_id"), (None, None))
            if topic is not None and (source_topic == topic or item.get("lesson_id") == topic):
                return 2
            if wanted_theme is not None and source_theme == wanted_theme:
                best = 1
        return best

    items = word_cards()  # newest first; the sort below is stable
    items.sort(key=closeness, reverse=True)
    return [item["content"]["english"] for item in items[:limit]]


def cards_in_text(text: str, limit: int = config.TEXT_KNOWN_PHRASES) -> List[str]:
    """Word cards whose words all occur in `text` - the ones a review of its
    translation could suggest again."""
    stems = set(learner_model.word_stems(text))
    found = [
        item["content"]["english"]
        for item in word_cards()
        if (words := learner_model.word_stems(item["content"]["english"]))
        and set(words) <= stems
    ]
    return found[:limit]


def fresh_vocabulary(entries: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Freshly written word card candidates without the ones that repeat a
    card, at most `limit`, renumbered (v1, v2... / r2-1, r2-2...) so the ids
    stay dense. Overlaps are kept; annotate_vocabulary marks them."""
    cards = word_cards()
    kept = [
        dict(entry)
        for entry in entries
        if not (
            (match := learner_model.similar_card(entry.get("english") or "", cards))
            and match["exact"]
        )
    ][:limit]
    for number, entry in enumerate(kept, start=1):
        prefix = re.sub(r"\d+$", "", str(entry.get("id") or ""))
        entry["id"] = f"{prefix}{number}"
    return kept


def annotate_vocabulary(source_id: str, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Candidates of a set or text with `similar` - the card each one repeats
    or overlaps ({"id", "english", "exact"}) - for the picker's hint. Worked
    out on read, so it follows the cards as they change; entries that already
    are cards from this source get no hint."""
    cards = word_cards()
    picked = set(picked_vocabulary(source_id))
    annotated = []
    for entry in entries:
        entry = dict(entry)
        if entry.get("id") not in picked:
            match = learner_model.similar_card(entry.get("english") or "", cards)
            if match is not None:
                entry["similar"] = match
        annotated.append(entry)
    return annotated


def word_card_list(today: Optional[dt.date] = None) -> List[Dict[str, Any]]:
    """Every word card with its state and the other card it repeats or
    overlaps (`similar`, or None), newest pick first - the «Мои слова» list."""
    today = today or dt.date.today()
    bank = load_item_bank()
    states = item_states(bank)
    cards = word_cards()
    # Only cards sharing a word can overlap: compare within those, not n x n.
    by_stem: Dict[str, List[Dict[str, Any]]] = {}
    for item in cards:
        for stem in set(learner_model.word_stems(item["content"]["english"])):
            by_stem.setdefault(stem, []).append(item)
    listed = []
    for item in cards:
        neighbours = {
            other["id"]: other
            for stem in set(learner_model.word_stems(item["content"]["english"]))
            for other in by_stem.get(stem, [])
        }
        similar = learner_model.similar_card(
            item["content"]["english"], list(neighbours.values()), skip_id=item["id"]
        )
        listed.append({**card(item, states[item["id"]], today), "similar": similar})
    return listed


def delete_word_card(card_id: str, now: Optional[dt.datetime] = None) -> int:
    """Take a word card away wherever it was picked (a «remove» record per
    live pick, so the log stays append-only). Returns how many picks that
    was; 0 - no such card. Its attempts stay: picking it again brings them
    back."""
    ts = (now or dt.datetime.now()).isoformat(timespec="seconds")
    with _picks_lock:
        latest: Dict[tuple, Dict[str, Any]] = {}
        for record in sorted(load_word_picks(), key=lambda r: r["ts"]):
            latest[(record["set_id"], record.get("vocab_id"))] = record
        live = [
            record
            for record in latest.values()
            if record.get("action") == "add"
            and learner_model.item_id(
                learner_model.KIND_WORD, (record.get("word") or {}).get("english") or ""
            )
            == card_id
        ]
        for record in live:
            utils.append_jsonl(
                _picks_path(), {**record, "ts": ts, "action": "remove", "deleted": True}
            )
    if live:
        rebuild_item_bank()
    return len(live)


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
    """A topic as the screens show it: label, area, level, description, links."""
    return curriculum.topic_info(key)


_LESSON_WORK = (learner_model.SET_EXERCISE, config.LESSON_TASK_EXERCISE)


def _lesson_step(
    mastery: List[Dict[str, Any]], attempts_today: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """The optional «Сегодня» step «Урок дня» (R8; it replaced the AI-set step,
    decided with the user 2026-09-26): the lesson the learner's own mistakes
    ask for most, else the roadmap's «Продолжить», with its next action -
    theory, a set, or the spoken task.

    Optional because its actions cost money: it never counts towards "all
    done" or the minutes left. Done once a set or a spoken task of any
    lesson was finished today.
    """
    done = [a for a in attempts_today if a.get("exercise") in _LESSON_WORK and a.get("topic")]
    if done:
        last = done[-1]
        return {
            "kind": "lesson",
            "optional": True,
            "status": "done",
            "lesson": _step_lesson(last["topic"]),
            "exercise": last["exercise"],
            "score": learner_model.attempt_score(last),
            "minutes": 0,
        }
    priority = {row["key"]: row["priority"] for row in mastery}
    view = roadmap_view(priority)
    recommended = view["recommended"][:1]
    lesson = recommended[0]["id"] if recommended else view["continue"]
    if lesson is None:
        return None
    row = next(
        r for level in view["levels"] for m in level["modules"] for r in m["lessons"]
        if r["id"] == lesson
    )
    action = roadmap.next_action(row, lesson in theory_dates())
    waiting = unstarted_set(lesson) if action == roadmap.ACTION_SET else None
    cost = {
        roadmap.ACTION_THEORY: theory_cost_estimate(),
        roadmap.ACTION_SET: 0.0 if waiting else set_cost_estimate(),
        roadmap.ACTION_SPOKEN: 0.0,
    }[action]
    return {
        "kind": "lesson",
        "optional": True,
        "status": "todo",
        "lesson": _step_lesson(lesson),
        "reason": "mistakes" if recommended else "course",
        "speech_mistakes": row["speech_mistakes"],
        "action": action,
        "set_id": waiting["id"] if waiting else None,
        "cost_usd": cost,
        "anthropic_configured": config.get_anthropic_api_key() is not None,
        "minutes": round(config.WORKOUT_MINUTES_SET),
    }


def _step_lesson(lesson: str) -> Dict[str, Any]:
    info = topic_info(lesson)
    level = curriculum.LEVEL_BY_KEY.get(info["level"] or "")
    return {**info, "level_label": level.label if level else ""}


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
    # Measured in the browser from the audio; missing on older takes.
    silences = (session.drill or {}).get("silences")
    report: Dict[str, Any] = {
        "metrics": speech_drills.speech_metrics(words, fillers, silences),
        "timeline": speech_drills.timeline(words, fillers, silences),
    }
    if session.kind == config.KIND_SHADOWING and session.drill:
        report["reading"] = speech_drills.align_reading(
            str(session.drill.get("reference", "")), words
        )
    return report


def drill_score(session: utils.Session, report: Dict[str, Any]) -> Optional[float]:
    """The attempt score of a spoken take, or None when it is not logged.

    Shadowing scores its reading. A monologue (and an older «60 секунд» take)
    scores its fluency, but only as round 1 of its series - the spontaneous
    take; later rounds are practice (2026-09-27: every English monologue
    counts, not only a timed one). A language without filler detection has
    no fluency score, so such a take is not logged."""
    if session.kind == config.KIND_SHADOWING:
        return report.get("reading", {}).get("score")
    spoken = (config.KIND_TALK, config.KIND_MONOLOGUE)
    if session.kind in spoken and (session.drill or {}).get("round", 1) == 1:
        return report["metrics"].get("fluency_score")
    return None


def record_speech_drill(session: utils.Session) -> Optional[Dict[str, Any]]:
    """Log a transcribed spoken take (a drill or a monologue) as a topic
    attempt on the fluency topic.

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


def _series_takes() -> List[utils.Session]:
    """Takes that belong to a series: «Говорение» monologues (their `drill`
    names the series) and older «60 секунд» takes, oldest first."""
    return [
        s
        for s in utils.list_sessions()
        if s.kind == config.KIND_TALK
        or (s.kind == config.KIND_MONOLOGUE and (s.drill or {}).get("series"))
    ]


def talk_series() -> List[Dict[str, Any]]:
    """Spoken takes grouped into series (same prompt, rounds 1..N), newest
    first, each round with its measurements. A prompt of None is an own topic."""
    series: Dict[str, Dict[str, Any]] = {}
    for take in reversed(_series_takes()):
        drill = take.drill or {}
        key = str(drill.get("series") or take.id)
        entry = series.setdefault(
            key,
            {
                "series": key,
                "started_at": take.started_at.isoformat(timespec="seconds"),
                # The prompt as it was shown (own themes' prompts can be deleted).
                "prompt": {
                    key: drill.get(key) for key in ("prompt_id", "question", "hint", "theme")
                },
                "language": take.language_key,
                "time_limit": drill.get("time_limit"),
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
    return 1 + sum(1 for take in _series_takes() if take.drill.get("series") == series_id)


def _speech_step(today: dt.date, sessions: List[utils.Session]) -> Optional[Dict[str, Any]]:
    """The optional «Сегодня» step: a spoken warm-up - a «Говорение» series
    (a second round said today), or shadowing a passage. Done once either was
    transcribed today; an older «60 секунд» take counts too."""
    done = [
        s
        for s in sessions
        if (s.is_drill or (s.drill or {}).get("round", 1) >= 2)
        and s.kind != config.KIND_PICTURE
        and s.status == utils.STATUS_DONE
        and s.started_at.date() == today
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
    return {
        "kind": "speech",
        "optional": True,
        "status": "todo",
        # Half a list away from the monologue's, so the two never coincide.
        "prompt": theme_store.prompt_of_day(today, offset=1),
        "passage": speech_drills.pick_passage(shadowing_passages()),
        "minutes": round(config.WORKOUT_MINUTES_SPEECH),
    }


def _dictation_step(now: dt.datetime) -> Dict[str, Any]:
    """The «Сегодня» dictation step (mandatory, decided 2026-09-20).

    Done once DICTATION_DAILY_SENTENCES sentences have been dictated today,
    across any lesson. With no lesson imported yet the step is "empty": it
    invites an import instead of holding the whole workout back.
    """
    done = dictation_store.done_today(now)
    target = config.DICTATION_DAILY_SENTENCES
    lesson = dictation_store.next_lesson(now)
    step: Dict[str, Any] = {
        "kind": "dictation",
        "status": "done" if done >= target else "todo",
        "done_today": done,
        "target": target,
        "lesson": lesson,
        "minutes": 0 if done >= target else round(config.WORKOUT_MINUTES_DICTATION),
    }
    if lesson is None and not done:
        step.update({"status": "empty", "minutes": 0})
    return step


# ------------------------------------------------------------ daily workout
def today_workout(now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """The «Сегодня» workout, assembled by code (no LLM): cards, one live
    activity, dictation, then the optional steps. Each step says whether it is
    done today, judged from the attempts log and today's recordings.

    The cloze on improved_version was dropped on 2026-09-26: gaps in a text the
    learner remembers test memory of that text, not the rule."""
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

    # 2. Live activity: a monologue on today's prompt, recorded and analysed.
    # A picture description done today counts too: the slot is "speak once",
    # and so does any take of a «Говорение» series.
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
    live_step = {
        "kind": "monologue",
        "status": live_status,
        "session_id": live.id if live else None,
        "activity": live.kind if live else config.KIND_MONOLOGUE,
        "prompt": theme_store.prompt_of_day(today),
        "minutes": 0 if live_status == "done" else round(config.WORKOUT_MINUTES_MONOLOGUE),
    }

    # 3. Listening dictation: a few sentences of a YouTube lesson ($0).
    dictation_step = _dictation_step(now)
    # 4. Optional (its actions are paid): «Урок дня» of the roadmap.
    lesson_step = _lesson_step(snap["mastery"], attempts_today)
    # 5. Optional, Deepgram only: a spoken warm-up.
    speech_step = _speech_step(today, sessions)

    focus = next((row for row in snap["mastery"] if row["priority"] > 0), None)
    steps = [cards_step, live_step, dictation_step]
    steps += [lesson_step] if lesson_step else []
    steps += [speech_step] if speech_step else []

    active_days = {dt.datetime.fromisoformat(a["ts"]).date() for a in snap["attempts"]}
    active_days |= {s.started_at.date() for s in recorded}
    # A dictated sentence is practice too: it keeps the streak alive.
    active_days |= dictation_store.active_days()
    # So is a checked irregular verb.
    active_days |= verb_store.active_days()
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
    """Exercise results per day, newest first, with topic labels.

    Dictation is not in the attempts log (it trains listening, not one of the
    taxonomy's topics), so its per-day counts are merged in here - including
    on days when dictation was the only thing practised.
    """
    history = learner_model.daily_activity(load_attempts(), days)
    by_day = {entry["date"]: entry for entry in history}
    for day, counts in dictation_store.daily_counts().items():
        entry = by_day.get(day)
        if entry is None:
            entry = {"date": day, "cards": 0, "cards_correct": 0, "drills": []}
            by_day[day] = entry
            history.append(entry)
        entry["dictation"] = counts
    for entry in history:
        for drill in entry["drills"]:
            drill["label"] = curriculum.topic_label(drill["topic"])
    history.sort(key=lambda entry: entry["date"], reverse=True)
    return history[:days]


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


def average_claude_cost(purpose: str, fallback: float) -> float:
    """The average cost of one Claude call of `purpose` so far, or `fallback`
    until the usage log has one - the price shown next to a button."""
    entry = usage_summary(recent=0)["by_purpose"].get(f"anthropic:{purpose}") or {}
    return entry.get("avg_cost_usd") or fallback
