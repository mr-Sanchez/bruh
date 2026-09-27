"""Offline tests for the FastAPI /api/* routes (no network, no real keys).

Fakes are injected through FastAPI's dependency_overrides, mirroring the
client_factory pattern already used inside transcriber.py/analyzer.py.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import api, config, learner_store, themes  # noqa: E402
from app.analyzer import (  # noqa: E402
    ANALYSIS_SCHEMA_VERSION,
    AnalysisResult,
    Drill,
    Issue,
    KeyPhrase,
    SceneDetail,
    Score,
    Scores,
)
from app.analyzer import AnalysisError, MissingAnthropicApiKeyError  # noqa: E402
from app.dictation_translation import (  # noqa: E402
    ReviewResult,
    SplitResult,
)
from app.exercise_sets import (  # noqa: E402
    ClaudeCall,
    GenerationResult,
    GradingResult,
    ModuleTestResult,
    PromptsResult,
    TranslationAnswer,
)
from app.server import app as fastapi_app  # noqa: E402
from app.transcriber import TranscriptionResult  # noqa: E402
from app.youtube import FetchedVideo, NoSubtitlesError  # noqa: E402
from tests.test_speech_drills import deepgram_payload, timed_words  # noqa: E402


class FakeTranscriber:
    has_api_key = True

    def __init__(
        self, transcript: str = "Yesterday I go to the store.", raw_response: Optional[Dict] = None
    ) -> None:
        self.transcript = transcript
        self.raw_response = raw_response or {"ok": True}

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        return self.transcribe_bytes(audio_path.read_bytes())

    def transcribe_bytes(self, audio: bytes, *, source: str = "upload") -> TranscriptionResult:
        self.last_audio = audio
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
            topic="present_perfect",
            quote="I go",
            explanation="Нужно прошедшее время.",
            correction="I went",
            better_versions=["I went there."],
            severity="moderate",
            drills=[
                Drill(russian="Вчера я ходил в офис.", english="Yesterday I went to the office."),
                Drill(
                    russian="Мы выпустили релиз в пятницу.",
                    english="We shipped the release on Friday.",
                ),
            ],
        )
        score = Score(score=6, comment="Норм.")
        return AnalysisResult(
            summary="Есть одна ошибка времени глагола.",
            issues=[issue],
            topic_counts={"present_perfect": 1},
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
            lesson_check=(
                {"score": 8, "verdict": "Хорошо.", "good_uses": ["I had left"], "missed": []}
                if kwargs.get("lesson") is not None
                else None
            ),
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


SET_VOCABULARY = [
    {"id": "v1", "english": "roll back a release", "russian": "откатить релиз",
     "example": "We had to roll back the release.", "example_russian": "Пришлось откатить релиз.",
     "note": ""},
    {"id": "v2", "english": "so far", "russian": "пока что, до сих пор",
     "example": "So far we have fixed two bugs.", "example_russian": "Пока что мы починили два бага.",
     "note": "Сигнал Present Perfect."},
]


class FakeGenerator:
    """Generates SET_EXERCISES; grades a translation right when it has "have"."""

    has_api_key = True

    def __init__(self) -> None:
        self.generated: List[Dict] = []
        self.graded: List[List[TranslationAnswer]] = []
        self.fail_grading: Optional[Exception] = None

    def generate(
        self, topic, seeds, avoid=(), theme=None, lesson=None, known_words=()
    ) -> GenerationResult:
        self.generated.append(
            {"topic": topic, "seeds": list(seeds), "avoid": list(avoid), "theme": theme,
             "lesson": lesson, "known_words": list(known_words)}
        )
        # Every later set gets new sentences, as the prompt asks: repeats are dropped.
        suffix = "" if len(self.generated) == 1 else f" ({len(self.generated)})"
        exercises = []
        for exercise in SET_EXERCISES:
            exercise = dict(exercise)
            for field in ("russian", "sentence", "after"):
                if field in exercise:
                    exercise[field] += suffix
            exercises.append(exercise)
        return GenerationResult(
            intro="Прошедшее время.",
            exercises=exercises,
            call=ClaudeCall(
                model=config.EXERCISE_SET_MODEL,
                usage={"input_tokens": 2_000, "output_tokens": 2_000},
                request_id="gen-1",
                effort="low",
            ),
            vocabulary=[dict(entry) for entry in SET_VOCABULARY],
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
                "topic": "sentence_structure",
            }
            for a in answers
        }
        usage = {"input_tokens": 1_000, "output_tokens": 500}
        return GradingResult(verdicts=verdicts, call=ClaudeCall(config.GRADING_MODEL, usage))

    def write_module_test(self, module_title, lessons, avoid=()) -> ModuleTestResult:
        """One choice (right answer: option 1) and one gap ("done") per lesson."""
        self.module_tests = getattr(self, "module_tests", []) + [
            {"title": module_title, "lessons": [l["key"] for l in lessons], "avoid": list(avoid)}
        ]
        n = len(self.module_tests)
        test = {
            "choices": [
                {"lesson": l["key"], "question": f"{l['key']} choice {n} ___.",
                 "options": ["a", "b", "c", "d"], "correct": 1, "explanation": "Потому что."}
                for l in lessons
            ],
            "gaps": [
                {"lesson": l["key"], "sentence": f"{l['key']} gap {n} ___ here.",
                 "answers": ["done"], "hint": "", "explanation": "Так."}
                for l in lessons
            ],
        }
        usage = {"input_tokens": 1_500, "output_tokens": 2_500}
        return ModuleTestResult(test=test, call=ClaudeCall(config.MODULE_TEST_MODEL, usage))

    def write_lesson_tasks(self, topic, level, theme=None) -> PromptsResult:
        self.task_requests = getattr(self, "task_requests", []) + [
            {"topic": topic["key"], "level": level, "theme": theme}
        ]
        n = len(self.task_requests)
        tasks = [
            {"question": f"Task {n}.{i}?", "hint": f"Задание {n}.{i}", "use": "Используйте X."}
            for i in (1, 2, 3)
        ]
        usage = {"input_tokens": 300, "output_tokens": 500}
        return PromptsResult(prompts=tasks, call=ClaudeCall(config.LESSON_TASK_MODEL, usage))

    def write_speaking_prompts(self, theme_label) -> PromptsResult:
        self.prompt_labels = getattr(self, "prompt_labels", []) + [theme_label]
        prompts = [{"question": f"About {theme_label} {n}?", "hint": f"Тема {n}"} for n in (1, 2)]
        usage = {"input_tokens": 300, "output_tokens": 400}
        return PromptsResult(prompts=prompts, call=ClaudeCall(config.THEME_PROMPTS_MODEL, usage))


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
        from app.curriculum import AREAS, TOPIC_KEYS

        response = self.client.get("/api/topics")
        self.assertEqual(response.status_code, 200)
        topics = {t["key"]: t for t in response.json()["topics"]}
        self.assertEqual(set(topics), set(TOPIC_KEYS))
        self.assertEqual(len(response.json()["areas"]), len(AREAS))
        self.assertEqual(topics["present_perfect"]["area"], "tenses")
        self.assertEqual(topics["present_perfect"]["level"], "b1")
        self.assertIsNone(topics["filler_words_fluency"]["level"])
        self.assertTrue(topics["articles_basic"]["resources"][0]["url"].startswith("https://"))
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
        self.assertIn("present_perfect", topic_keys)

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
        verb_tense = next(t for t in progress["topics"] if t["key"] == "present_perfect")
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
            json={"item_id": item_id, "topic": "articles_basic", "exercise": "x", "correct": True},
        )
        self.assertEqual(both.status_code, 400)

    def test_items_carry_their_exercise_format(self) -> None:
        self._analyzed_session()
        item = self.client.get("/api/learner/items").json()["items"][0]
        self.assertEqual(
            item["exercise"],
            {
                "type": "translate",
                "drill": 0,
                "russian": "Вчера я ходил в офис.",
                "reference": "Yesterday I went to the office.",
                "focus": "",
            },
        )
        self.assertEqual(
            item["content"]["drills"][1]["english"], "We shipped the release on Friday."
        )
        queue = self.client.get("/api/learner/queue").json()
        self.assertEqual(queue["new"][0]["exercise"]["type"], "translate")
        # After one attempt the card moves on to its next sentence.
        self.client.post(
            "/api/learner/attempts",
            json={"item_id": item["id"], "exercise": "card_translate", "correct": False},
        )
        item = self.client.get("/api/learner/items").json()["items"][0]
        self.assertEqual(item["exercise"]["drill"], 1)

    def test_old_mistakes_without_practice_sentences_are_retired(self) -> None:
        session_id = self._analyzed_session()
        path = config.recordings_dir() / session_id / config.ANALYSIS_FILENAME
        analysis = json.loads(path.read_text(encoding="utf-8"))
        del analysis["issues"][0]["drills"]  # an analysis from before schema v4
        path.write_text(json.dumps(analysis), encoding="utf-8")
        learner_store.refresh_after_analysis()

        self.assertEqual(self.client.get("/api/learner/items").json()["count"], 0)
        self.assertEqual(self.client.get("/api/learner/queue").json()["new"], [])
        topics = self.client.get("/api/learner/topics").json()["topics"]
        self.assertEqual(next(t for t in topics if t["key"] == "present_perfect")["items"], 0)
        # The bank still has it, so earlier attempts keep their topic.
        self.assertEqual(len(learner_store.load_item_bank()["items"]), 1)

    # ---------------------------------------------------------- card checks
    def _card(self) -> Dict:
        self._analyzed_session()
        return self.client.get("/api/learner/items").json()["items"][0]

    def test_a_card_answer_is_checked_by_claude_once(self) -> None:
        item = self._card()
        url = f"/api/learner/cards/{item['id']}/check"
        first = self.client.post(url, json={"drill": 0, "answer": "I have gone to the office"})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual((first.json()["graded_by"], first.json()["correct"]), ("claude", True))
        (graded,) = self.fake_generator.graded[0]
        self.assertEqual(graded.russian, "Вчера я ходил в офис.")
        self.assertEqual(graded.reference, "Yesterday I went to the office.")
        self.assertIn("I went", graded.focus)  # no pattern: the fix itself is the focus

        again = self.client.post(url, json={"drill": 0, "answer": "i have gone to the office!"})
        self.assertEqual(again.json()["graded_by"], "cache")
        self.assertEqual(len(self.fake_generator.graded), 1)
        usage = self.client.get("/api/usage").json()["by_purpose"]
        self.assertEqual(usage["anthropic:card_grading"]["calls"], 1)
        # The check logs no attempt: the browser posts it, so it can overrule.
        self.assertEqual(learner_store.load_attempts(), [])

    def test_an_exact_or_empty_answer_needs_no_call(self) -> None:
        item = self._card()
        url = f"/api/learner/cards/{item['id']}/check"
        exact = self.client.post(
            url, json={"drill": 1, "answer": "we shipped the release on friday"}
        )
        self.assertEqual((exact.json()["graded_by"], exact.json()["correct"]), ("match", True))
        empty = self.client.post(url, json={"drill": 0, "answer": "  "})
        self.assertEqual((empty.json()["graded_by"], empty.json()["correct"]), ("empty", False))
        self.assertEqual(self.fake_generator.graded, [])

    def test_card_check_errors(self) -> None:
        item = self._card()
        url = f"/api/learner/cards/{item['id']}/check"
        self.assertEqual(self.client.post(url, json={"drill": 5, "answer": "x"}).status_code, 400)
        unknown = self.client.post(
            "/api/learner/cards/fix-000000000000/check", json={"drill": 0, "answer": "x"}
        )
        self.assertEqual(unknown.status_code, 404)
        self.fake_generator.fail_grading = MissingAnthropicApiKeyError("Нет ключа.")
        self.assertEqual(self.client.post(url, json={"drill": 0, "answer": "x"}).status_code, 400)
        self.fake_generator.fail_grading = AnalysisError("Сеть.")
        self.assertEqual(self.client.post(url, json={"drill": 0, "answer": "x"}).status_code, 502)

    def test_topic_drill_attempt_derives_correct_from_its_score(self) -> None:
        passed = self.client.post(
            "/api/learner/attempts", json={"topic": "articles_basic", "exercise": "ai_set", "score": 0.9}
        )
        self.assertEqual(passed.status_code, 201, passed.text)
        self.assertTrue(passed.json()["attempt"]["correct"])
        failed = self.client.post(
            "/api/learner/attempts",
            json={"topic": "articles_basic", "exercise": "ai_set", "score": 0.5, "correct": True},
        )
        self.assertFalse(failed.json()["attempt"]["correct"])  # derived from the score
        articles = next(
            t for t in self.client.get("/api/learner/topics").json()["topics"]
            if t["key"] == "articles_basic"
        )
        self.assertAlmostEqual(articles["accuracy"], 0.7)

    def test_topic_drill_attempt_is_validated(self) -> None:
        cases = [
            ({"topic": "no_such_topic", "exercise": "cloze", "score": 1.0}, 404),
            ({"topic": "articles_basic", "exercise": "cloze"}, 400),
            ({"topic": "articles_basic", "exercise": "cloze", "score": 1.5}, 422),
            ({"topic": "articles_basic", "exercise": "cloze", "score": 1, "session_id": "../x"}, 422),
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
        body = self.client.get("/api/learner/topics").json()
        tense = next(t for t in body["topics"] if t["key"] == "present_perfect")
        self.assertEqual(tense["items"], 1)
        self.assertEqual(tense["due_items"], 1)
        self.assertIsNone(tense["accuracy"])
        self.assertGreater(tense["priority"], 0)
        self.assertEqual((tense["area"], tense["level"]), ("tenses", "b1"))
        area = next(a for a in body["areas"] if a["key"] == "tenses")
        self.assertEqual((area["label"], area["items"]), ("Времена глагола", 1))

    def test_curriculum_lists_levels_modules_and_lessons(self) -> None:
        body = self.client.get("/api/curriculum").json()
        self.assertEqual([level["key"] for level in body["levels"]], ["a2", "b1", "b2", "c1"])
        first_module = body["levels"][0]["modules"][0]
        self.assertEqual(first_module["lessons"][0], "present_simple_continuous")
        self.assertIn("present_perfect", {t["key"] for t in body["topics"]})

    def test_today_is_empty_before_any_recording(self) -> None:
        body = self.client.get("/api/learner/today").json()
        self.assertEqual(
            [s["kind"] for s in body["steps"]], ["cards", "monologue", "dictation", "lesson"]
        )
        self.assertEqual(body["steps"][0]["status"], "empty")
        self.assertEqual(body["steps"][1]["status"], "todo")
        self.assertTrue(body["steps"][1]["prompt"]["question"])
        # No lesson imported yet: the dictation step invites one instead of
        # holding the whole workout back.
        self.assertEqual(body["steps"][2]["status"], "empty")
        self.assertIsNone(body["steps"][2]["lesson"])
        # No mistakes yet: «Урок дня» is the course's first lesson, theory first.
        lesson = body["steps"][3]
        self.assertEqual(
            (lesson["lesson"]["key"], lesson["reason"], lesson["action"], lesson["optional"]),
            ("present_simple_continuous", "course", "theory", True),
        )
        self.assertEqual(body["streak_days"], 0)
        self.assertIsNone(body["focus_topic"])

    def test_today_workout_tracks_each_step(self) -> None:
        session_id = self._analyzed_session()
        body = self.client.get("/api/learner/today").json()
        cards, live, dictation_step, _lesson = body["steps"]
        self.assertEqual((cards["status"], cards["queue_total"]), ("todo", 1))
        self.assertEqual((live["status"], live["session_id"]), ("done", session_id))
        self.assertEqual(body["focus_topic"]["key"], "present_perfect")
        self.assertEqual(body["streak_days"], 1)

        self.client.post(
            "/api/learner/attempts",
            json={"item_id": cards["items"][0]["id"], "exercise": "card_translate", "correct": True},
        )
        body = self.client.get("/api/learner/today").json()
        self.assertEqual(
            [s["status"] for s in body["steps"]], ["done", "done", "empty", "todo"]
        )
        self.assertEqual(dictation_step["kind"], "dictation")
        self.assertEqual(body["minutes_left"], 0)

        history = self.client.get("/api/learner/history").json()["days"]
        self.assertEqual((history[0]["cards"], history[0]["cards_correct"]), (1, 1))

    def test_a_picture_description_fills_the_live_step(self) -> None:
        def live_step() -> Dict:
            steps = self.client.get("/api/learner/today").json()["steps"]
            return next(step for step in steps if step["kind"] == "monologue")

        upload = self._upload_picture(text="There is a cat.")
        live = live_step()
        self.assertEqual((live["status"], live["activity"]), ("analyze", "picture"))
        self.client.post(f"/api/sessions/{upload['session_id']}/analyze")
        live = live_step()
        self.assertEqual((live["status"], live["session_id"]), ("done", upload["session_id"]))

    def test_static_files_are_revalidated_after_an_update(self) -> None:
        response = self.client.get("/js/app.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-cache")


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
        first = self._upload_drill("talk", prompt_id="travel:2")
        detail = self.client.get(f"/api/sessions/{first['session_id']}").json()
        self.assertEqual(detail["kind"], "talk")
        question, hint = themes.THEME_BY_KEY["travel"].prompts[2]
        self.assertEqual(
            detail["drill"],
            {
                "prompt_id": "travel:2",
                "question": question,
                "hint": hint,
                "theme": {"key": "travel", "label": "Путешествия"},
                "series": first["session_id"],
                "round": 1,
            },
        )
        # A take on a prompt makes its context the default.
        self.assertEqual(self.client.get("/api/themes").json()["last"]["key"], "travel")
        metrics = detail["speech"]["metrics"]
        self.assertEqual((metrics["fillers"], metrics["long_pauses"]), (1, 1))

        attempts = self._drill_attempts()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["topic"], config.FLUENCY_TOPIC)
        self.assertEqual(attempts[0]["score"], metrics["fluency_score"])
        self.assertEqual(attempts[0]["session_id"], first["session_id"])

        # Rounds 2 and 3 take the prompt of the series, whatever is sent.
        for expected_round in (2, 3):
            take = self._upload_drill("talk", prompt_id="news:1", series=first["session_id"])
            drill = self.client.get(f"/api/sessions/{take['session_id']}").json()["drill"]
            self.assertEqual((drill["round"], drill["prompt_id"]), (expected_round, "travel:2"))
        self.assertEqual(len(self._drill_attempts()), 1)

        series = self.client.get("/api/speech/talks").json()["series"]
        self.assertEqual(len(series), 1)
        self.assertEqual([r["round"] for r in series[0]["rounds"]], [1, 2, 3])
        self.assertEqual(series[0]["prompt"]["hint"], hint)
        self.assertEqual(series[0]["rounds"][0]["metrics"]["fillers"], 1)

        usage = self.client.get("/api/usage").json()["by_purpose"]
        self.assertEqual(usage["deepgram:speech_drill"]["calls"], 3)

    def test_talk_takes_are_validated(self) -> None:
        self._upload_drill("talk", expect=400)
        for bad in ("it_backend:99", "no_such:0", "own_1:0", "it_backend", "x"):
            self._upload_drill("talk", expect=400, prompt_id=bad)
        monologue = self._upload_session()
        self._upload_drill(
            "talk", expect=400, prompt_id="it_backend:0", series=monologue["session_id"]
        )
        self._upload_drill(
            "talk", expect=404, prompt_id="it_backend:0", series="2020-01-01_00-00-00"
        )
        self._upload_drill("talk", expect=400, prompt_id="it_backend:0", series="..")
        typed = self.client.post(
            "/api/sessions", data={"kind": "talk", "text": "hello", "prompt_id": "it_backend:0"}
        )
        self.assertEqual(typed.status_code, 400)

    def test_a_talk_without_filler_detection_is_not_scored(self) -> None:
        self._hear("Я думаю что да")
        take = self._upload_drill("talk", prompt_id="it_backend:0", language="ru")
        detail = self.client.get(f"/api/sessions/{take['session_id']}").json()
        self.assertIsNone(detail["speech"]["metrics"]["fillers"])
        self.assertEqual(self._drill_attempts(), [])

    def test_drills_are_not_analysed_and_do_not_fill_the_live_step(self) -> None:
        self._hear("Okay let me try this")
        take = self._upload_drill("talk", prompt_id="it_backend:0")
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
    def _create_set(self, topic: str = "present_perfect", force: bool = False) -> Dict:
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
        body = self.client.get("/api/practice/sets", params={"topic": "articles_basic"}).json()
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
        self.assertEqual(exercise_set["topic"], "present_perfect")
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
        listed = self.client.get("/api/practice/sets", params={"topic": "present_perfect"}).json()
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
            self.client.post("/api/practice/sets", json={"topic": "articles_basic"}).status_code, 400
        )
        self.fake_generator.generate = failing(AnalysisError("upstream"))
        self.assertEqual(
            self.client.post("/api/practice/sets", json={"topic": "articles_basic"}).status_code, 502
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
        self.assertEqual(next(t for t in topics if t["key"] == "present_perfect")["accuracy"], 1.0)
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
        # Only the translation has a Russian sentence to practise: gap and fix
        # mistakes stay as retired history, the blank translation makes nothing.
        self.assertEqual(body["new_cards"], 1)
        empty = next(r for r in body["run"]["results"] if r["exercise_id"] == "ex4")
        self.assertEqual(
            (empty["graded_by"], empty["corrected"]), ("empty", "We had a call yesterday.")
        )

        queue = self.client.get("/api/learner/queue").json()
        from_set = [item for item in queue["new"] if item.get("origin") == "ai_set"]
        self.assertEqual(len(from_set), 1)
        self.assertEqual(from_set[0]["content"]["correction"], "I have already fixed the bug.")
        self.assertEqual(from_set[0]["exercise"]["russian"], "Я уже починил баг.")
        # Filed under the topic Claude tagged the mistake with, not the set's.
        self.assertEqual(from_set[0]["topic"], "sentence_structure")

        again = self._submit(set_id, answers)
        self.assertEqual(len(self.fake_generator.graded), 1)  # verdict came from the cache
        ex3 = next(r for r in again["run"]["results"] if r["exercise_id"] == "ex3")
        self.assertEqual((ex3["graded_by"], ex3["topic"]), ("cache", "sentence_structure"))
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

    def test_a_card_answer_can_be_dictated(self) -> None:
        transcriber = FakeTranscriber(transcript=" I will deploy it after the code review. ")
        profiles = []
        fastapi_app.dependency_overrides[api.get_transcriber_factory] = lambda: (
            lambda profile: profiles.append(profile) or transcriber
        )
        response = self.client.post(
            "/api/learner/dictate",
            files={"audio": ("answer.webm", b"webm-bytes", "audio/webm")},
            data={"duration_seconds": "3.2"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["text"], "I will deploy it after the code review.")
        self.assertEqual(transcriber.last_audio, b"webm-bytes")
        self.assertEqual((profiles[0].language, profiles[0].filler_words), ("en-US", False))
        usage = self.client.get("/api/usage").json()["by_purpose"]
        self.assertEqual(usage["deepgram:card_dictation"]["calls"], 1)
        # Nothing is kept as a recording.
        self.assertEqual(self.client.get("/api/sessions").json()["sessions"], [])

        empty = self.client.post(
            "/api/learner/dictate", files={"audio": ("a.webm", b"", "audio/webm")}
        )
        self.assertEqual(empty.status_code, 400)
        with patch.object(config, "CARD_DICTATION_MAX_BYTES", 4):
            too_long = self.client.post(
                "/api/learner/dictate", files={"audio": ("a.webm", b"12345", "audio/webm")}
            )
        self.assertEqual(too_long.status_code, 400)

    def test_picked_vocabulary_becomes_word_cards(self) -> None:
        created = self._create_set()
        set_id = created["set"]["id"]
        self.assertEqual([v["id"] for v in created["set"]["vocabulary"]], ["v1", "v2"])
        self.assertEqual(created["picked_vocabulary"], [])
        body = self._submit(set_id, self.GOOD_ANSWERS)
        self.assertEqual(body["picked_vocabulary"], [])
        self.assertEqual(body["words_per_day"], config.NEW_WORDS_PER_DAY)

        url = f"/api/practice/sets/{set_id}/vocabulary"
        self.assertEqual(self.client.put(url, json={"picked": ["v9"]}).status_code, 400)
        saved = self.client.put(url, json={"picked": ["v2", "v1"]}).json()
        self.assertEqual((saved["picked"], saved["added"], saved["removed"]), (["v1", "v2"], 2, 0))

        words = [i for i in self.client.get("/api/learner/queue").json()["new"]
                 if i["kind"] == "word"]
        self.assertEqual([w["content"]["english"] for w in words], ["roll back a release", "so far"])
        self.assertEqual(words[0]["exercise"], {"type": "flip"})
        self.assertEqual(words[1]["content"]["note"], "Сигнал Present Perfect.")
        self.assertEqual(words[0]["lesson_id"], "present_perfect")

        # Unticking takes the card away; the choice is shown with the set.
        saved = self.client.put(url, json={"picked": ["v2"]}).json()
        self.assertEqual((saved["added"], saved["removed"]), (0, 1))
        self.assertEqual(
            self.client.get(f"/api/practice/sets/{set_id}").json()["picked_vocabulary"], ["v2"]
        )
        queue = self.client.get("/api/learner/queue").json()
        self.assertEqual([i["content"]["english"] for i in queue["new"] if i["kind"] == "word"],
                         ["so far"])
        # The next set is told which words are already cards.
        self._create_set(force=True)
        self.assertEqual(self.fake_generator.generated[-1]["known_words"], ["so far"])

    def test_lesson_of_the_day_follows_the_mistakes_and_its_next_action(self) -> None:
        self._analyzed_session()  # a mistake on present_perfect
        with patch.object(config, "get_anthropic_api_key", return_value="key"):
            body = self.client.get("/api/learner/today").json()
        step = body["steps"][-1]
        self.assertEqual((step["kind"], step["status"], step["optional"]), ("lesson", "todo", True))
        self.assertEqual((step["lesson"]["key"], step["reason"]), ("present_perfect", "mistakes"))
        self.assertEqual((step["action"], step["speech_mistakes"]), ("theory", 1))
        self.assertEqual(step["cost_usd"], config.THEORY_COST_ESTIMATE_USD)
        self.assertEqual(body["minutes_left"], 0)  # one card (~0.5 min); the lesson is optional

        learner_store.add_theory_version("present_perfect", {"created_at": "2026-09-26T09:00:00"})
        step = self.client.get("/api/learner/today").json()["steps"][-1]
        self.assertEqual((step["action"], step["set_id"]), ("set", None))
        self.assertEqual(step["cost_usd"], config.SET_COST_ESTIMATE_USD)
        set_id = self._create_set()["set"]["id"]
        step = self.client.get("/api/learner/today").json()["steps"][-1]
        self.assertEqual((step["set_id"], step["cost_usd"]), (set_id, 0.0))

        self._submit(set_id, self.GOOD_ANSWERS)
        step = self.client.get("/api/learner/today").json()["steps"][-1]
        self.assertEqual((step["status"], step["score"]), ("done", 1.0))
        self.assertEqual(step["lesson"]["key"], "present_perfect")

        roadmap = self.client.get("/api/roadmap").json()
        self.assertEqual([r["id"] for r in roadmap["recommended"]], ["present_perfect"])


class FakeFetcher:
    """Stands in for the YouTube import: writes an audio file, returns captions."""

    VTT = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:05.000\n"
        "Hello everyone, and welcome back to the channel.\n\n"
        "00:00:05.100 --> 00:00:09.000\n"
        "Today we are going to talk about code review.\n"
    )

    def __init__(self, error: Optional[Exception] = None) -> None:
        self.error = error
        self.calls: List = []

    def fetch(self, url: str, target_dir: Path, languages) -> FetchedVideo:
        self.calls.append((url, tuple(languages)))
        if self.error is not None:
            raise self.error
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "audio.m4a").write_bytes(b"fake-audio-bytes")
        return FetchedVideo(
            video_id="dQw4w9WgXcQ",
            url=url,
            title="Deploying on Fridays",
            uploader="Some Channel",
            duration_seconds=300.0,
            audio_filename="audio.m4a",
            subtitles=self.VTT,
            subtitle_language="en",
            subtitle_kind="manual",
        )


class DictationApiTests(unittest.TestCase):
    """The dictation routes, with the YouTube import replaced by a fake."""

    URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        base_dir_patch.start()
        self.addCleanup(base_dir_patch.stop)
        self.fetcher = FakeFetcher()
        fastapi_app.dependency_overrides[api.get_fetcher_factory] = lambda: (lambda: self.fetcher)
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.client = TestClient(fastapi_app)

    def _import(self) -> Dict:
        response = self.client.post("/api/dictation/lessons", json={"url": self.URL})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def test_importing_a_video_builds_a_lesson_from_its_subtitles(self) -> None:
        body = self._import()
        self.assertEqual(body["lesson_id"], "dQw4w9WgXcQ")
        self.assertEqual(self.fetcher.calls[0][1], ("en",))

        lesson = self.client.get("/api/dictation/lessons/dQw4w9WgXcQ").json()
        self.assertEqual(lesson["status"], "ready")
        self.assertEqual(lesson["title"], "Deploying on Fridays")
        self.assertEqual(len(lesson["sentences"]), 2)
        self.assertEqual(lesson["sentences"][0]["tokens"][0]["text"], "Hello")
        # The audio is served as a file, so the browser can seek in it.
        audio = self.client.get("/api/dictation/lessons/dQw4w9WgXcQ/audio")
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio.content, b"fake-audio-bytes")

    def test_an_already_imported_video_is_not_downloaded_twice(self) -> None:
        self._import()
        self._import()
        self.assertEqual(len(self.fetcher.calls), 1)

    def test_a_link_that_is_not_youtube_is_refused(self) -> None:
        response = self.client.post("/api/dictation/lessons", json={"url": "https://vimeo.com/1"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.fetcher.calls, [])

    def test_a_video_without_subtitles_leaves_the_lesson_in_error(self) -> None:
        self.fetcher.error = NoSubtitlesError("У этого видео нет субтитров.")
        self._import()

        lesson = self.client.get("/api/dictation/lessons/dQw4w9WgXcQ").json()

        self.assertEqual(lesson["status"], "error")
        self.assertIn("субтитров", lesson["error_message"])
        response = self.client.get("/api/dictation/lessons/dQw4w9WgXcQ/audio")
        self.assertEqual(response.status_code, 404)

    def test_a_dictated_sentence_is_graded_on_the_server(self) -> None:
        self._import()

        response = self.client.post(
            "/api/dictation/lessons/dQw4w9WgXcQ/results",
            json={
                "sentence": 0,
                # "channel" is misspelt, "welcome" was hinted, the rest is right.
                "answers": ["hello", "everyone", "and", "welcome", "back", "to", "the", "chanel"],
                "hints": [3],
                "error_chars": 2,
                "seconds": 41.5,
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["result"]["incorrect_words"], ["channel"])
        self.assertEqual(body["result"]["hint_words"], ["welcome"])
        self.assertEqual(body["result"]["error_chars"], 2)
        self.assertFalse(body["result"]["completed"])
        self.assertEqual(body["progress"]["started"], 1)
        self.assertEqual(body["progress"]["done"], 0)

        stats = self.client.get("/api/dictation/stats").json()
        self.assertEqual(stats["lessons"], 1)
        self.assertEqual(stats["hints"], 1)
        self.assertEqual(stats["tricky_words"], [])  # once is not yet "tricky"

    def test_an_unknown_sentence_or_lesson_is_a_404(self) -> None:
        self._import()
        response = self.client.post(
            "/api/dictation/lessons/dQw4w9WgXcQ/results", json={"sentence": 99, "answers": []}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/api/dictation/lessons/aaaaaaaaaaa").status_code, 404)

    def test_the_lesson_id_is_guarded_like_a_session_id(self) -> None:
        for bad_id in ("..", "a/b", "%2e%2e", "a b"):
            response = self.client.get(f"/api/dictation/lessons/{bad_id}")
            self.assertIn(response.status_code, (400, 404), bad_id)

    def test_dictation_fills_the_daily_step_and_the_history(self) -> None:
        self._import()
        step = next(
            s
            for s in self.client.get("/api/learner/today").json()["steps"]
            if s["kind"] == "dictation"
        )
        self.assertEqual(step["status"], "todo")
        self.assertEqual(step["target"], config.DICTATION_DAILY_SENTENCES)
        self.assertEqual(step["lesson"]["id"], "dQw4w9WgXcQ")

        with patch.object(config, "DICTATION_DAILY_SENTENCES", 1):
            self.client.post(
                "/api/dictation/lessons/dQw4w9WgXcQ/results",
                json={
                    "sentence": 0,
                    "answers": [
                        "Hello",
                        "everyone",
                        "and",
                        "welcome",
                        "back",
                        "to",
                        "the",
                        "channel",
                    ],
                },
            )
            body = self.client.get("/api/learner/today").json()

        step = next(s for s in body["steps"] if s["kind"] == "dictation")
        self.assertEqual((step["status"], step["done_today"]), ("done", 1))
        self.assertEqual(body["streak_days"], 1)  # a dictated sentence counts

        day = self.client.get("/api/learner/history").json()["days"][0]
        self.assertEqual(day["dictation"]["sentences"], 1)

    def test_deleting_a_lesson_removes_it(self) -> None:
        self._import()

        deleted = self.client.delete("/api/dictation/lessons/dQw4w9WgXcQ").json()

        self.assertEqual(deleted["deleted"], "dQw4w9WgXcQ")
        self.assertEqual(self.client.get("/api/dictation/lessons").json()["lessons"], [])


class FakeTranslator:
    """Splits every lesson at sentences 9 and 19; reviews every text as "fair"."""

    has_api_key = True

    def __init__(self) -> None:
        self.splits: List[List[str]] = []
        self.reviews: List[Dict] = []
        self.error: Optional[Exception] = None

    def split(self, sentences) -> SplitResult:
        if self.error is not None:
            raise self.error
        self.splits.append(list(sentences))
        usage = {"input_tokens": 3_000, "output_tokens": 40}
        return SplitResult(starts=[9, 19], call=ClaudeCall(config.TRANSLATION_SPLIT_MODEL, usage))

    def review(self, sentences, translation, *, subtitle_language="en") -> ReviewResult:
        if self.error is not None:
            raise self.error
        self.reviews.append(
            {"sentences": list(sentences), "text": translation, "language": subtitle_language}
        )
        usage = {"input_tokens": 700, "output_tokens": 400}
        review = {"quality": "fair", "summary": "Норм.", "issues": [], "model_translation": "x"}
        return ReviewResult(review=review, call=ClaudeCall(config.TRANSLATION_REVIEW_MODEL, usage))


class DictationTranslationApiTests(unittest.TestCase):
    """Cutting a lesson into parts and reviewing translations, with fakes."""

    URL = DictationApiTests.URL
    LESSON = "/api/dictation/lessons/dQw4w9WgXcQ"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        base_dir_patch.start()
        self.addCleanup(base_dir_patch.stop)
        self.fetcher = FakeFetcher()
        self.fetcher.VTT = "WEBVTT\n\n" + "".join(
            f"00:{n * 4 // 60:02d}:{n * 4 % 60:02d}.000 --> "
            f"00:{n * 4 // 60:02d}:{n * 4 % 60 + 3:02d}.000\n"
            f"Sentence number {n} is about deployment today.\n\n"
            for n in range(30)
        )
        self.translator = FakeTranslator()
        fastapi_app.dependency_overrides[api.get_fetcher_factory] = lambda: (lambda: self.fetcher)
        fastapi_app.dependency_overrides[api.get_translator_factory] = lambda: (
            lambda: self.translator
        )
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.client = TestClient(fastapi_app)
        self.client.post("/api/dictation/lessons", json={"url": self.URL})

    def test_a_long_lesson_needs_one_split_call_and_keeps_the_result(self) -> None:
        lesson = self.client.get(self.LESSON).json()
        self.assertEqual(len(lesson["sentences"]), 30)
        self.assertTrue(lesson["translation"]["needs_split"])

        body = self.client.post(f"{self.LESSON}/parts").json()

        self.assertFalse(body["needs_split"])
        self.assertEqual([p["sentences"] for p in body["parts"]], [9, 10, 11])
        self.assertTrue(body["parts"][0]["text"].startswith("Sentence number 0 is"))
        # The cut is cached: asking again is free, force pays for a new one.
        self.client.post(f"{self.LESSON}/parts")
        self.assertEqual(len(self.translator.splits), 1)
        self.client.post(f"{self.LESSON}/parts?force=true")
        self.assertEqual(len(self.translator.splits), 2)
        usage = self.client.get("/api/usage").json()
        self.assertEqual(usage["by_purpose"]["anthropic:dictation_split"]["calls"], 2)

    def test_a_translation_is_reviewed_stored_and_not_paid_for_twice(self) -> None:
        self.client.post(f"{self.LESSON}/parts")

        first = self.client.post(f"{self.LESSON}/parts/1/translation", json={"text": " Перевод. "})
        again = self.client.post(f"{self.LESSON}/parts/1/translation", json={"text": "Перевод."})

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json()["translation"]["review"]["quality"], "fair")
        self.assertTrue(again.json()["cached"])
        self.assertEqual(len(self.translator.reviews), 1)
        self.assertEqual(len(self.translator.reviews[0]["sentences"]), 10)
        self.assertEqual(self.translator.reviews[0]["language"], "en")
        shown = self.client.get(self.LESSON).json()["translation"]["parts"][1]["translation"]
        self.assertEqual(shown["text"], "Перевод.")
        usage = self.client.get("/api/usage").json()
        self.assertEqual(usage["by_purpose"]["anthropic:dictation_translation"]["calls"], 1)
        # Translation results stay out of the learner model.
        self.assertEqual(self.client.get("/api/learner/items").json()["count"], 0)

    def test_a_failed_review_still_keeps_the_text(self) -> None:
        self.client.post(f"{self.LESSON}/parts")
        self.translator.error = AnalysisError("Claude недоступен")

        response = self.client.post(f"{self.LESSON}/parts/0/translation", json={"text": "Мой текст"})

        self.assertEqual(response.status_code, 502)
        shown = self.client.get(self.LESSON).json()["translation"]["parts"][0]["translation"]
        self.assertEqual((shown["text"], shown["review"]), ("Мой текст", None))
        # The same text is reviewed on the next try, since nothing was graded.
        self.translator.error = None
        retry = self.client.post(f"{self.LESSON}/parts/0/translation", json={"text": "Мой текст"})
        self.assertEqual(retry.status_code, 201)
        self.assertFalse(retry.json()["cached"])

    def test_a_missing_key_is_a_400_and_bad_parts_are_refused(self) -> None:
        self.translator.error = MissingAnthropicApiKeyError("no key")
        self.assertEqual(self.client.post(f"{self.LESSON}/parts").status_code, 400)
        self.translator.error = None

        # Not cut yet: a translation has no part to belong to.
        early = self.client.post(f"{self.LESSON}/parts/0/translation", json={"text": "x"})
        self.assertEqual(early.status_code, 409)
        self.client.post(f"{self.LESSON}/parts")
        late = self.client.post(f"{self.LESSON}/parts/7/translation", json={"text": "x"})
        self.assertEqual(late.status_code, 404)
        blank = self.client.post(f"{self.LESSON}/parts/0/translation", json={"text": "   "})
        self.assertEqual(blank.status_code, 400)
        self.assertEqual(self.translator.reviews, [])

    def test_a_lesson_that_fits_one_part_is_never_split_by_the_model(self) -> None:
        fetcher = FakeFetcher()  # two sentences
        fastapi_app.dependency_overrides[api.get_fetcher_factory] = lambda: (lambda: fetcher)
        self.client.delete(self.LESSON)
        self.client.post("/api/dictation/lessons", json={"url": self.URL})

        body = self.client.post(f"{self.LESSON}/parts").json()

        self.assertEqual([p["sentences"] for p in body["parts"]], [2])
        self.assertEqual(self.translator.splits, [])


class RoadmapApiTests(unittest.TestCase):
    """GET /api/roadmap and the lesson marks - no model calls at all."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        base_dir_patch.start()
        self.addCleanup(base_dir_patch.stop)
        self.client = TestClient(fastapi_app)

    @staticmethod
    def _lesson(data: Dict, lesson_id: str) -> Dict:
        return next(
            lesson
            for level in data["levels"]
            for module in level["modules"]
            for lesson in module["lessons"]
            if lesson["id"] == lesson_id
        )

    def test_fresh_roadmap_starts_at_the_first_lesson(self) -> None:
        data = self.client.get("/api/roadmap").json()
        self.assertEqual(data["continue"], "present_simple_continuous")
        self.assertEqual(data["counts"]["not_started"], data["total"])

    def test_marks_are_logged_and_can_be_cleared(self) -> None:
        url = "/api/roadmap/lessons/present_simple_continuous/mark"
        data = self.client.post(url, json={"mark": "known"}).json()
        self.assertEqual(self._lesson(data, "present_simple_continuous")["status"], "known")
        self.assertEqual(data["continue"], "past_simple")

        data = self.client.post(url, json={"mark": None}).json()
        self.assertEqual(self._lesson(data, "present_simple_continuous")["status"], "not_started")
        marks = learner_store.load_roadmap_marks()
        self.assertEqual([m["mark"] for m in marks], ["known", None])

    def test_set_runs_drive_the_status(self) -> None:
        import datetime as dt

        for day in (21, 22):
            learner_store.append_attempt(
                None, "ai_set", True, topic="past_simple", score=0.9,
                set_id="set-x", when=dt.datetime(2026, 9, day, 10),
            )
        data = self.client.get("/api/roadmap").json()
        self.assertEqual(self._lesson(data, "past_simple")["status"], "mastered")

    def test_rejects_unknown_lessons_and_marks(self) -> None:
        bad_lesson = self.client.post("/api/roadmap/lessons/other/mark", json={"mark": "known"})
        self.assertEqual(bad_lesson.status_code, 404)
        bad_mark = self.client.post(
            "/api/roadmap/lessons/past_simple/mark", json={"mark": "mastered"}
        )
        self.assertEqual(bad_mark.status_code, 422)
        self.assertEqual(learner_store.load_roadmap_marks(), [])


class SessionDirectoryGuardTests(unittest.TestCase):
    """The path-traversal guard on session ids, tested directly."""

    def test_rejects_path_traversal_attempts(self) -> None:
        from fastapi import HTTPException

        for bad_id in ("..", ".", "a/b", "a\\b", ""):
            with self.assertRaises(HTTPException):
                api._session_directory(bad_id)


if __name__ == "__main__":
    unittest.main()
