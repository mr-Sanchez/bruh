"""Offline tests for app.dictation_translation: the split and review calls."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.analyzer import AnalysisError, MissingAnthropicApiKeyError  # noqa: E402
from app.dictation_translation import (  # noqa: E402
    LessonTranslator,
    PartStarts,
    TranslationIssue,
    TranslationReview,
    translation_languages,
)


class FakeMessages:
    def __init__(self, parsed: Any) -> None:
        self.parsed = parsed
        self.calls: List[Dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=self.parsed,
            _request_id="req-fake",
            usage=SimpleNamespace(input_tokens=900, output_tokens=300,
                                  cache_creation_input_tokens=None, cache_read_input_tokens=0),
        )


def translator_with(parsed: Any) -> tuple:
    messages = FakeMessages(parsed)
    client = SimpleNamespace(messages=messages)
    return LessonTranslator("test-key", client_factory=lambda key: client), messages


REVIEW = TranslationReview(
    quality="fair",
    summary="Смысл передан, но есть неточности.",
    issues=[
        TranslationIssue(source="code review", quote="проверка кода", problem="Устойчивый термин.",
                         better="код-ревью"),
        TranslationIssue(source="x", quote="", problem="  ", better="пустая проблема"),
    ],
    model_translation="Сегодня мы поговорим о код-ревью.",
)


class SplitTests(unittest.TestCase):
    def test_split_sends_numbered_sentences_to_haiku_and_returns_the_starts(self) -> None:
        translator, messages = translator_with(PartStarts(starts=[8, 17]))

        result = translator.split(["First one.", "Second one."])

        call = messages.calls[0]
        self.assertEqual(call["model"], config.TRANSLATION_SPLIT_MODEL)
        self.assertNotIn("output_config", call)  # Haiku takes no effort
        self.assertEqual(call["messages"][0]["content"], "0: First one.\n1: Second one.")
        self.assertIn("5-15 sentences", call["system"])
        self.assertEqual(result.starts, [8, 17])
        self.assertEqual(result.call.usage["output_tokens"], 300)


class ReviewTests(unittest.TestCase):
    def test_review_sends_source_and_translation_and_cleans_the_answer(self) -> None:
        translator, messages = translator_with(REVIEW)

        result = translator.review(
            ["Today we talk about code review."], " Сегодня мы говорим о проверке кода. "
        )

        call = messages.calls[0]
        self.assertEqual(call["model"], config.TRANSLATION_REVIEW_MODEL)
        self.assertEqual(call["output_config"], {"effort": config.TRANSLATION_REVIEW_EFFORT})
        self.assertEqual(result.call.effort, config.TRANSLATION_REVIEW_EFFORT)
        request = call["messages"][0]["content"]
        self.assertIn("Original (English):\n1: Today we talk about code review.", request)
        self.assertIn("translation (Russian):\nСегодня мы говорим о проверке кода.", request)
        self.assertIn("into Russian", messages.calls[0]["system"])
        self.assertEqual(result.review["quality"], "fair")
        # An issue without a problem statement is not shown.
        self.assertEqual(len(result.review["issues"]), 1)
        self.assertEqual(result.review["issues"][0]["better"], "код-ревью")

    def test_a_russian_video_is_translated_into_english(self) -> None:
        self.assertEqual(translation_languages("ru"), ("Russian", "English"))
        self.assertEqual(translation_languages("en"), ("English", "Russian"))
        self.assertEqual(translation_languages(None), ("English", "Russian"))
        translator, messages = translator_with(REVIEW)

        translator.review(["Привет."], "Hello.", subtitle_language="ru")

        self.assertIn("Original (Russian)", messages.calls[0]["messages"][0]["content"])

    def test_no_key_and_unparsable_answers_fail_clearly(self) -> None:
        with self.assertRaises(MissingAnthropicApiKeyError):
            LessonTranslator(None).split(["x"])
        translator, _ = translator_with(None)
        with self.assertRaises(AnalysisError):
            translator.review(["x"], "y")


if __name__ == "__main__":
    unittest.main()
