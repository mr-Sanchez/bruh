"""Claude-based speech analysis: structured, topic-tagged feedback in Russian.

This is the automated replacement for the old "copy the transcript and paste
it into an LLM chat yourself" workflow (see the README's former manual-prompt
section). Feedback content mirrors that original prompt's categories (tense,
word order, prepositions, agreement, word choice, run-ons, fillers,
repetitions, naturalness) but is additionally tagged with a topic from the
closed catalogue in app.curriculum (the topics the roadmap's lessons teach),
so mistakes can be aggregated across sessions into a "what to practice" view.

The feedback is shaped like a speaking coach's review rather than an error
list: each mistake carries a minimal correction plus simpler/more natural
alternatives and the reusable construction behind the fix, followed by the
words the speaker was searching for, an improved retelling of the whole
monologue, a handful of takeaways and per-skill scores (1-10). The retelling
is a separate field of the analysis - the stored transcript is never touched.
Each English mistake also carries a few new Russian -> English practice
sentences on the same construction: the learner's cards are built from those
(decided 2026-09-26), so a review never depends on remembering the recording.

A picture description (Stage 4) is the same call with the picture attached:
the language feedback is identical, plus what the speaker did not mention
and words for the scene. A description can also be typed instead of spoken;
then there is no delivery to judge, so fluency stays out of the overall score.

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

import base64
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Final, List, Literal, Optional

from pydantic import BaseModel, Field

from app import config, curriculum, themes, utils

logger = logging.getLogger(__name__)

ClientFactory = Callable[[str], Any]

TopicKey = Literal[*curriculum.TOPIC_KEYS]
Severity = Literal["minor", "moderate", "major"]

# Bumped whenever the shape of analysis.json changes. v1 had only
# summary + issues(topic, quote, explanation, correction, severity); the
# frontend still renders v1 files, it just shows fewer sections. v3 adds
# kind / input_mode and, for pictures, not_mentioned + scene_vocabulary.
# v4 adds issues[].drills - the practice sentences cards are built from.
# v5: issues[].topic is a taxonomy v2 key (app/curriculum.py).
ANALYSIS_SCHEMA_VERSION: Final[int] = 5

SCORE_MIN: Final[int] = 1
SCORE_MAX: Final[int] = 10


class Pattern(BaseModel):
    """A reusable construction behind a fix, e.g. "help someone + verb"."""

    rule: str
    examples: List[str] = Field(default_factory=list)


class Drill(BaseModel):
    """A new sentence that needs the construction the speaker got wrong:
    Russian to say, English to check against. Cards are built from these, not
    from the quote itself - a verbatim fragment seen a week later has no
    context left, and recalling it trains memory of one text, not the rule."""

    russian: str
    english: str


class Issue(BaseModel):
    """One tagged mistake, quoted from the transcript, with a Russian fix."""

    topic: TopicKey
    quote: str
    explanation: str
    correction: str
    better_versions: List[str] = Field(default_factory=list)
    pattern: Optional[Pattern] = None
    severity: Severity
    drills: List[Drill] = Field(default_factory=list)


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


class SceneDetail(BaseModel):
    """Something in the picture the speaker left out, and a way to say it."""

    detail: str
    phrase: str


class PictureAnalysis(SpeechAnalysis):
    """The schema for a picture description: the usual feedback plus the scene."""

    not_mentioned: List[SceneDetail] = Field(default_factory=list)
    scene_vocabulary: List[KeyPhrase] = Field(default_factory=list)


class MissedUse(BaseModel):
    """A place where the lesson's construction was needed but not used."""

    quote: str
    better: str


class LessonCheck(BaseModel):
    """How well a lesson's spoken task used the lesson's rule (Stage 8, R6)."""

    score: int
    verdict: str
    good_uses: List[str] = Field(default_factory=list)
    missed: List[MissedUse] = Field(default_factory=list)


class LessonAnalysis(SpeechAnalysis):
    """The schema for a lesson's spoken task: the usual feedback plus the rule check."""

    lesson_check: LessonCheck


@dataclass(frozen=True)
class ImageInput:
    """The picture a description is about, as sent to Claude."""

    data: bytes
    media_type: str


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
    # Picture descriptions only; empty for a monologue.
    not_mentioned: List[SceneDetail] = field(default_factory=list)
    scene_vocabulary: List[KeyPhrase] = field(default_factory=list)
    # A lesson's spoken task only: LessonCheck as a dict (score clamped 1..10).
    lesson_check: Optional[Dict[str, Any]] = None
    model: str = config.ANALYSIS_MODEL
    effort: str = config.DEFAULT_ANALYSIS_EFFORT
    request_id: Optional[str] = None
    # Token counts from response.usage, for the usage/cost log.
    usage: Dict[str, int] = field(default_factory=dict)


def _default_client_factory(api_key: str) -> Any:
    """Build a real Anthropic client (imported lazily to keep startup fast)."""
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


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
- `topic`: exactly one key from this fixed list, grouped by area (use the \
key exactly as written, do not invent new keys). Pick the most specific \
topic that names the mistake - for a wrong tense, the tense that was needed:
{taxonomy}
  Use "other" sparingly, only when nothing else fits.
