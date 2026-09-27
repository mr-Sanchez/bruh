"""Claude layer of the dictation's translation task (two calls in all).

After a lesson has been dictated the learner translates it, a part at a time
(English -> Russian, or the other way round for a Russian video). Two explicit
clicks spend money:

  * `split`   - one Haiku call per lesson: which sentences open a new part, so that
                each part is one train of thought. Only the numbered sentences
                go in and a list of numbers comes out; `dictation.plan_parts`
                repairs the answer. A lesson that fits in one part needs no
                call at all.
  * `review`  - one Sonnet call per submitted translation: what is wrong, why, and how
                to say it better. There is no reference translation to pay for
                first - the model judges the learner's text against the source.

Same shape as app/exercise_sets.py: structured outputs through Pydantic
schemas, everything the learner reads as explanation in Russian, and a
`client_factory` so tests never touch the network. Nothing here runs on its
own; api.py calls it on a click and dictation_store keeps the result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app import config
from app.analyzer import AnalysisError, MissingAnthropicApiKeyError, friendly_api_error
from app.exercise_sets import ClaudeCall, ClientFactory, _call_info, _default_client_factory

logger = logging.getLogger(__name__)

Quality = Literal["good", "fair", "weak"]


# ------------------------------------------------------------------ schemas
class PartStarts(BaseModel):
    # Index of the first sentence of every part except the first one.
    starts: List[int] = Field(default_factory=list)


class TranslationIssue(BaseModel):
    source: str  # the fragment of the original this is about
    quote: str  # the learner's own words, verbatim; empty when they left it out
    problem: str
    better: str


class TranslationReview(BaseModel):
    quality: Quality
    summary: str
    issues: List[TranslationIssue] = Field(default_factory=list)
    model_translation: str


@dataclass
class SplitResult:
    starts: List[int]
    call: ClaudeCall


@dataclass
class ReviewResult:
    review: Dict[str, Any]
    call: ClaudeCall


# ------------------------------------------------------------------ prompts
SPLIT_PROMPT = """You cut the transcript of a video into parts for a \
translation exercise. You get the sentences, numbered from 0.

Return `starts`: the number of the first sentence of every part except the \
first (never 0), in increasing order. Each part should be one train of \
thought - a topic, a story beat, a question and its answer - and hold \
{minimum}-{maximum} sentences, ideally {ideal}. Cut where the subject changes, \
never in the middle of an example or an argument. A transcript that fits one \
part gets an empty list."""

REVIEW_PROMPT = """You review a Russian-speaking learner's translation of a \
passage of a video. The learner listened to the {source} original, typed it \
out, and then translated it into {target}. The original is given as numbered \
sentences; the translation is free text.

- `quality`: "good" when it conveys the meaning fully and reads naturally; \
"fair" when the meaning is there but with inaccuracies or awkward wording; \
"weak" when parts are missing, wrong or unrelated. Other wording than yours is \
fine - judge the meaning and the naturalness, not word-for-word equivalence.
- `summary`: two or three Russian sentences: the overall impression, and what \
to work on first.
- `issues`: the mistakes and omissions that matter, most important first, at \
most 8 (none when the translation is good). For each: `source` - the fragment \
of the original; `quote` - the learner's words, copied exactly (empty when \
they left the fragment out); `problem` - one Russian sentence on what is \
wrong (a mistranslation, an omission, an added meaning, unnatural {target}, a \
false friend); `better` - how to say it in {target}.
- `model_translation`: your own natural {target} translation of the whole \
passage.
- All explanations are in Russian; `quote`, `better` and `model_translation` \
are in {target}. Do not nitpick punctuation or capitalisation."""

_LANGUAGE_NAMES = {"en": "English", "ru": "Russian"}


def translation_languages(subtitle_language: Optional[str]) -> Tuple[str, str]:
    """(source, target) language names for a lesson: English is translated
    into Russian and anything else (Russian) into English."""
    source = _LANGUAGE_NAMES.get((subtitle_language or "en").split("-")[0].lower(), "English")
    return source, ("Russian" if source == "English" else "English")


def numbered(sentences: Sequence[str], offset: int = 0) -> str:
    return "\n".join(f"{offset + n}: {text}" for n, text in enumerate(sentences))


# ---------------------------------------------------------------- translator
class LessonTranslator:
    """Splits lessons and reviews translations; one Claude call per method."""

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

    def split(self, sentences: Sequence[str]) -> SplitResult:
        """The sentence indices where new parts begin."""
        system = SPLIT_PROMPT.format(
            minimum=config.TRANSLATION_PART_MIN_SENTENCES,
            maximum=config.TRANSLATION_PART_MAX_SENTENCES,
            ideal="8-10",
        )
        logger.info("Lesson split requested: %d sentences", len(sentences))
        response = self._call(
            model=config.TRANSLATION_SPLIT_MODEL,
            max_tokens=config.TRANSLATION_SPLIT_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": numbered(sentences)}],
            output_format=PartStarts,
        )
        return SplitResult(
            starts=list(response.parsed_output.starts),
            call=_call_info(response, config.TRANSLATION_SPLIT_MODEL),
        )

    def review(
        self,
        sentences: Sequence[str],
        translation: str,
        *,
        subtitle_language: Optional[str] = "en",
    ) -> ReviewResult:
        """Review one part's translation against its source sentences."""
        source, target = translation_languages(subtitle_language)
        request = (
            f"Original ({source}):\n{numbered(sentences, offset=1)}\n\n"
            f"Learner's translation ({target}):\n{translation.strip()}"
        )
        logger.info("Translation review requested: %d sentences", len(sentences))
        response = self._call(
            model=config.TRANSLATION_REVIEW_MODEL,
            max_tokens=config.TRANSLATION_REVIEW_MAX_TOKENS,
            system=REVIEW_PROMPT.format(source=source, target=target),
            messages=[{"role": "user", "content": request}],
            output_format=TranslationReview,
            output_config={"effort": config.TRANSLATION_REVIEW_EFFORT},
            thinking={"type": "adaptive"},
        )
        parsed: TranslationReview = response.parsed_output
        review = {
            "quality": parsed.quality,
            "summary": parsed.summary.strip(),
            "issues": [
                {key: value.strip() for key, value in issue.model_dump().items()}
                for issue in parsed.issues
                if issue.problem.strip()
            ],
            "model_translation": parsed.model_translation.strip(),
        }
        return ReviewResult(
            review=review,
            call=_call_info(
                response, config.TRANSLATION_REVIEW_MODEL, config.TRANSLATION_REVIEW_EFFORT
            ),
        )

    # -------------------------------------------------------------- helpers
    def _call(self, **kwargs: Any) -> Any:
        if not self._api_key:
            raise MissingAnthropicApiKeyError(config.MISSING_ANTHROPIC_API_KEY_MESSAGE)
        try:
            client = self._client_factory(self._api_key)
            response = client.messages.parse(timeout=self._timeout_seconds, **kwargs)
        except Exception as exc:
            raise friendly_api_error(
                exc, "ИИ ассистент не ответил вовремя. Попробуйте ещё раз."
            ) from exc
        if getattr(response, "stop_reason", None) == "refusal":
            raise AnalysisError("ИИ ассистент отказался выполнить запрос. Попробуйте ещё раз.")
        if getattr(response, "parsed_output", None) is None:
            raise AnalysisError("ИИ ассистент вернул ответ, который не удалось разобрать.")
        return response
