"""Offline tests for the Claude analysis integration (no network needed)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Dict

import httpx2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.analyzer import (  # noqa: E402
    AnalysisError,
    ClaudeAnalyzer,
    ImageInput,
    Issue,
    MissingAnthropicApiKeyError,
    PictureAnalysis,
    SpeechAnalysis,
)
from app.curriculum import TOPIC_KEYS  # noqa: E402


class FakeResponse:
    def __init__(self, parsed: SpeechAnalysis, stop_reason: str = "end_turn") -> None:
        self.parsed_output = parsed
        self.stop_reason = stop_reason
        self.stop_details = None
        self._request_id = "fake-request-id"


class FakeMessages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.captured: Dict[str, Any] = {}

    def parse(self, **kwargs: Any) -> Any:
        self.captured = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class FakeClient:
    def __init__(self, messages: FakeMessages) -> None:
        self.messages = messages


def make_analyzer(response: Any = None, error: Exception | None = None):
    parsed = response or SpeechAnalysis(
        summary="Хороший результат, но есть пара ошибок.",
        strengths=["Говорит длинными фразами без остановок."],
        issues=[
            {
                "topic": "present_perfect",
                "quote": "Yesterday I go to the store",
                "explanation": "Нужно прошедшее время.",
                "correction": "Yesterday I went to the store",
                "better_versions": ["I went shopping yesterday."],
                "pattern": {"rule": "yesterday + Past Simple", "examples": ["I went home."]},
                "severity": "moderate",
            }
        ],
        vocabulary=[{"phrase": "recruiter", "meaning": "рекрутер", "example": "A recruiter called me."}],
        improved_version="Yesterday I went to the store.",
        takeaways=[{"phrase": "went", "meaning": "прошедшее от go", "example": "I went home."}],
        scores={
            "grammar": {"score": 5, "comment": "Ошибки во временах."},
            "vocabulary": {"score": 6, "comment": "Хватает слов."},
            "fluency": {"score": 6, "comment": "Есть паузы."},
            "naturalness": {"score": 5, "comment": "Кальки с русского."},
        },
    )
    messages = FakeMessages(FakeResponse(parsed) if response is None or isinstance(response, SpeechAnalysis) else response, error)
    analyzer = ClaudeAnalyzer("fake-key", client_factory=lambda key: FakeClient(messages))
    return analyzer, messages


def _fake_request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


class ClaudeAnalyzerTests(unittest.TestCase):
    def test_missing_api_key_raises_the_documented_message(self) -> None:
        analyzer = ClaudeAnalyzer(None, client_factory=lambda key: None)
        with self.assertRaises(MissingAnthropicApiKeyError) as ctx:
            analyzer.analyze("some transcript", config.default_profile(), 10.0)
        self.assertEqual(str(ctx.exception), config.MISSING_ANTHROPIC_API_KEY_MESSAGE)

    def test_empty_transcript_is_rejected_without_a_network_call(self) -> None:
        analyzer, messages = make_analyzer()
        with self.assertRaises(AnalysisError):
            analyzer.analyze("   ", config.default_profile(), 10.0)
        self.assertEqual(messages.captured, {})

    def test_request_is_built_with_russian_instructions_and_full_taxonomy(self) -> None:
        analyzer, messages = make_analyzer()
        analyzer.analyze("Yesterday I go... um... to the store.", config.default_profile(), 42.0)

        self.assertEqual(messages.captured["model"], config.ANALYSIS_MODEL)
        self.assertIn("Russian", messages.captured["system"])
        for key in TOPIC_KEYS:
            self.assertIn(key, messages.captured["system"])
        self.assertEqual(messages.captured["output_format"], SpeechAnalysis)
        self.assertEqual(messages.captured["thinking"], {"type": "adaptive"})
        content = messages.captured["messages"][0]["content"]
        self.assertEqual([block["type"] for block in content], ["text"])
        self.assertIn("Yesterday I go", content[0]["text"])

    def test_prompt_asks_for_the_coach_style_sections(self) -> None:
        analyzer, messages = make_analyzer()
        analyzer.analyze("Yesterday I go to the store.", config.default_profile(), 5.0)
        system = messages.captured["system"]
        for field_name in (
            "better_versions",
            "pattern",
            "vocabulary",
            "improved_version",
            "takeaways",
            "scores",
        ):
            self.assertIn(field_name, system)
        self.assertGreaterEqual(messages.captured["max_tokens"], 16_000)

    def test_prompt_asks_for_new_practice_sentences_per_mistake(self) -> None:
        # Cards are built from these, not from the verbatim quote (2026-09-26).
        analyzer, messages = make_analyzer()
        analyzer.analyze("Yesterday I go to the store.", config.default_profile(), 5.0)
        system = messages.captured["system"]
        self.assertIn(f"`drills`: {config.DRILLS_PER_ISSUE} practice sentences", system)
        self.assertIn("never the speaker's own sentence", system)
        self.assertIn("drills", Issue.model_json_schema()["properties"])

    def test_successful_analysis_returns_topic_counts(self) -> None:
        analyzer, _ = make_analyzer()
        result = analyzer.analyze("Yesterday I go to the store.", config.default_profile(), 5.0)
        self.assertEqual(result.topic_counts, {"present_perfect": 1})
        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.request_id, "fake-request-id")

    def test_successful_analysis_carries_the_coach_sections(self) -> None:
        analyzer, _ = make_analyzer()
        result = analyzer.analyze("Yesterday I go to the store.", config.default_profile(), 5.0)
        self.assertEqual(result.issues[0].better_versions, ["I went shopping yesterday."])
        self.assertEqual(result.issues[0].pattern.rule, "yesterday + Past Simple")
        self.assertEqual(result.vocabulary[0].phrase, "recruiter")
        self.assertEqual(result.improved_version, "Yesterday I went to the store.")
        self.assertEqual(len(result.takeaways), 1)
        self.assertEqual(result.strengths, ["Говорит длинными фразами без остановок."])
        # (5 + 6 + 6 + 5) / 4 = 5.5
        self.assertEqual(result.overall_score, 5.5)

    def test_picture_request_sends_the_image_and_asks_for_the_scene(self) -> None:
        parsed = PictureAnalysis(
            summary="Описание неполное.",
            not_mentioned=[{"detail": "Собака на заднем плане", "phrase": "A dog is sleeping."}],
            scene_vocabulary=[
                {"phrase": "in the background", "meaning": "на заднем плане", "example": "x"}
            ],
        )
        analyzer, messages = make_analyzer(parsed)
        image = ImageInput(data=b"\xff\xd8\xffjpeg", media_type="image/jpeg")
        result = analyzer.analyze("There is a man.", config.default_profile(), 30.0, image=image)

        content = messages.captured["messages"][0]["content"]
        self.assertEqual([block["type"] for block in content], ["image", "text"])
        self.assertEqual(content[0]["source"]["media_type"], "image/jpeg")
        self.assertEqual(content[0]["source"]["data"], "/9j/anBlZw==")
        self.assertEqual(messages.captured["output_format"], PictureAnalysis)
        self.assertIn("PICTURE DESCRIPTION", messages.captured["system"])
        self.assertIn("not_mentioned", messages.captured["system"])
        self.assertEqual(result.not_mentioned[0].phrase, "A dog is sleeping.")
        self.assertEqual(result.scene_vocabulary[0].phrase, "in the background")

    def test_typed_text_leaves_fluency_out_of_the_overall_score(self) -> None:
        parsed = SpeechAnalysis(
            summary="ok",
            scores={
                "grammar": {"score": 6, "comment": ""},
                "vocabulary": {"score": 7, "comment": ""},
                "fluency": {"score": 1, "comment": ""},
                "naturalness": {"score": 8, "comment": ""},
            },
        )
        analyzer, messages = make_analyzer(parsed)
        result = analyzer.analyze("I writed this.", config.default_profile(), typed=True)
        self.assertEqual(result.overall_score, 7.0)
        self.assertIn("TYPED", messages.captured["system"])
        self.assertNotIn("PICTURE DESCRIPTION", messages.captured["system"])
        self.assertIn("Typed text", messages.captured["messages"][0]["content"][-1]["text"])

    def test_out_of_range_scores_are_clamped(self) -> None:
        parsed = SpeechAnalysis(
            summary="ok",
            scores={
                "grammar": {"score": 0, "comment": ""},
                "vocabulary": {"score": 14, "comment": ""},
                "fluency": {"score": 7, "comment": ""},
                "naturalness": {"score": 7, "comment": ""},
            },
        )
        analyzer, _ = make_analyzer(parsed)
        result = analyzer.analyze("some transcript", config.default_profile(), 1.0)
        self.assertEqual(result.scores.grammar.score, 1)
        self.assertEqual(result.scores.vocabulary.score, 10)
        # (1 + 10 + 7 + 7) / 4 = 6.25 -> nearest half point
        self.assertEqual(result.overall_score, 6.5)

    def test_missing_scores_leave_overall_empty(self) -> None:
        analyzer, _ = make_analyzer(SpeechAnalysis(summary="ok"))
        result = analyzer.analyze("some transcript", config.default_profile(), 1.0)
        self.assertIsNone(result.scores)
        self.assertIsNone(result.overall_score)

    def test_refusal_stop_reason_is_mapped_to_a_friendly_error(self) -> None:
        response = FakeResponse(SpeechAnalysis(summary="", issues=[]), stop_reason="refusal")
        messages = FakeMessages(response)
        analyzer = ClaudeAnalyzer("fake-key", client_factory=lambda key: FakeClient(messages))
        with self.assertRaises(AnalysisError):
            analyzer.analyze("some transcript", config.default_profile(), 1.0)

    def test_authentication_error_is_mapped(self) -> None:
        request = _fake_request()
        response = httpx2.Response(401, request=request)
        error = __import__("anthropic").AuthenticationError("bad key", response=response, body=None)
        analyzer, _ = make_analyzer(error=error)
        with self.assertRaises(AnalysisError) as ctx:
            analyzer.analyze("some transcript", config.default_profile(), 1.0)
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))

    def test_rate_limit_error_is_mapped(self) -> None:
        request = _fake_request()
        response = httpx2.Response(429, request=request, headers={"retry-after": "30"})
        error = __import__("anthropic").RateLimitError("rate limited", response=response, body=None)
        analyzer, _ = make_analyzer(error=error)
        with self.assertRaises(AnalysisError) as ctx:
            analyzer.analyze("some transcript", config.default_profile(), 1.0)
        self.assertIn("30", str(ctx.exception))

    def test_connection_error_is_mapped(self) -> None:
        request = _fake_request()
        error = __import__("anthropic").APIConnectionError(request=request)
        analyzer, _ = make_analyzer(error=error)
        with self.assertRaises(AnalysisError) as ctx:
            analyzer.analyze("some transcript", config.default_profile(), 1.0)
        self.assertIn("интернет", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
