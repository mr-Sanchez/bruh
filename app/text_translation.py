"""Claude layer of «Перевод текста»: write a text, review its translation.

The learner reads an English text and translates it into Russian (typed or
dictated). The text is either pasted by the learner (free) or written by
Claude in the context («уклон») and at the size they picked - a size is how
long the translation takes, 5-15 minutes (the user's brief, 2026-09-27).
Two explicit clicks spend money, both on Sonnet:

  * `write_text` - one call per generated text: a short Russian title and
                   the text itself, at the learner's level;
  * `review`     - one call per submitted translation: how accurate it is
                   overall, the mistakes that matter, where the Russian sounds
                   unnatural and what to say instead, a final translation,
                   and useful phrases of the text the learner may pick as
                   RU -> EN word cards (the set vocabulary's shape, so the
                   same pick log and card kind serve both).

The direction is kept as data ("en-ru") so the reverse can be added later
without touching the stored texts. Same shape as app/dictation_translation.py:
structured outputs, Russian explanations, a `client_factory` for tests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app import config, themes
from app.analyzer import AnalysisError, MissingAnthropicApiKeyError, friendly_api_error
from app.exercise_sets import (
    _LEVEL_NAMES,
    ClaudeCall,
    ClientFactory,
    VocabularyItem,
    _call_info,
    _default_client_factory,
    clean_vocabulary,
)

logger = logging.getLogger(__name__)

DIRECTION_EN_RU = "en-ru"
# direction -> (source language, target language)
DIRECTIONS: Dict[str, Tuple[str, str]] = {DIRECTION_EN_RU: ("English", "Russian")}
LEVELS: Tuple[str, ...] = ("a2", "b1", "b2", "c1")
DEFAULT_LEVEL = "b1"

MistakeKind = Literal["grammar", "meaning", "omission", "addition", "word_choice", "spelling"]


# ------------------------------------------------------------------ schemas
class WrittenText(BaseModel):
    title: str
    text: str


class TextMistake(BaseModel):
    source: str  # the fragment of the original
    quote: str  # the learner's words, verbatim; empty for an omission
    kind: MistakeKind
    problem: str
    correction: str


class UnnaturalSpot(BaseModel):
    quote: str
    why: str
    suggestion: str


class TextReview(BaseModel):
    accuracy: int  # 0-100; clamped after parsing, not by the schema
    summary: str
    mistakes: List[TextMistake] = Field(default_factory=list)
    unnatural: List[UnnaturalSpot] = Field(default_factory=list)
    final_translation: str
    phrases: List[VocabularyItem] = Field(default_factory=list)


@dataclass
class WriteResult:
    title: str
    text: str
    call: ClaudeCall


@dataclass
class ReviewResult:
    # {"accuracy", "summary", "mistakes", "unnatural", "final_translation"}
    review: Dict[str, Any]
    # Word card candidates, ids `<prefix>1...` (see clean_vocabulary).
    phrases: List[Dict[str, str]]
    call: ClaudeCall


# ------------------------------------------------------------------ prompts
WRITE_PROMPT = """You write a text for a translation exercise. The learner \
is an adult Russian speaker who will read your {source} text and translate \
it into {target}.

- `text`: {min_words}-{max_words} words of natural, authentic {source}, set \
in the situations named on the request's "Context:" line and at the given \
level. Pick a genre that fits the context and vary it between texts: a work \
email or chat message, a short article or blog post, a story of something \
that happened, an explanation of how something works, a review, an \
announcement. Plain paragraphs separated by a blank line; no headings, \
lists, markdown or dialogue scripts.
- Make it worth translating: everyday collocations, a few phrasal verbs and \
set phrases, and a couple of spots a Russian speaker cannot translate word \
for word - but nothing literary, rare or slangy for the level.
- `title`: a short Russian name of the text, 2-6 words.
- Do not write about the subjects of the titles listed as used before."""

REVIEW_PROMPT = """You review a Russian-speaking learner's translation of a \
{source} text into {target}. You get the original and the learner's \
translation (typed or dictated - ignore missing punctuation and \
capitalisation, and do not treat an obvious speech-recognition slip as a \
mistake unless it changes the meaning).

