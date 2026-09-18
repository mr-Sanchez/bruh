"""Claude-based speech analysis: structured, topic-tagged feedback in Russian.

This is the automated replacement for the old "copy the transcript and paste
it into an LLM chat yourself" workflow (see the README's former manual-prompt
section). Feedback content mirrors that original prompt's categories (tense,
word order, prepositions, agreement, word choice, run-ons, fillers,
repetitions, naturalness) but is additionally tagged with a topic from the
fixed taxonomy in app.progress_store, so mistakes can be aggregated across
sessions into a "what to practice" view.

The feedback is shaped like a speaking coach's review rather than an error
list: each mistake carries a minimal correction plus simpler/more natural
alternatives and the reusable construction behind the fix, followed by the
words the speaker was searching for, an improved retelling of the whole
monologue, a handful of takeaways and per-skill scores (1-10). The retelling
is a separate field of the analysis - the stored transcript is never touched.

Design rules, mirrored from app/transcriber.py's shape and testability:
  * feedback is always written in Russian, regardless of what language was
    practiced - the user reads Russian explanations for English *or* Russian
    speech
  * quotes are copied verbatim from the transcript, in whatever language was
    spoken; corrections stay in that same language (no translation)
  * the model is called with structured outputs (a Pydantic schema), so the
    app gets reliable JSON back, never freeform text to regex-parse
  * a `client_factory` constructor param allows a fake Anthropic client in
    tests - no network access needed to exercise everything but the HTTP call
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Final, List, Literal, Optional

from pydantic import BaseModel, Field

from app import config, utils
from app.progress_store import TOPIC_KEYS, TOPIC_TAXONOMY

logger = logging.getLogger(__name__)

ClientFactory = Callable[[str], Any]

TopicKey = Literal[*TOPIC_KEYS]
Severity = Literal["minor", "moderate", "major"]

# Bumped whenever the shape of analysis.json changes. v1 had only
# summary + issues(topic, quote, explanation, correction, severity); the
# frontend still renders v1 files, it just shows fewer sections.
ANALYSIS_SCHEMA_VERSION: Final[int] = 2

SCORE_MIN: Final[int] = 1
SCORE_MAX: Final[int] = 10


class Pattern(BaseModel):
    """A reusable construction behind a fix, e.g. "help someone + verb"."""

    rule: str
    examples: List[str] = Field(default_factory=list)


class Issue(BaseModel):
    """One tagged mistake, quoted from the transcript, with a Russian fix."""

    topic: TopicKey
    quote: str
    explanation: str
    correction: str
    better_versions: List[str] = Field(default_factory=list)
    pattern: Optional[Pattern] = None
    severity: Severity


class KeyPhrase(BaseModel):
    """A word or construction worth remembering, with a Russian gloss."""

    phrase: str
    meaning: str
    example: str


class Score(BaseModel):
    score: int
    comment: str


class Scores(BaseModel):
    grammar: Score
    vocabulary: Score
    fluency: Score
    naturalness: Score


class SpeechAnalysis(BaseModel):
    """The structured-output schema Claude is asked to fill in."""

    summary: str
    strengths: List[str] = Field(default_factory=list)
    issues: List[Issue] = Field(default_factory=list)
    vocabulary: List[KeyPhrase] = Field(default_factory=list)
    improved_version: str = ""
    takeaways: List[KeyPhrase] = Field(default_factory=list)
    scores: Optional[Scores] = None


class AnalysisError(RuntimeError):
    """A user-facing analysis failure (message is shown in the UI)."""


class MissingAnthropicApiKeyError(AnalysisError):
    """The ANTHROPIC_API_KEY environment variable is not set."""


@dataclass
class AnalysisResult:
    summary: str
    issues: List[Issue] = field(default_factory=list)
    topic_counts: Dict[str, int] = field(default_factory=dict)
    strengths: List[str] = field(default_factory=list)
    vocabulary: List[KeyPhrase] = field(default_factory=list)
    improved_version: str = ""
    takeaways: List[KeyPhrase] = field(default_factory=list)
    scores: Optional[Scores] = None
    overall_score: Optional[float] = None
    model: str = config.ANALYSIS_MODEL
    effort: str = config.DEFAULT_ANALYSIS_EFFORT
    request_id: Optional[str] = None


def _default_client_factory(api_key: str) -> Any:
    """Build a real Anthropic client (imported lazily to keep startup fast)."""
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def _topic_taxonomy_lines() -> str:
    return "\n".join(f'- "{key}": {info["description"]}' for key, info in TOPIC_TAXONOMY.items())


SYSTEM_PROMPT_TEMPLATE = """You are a friendly, practical Russian-speaking speaking coach reviewing a \
verbatim speech transcript of someone practicing spoken English and/or \
Russian. Your main job is to show the speaker how they could have said each \
thing better: more correctly, more simply and more naturally.

