"""Claude layer of «Спросить ИИ»: a chat about a fragment of any screen.

The learner selects text anywhere in the app - a mistake in a review, a
card, a theory paragraph - and asks about it (the user's brief, 2026-09-27:
"why does it say past tense when the English uses present simple?"). The
chat opens with three things the model cannot see otherwise: the selected
words, the card around them as the screen shows it (its labels included:
«Вы написали», «✓ …», the pill with the mistake kind), and the screen's
title. Everything after that is an ordinary conversation.

One method, one call per answer; the caller keeps the chat on disk
(app/chat_store.py) and sends it whole each time. Plain text out - the
panel renders a small safe subset of Markdown - so `messages.create`, not a
structured output. Same `client_factory` seam as the other Claude layers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from app import config
from app.analyzer import AnalysisError, MissingAnthropicApiKeyError, friendly_api_error
from app.exercise_sets import ClaudeCall, ClientFactory, _call_info, _default_client_factory

logger = logging.getLogger(__name__)

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

SYSTEM_PROMPT = """You are the tutor inside English Coach, an app where an \
adult Russian speaker practises English: monologues and picture \
descriptions reviewed by an AI, translation exercises in both directions \
(English -> Russian texts, Russian -> English cards), dictation from YouTube \
videos, grammar lessons with theory, flashcards of their own mistakes.

The learner selected a fragment of an app screen and asks about it. You get \
the screen's title, the card around the selection as plain text (labels \
such as «Вы написали», «✓» before a correction, a mistake kind like \
«Грамматика» are part of the app's layout), the selected words and the \
question. The card may be the app's own feedback - which can be unclear, \
incomplete or even wrong: say so plainly when it is.

- Answer in Russian. Quote English or Russian examples verbatim; a \
correction is in the language of what it corrects.
- Be precise about which language a rule belongs to: when the card is about \
a Russian translation, say that the rule is Russian grammar, and contrast \
it with English when that is the confusion.
- Short and to the point: a direct answer first, then the why, then two or \
three examples when they help. No introductions, no repeating the question.
- Plain text with light Markdown only: **bold**, *italics*, `code`, lists \
starting with "- ". No headings, tables or links."""


@dataclass
class ReplyResult:
    text: str
    call: ClaudeCall


def opening_message(
    *, question: str, selection: str, context: str, screen: str
) -> str:
    """The first user turn: what the learner sees, then what they ask."""
    parts = [f"Screen: {screen.strip() or 'unknown'}"]
    if context.strip() and context.strip() != selection.strip():
        parts += ["", "The card around the selection, as the screen shows it:", context.strip()]
    parts += ["", f"Selected: «{selection.strip()}»", "", f"Question: {question.strip()}"]
    return "\n".join(parts)


def api_messages(chat: Dict[str, Any]) -> List[Dict[str, str]]:
    """A stored chat as Messages API turns; the first one carries the context."""
    turns: List[Dict[str, str]] = []
    for index, message in enumerate(chat.get("messages") or []):
        text = message.get("text", "")
        if index == 0:
            text = opening_message(
                question=text,
                selection=chat.get("selection", ""),
                context=chat.get("context", ""),
                screen=(chat.get("screen") or {}).get("title", ""),
            )
        turns.append({"role": message.get("role", ROLE_USER), "content": text})
    return turns


class Assistant:
    """Answers the last user turn of a chat; one Claude call per answer."""

    def __init__(
        self,
        api_key: Optional[str],
        *,
        timeout_seconds: int = config.EXERCISE_API_TIMEOUT_SECONDS,
        client_factory: Optional[ClientFactory] = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory or _default_client_factory

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    def reply(self, messages: Sequence[Dict[str, str]]) -> ReplyResult:
        """Claude's answer to `messages` (Messages API turns, the last one the user's)."""
        if not self._api_key:
            raise MissingAnthropicApiKeyError(config.MISSING_ANTHROPIC_API_KEY_MESSAGE)
        logger.info("Assistant answer requested: %d turns", len(messages))
        try:
            client = self._client_factory(self._api_key)
            response = client.messages.create(
                timeout=self._timeout_seconds,
                model=config.ASSISTANT_MODEL,
                max_tokens=config.ASSISTANT_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=list(messages),
                output_config={"effort": config.ASSISTANT_EFFORT},
                thinking={"type": "adaptive"},
            )
        except Exception as exc:
            raise friendly_api_error(
                exc, "Claude API не ответил вовремя. Попробуйте ещё раз."
            ) from exc
        if getattr(response, "stop_reason", None) == "refusal":
            raise AnalysisError("Claude отказался отвечать. Попробуйте переформулировать.")
        text = "".join(
            getattr(block, "text", "")
            for block in getattr(response, "content", None) or []
            if getattr(block, "type", None) == "text"
        ).strip()
        if not text:
            raise AnalysisError("Claude вернул пустой ответ. Попробуйте ещё раз.")
        return ReplyResult(
            text=text,
            call=_call_info(response, config.ASSISTANT_MODEL, config.ASSISTANT_EFFORT),
        )
