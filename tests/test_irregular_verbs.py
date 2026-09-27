"""Offline tests for the irregular-verb table, its grading, drill picking and routes."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import config, irregular_verbs, verb_store  # noqa: E402
from app.server import app as fastapi_app  # noqa: E402


def record(verb: str, correct: bool, ts: str) -> dict:
    return {"verb": verb, "correct": correct, "ts": ts}


class TableTests(unittest.TestCase):
    def test_table_is_well_formed(self) -> None:
        verbs = irregular_verbs.VERBS
        self.assertGreaterEqual(len(verbs), 100)
        self.assertEqual([v.rank for v in verbs], list(range(1, len(verbs) + 1)))
        self.assertEqual(len({v.key for v in verbs}), len(verbs))
        # The drill shows only the Russian, so it must name one verb.
        self.assertEqual(len({v.translation for v in verbs}), len(verbs))
        for verb in verbs:
            for variants in verb.forms:
                self.assertTrue(variants, verb.key)
                for form in variants:
                    self.assertRegex(form, r"^[a-z]+$", verb.key)

    def test_most_frequent_verbs_come_first(self) -> None:
        keys = [v.key for v in irregular_verbs.VERBS[:5]]
        self.assertEqual(keys, ["be", "have", "do", "say", "go"])


class GradingTests(unittest.TestCase):
    def test_one_variant_is_enough_and_case_and_spaces_do_not_matter(self) -> None:
        be = irregular_verbs.lookup("be")
        self.assertTrue(irregular_verbs.grade(be, [" Be ", "was", "been"])["correct"])
        self.assertTrue(irregular_verbs.grade(be, ["be", "was/were", "been"])["correct"])
        self.assertTrue(irregular_verbs.grade(be, ["be", "were, was", "been"])["correct"])

    def test_wrong_extra_variant_or_empty_answer_fails(self) -> None:
        go = irregular_verbs.lookup("go")
        graded = irregular_verbs.grade(go, ["go", "went/goed", ""])
        self.assertFalse(graded["correct"])
        self.assertEqual([f["correct"] for f in graded["forms"]], [True, False, False])
        self.assertEqual(graded["forms"][2]["expected"], ["gone"])

    def test_british_spelling_accepted(self) -> None:
        learn = irregular_verbs.lookup("learn")
        self.assertTrue(irregular_verbs.grade(learn, ["learn", "learnt", "learned"])["correct"])


class StatsAndPickTests(unittest.TestCase):
    def test_stats_track_the_run_of_correct_answers(self) -> None:
        stats = irregular_verbs.verb_stats(
            [
                record("go", False, "2026-09-20T10:00:00"),
                record("go", True, "2026-09-21T10:00:00"),
                record("go", True, "2026-09-22T10:00:00"),
                record("go", True, "2026-09-23T10:00:00"),
                record("unknown", True, "2026-09-23T10:00:00"),
            ]
        )
        self.assertEqual(set(stats), {"go"})
        self.assertEqual(stats["go"]["attempts"], 4)
        self.assertEqual(stats["go"]["streak"], 3)
        self.assertTrue(stats["go"]["learned"])

    def test_pick_mistakes_then_new_by_frequency_then_reviews(self) -> None:
        stats = irregular_verbs.verb_stats(
            [
                record("be", True, "2026-09-20T10:00:00"),
                record("take", False, "2026-09-20T10:00:00"),
            ]
        )
        picked = [v.key for v in irregular_verbs.pick(stats, 3)]
        self.assertEqual(picked, ["take", "have", "do"])
        everything = irregular_verbs.pick(stats, 10_000)
        self.assertEqual(len(everything), len(irregular_verbs.VERBS))
        self.assertEqual(everything[-1].key, "be")


class VerbRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        base_dir_patch.start()
        self.addCleanup(base_dir_patch.stop)
        self.client = TestClient(fastapi_app)

    def test_table_drill_and_check(self) -> None:
        table = self.client.get("/api/irregular-verbs").json()
        self.assertEqual(table["verbs"][0]["key"], "be")
        self.assertIsNone(table["verbs"][0]["stats"])
        self.assertEqual(table["summary"]["trained"], 0)

        drill = self.client.get("/api/irregular-verbs/drill?count=3").json()
        self.assertEqual([v["key"] for v in drill["verbs"]], ["be", "have", "do"])
        self.assertNotIn("past", drill["verbs"][0])

        response = self.client.post(
            "/api/irregular-verbs/check", json={"verb": "have", "answers": ["have", "had", "haved"]}
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertFalse(body["correct"])
        self.assertEqual(body["stats"]["attempts"], 1)

        # The mistake is logged (append-only) and comes first next time.
        self.assertEqual(len(verb_store.all_results()), 1)
        drill = self.client.get("/api/irregular-verbs/drill?count=2").json()
        self.assertEqual([v["key"] for v in drill["verbs"]], ["have", "be"])
        self.assertEqual(self.client.get("/api/irregular-verbs").json()["summary"]["to_fix"], 1)

    def test_rejects_unknown_verb_and_bad_input(self) -> None:
        unknown = {"verb": "goed", "answers": ["a", "b", "c"]}
        self.assertEqual(self.client.post("/api/irregular-verbs/check", json=unknown).status_code, 404)
        short = {"verb": "go", "answers": ["go"]}
        self.assertEqual(self.client.post("/api/irregular-verbs/check", json=short).status_code, 422)
        self.assertEqual(self.client.get("/api/irregular-verbs/drill?count=0").status_code, 400)
        self.assertEqual(verb_store.all_results(), [])

    def test_a_checked_verb_keeps_the_streak_alive(self) -> None:
        self.client.post(
            "/api/irregular-verbs/check", json={"verb": "go", "answers": ["go", "went", "gone"]}
        )
        today = self.client.get("/api/learner/today").json()
        self.assertTrue(today["active_today"])


if __name__ == "__main__":
    unittest.main()
