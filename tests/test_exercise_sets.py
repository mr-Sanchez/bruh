"""Offline tests for app.exercise_sets: set generation and grading with a fake client."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.analyzer import AnalysisError, MissingAnthropicApiKeyError  # noqa: E402
from app.exercise_sets import (  # noqa: E402
    ExerciseSetGenerator,
    FixExercise,
    GapExercise,
    GeneratedSet,
    Grading,
    TranslateExercise,
    TranslationAnswer,
    Verdict,
    assemble_exercises,
)

TOPIC = {"key": "present_perfect", "label": "Времена", "description": "время глагола"}


def sample_set() -> GeneratedSet:
    return GeneratedSet(
        intro="Прошедшее время для законченных действий.",
        gaps=[
            GapExercise(sentence="Yesterday we ___ the release.", answers=["shipped"], hint="(ship)",
                        explanation="Вчера - Past Simple."),
            GapExercise(sentence="No blank here.", answers=["x"], explanation="сломано"),
            GapExercise(sentence="Two ___ blanks ___.", answers=["x"], explanation="сломано"),
        ],
        fixes=[
            FixExercise(
                sentence="I go to the office yesterday.",
                correction="I went to the office yesterday.",
                also_accepted=["I went into the office yesterday."],
                explanation="Past Simple.",
            ),
        ],
        translations=[
            TranslateExercise(
                russian="Я уже починил баг.",
                reference="I have already fixed the bug.",
                focus="Present Perfect",
            ),
        ],
    )


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
            usage=SimpleNamespace(input_tokens=1000, output_tokens=2000,
                                  cache_creation_input_tokens=None, cache_read_input_tokens=0),
        )


def generator_with(parsed: Any) -> tuple:
    messages = FakeMessages(parsed)
    client = SimpleNamespace(messages=messages)
    return ExerciseSetGenerator("test-key", client_factory=lambda key: client), messages


class AssembleTests(unittest.TestCase):
    def test_broken_gaps_are_dropped_and_ids_follow_the_running_order(self) -> None:
        exercises = assemble_exercises(sample_set())
        self.assertEqual([e["type"] for e in exercises], ["gap", "fix", "translate"])
        self.assertEqual([e["id"] for e in exercises], ["ex1", "ex2", "ex3"])
        gap = exercises[0]
        self.assertEqual((gap["before"], gap["after"]), ("Yesterday we ", " the release."))
        self.assertEqual(exercises[1]["accept"][0], "I went to the office yesterday.")
        self.assertEqual(len(exercises[1]["accept"]), 2)

    def test_lists_are_capped_to_the_configured_counts(self) -> None:
        many = GeneratedSet(
            intro="",
            translations=[
                TranslateExercise(russian=f"Фраза {i}", reference=f"Sentence {i}", focus="x")
                for i in range(10)
            ],
        )
        self.assertEqual(len(assemble_exercises(many)), config.SET_TRANSLATIONS)


class GenerateTests(unittest.TestCase):
    def test_generate_builds_the_request_from_seeds_and_avoid_list(self) -> None:
        generator, messages = generator_with(sample_set())
        seeds = [
            {"kind": "fix", "content": {"quote": "I go there", "correction": "I went there"}},
            {
                "kind": "pattern",
                "content": {"rule": "yesterday + Past Simple", "examples": ["I saw it."]},
            },
        ]
        result = generator.generate(TOPIC, seeds, avoid=["Old sentence."])

        call = messages.calls[0]
        self.assertEqual(call["model"], config.EXERCISE_SET_MODEL)
        self.assertEqual(call["output_config"], {"effort": config.EXERCISE_SET_EFFORT})
        self.assertIs(call["output_format"], GeneratedSet)
        request = call["messages"][0]["content"]
        self.assertIn('said: "I go there" -> correct: "I went there"', request)
        self.assertIn("rule: yesterday + Past Simple (e.g. I saw it.)", request)
        self.assertIn("- Old sentence.", request)
        self.assertEqual(len(result.exercises), 3)
        self.assertEqual(result.call.usage["output_tokens"], 2000)
        self.assertEqual(result.call.usage["cache_creation_input_tokens"], 0)

    def test_generate_without_a_key_or_with_nothing_usable_fails_clearly(self) -> None:
        with self.assertRaises(MissingAnthropicApiKeyError):
            ExerciseSetGenerator(None).generate(TOPIC, [])
        generator, _ = generator_with(GeneratedSet(intro="", gaps=[
            GapExercise(sentence="no blank", answers=["x"], explanation="")
        ]))
        with self.assertRaises(AnalysisError):
            generator.generate(TOPIC, [])


class GradeTests(unittest.TestCase):
    def test_grade_maps_verdicts_back_to_exercise_ids(self) -> None:
        grading = Grading(verdicts=[
            Verdict(number=2, correct=False, comment="Нужно Present Perfect.",
                    corrected="I have fixed it.", topic="present_perfect"),
            Verdict(number=1, correct=True, comment="Верно.", corrected="It works.",
                    topic="present_perfect"),
            Verdict(number=7, correct=True, comment="лишний", corrected="", topic="other"),
        ])
        generator, messages = generator_with(grading)
        answers = [
            TranslationAnswer("ex5", "Работает.", "It works.", "Present Simple", "It works."),
            TranslationAnswer(
                "ex6", "Я починил.", "I have fixed it.", "Present Perfect", "I fixed it."
            ),
        ]
        result = generator.grade(TOPIC, answers)

        call = messages.calls[0]
        self.assertEqual(call["model"], config.GRADING_MODEL)
        self.assertEqual(call["output_config"], {"effort": config.GRADING_EFFORT})
        self.assertIn('"sentence_structure"', call["system"])  # the taxonomy to tag from
        self.assertNotIn("{taxonomy}", call["system"])
        self.assertIn("Learner: I fixed it.", call["messages"][0]["content"])
        self.assertEqual(set(result.verdicts), {"ex5", "ex6"})
        self.assertFalse(result.verdicts["ex6"]["correct"])
        self.assertEqual(result.verdicts["ex6"]["corrected"], "I have fixed it.")
        self.assertEqual(result.verdicts["ex6"]["topic"], "present_perfect")
        self.assertEqual(result.call.effort, config.GRADING_EFFORT)

    def test_a_verdict_topic_outside_the_taxonomy_is_rejected_by_the_schema(self) -> None:
        with self.assertRaises(ValueError):
            Verdict(number=1, correct=False, comment="", corrected="", topic="there_is")


if __name__ == "__main__":
    unittest.main()