- `severity`: "minor" (barely noticeable), "moderate" (a native listener \
would notice), "major" (impedes understanding, or is systematic/frequent).
- `drills`: {drills} practice sentences for this mistake, used later as \
flashcards "say it in English". Each is a NEW situation taken from the \
"Context for drills" line of the request (vary people and settings within \
it), never the speaker's own sentence or a \
paraphrase of it, and needs exactly the construction or word choice the \
speaker got wrong - so saying it right means having learned the fix. \
`russian`: one natural, unambiguous Russian sentence of 5-14 words whose \
natural English translation uses that construction. `english`: that natural \
English translation, at the speaker's level. Leave `drills` empty when the \
quote is not English, or when the topic is "filler_words_fluency" or \
"repetition_self_correction" (delivery, not something to recall).
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

PICTURE_PROMPT = """

This is a PICTURE DESCRIPTION exercise: the speaker was describing the \
attached picture. Review the language exactly as above, and additionally:
- In `summary`, add one sentence (Russian) on how complete and accurate the \
description was. If the speaker described something that is not in the \
picture, say so there - content slips are not language issues.
- `improved_version` retells the speaker's own description; do not add \
things they did not mention (those go into `not_mentioned`).
- `not_mentioned`: 3-6 notable things in the picture the speaker did not \
mention - the main action, people and what they are doing, the setting, \
the mood, one or two easy-to-name details. `detail` is a short Russian \
note on what it is; `phrase` is one simple sentence in the practiced \
language that describes it, at the speaker's level.
- `scene_vocabulary`: 5-8 words or phrases useful for describing this \
picture, especially ones the speaker lacked, avoided or described around; \
include useful positional phrases (in the foreground, on the left, ...) \
when the speaker did not use them. `phrase` in the practiced language, \
`meaning` a Russian gloss, `example` a sentence about this picture. Do not \
repeat anything already in `vocabulary`."""

TYPED_PROMPT = """

The text was TYPED by the learner, not spoken: there are no filler words, \
hesitations or restarts to find, so do not use the "filler_words_fluency" \
or "repetition_self_correction" topics. Score `fluency` as how smoothly the \
text reads (linking words, sentence flow); it is not counted in the overall \
score."""


LESSON_PROMPT = """

This is the SPOKEN TASK of a course lesson: the speaker was asked to talk on \
a task that needs the lesson's construction (given in the request). Review \
the language exactly as above, and additionally fill `lesson_check`:
- `score`: {score_min}-{score_max}, how well the speaker used the lesson's \
construction: used where needed, correctly, and more than once. Using it \
rarely or avoiding it lowers the score even if the rest is correct.
- `verdict`: 1-3 Russian sentences - whether they used it, how well, and \
the one thing to fix about it.
- `good_uses`: verbatim quotes from the transcript where it was used \
correctly (empty if none).
- `missed`: places where it was needed but missing or wrong: `quote` \
verbatim, `better` the same thing said with the construction.
Mistakes with the construction also go to `issues` as usual, under the \
lesson's topic key."""


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
        *,
        image: Optional[ImageInput] = None,
        typed: bool = False,
        theme: Optional[Dict[str, Optional[str]]] = None,
        lesson: Optional[Dict[str, Any]] = None,
    ) -> AnalysisResult:
        """Analyze a verbatim transcript. Never modifies the transcript itself.

        With `image`, the transcript is a description of that picture; with
        `typed`, the learner wrote it instead of speaking it. `theme` is the
        context («уклон») the practice sentences are set in. `lesson` makes it
        a lesson's spoken task: {"key", "label", "description", "task", "use"}
        - the rule is checked in `lesson_check`.
        """
        if not self._api_key:
            raise MissingAnthropicApiKeyError(config.MISSING_ANTHROPIC_API_KEY_MESSAGE)
        if not transcript.strip():
            raise AnalysisError("There is no transcript to analyze yet.")

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            taxonomy=curriculum.taxonomy_prompt_lines(),
            score_min=SCORE_MIN,
            score_max=SCORE_MAX,
            drills=config.DRILLS_PER_ISSUE,
        )
        if image is not None:
            system_prompt += PICTURE_PROMPT
        if typed:
            system_prompt += TYPED_PROMPT
        lesson_lines = ""
        if lesson is not None:
            system_prompt += LESSON_PROMPT.format(score_min=SCORE_MIN, score_max=SCORE_MAX)
            lesson_lines = (
                f"Lesson: {lesson['label']} (topic key {lesson['key']}) - "
                f"{lesson.get('description', '')}\n"
                f"Task given: {lesson.get('task', '')}\n"
                f"Construction to use: {lesson.get('use', '')}\n"
            )
        source = (
            "Typed text (verbatim, as the learner wrote it)"
            if typed
            else f"Recording duration: {utils.format_duration(duration_seconds)}\n\n"
            "Transcript (verbatim)"
        )
        user_message = (
            f"Practiced language: {profile.label} ({profile.language})\n"
            f"Context for drills: {themes.model_context(theme)}\n"
            f"{lesson_lines}"
            f"{source}:\n---\n"
            f"{transcript}\n---\n"
            "Analyze this transcript per your instructions and return the "
            "structured result."
        )
        content: List[Dict[str, Any]] = []
        if image is not None:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image.media_type,
                        "data": base64.standard_b64encode(image.data).decode("ascii"),
                    },
                }
            )
        content.append({"type": "text", "text": user_message})

        logger.info(
            "Analysis started: model=%s, effort=%s, transcript_chars=%d, image=%s, typed=%s",
            self._model,
            self._effort,
            len(transcript),
            image is not None,
            typed,
        )

        try:
            client = self._client_factory(self._api_key)
            response = client.messages.parse(
                model=self._model,
                max_tokens=config.ANALYSIS_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": content}],
                output_format=(
                    PictureAnalysis
                    if image is not None
                    else LessonAnalysis if lesson is not None else SpeechAnalysis
                ),
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
                "ИИ ассистент отказался анализировать эту запись. Попробуйте ещё раз "
                "или проверьте содержимое транскрипта."
            )

        parsed = response.parsed_output
        if parsed is None:
            raise AnalysisError("ИИ ассистент вернул ответ, который не удалось разобрать.")

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
            overall_score=_overall_score(scores, skip=("fluency",) if typed else ()),
            not_mentioned=list(getattr(parsed, "not_mentioned", [])),
            scene_vocabulary=list(getattr(parsed, "scene_vocabulary", [])),
            lesson_check=_lesson_check(getattr(parsed, "lesson_check", None)),
            model=self._model,
            effort=self._effort,
            request_id=request_id,
            usage=usage_counts(response),
        )

    # --------------------------------------------------------------- errors
    def _to_friendly_error(self, exc: Exception) -> AnalysisError:
        return friendly_api_error(
            exc,
            "ИИ ассистент не ответил вовремя. Транскрипт сохранён - "
            "попробуйте выполнить анализ ещё раз.",
        )