The transcript is raw and unedited: it deliberately preserves filler words, \
hesitations, false starts, self-corrections and grammar mistakes exactly as \
spoken. Do not assume these are transcription errors - they reflect what the \
speaker actually said.

Language rules:
- ALL explanatory text you write (summary, strengths, explanations, \
meanings, score comments) MUST be in Russian, regardless of what language \
the speaker was practicing.
- `quote` must be copied VERBATIM from the transcript, unmodified, in \
whatever language the speaker used at that point.
- Everything that shows how to say something (`correction`, \
`better_versions`, pattern `rule` and `examples`, `phrase`, `example`, \
`improved_version`) is in the language that was practiced. Do not translate \
between languages.

Fill in the result as follows:

`summary` - 2-4 Russian sentences: overall impression, what went well, and \
the single most important thing to work on next.

`strengths` - 1-3 short Russian points on what the speaker did well, \
concrete and tied to this recording (for example: explained an unknown word \
in other words instead of stopping, sustained a long answer, used a good \
phrase). Empty only if nothing stands out.

`issues` - the mistakes, in the order they occur in the transcript:
- `quote`: the verbatim fragment. For a tangled, rebuilt sentence, quote the \
whole fragment rather than one word.
- `explanation`: in Russian, what is wrong and why; if the phrasing \
accidentally means something else, say what it actually means.
- `correction`: the minimal correct version of the quote, as close to the \
speaker's own words as possible.
- `better_versions`: 0-2 ways a native speaker would actually say it - \
simpler or more natural than the minimal correction. Leave empty when the \
correction already sounds natural. Never repeat the correction here.
- `pattern`: when the fix rests on a reusable construction or rule, give it \
as a short formula (for example "help someone + verb", "If + Present \
Simple, will + verb", "might + base verb", "improve X by doing Y") with 1-3 \
short example sentences, ideally from the speaker's own context (IT, work, \
learning). null when there is no useful general rule.
- `topic`: exactly one key from this fixed list (use the key exactly as \
written, do not invent new keys):
{taxonomy}
  Use "other" sparingly, only when nothing else fits.
- `severity`: "minor" (barely noticeable), "moderate" (a native listener \
would notice), "major" (impedes understanding, or is systematic/frequent).
- Look for, where present: verb tense errors, article errors, word order, \
preposition errors, subject-verb agreement, wrong word choice / false \
friends, run-on or fragmented sentences, filler words / hesitations, \
repetitions / self-corrections, and unnatural or calqued phrasing.
- Do not invent issues that are not in the transcript and do not comment on \
correct speech. Group repeated instances of the same filler or repetition \
into one issue instead of listing every occurrence.

`vocabulary` - words the speaker was clearly searching for, described \
around ("someone who searches for employees" -> "recruiter"), or confused \
with a similar word (employee / employer), plus better terms for words used \
imprecisely. `phrase` is the word, `meaning` a Russian gloss (mention the \
confused pair if relevant), `example` a short sentence. Empty if none.

`improved_version` - the whole monologue retold the way it could have \
sounded: same ideas, same order, nothing added, at a level just slightly \
above the speaker's current one - natural and simple, not advanced or \
literary. Split into paragraphs with blank lines. This is a separate model \
answer shown next to the transcript, not an edit of it.

`takeaways` - the 3-5 most useful constructions from this recording to \
remember (not every fix - only what will come up again): `phrase` the \
construction, `meaning` a short Russian explanation, `example` one sentence.

`scores` - integers from {score_min} to {score_max} judged on this \
recording only, each with a one-sentence Russian comment: grammar, \
vocabulary (range and precision), fluency (flow, pauses, restarts), \
naturalness (how native-like the phrasing is). Be honest and consistent, \
not generous: roughly 5 means understandable with frequent errors, 8 means \
occasional minor slips.

