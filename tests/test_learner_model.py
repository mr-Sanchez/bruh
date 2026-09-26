"""Offline tests for the learner model's pure rules (no files, no clock)."""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, learner_model  # noqa: E402
from app.learner_model import KIND_FIX, KIND_PATTERN, KIND_PHRASE  # noqa: E402
from app.utils import AnalysedSession  # noqa: E402


def session(
    session_id: str, when: dt.datetime, analysis: Dict[str, Any], language: str = "en-US"
) -> AnalysedSession:
    return AnalysedSession(session_id, when, language, analysis)


def issue(
    correction: str,
    *,
    quote: str = "some wrong words",
    topic: str = "prepositions",
    rule: str = "",
    drills: Any = None,
) -> Dict[str, Any]:
    return {
        "topic": topic,
        "quote": quote,
        "explanation": "Объяснение.",
        "correction": correction,
        "better_versions": [],
        "pattern": {"rule": rule, "examples": ["Example."]} if rule else None,
        "severity": "moderate",
        "drills": [DRILL] if drills is None else drills,
    }


DRILL = {"russian": "Я отвечаю за бэкенд.", "english": "I'm responsible for the backend."}


def phrase(text: str) -> Dict[str, str]:
    return {"phrase": text, "meaning": "значение", "example": f"Use {text}."}


def attempt(when: dt.datetime, correct: bool) -> Dict[str, Any]:
    return {"ts": when.isoformat(), "correct": correct}


DAY1 = dt.datetime(2026, 9, 1, 10, 0)


def day(n: int, hour: int = 12) -> dt.datetime:
    return dt.datetime(2026, 9, n, hour, 0)


class IdentityTests(unittest.TestCase):
    def test_item_id_ignores_case_quotes_spacing_and_edge_punctuation(self) -> None:
        a = learner_model.item_id(KIND_PHRASE, "  I’m responsible  FOR it. ")
        b = learner_model.item_id(KIND_PHRASE, "i'm responsible for it")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("phr-"))

    def test_item_id_differs_by_kind(self) -> None:
        self.assertNotEqual(
            learner_model.item_id(KIND_FIX, "go to work"),
            learner_model.item_id(KIND_PHRASE, "go to work"),
        )


class BankTests(unittest.TestCase):
    def test_issue_yields_a_fix_linked_to_its_pattern(self) -> None:
        analysis = {
            "issues": [
                issue("responsible for the backend", rule="responsible for + noun"),
            ]
        }
        items = learner_model.items_from_analysis(session("s1", DAY1, analysis))
        kinds = {item["kind"]: item for item in items}
        self.assertEqual(set(kinds), {KIND_FIX, KIND_PATTERN})
        self.assertEqual(kinds[KIND_FIX]["pattern_id"], kinds[KIND_PATTERN]["id"])
        self.assertEqual(kinds[KIND_FIX]["topic"], "prepositions")
        self.assertEqual(
            kinds[KIND_FIX]["occurrences"], [{"session_id": "s1", "at": DAY1.isoformat()}]
        )
        self.assertEqual(kinds[KIND_FIX]["content"]["drills"], [DRILL])
        self.assertEqual(kinds[KIND_FIX]["content"]["focus"], "responsible for + noun")
        self.assertFalse(learner_model.is_retired(kinds[KIND_FIX]))

    def test_a_fix_without_practice_sentences_is_retired(self) -> None:
        # Analyses from before 2026-09-26 (and non-English mistakes) have no
        # drills: the item stays as history but is never shown as a card.
        analysis = {"issues": [issue("went home", drills=[]), issue("go to work", drills=[
            {"russian": "", "english": "Half a drill."}, {"russian": "Иду.", "english": "I go."},
        ])]}
        old, new = learner_model.items_from_analysis(session("s1", DAY1, analysis))
        self.assertTrue(learner_model.is_retired(old))
        self.assertEqual(new["content"]["drills"], [{"russian": "Иду.", "english": "I go."}])
        self.assertFalse(learner_model.is_retired({"kind": KIND_PHRASE, "content": {}}))

    def test_delivery_topics_and_no_op_fixes_do_not_become_cards(self) -> None:
        analysis = {
            "issues": [
                issue("I think", quote="uh, I, I think", topic="filler_words_fluency"),
                issue("I think", quote="I think", topic="repetition_self_correction"),
                issue("same words", quote="Same words."),
            ]
        }
        self.assertEqual(learner_model.items_from_analysis(session("s1", DAY1, analysis)), [])

    def test_same_phrase_across_recordings_merges_into_one_item(self) -> None:
        first = session("s1", DAY1, {"vocabulary": [phrase("recruiter")]})
        second = session(
            "s2",
            day(5),
            {"takeaways": [{**phrase("Recruiter"), "meaning": "новое значение"}]},
        )
        bank = learner_model.build_bank([first, second])
        self.assertEqual(len(bank), 1)
        item = next(iter(bank.values()))
        self.assertEqual([o["session_id"] for o in item["occurrences"]], ["s1", "s2"])
        self.assertEqual(item["sources"], ["takeaways", "vocabulary"])
        self.assertEqual(item["content"]["meaning"], "новое значение")

    def test_same_phrase_twice_in_one_recording_is_one_occurrence(self) -> None:
        analysis = {"vocabulary": [phrase("employer")], "takeaways": [phrase("employer")]}
        bank = learner_model.build_bank([session("s1", DAY1, analysis)])
        (item,) = bank.values()
        self.assertEqual(len(item["occurrences"]), 1)


