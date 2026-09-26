"""A module's entry test (Stage 8, R7): assembling and grading, no I/O.

Decided 2026-09-26: a short test over a whole roadmap module lets the learner
mark what they already know «уже знаю» honestly, and every missed question
points at its lesson. Claude writes the test once on a click (see
exercise_sets.ExerciseSetGenerator.write_module_test); everything here is
deterministic:

  * `assemble` keeps only well-formed questions tagged with one of the
    module's lessons, in course order, one choice and one gap per lesson;
  * `public` is what the browser gets before answering - no answers in it,
    so the page cannot leak them;
  * `grade` checks the answers on the server (choice: the option index; gap:
    case- and punctuation-insensitive, like set gaps) and rolls them up per
    lesson: a lesson is known when all of its questions are right.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from app import learner_model

GAP_MARK = "___"
TYPE_CHOICE = "choice"
TYPE_GAP = "gap"


def assemble(raw: Mapping[str, Any], lessons: Sequence[str]) -> List[Dict[str, Any]]:
    """Questions in lesson order with ids q1, q2, ...; malformed ones dropped."""
    choices: Dict[str, List[Dict[str, Any]]] = {}
    gaps: Dict[str, List[Dict[str, Any]]] = {}
    for item in raw.get("choices") or []:
        options = [str(o).strip() for o in item.get("options") or []]
        correct = item.get("correct")
        if (
            item.get("lesson") in lessons
            and str(item.get("question", "")).strip()
            and 2 <= len(options) <= 6
            and all(options)
            and isinstance(correct, int)
            and 0 <= correct < len(options)
        ):
            choices.setdefault(item["lesson"], []).append(
                {
                    "type": TYPE_CHOICE,
                    "question": item["question"].strip(),
                    "options": options,
                    "correct": correct,
                    "explanation": str(item.get("explanation", "")).strip(),
                }
            )
    for item in raw.get("gaps") or []:
        parts = str(item.get("sentence", "")).split(GAP_MARK)
        accept = [str(a).strip() for a in item.get("answers") or [] if str(a).strip()]
        if item.get("lesson") in lessons and len(parts) == 2 and accept:
            gaps.setdefault(item["lesson"], []).append(
                {
                    "type": TYPE_GAP,
                    "before": parts[0],
                    "after": parts[1],
                    "accept": accept,
                    "hint": str(item.get("hint", "")).strip(),
                    "explanation": str(item.get("explanation", "")).strip(),
                }
            )
    questions: List[Dict[str, Any]] = []
    for lesson in lessons:
        for question in choices.get(lesson, [])[:1] + gaps.get(lesson, [])[:1]:
            questions.append({"lesson": lesson, **question})
    for number, question in enumerate(questions, start=1):
        question["id"] = f"q{number}"
    return questions


def public(question: Mapping[str, Any]) -> Dict[str, Any]:
    """A question without its answer, for the page before it is answered."""
    hidden = {"correct", "accept", "explanation"}
    return {key: value for key, value in question.items() if key not in hidden}


def sentence(question: Mapping[str, Any]) -> str:
    if question.get("type") == TYPE_CHOICE:
        return str(question.get("question", ""))
    return f"{question.get('before', '')}{GAP_MARK}{question.get('after', '')}".strip()


def right_answer(question: Mapping[str, Any]) -> str:
    if question.get("type") == TYPE_CHOICE:
        return question["options"][question["correct"]]
    return question["accept"][0]


def grade(
    questions: Sequence[Mapping[str, Any]], answers: Mapping[str, str]
) -> Dict[str, Any]:
    """Results per question and per lesson; `score` is the share right."""
    results = []
    by_lesson: Dict[str, Dict[str, Any]] = {}
    for question in questions:
        given = str(answers.get(question["id"], "") or "").strip()
        if question["type"] == TYPE_CHOICE:
            correct = given.isdigit() and int(given) == question["correct"]
        else:
            correct = bool(given) and learner_model.answer_matches(given, question["accept"])
        results.append(
            {
                "id": question["id"],
                "lesson": question["lesson"],
                "answer": given,
                "correct": correct,
                "right_answer": right_answer(question),
                "explanation": question.get("explanation", ""),
            }
        )
        entry = by_lesson.setdefault(question["lesson"], {"right": 0, "total": 0})
        entry["total"] += 1
        entry["right"] += int(correct)
    for entry in by_lesson.values():
        entry["known"] = entry["right"] == entry["total"]
        entry["score"] = round(entry["right"] / entry["total"], 3)
    total = len(results)
    return {
        "results": results,
        "by_lesson": by_lesson,
        "score": round(sum(r["correct"] for r in results) / total, 3) if total else 0.0,
    }


def complete_enough(questions: Sequence[Mapping[str, Any]], lessons: Sequence[str]) -> bool:
    """A test is worth keeping only if every lesson has at least one question."""
    tested = {q["lesson"] for q in questions}
    return bool(lessons) and all(lesson in tested for lesson in lessons)
