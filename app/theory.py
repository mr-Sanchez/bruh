"""Claude writes a roadmap lesson's theory (Stage 8, R4).

The roadmap is fixed (app/curriculum.py); the model only writes a lesson's
content, on a click. Theory is one structured answer: the rule in plain
Russian, sections with neutral English examples (with translations), the
mistakes Russian speakers typically make on it, a comment on the learner's
OWN mistakes on the topic (from their recordings), and what to remember.

Decided 2026-09-26: theory does not follow the context «уклон» - examples
are neutral, everyday and work life. It is paid for, so app.learner_store
keeps every version; nothing here runs on its own.

Same shape as app/exercise_sets.py: Pydantic schemas for structured output,
Russian for everything explanatory, a `client_factory` for offline tests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from app import config
from app.analyzer import AnalysisError, MissingAnthropicApiKeyError, friendly_api_error
from app.exercise_sets import ClaudeCall, _call_info

logger = logging.getLogger(__name__)

ClientFactory = Callable[[str], Any]

# The learner's level in plain words, by roadmap level key.
_LEVEL_WORDS: Dict[str, str] = {
    "a2": "A2 (elementary): short sentences, the most common words",
    "b1": "B1 (intermediate): everyday and work situations, simple explanations",
    "b2": "B2 (upper-intermediate): precise, natural, with nuances",
    "c1": "C1 (advanced): subtle distinctions, style and register",
}


# ------------------------------------------------------------------ schemas
class TheoryExample(BaseModel):
    english: str
    russian: str


class TheorySection(BaseModel):
    heading: str
    text: str
    examples: List[TheoryExample] = Field(default_factory=list)


class TypicalMistake(BaseModel):
    wrong: str
    right: str
    why: str


class OwnMistake(BaseModel):
    said: str
    correct: str
    comment: str


class LessonTheory(BaseModel):
    summary: str
    sections: List[TheorySection] = Field(default_factory=list)
    typical_mistakes: List[TypicalMistake] = Field(default_factory=list)
    own_mistakes: List[OwnMistake] = Field(default_factory=list)
    remember: List[str] = Field(default_factory=list)


@dataclass
class TheoryResult:
    content: Dict[str, Any]  # LessonTheory as a dict
    call: ClaudeCall


# ------------------------------------------------------------------- prompt
THEORY_PROMPT = """You are a clear, practical English teacher writing the \
theory page of ONE lesson for an adult Russian speaker (a backend developer) \
who learns spoken English. The lesson teaches exactly one topic; stay on it.

Write:
- `summary`: 1-2 Russian sentences - the rule in the plainest words, what \
the learner will be able to do after the lesson.
- `sections`: 2-5 sections that build the topic step by step (form, when to \
use, contrasts with what Russian speakers confuse it with, useful fixed \
phrases). `heading`: short Russian. `text`: Russian, 2-5 short sentences, \
concrete, no linguistic jargon without an explanation; formulas like \
"have + V3" are welcome. `examples`: 2-4 short natural English sentences \
with a Russian translation each.
- `typical_mistakes`: 3-5 mistakes Russian speakers really make on this \
topic: `wrong` (the English mistake), `right` (the fix), `why` (one Russian \
sentence).
- `own_mistakes`: one entry per mistake of the learner's own that you are \
given (in the given order, none when none are given): `said` - their words \
exactly as given, `correct` - the correction, `comment` - 1-2 Russian \
sentences tying it to this lesson's rule. Leave out one that is not really \
about this topic.
- `remember`: 3-5 short points to remember (Russian, English examples inline).

Rules:
- Match the level you are given: simple for A2, more nuance for B2-C1.
- Examples are neutral: everyday life and work, varied, natural spoken \
English, 5-14 words. Do not build them around the learner's own sentences.
- Everything explanatory is Russian; examples and forms are English."""


def _default_client_factory(api_key: str) -> Any:
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


class TheoryWriter:
    """One Claude call per lesson theory."""

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

    def write(
        self,
        topic: Dict[str, Any],
        level: Optional[str],
        own_mistakes: Sequence[Dict[str, str]] = (),
    ) -> TheoryResult:
        """Theory for `topic` (curriculum.topic_info) at roadmap `level`;
        `own_mistakes` are [{"said", "correct"}] from the learner's speech."""
        if not self._api_key:
            raise MissingAnthropicApiKeyError(config.MISSING_ANTHROPIC_API_KEY_MESSAGE)
        logger.info("Theory requested: topic=%s, own mistakes=%d", topic["key"], len(own_mistakes))
        try:
            client = self._client_factory(self._api_key)
            response = client.messages.parse(
                model=config.THEORY_MODEL,
                max_tokens=config.THEORY_MAX_TOKENS,
                system=THEORY_PROMPT,
                messages=[{"role": "user", "content": theory_request(topic, level, own_mistakes)}],
                output_format=LessonTheory,
                output_config={"effort": config.THEORY_EFFORT},
                thinking={"type": "adaptive"},
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            raise friendly_api_error(
                exc, "Claude API не ответил вовремя. Попробуйте ещё раз."
            ) from exc
        if getattr(response, "stop_reason", None) == "refusal":
            raise AnalysisError("Claude отказался выполнить запрос. Попробуйте ещё раз.")
        parsed: Optional[LessonTheory] = getattr(response, "parsed_output", None)
        if parsed is None or not (parsed.summary.strip() and parsed.sections):
            raise AnalysisError("Claude вернул пустую теорию. Попробуйте ещё раз.")
        return TheoryResult(
            content=parsed.model_dump(),
            call=_call_info(response, config.THEORY_MODEL, config.THEORY_EFFORT),
        )


def theory_request(
    topic: Dict[str, Any], level: Optional[str], own_mistakes: Sequence[Dict[str, str]]
) -> str:
    lines = [
        f"Lesson topic: {topic['label']} ({topic['key']})",
        f"Mistakes that belong to this topic: {topic.get('description', '')}",
        f"Learner's level: {_LEVEL_WORDS.get(level or '', 'B1 (intermediate)')}",
        "",
    ]
    if own_mistakes:
        lines.append("The learner's own mistakes on this topic (from their recordings):")
        lines += [f'- said: "{m["said"]}" -> correct: "{m["correct"]}"' for m in own_mistakes]
    else:
        lines.append("No recorded mistakes of the learner on this topic yet.")
    lines += ["", "Write the lesson's theory."]
    return "\n".join(lines)


def own_mistakes_from_items(items: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    """The learner's fix items as {"said", "correct"} (pattern items carry no quote)."""
    mistakes = []
    for item in items:
        content = item.get("content") or {}
        if item.get("kind") == "fix" and content.get("quote") and content.get("correction"):
            mistakes.append({"said": content["quote"], "correct": content["correction"]})
    return mistakes