class LeitnerTests(unittest.TestCase):
    def _item(self, *occurrence_days: dt.datetime, language: str = "en-US") -> Dict[str, Any]:
        return {
            "id": "phr-test",
            "language": language,
            "occurrences": [
                {"session_id": f"s{i}", "at": when.isoformat()}
                for i, when in enumerate(occurrence_days)
            ],
        }

    def _state(
        self,
        item: Dict[str, Any],
        attempts: List[Dict[str, Any]],
        sessions: List[Dict[str, Any]] = (),
    ) -> learner_model.ItemState:
        return learner_model.item_state(item, attempts, list(sessions))

    def test_new_item_is_due_on_the_day_it_was_spoken(self) -> None:
        state = self._state(self._item(DAY1), [])
        self.assertTrue(state.is_new)
        self.assertEqual(state.box, 1)
        self.assertEqual(state.due, "2026-09-01")
        self.assertTrue(state.is_due(dt.date(2026, 9, 1)))

    def test_correct_answers_climb_the_boxes_with_growing_intervals(self) -> None:
        attempts = [attempt(day(1), True), attempt(day(3), True), attempt(day(7), True)]
        state = self._state(self._item(DAY1), attempts)
        self.assertEqual(state.box, 4)
        self.assertEqual(state.due, "2026-09-15")  # day 7 + 8 days
        self.assertEqual((state.attempts, state.correct), (3, 3))

    def test_early_correct_answer_does_not_advance(self) -> None:
        attempts = [attempt(day(1), True), attempt(day(2), True)]  # due again on day 3
        state = self._state(self._item(DAY1), attempts)
        self.assertEqual(state.box, 2)
        self.assertEqual(state.due, "2026-09-03")

    def test_wrong_answer_goes_back_to_box_one(self) -> None:
        attempts = [attempt(day(1), True), attempt(day(3), True), attempt(day(7), False)]
        state = self._state(self._item(DAY1), attempts)
        self.assertEqual(state.box, 1)
        self.assertEqual(state.due, "2026-09-08")

    def test_mistake_reappearing_in_new_speech_resets_to_box_one(self) -> None:
        attempts = [attempt(day(1), True), attempt(day(3), True)]
        state = self._state(self._item(DAY1, day(4)), attempts)
        self.assertEqual(state.box, 1)
        self.assertEqual(state.due, "2026-09-04")
        self.assertEqual(state.reset_by_speech_at, day(4).isoformat())

    def test_older_recording_analysed_later_does_not_reset(self) -> None:
        # Spoken on day 1 before the first practice, but analysed afterwards -
        # it says nothing new about whether the practice worked.
        attempts = [attempt(day(1), True), attempt(day(3), True)]
        state = self._state(self._item(DAY1, day(1, hour=11)), attempts)
        self.assertEqual(state.box, 3)
        self.assertIsNone(state.reset_by_speech_at)

    def _to_box_five(self) -> List[Dict[str, Any]]:
        return [
            attempt(day(1), True),  # -> 2, due day 3
            attempt(day(3), True),  # -> 3, due day 7
            attempt(day(7), True),  # -> 4, due day 15
            attempt(day(15), True),  # -> 5, due day 31
        ]

    def test_item_closes_after_three_clean_recordings_in_its_language(self) -> None:
        sessions = [
            {"recorded_at": day(16).isoformat(), "language": "en-US"},
            {"recorded_at": day(17).isoformat(), "language": "ru"},  # does not count
            {"recorded_at": day(18).isoformat(), "language": "multi"},
        ]
        state = self._state(self._item(DAY1), self._to_box_five(), sessions)
        self.assertEqual(state.box, 5)
        self.assertFalse(state.closed)

        sessions.append({"recorded_at": day(19).isoformat(), "language": "en-US"})
        state = self._state(self._item(DAY1), self._to_box_five(), sessions)
        self.assertTrue(state.closed)
        self.assertIsNone(state.due)
        self.assertFalse(state.is_due(dt.date(2026, 12, 31)))