If the transcript has no notable issues, return an empty issues list and \
say so in the summary."""


class ClaudeAnalyzer:
    """Thin, testable wrapper around a single structured-output Claude call."""

    def __init__(
        self,
        api_key: Optional[str],
        *,
        model: str = config.ANALYSIS_MODEL,
        effort: Optional[str] = None,
        timeout_seconds: int = config.ANALYSIS_TIMEOUT_SECONDS,
        client_factory: Optional[ClientFactory] = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._model = model
        self._effort = effort or config.analysis_effort()
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory or _default_client_factory

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    def analyze(
        self,
        transcript: str,
        profile: "config.LanguageProfile",
        duration_seconds: float = 0.0,
    ) -> AnalysisResult:
        """Analyze a verbatim transcript. Never modifies the transcript itself."""
        if not self._api_key:
            raise MissingAnthropicApiKeyError(config.MISSING_ANTHROPIC_API_KEY_MESSAGE)
        if not transcript.strip():
            raise AnalysisError("There is no transcript to analyze yet.")

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            taxonomy=_topic_taxonomy_lines(), score_min=SCORE_MIN, score_max=SCORE_MAX
        )
        user_message = (
            f"Practiced language: {profile.label} ({profile.language})\n"
            f"Recording duration: {utils.format_duration(duration_seconds)}\n\n"
            "Transcript (verbatim):\n---\n"
            f"{transcript}\n---\n"
            "Analyze this transcript per your instructions and return the "
            "structured result."
        )

        logger.info(
            "Analysis started: model=%s, effort=%s, transcript_chars=%d",
            self._model,
            self._effort,
            len(transcript),
        )

        try:
            client = self._client_factory(self._api_key)
            response = client.messages.parse(
                model=self._model,
                max_tokens=config.ANALYSIS_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                output_format=SpeechAnalysis,
                output_config={"effort": self._effort},
                thinking={"type": "adaptive"},
                timeout=self._timeout_seconds,
            )
        except Exception as exc:  # mapped to a friendly message below
            raise self._to_friendly_error(exc) from exc

        if getattr(response, "stop_reason", None) == "refusal":
            logger.warning(
                "Claude declined to analyze this transcript: %s",
                getattr(response, "stop_details", None),
            )
            raise AnalysisError(
                "Claude отказался анализировать эту запись. Попробуйте ещё раз "
                "или проверьте содержимое транскрипта."
            )

        parsed = response.parsed_output
        if parsed is None:
            raise AnalysisError("Claude вернул ответ, который не удалось разобрать.")

        topic_counts: Dict[str, int] = {}
        for issue in parsed.issues:
            topic_counts[issue.topic] = topic_counts.get(issue.topic, 0) + 1

        request_id = getattr(response, "_request_id", None) or getattr(response, "id", None)
        logger.info(
            "Analysis succeeded: request_id=%s, issues=%d",
            request_id,
            len(parsed.issues),
        )

        scores = _clamped_scores(parsed.scores)
        return AnalysisResult(
            summary=parsed.summary,
            issues=parsed.issues,
            topic_counts=topic_counts,
            strengths=parsed.strengths,
            vocabulary=parsed.vocabulary,
            improved_version=parsed.improved_version,
            takeaways=parsed.takeaways,
            scores=scores,
            overall_score=_overall_score(scores),
            model=self._model,
            effort=self._effort,
            request_id=request_id,
        )

    # --------------------------------------------------------------- errors
    def _to_friendly_error(self, exc: Exception) -> AnalysisError:
        """Turn SDK/network exceptions into something worth showing a human."""
        if isinstance(exc, AnalysisError):
            return exc

        import anthropic

        if isinstance(exc, anthropic.AuthenticationError):
            return AnalysisError(
                "Claude отклонил API-ключ. Проверьте ANTHROPIC_API_KEY."
            )
        if isinstance(exc, anthropic.PermissionDeniedError):
            return AnalysisError(
                "У ключа ANTHROPIC_API_KEY нет прав на эту операцию/модель."
            )
        if isinstance(exc, anthropic.RateLimitError):
            retry_after = _retry_after(exc)
            suffix = (
                f" Повторите попытку через {retry_after} с."
                if retry_after
                else " Повторите попытку позже."
            )
            return AnalysisError(f"Достигнут лимит запросов к Claude.{suffix}")
        if isinstance(exc, anthropic.APITimeoutError):
            return AnalysisError(
                "Claude API не ответил вовремя. Транскрипт сохранён - "
                "попробуйте выполнить анализ ещё раз."
            )
        if isinstance(exc, anthropic.APIConnectionError):
            return AnalysisError(
                "Не удалось связаться с Claude API. Проверьте подключение к "
                "интернету и повторите попытку."
            )
        if isinstance(exc, anthropic.APIStatusError):
            return AnalysisError(f"Ошибка Claude API (HTTP {exc.status_code}).")

        logger.exception("Unexpected analysis failure")
        return AnalysisError(f"Не удалось выполнить анализ: {exc}")


def _clamped_scores(scores: Optional[Scores]) -> Optional[Scores]:
    """Keep scores inside the documented range.

    Structured outputs guarantee an integer, not its bounds, and a stray 0 or
    11 would skew the cross-session score history later.
    """
    if scores is None:
        return None
    clamped = scores.model_copy(deep=True)
    for name in Scores.model_fields:
        item: Score = getattr(clamped, name)
        item.score = min(SCORE_MAX, max(SCORE_MIN, item.score))
    return clamped


def _overall_score(scores: Optional[Scores]) -> Optional[float]:
    """Mean of the four skill scores, rounded to the nearest 0.5.

    Computed here rather than asked of the model so it always agrees with the
    per-skill numbers shown next to it.
    """
    if scores is None:
        return None
    values = [getattr(scores, name).score for name in Scores.model_fields]
    # floor(x + 0.5) rather than round(): round() is banker's rounding, which
    # would turn 6.25 into 6.0 but 6.75 into 7.0.
    return math.floor(sum(values) / len(values) * 2 + 0.5) / 2


def _retry_after(exc: Exception) -> Optional[str]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        return headers.get("retry-after")
    except AttributeError:  # pragma: no cover
        return None
