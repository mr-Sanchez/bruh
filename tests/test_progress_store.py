"""Offline tests for cross-session topic aggregation (no network needed)."""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, curriculum, progress_store  # noqa: E402


def write_analysed_session(
    recordings_root: Path,
    session_id: str,
    recorded_at: Optional[dt.datetime],
    analysis: Dict[str, Any],
    language: str = "en-US",
) -> Path:
    """A minimal recordings/<id>/ with session.json (if dated) and analysis.json."""
    directory = recordings_root / session_id
    directory.mkdir(parents=True, exist_ok=True)
    if recorded_at is not None:
        meta = {"started_at": recorded_at.isoformat(), "language_key": language}
        (directory / config.SESSION_META_FILENAME).write_text(json.dumps(meta), encoding="utf-8")
    (directory / config.ANALYSIS_FILENAME).write_text(
        json.dumps(analysis, ensure_ascii=False), encoding="utf-8"
    )
    return directory


def _score(value: int) -> Dict[str, Any]:
    return {"score": value, "comment": "..."}


class ProgressStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        self._base_dir_patch.start()
        self.addCleanup(self._base_dir_patch.stop)
        self.root = config.recordings_dir()

    def test_rebuild_aggregates_counts_and_recording_dates(self) -> None:
        first = dt.datetime(2026, 9, 1, 10, 0, 0)
        second = dt.datetime(2026, 9, 3, 10, 0, 0)
        write_analysed_session(
            self.root, "s1", first, {"topic_counts": {"present_perfect": 2, "articles_basic": 1}}
        )
        write_analysed_session(self.root, "s2", second, {"topic_counts": {"present_perfect": 1}})

        data = progress_store.rebuild_from_sessions()

        self.assertEqual(data["topics"]["present_perfect"]["count"], 3)
        self.assertEqual(data["topics"]["present_perfect"]["last_seen"], second.isoformat())
        self.assertEqual(data["topics"]["present_perfect"]["first_seen"], first.isoformat())
        self.assertEqual(data["topics"]["articles_basic"]["count"], 1)
        self.assertEqual(data["topics"]["present_perfect"]["session_ids"], ["s1", "s2"])

    def test_rebuild_replaces_rather_than_adds_on_reanalysis(self) -> None:
        when = dt.datetime(2026, 9, 1, 10, 0, 0)
        write_analysed_session(self.root, "s1", when, {"topic_counts": {"present_perfect": 1}})
        progress_store.rebuild_from_sessions()
        write_analysed_session(self.root, "s1", when, {"topic_counts": {"present_perfect": 2}})
        data = progress_store.rebuild_from_sessions()
        self.assertEqual(data["topics"]["present_perfect"]["count"], 2)
        self.assertEqual(data["topics"]["present_perfect"]["session_ids"], ["s1"])

    def test_weakness_score_decays_with_recency(self) -> None:
        now = dt.datetime(2026, 9, 15, 0, 0, 0)
        recent = {"count": 10, "last_seen": (now - dt.timedelta(days=1)).isoformat()}
        old = {"count": 10, "last_seen": (now - dt.timedelta(days=60)).isoformat()}
        recent_score = progress_store.compute_weakness_score(recent, now)
        old_score = progress_store.compute_weakness_score(old, now)
        self.assertGreater(recent_score, old_score)

    def test_top_weak_topics_sorted_by_score(self) -> None:
        now = dt.datetime(2026, 9, 15, 0, 0, 0)
        write_analysed_session(
            self.root, "s1", now - dt.timedelta(days=30), {"topic_counts": {"articles_basic": 1}}
        )
        write_analysed_session(self.root, "s2", now, {"topic_counts": {"present_perfect": 5}})
        progress_store.rebuild_from_sessions()
        ranked = progress_store.top_weak_topics(n=10, now=now)
        self.assertEqual(ranked[0]["key"], "present_perfect")
        self.assertEqual(ranked[0]["label"], curriculum.topic_label("present_perfect"))

    def test_rebuild_falls_back_to_analysis_date_without_session_json(self) -> None:
        analysis = {
            "created_at": "2026-09-01T10:00:00",
            "topic_counts": {"word_order": 2, "prepositions_time_place": 1},
        }
        write_analysed_session(self.root, "2026-09-01_10-00-00", None, analysis)

        data = progress_store.rebuild_from_sessions(self.root)
        self.assertEqual(data["topics"]["word_order"]["count"], 2)
        self.assertEqual(data["topics"]["prepositions_time_place"]["count"], 1)
        self.assertEqual(data["topics"]["word_order"]["last_seen"], "2026-09-01T10:00:00")

    def test_score_history_keeps_v2_scores_in_recording_order(self) -> None:
        scores = {
            "grammar": _score(6),
            "vocabulary": _score(7),
            "fluency": _score(5),
            "naturalness": _score(6),
        }
        write_analysed_session(
            self.root,
            "late",
            dt.datetime(2026, 9, 5, 9, 0),
            {"schema_version": 2, "scores": scores, "overall_score": 6.0, "language": "en-US"},
        )
        write_analysed_session(
            self.root, "v1", dt.datetime(2026, 9, 1, 9, 0), {"topic_counts": {"articles_basic": 1}}
        )
        write_analysed_session(
            self.root,
            "early",
            dt.datetime(2026, 9, 2, 9, 0),
            {"schema_version": 2, "scores": scores, "overall_score": 6.0},
        )

        history = progress_store.rebuild_from_sessions()["score_history"]

        self.assertEqual([entry["session_id"] for entry in history], ["early", "late"])
        self.assertEqual(history[1]["grammar"], 6)
        self.assertEqual(history[1]["fluency"], 5)
        self.assertEqual(history[1]["overall"], 6.0)
        self.assertEqual(history[1]["at"], "2026-09-05T09:00:00")

    def test_load_progress_rebuilds_an_old_schema_file(self) -> None:
        write_analysed_session(
            self.root, "s1", dt.datetime(2026, 9, 1), {"topic_counts": {"articles_basic": 1}}
        )
        config.data_dir().mkdir(parents=True)
        old = {"schema_version": 1, "topics": {"present_perfect": {"count": 99}}}
        (config.data_dir() / config.PROGRESS_FILENAME).write_text(json.dumps(old), "utf-8")

        data = progress_store.load_progress()

        self.assertEqual(data["schema_version"], progress_store.SCHEMA_VERSION)
        self.assertEqual(set(data["topics"]), {"articles_basic"})
        self.assertEqual(data["score_history"], [])


if __name__ == "__main__":
    unittest.main()
