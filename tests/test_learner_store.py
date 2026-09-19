"""Offline tests for the learner model's files: item bank, attempts, usage."""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, learner_store  # noqa: E402
from tests.test_progress_store import write_analysed_session  # noqa: E402

ANALYSIS = {
    "schema_version": 2,
    "language": "en-US",
    "issues": [
        {
            "topic": "prepositions",
            "quote": "responsible of",
            "explanation": "Нужен for.",
            "correction": "responsible for",
            "better_versions": [],
            "pattern": {"rule": "responsible for + noun", "examples": []},
            "severity": "moderate",
        }
    ],
    "vocabulary": [{"phrase": "recruiter", "meaning": "рекрутер", "example": "..."}],
    "topic_counts": {"prepositions": 1},
}


class LearnerStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        base_dir_patch.start()
        self.addCleanup(base_dir_patch.stop)
        self.root = config.recordings_dir()

    def test_bank_is_rebuilt_from_analysis_files(self) -> None:
        write_analysed_session(self.root, "s1", dt.datetime(2026, 9, 1, 10), ANALYSIS)
        bank = learner_store.load_item_bank()  # no file yet -> rebuilt
        kinds = sorted(item["kind"] for item in bank["items"].values())
        self.assertEqual(kinds, ["fix", "pattern", "phrase"])
        self.assertEqual(bank["sessions"][0]["session_id"], "s1")
        self.assertTrue((config.data_dir() / config.ITEM_BANK_FILENAME).is_file())

    def test_attempts_log_is_appended_and_survives_a_bad_line(self) -> None:
        when = dt.datetime(2026, 9, 2, 9, 30)
        learner_store.append_attempt("fix-1", "review_card", True, answer="for", when=when)
        with (config.data_dir() / config.ATTEMPTS_FILENAME).open("a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        learner_store.append_attempt("fix-1", "cloze", False, context="today", when=when)

        attempts = learner_store.load_attempts()

        self.assertEqual(len(attempts), 2)
        self.assertEqual(
            attempts[0],
            {
                "v": 1,
                "ts": "2026-09-02T09:30:00",
                "item_id": "fix-1",
                "exercise": "review_card",
                "correct": True,
                "answer": "for",
            },
        )
        self.assertEqual(attempts[1]["context"], "today")
        self.assertNotIn("answer", attempts[1])

    def test_rebuilding_the_bank_never_touches_attempts(self) -> None:
        write_analysed_session(self.root, "s1", dt.datetime(2026, 9, 1, 10), ANALYSIS)
        learner_store.append_attempt("fix-1", "review_card", True)
        before = (config.data_dir() / config.ATTEMPTS_FILENAME).read_bytes()
        learner_store.refresh_after_analysis()
        self.assertEqual((config.data_dir() / config.ATTEMPTS_FILENAME).read_bytes(), before)

    def test_item_states_apply_attempts(self) -> None:
        write_analysed_session(self.root, "s1", dt.datetime(2026, 9, 1, 10), ANALYSIS)
        bank = learner_store.load_item_bank()
        phrase_id = next(i for i, item in bank["items"].items() if item["kind"] == "phrase")
        learner_store.append_attempt(
            phrase_id, "review_card", True, when=dt.datetime(2026, 9, 1, 20)
        )
        state = learner_store.item_states()[phrase_id]
        self.assertEqual(state.box, 2)
        self.assertFalse(state.is_new)

    def test_topic_drill_attempts_are_logged_but_do_not_touch_item_states(self) -> None:
        write_analysed_session(self.root, "s1", dt.datetime(2026, 9, 1, 10), ANALYSIS)
        record = learner_store.append_attempt(
            None, "cloze", False, topic="articles", score=0.6666, session_id="s1", answer="a | the"
        )
        self.assertEqual(record["score"], 0.667)
        self.assertNotIn("item_id", record)
        self.assertEqual(learner_store.load_attempts(), [record])
        self.assertTrue(all(state.is_new for state in learner_store.item_states().values()))
        with self.assertRaises(ValueError):
            learner_store.append_attempt(None, "cloze", True)

    def test_practice_texts_skip_russian_and_carry_cloze_results(self) -> None:
        english = {**ANALYSIS, "improved_version": "I talked to the recruiter at the office."}
        russian = {**ANALYSIS, "language": "ru", "improved_version": "Я поговорил с рекрутером."}
        write_analysed_session(self.root, "s1", dt.datetime(2026, 9, 1, 10), english)
        write_analysed_session(self.root, "s2", dt.datetime(2026, 9, 2, 10), russian, language="ru")
        write_analysed_session(self.root, "s3", dt.datetime(2026, 9, 3, 10), ANALYSIS)  # no text
        learner_store.append_attempt(None, "cloze", True, topic="articles", score=1.0, session_id="s1")

        plain = learner_store.practice_texts()
        self.assertEqual([t["session_id"] for t in plain], ["s1"])
        self.assertNotIn("segments", plain[0])

        (articles,) = learner_store.practice_texts("articles")
        self.assertEqual(articles["gaps"], 2)
        self.assertEqual((articles["attempts"], articles["best_score"]), (1, 1.0))
        (prepositions,) = learner_store.practice_texts("prepositions")
        self.assertEqual((prepositions["gaps"], prepositions["attempts"]), (1, 0))

    def test_claude_cost_estimate(self) -> None:
        usage = {
            "input_tokens": 3_000,
            "output_tokens": 5_000,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        # 3000 * $2/M + 5000 * $10/M = $0.006 + $0.05
        self.assertAlmostEqual(learner_store.estimate_claude_cost("claude-sonnet-5", usage), 0.056)
        self.assertIsNone(learner_store.estimate_claude_cost("unknown-model", usage))

    def test_usage_log_and_summary(self) -> None:
        learner_store.record_claude_usage(
            "analysis", "claude-sonnet-5", {"input_tokens": 1_000_000}, session_id="s1"
        )
        learner_store.record_deepgram_usage("transcription", "en-US", 120.0, session_id="s1")

        summary = learner_store.usage_summary()

        self.assertAlmostEqual(summary["by_service_usd"]["anthropic"], 2.0)
        self.assertAlmostEqual(summary["by_service_usd"]["deepgram"], 0.0086)
        self.assertEqual(summary["by_purpose"]["anthropic:analysis"]["calls"], 1)
        self.assertEqual(summary["recent"][0]["service"], "deepgram")  # newest first
        line = (config.data_dir() / config.USAGE_FILENAME).read_text("utf-8").splitlines()[0]
        self.assertEqual(json.loads(line)["session_id"], "s1")


if __name__ == "__main__":
    unittest.main()
