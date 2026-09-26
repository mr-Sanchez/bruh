"""Offline tests for lesson theory (Stage 8, R4): the Claude layer with a fake
client, storage of versions, the /api/lessons/<id>/theory routes and the
roadmap's «теория» status."""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import api, config, curriculum, learner_store, theory  # noqa: E402
from app.analyzer import AnalysisError  # noqa: E402
from app.exercise_sets import ClaudeCall  # noqa: E402
from app.server import app as fastapi_app  # noqa: E402
from tests.test_learner_store import ANALYSIS  # noqa: E402
from tests.test_progress_store import write_analysed_session  # noqa: E402

CONTENT = {
    "summary": "Предлог после глагола запоминается вместе с глаголом.",
    "sections": [
        {"heading": "Форма", "text": "Первый абзац.\n\nВторой абзац.",
         "examples": [{"english": "It depends on you.", "russian": "Это зависит от тебя."}]}
    ],
    "typical_mistakes": [{"wrong": "depend from", "right": "depend on", "why": "Калька."}],
    "own_mistakes": [],
    "remember": ["depend on"],
}


class FakeClient:
    def __init__(self, parsed) -> None:
        self.parsed = parsed
        self.calls: List[Dict] = []
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            parsed_output=self.parsed,
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=2000,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0),
            id="msg-1",
        )


class TheoryWriterTests(unittest.TestCase):
    def test_request_names_topic_level_and_own_mistakes(self) -> None:
        client = FakeClient(theory.LessonTheory.model_validate(CONTENT))
        writer = theory.TheoryWriter("key", client_factory=lambda _key: client)
        topic = curriculum.topic_info("dependent_prepositions")
        result = writer.write(topic, "b1", [{"said": "depend from", "correct": "depend on"}])

        call = client.calls[0]
        self.assertEqual(call["model"], config.THEORY_MODEL)
        request = call["messages"][0]["content"]
        self.assertIn("dependent_prepositions", request)
        self.assertIn("B1", request)
        self.assertIn('said: "depend from"', request)
        self.assertEqual(result.content["summary"], CONTENT["summary"])
        self.assertEqual(result.call.effort, config.THEORY_EFFORT)

    def test_empty_theory_is_an_error(self) -> None:
        client = FakeClient(theory.LessonTheory(summary="", sections=[]))
        writer = theory.TheoryWriter("key", client_factory=lambda _key: client)
        with self.assertRaises(AnalysisError):
            writer.write(curriculum.topic_info("past_simple"), "a2")

    def test_own_mistakes_come_from_fix_items_only(self) -> None:
        items = [
            {"kind": "fix", "content": {"quote": "a", "correction": "b"}},
            {"kind": "pattern", "content": {"rule": "r"}},
            {"kind": "fix", "content": {"quote": "", "correction": "b"}},
        ]
        self.assertEqual(theory.own_mistakes_from_items(items), [{"said": "a", "correct": "b"}])


class FakeWriter:
    has_api_key = True

    def __init__(self) -> None:
        self.calls: List[Dict] = []

    def write(self, topic, level, own_mistakes=()):
        self.calls.append({"topic": topic["key"], "level": level, "own": list(own_mistakes)})
        content = {**CONTENT, "summary": f"Версия {len(self.calls)}"}
        usage = {"input_tokens": 1000, "output_tokens": 2000}
        return theory.TheoryResult(content=content, call=ClaudeCall(config.THEORY_MODEL, usage,
                                                                      "req", "low"))


class TheoryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        base_dir_patch.start()
        self.addCleanup(base_dir_patch.stop)
        self.writer = FakeWriter()
        fastapi_app.dependency_overrides[api.get_theory_writer_factory] = lambda: (
            lambda: self.writer
        )
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.client = TestClient(fastapi_app)

    def _status(self, lesson: str) -> str:
        data = self.client.get("/api/roadmap").json()
        return next(
            row["status"]
            for level in data["levels"]
            for module in level["modules"]
            for row in module["lessons"]
            if row["id"] == lesson
        )

    def test_theory_is_written_on_a_click_and_every_version_is_kept(self) -> None:
        url = "/api/lessons/prepositions_time_place/theory"
        empty = self.client.get(url).json()
        self.assertIsNone(empty["theory"])
        self.assertEqual(empty["cost_estimate_usd"], config.THEORY_COST_ESTIMATE_USD)
        self.assertEqual(self._status("prepositions_time_place"), "not_started")

        # The learner's own mistake on the topic goes into the request.
        write_analysed_session(config.recordings_dir(), "s1", dt.datetime(2026, 9, 1, 10), ANALYSIS)
        learner_store.refresh_after_analysis()
        first = self.client.post(url).json()
        self.assertEqual(first["theory"]["summary"], "Версия 1")
        self.assertEqual(self.writer.calls[0]["level"], "a2")
        self.assertEqual(
            self.writer.calls[0]["own"], [{"said": "responsible of", "correct": "responsible for"}]
        )
        self.assertEqual(self._status("prepositions_time_place"), "theory")

        second = self.client.post(url).json()
        self.assertEqual((second["theory"]["summary"], second["version"]), ("Версия 2", 1))
        self.assertEqual(len(second["versions"]), 2)
        old = self.client.get(url, params={"version": 0}).json()
        self.assertEqual(old["theory"]["summary"], "Версия 1")
        self.assertEqual(self.client.get(url, params={"version": 5}).status_code, 404)

        usage = self.client.get("/api/usage").json()["by_purpose"]["anthropic:theory"]
        self.assertEqual(usage["calls"], 2)
        self.assertNotEqual(
            self.client.get(url).json()["cost_estimate_usd"], config.THEORY_COST_ESTIMATE_USD
        )

    def test_only_roadmap_lessons_have_theory(self) -> None:
        for lesson in ("other", "filler_words_fluency", "nope"):
            self.assertEqual(self.client.get(f"/api/lessons/{lesson}/theory").status_code, 404)
            self.assertEqual(self.client.post(f"/api/lessons/{lesson}/theory").status_code, 404)
        self.assertEqual(self.writer.calls, [])

    def test_missing_key_and_upstream_failure(self) -> None:
        from app.analyzer import MissingAnthropicApiKeyError

        for exc, code in ((MissingAnthropicApiKeyError("нет ключа"), 400),
                          (AnalysisError("сбой"), 502)):
            with patch.object(self.writer, "write", side_effect=exc):
                response = self.client.post("/api/lessons/past_simple/theory")
            self.assertEqual(response.status_code, code)
        self.assertIsNone(self.client.get("/api/lessons/past_simple/theory").json()["theory"])


if __name__ == "__main__":
    unittest.main()
