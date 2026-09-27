"""Offline tests for «Спросить ИИ»: app.assistant, app.chat_store and the
/api/assistant routes (fake Claude, temp data dir)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Sequence
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import api, assistant, config, learner_store  # noqa: E402
from app.analyzer import AnalysisError, MissingAnthropicApiKeyError  # noqa: E402
from app.assistant import Assistant, ReplyResult  # noqa: E402
from app.exercise_sets import ClaudeCall  # noqa: E402
from app.server import app as fastapi_app  # noqa: E402


class FakeMessages:
    def __init__(self, content: List[Any], stop_reason: str = "end_turn") -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=self.content,
            _request_id="req-fake",
            usage=SimpleNamespace(input_tokens=900, output_tokens=300,
                                  cache_creation_input_tokens=None, cache_read_input_tokens=0),
        )


def assistant_with(content: List[Any], stop_reason: str = "end_turn") -> tuple:
    messages = FakeMessages(content, stop_reason)
    client = SimpleNamespace(messages=messages)
    return Assistant("test-key", client_factory=lambda key: client), messages


CHAT = {
    "selection": "so that this kind of mistake doesn't happen again",
    "context": "Грамматика\nso that this kind of mistake doesn't happen again\n"
               "Вы написали: таким образом подобные ошибки больше не происходят",
    "screen": {"title": "Перевод текста", "route": "#/translate/txt-1"},
    "messages": [
        {"role": "user", "text": "Почему прошедшее время?"},
        {"role": "assistant", "text": "Это правило русского языка."},
        {"role": "user", "text": "А в английском?"},
    ],
}


class AssistantTests(unittest.TestCase):
    def test_the_first_turn_carries_the_screen_card_and_selection(self) -> None:
        turns = assistant.api_messages(CHAT)
        self.assertEqual([t["role"] for t in turns], ["user", "assistant", "user"])
        first = turns[0]["content"]
        self.assertIn("Screen: Перевод текста", first)
        self.assertIn("Вы написали: таким образом", first)
        self.assertIn("Selected: «so that this kind", first)
        self.assertTrue(first.endswith("Question: Почему прошедшее время?"))
        self.assertEqual(turns[2]["content"], "А в английском?")

    def test_a_card_that_is_only_the_selection_is_not_repeated(self) -> None:
        text = assistant.opening_message(
            question="Что это?", selection="roll out", context="roll out", screen=""
        )
        self.assertNotIn("The card around", text)
        self.assertIn("Screen: unknown", text)

    def test_reply_asks_sonnet_and_joins_the_text_blocks(self) -> None:
        bot, messages = assistant_with([
            SimpleNamespace(type="thinking", thinking="..."),
            SimpleNamespace(type="text", text=" После «чтобы» — "),
            SimpleNamespace(type="text", text="форма прошедшего времени. "),
        ])
        result = bot.reply(assistant.api_messages(CHAT))
        self.assertEqual(result.text, "После «чтобы» — форма прошедшего времени.")
        self.assertEqual(result.call.model, config.ASSISTANT_MODEL)
        self.assertEqual(result.call.request_id, "req-fake")
        call = messages.calls[0]
        self.assertEqual(call["model"], config.ASSISTANT_MODEL)
        self.assertEqual(call["output_config"], {"effort": config.ASSISTANT_EFFORT})
        self.assertIn("Answer in Russian", call["system"])
        self.assertEqual(len(call["messages"]), 3)

    def test_an_empty_answer_or_a_refusal_is_an_error(self) -> None:
        bot, _ = assistant_with([])
        with self.assertRaises(AnalysisError):
            bot.reply([{"role": "user", "content": "?"}])
        bot, _ = assistant_with([SimpleNamespace(type="text", text="x")], "refusal")
        with self.assertRaises(AnalysisError):
            bot.reply([{"role": "user", "content": "?"}])

    def test_no_key_means_no_call(self) -> None:
        with self.assertRaises(MissingAnthropicApiKeyError):
            Assistant("").reply([{"role": "user", "content": "?"}])


class FakeAssistant:
    def __init__(self) -> None:
        self.calls: List[Sequence[Dict[str, str]]] = []
        self.error: Exception | None = None

    def reply(self, messages: Sequence[Dict[str, str]]) -> ReplyResult:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return ReplyResult(
            text=f"Ответ {len(self.calls)}",
            call=ClaudeCall(model=config.ASSISTANT_MODEL,
                            usage={"input_tokens": 1000, "output_tokens": 400},
                            request_id="req-1", effort="low"),
        )


class AssistantApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        base_dir_patch.start()
        self.addCleanup(base_dir_patch.stop)
        self.fake = FakeAssistant()
        fastapi_app.dependency_overrides[api.get_assistant_factory] = lambda: (lambda: self.fake)
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.client = TestClient(fastapi_app)

    def _create(self) -> Dict[str, Any]:
        response = self.client.post("/api/assistant/chats", json={
            "question": " Почему прошедшее время? ",
            "selection": CHAT["selection"],
            "context": CHAT["context"],
            "screen": CHAT["screen"],
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_a_chat_is_stored_free_then_answered_on_reply(self) -> None:
        chat = self._create()
        self.assertTrue(chat["awaits_answer"])
        self.assertEqual(chat["messages"][0]["text"], "Почему прошедшее время?")
        self.assertEqual(self.fake.calls, [])

        answered = self.client.post(f"/api/assistant/chats/{chat['id']}/reply").json()
        self.assertFalse(answered["awaits_answer"])
        self.assertEqual(answered["messages"][-1]["role"], "assistant")
        self.assertEqual(answered["messages"][-1]["text"], "Ответ 1")
        self.assertIsNotNone(answered["messages"][-1]["cost_usd"])
        self.assertIn("Selected: «so that", self.fake.calls[0][0]["content"])
        usage = learner_store.load_usage()
        self.assertEqual([u["purpose"] for u in usage], ["assistant"])
        self.assertEqual(usage[0]["session_id"], chat["id"])

        # A second click does not pay again.
        again = self.client.post(f"/api/assistant/chats/{chat['id']}/reply").json()
        self.assertEqual(len(again["messages"]), 2)
        self.assertEqual(len(self.fake.calls), 1)

    def test_a_follow_up_sends_the_whole_chat(self) -> None:
        chat = self._create()
        self.client.post(f"/api/assistant/chats/{chat['id']}/reply")
        added = self.client.post(
            f"/api/assistant/chats/{chat['id']}/messages", json={"text": "А в английском?"}
        )
        self.assertEqual(added.status_code, 201)
        self.client.post(f"/api/assistant/chats/{chat['id']}/reply")
        roles = [t["role"] for t in self.fake.calls[1]]
        self.assertEqual(roles, ["user", "assistant", "user"])
        listed = self.client.get("/api/assistant/chats").json()
        self.assertEqual(len(listed["chats"]), 1)
        self.assertEqual(listed["chats"][0]["messages"], 4)
        self.assertTrue(listed["chats"][0]["title"].startswith("so that"))

    def test_a_question_waits_for_its_answer(self) -> None:
        chat = self._create()
        response = self.client.post(
            f"/api/assistant/chats/{chat['id']}/messages", json={"text": "Ещё?"}
        )
        self.assertEqual(response.status_code, 409)

    def test_a_failed_answer_keeps_the_question_for_a_retry(self) -> None:
        chat = self._create()
        self.fake.error = AnalysisError("Claude API не ответил вовремя.")
        response = self.client.post(f"/api/assistant/chats/{chat['id']}/reply")
        self.assertEqual(response.status_code, 502)
        stored = self.client.get(f"/api/assistant/chats/{chat['id']}").json()
        self.assertTrue(stored["awaits_answer"])
        self.fake.error = MissingAnthropicApiKeyError("Нет ключа.")
        self.assertEqual(
            self.client.post(f"/api/assistant/chats/{chat['id']}/reply").status_code, 400
        )
        self.fake.error = None
        retried = self.client.post(f"/api/assistant/chats/{chat['id']}/reply").json()
        self.assertFalse(retried["awaits_answer"])

    def test_an_empty_question_is_refused(self) -> None:
        response = self.client.post("/api/assistant/chats", json={"question": "  "})
        self.assertEqual(response.status_code, 400)

    def test_a_long_chat_takes_no_more_questions(self) -> None:
        chat = self._create()
        with patch.object(config, "ASSISTANT_MAX_MESSAGES", 2):
            self.client.post(f"/api/assistant/chats/{chat['id']}/reply")
            response = self.client.post(
                f"/api/assistant/chats/{chat['id']}/messages", json={"text": "Ещё?"}
            )
        self.assertEqual(response.status_code, 400)

    def test_ids_from_the_url_are_checked(self) -> None:
        self.assertEqual(self.client.get("/api/assistant/chats/..secret").status_code, 400)
        self.assertEqual(self.client.get("/api/assistant/chats/chat-x").status_code, 400)
        self.assertEqual(
            self.client.get("/api/assistant/chats/chat-20260927-120000").status_code, 404
        )


if __name__ == "__main__":
    unittest.main()
