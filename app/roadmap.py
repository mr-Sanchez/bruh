"""The roadmap's derived state: every lesson's status and where to continue.

Pure functions over the attempts log and the learner's marks, like
app.learner_model - nothing here reads files. The course itself (levels ->
modules -> lessons) is app.curriculum; this module only says how far the
learner has got in it.

Decided 2026-09-26: movement is free. Every lesson is open, «Продолжить» is
the first lesson in course order that is still to do, and «Пропустить» /
«Уже знаю» are marks, never locks. A mark says what the learner thinks; the
exercises say what they can do, so:

  * a lesson is *mastered* after LESSON_MASTERY_RUNS set runs at the pass
    score on different days - whatever it is marked;
  * otherwise a mark stands until the learner works on the lesson again
    (a set run or new theory after the mark): «уже знаю» followed by a 50 %
    run is "practising", not "known".
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from app import config, curriculum, learner_model

# Lesson statuses, in the order a lesson usually goes through them.
NOT_STARTED = "not_started"
THEORY = "theory"
PRACTISING = "practising"
MASTERED = "mastered"
SKIPPED = "skipped"
KNOWN = "known"
STATUSES = (NOT_STARTED, THEORY, PRACTISING, MASTERED, SKIPPED, KNOWN)

# What the learner may set by hand; None clears the mark.
MARKS = (SKIPPED, KNOWN)
# Lessons «Продолжить» passes over.
_SETTLED = frozenset({MASTERED, SKIPPED, KNOWN})
# Lessons that count as done in a module's or level's progress.
_DONE = frozenset({MASTERED, KNOWN})


def _parse(timestamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(timestamp)


def latest_marks(marks: Iterable[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """The mark in force per lesson: the newest record wins, a cleared mark drops out."""
    latest: Dict[str, Dict[str, Any]] = {}
    for record in sorted(marks, key=lambda r: _parse(r["ts"])):
        lesson = record.get("lesson")
        if lesson in curriculum.MODULE_OF_LESSON:
            latest[lesson] = dict(record)
    return {lesson: r for lesson, r in latest.items() if r.get("mark") in MARKS}


def lesson_status(
    lesson: str,
    runs: Sequence[Mapping[str, Any]],
    mark: Optional[Mapping[str, Any]] = None,
    theory_at: Optional[str] = None,
    spoken: Sequence[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    """One lesson's status and the numbers behind it.

    `runs` are the lesson topic's set-run attempts, `mark` its mark in force,
    `theory_at` when its theory was written (None until there is any),
    `spoken` its spoken-task attempts - work on the lesson ("practising"),
    but mastery is still measured by set runs only.
    """
    scores = [learner_model.attempt_score(run) for run in runs]
    passed_days = sorted(
        {_parse(run["ts"]).date() for run, score in zip(runs, scores)
         if score >= config.DRILL_PASS_SCORE}
    )
    activity = [_parse(run["ts"]) for run in list(runs) + list(spoken)]
    if theory_at:
        activity.append(_parse(theory_at))
    last_activity = max(activity) if activity else None

    if len(passed_days) >= config.LESSON_MASTERY_RUNS:
        status = MASTERED
    elif mark is not None and (last_activity is None or _parse(mark["ts"]) >= last_activity):
        status = mark["mark"]
    elif runs or spoken:
        status = PRACTISING
    elif theory_at:
        status = THEORY
    else:
        status = NOT_STARTED
    return {
        "id": lesson,
        "status": status,
        "mark": mark["mark"] if mark is not None else None,
        "runs": len(runs),
        "best_score": round(max(scores), 3) if scores else None,
        "passed_days": len(passed_days),
        "spoken_tasks": len(spoken),
        "best_spoken": (
            round(max(learner_model.attempt_score(a) for a in spoken), 3) if spoken else None
        ),
        "last_activity": last_activity.isoformat(timespec="seconds") if last_activity else None,
    }


def course(
    attempts: Sequence[Mapping[str, Any]],
    marks: Iterable[Mapping[str, Any]],
    *,
    theory_dates: Optional[Mapping[str, str]] = None,
    speech_mistakes: Optional[Mapping[str, int]] = None,
) -> Dict[str, Any]:
    """The whole roadmap with statuses: levels -> modules -> lessons, plus
    the lesson «Продолжить» opens (None once every lesson is settled).

    `speech_mistakes` - mistakes per topic found in analysed recordings, so
    the learner sees which lessons their own speech asks for.
    """
    theory_dates = theory_dates or {}
    speech_mistakes = speech_mistakes or {}
    in_force = latest_marks(marks)
    runs_by_lesson: Dict[str, List[Mapping[str, Any]]] = {}
    spoken_by_lesson: Dict[str, List[Mapping[str, Any]]] = {}
    for attempt in attempts:
        if not attempt.get("topic"):
            continue
        if attempt.get("exercise") == learner_model.SET_EXERCISE:
            runs_by_lesson.setdefault(attempt["topic"], []).append(attempt)
        elif attempt.get("exercise") == config.LESSON_TASK_EXERCISE:
            spoken_by_lesson.setdefault(attempt["topic"], []).append(attempt)

    lessons: Dict[str, Dict[str, Any]] = {}
    for lesson in curriculum.LESSON_IDS:
        row = lesson_status(
            lesson,
            runs_by_lesson.get(lesson, []),
            in_force.get(lesson),
            theory_dates.get(lesson),
            spoken_by_lesson.get(lesson, []),
        )
        topic = curriculum.TOPIC_BY_KEY[lesson]
        row.update(
            label=topic.label,
            area=topic.area,
            area_label=curriculum.AREA_BY_KEY[topic.area].label,
            speech_mistakes=int(speech_mistakes.get(lesson, 0)),
        )
        lessons[lesson] = row

    levels = []
    for level in curriculum.LEVELS:
        modules = []
        for module in curriculum.MODULES:
            if module.level != level.key:
                continue
            rows = [lessons[lesson] for lesson in module.lessons]
            modules.append(
                {"key": module.key, "title": module.title, **_tally(rows), "lessons": rows}
            )
        level_rows = [row for m in modules for row in m["lessons"]]
        levels.append(
            {"key": level.key, "label": level.label, **_tally(level_rows), "modules": modules}
        )

    counts = {status: 0 for status in STATUSES}
    for row in lessons.values():
        counts[row["status"]] += 1
    next_lesson = next(
        (lesson for lesson in curriculum.LESSON_IDS if lessons[lesson]["status"] not in _SETTLED),
        None,
    )
    return {
        "levels": levels,
        "counts": counts,
        "total": len(lessons),
        "continue": next_lesson,
        "mastery_runs": config.LESSON_MASTERY_RUNS,
        "pass_score": config.DRILL_PASS_SCORE,
    }


def recommend(
    course_data: Mapping[str, Any],
    priority: Mapping[str, float],
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Lessons the learner's own speech asks for (R8): topics with priority
    (learner_model.topic_mastery - weakness in speech scaled by exercise
    accuracy), highest first. A mastered lesson is left out; one marked
    «уже знаю» or «пропущен» is not - the recordings say otherwise."""
    rows = [
        {**row, "priority": priority[row["id"]]}
        for level in course_data["levels"]
        for module in level["modules"]
        for row in module["lessons"]
        if priority.get(row["id"], 0) > 0 and row["status"] != MASTERED
    ]
    rows.sort(key=lambda row: (-row["priority"], curriculum.LESSON_IDS.index(row["id"])))
    return rows[:limit]


# What to do next in a lesson, in this order: read the theory, do a set, say
# it in a spoken task, then more sets until it is mastered.
ACTION_THEORY = "theory"
ACTION_SET = "set"
ACTION_SPOKEN = "spoken"


def next_action(row: Mapping[str, Any], has_theory: bool) -> str:
    if not has_theory:
        return ACTION_THEORY
    if row.get("runs", 0) and not row.get("spoken_tasks", 0):
        return ACTION_SPOKEN
    return ACTION_SET


def _tally(rows: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    return {"total": len(rows), "done": sum(1 for row in rows if row["status"] in _DONE)}
