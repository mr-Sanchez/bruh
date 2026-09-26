"""Offline tests for a module's entry test (Stage 8, R7): assembling what
Claude wrote, grading on the server, runs and attempts, marking passed
lessons «уже знаю»."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import api, config, curriculum, learner_store, module_test  # noqa: E402
from app.server import app as fastapi_app  # noqa: E402
from tests.test_api import FakeGenerator  # noqa: E402

MODULE = "a2_communication"  # confusable_words, small_talk, polite_requests
LESSONS = list(curriculum.MODULE_BY_KEY[MODULE].lessons)


def raw_test():
    return {
        "choices": [
            {"lesson": "small_talk", "question": "How ___ you?", "options": ["is", "are"],
             "correct": 1, "explanation": "e"},
            {"lesson": "confusable_words", "question": "He ___ me.", "options": ["said", "told"],
             "correct": 1, "explanation": "e"},
            {"lesson": "nope", "question": "x", "options": ["a", "b"], "correct": 0,
             "explanation": ""},
            {"lesson": "small_talk", "question": "bad", "options": ["a"], "correct": 0,
             "explanation": ""},
            {"lesson": "polite_requests", "question": "q", "options": ["a", "b"], "correct": 5,
             "explanation": ""},
        ],
        "gaps": [
            {"lesson": "polite_requests", "sentence": "___ you help me?", "answers": ["Could"],
             "explanation": "e"},
            {"lesson": "small_talk", "sentence": "no blank", "answers": ["x"], "explanation": ""},
        ],
    }


class AssembleGradeTests(unittest.TestCase):
    def test_assemble_keeps_valid_questions_in_lesson_order(self) -> None:
        questions = module_test.assemble(raw_test(), LESSONS)
        self.assertEqual(
            [(q["id"], q["lesson"], q["type"]) for q in questions],
            [("q1", "confusable_words", "choice"), ("q2", "small_talk", "choice"),
             ("q3", "polite_requests", "gap")],
        )
        self.assertTrue(module_test.complete_enough(questions, LESSONS))
        self.assertFalse(module_test.complete_enough(questions[:2], LESSONS))

    def test_public_questions_carry_no_answers(self) -> None:
        for question in module_test.assemble(raw_test(), LESSONS):
            shown = module_test.public(question)
            self.assertFalse({"correct", "accept", "explanation"} & set(shown))

    def test_grade_by_question_and_lesson(self) -> None:
        questions = module_test.assemble(raw_test(), LESSONS)
        graded = module_test.grade(questions, {"q1": "1", "q2": "0", "q3": "could!"})
        self.assertEqual([r["correct"] for r in graded["results"]], [True, False, True])
        self.assertEqual(graded["results"][1]["right_answer"], "are")
        self.assertTrue(graded["by_lesson"]["confusable_words"]["known"])
        self.assertFalse(graded["by_lesson"]["small_talk"]["known"])
        self.assertAlmostEqual(graded["score"], 0.667)
        empty = module_test.grade(questions, {})
        self.assertEqual(empty["score"], 0.0)


class ModuleTestApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        base_dir_patch.start()
        self.addCleanup(base_dir_patch.stop)
        self.generator = FakeGenerator()
        fastapi_app.dependency_overrides[api.get_generator_factory] = lambda: (
            lambda: self.generator
        )
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.client = TestClient(fastapi_app)
        self.url = f"/api/modules/{MODULE}/test"

    def test_write_take_and_mark_the_passed_lessons(self) -> None:
        empty = self.client.get(self.url).json()
        self.assertIsNone(empty["test"])
        self.assertEqual([l["key"] for l in empty["lessons"]], LESSONS)

        created = self.client.post(self.url)
        self.assertEqual(created.status_code, 201, created.text)
        test = created.json()["test"]
        self.assertEqual(len(test["questions"]), 2 * len(LESSONS))
        self.assertNotIn("correct", test["questions"][0])
        self.assertIsNone(test["last_run"])

        # Right on the first two lessons, one gap wrong on the third.
        answers = {q["id"]: ("1" if q["type"] == "choice" else "done") for q in test["questions"]}
        answers["q6"] = "nope"
        body = self.client.post(f"{self.url}/{test['id']}/submit", json={"answers": answers}).json()
        run = body["test"]["last_run"]
        self.assertEqual([run["by_lesson"][l]["known"] for l in LESSONS], [True, True, False])
        attempts = [a for a in learner_store.load_attempts() if a["exercise"] == "module_test"]
        self.assertEqual(sorted(a["topic"] for a in attempts), sorted(LESSONS))

        marked = self.client.post(
            f"/api/modules/{MODULE}/mark-known", json={"lessons": LESSONS[:2]}
        ).json()
        self.assertEqual(
            [l["status"] for l in marked["lessons"]], ["known", "known", "not_started"]
        )

    def test_a_new_test_avoids_the_old_sentences_and_old_tests_stay(self) -> None:
        self.client.post(self.url)
        second = self.client.post(self.url).json()
        self.assertIn("small_talk choice 1 ___.", self.generator.module_tests[1]["avoid"])
        self.assertEqual([t["id"] for t in second["tests"]], ["test-1", "test-2"])
        self.assertEqual(second["test"]["id"], "test-2")
        first = self.client.get(self.url, params={"test": "test-1"}).json()["test"]
        self.assertEqual(first["id"], "test-1")
        usage = self.client.get("/api/usage").json()["by_purpose"]["anthropic:module_test"]
        self.assertEqual(usage["calls"], 2)

    def test_an_incomplete_test_is_refused_and_bad_requests(self) -> None:
        original = self.generator.write_module_test

        def partial(title, lessons, avoid=()):
            return original(title, lessons[:1], avoid)  # questions for one lesson only

        with patch.object(self.generator, "write_module_test", side_effect=partial):
            self.assertEqual(self.client.post(self.url).status_code, 502)
        self.assertEqual(learner_store.load_module_tests(MODULE), [])

        self.assertEqual(self.client.get("/api/modules/nope/test").status_code, 404)
        self.assertEqual(self.client.post(f"{self.url}/test-9/submit", json={}).status_code, 404)
        wrong = self.client.post(
            f"/api/modules/{MODULE}/mark-known", json={"lessons": ["past_simple"]}
        )
        self.assertEqual(wrong.status_code, 400)


if __name__ == "__main__":
    unittest.main()