- `accuracy`: 0-100, how fully and correctly the translation conveys the \
original's meaning (100: everything is there and right; 70: the gist with \
several real errors; below 40: much is missing or wrong). Naturalness is not \
part of this number.
- `summary`: two or three Russian sentences: the overall impression, and \
what to work on first.
- `mistakes`: errors that matter, most important first, at most 10: \
`grammar` (case, agreement, aspect, tense, word form in {target}), \
`meaning` (a mistranslation, a false friend, a wrong tense or modality \
carried over), `omission` (something left out), `addition` (a meaning that \
is not in the original), `word_choice` (a word that is wrong here, not just \
another option), `spelling`. For each: `source` - the fragment of the \
original; `quote` - the learner's words copied exactly (empty for an \
omission); `problem` - one or two Russian sentences on what is wrong and \
why; `correction` - the fixed fragment in {target}. Other wording than yours \
is not a mistake; never invent one.
- `unnatural`: places that are correct but sound like a word-for-word \
translation or not like a native {target} speaker, at most 8, none repeating \
a mistake above. `quote` - the learner's words exactly; `why` - one Russian \
sentence; `suggestion` - how a native speaker would say it.
- `final_translation`: the whole text as a good, natural {target} \
translation. Keep the learner's wording wherever it is already right and \
natural; fix everything above.
- `phrases`: exactly {phrases} useful {source} words or phrases from the \
original worth learning as flashcards - collocations, phrasal verbs, set \
phrases, first of all the ones the learner mistranslated, skipped or \
translated clumsily. No very basic words, no grammar formulas. `english`: \
the phrase as it is learned ("reach out to someone"); `russian`: its natural \
{target} equivalent - the front of the card, so it must lead back to this \
phrase; `example`: the original's sentence with it (shorten if long); \
`example_russian`: that sentence in {target}; `note`: optional, one short \
Russian remark (register, a typical mistake), empty when there is nothing \
useful to say.
- Explanations (`summary`, `problem`, `why`, `note`) are Russian."""


def direction_languages(direction: str) -> Tuple[str, str]:
    return DIRECTIONS.get(direction, DIRECTIONS[DIRECTION_EN_RU])


def size_words(size: str) -> Tuple[int, int]:
    return config.TEXT_SIZES.get(size, config.TEXT_SIZES[config.TEXT_DEFAULT_SIZE])[2]


# ---------------------------------------------------------------- translator
class TextTranslator:
    """Writes texts and reviews their translations; one Claude call per method."""

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

    def write_text(
        self,
        *,
        size: str,
        level: str,
        theme: Optional[Dict[str, Optional[str]]] = None,
        avoid_titles: Sequence[str] = (),
        direction: str = DIRECTION_EN_RU,
    ) -> WriteResult:
        """A new text of `size` words at `level`, in the context `theme`."""
        source, target = direction_languages(direction)
        low, high = size_words(size)
        lines = [
            f"Context: {themes.model_context(theme)}",
            f"Level: {_LEVEL_NAMES.get(level, _LEVEL_NAMES[DEFAULT_LEVEL])}",
            f"Length: {low}-{high} words",
        ]
        if avoid_titles:
            lines += ["", "Titles used before - write about something else:"]
            lines += [f"- {title}" for title in avoid_titles]
        lines += ["", "Write the text."]
        logger.info("Translation text requested: size=%s, level=%s", size, level)
        response = self._call(
            model=config.TEXT_WRITE_MODEL,
            max_tokens=config.TEXT_WRITE_MAX_TOKENS,
            system=WRITE_PROMPT.format(
                source=source, target=target, min_words=low, max_words=high
            ),
            messages=[{"role": "user", "content": "\n".join(lines)}],
            output_format=WrittenText,
            output_config={"effort": config.TEXT_WRITE_EFFORT},
            thinking={"type": "adaptive"},
        )
        parsed: WrittenText = response.parsed_output
        text = parsed.text.strip()
        if not text:
            raise AnalysisError("Claude вернул пустой текст. Попробуйте ещё раз.")
        return WriteResult(
            title=parsed.title.strip() or "Текст",
            text=text,
            call=_call_info(response, config.TEXT_WRITE_MODEL, config.TEXT_WRITE_EFFORT),
        )

    def review(
        self,
        text: str,
        translation: str,
        *,
        direction: str = DIRECTION_EN_RU,
        phrase_prefix: str = "p",
    ) -> ReviewResult:
        """Review a translation of `text`; phrase ids get `phrase_prefix`."""
        source, target = direction_languages(direction)
        request = (
            f"Original ({source}):\n{text.strip()}\n\n"
            f"Learner's translation ({target}):\n{translation.strip()}"
        )
        logger.info("Text translation review requested: %d chars", len(translation))
        response = self._call(
            model=config.TEXT_REVIEW_MODEL,
            max_tokens=config.TEXT_REVIEW_MAX_TOKENS,
            system=REVIEW_PROMPT.format(
                source=source, target=target, phrases=config.TEXT_PHRASES
            ),
            messages=[{"role": "user", "content": request}],
            output_format=TextReview,
            output_config={"effort": config.TEXT_REVIEW_EFFORT},
            thinking={"type": "adaptive"},
        )
        parsed: TextReview = response.parsed_output
        review = {
            "accuracy": max(0, min(100, int(parsed.accuracy))),
            "summary": parsed.summary.strip(),
            "mistakes": [
                {key: value.strip() for key, value in mistake.model_dump().items()}
                for mistake in parsed.mistakes
                if mistake.problem.strip()
            ],
            "unnatural": [
                {key: value.strip() for key, value in spot.model_dump().items()}
                for spot in parsed.unnatural
                if spot.quote.strip() and spot.suggestion.strip()
            ],
            "final_translation": parsed.final_translation.strip(),
        }
        return ReviewResult(
            review=review,
            phrases=clean_vocabulary(parsed.phrases, config.TEXT_PHRASES, phrase_prefix),
            call=_call_info(response, config.TEXT_REVIEW_MODEL, config.TEXT_REVIEW_EFFORT),
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
                exc, "Claude API не ответил вовремя. Попробуйте ещё раз."
            ) from exc
        if getattr(response, "stop_reason", None) == "refusal":
            raise AnalysisError("Claude отказался выполнить запрос. Попробуйте ещё раз.")
        if getattr(response, "parsed_output", None) is None:
            raise AnalysisError("Claude вернул ответ, который не удалось разобрать.")
        return response


def word_count(text: str) -> int:
    return len(text.split())


def minutes_for(text: str) -> int:
    """Rough minutes a translation of `text` takes (at least one)."""
    return max(1, round(word_count(text) / config.TEXT_WORDS_PER_MINUTE))
