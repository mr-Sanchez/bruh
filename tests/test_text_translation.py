"""Offline tests for «Перевод текста»: app.text_translation, app.text_store
and the /api/translate routes (fake Claude, fake Deepgram, temp data dir)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import api, config, text_store, text_translation  # noqa: E402
from app.analyzer import AnalysisError, MissingAnthropicApiKeyError  # noqa: E402
from app.exercise_sets import ClaudeCall, VocabularyItem  # noqa: E402
from app.server import app as fastapi_app  # noqa: E402
from app.text_translation import (  # noqa: E402
    ReviewResult,
    TextMistake,
    TextReview,
    TextTranslator,
    UnnaturalSpot,
    WriteResult,
    WrittenText,
    minutes_for,
)
from tests.test_api import FakeTranscriber  # noqa: E402


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
            usage=SimpleNamespace(input_tokens=1500, output_tokens=2000,
                                  cache_creation_input_tokens=None, cache_read_input_tokens=0),
        )


def translator_with(parsed: Any) -> tuple:
    messages = FakeMessages(parsed)
    client = SimpleNamespace(messages=messages)
    return TextTranslator("test-key", client_factory=lambda key: client), messages


REVIEW = TextReview(
    accuracy=140,  # clamped to 100
    summary=" Смысл передан, есть неточности. ",
    mistakes=[
        TextMistake(source="roll out", quote="катить", kind="meaning",
                    problem="Здесь это «выпустить».", correction="выпустить"),
        TextMistake(source="x", quote="y", kind="grammar", problem="  ", correction="z"),
    ],
    unnatural=[
        UnnaturalSpot(quote="сделать решение", why="Калька.", suggestion="принять решение"),
        UnnaturalSpot(quote="", why="нет цитаты", suggestion="что-то"),
    ],
    final_translation="Мы выпустим обновление в пятницу.",
    phrases=[
        VocabularyItem(english="roll out", russian="выпустить, внедрить",
                       example="We will roll out the update.", example_russian="Мы выпустим."),
        VocabularyItem(english="have + V3", russian="формула", example="", example_russian=""),
        VocabularyItem(english="Roll out", russian="повтор", example="", example_russian=""),
    ],
)


class TextTranslatorTests(unittest.TestCase):
    def test_write_text_asks_sonnet_for_the_size_level_and_context(self) -> None:
        translator, messages = translator_with(
            WrittenText(title=" Релиз в пятницу ", text=" We are rolling out... ",
                        gist=" a release on Friday ")
        )
        result = translator.write_text(
            size="short", level="b2", theme={"key": None, "label": "gardening"}, genre="email",
            recent=[{"gist": "tomatoes died in the frost", "genre": "story",
                     "opening": "Last night the frost"}],
        )
        call = messages.calls[0]
        self.assertEqual(call["model"], config.TEXT_WRITE_MODEL)
        self.assertEqual(call["output_config"], {"effort": config.TEXT_WRITE_EFFORT})
        self.assertIn("80-110 words", call["system"])
        request = call["messages"][0]["content"]
        self.assertIn("gardening", request)
        self.assertIn("B2", request)
        self.assertIn(f"Genre: {text_translation.GENRES['email'][0]}", request)
        self.assertIn('- tomatoes died in the frost; opened "Last night the frost..."', request)
        self.assertEqual((result.title, result.text), ("Релиз в пятницу", "We are rolling out..."))
        self.assertEqual(result.gist, "a release on Friday")

    def test_the_genre_is_a_new_one_else_the_longest_unused(self) -> None:
        pick = text_translation.pick_genre
        everything = list(text_translation.GENRES)
        self.assertNotIn(pick(["story", None, "email"]), {"story", "email"})
        # All used: the one whose last use is the oldest.
        newest_first = ["chat"] + everything[::-1]
        self.assertEqual(pick(newest_first), everything[0])

    def test_an_empty_text_is_an_error(self) -> None:
        translator, _ = translator_with(WrittenText(title="x", text="  ", gist=""))
        with self.assertRaises(AnalysisError):
            translator.write_text(size="medium", level="b1")

    def test_review_cleans_the_answer_and_numbers_the_phrases(self) -> None:
        translator, messages = translator_with(REVIEW)
        result = translator.review("We will roll out.", " Мы катим. ", phrase_prefix="r2-")
        call = messages.calls[0]
        self.assertEqual(call["model"], config.TEXT_REVIEW_MODEL)
        self.assertIn("Learner's translation (Russian):\nМы катим.", call["messages"][0]["content"])
        self.assertIn("into Russian", call["system"])
        self.assertEqual(result.review["accuracy"], 100)
        self.assertEqual(result.review["summary"], "Смысл передан, есть неточности.")
        self.assertEqual([m["quote"] for m in result.review["mistakes"]], ["катить"])
        self.assertEqual([u["quote"] for u in result.review["unnatural"]], ["сделать решение"])
        # The formula and the repeat are dropped.
        self.assertEqual([(p["id"], p["english"]) for p in result.phrases], [("r2-1", "roll out")])

    def test_no_key_means_no_call(self) -> None:
        with self.assertRaises(MissingAnthropicApiKeyError):
            TextTranslator("").review("text", "перевод")

    def test_minutes_follow_the_word_count(self) -> None:
        self.assertEqual(minutes_for("word " * 180), 10)
        self.assertEqual(minutes_for("one"), 1)


class FakeTextTranslator:
    has_api_key = True

    def __init__(self) -> None:
        self.reviews = 0
        self.fail: Optional[Exception] = None
        self.last_write: Dict[str, Any] = {}
        self.known_phrases: List[List[str]] = []

    def write_text(self, **kwargs: Any) -> WriteResult:
        self.last_write = kwargs
        return WriteResult(
            gist="rolling out an update on Friday",
            title="Релиз в пятницу",
            text="We are rolling out the update on Friday.\n\nPlease reach out if anything breaks.",
            call=ClaudeCall(model=config.TEXT_WRITE_MODEL, usage={"input_tokens": 500,
                                                                   "output_tokens": 800}),
        )

    def review(self, text: str, translation: str, *, direction: str,
               phrase_prefix: str, known_phrases=()) -> ReviewResult:
        if self.fail:
            raise self.fail
        self.known_phrases.append(list(known_phrases))
        self.reviews += 1
        phrases = [
            {"id": f"{phrase_prefix}1", "english": "roll out", "russian": "выпустить",
             "example": "", "example_russian": "", "note": ""},
            {"id": f"{phrase_prefix}2", "english": "reach out", "russian": "связаться",
             "example": "", "example_russian": "", "note": ""},
        ]
        return ReviewResult(
            review={"accuracy": 80, "summary": "Хорошо.", "mistakes": [], "unnatural": [],
                    "final_translation": "Мы выпускаем обновление в пятницу."},
            phrases=phrases,
            call=ClaudeCall(model=config.TEXT_REVIEW_MODEL, usage={"input_tokens": 1500,
                                                                    "output_tokens": 2000}),
        )


class TranslateApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        base_dir_patch.start()
        self.addCleanup(base_dir_patch.stop)
        self.translator = FakeTextTranslator()
        fastapi_app.dependency_overrides[api.get_text_translator_factory] = lambda: (
            lambda: self.translator
        )
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.client = TestClient(fastapi_app)

    def _generate(self) -> Dict[str, Any]:
        response = self.client.post(
            "/api/translate/texts", json={"size": "short", "level": "b2"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_a_generated_text_is_stored_with_its_cost_and_listed(self) -> None:
        text = self._generate()
        self.assertRegex(text["id"], text_store.ID_PATTERN)
        self.assertEqual((text["origin"], text["size"], text["level"]), ("generated", "short", "b2"))
        self.assertEqual(self.translator.last_write["size"], "short")
        self.assertIsNotNone(text["theme"])  # the default context
        self.assertGreater(text["generation"]["usage"]["cost_usd"], 0)
        listed = self.client.get("/api/translate/texts").json()
        self.assertEqual([t["id"] for t in listed["texts"]], [text["id"]])
        self.assertEqual([s["key"] for s in listed["sizes"]], ["short", "medium", "long"])
        usage = self.client.get("/api/usage").json()["by_purpose"]
        self.assertEqual(usage["anthropic:translate_text"]["calls"], 1)

    def test_a_second_text_in_the_context_avoids_the_first_plot_and_genre(self) -> None:
        first = self._generate()
        self.assertIn(first["genre"], text_translation.GENRES)
        self.assertEqual(first["gist"], "rolling out an update on Friday")
        self.assertEqual(self.translator.last_write["recent"], [])
        self._generate()
        (recent,) = self.translator.last_write["recent"]
        self.assertEqual(recent, {"gist": "rolling out an update on Friday",
                                  "genre": first["genre"], "opening": "We are rolling out"})
        self.assertNotEqual(self.translator.last_write["genre"], first["genre"])
        listed = {t["id"]: t for t in self.client.get("/api/translate/texts").json()["texts"]}
        self.assertEqual(listed[first["id"]]["genre"], first["genre"])

    def test_a_text_from_before_gists_offers_its_title_and_first_sentence(self) -> None:
        text_store.save_text({
            "id": "txt-20260101-000000", "origin": "generated", "title": "Долгий баг",
            "theme": {"key": "it_backend"}, "attempts": [],
            "text": "Yesterday our payment service went down. Nobody noticed.",
        })
        (recent,) = text_store.recent_texts({"key": "it_backend", "label": "IT"})
        self.assertEqual(recent["gist"], "Долгий баг: Yesterday our payment service went down.")
        self.assertIsNone(recent["genre"])

    def test_a_custom_text_is_stored_as_pasted_for_free(self) -> None:
        pasted = "  The quick brown fox jumps over the lazy dog, twice a day.  "
        response = self.client.post("/api/translate/texts/custom", json={"text": pasted})
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["text"], pasted.strip())
        self.assertEqual(body["title"], "The quick brown fox jumps over…")
        self.assertEqual(body["origin"], "custom")
        self.assertEqual(self.client.get("/api/usage").json()["by_purpose"], {})
        short = self.client.post("/api/translate/texts/custom", json={"text": "Hi"})
        self.assertEqual(short.status_code, 400)

    def test_review_is_stored_and_the_same_translation_is_not_paid_twice(self) -> None:
        text_id = self._generate()["id"]
        url = f"/api/translate/texts/{text_id}/review"
        first = self.client.post(url, json={"translation": " Мы выпускаем обновление. "})
        self.assertEqual(first.status_code, 201, first.text)
        body = first.json()
        self.assertFalse(body["cached"])
        attempt = body["attempts"][-1]
        self.assertEqual((attempt["n"], attempt["text"]), (1, "Мы выпускаем обновление."))
        self.assertEqual(attempt["review"]["accuracy"], 80)
        self.assertEqual([p["id"] for p in attempt["phrases"]], ["r1-1", "r1-2"])

        again = self.client.post(url, json={"translation": "Мы выпускаем обновление."}).json()
        self.assertTrue(again["cached"])
        self.assertEqual(self.translator.reviews, 1)

        other = self.client.post(url, json={"translation": "Другой перевод."}).json()
        self.assertEqual([p["id"] for p in other["attempts"][-1]["phrases"]], ["r2-1", "r2-2"])
        self.assertEqual(self.translator.reviews, 2)
        self.assertEqual(
            self.client.post(url, json={"translation": "  "}).status_code, 400
        )

    def test_a_failed_review_keeps_the_translation(self) -> None:
        text_id = self._generate()["id"]
        self.translator.fail = MissingAnthropicApiKeyError("Нет ключа.")
        response = self.client.post(
            f"/api/translate/texts/{text_id}/review", json={"translation": "Мой перевод."}
        )
        self.assertEqual(response.status_code, 400)
        stored = self.client.get(f"/api/translate/texts/{text_id}").json()
        self.assertEqual(stored["attempts"][-1]["text"], "Мой перевод.")
        self.assertIsNone(stored["attempts"][-1]["review"])

    def test_picked_phrases_become_word_cards(self) -> None:
        text_id = self._generate()["id"]
        review_url = f"/api/translate/texts/{text_id}/review"
        self.client.post(review_url, json={"translation": "Первый."})
        self.client.post(review_url, json={"translation": "Второй."})
        url = f"/api/translate/texts/{text_id}/phrases"

        saved = self.client.put(url, json={"picked": ["r1-1", "r2-2"]}).json()
        self.assertEqual((saved["added"], saved["removed"]), (2, 0))
        words = self.client.get("/api/learner/items?kind=word").json()["items"]
        self.assertEqual(sorted(w["content"]["english"] for w in words), ["reach out", "roll out"])
        self.assertEqual(
            self.client.get(f"/api/translate/texts/{text_id}").json()["picked"], ["r1-1", "r2-2"]
        )

        saved = self.client.put(url, json={"picked": ["r1-1"]}).json()
        self.assertEqual(saved["removed"], 1)
        bad = self.client.put(url, json={"picked": ["r9-9"]})
        self.assertEqual(bad.status_code, 400)

        # The next review is told which cards are in the text ("rolling out"),
        # and a suggestion that repeats a card is dropped, the rest renumbered.
        third = self.client.post(review_url, json={"translation": "Третий."}).json()
        self.assertEqual(self.translator.known_phrases[-1], ["roll out"])
        phrases = third["attempts"][-1]["phrases"]
        self.assertEqual([(p["id"], p["english"]) for p in phrases], [("r3-1", "reach out")])
        # An earlier review's phrase that is not a card here but was picked
        # elsewhere would say so; this one's own pick carries no hint.
        first = third["attempts"][0]["phrases"]
        self.assertNotIn("similar", first[0])
        self.assertNotIn("similar", first[1])

    def test_ids_from_the_url_are_checked(self) -> None:
        self.assertEqual(self.client.get("/api/translate/texts/..%5Cx").status_code, 400)
        self.assertEqual(
            self.client.get("/api/translate/texts/txt-20260101-000000").status_code, 404
        )

    def test_a_translation_can_be_dictated_in_russian(self) -> None:
        profiles = []
        transcriber = FakeTranscriber(transcript=" Мы выпускаем обновление в пятницу ")
        fastapi_app.dependency_overrides[api.get_transcriber_factory] = lambda: (
            lambda profile: profiles.append(profile) or transcriber
        )
        response = self.client.post(
            "/api/learner/dictate",
            files={"audio": ("a.webm", b"webm-bytes", "audio/webm")},
            data={"duration_seconds": "4", "language": "ru"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["text"], "Мы выпускаем обновление в пятницу")
        self.assertEqual(profiles[0].language, "ru")
        usage = self.client.get("/api/usage").json()["by_purpose"]
        self.assertEqual(usage["deepgram:text_dictation"]["calls"], 1)


if __name__ == "__main__":
    unittest.main()
