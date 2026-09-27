"""Claude-generated exercise sets (Stage 5): generation and grading.

The free drills can only reuse sentences the learner already
spoke. A set adds *new* sentences on the same weak spot: 8-10 exercises on one
topic, built around the learner's own mistakes and rules on it, in the
context («уклон», app.themes) the learner picked for this set. Three kinds,
easy to hard:

  * gap        - one blank in an English sentence; checked in the browser;
  * fix        - an English sentence with one mistake to correct; checked in
                 the browser against the correction and its accepted variants;
  * translate  - a Russian sentence to say in English with the target
                 construction; free-form, so Claude grades these, all of a
                 set's answers in a single call at the end (decided 2026-09-19:
                 one call is cheaper than one per answer).

The same call also writes a few useful words and phrases on the set's topic
and context (`vocabulary`, 2026-09-27). They are shown after the run and the
learner picks which ones become RU -> EN word cards (learner_store); nothing
becomes a card without that choice.

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

from app import config, themes
from app.analyzer import (
    AnalysisError,
    MissingAnthropicApiKeyError,
    TopicKey,
    friendly_api_error,
    usage_counts,
)
from app.curriculum import taxonomy_prompt_lines

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


class VocabularyItem(BaseModel):
    english: str
    russian: str
    example: str
    example_russian: str
    note: str = ""


class GeneratedSet(BaseModel):
    intro: str
    gaps: List[GapExercise] = Field(default_factory=list)
    fixes: List[FixExercise] = Field(default_factory=list)
    translations: List[TranslateExercise] = Field(default_factory=list)
    vocabulary: List[VocabularyItem] = Field(default_factory=list)


class Verdict(BaseModel):
    number: int
    correct: bool
    comment: str
    corrected: str
    # The main mistake's own topic: a set on articles can still catch an
    # "it was" for "there were", and the card should train where it belongs.
    topic: TopicKey


class Grading(BaseModel):
    verdicts: List[Verdict] = Field(default_factory=list)


class SpeakingPrompt(BaseModel):
    question: str
    hint: str


class SpeakingPrompts(BaseModel):
    prompts: List[SpeakingPrompt] = Field(default_factory=list)


class TestChoice(BaseModel):
    lesson: str
    question: str
    options: List[str]
    correct: int
    explanation: str


class TestGap(BaseModel):
    lesson: str
    sentence: str
    answers: List[str]
    hint: str = ""
    explanation: str


class ModuleTest(BaseModel):
    choices: List[TestChoice] = Field(default_factory=list)
    gaps: List[TestGap] = Field(default_factory=list)


class LessonTask(BaseModel):
    question: str
    hint: str
    use: str


class LessonTasks(BaseModel):
    tasks: List[LessonTask] = Field(default_factory=list)


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
    # Word card candidates: [{"id", "english", "russian", "example",
    # "example_russian", "note"}], see assemble_vocabulary.
    vocabulary: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class GradingResult:
    # By exercise id: {"correct", "comment", "corrected", "topic"}.
    verdicts: Dict[str, Dict[str, Any]]
    call: ClaudeCall


@dataclass
class ModuleTestResult:
    test: Dict[str, Any]  # ModuleTest as a dict: {"choices", "gaps"}
    call: ClaudeCall


@dataclass
class PromptsResult:
    # [{"question", "hint"}]: English question, Russian hint.
    prompts: List[Dict[str, str]]
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
- `vocabulary`: exactly {vocabulary} useful English words or phrases worth \
learning as flashcards: natural collocations, phrasal verbs, set phrases and \
words a learner at this level needs in the request's context, tied to the \
topic where that makes sense (for a grammar topic: phrases that go with the \
construction, such as "by the time", "so far"). No grammar notation or \
formulas ("have + V3"), no very basic single words. `english`: the word or \
phrase as it is learned ("reach out to someone", "a tight deadline"). \
`russian`: its natural Russian equivalent - the front of the card, so it \
must lead to this English and not to a looser synonym. `example`: a short \
English sentence using it in the context. `example_russian`: that sentence \
in Russian. `note`: optional, one short Russian remark - register, a typical \
mistake of Russian speakers, or how it differs from a similar word; empty \
when there is nothing useful to say.

Rules:
- Context: every sentence is set in the situations named on the request's \
"Context:" line. Vary people, situations and sentence shapes within it; never \
reuse the learner's own sentences word for word.
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
wording than the reference is fine: a synonym, a different but acceptable \
article, tense or word order is NOT a mistake - judge only what a native \
speaker would actually call wrong. A small typo that is clearly not a \
grammar mistake does not make it wrong. An empty or off-meaning answer is \
wrong.
- `comment`: one or two Russian sentences naming the real mistake precisely \
(the learner's exact words -> the right ones) and why; never invent a \
mistake the answer does not have, never contradict yourself, no nitpicks on \
acceptable choices. A brief confirmation when it is right.
- `corrected`: the learner's own answer with the minimal fixes that make it \
correct (unchanged when it is already correct; the reference when the answer \
is empty or unusable).
- `topic`: the key of the main mistake's topic, from this closed list (use \
exactly these keys) - which may differ from the set's topic:
{taxonomy}
  For a correct answer, or an empty one, use the set's topic key.
- Return one verdict per item, with its `number`."""