def friendly_api_error(exc: Exception, timeout_message: str) -> AnalysisError:
    """Turn SDK/network exceptions into something worth showing a human.

    Shared by every Claude call in the app (analysis, exercise sets, grading);
    only the timeout advice differs between them.
    """
    if isinstance(exc, AnalysisError):
        return exc

    import anthropic

    if isinstance(exc, anthropic.AuthenticationError):
        return AnalysisError("ИИ ассистент отклонил API-ключ. Проверьте ANTHROPIC_API_KEY.")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return AnalysisError("У ключа ANTHROPIC_API_KEY нет прав на эту операцию/модель.")
    if isinstance(exc, anthropic.RateLimitError):
        retry_after = _retry_after(exc)
        suffix = (
            f" Повторите попытку через {retry_after} с."
            if retry_after
            else " Повторите попытку позже."
        )
        return AnalysisError(f"Достигнут лимит запросов к ИИ ассистенту.{suffix}")
    if isinstance(exc, anthropic.APITimeoutError):
        return AnalysisError(timeout_message)
    if isinstance(exc, anthropic.APIConnectionError):
        return AnalysisError(
            "Не удалось связаться с ИИ ассистентом. Проверьте подключение к "
            "интернету и повторите попытку."
        )
    if isinstance(exc, anthropic.APIStatusError):
        return AnalysisError(f"Ошибка ИИ ассистента (HTTP {exc.status_code}).")

    logger.exception("Unexpected Claude API failure")
    return AnalysisError(f"Не удалось выполнить запрос к ИИ ассистенту: {exc}")


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


def _lesson_check(check: Optional[LessonCheck]) -> Optional[Dict[str, Any]]:
    """The rule check of a lesson's spoken task as stored, score in range."""
    if check is None:
        return None
    data = check.model_dump()
    data["score"] = min(SCORE_MAX, max(SCORE_MIN, check.score))
    return data


def _overall_score(scores: Optional[Scores], skip: tuple = ()) -> Optional[float]:
    """Mean of the skill scores, rounded to the nearest 0.5.

    Computed here rather than asked of the model so it always agrees with the
    per-skill numbers shown next to it. `skip` leaves skills out - fluency
    for a typed text, which has no delivery to judge.
    """
    if scores is None:
        return None
    values = [getattr(scores, name).score for name in Scores.model_fields if name not in skip]
    # floor(x + 0.5) rather than round(): round() is banker's rounding, which
    # would turn 6.25 into 6.0 but 6.75 into 7.0.
    return math.floor(sum(values) / len(values) * 2 + 0.5) / 2


_USAGE_FIELDS: Final[tuple] = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def usage_counts(response: Any) -> Dict[str, int]:
    """Token counts from response.usage; missing or null fields count as 0."""
    usage = getattr(response, "usage", None)
    counts: Dict[str, int] = {}
    for name in _USAGE_FIELDS:
        value = getattr(usage, name, None) if usage is not None else None
        counts[name] = value if isinstance(value, int) else 0
    return counts


def _retry_after(exc: Exception) -> Optional[str]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        return headers.get("retry-after")
    except AttributeError:  # pragma: no cover
        return None
