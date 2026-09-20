"""Claude-generated exercise sets (Stage 5): generation and grading.

The free drills (cards, cloze) can only reuse sentences the learner already
spoke. A set adds *new* sentences on the same weak spot: 8-10 exercises on one
topic, built around the learner's own mistakes and rules on it, in an IT /
work context. Three kinds, easy to hard:

  * gap        - one blank in an English sentence; checked in the browser;
  * fix        - an English sentence with one mistake to correct; checked in
                 the browser against the correction and its accepted variants;
  * translate  - a Russian sentence to say in English with the target
                 construction; free-form, so Claude grades these, all of a
                 set's answers in a single call at the end (decided 2026-09-19:
                 one call is cheaper than one per answer).

Everything costs money, so nothing here runs on its own: api.py calls it on
an explicit click, and learner_store keeps the result on disk for reuse.

Same shape as app/analyzer.py: structured outputs through Pydantic schemas,
Russian for everything the learner reads as explanation, and a
`client_factory` so tests never touch the network.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from app import config
from app.analyzer import AnalysisError, MissingAnthropicApiKeyError, friendly_api_error, usage_counts

logger = logging.getLogger(__name__)

ClientFactory = Callable[[str], Any]

GAP_MARK = "___"

TYPE_GAP = "gap"
TYPE_FIX = "fix"
TYPE_TRANSLATE = "translate"


# ------------------------------------------------------------------ schemas
class GapExercise(BaseModel):
    sentence: str  # contains GAP_MARK exactly once
    answers: List[str]
    hint: str = ""
    explanation: str


class FixExercise(BaseModel):
    sentence: str
    correction: str
    also_accepted: List[str] = Field(default_factory=list)
    explanation: str


class TranslateExercise(BaseModel):
    russian: str
    reference: str
    focus: str


class GeneratedSet(BaseModel):
    intro: str
    gaps: List[GapExercise] = Field(default_factory=list)
    fixes: List[FixExercise] = Field(default_factory=list)
    translations: List[TranslateExercise] = Field(default_factory=list)


class Verdict(BaseModel):
    number: int
    correct: bool
    comment: str
    corrected: str


class Grading(BaseModel):
    verdicts: List[Verdict] = Field(default_factory=list)


# ------------------------------------------------------------------ results
@dataclass
class ClaudeCall:
    model: str
    usage: Dict[str, int] = field(default_factory=dict)
    request_id: Optional[str] = None
    effort: Optional[str] = None


@dataclass
class GenerationResult:
    intro: str
    exercises: List[Dict[str, Any]]
    call: ClaudeCall


@dataclass
class GradingResult:
    # By exercise id: {"correct", "comment", "corrected"}.
    verdicts: Dict[str, Dict[str, Any]]
    call: ClaudeCall


@dataclass(frozen=True)
class TranslationAnswer:
    exercise_id: str
    russian: str
    reference: str
    focus: str
    answer: str


# ------------------------------------------------------------------ prompts
GENERATION_PROMPT = """You write short English practice exercises for one \
adult learner, a Russian speaker who works in IT and practises spoken \
English. Every set trains ONE grammar or vocabulary topic, built around the \
learner's own mistakes and rules on it, which you are given.

Write:
- `intro`: 1-2 Russian sentences on what this set trains (the rule in plain \
words).
- `gaps`: exactly {gaps} sentences, each with exactly one blank written as \
"___" where the topic's word or form goes. `answers`: every correct filler \
(the main one first; include contractions or equally correct variants). \
`hint`: optional short cue shown next to the blank, such as the base verb \
"(go)" for a tense; empty when the blank should be guessed. `explanation`: \
one Russian sentence on why this answer.
- `fixes`: exactly {fixes} sentences that each contain exactly one mistake of \
this topic, the kind this learner makes. `correction`: the whole sentence \
with the minimal fix. `also_accepted`: other fully correct whole-sentence \
fixes, if any. `explanation`: one Russian sentence.
- `translations`: exactly {translations} natural Russian sentences whose \
English translation needs the target construction. `reference`: a natural \
English translation. `focus`: a short Russian cue naming the construction to \
use (for example "Present Perfect: have done").