class TopicMasteryTests(unittest.TestCase):
    def test_priority_scales_speech_weakness_by_drill_accuracy(self) -> None:
        bank = {
            "fix-a": {"id": "fix-a", "topic": "articles"},
            "fix-b": {"id": "fix-b", "topic": "prepositions"},
        }
        attempts = [
            {"item_id": "fix-a", "ts": day(2).isoformat(), "correct": True},
            {"item_id": "fix-a", "ts": day(3).isoformat(), "correct": True},
            {"item_id": "fix-b", "ts": day(2).isoformat(), "correct": False},
        ]
        rows = learner_model.topic_mastery(
            {"articles": 4.0, "prepositions": 2.0, "verb_tense": 1.0},
            bank,
            attempts,
            {},
            dt.date(2026, 9, 4),
        )
        by_key = {row["key"]: row for row in rows}
        self.assertEqual(by_key["articles"]["accuracy"], 1.0)
        self.assertAlmostEqual(by_key["articles"]["priority"], 2.0)  # 4 * 0.5
        self.assertAlmostEqual(by_key["prepositions"]["priority"], 3.0)  # 2 * 1.5
        self.assertIsNone(by_key["verb_tense"]["accuracy"])
        self.assertAlmostEqual(by_key["verb_tense"]["priority"], 1.0)
        self.assertEqual([row["key"] for row in rows], ["prepositions", "articles", "verb_tense"])

    def test_topic_drills_count_towards_accuracy_by_their_score(self) -> None:
        bank = {"fix-a": {"id": "fix-a", "topic": "articles"}}
        attempts = [
            {"item_id": "fix-a", "ts": day(2).isoformat(), "correct": True},
            {"topic": "articles", "ts": day(3).isoformat(), "correct": False, "score": 0.5},
        ]
        rows = learner_model.topic_mastery({"articles": 2.0}, bank, attempts, {}, day(4).date())
        self.assertEqual(rows[0]["recent_attempts"], 2)
        self.assertAlmostEqual(rows[0]["accuracy"], 0.75)
        self.assertAlmostEqual(rows[0]["priority"], 1.5)  # 2 * (1.5 - 0.75)


