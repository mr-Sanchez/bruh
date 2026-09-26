"""Offline tests for a roadmap lesson's exercise sets (Stage 8, R5): the lesson's
level and theory reach the generator, sets never repeat earlier sentences,
runs carry the lesson id, and the lesson page gets its status."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import api, config, curriculum, exercise_sets, learner_store  # noqa: E402
from app.server import app as fastapi_app  # noqa: E402
from tests.test_api import SET_EXERCISES, FakeGenerator  # noqa: E402
from tests.test_theory import CONTENT, FakeWriter  # noqa: E402

LESSON = "past_simple"
GOOD = {
    "ex1": {"answer": "shipped"},
    "ex2": {"answer": "I went there yesterday.", "correct": True},
    "ex3": {"answer": "I have already fixed the bug."},
    "ex4": {"answer": "We had a call yesterday."},
}


class RepeatTests(unittest.TestCase):
    def test_repeats_of_earlier_sentences_are_dropped_and_ids_renumbered(self) -> None:
        exercises = [dict(e) for e in SET_EXERCISES]
        earlier = ["i go there YESTERDAY", "Yesterday we ___ it."]
        kept = exercise_sets.drop_repeats(exercises, earlier)
        self.assertEqual([e["type"] for e in kept], ["translate", "translate"])
        self.assertEqual([e["id"] for e in kept], ["ex1", "ex2"])

    def test_a_repeat_inside_one_set_is_dropped_too(self) -> None:
        twice = [dict(SET_EXERCISES[2]), dict(SET_EXERCISES[2])]
        self.assertEqual(len(exercise_sets.drop_repeats(twice, [])), 1)

    def test_the_request_carries_the_lesson_level_and_theory(self) -> None:
        topic = curriculum.topic_info(LESSON)
        text = exercise_sets._generation_request(
            topic, [], [], None, {"level": "a2", "theory": CONTENT}
        )
        self.assertIn("A2 (elementary)", text)
        self.assertIn(CONTENT["summary"], text)
        self.assertIn("Форма", text)
        self.assertIn("remember: depend on", text)
        without = exercise_sets._generation_request(topic, [], [], None, {"level": "b1"})
        self.assertNotIn("theory", without)


class LessonSetApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        base_dir_patch.start()
        self.addCleanup(base_dir_patch.stop)
        self.generator = FakeGenerator()
        self.writer = FakeWriter()
        fastapi_app.dependency_overrides[api.get_generator_factory] = lambda: (
            lambda: self.generator
        )
        fastapi_app.dependency_overrides[api.get_theory_writer_factory] = lambda: (
            lambda: self.writer
        )
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.client = TestClient(fastapi_app)

    def _create(self, force: bool = True) -> dict:
        response = self.client.post("/api/practice/sets", json={"topic": LESSON, "force": force})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["set"]

    def test_a_lesson_set_follows_its_latest_theory_and_logs_the_lesson(self) -> None:
        first = self._create()
        self.assertEqual(self.generator.generated[0]["lesson"], {"level": "a2", "theory": None})
        self.assertEqual((first["lesson_id"], first["theory_version"]), (LESSON, None))

        self.client.post(f"/api/lessons/{LESSON}/theory")
        self.client.post(f"/api/lessons/{LESSON}/theory")
        second = self._create()
        lesson = self.generator.generated[1]["lesson"]
        self.assertEqual(lesson["theory"]["summary"], "Версия 2")
        self.assertEqual(second["theory_version"], 1)
        # Earlier sentences of the topic go into the request.
        self.assertIn("Я уже починил баг.", self.generator.generated[1]["avoid"])

        response = self.client.post(
            f"/api/practice/sets/{first['id']}/submit",
            json={"answers": [{"exercise_id": k, **v} for k, v in GOOD.items()]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        run = [a for a in learner_store.load_attempts() if a["exercise"] == "ai_set"][-1]
        self.assertEqual((run["topic"], run["lesson_id"]), (LESSON, LESSON))

        row = self.client.get(f"/api/roadmap/lessons/{LESSON}").json()
        self.assertEqual((row["status"], row["runs"], row["passed_days"]), ("practising", 1, 1))
        self.assertEqual(row["level"]["key"], "a2")
        self.assertEqual(row["mastery_runs"], config.LESSON_MASTERY_RUNS)
        self.assertEqual(self.client.get("/api/roadmap/lessons/other").status_code, 404)

    def test_a_set_that_only_repeats_old_sentences_is_refused(self) -> None:
        self._create()
        self.generator.generated.clear()  # the fake makes the next set identical to the first
        response = self.client.post("/api/practice/sets", json={"topic": LESSON, "force": True})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(learner_store.list_sets(LESSON)), 1)
        # The call was paid for, so it is in the usage log all the same.
        usage = self.client.get("/api/usage").json()["by_purpose"]["anthropic:exercise_set"]
        self.assertEqual(usage["calls"], 2)


if __name__ == "__main__":
    unittest.main()
