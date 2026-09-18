"""Offline tests for cross-session topic aggregation (no network needed)."""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, progress_store  # noqa: E402


class ProgressStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        self._base_dir_patch.start()
        self.addCleanup(self._base_dir_patch.stop)

    def test_record_analysis_aggregates_counts_and_recency(self) -> None:
        first = dt.datetime(2026, 9, 1, 10, 0, 0)
        second = dt.datetime(2026, 9, 3, 10, 0, 0)

        progress_store.record_analysis("session-1", {"verb_tense": 2, "articles": 1}, when=first)
        data = progress_store.record_analysis("session-2", {"verb_tense": 1}, when=second)

        self.assertEqual(data["topics"]["verb_tense"]["count"], 3)
        self.assertEqual(data["topics"]["verb_tense"]["last_seen"], second.isoformat())
        self.assertEqual(data["topics"]["verb_tense"]["first_seen"], first.isoformat())
        self.assertEqual(data["topics"]["articles"]["count"], 1)
        self.assertIn("session-1", data["topics"]["verb_tense"]["session_ids"])
        self.assertIn("session-2", data["topics"]["verb_tense"]["session_ids"])

    def test_record_analysis_does_not_duplicate_session_ids(self) -> None:
        when = dt.datetime(2026, 9, 1, 10, 0, 0)
        progress_store.record_analysis("session-1", {"verb_tense": 1}, when=when)
        data = progress_store.record_analysis("session-1", {"verb_tense": 2}, when=when)
        self.assertEqual(data["topics"]["verb_tense"]["session_ids"].count("session-1"), 1)
        self.assertEqual(data["topics"]["verb_tense"]["count"], 3)

    def test_weakness_score_decays_with_recency(self) -> None:
        now = dt.datetime(2026, 9, 15, 0, 0, 0)
        recent = {"count": 10, "last_seen": (now - dt.timedelta(days=1)).isoformat()}
        old = {"count": 10, "last_seen": (now - dt.timedelta(days=60)).isoformat()}
        recent_score = progress_store.compute_weakness_score(recent, now)
        old_score = progress_store.compute_weakness_score(old, now)
        self.assertGreater(recent_score, old_score)

    def test_top_weak_topics_sorted_by_score(self) -> None:
        now = dt.datetime(2026, 9, 15, 0, 0, 0)
        progress_store.record_analysis("s1", {"articles": 1}, when=now - dt.timedelta(days=30))
        progress_store.record_analysis("s2", {"verb_tense": 5}, when=now)
        ranked = progress_store.top_weak_topics(n=10, now=now)
        self.assertEqual(ranked[0]["key"], "verb_tense")
        self.assertEqual(ranked[0]["label"], progress_store.TOPIC_TAXONOMY["verb_tense"]["label"])

    def test_rebuild_from_sessions_reconstructs_the_store(self) -> None:
        recordings_root = Path(self._tmp.name) / "recordings"
        session_dir = recordings_root / "2026-09-01_10-00-00"
        session_dir.mkdir(parents=True)
        analysis = {
            "created_at": "2026-09-01T10:00:00",
            "topic_counts": {"word_order": 2, "prepositions": 1},
        }
        (session_dir / config.ANALYSIS_FILENAME).write_text(
            json.dumps(analysis), encoding="utf-8"
        )

        data = progress_store.rebuild_from_sessions(recordings_root)
        self.assertEqual(data["topics"]["word_order"]["count"], 2)
        self.assertEqual(data["topics"]["prepositions"]["count"], 1)
        self.assertIn("2026-09-01_10-00-00", data["topics"]["word_order"]["session_ids"])


if __name__ == "__main__":
    unittest.main()
