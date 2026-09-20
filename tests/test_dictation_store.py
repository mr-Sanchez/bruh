"""Offline tests for dictation lessons on disk: import states, results, stats."""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, dictation, dictation_store  # noqa: E402
from app.youtube import FetchedVideo  # noqa: E402
from tests.test_dictation import MANUAL_VTT  # noqa: E402


def fetched(video_id: str = "dQw4w9WgXcQ") -> FetchedVideo:
    return FetchedVideo(
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title="Deploying on Fridays",
        uploader="Some Channel",
        duration_seconds=300.0,
        audio_filename="audio.m4a",
        subtitles=MANUAL_VTT,
        subtitle_language="en",
        subtitle_kind="manual",
    )


class DictationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base_dir_patch = patch.object(config, "base_dir", return_value=Path(self._tmp.name))
        base_dir_patch.start()
        self.addCleanup(base_dir_patch.stop)

    def _ready_lesson(self, video_id: str = "dQw4w9WgXcQ"):
        lesson = dictation_store.start_import(video_id, "https://youtu.be/x", "en-US")
        video = fetched(video_id)
        (dictation_store.lesson_dir(video_id) / "audio.m4a").write_bytes(b"fake")
        return dictation_store.finish_import(
            lesson, video, dictation.lesson_sentences(video.subtitles)
        )

    def test_an_import_goes_from_importing_to_ready(self) -> None:
        lesson = dictation_store.start_import("dQw4w9WgXcQ", "https://youtu.be/x", "en-US")
        self.assertEqual(lesson["status"], config.LESSON_STATUS_IMPORTING)

        ready = self._ready_lesson()

        self.assertEqual(ready["status"], config.LESSON_STATUS_READY)
        self.assertEqual(len(ready["sentences"]), 2)
        directory = dictation_store.lesson_dir("dQw4w9WgXcQ")
        self.assertTrue((directory / config.LESSON_META_FILENAME).is_file())
        # The caption track is kept verbatim next to the audio.
        self.assertEqual(
            (directory / config.LESSON_SUBTITLES_FILENAME).read_text(encoding="utf-8"), MANUAL_VTT
        )
        self.assertIsNotNone(dictation_store.audio_path(ready))

    def test_a_failed_import_keeps_its_message(self) -> None:
        lesson = dictation_store.start_import("dQw4w9WgXcQ", "https://youtu.be/x", "en-US")

        dictation_store.fail_import(lesson, "У этого видео нет субтитров.")

        stored = dictation_store.load_lesson("dQw4w9WgXcQ")
        self.assertEqual(stored["status"], config.LESSON_STATUS_ERROR)
        self.assertIn("субтитров", stored["error_message"])

    def test_results_are_appended_and_never_rewritten(self) -> None:
        self._ready_lesson()

        dictation_store.append_result(
            "dQw4w9WgXcQ", {"sentence": 0, "completed": False, "total_words": 8}
        )
        dictation_store.append_result(
            "dQw4w9WgXcQ",
            {
                "sentence": 0,
                "completed": True,
                "total_words": 8,
                "correct_words": ["Hello"] * 8,
            },
        )

        records = dictation_store.lesson_results("dQw4w9WgXcQ")
        self.assertEqual(len(records), 2)
        self.assertTrue(all(record["v"] == 1 and record["ts"] for record in records))
        self.assertEqual(
            dictation.lesson_progress(2, records)["done"], 1
        )

    def test_re_importing_keeps_the_results(self) -> None:
        self._ready_lesson()
        dictation_store.append_result("dQw4w9WgXcQ", {"sentence": 0, "completed": True})

        dictation_store.start_import("dQw4w9WgXcQ", "https://youtu.be/x", "en-US")

        self.assertEqual(len(dictation_store.lesson_results("dQw4w9WgXcQ")), 1)

    def test_summary_and_payload_carry_the_progress(self) -> None:
        lesson = self._ready_lesson()
        dictation_store.append_result(
            "dQw4w9WgXcQ",
            {"sentence": 0, "completed": True, "total_words": 8, "correct_words": ["a"] * 8},
        )

        summary = dictation_store.lesson_summary(lesson)
        payload = dictation_store.lesson_payload(lesson)

        self.assertEqual(summary["progress"]["done"], 1)
        self.assertEqual(summary["progress"]["next_index"], 1)
        self.assertNotIn("sentences", summary.get("payload", {}))
        self.assertEqual(len(payload["sentences"]), 2)
        self.assertTrue(payload["sentences"][0]["tokens"][0]["word"])
        self.assertIn(0, payload["results"])

    def test_stats_collect_every_lesson(self) -> None:
        self._ready_lesson("aaaaaaaaaaa")
        self._ready_lesson("bbbbbbbbbbb")
        dictation_store.append_result(
            "aaaaaaaaaaa",
            {
                "sentence": 0,
                "completed": True,
                "total_words": 4,
                "correct_words": ["one", "two", "three"],
                "incorrect_words": ["deploy"],
                "hint_words": [],
            },
        )
        dictation_store.append_result(
            "bbbbbbbbbbb",
            {
                "sentence": 0,
                "completed": True,
                "total_words": 2,
                "correct_words": ["one"],
                "incorrect_words": ["Deploy,"],
                "hint_words": [],
            },
        )

        stats = dictation_store.stats()

        self.assertEqual(stats["lessons"], 2)
        self.assertEqual(stats["sentences"], 2)
        self.assertEqual(stats["words"], 6)
        self.assertAlmostEqual(stats["accuracy"], 0.667)
        self.assertEqual([row["key"] for row in stats["tricky_words"]], ["deploy"])

    def test_the_day_count_and_the_streak_see_only_finished_sentences(self) -> None:
        self._ready_lesson()
        dictation_store.append_result("dQw4w9WgXcQ", {"sentence": 0, "completed": True})
        dictation_store.append_result("dQw4w9WgXcQ", {"sentence": 1, "completed": False})

        self.assertEqual(dictation_store.done_today(), 1)
        self.assertEqual(dictation_store.active_days(), {dt.date.today()})

    def test_the_next_lesson_is_the_one_in_progress(self) -> None:
        self.assertIsNone(dictation_store.next_lesson())
        self._ready_lesson("aaaaaaaaaaa")
        self._ready_lesson("bbbbbbbbbbb")
        dictation_store.append_result("aaaaaaaaaaa", {"sentence": 0, "completed": True})

        self.assertEqual(dictation_store.next_lesson()["id"], "aaaaaaaaaaa")

    def test_deleting_a_lesson_removes_its_folder(self) -> None:
        self._ready_lesson()
        dictation_store.append_result("dQw4w9WgXcQ", {"sentence": 0, "completed": True})

        self.assertTrue(dictation_store.delete_lesson("dQw4w9WgXcQ"))

        self.assertFalse(dictation_store.lesson_dir("dQw4w9WgXcQ").exists())
        self.assertEqual(dictation_store.list_lessons(), [])
        self.assertFalse(dictation_store.delete_lesson("dQw4w9WgXcQ"))


if __name__ == "__main__":
    unittest.main()