class DailyQueueTests(unittest.TestCase):
    TODAY = dt.date(2026, 9, 10)

    def _bank(self, fixes: int, phrases: int, topic: str = "articles") -> Dict[str, Any]:
        bank = {}
        for n in range(fixes):
            bank[f"fix-{n:02d}"] = {
                "id": f"fix-{n:02d}",
                "kind": KIND_FIX,
                "topic": topic,
                "severity": "moderate",
                "content": {"drills": [DRILL]},
                "occurrences": [{"session_id": "s1", "at": day(1).isoformat()}],
            }
        for n in range(phrases):
            bank[f"phr-{n:02d}"] = {
                "id": f"phr-{n:02d}",
                "kind": KIND_PHRASE,
                "topic": None,
                "occurrences": [{"session_id": "s1", "at": day(1).isoformat()}],
            }
        return bank

    def _states(self, bank: Dict[str, Any], attempts=None) -> Dict[str, Any]:
        attempts = attempts or {}
        return {
            item_id: learner_model.item_state(item, attempts.get(item_id, []), [])
            for item_id, item in bank.items()
        }

    def test_new_cards_are_capped_and_interleave_phrases(self) -> None:
        bank = self._bank(fixes=20, phrases=20)
        queue = learner_model.daily_queue(bank, self._states(bank), {}, self.TODAY)
        self.assertEqual(queue["new_limit"], 10)
        self.assertEqual(len(queue["new"]), 10)
        kinds = [bank[i]["kind"] for i in queue["new"]]
        self.assertEqual(kinds[:3], [KIND_FIX, KIND_FIX, KIND_PHRASE])
        self.assertEqual(queue["new_waiting"], 30)
        self.assertEqual(queue["reviews"], [])

    def test_cards_started_today_use_up_the_allowance(self) -> None:
        bank = self._bank(fixes=20, phrases=0)
        today_noon = dt.datetime(2026, 9, 10, 12)
        attempts = {f"fix-{n:02d}": [attempt(today_noon, True)] for n in range(4)}
        queue = learner_model.daily_queue(bank, self._states(bank, attempts), {}, self.TODAY)
        self.assertEqual(queue["new_started_today"], 4)
        self.assertEqual(len(queue["new"]), 6)

    def test_review_backlog_lowers_the_allowance(self) -> None:
        bank = self._bank(fixes=50, phrases=0)
        # 31 cards answered wrong yesterday are due for review today.
        yesterday = dt.datetime(2026, 9, 9, 12)
        attempts = {f"fix-{n:02d}": [attempt(yesterday, False)] for n in range(31)}
        queue = learner_model.daily_queue(bank, self._states(bank, attempts), {}, self.TODAY)
        self.assertEqual(len(queue["reviews"]), 31)
        self.assertEqual(queue["new_limit"], 7)
        self.assertEqual(len(queue["new"]), 7)

    def test_new_cards_from_higher_priority_topics_come_first(self) -> None:
        bank = {**self._bank(fixes=1, phrases=0, topic="articles")}
        bank["fix-zz"] = {**bank["fix-00"], "id": "fix-zz", "topic": "verb_tense"}
        queue = learner_model.daily_queue(
            bank, self._states(bank), {"articles": 1.0, "verb_tense": 5.0}, self.TODAY
        )
        self.assertEqual(queue["new"], ["fix-zz", "fix-00"])

    def test_retired_fixes_never_reach_the_queue(self) -> None:
        bank = self._bank(fixes=2, phrases=0)
        bank["fix-01"]["content"] = {"drills": []}
        yesterday = dt.datetime(2026, 9, 9, 12)
        attempts = {"fix-01": [attempt(yesterday, False)]}  # would be due today
        queue = learner_model.daily_queue(bank, self._states(bank, attempts), {}, self.TODAY)
        self.assertEqual((queue["reviews"], queue["new"], queue["new_waiting"]), ([], ["fix-00"], 0))


