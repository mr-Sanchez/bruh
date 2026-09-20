"""Offline tests for the FastAPI /api/* routes (no network, no real keys).

Fakes are injected through FastAPI's dependency_overrides, mirroring the
client_factory pattern already used inside transcriber.py/analyzer.py.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import api, config  # noqa: E402
from app.analyzer import (  # noqa: E402
    ANALYSIS_SCHEMA_VERSION,
    AnalysisResult,
    Issue,
    KeyPhrase,
    SceneDetail,
    Score,
    Scores,
)
from app.analyzer import AnalysisError, MissingAnthropicApiKeyError  # noqa: E402
from app.exercise_sets import (  # noqa: E402
    ClaudeCall,
    GenerationResult,
    GradingResult,
    TranslationAnswer,
)
from app.server import app as fastapi_app  # noqa: E402
from app.transcriber import TranscriptionResult  # noqa: E402
from tests.test_speech_drills import deepgram_payload, timed_words  # noqa: E402


class FakeTranscriber:
    has_api_key = True

    def __init__(
        self, transcript: str = "Yesterday I go to the store.", raw_response: Optional[Dict] = None
    ) -> None:
        self.transcript = transcript
        self.raw_response = raw_response or {"ok": True}

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        return TranscriptionResult(
            transcript=self.transcript,
            raw_response=self.raw_response,
            request_id="fake-request-id",
            confidence=0.95,
            audio_duration=1.0,
        )


# Smallest valid-looking JPEG header: the API sniffs the type from the bytes.
FAKE_JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes"


class FakeAnalyzer:
    def __init__(self) -> None:
        self.calls = 0
        self.last_kwargs: Dict = {}

    def analyze(
        self, transcript: str, profile, duration_seconds: float = 0.0, **kwargs
    ) -> AnalysisResult:
        self.calls += 1
        self.last_kwargs = kwargs
        picture = kwargs.get("image") is not None
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
            not_mentioned=(
                [SceneDetail(detail="Кот на подоконнике", phrase="A cat is on the windowsill.")]
                if picture
                else []
            ),
            scene_vocabulary=(
                [KeyPhrase(phrase="windowsill", meaning="подоконник", example="A cat sits there.")]
                if picture
                else []
            ),
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


SET_EXERCISES = [
    {"id": "ex1", "type": "gap", "before": "Yesterday we ", "after": " it.",
     "accept": ["shipped"], "hint": "(ship)", "explanation": "Past Simple."},
    {"id": "ex2", "type": "fix", "sentence": "I go there yesterday.",
     "accept": ["I went there yesterday."], "explanation": "Past Simple."},
    {"id": "ex3", "type": "translate", "russian": "Я уже починил баг.",
     "reference": "I have already fixed the bug.", "focus": "Present Perfect"},
    {"id": "ex4", "type": "translate", "russian": "Мы созвонились вчера.",
     "reference": "We had a call yesterday.", "focus": "Past Simple"},
]


class FakeGenerator:
    """Generates SET_EXERCISES; grades a translation right when it has "have"."""

    has_api_key = True

    def __init__(self) -> None:
        self.generated: List[Dict] = []
        self.graded: List[List[TranslationAnswer]] = []
        self.fail_grading: Optional[Exception] = None

    def generate(self, topic, seeds, avoid=()) -> GenerationResult:
        self.generated.append({"topic": topic, "seeds": list(seeds), "avoid": list(avoid)})
        return GenerationResult(
            intro="Прошедшее время.",
            exercises=[dict(e) for e in SET_EXERCISES],
            call=ClaudeCall(
                model=config.EXERCISE_SET_MODEL,
                usage={"input_tokens": 2_000, "output_tokens": 2_000},
                request_id="gen-1",
                effort="low",
            ),
        )

    def grade(self, topic, answers) -> GradingResult:
        if self.fail_grading is not None:
            raise self.fail_grading
        self.graded.append(list(answers))
        verdicts = {
            a.exercise_id: {
                "correct": "have" in a.answer,
                "comment": "Коммент.",
                "corrected": a.reference,
            }
            for a in answers
        }
        usage = {"input_tokens": 1_000, "output_tokens": 500}
        return GradingResult(verdicts=verdicts, call=ClaudeCall(config.GRADING_MODEL, usage))


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
        self.fake_generator = FakeGenerator()
        fastapi_app.dependency_overrides[api.get_generator_factory] = lambda: (
            lambda: self.fake_generator
        )
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        # A real key in the developer's environment must not change what the
        # routes offer (the «Сегодня» AI set step only shows with a key).
        key_patch = patch.object(config, "get_anthropic_api_key", return_value=None)
        key_patch.start()
        self.addCleanup(key_patch.stop)
        # Same for Deepgram: the spoken warm-up step only shows with its key.
        self.deepgram_key = patch.object(config, "get_api_key", return_value=None)
        self.deepgram_key.start()
        self.addCleanup(self.deepgram_key.stop)

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

    def _upload_picture(self, **data: str) -> Dict:
        files = {"image": ("image.jpg", FAKE_JPEG, "image/jpeg")}
        if "text" not in data:
            files["file"] = ("audio.webm", b"fake-audio-bytes", "audio/webm")
            data.setdefault("client_duration_seconds", "30.0")
        response = self.client.post(
            "/api/sessions", files=files, data={"kind": "picture", "language": "en-US", **data}
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def test_spoken_picture_description_keeps_audio_and_image(self) -> None:
        upload = self._upload_picture()
        detail = self.client.get(f"/api/sessions/{upload['session_id']}").json()
        self.assertEqual(detail["kind"], "picture")
        self.assertEqual(detail["input_mode"], "voice")
        self.assertEqual(detail["transcript"], "Yesterday I go to the store.")
        self.assertTrue(detail["audio_url"].endswith("/audio"))
        self.assertTrue(detail["image_url"].endswith("/image"))

        image = self.client.get(detail["image_url"])
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.content, FAKE_JPEG)
        self.assertEqual(image.headers["content-type"], "image/jpeg")
        session_dir = config.recordings_dir() / upload["session_id"]
        self.assertTrue((session_dir / "image.jpg").is_file())

    def test_typed_picture_description_skips_deepgram_and_keeps_the_text(self) -> None:
        text = "  There is a cat, um, on the windowsil.\n"
        upload = self._upload_picture(text=text)
        self.assertEqual(upload["status"], "done")
        detail = self.client.get(f"/api/sessions/{upload['session_id']}").json()
        self.assertEqual(detail["input_mode"], "text")
        self.assertIsNone(detail["audio_url"])
        session_dir = config.recordings_dir() / upload["session_id"]
        self.assertFalse((session_dir / config.RESPONSE_FILENAME).exists())
        # Stored verbatim, typos and all.
        transcript = (session_dir / config.TRANSCRIPT_FILENAME).read_text(encoding="utf-8")
        self.assertTrue(transcript.endswith("---\n\n" + text + "\n"))

    def test_picture_analysis_sends_the_image_and_feeds_the_bank(self) -> None:
        upload = self._upload_picture(text="There is a cat.")
        response = self.client.post(f"/api/sessions/{upload['session_id']}/analyze")
        self.assertEqual(response.status_code, 200, response.text)
        analysis = response.json()["analysis"]
        self.assertEqual(analysis["kind"], "picture")
        self.assertEqual(analysis["input_mode"], "text")
        self.assertEqual(analysis["not_mentioned"][0]["detail"], "Кот на подоконнике")
        self.assertEqual(analysis["scene_vocabulary"][0]["phrase"], "windowsill")

        image = self.fake_analyzer.last_kwargs["image"]
        self.assertEqual(image.data, FAKE_JPEG)
        self.assertEqual(image.media_type, "image/jpeg")
        self.assertTrue(self.fake_analyzer.last_kwargs["typed"])

        phrases = self.client.get("/api/learner/items?kind=phrase").json()["items"]
        self.assertIn("windowsill", [item["content"]["phrase"] for item in phrases])
        purposes = self.client.get("/api/usage").json()["by_purpose"]
        self.assertIn("anthropic:picture_analysis", purposes)
        self.assertNotIn("anthropic:analysis", purposes)

    def test_typed_fluency_stays_out_of_the_score_history(self) -> None:
        upload = self._upload_picture(text="There is a cat.")
        self.client.post(f"/api/sessions/{upload['session_id']}/analyze")
        history = self.client.get("/api/progress").json()["score_history"]
        self.assertIsNone(history[0]["fluency"])
        self.assertEqual(history[0]["grammar"], 6)

    def test_monologue_analysis_sends_no_image(self) -> None:
        upload = self._upload_session()
        analysis = self.client.post(f"/api/sessions/{upload['session_id']}/analyze").json()
        self.assertIsNone(self.fake_analyzer.last_kwargs["image"])
        self.assertFalse(self.fake_analyzer.last_kwargs["typed"])
        self.assertEqual(analysis["analysis"]["kind"], "monologue")
        self.assertNotIn("not_mentioned", analysis["analysis"])

    def test_picture_upload_is_validated(self) -> None:
        def post(files: Dict, data: Dict) -> int:
            return self.client.post("/api/sessions", files=files, data=data).status_code

        audio = ("audio.webm", b"fake-audio-bytes", "audio/webm")
        picture = {"kind": "picture", "client_duration_seconds": "5.0"}
        # No image, or bytes that are not an image (the claimed type is ignored).
        self.assertEqual(post({"file": audio}, picture), 400)
        self.assertEqual(
            post({"file": audio, "image": ("x.jpg", b"<svg/>", "image/jpeg")}, picture), 400
        )
        # An image on a monologue, an unknown kind, both or neither of audio/text.
        image = ("image.jpg", FAKE_JPEG, "image/jpeg")
        monologue = {"client_duration_seconds": "5.0"}
        self.assertEqual(post({"file": audio, "image": image}, monologue), 400)
        self.assertEqual(post({"file": audio}, {"kind": "essay"}), 400)
        self.assertEqual(post({"file": audio, "image": image}, {**picture, "text": "Hi"}), 400)
        self.assertEqual(post({"image": image}, {"kind": "picture"}), 400)
        self.assertEqual(post({"image": image}, {"kind": "picture", "text": "   "}), 400)
        self.assertEqual(list(config.recordings_dir().glob("*")), [])

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

    def test_today_is_empty_before_any_recording(self) -> None:
        body = self.client.get("/api/learner/today").json()
        self.assertEqual([s["kind"] for s in body["steps"]], ["cards", "monologue"])
        self.assertEqual(body["steps"][0]["status"], "empty")
        self.assertEqual(body["steps"][1]["status"], "todo")
        self.assertTrue(body["steps"][1]["prompt"]["question"])
        self.assertEqual(body["streak_days"], 0)
        self.assertIsNone(body["focus_topic"])

    def test_today_workout_tracks_each_step(self) -> None:
        session_id = self._analyzed_session()
        body = self.client.get("/api/learner/today").json()
        cards, drill, live = body["steps"]
        self.assertEqual((cards["status"], cards["queue_total"]), ("todo", 1))
        self.assertEqual((drill["kind"], drill["status"]), ("cloze", "todo"))
        self.assertEqual(drill["topic"]["key"], "articles")
        self.assertEqual(drill["text"]["session_id"], session_id)
        self.assertEqual((live["status"], live["session_id"]), ("done", session_id))
        self.assertEqual(body["focus_topic"]["key"], "verb_tense")
        self.assertEqual(body["streak_days"], 1)

        self.client.post(
            "/api/learner/attempts",
            json={"item_id": cards["items"][0]["id"], "exercise": "card_type", "correct": True},
        )
        self.client.post(
            "/api/learner/attempts",
            json={"topic": "articles", "exercise": "cloze", "score": 1.0, "session_id": session_id},
        )
        body = self.client.get("/api/learner/today").json()
        self.assertEqual([s["status"] for s in body["steps"]], ["done", "done", "done"])
        self.assertEqual(body["minutes_left"], 0)

        history = self.client.get("/api/learner/history").json()["days"]
        self.assertEqual((history[0]["cards"], history[0]["cards_correct"]), (1, 1))
        self.assertEqual(history[0]["drills"][0]["label"], "Артикли (a / an / the)")

    def test_a_picture_description_fills_the_live_step(self) -> None:
        upload = self._upload_picture(text="There is a cat.")
        live = self.client.get("/api/learner/today").json()["steps"][-1]
        self.assertEqual((live["status"], live["activity"]), ("analyze", "picture"))
        self.client.post(f"/api/sessions/{upload['session_id']}/analyze")
        live = self.client.get("/api/learner/today").json()["steps"][-1]
        self.assertEqual((live["status"], live["session_id"]), ("done", upload["session_id"]))

    def test_static_files_are_revalidated_after_an_update(self) -> None:
        response = self.client.get("/js/app.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-cache")

    def test_config_lists_speaking_prompts(self) -> None:
        prompts = self.client.get("/api/config").json()["speaking_prompts"]
        self.assertEqual(len(prompts), len(config.SPEAKING_PROMPTS))
        self.assertEqual(prompts[3]["index"], 3)


    # ------------------------------------------------------- spoken drills
    def _hear(self, text: str, **timing) -> None:
        """Make the fake Deepgram hear `text`, with word timings."""
        words = timed_words(text, **timing)
        response = deepgram_payload(words)
        fastapi_app.dependency_overrides[api.get_transcriber_factory] = lambda: (
            lambda profile: FakeTranscriber(text, response)
        )

    def _upload_drill(self, kind: str, expect: int = 202, **data: str) -> Dict:
        response = self.client.post(
            "/api/sessions",
            files={"file": ("audio.webm", b"fake-audio-bytes", "audio/webm")},
            data={"kind": kind, "language": "en-US", "client_duration_seconds": "60", **data},
        )
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def _drill_attempts(self) -> List[Dict]:
        from app import learner_store

        return [a for a in learner_store.load_attempts() if a["exercise"] in ("talk", "shadowing")]

    def test_a_talk_series_logs_only_its_first_round(self) -> None:
        self._hear("So uh I think we should ship it today", gaps={4: 3.0})
        first = self._upload_drill("talk", prompt_index="2")
        detail = self.client.get(f"/api/sessions/{first['session_id']}").json()
        self.assertEqual(detail["kind"], "talk")
        self.assertEqual(
            detail["drill"], {"prompt_index": 2, "series": first["session_id"], "round": 1}
        )
        metrics = detail["speech"]["metrics"]
        self.assertEqual((metrics["fillers"], metrics["long_pauses"]), (1, 1))

        attempts = self._drill_attempts()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["topic"], config.FLUENCY_TOPIC)
        self.assertEqual(attempts[0]["score"], metrics["fluency_score"])
        self.assertEqual(attempts[0]["session_id"], first["session_id"])

        # Rounds 2 and 3 take the prompt of the series, whatever is sent.
        for expected_round in (2, 3):
            take = self._upload_drill("talk", prompt_index="7", series=first["session_id"])
            drill = self.client.get(f"/api/sessions/{take['session_id']}").json()["drill"]
            self.assertEqual((drill["round"], drill["prompt_index"]), (expected_round, 2))
        self.assertEqual(len(self._drill_attempts()), 1)

        series = self.client.get("/api/speech/talks").json()["series"]
        self.assertEqual(len(series), 1)
        self.assertEqual([r["round"] for r in series[0]["rounds"]], [1, 2, 3])
        self.assertEqual(series[0]["rounds"][0]["metrics"]["fillers"], 1)

        usage = self.client.get("/api/usage").json()["by_purpose"]
        self.assertEqual(usage["deepgram:speech_drill"]["calls"], 3)

    def test_talk_takes_are_validated(self) -> None:
        self._upload_drill("talk", expect=400)
        self._upload_drill("talk", expect=400, prompt_index=str(len(config.SPEAKING_PROMPTS)))
        monologue = self._upload_session()
        self._upload_drill(
            "talk", expect=400, prompt_index="0", series=monologue["session_id"]
        )
        self._upload_drill("talk", expect=404, prompt_index="0", series="2020-01-01_00-00-00")
        self._upload_drill("talk", expect=400, prompt_index="0", series="..")
        typed = self.client.post(
            "/api/sessions", data={"kind": "talk", "text": "hello", "prompt_index": "0"}
        )
        self.assertEqual(typed.status_code, 400)

    def test_a_talk_without_filler_detection_is_not_scored(self) -> None:
        self._hear("Я думаю что да")
        take = self._upload_drill("talk", prompt_index="0", language="ru")
        detail = self.client.get(f"/api/sessions/{take['session_id']}").json()
        self.assertIsNone(detail["speech"]["metrics"]["fillers"])
        self.assertEqual(self._drill_attempts(), [])

    def test_drills_are_not_analysed_and_do_not_fill_the_live_step(self) -> None:
        self._hear("Okay let me try this")
        take = self._upload_drill("talk", prompt_index="0")
        response = self.client.post(f"/api/sessions/{take['session_id']}/analyze")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.fake_analyzer.calls, 0)
        steps = {s["kind"]: s for s in self.client.get("/api/learner/today").json()["steps"]}
        self.assertEqual(steps["monologue"]["status"], "todo")
        self.assertEqual(steps["speech"]["status"], "done")

    def test_shadowing_reads_a_passage_of_the_improved_version(self) -> None:
        source = self._analyzed_session()
        passages = self.client.get("/api/speech/passages").json()
        self.assertEqual(len(passages["passages"]), 1)
        self.assertEqual(passages["next"]["text"], "Yesterday I went to the store.")
        self.assertEqual(passages["next"]["attempts"], 0)

        self._hear("Yesterday I go to the store.")
        take = self._upload_drill(
            "shadowing", source_session_id=source, passage="0", language="ru"
        )
        detail = self.client.get(f"/api/sessions/{take['session_id']}").json()
        self.assertEqual(detail["language"], "en-US")
        self.assertEqual(detail["drill"]["reference"], "Yesterday I went to the store.")
        reading = detail["speech"]["reading"]
        self.assertEqual((reading["wrong"], reading["score"]), (1, round(5 / 6, 3)))

        attempts = self._drill_attempts()
        self.assertEqual([(a["exercise"], a["score"]) for a in attempts], [("shadowing", 0.833)])
        passages = self.client.get("/api/speech/passages").json()["passages"]
        self.assertEqual((passages[0]["attempts"], passages[0]["best_score"]), (1, 0.833))

    def test_shadowing_takes_are_validated(self) -> None:
        source = self._analyzed_session()
        self._upload_drill("shadowing", expect=400, source_session_id=source)
        self._upload_drill("shadowing", expect=404, source_session_id=source, passage="5")
        self._upload_drill("shadowing", expect=400, source_session_id="..", passage="0")

    def test_today_offers_a_spoken_warm_up_with_a_deepgram_key(self) -> None:
        self.assertNotIn(
            "speech", [s["kind"] for s in self.client.get("/api/learner/today").json()["steps"]]
        )
        with patch.object(config, "get_api_key", return_value="dg-key"):
            source = self._analyzed_session()
            body = self.client.get("/api/learner/today").json()
            speech = body["steps"][-1]
            self.assertEqual((speech["kind"], speech["status"]), ("speech", "todo"))
            self.assertTrue(speech["optional"])
            self.assertEqual(speech["passage"]["session_id"], source)
            self.assertTrue(speech["prompt"]["question"])
            minutes_before = body["minutes_left"]

            self._hear("Yesterday I went to the store.")
            self._upload_drill("shadowing", source_session_id=source, passage="0")
            body = self.client.get("/api/learner/today").json()
            speech = body["steps"][-1]
            self.assertEqual((speech["status"], speech["activity"]), ("done", "shadowing"))
            self.assertEqual(body["minutes_left"], minutes_before)

    # ------------------------------------------------------ AI exercise sets
    def _create_set(self, topic: str = "verb_tense", force: bool = False) -> Dict:
        response = self.client.post("/api/practice/sets", json={"topic": topic, "force": force})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _submit(self, set_id: str, answers: Dict[str, Dict], status: int = 200) -> Dict:
        payload = [{"exercise_id": key, **value} for key, value in answers.items()]
        response = self.client.post(
            f"/api/practice/sets/{set_id}/submit", json={"answers": payload, "context": "topic"}
        )
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    GOOD_ANSWERS = {
        "ex1": {"answer": "shipped"},
        "ex2": {"answer": "I went there yesterday!", "correct": True},
        "ex3": {"answer": "I have already fixed that bug."},
        "ex4": {"answer": "We had a call yesterday."},  # the reference: no Claude call
    }

    def test_sets_are_listed_per_allowed_topic_with_a_price(self) -> None:
        body = self.client.get("/api/practice/sets", params={"topic": "articles"}).json()
        self.assertEqual(body["sets"], [])
        self.assertEqual(body["cost_estimate_usd"], config.SET_COST_ESTIMATE_USD)
        self.assertFalse(body["anthropic_configured"])
        self.assertEqual(
            self.client.get("/api/practice/sets", params={"topic": "other"}).status_code, 400
        )
        self.assertEqual(
            self.client.get("/api/practice/sets", params={"topic": "nope"}).status_code, 404
        )
        self.assertEqual(self.client.post(
            "/api/practice/sets", json={"topic": "filler_words_fluency"}).status_code, 400)

    def test_creating_a_set_is_paid_once_until_it_is_started(self) -> None:
        self._analyzed_session()
        first = self._create_set()
        self.assertFalse(first["reused"])
        exercise_set = first["set"]
        self.assertEqual(exercise_set["topic"], "verb_tense")
        self.assertEqual(len(exercise_set["exercises"]), 4)
        self.assertGreater(exercise_set["generation"]["usage"]["cost_usd"], 0)
        seeds = self.fake_generator.generated[0]["seeds"]
        self.assertEqual([s["content"]["correction"] for s in seeds], ["I went"])

        again = self._create_set()
        self.assertTrue(again["reused"])
        self.assertEqual(again["set"]["id"], exercise_set["id"])
        self.assertEqual(len(self.fake_generator.generated), 1)

        forced = self._create_set(force=True)
        self.assertNotEqual(forced["set"]["id"], exercise_set["id"])
        self.assertEqual(len(self.fake_generator.generated), 2)
        self.assertIn("Yesterday we ___ it.", self.fake_generator.generated[1]["avoid"])

        usage = self.client.get("/api/usage").json()
        self.assertEqual(usage["by_purpose"]["anthropic:exercise_set"]["calls"], 2)
        listed = self.client.get("/api/practice/sets", params={"topic": "verb_tense"}).json()
        self.assertEqual(len(listed["sets"]), 2)
        average = usage["by_purpose"]["anthropic:exercise_set"]["avg_cost_usd"]
        self.assertEqual(listed["cost_estimate_usd"], average)

    def test_generation_errors_map_to_http_codes(self) -> None:
        def failing(error: Exception):
            def generate(*args, **kwargs):
                raise error
            return generate

        self.fake_generator.generate = failing(MissingAnthropicApiKeyError("no key"))
        self.assertEqual(
            self.client.post("/api/practice/sets", json={"topic": "articles"}).status_code, 400
        )
        self.fake_generator.generate = failing(AnalysisError("upstream"))
        self.assertEqual(
            self.client.post("/api/practice/sets", json={"topic": "articles"}).status_code, 502
        )

    def test_set_ids_are_guarded(self) -> None:
        self.assertEqual(self.client.get("/api/practice/sets/not-a-set").status_code, 400)
        self.assertEqual(self.client.get("/api/practice/sets/set-..-x").status_code, 400)
        self.assertEqual(self.client.get("/api/practice/sets/set-20260101-000000").status_code, 404)

    def test_submit_needs_every_exercise_once(self) -> None:
        set_id = self._create_set()["set"]["id"]
        self._submit(set_id, {"ex1": {"answer": "shipped"}}, status=400)
        response = self.client.post(
            f"/api/practice/sets/{set_id}/submit",
            json={"answers": [{"exercise_id": "ex1"}] * 2 + [
                {"exercise_id": k} for k in ("ex2", "ex3", "ex4")]},
        )
        self.assertEqual(response.status_code, 400)

    def test_a_perfect_run_scores_the_topic_and_makes_no_cards(self) -> None:
        set_id = self._create_set()["set"]["id"]
        body = self._submit(set_id, self.GOOD_ANSWERS)

        run = body["run"]
        self.assertEqual((run["correct"], run["total"], run["score"]), (4, 4, 1.0))
        graded_by = {r["exercise_id"]: r["graded_by"] for r in run["results"]}
        self.assertEqual(
            graded_by, {"ex1": "match", "ex2": "browser", "ex3": "claude", "ex4": "match"}
        )
        self.assertEqual(len(self.fake_generator.graded[0]), 1)  # only ex3 needed Claude
        self.assertEqual(body["new_cards"], 0)
        self.assertEqual(body["summary"]["best_score"], 1.0)

        topics = self.client.get("/api/learner/topics").json()["topics"]
        self.assertEqual(next(t for t in topics if t["key"] == "verb_tense")["accuracy"], 1.0)
        history = self.client.get("/api/learner/history").json()["days"][0]["drills"][0]
        self.assertEqual((history["exercise"], history["set_id"]), ("ai_set", set_id))
        usage = self.client.get("/api/usage").json()["by_purpose"]
        self.assertEqual(usage["anthropic:exercise_grading"]["calls"], 1)

    def test_wrong_answers_become_new_cards_and_a_redo_reuses_verdicts(self) -> None:
        set_id = self._create_set()["set"]["id"]
        answers = {
            "ex1": {"answer": "ship"},
            "ex2": {"answer": "I go there yesterday."},
            "ex3": {"answer": "I already fixed the bug."},
            "ex4": {"answer": ""},
        }
        body = self._submit(set_id, answers)
        self.assertEqual(body["run"]["correct"], 0)
        self.assertEqual(body["new_cards"], 3)  # the blank translation makes none
        empty = next(r for r in body["run"]["results"] if r["exercise_id"] == "ex4")
        self.assertEqual(
            (empty["graded_by"], empty["corrected"]), ("empty", "We had a call yesterday.")
        )

        queue = self.client.get("/api/learner/queue").json()
        corrections = {item["content"]["correction"] for item in queue["new"]}
        self.assertIn("I have already fixed the bug.", corrections)
        self.assertIn("Yesterday we shipped it.", corrections)
        self.assertTrue(all(item["origin"] == "ai_set" for item in queue["new"]))

        again = self._submit(set_id, answers)
        self.assertEqual(len(self.fake_generator.graded), 1)  # verdict came from the cache
        ex3 = next(r for r in again["run"]["results"] if r["exercise_id"] == "ex3")
        self.assertEqual(ex3["graded_by"], "cache")
        self.assertEqual(again["new_cards"], 0)
        self.assertEqual(again["summary"]["runs"], 2)

    def test_grading_failure_can_be_replaced_by_self_grading(self) -> None:
        set_id = self._create_set()["set"]["id"]
        answers = dict(self.GOOD_ANSWERS)
        self.fake_generator.fail_grading = AnalysisError("Claude недоступен")
        failed = self.client.post(
            f"/api/practice/sets/{set_id}/submit",
            json={"answers": [{"exercise_id": k, **v} for k, v in answers.items()]},
        )
        self.assertEqual(failed.status_code, 502)
        stored = self.client.get(f"/api/practice/sets/{set_id}").json()
        self.assertEqual(stored["summary"]["runs"], 0)

        answers["ex3"] = {"answer": "I have already fixed that bug.", "correct": True}
        body = self._submit(set_id, answers)
        ex3 = next(r for r in body["run"]["results"] if r["exercise_id"] == "ex3")
        self.assertEqual((ex3["graded_by"], ex3["correct"]), ("self", True))

    def test_today_offers_an_optional_set_on_the_main_topic(self) -> None:
        self._analyzed_session()
        with patch.object(config, "get_anthropic_api_key", return_value="key"):
            body = self.client.get("/api/learner/today").json()
        step = body["steps"][-1]
        self.assertEqual((step["kind"], step["status"], step["optional"]), ("ai_set", "todo", True))
        self.assertEqual(step["topic"]["key"], "verb_tense")
        self.assertIsNone(step["set_id"])
        self.assertEqual(step["cost_usd"], config.SET_COST_ESTIMATE_USD)
        self.assertEqual(body["minutes_left"], 3)  # the cloze only: the set is optional

        # Without a key the step only shows for a set that is already paid for.
        last = self.client.get("/api/learner/today").json()["steps"][-1]
        self.assertEqual(last["kind"], "monologue")
        set_id = self._create_set()["set"]["id"]
        step = self.client.get("/api/learner/today").json()["steps"][-1]
        self.assertEqual((step["set_id"], step["cost_usd"]), (set_id, 0.0))

        self._submit(set_id, self.GOOD_ANSWERS)
        step = self.client.get("/api/learner/today").json()["steps"][-1]
        self.assertEqual((step["status"], step["score"]), ("done", 1.0))


class SessionDirectoryGuardTests(unittest.TestCase):
    """The path-traversal guard on session ids, tested directly."""

    def test_rejects_path_traversal_attempts(self) -> None:
        from fastapi import HTTPException

        for bad_id in ("..", ".", "a/b", "a\\b", ""):
            with self.assertRaises(HTTPException):
                api._session_directory(bad_id)


if __name__ == "__main__":
    unittest.main()