Rules:
- Context: IT and everyday work life (projects, meetings, code review, \
deployments, colleagues, learning), with some everyday life. Vary people, \
situations and sentence shapes; never reuse the learner's own sentences \
word for word.
- Level: just above the learner's own sentences - clear and natural, not \
literary. 6-16 words per sentence.
- Every exercise must have one clear correct answer; avoid sentences where \
the other option is also acceptable.
- Everything the learner reads as explanation is Russian; exercise sentences \
are English (translations: the prompt is Russian, the answer English)."""

GRADING_PROMPT = """You grade a Russian-speaking learner's English \
translations. For each numbered item you get the Russian sentence, the \
construction it trains, a reference translation and the learner's answer.

- `correct`: true when the answer is correct, natural enough English that \
says the same thing, AND uses the target construction correctly. Other \
wording than the reference is fine. A small typo that is clearly not a \
grammar mistake does not make it wrong. An empty or off-meaning answer is \
wrong.
- `comment`: one or two Russian sentences: what is wrong and why, or a brief \
confirmation when it is right.
- `corrected`: the learner's own answer with the minimal fixes that make it \
correct (unchanged when it is already correct; the reference when the answer \
is empty or unusable).
- Return one verdict per item, with its `number`."""


def _default_client_factory(api_key: str) -> Any:
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


# ---------------------------------------------------------------- generator
class ExerciseSetGenerator:
    """Builds and grades sets; one Claude call per method."""

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

    def generate(
        self,
        topic: Dict[str, str],
        seeds: Sequence[Dict[str, Any]],
        avoid: Sequence[str] = (),
    ) -> GenerationResult:
        """A new set on `topic` ({"key", "label", "description"}).

        `seeds` are the learner's own bank items on the topic (fix / pattern),
        `avoid` sentences from earlier sets on it.
        """
        self._require_key()
        system = GENERATION_PROMPT.format(
            gaps=config.SET_GAPS, fixes=config.SET_FIXES, translations=config.SET_TRANSLATIONS
        )
        user = _generation_request(topic, seeds, avoid)
        logger.info("Exercise set requested: topic=%s, seeds=%d", topic["key"], len(seeds))
        response = self._call(
            model=config.EXERCISE_SET_MODEL,
            max_tokens=config.EXERCISE_SET_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=GeneratedSet,
            output_config={"effort": config.EXERCISE_SET_EFFORT},
            thinking={"type": "adaptive"},
        )
        parsed: GeneratedSet = response.parsed_output
        exercises = assemble_exercises(parsed)
        if not exercises:
            raise AnalysisError("Claude вернул пустой набор упражнений. Попробуйте ещё раз.")
        return GenerationResult(
            intro=parsed.intro.strip(),
            exercises=exercises,
            call=_call_info(response, config.EXERCISE_SET_MODEL, config.EXERCISE_SET_EFFORT),
        )

    def grade(self, topic: Dict[str, str], answers: Sequence[TranslationAnswer]) -> GradingResult:
        """Grade free translations in one call. Items the model skips are
        left out of the result; the caller decides what to do with them."""
        self._require_key()
        lines = [f"Topic: {topic['label']} ({topic['key']})", ""]
        for number, item in enumerate(answers, start=1):
            lines += [
                f"{number}. Russian: {item.russian}",
                f"   Construction: {item.focus}",
                f"   Reference: {item.reference}",
                f"   Learner: {item.answer.strip() or '(empty)'}",
            ]
        response = self._call(
            model=config.GRADING_MODEL,
            max_tokens=config.GRADING_MAX_TOKENS,
            system=GRADING_PROMPT,
            messages=[{"role": "user", "content": "\n".join(lines)}],
            output_format=Grading,
        )
        verdicts: Dict[str, Dict[str, Any]] = {}
        for verdict in response.parsed_output.verdicts:
            if 1 <= verdict.number <= len(answers):
                verdicts[answers[verdict.number - 1].exercise_id] = {
                    "correct": verdict.correct,
                    "comment": verdict.comment.strip(),
                    "corrected": verdict.corrected.strip(),
                }
        return GradingResult(verdicts=verdicts, call=_call_info(response, config.GRADING_MODEL))

    # -------------------------------------------------------------- helpers
    def _require_key(self) -> None:
        if not self._api_key:
            raise MissingAnthropicApiKeyError(config.MISSING_ANTHROPIC_API_KEY_MESSAGE)

    def _call(self, **kwargs: Any) -> Any:
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


def _call_info(response: Any, model: str, effort: Optional[str] = None) -> ClaudeCall:
    return ClaudeCall(
        model=model,
        usage=usage_counts(response),
        request_id=getattr(response, "_request_id", None) or getattr(response, "id", None),
        effort=effort,
    )


def _generation_request(
    topic: Dict[str, str], seeds: Sequence[Dict[str, Any]], avoid: Sequence[str]
) -> str:
    lines = [
        f"Topic: {topic['label']} ({topic['key']}) - {topic.get('description', '')}",
        "",
    ]
    if seeds:
        lines.append("The learner's own mistakes and rules on this topic (from their speech):")
        for item in seeds:
            content = item.get("content") or {}
            if item.get("kind") == "pattern":
                examples = "; ".join(content.get("examples") or [])
                suffix = f" (e.g. {examples})" if examples else ""
                lines.append(f"- rule: {content.get('rule', '')}{suffix}")
            else:
                quote, correction = content.get("quote", ""), content.get("correction", "")
                lines.append(f'- said: "{quote}" -> correct: "{correction}"')
    else:
        lines.append(
            "No recorded mistakes on this topic yet: cover its most common problems "
            "for Russian speakers."
        )
    if avoid:
        lines += ["", "Sentences from earlier sets - do not repeat them:"]
        lines += [f"- {sentence}" for sentence in avoid]
    lines += ["", "Write the set."]
    return "\n".join(lines)


def assemble_exercises(parsed: GeneratedSet) -> List[Dict[str, Any]]:
    """The set's exercises in running order, with stable ids.

    Structured outputs guarantee the shape, not the content: a gap sentence
    without exactly one blank or an exercise with no answer is dropped rather
    than shown broken, and each list is capped to the configured count.
    """
    exercises: List[Dict[str, Any]] = []

    for gap in parsed.gaps[: config.SET_GAPS]:
        parts = gap.sentence.split(GAP_MARK)
        answers = [a.strip() for a in gap.answers if a.strip()]
        if len(parts) != 2 or not answers:
            continue
        exercises.append(
            {
                "type": TYPE_GAP,
                "before": parts[0],
                "after": parts[1],
                "accept": answers,
                "hint": gap.hint.strip(),
                "explanation": gap.explanation.strip(),
            }
        )
    for fix in parsed.fixes[: config.SET_FIXES]:
        correction = fix.correction.strip()
        if not correction or not fix.sentence.strip():
            continue
        exercises.append(
            {
                "type": TYPE_FIX,
                "sentence": fix.sentence.strip(),
                "accept": [correction] + [a.strip() for a in fix.also_accepted if a.strip()],
                "explanation": fix.explanation.strip(),
            }
        )
    for item in parsed.translations[: config.SET_TRANSLATIONS]:
        if not item.russian.strip() or not item.reference.strip():
            continue
        exercises.append(
            {
                "type": TYPE_TRANSLATE,
                "russian": item.russian.strip(),
                "reference": item.reference.strip(),
                "focus": item.focus.strip(),
            }
        )
    for number, exercise in enumerate(exercises, start=1):
        exercise["id"] = f"ex{number}"
    return exercises