class CardExerciseTests(unittest.TestCase):
    def _card(self, item: Dict[str, Any], attempts=()) -> Dict[str, Any]:
        item = {"id": "x", "occurrences": [{"session_id": "s", "at": DAY1.isoformat()}], **item}
        state = learner_model.item_state(item, list(attempts), [])
        return learner_model.card_exercise(item, state)

    def _fix(self, drills) -> Dict[str, Any]:
        return {
            "kind": KIND_FIX,
            "content": {"quote": "q", "correction": "c", "focus": "for + noun", "drills": drills},
        }

    def test_fix_is_a_translation_that_moves_to_the_next_sentence_each_attempt(self) -> None:
        drills = [DRILL, {"russian": "Она ждёт ответа.", "english": "She is waiting for an answer."}]
        first = self._card(self._fix(drills))
        self.assertEqual(
            first,
            {
                "type": "translate",
                "drill": 0,
                "russian": DRILL["russian"],
                "reference": DRILL["english"],
                "focus": "for + noun",
            },
        )
        second = self._card(self._fix(drills), [attempt(day(2), False)])
        self.assertEqual((second["drill"], second["russian"]), (1, "Она ждёт ответа."))
        third = self._card(self._fix(drills), [attempt(day(2), False), attempt(day(3), True)])
        self.assertEqual(third["drill"], 0)

    def test_fix_without_sentences_falls_back_to_self_grading(self) -> None:
        self.assertEqual(self._card(self._fix([])), {"type": "self"})

    def test_pattern_is_self_graded(self) -> None:
        pattern = {"kind": KIND_PATTERN, "content": {"rule": "help + verb", "examples": []}}
        self.assertEqual(self._card(pattern), {"type": "self"})

    def test_phrase_found_in_its_example_becomes_a_gap(self) -> None:
        item = {
            "kind": KIND_PHRASE,
            "content": {
                "phrase": "business analyst",
                "meaning": "бизнес-аналитик",
                "example": "I'll ask the business analysts to help.",
            },
        }
        self.assertEqual(
            self._card(item),
            {
                "type": "gap",
                "before": "I'll ask the ",
                "after": " to help.",
                "accept": ["business analysts", "business analyst"],
            },
        )

    def test_phrase_gap_rules(self) -> None:
        gap = learner_model.phrase_gap("research (verb)", "Let me Research it.")
        self.assertEqual(gap["accept"], ["Research"])
        self.assertIsNone(learner_model.phrase_gap("help + verb-ing", "Help me find it."))
        self.assertIsNone(learner_model.phrase_gap("recruiter", "An employer hired me."))
        # "a" must not match inside "about"
        self.assertEqual(learner_model.phrase_gap("a", "Think about a plan.")["before"], "Think about ")


class WorkoutTests(unittest.TestCase):
    def test_streak_counts_up_to_yesterday_until_today_is_active(self) -> None:
        today = dt.date(2026, 9, 10)
        active = {dt.date(2026, 9, 8), dt.date(2026, 9, 9)}
        self.assertEqual(learner_model.activity_streak(active, today), 2)
        self.assertEqual(learner_model.activity_streak(active | {today}, today), 3)
        self.assertEqual(learner_model.activity_streak({dt.date(2026, 9, 8)}, today), 0)
        self.assertEqual(learner_model.activity_streak(set(), today), 0)

    def test_prompt_changes_daily_and_stays_in_range(self) -> None:
        first = learner_model.speaking_prompt_index(dt.date(2026, 9, 1))
        second = learner_model.speaking_prompt_index(dt.date(2026, 9, 2))
        self.assertNotEqual(first, second)
        self.assertTrue(0 <= first < len(config.SPEAKING_PROMPTS))

    def test_daily_activity_sums_cards_and_lists_drills(self) -> None:
        attempts = [
            {"ts": day(1, 9).isoformat(), "item_id": "a", "correct": True},
            {"ts": day(1, 10).isoformat(), "item_id": "b", "correct": False},
            {"ts": day(1, 11).isoformat(), "topic": "articles", "exercise": "ai_set",
             "score": 0.75, "correct": False},
            {"ts": day(3).isoformat(), "item_id": "a", "correct": True},
        ]
        history = learner_model.daily_activity(attempts, days=10)
        self.assertEqual([d["date"] for d in history], ["2026-09-03", "2026-09-01"])
        self.assertEqual((history[1]["cards"], history[1]["cards_correct"]), (2, 1))
        self.assertEqual(history[1]["drills"][0]["score"], 0.75)
        self.assertEqual(len(learner_model.daily_activity(attempts, days=1)), 1)


if __name__ == "__main__":
    unittest.main()


def exercise_set(results: List[Dict[str, Any]]) -> tuple:
    data = {
        "id": "set-20260901-120000",
        "topic": "verb_tense",
        "language": "en",
        "exercises": [
            {"id": "ex1", "type": "gap", "before": "Yesterday we ", "after": " it.",
             "accept": ["shipped"], "explanation": "Past Simple."},
            {"id": "ex2", "type": "fix", "sentence": "I go there yesterday.",
             "accept": ["I went there yesterday.", "I went over there yesterday."],
             "explanation": "Past Simple."},
            {"id": "ex3", "type": "translate", "russian": "Я уже починил баг.",
             "reference": "I have already fixed the bug.", "focus": "Present Perfect"},
            {"id": "ex4", "type": "translate", "russian": "Мы созвонились.",
             "reference": "We had a call.", "focus": "Past Simple"},
        ],
    }
    run = {"at": day(2).isoformat(), "results": results}
    return data, run


