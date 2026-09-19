"""Offline tests for the FastAPI /api/* routes (no network, no real keys).

Fakes are injected through FastAPI's dependency_overrides, mirroring the
client_factory pattern already used inside transcriber.py/analyzer.py.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import api, config  # noqa: E402
from app.analyzer import (  # noqa: E402
    ANALYSIS_SCHEMA_VERSION,
    AnalysisResult,
    Issue,
    Score,
    Scores,
)
from app.server import app as fastapi_app  # noqa: E402
from app.transcriber import TranscriptionResult  # noqa: E402


class FakeTranscriber:
    has_api_key = True

    def __init__(self, transcript: str = "Yesterday I go to the store.") -> None:
        self.transcript = transcript

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        return TranscriptionResult(
            transcript=self.transcript,
            raw_response={"ok": True},
            request_id="fake-request-id",
            confidence=0.95,
            audio_duration=1.0,
        )


class FakeAnalyzer:
    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, transcript: str, profile, duration_seconds: float) -> AnalysisResult:
        self.calls += 1
        issue = Issue(
            topic="verb_tense",
            quote="I go",
            explanation="Нужно прошедшее время.",
            correction="I went",
            better_versions=["I went there."],
            severity="moderate",
        )
        score = Score(score=6, comment="Норм.")
        return AnalysisResult(
            summary="Есть одна ошибка времени глагола.",
            issues=[issue],
            topic_counts={"verb_tense": 1},
            improved_version="Yesterday I went to the store.",
            scores=Scores(grammar=score, vocabulary=score, fluency=score, naturalness=score),
            overall_score=6.0,
            model=config.ANALYSIS_MODEL,
            effort="medium",
            request_id="fake-analysis-id",
            usage={
                "input_tokens": 3_000,
                "output_tokens": 5_000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        )


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        base_dir_patch.start()
        self.addCleanup(base_dir_patch.stop)

        self.fake_analyzer = FakeAnalyzer()
        fastapi_app.dependency_overrides[api.get_transcriber_factory] = lambda: (
            lambda profile: FakeTranscriber()
        )
        fastapi_app.dependency_overrides[api.get_analyzer_factory] = lambda: (
            lambda: self.fake_analyzer
        )
        self.addCleanup(fastapi_app.dependency_overrides.clear)

        self.client = TestClient(fastapi_app)

    def _upload_session(self) -> Dict:
        response = self.client.post(
            "/api/sessions",
            files={"file": ("audio.webm", b"fake-audio-bytes", "audio/webm")},
            data={"language": "en-US", "client_duration_seconds": "5.0", "mime_type": "audio/webm"},
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def test_config_endpoint_lists_language_profiles(self) -> None:
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        keys = {p["key"] for p in body["language_profiles"]}
        self.assertEqual(keys, {"en-US", "ru", "multi"})

    def test_topics_endpoint_lists_the_full_taxonomy(self) -> None:
        from app.progress_store import TOPIC_TAXONOMY

        response = self.client.get("/api/topics")
        self.assertEqual(response.status_code, 200)
        topics = {t["key"]: t for t in response.json()["topics"]}
        self.assertEqual(set(topics), set(TOPIC_TAXONOMY.keys()))
        self.assertTrue(topics["articles"]["has_cloze"])
        self.assertFalse(topics["verb_tense"]["has_cloze"])
        self.assertTrue(topics["articles"]["resources"][0]["url"].startswith("https://"))
        self.assertEqual(topics["other"]["resources"], [])

    def test_upload_transcribes_synchronously_within_the_test_client_call(self) -> None:
        upload = self._upload_session()
        self.assertEqual(upload["status"], "transcribing")

        detail = self.client.get(f"/api/sessions/{upload['session_id']}").json()
        self.assertEqual(detail["status"], "done")
        self.assertEqual(detail["transcript"], "Yesterday I go to the store.")
        self.assertIsNone(detail["analysis"])
        self.assertTrue(detail["audio_url"].endswith("/audio"))

    def test_upload_rejects_an_empty_recording(self) -> None:
        response = self.client.post(
            "/api/sessions",
            files={"file": ("audio.webm", b"", "audio/webm")},
            data={"language": "en-US", "client_duration_seconds": "5.0"},
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_session_is_404(self) -> None:
        response = self.client.get("/api/sessions/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_analyze_writes_analysis_and_updates_progress(self) -> None:
        upload = self._upload_session()
        session_id = upload["session_id"]

        response = self.client.post(f"/api/sessions/{session_id}/analyze")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["progress_updated"])
        self.assertEqual(body["analysis"]["summary"], "Есть одна ошибка времени глагола.")
        self.assertEqual(body["analysis"]["schema_version"], ANALYSIS_SCHEMA_VERSION)
        self.assertEqual(body["analysis"]["improved_version"], "Yesterday I went to the store.")
        self.assertEqual(body["analysis"]["issues"][0]["better_versions"], ["I went there."])
        self.assertEqual(body["analysis"]["overall_score"], 6.0)
        self.assertEqual(body["analysis"]["scores"]["grammar"]["score"], 6)
        self.assertEqual(self.fake_analyzer.calls, 1)

        progress = self.client.get("/api/progress").json()
        topic_keys = {t["key"] for t in progress["topics"]}
        self.assertIn("verb_tense", topic_keys)

    def test_analyze_is_idempotent_without_force(self) -> None:
        upload = self._upload_session()
        session_id = upload["session_id"]

        first = self.client.post(f"/api/sessions/{session_id}/analyze").json()
        second = self.client.post(f"/api/sessions/{session_id}/analyze").json()

        self.assertEqual(self.fake_analyzer.calls, 1)
        self.assertFalse(second["progress_updated"])
        self.assertEqual(first["analysis"], second["analysis"])

    def test_analyze_force_calls_the_analyzer_again(self) -> None:
        upload = self._upload_session()
        session_id = upload["session_id"]

        self.client.post(f"/api/sessions/{session_id}/analyze")
        response = self.client.post(
            f"/api/sessions/{session_id}/analyze", json={"force": True}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fake_analyzer.calls, 2)

    def test_analyze_without_transcript_is_rejected(self) -> None:
        response = self.client.post(
            "/api/sessions",
            files={"file": ("audio.webm", b"fake-audio-bytes", "audio/webm")},
            data={"language": "en-US", "client_duration_seconds": "0.0"},
        )
        session_id = response.json()["session_id"]
        analyze_response = self.client.post(f"/api/sessions/{session_id}/analyze")
        self.assertEqual(analyze_response.status_code, 400)


    def _analyzed_session(self) -> str:
        session_id = self._upload_session()["session_id"]
        response = self.client.post(f"/api/sessions/{session_id}/analyze")
        self.assertEqual(response.status_code, 200, response.text)
        return session_id

    def test_analyze_logs_usage_and_stores_its_cost(self) -> None:
        session_id = self._analyzed_session()
        analysis = self.client.get(f"/api/sessions/{session_id}").json()["analysis"]
        self.assertAlmostEqual(analysis["usage"]["cost_usd"], 0.056)

        usage = self.client.get("/api/usage").json()
        self.assertEqual(usage["by_purpose"]["anthropic:analysis"]["calls"], 1)
        # The fake transcriber reports 1 s of audio.
        self.assertEqual(usage["by_purpose"]["deepgram:transcription"]["calls"], 1)

    def test_progress_includes_score_history(self) -> None:
        session_id = self._analyzed_session()
        history = self.client.get("/api/progress").json()["score_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["session_id"], session_id)
        self.assertEqual(history[0]["overall"], 6.0)

    def test_forced_reanalysis_replaces_counts_and_scores(self) -> None:
        session_id = self._analyzed_session()
        self.client.post(f"/api/sessions/{session_id}/analyze", json={"force": True})
        progress = self.client.get("/api/progress").json()
        verb_tense = next(t for t in progress["topics"] if t["key"] == "verb_tense")
        self.assertEqual(verb_tense["count"], 1)
        self.assertEqual(len(progress["score_history"]), 1)

    def test_analysis_fills_the_item_bank(self) -> None:
        session_id = self._analyzed_session()
        body = self.client.get("/api/learner/items").json()
        self.assertEqual(body["count"], 1)
        item = body["items"][0]
        self.assertEqual(item["kind"], "fix")
        self.assertEqual(item["content"]["correction"], "I went")
        self.assertEqual(item["occurrences"][0]["session_id"], session_id)
        self.assertTrue(item["state"]["is_new"])
        self.assertTrue(item["state"]["is_due"])

        due = self.client.get("/api/learner/items", params={"due_only": True}).json()
        self.assertEqual(due["count"], 1)

    def test_attempt_is_logged_and_moves_the_item(self) -> None:
        self._analyzed_session()
        item_id = self.client.get("/api/learner/items").json()["items"][0]["id"]

        response = self.client.post(
            "/api/learner/attempts",
            json={"item_id": item_id, "exercise": "review_card", "correct": True, "answer": "went"},
        )

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["attempt"]["answer"], "went")
        self.assertEqual(body["state"]["box"], 2)
        self.assertFalse(body["state"]["is_due"])
        due = self.client.get("/api/learner/items", params={"due_only": True}).json()
        self.assertEqual(due["count"], 0)

    def test_attempt_on_unknown_item_or_bad_exercise_is_rejected(self) -> None:
        self._analyzed_session()
        item_id = self.client.get("/api/learner/items").json()["items"][0]["id"]
        unknown = self.client.post(
            "/api/learner/attempts",
            json={"item_id": "fix-000000000000", "exercise": "review_card", "correct": True},
        )
        self.assertEqual(unknown.status_code, 404)
        bad = self.client.post(
            "/api/learner/attempts",
            json={"item_id": item_id, "exercise": "../etc", "correct": True},
        )
        self.assertEqual(bad.status_code, 422)

    def test_card_attempt_needs_correct_and_exactly_one_target(self) -> None:
        self._analyzed_session()
        item_id = self.client.get("/api/learner/items").json()["items"][0]["id"]
        no_correct = self.client.post(
            "/api/learner/attempts", json={"item_id": item_id, "exercise": "card_type"}
        )
        self.assertEqual(no_correct.status_code, 400)
        both = self.client.post(
            "/api/learner/attempts",
            json={"item_id": item_id, "topic": "articles", "exercise": "x", "correct": True},
        )
        self.assertEqual(both.status_code, 400)

    def test_items_carry_their_exercise_format(self) -> None:
        self._analyzed_session()
        item = self.client.get("/api/learner/items").json()["items"][0]
        self.assertEqual(item["exercise"], {"type": "type", "accept": ["I went", "I went there."]})
        queue = self.client.get("/api/learner/queue").json()
        self.assertEqual(queue["new"][0]["exercise"]["type"], "type")

    def test_cloze_texts_and_topic_drill_attempt(self) -> None:
        session_id = self._analyzed_session()
        texts = self.client.get("/api/learner/texts", params={"topic": "articles"}).json()["texts"]
        self.assertEqual(texts[0]["session_id"], session_id)
        self.assertEqual([s["gap"] for s in texts[0]["segments"] if "gap" in s], ["the"])
        self.assertEqual(
            self.client.get("/api/learner/texts", params={"topic": "verb_tense"}).status_code, 404
        )

        passed = self.client.post(
            "/api/learner/attempts",
            json={"topic": "articles", "exercise": "cloze", "score": 0.9, "session_id": session_id},
        )
        self.assertEqual(passed.status_code, 201, passed.text)
        self.assertTrue(passed.json()["attempt"]["correct"])
        failed = self.client.post(
            "/api/learner/attempts",
            json={"topic": "articles", "exercise": "cloze", "score": 0.5, "correct": True},
        )
        self.assertFalse(failed.json()["attempt"]["correct"])  # derived from the score

        texts = self.client.get("/api/learner/texts", params={"topic": "articles"}).json()["texts"]
        self.assertEqual((texts[0]["attempts"], texts[0]["best_score"]), (1, 0.9))
        articles = next(
            t for t in self.client.get("/api/learner/topics").json()["topics"]
            if t["key"] == "articles"
        )
        self.assertAlmostEqual(articles["accuracy"], 0.7)

    def test_topic_drill_attempt_is_validated(self) -> None:
        cases = [
            ({"topic": "no_such_topic", "exercise": "cloze", "score": 1.0}, 404),
            ({"topic": "articles", "exercise": "cloze"}, 400),
            ({"topic": "articles", "exercise": "cloze", "score": 1.5}, 422),
            ({"topic": "articles", "exercise": "cloze", "score": 1, "session_id": "../x"}, 422),
        ]
        for payload, status in cases:
            with self.subTest(payload=payload):
                response = self.client.post("/api/learner/attempts", json=payload)
                self.assertEqual(response.status_code, status, response.text)

    def test_learner_queue_offers_new_cards(self) -> None:
        self._analyzed_session()
        queue = self.client.get("/api/learner/queue").json()
        self.assertEqual(queue["new_limit"], 10)
        self.assertEqual([item["content"]["correction"] for item in queue["new"]], ["I went"])
        self.assertEqual(queue["reviews"], [])

    def test_learner_topics_combine_speech_and_drills(self) -> None:
        self._analyzed_session()
        topics = self.client.get("/api/learner/topics").json()["topics"]
        verb_tense = next(t for t in topics if t["key"] == "verb_tense")
        self.assertEqual(verb_tense["items"], 1)
        self.assertEqual(verb_tense["due_items"], 1)
        self.assertIsNone(verb_tense["accuracy"])
        self.assertGreater(verb_tense["priority"], 0)


class SessionDirectoryGuardTests(unittest.TestCase):
    """The path-traversal guard on session ids, tested directly."""

    def test_rejects_path_traversal_attempts(self) -> None:
        from fastapi import HTTPException

        for bad_id in ("..", ".", "a/b", "a\\b", ""):
            with self.assertRaises(HTTPException):
                api._session_directory(bad_id)


if __name__ == "__main__":
    unittest.main()