SPEAKING_PROMPTS_PROMPT = """You write speaking prompts for an adult \
Russian-speaking learner of English (B1-B2) who practises talking for 1-3 \
minutes on a question. The learner chose the context the questions must come \
from; it is given in their own words.

Write exactly {count} prompts, all clearly within that context and different \
from each other: a mix of telling about their own experience, explaining or \
describing something, a role-play situation ("You are ... Explain / ask / \
complain ..."), and giving an opinion with reasons.
- `question`: one or two plain English sentences, easy to understand, that \
invite a longer answer (not a yes/no question).
- `hint`: a short Russian label of the prompt, 2-6 words (for example \
"Недавний проект и ваша роль")."""


MODULE_TEST_PROMPT = """You write a short placement test for one module of \
an English course, for an adult Russian speaker. The test decides which of \
the module's lessons the learner already knows, so every question checks \
exactly ONE lesson's topic - the one it is tagged with - and a learner who \
does not know that topic should get it wrong.

For EVERY lesson you are given, write exactly one `choices` item and exactly \
one `gaps` item, with `lesson` set to that lesson's key:
- `choices`: `question` - an English sentence with "___" where the tested \
form goes (or a short question about usage); `options` - 4 short options, \
exactly one correct, the wrong ones being the mistakes Russian speakers \
really make; `correct` - the 0-based index of the right option.
- `gaps`: `sentence` - an English sentence with exactly one "___"; \
`answers` - every correct filler (the main one first, contractions too); \
`hint` - an optional cue such as the base verb "(go)"; empty when the blank \
should be guessed.
- `explanation` (both): one Russian sentence on why the answer is right.

Rules: neutral everyday and work sentences, 6-14 words, at the lesson's \
level; exactly one correct answer; do not reuse the sentences listed as used \
before."""


LESSON_TASKS_PROMPT = """You write speaking tasks for one lesson of an \
English course. The learner is an adult Russian speaker; a task is 1-2 \
minutes of talking, and it must make the learner USE the lesson's \
construction many times, naturally - the task is built so that a good answer \
cannot avoid it (for Past Perfect: explain what had already happened before \
something else; for polite requests: ask several people for things).

Write exactly {count} different tasks, all in the given context and at the \
given level.
- `question`: 1-3 plain English sentences - the situation and what to talk \
about (a role-play or their own experience).
- `hint`: a short Russian label, 2-6 words.
- `use`: one Russian sentence naming what to use, with a tiny English \
example (for example "Past Perfect для того, что случилось раньше: \
I had already left when...")."""