class ExerciseSetTests(unittest.TestCase):
    def test_answers_are_compared_without_case_or_punctuation(self) -> None:
        self.assertEqual(
            learner_model.normalize_answer("  It’s DONE, isn't it?! "), "it's done isn't it"
        )
        self.assertTrue(
            learner_model.answer_matches("i went there yesterday", ["I went there yesterday."])
        )
        self.assertFalse(learner_model.answer_matches("", [""]))
        self.assertAlmostEqual(
            learner_model.set_run_score([{"correct": True}, {"correct": False}]), 0.5
        )

    def test_only_topics_a_set_can_train_are_allowed(self) -> None:
        self.assertTrue(learner_model.set_topic_allowed("articles"))
        for topic in ("filler_words_fluency", "repetition_self_correction", "other", "nope"):
            self.assertFalse(learner_model.set_topic_allowed(topic))

    def test_wrong_answers_become_fix_cards_of_the_learners_own_sentences(self) -> None:
        data, run = exercise_set([
            {"exercise_id": "ex1", "answer": "ship", "correct": False},
            {"exercise_id": "ex2", "answer": "I go there yesterday.", "correct": False},
            {"exercise_id": "ex3", "answer": "I already fixed the bug.", "correct": False,
             "comment": "Нужно Present Perfect.", "corrected": "I have already fixed the bug."},
            {"exercise_id": "ex4", "answer": "", "correct": False, "corrected": "We had a call."},
        ])
        items = learner_model.items_from_set_run(data, run)

        self.assertEqual(len(items), 3)  # the blank translation makes no card
        gap, fix, translation = items
        self.assertEqual(gap["content"]["quote"], "Yesterday we ship it.")
        self.assertEqual(gap["content"]["correction"], "Yesterday we shipped it.")
        self.assertEqual(fix["content"]["better_versions"], ["I went over there yesterday."])
        self.assertEqual(translation["content"]["quote"], "I already fixed the bug.")
        self.assertEqual(translation["content"]["correction"], "I have already fixed the bug.")
        self.assertEqual(translation["content"]["better_versions"], [])  # same as the reference
        self.assertEqual(translation["content"]["explanation"], "Нужно Present Perfect.")
        # Only a translation has a Russian sentence to practise with: it is the
        # live card; the gap and fix mistakes are kept as retired history.
        self.assertEqual(
            translation["content"]["drills"],
            [{"russian": "Я уже починил баг.", "english": "I have already fixed the bug."}],
        )
        self.assertEqual(translation["content"]["focus"], "Present Perfect")
        self.assertEqual([learner_model.is_retired(i) for i in items], [True, True, False])
        for item in items:
            self.assertEqual(
                (item["kind"], item["topic"], item["origin"]), (KIND_FIX, "verb_tense", "ai_set")
            )
            self.assertEqual(item["occurrences"][0]["set_id"], "set-20260901-120000")

    def test_right_answers_make_no_cards(self) -> None:
        data, run = exercise_set([{"exercise_id": "ex1", "answer": "shipped", "correct": True}])
        self.assertEqual(learner_model.items_from_set_run(data, run), [])

    def test_set_mistake_merges_into_a_speech_item_without_rewording_it(self) -> None:
        spoken = session("s1", DAY1, {"issues": [
            issue("I went there yesterday.", quote="I go there yesterday", topic="verb_tense")
        ]})
        data, run = exercise_set([
            {"exercise_id": "ex2", "answer": "I go there yesterday.", "correct": False}
        ])
        bank = learner_model.build_bank([spoken], learner_model.items_from_set_run(data, run))

        self.assertEqual(len(bank), 1)
        item = next(iter(bank.values()))
        self.assertNotIn("origin", item)
        self.assertEqual(item["content"]["quote"], "I go there yesterday")
        self.assertEqual([o.get("session_id") or o.get("set_id") for o in item["occurrences"]],
                         ["s1", "set-20260901-120000"])
