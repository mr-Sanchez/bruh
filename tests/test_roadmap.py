"""Offline tests for the roadmap's lesson statuses (app/roadmap.py)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import curriculum, roadmap  # noqa: E402

LESSON = "present_perfect"


def run(ts: str, score: float, topic: str = LESSON) -> Dict:
    return {"ts": ts, "exercise": "ai_set", "topic": topic, "score": score, "correct": score >= 0.8}


def mark(ts: str, value, lesson: str = LESSON) -> Dict:
    return {"ts": ts, "lesson": lesson, "mark": value}


def status_of(attempts: List[Dict], marks: List[Dict] = (), **kwargs) -> Dict:
    data = roadmap.course(attempts, marks, **kwargs)
    for level in data["levels"]:
        for module in level["modules"]:
            for lesson in module["lessons"]:
                if lesson["id"] == LESSON:
                    return lesson
    raise AssertionError("lesson not in the course")


class LessonStatusTests(unittest.TestCase):
    def test_untouched_lesson_is_not_started(self) -> None:
        self.assertEqual(status_of([])["status"], roadmap.NOT_STARTED)

    def test_theory_then_practice(self) -> None:
        theory = {LESSON: "2026-09-20T10:00:00"}
        self.assertEqual(status_of([], theory_dates=theory)["status"], roadmap.THEORY)
        row = status_of([run("2026-09-21T10:00:00", 0.5)], theory_dates=theory)
        self.assertEqual(row["status"], roadmap.PRACTISING)
        self.assertEqual((row["runs"], row["best_score"]), (1, 0.5))

    def test_mastered_needs_two_passing_runs_on_different_days(self) -> None:
        same_day = [run("2026-09-21T10:00:00", 0.9), run("2026-09-21T18:00:00", 1.0)]
        self.assertEqual(status_of(same_day)["status"], roadmap.PRACTISING)
        two_days = [run("2026-09-21T10:00:00", 0.9), run("2026-09-23T09:00:00", 0.8)]
        row = status_of(two_days)
        self.assertEqual(row["status"], roadmap.MASTERED)
        self.assertEqual(row["passed_days"], 2)

    def test_failing_runs_do_not_count_towards_mastery(self) -> None:
        runs = [run("2026-09-21T10:00:00", 0.9), run("2026-09-22T10:00:00", 0.7)]
        self.assertEqual(status_of(runs)["status"], roadmap.PRACTISING)

    def test_other_exercises_and_topics_are_ignored(self) -> None:
        attempts = [
            {"ts": "2026-09-21T10:00:00", "item_id": "fix-1", "exercise": "review_card",
             "correct": True},
            {"ts": "2026-09-21T10:00:00", "exercise": "talk", "topic": LESSON, "score": 1.0},
            run("2026-09-21T10:00:00", 1.0, topic="past_simple"),
        ]
        self.assertEqual(status_of(attempts)["status"], roadmap.NOT_STARTED)

    def test_mark_stands_until_the_lesson_is_worked_on_again(self) -> None:
        marks = [mark("2026-09-21T10:00:00", "known")]
        self.assertEqual(status_of([], marks)["status"], roadmap.KNOWN)
        before = [run("2026-09-20T10:00:00", 0.4)]
        self.assertEqual(status_of(before, marks)["status"], roadmap.KNOWN)
        after = [run("2026-09-22T10:00:00", 0.4)]
        row = status_of(after, marks)
        self.assertEqual(row["status"], roadmap.PRACTISING)
        self.assertEqual(row["mark"], "known")

    def test_mastery_beats_any_mark(self) -> None:
        runs = [run("2026-09-21T10:00:00", 0.9), run("2026-09-22T10:00:00", 0.9)]
        marks = [mark("2026-09-23T10:00:00", "skipped")]
        self.assertEqual(status_of(runs, marks)["status"], roadmap.MASTERED)

    def test_newest_mark_wins_and_null_clears(self) -> None:
        marks = [mark("2026-09-21T10:00:00", "skipped"), mark("2026-09-22T10:00:00", "known")]
        self.assertEqual(status_of([], marks)["status"], roadmap.KNOWN)
        marks.append(mark("2026-09-23T10:00:00", None))
        row = status_of([], marks)
        self.assertEqual((row["status"], row["mark"]), (roadmap.NOT_STARTED, None))

    def test_marks_on_unknown_lessons_are_dropped(self) -> None:
        marks = [mark("2026-09-21T10:00:00", "known", lesson="filler_words_fluency"),
                 mark("2026-09-21T10:00:00", "known", lesson="no_such_lesson")]
        self.assertEqual(roadmap.latest_marks(marks), {})


class RecommendationTests(unittest.TestCase):
    def test_lessons_follow_speech_priority_and_skip_mastered_ones(self) -> None:
        mastered = [run("2026-09-21T10:00:00", 0.9, "past_simple"),
                     run("2026-09-22T10:00:00", 0.9, "past_simple")]
        marks = [mark("2026-09-21T10:00:00", "known", "articles_basic")]
        data = roadmap.course(mastered, marks)
        priority = {"past_simple": 3.0, "articles_basic": 1.0, LESSON: 2.0, "other": 5.0,
                    "questions": 0.0}
        ids = [row["id"] for row in roadmap.recommend(data, priority)]
        # Mastered is out, «уже знаю» stays (speech says otherwise), no priority = no advice.
        self.assertEqual(ids, [LESSON, "articles_basic"])
        self.assertEqual(roadmap.recommend(data, priority, limit=1)[0]["priority"], 2.0)

    def test_next_action_goes_theory_set_spoken_set(self) -> None:
        self.assertEqual(roadmap.next_action({"runs": 3}, has_theory=False), "theory")
        self.assertEqual(roadmap.next_action({"runs": 0}, has_theory=True), "set")
        self.assertEqual(roadmap.next_action({"runs": 1, "spoken_tasks": 0}, True), "spoken")
        self.assertEqual(roadmap.next_action({"runs": 1, "spoken_tasks": 1}, True), "set")


class CourseTests(unittest.TestCase):
    def test_tree_follows_the_curriculum(self) -> None:
        data = roadmap.course([], [])
        self.assertEqual([level["key"] for level in data["levels"]],
                         [level.key for level in curriculum.LEVELS])
        ids = [lesson["id"] for level in data["levels"] for module in level["modules"]
               for lesson in module["lessons"]]
        self.assertEqual(ids, list(curriculum.LESSON_IDS))
        self.assertEqual(data["total"], len(curriculum.LESSON_IDS))
        self.assertEqual(data["counts"][roadmap.NOT_STARTED], data["total"])

    def test_continue_skips_settled_lessons_in_course_order(self) -> None:
        first, second, third = curriculum.LESSON_IDS[:3]
        self.assertEqual(roadmap.course([], [])["continue"], first)
        marks = [mark("2026-09-21T10:00:00", "known", first),
                 mark("2026-09-21T10:00:00", "skipped", second)]
        self.assertEqual(roadmap.course([], marks)["continue"], third)
        # A lesson in progress is still to do.
        practised = [run("2026-09-21T10:00:00", 0.5, topic=first)]
        self.assertEqual(roadmap.course(practised, [])["continue"], first)

    def test_continue_is_none_when_everything_is_settled(self) -> None:
        marks = [mark("2026-09-21T10:00:00", "known", lesson) for lesson in curriculum.LESSON_IDS]
        data = roadmap.course([], marks)
        self.assertIsNone(data["continue"])
        self.assertEqual(data["levels"][0]["done"], data["levels"][0]["total"])

    def test_done_counts_mastered_and_known_but_not_skipped(self) -> None:
        first, second = curriculum.MODULES[0].lessons[:2]
        marks = [mark("2026-09-21T10:00:00", "known", first),
                 mark("2026-09-21T10:00:00", "skipped", second)]
        module = roadmap.course([], marks)["levels"][0]["modules"][0]
        self.assertEqual(module["done"], 1)

    def test_speech_mistakes_are_attached(self) -> None:
        row = status_of([], speech_mistakes={LESSON: 3})
        self.assertEqual(row["speech_mistakes"], 3)
        self.assertEqual(row["label"], curriculum.topic_label(LESSON))


if __name__ == "__main__":
    unittest.main()