def _grading_prompt() -> str:
    return GRADING_PROMPT.format(taxonomy=taxonomy_prompt_lines(indent="  "))


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
        theme: Optional[Dict[str, Optional[str]]] = None,
        lesson: Optional[Dict[str, Any]] = None,
        known_words: Sequence[str] = (),
    ) -> GenerationResult:
        """A new set on `topic` ({"key", "label", "description"}).

        `seeds` are the learner's own bank items on the topic (fix / pattern),
        `avoid` sentences from earlier sets on it, `theme` the context the
        sentences are set in (None: the default, IT / backend), `lesson`
        the roadmap lesson it is for - {"level", "theory"} (theory: the
        content of its latest version, or None) - so a lesson's set trains
        exactly what its theory taught, at its level. `known_words` are the
        words and phrases the learner already has as cards, so the set's
        vocabulary suggests new ones.
        """
        self._require_key()
        system = GENERATION_PROMPT.format(
            gaps=config.SET_GAPS,
            fixes=config.SET_FIXES,
            translations=config.SET_TRANSLATIONS,
            vocabulary=config.SET_VOCABULARY,
        )
        user = _generation_request(topic, seeds, avoid, theme, lesson, known_words)
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
            vocabulary=assemble_vocabulary(parsed),
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
            system=_grading_prompt(),
            messages=[{"role": "user", "content": "\n".join(lines)}],
            output_format=Grading,
            output_config={"effort": config.GRADING_EFFORT},
            thinking={"type": "adaptive"},
        )
        verdicts: Dict[str, Dict[str, Any]] = {}
        for verdict in response.parsed_output.verdicts:
            if 1 <= verdict.number <= len(answers):
                verdicts[answers[verdict.number - 1].exercise_id] = {
                    "correct": verdict.correct,
                    "comment": verdict.comment.strip(),
                    "corrected": verdict.corrected.strip(),
                    "topic": verdict.topic,
                }
        return GradingResult(
            verdicts=verdicts,
            call=_call_info(response, config.GRADING_MODEL, config.GRADING_EFFORT),
        )

    def write_speaking_prompts(self, theme_label: str) -> PromptsResult:
        """Monologue / «60 секунд» prompts for the learner's own context
        (built-in contexts have hand-written ones in app.themes)."""
        self._require_key()
        response = self._call(
            model=config.THEME_PROMPTS_MODEL,
            max_tokens=config.THEME_PROMPTS_MAX_TOKENS,
            system=SPEAKING_PROMPTS_PROMPT.format(count=config.THEME_PROMPTS_COUNT),
            messages=[{"role": "user", "content": f"Context: {theme_label}"}],
            output_format=SpeakingPrompts,
        )
        prompts = [
            {"question": p.question.strip(), "hint": p.hint.strip()}
            for p in response.parsed_output.prompts
            if p.question.strip() and p.hint.strip()
        ][: config.THEME_PROMPTS_COUNT]
        if not prompts:
            raise AnalysisError("Claude не придумал ни одной темы. Попробуйте ещё раз.")
        return PromptsResult(
            prompts=prompts, call=_call_info(response, config.THEME_PROMPTS_MODEL)
        )

    def write_module_test(
        self,
        module_title: str,
        lessons: Sequence[Dict[str, Any]],
        avoid: Sequence[str] = (),
    ) -> ModuleTestResult:
        """A module's entry test: one choice and one gap per lesson
        (`lessons`: curriculum.topic_info dicts). The raw ModuleTest comes
        back as a dict; app.module_test checks and assembles it."""
        self._require_key()
        lines = [f"Module: {module_title}", "", "Lessons (key - topic - typical mistakes):"]
        lines += [
            f"- {lesson['key']} - {lesson['label']} ({(lesson.get('level') or 'b1').upper()})"
            f" - {lesson.get('description', '')}"
            for lesson in lessons
        ]
        if avoid:
            lines += ["", "Sentences used before - do not reuse them:"]
            lines += [f"- {sentence}" for sentence in avoid]
        lines += ["", "Write the test."]
        response = self._call(
            model=config.MODULE_TEST_MODEL,
            max_tokens=config.MODULE_TEST_MAX_TOKENS,
            system=MODULE_TEST_PROMPT,
            messages=[{"role": "user", "content": "\n".join(lines)}],
            output_format=ModuleTest,
            output_config={"effort": config.MODULE_TEST_EFFORT},
            thinking={"type": "adaptive"},
        )
        return ModuleTestResult(
            test=response.parsed_output.model_dump(),
            call=_call_info(response, config.MODULE_TEST_MODEL, config.MODULE_TEST_EFFORT),
        )

    def write_lesson_tasks(
        self,
        topic: Dict[str, Any],
        level: Optional[str],
        theme: Optional[Dict[str, Optional[str]]] = None,
    ) -> PromptsResult:
        """Spoken tasks for a roadmap lesson: each makes the learner use the
        lesson's construction, in the chosen context. prompts: [{"question",
        "hint", "use"}]."""
        self._require_key()
        request = "\n".join(
            [
                f"Lesson topic: {topic['label']} ({topic['key']}) - {topic.get('description', '')}",
                f"Level: {_LEVEL_NAMES.get(level or '', 'B1')}",
                f"Context: {themes.model_context(theme)}",
            ]
        )
        response = self._call(
            model=config.LESSON_TASK_MODEL,
            max_tokens=config.LESSON_TASK_MAX_TOKENS,
            system=LESSON_TASKS_PROMPT.format(count=config.LESSON_TASKS_PER_CALL),
            messages=[{"role": "user", "content": request}],
            output_format=LessonTasks,
            output_config={"effort": config.LESSON_TASK_EFFORT},
            thinking={"type": "adaptive"},
        )
        tasks = [
            {"question": t.question.strip(), "hint": t.hint.strip(), "use": t.use.strip()}
            for t in response.parsed_output.tasks
            if t.question.strip() and t.hint.strip()
        ][: config.LESSON_TASKS_PER_CALL]
        if not tasks:
            raise AnalysisError("Claude не придумал ни одного задания. Попробуйте ещё раз.")
        return PromptsResult(
            prompts=tasks,
            call=_call_info(response, config.LESSON_TASK_MODEL, config.LESSON_TASK_EFFORT),
        )

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
    topic: Dict[str, str],
    seeds: Sequence[Dict[str, Any]],
    avoid: Sequence[str],
    theme: Optional[Dict[str, Optional[str]]] = None,
    lesson: Optional[Dict[str, Any]] = None,
    known_words: Sequence[str] = (),
) -> str:
    lines = [
        f"Topic: {topic['label']} ({topic['key']}) - {topic.get('description', '')}",
        f"Context: {themes.model_context(theme)}",
        "",
    ]
    if lesson:
        lines += _lesson_lines(lesson)
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
    if known_words:
        lines += ["", "Words and phrases the learner already has as cards - suggest other ones:"]
        lines.append("; ".join(known_words))
    lines += ["", "Write the set."]
    return "\n".join(lines)


