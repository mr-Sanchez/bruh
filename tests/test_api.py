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
        keys = {t["key"] for t in response.json()["topics"]}
        self.assertEqual(keys, set(TOPIC_TAXONOMY.keys()))

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


class SessionDirectoryGuardTests(unittest.TestCase):
    """The path-traversal guard on session ids, tested directly."""

    def test_rejects_path_traversal_attempts(self) -> None:
        from fastapi import HTTPException

        for bad_id in ("..", ".", "a/b", "a\\b", ""):
            with self.assertRaises(HTTPException):
                api._session_directory(bad_id)


if __name__ == "__main__":
    unittest.main()
