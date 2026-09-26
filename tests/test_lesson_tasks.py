"""Offline tests for a lesson's spoken task (Stage 8, R6): tasks written on a
click per context, the take stored with its task, the analysis checking the
lesson's rule, one attempt per take, and the roadmap status."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import api, config, curriculum, learner_store, roadmap, utils  # noqa: E402
from app.analyzer import ClaudeAnalyzer, LessonAnalysis, LessonCheck  # noqa: E402
from app.server import app as fastapi_app  # noqa: E402
from tests.test_api import FakeAnalyzer, FakeGenerator  # noqa: E402

LESSON = "past_perfect"


class LessonTaskApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        base_dir_patch.start()
        self.addCleanup(base_dir_patch.stop)
        self.generator = FakeGenerator()
        self.analyzer = FakeAnalyzer()
        fastapi_app.dependency_overrides[api.get_generator_factory] = lambda: (
            lambda: self.generator
        )
        fastapi_app.dependency_overrides[api.get_analyzer_factory] = lambda: (
            lambda: self.analyzer
        )
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.client = TestClient(fastapi_app)

    def _typed_take(self, **fields) -> dict:
        """A lesson take, typed so no Deepgram fake is needed (the route is the same)."""
        response = self.client.post(
            "/api/sessions",
            data={"text": "When I came, they had already left.", "language": "en-US", **fields},
        )
        return response

    def test_tasks_are_written_per_context_and_appended(self) -> None:
        url = f"/api/lessons/{LESSON}/tasks"
        self.assertEqual(self.client.get(url).json()["tasks"], [])
        first = self.client.post(url, json={"theme": {"key": "travel"}}).json()
        self.assertEqual([t["id"] for t in first["tasks"]], ["t1", "t2", "t3"])
        self.assertEqual(first["tasks"][0]["theme"], {"key": "travel", "label": "Путешествия"})
        self.assertEqual(self.generator.task_requests[0]["level"], "b1")

        second = self.client.post(url, json={"theme": {"label": "ремонт машины"}}).json()
        self.assertEqual(second["added"], ["t4", "t5", "t6"])
        self.assertEqual(second["tasks"][3]["theme"], {"key": None, "label": "ремонт машины"})
        usage = self.client.get("/api/usage").json()["by_purpose"]["anthropic:lesson_tasks"]
        self.assertEqual(usage["calls"], 2)
        self.assertEqual(self.client.get("/api/lessons/other/tasks").status_code, 404)

    def test_a_take_is_analysed_with_the_rule_in_focus_and_logged_once(self) -> None:
        self.client.post(f"/api/lessons/{LESSON}/tasks", json={"theme": {"key": "travel"}})
        created = self._typed_take(lesson_id=LESSON, task_id="t2")
        self.assertEqual(created.status_code, 202, created.text)
        session_id = created.json()["session_id"]
        detail = self.client.get(f"/api/sessions/{session_id}").json()
        self.assertEqual(detail["kind"], "monologue")
        self.assertEqual(detail["lesson"]["task"]["question"], "Task 1.2?")
        self.assertEqual(detail["lesson"]["label"], curriculum.topic_label(LESSON))

        analysis = self.client.post(f"/api/sessions/{session_id}/analyze").json()["analysis"]
        focus = self.analyzer.last_kwargs["lesson"]
        self.assertEqual((focus["key"], focus["task"], focus["use"]),
                         (LESSON, "Task 1.2?", "Используйте X."))
        self.assertEqual(analysis["lesson"]["check"]["score"], 8)

        attempts = [a for a in learner_store.load_attempts() if a["exercise"] == "lesson_task"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual((attempts[0]["topic"], attempts[0]["lesson_id"]), (LESSON, LESSON))
        self.assertAlmostEqual(attempts[0]["score"], round(7 / 9, 3))

        # A forced re-analysis replaces the analysis, never adds an attempt.
        self.client.post(f"/api/sessions/{session_id}/analyze", json={"force": True})
        attempts = [a for a in learner_store.load_attempts() if a["exercise"] == "lesson_task"]
        self.assertEqual(len(attempts), 1)

        row = self.client.get(f"/api/roadmap/lessons/{LESSON}").json()
        self.assertEqual((row["status"], row["spoken_tasks"], row["runs"]), ("practising", 1, 0))

    def test_a_plain_monologue_has_no_lesson_check(self) -> None:
        session_id = self._typed_take().json()["session_id"]
        analysis = self.client.post(f"/api/sessions/{session_id}/analyze").json()["analysis"]
        self.assertIsNone(self.analyzer.last_kwargs["lesson"])
        self.assertNotIn("lesson", analysis)
        self.assertEqual(learner_store.load_attempts(), [])

    def test_bad_lesson_takes_are_refused(self) -> None:
        self.client.post(f"/api/lessons/{LESSON}/tasks", json={"theme": {"key": "travel"}})
        self.assertEqual(self._typed_take(lesson_id=LESSON, task_id="t9").status_code, 404)
        self.assertEqual(self._typed_take(lesson_id="other", task_id="t1").status_code, 404)
        self.assertEqual(self._typed_take(task_id="t1").status_code, 404)
        picture = self._typed_take(lesson_id=LESSON, task_id="t1", kind="picture")
        self.assertEqual(picture.status_code, 400)
        self.assertEqual(utils.list_sessions(), [])


class LessonCheckSchemaTests(unittest.TestCase):
    def test_the_analyzer_asks_for_the_lesson_check_and_clamps_it(self) -> None:
        calls = []
        parsed = LessonAnalysis(
            summary="s",
            lesson_check=LessonCheck(score=14, verdict="v", good_uses=[], missed=[]),
        )

        def parse(**kwargs):
            calls.append(kwargs)
            usage = SimpleNamespace(input_tokens=1, output_tokens=1,
                                    cache_creation_input_tokens=0, cache_read_input_tokens=0)
            return SimpleNamespace(parsed_output=parsed, stop_reason="end_turn", usage=usage)

        client = SimpleNamespace(messages=SimpleNamespace(parse=parse))
        analyzer = ClaudeAnalyzer("key", client_factory=lambda _k: client)
        lesson = {"key": LESSON, "label": "Past Perfect", "description": "d",
                  "task": "Tell a story.", "use": "had done"}
        result = analyzer.analyze("I had gone.", config.default_profile(), lesson=lesson)

        self.assertIs(calls[0]["output_format"], LessonAnalysis)
        self.assertIn("SPOKEN TASK", calls[0]["system"])
        request = calls[0]["messages"][0]["content"][0]["text"]
        self.assertIn("Construction to use: had done", request)
        self.assertEqual(result.lesson_check["score"], 10)


class RoadmapSpokenTests(unittest.TestCase):
    def test_spoken_tasks_are_work_but_not_mastery(self) -> None:
        spoken = [
            {"ts": f"2026-09-2{d}T10:00:00", "exercise": "lesson_task", "topic": LESSON,
             "score": 1.0}
            for d in (1, 2)
        ]
        row = roadmap.lesson_status(LESSON, [], None, None, spoken)
        self.assertEqual((row["status"], row["spoken_tasks"], row["best_spoken"]),
                         (roadmap.PRACTISING, 2, 1.0))
        marked = roadmap.lesson_status(
            LESSON, [], {"ts": "2026-09-20T10:00:00", "mark": "known"}, None, spoken
        )
        self.assertEqual(marked["status"], roadmap.PRACTISING)


if __name__ == "__main__":
    unittest.main()