_LEVEL_NAMES: Dict[str, str] = {
    "a2": "A2 (elementary)", "b1": "B1 (intermediate)",
    "b2": "B2 (upper-intermediate)", "c1": "C1 (advanced)",
}


def _lesson_lines(lesson: Dict[str, Any]) -> List[str]:
    """The lesson part of a set request: its level and what its theory taught."""
    level = _LEVEL_NAMES.get(lesson.get("level") or "", "B1")
    lines = [f"This set is for a course lesson at level {level}: keep sentences at that level."]
    theory = lesson.get("theory")
    if theory:
        lines.append("The lesson's theory the learner has read - train exactly these points:")
        lines.append(f"- {theory.get('summary', '')}")
        for section in theory.get("sections") or []:
            examples = "; ".join(e.get("english", "") for e in (section.get("examples") or [])[:2])
            heading, text = section.get("heading", ""), section.get("text", "")
            lines.append(f"- {heading}: {text} (e.g. {examples})")
        for point in theory.get("remember") or []:
            lines.append(f"- remember: {point}")
        lines.append("Do not copy the theory's example sentences.")
    lines.append("")
    return lines


def exercise_text(exercise: Dict[str, Any]) -> str:
    """The sentence an exercise shows: the Russian of a translation, the
    English of a fix, or a gap sentence with its blank."""
    text = exercise.get("russian") or exercise.get("sentence")
    if not text:
        text = f"{exercise.get('before', '')}{GAP_MARK}{exercise.get('after', '')}"
    return text.strip()


def drop_repeats(
    exercises: List[Dict[str, Any]], earlier: Sequence[str]
) -> List[Dict[str, Any]]:
    """Exercises whose sentence an earlier set on the topic already had are
    dropped (case, spacing and punctuation aside), and ids renumbered - the
    prompt asks for new sentences, this makes sure of it."""
    seen = {_sentence_key(text) for text in earlier}
    kept = []
    for exercise in exercises:
        key = _sentence_key(exercise_text(exercise))
        if key not in seen:
            seen.add(key)
            kept.append(exercise)
    for number, exercise in enumerate(kept, start=1):
        exercise["id"] = f"ex{number}"
    return kept


def _sentence_key(text: str) -> str:
    return " ".join("".join(c.lower() if c.isalnum() or c == "_" else " " for c in text).split())


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


def assemble_vocabulary(parsed: GeneratedSet) -> List[Dict[str, str]]:
    """The set's word card candidates with stable ids (v1, v2...).

    Entries without an English or a Russian side are dropped, and so are
    formulas ("have + V3") and repeats of the same English: a card needs one
    phrase to recall from one Russian prompt.
    """
    vocabulary: List[Dict[str, str]] = []
    seen = set()
    for entry in parsed.vocabulary:
        english, russian = entry.english.strip(), entry.russian.strip()
        key = _sentence_key(english)
        if not key or not russian or "+" in english or key in seen:
            continue
        seen.add(key)
        vocabulary.append(
            {
                "english": english,
                "russian": russian,
                "example": entry.example.strip(),
                "example_russian": entry.example_russian.strip(),
                "note": entry.note.strip(),
            }
        )
        if len(vocabulary) == config.SET_VOCABULARY:
            break
    for number, entry in enumerate(vocabulary, start=1):
        entry["id"] = f"v{number}"
    return vocabulary
