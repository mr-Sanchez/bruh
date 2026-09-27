"""Offline tests for the dictation's pure rules and the YouTube import.

Nothing here touches the network: `parse_vtt`/`sentences_from_cues` work on
sample subtitle files, and the fetcher is driven through a fake yt-dlp.
"""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, dictation, youtube  # noqa: E402

MANUAL_VTT = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:05.000
Hello everyone, and welcome back to the channel.

00:00:05.100 --> 00:00:09.000
Today we are going to talk about code review &amp; how to do it well.
"""

# YouTube's automatic captions scroll: every cue repeats the previous line.
AUTO_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.030 --> 00:00:02.270 align:start position:0%
so today I want to<00:00:01.000><c> talk</c><00:00:01.400><c> about</c>

00:00:02.270 --> 00:00:04.500 align:start position:0%
so today I want to talk about
the way we deploy

00:00:04.500 --> 00:00:07.000 align:start position:0%
the way we deploy
our services every single morning
"""

# The exact shape yt-dlp saves: a cue holds a line of one space, the karaoke
# line with a stamp per word, and (in the next 10 ms cue) that line again.
KARAOKE_VTT = (
    "WEBVTT\n\n"
    "00:00:00.080 --> 00:00:02.230 align:start position:0%\n"
    " \n"
    "Did<00:00:00.320><c> you</c><00:00:00.400><c> know</c><00:00:00.560><c> that</c>"
    "<00:00:00.880><c> Google</c><00:00:01.280><c> spends</c>\n\n"
    "00:00:02.230 --> 00:00:02.240 align:start position:0%\n"
    "Did you know that Google spends\n"
    " \n\n"
    "00:00:02.240 --> 00:00:06.070 align:start position:0%\n"
    "Did you know that Google spends\n"
    "$500<00:00:02.879><c> million</c><00:00:03.840><c> every</c><00:00:04.400><c> day.</c>"
    "<00:00:05.000><c> Then</c><00:00:05.400><c> we</c><00:00:05.700><c> go</c>\n\n"
)


class ParsingTests(unittest.TestCase):
    def test_parses_cues_with_entities_and_inline_tags(self) -> None:
        cues = dictation.parse_vtt(MANUAL_VTT)

        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].start, 1.0)
        self.assertEqual(cues[0].end, 5.0)
        self.assertIn("code review & how", cues[1].text)

    def test_rolling_repetition_of_automatic_captions_is_dropped(self) -> None:
        words = [word.text for word in dictation.cue_words(dictation.parse_vtt(AUTO_VTT))]

        self.assertEqual(
            words,
            "so today I want to talk about the way we deploy our services "
            "every single morning".split(),
        )

    def test_the_line_after_a_blank_one_is_not_lost_and_keeps_its_word_times(self) -> None:
        cues = dictation.parse_vtt(KARAOKE_VTT)

        self.assertEqual(cues[0].text, "Did you know that Google spends")
        words = dictation.cue_words(cues)
        self.assertEqual(
            [word.text for word in words],
            "Did you know that Google spends $500 million every day. Then we go".split(),
        )
        by_text = {word.text: word for word in words}
        self.assertAlmostEqual(by_text["Did"].start, 0.08)
        self.assertAlmostEqual(by_text["Did"].end, 0.32)
        self.assertAlmostEqual(by_text["million"].start, 2.879)
        self.assertAlmostEqual(by_text["Then"].start, 5.0)

    def test_a_sentence_plays_from_its_own_first_word_not_from_a_cue_start(self) -> None:
        sentences = dictation.lesson_sentences(KARAOKE_VTT, min_words=3)

        self.assertEqual(len(sentences), 2)
        self.assertAlmostEqual(sentences[0]["start"], max(0.0, 0.08 - config.DICTATION_PAD_SECONDS))
        # "day." ends at 5.0, where "Then" begins - not at the cue's end (6.07).
        self.assertAlmostEqual(sentences[0]["end"], 5.0 + config.DICTATION_PAD_SECONDS)
        self.assertAlmostEqual(sentences[1]["start"], 5.0 - config.DICTATION_PAD_SECONDS)

    def test_words_of_an_untimed_cue_are_spread_over_it(self) -> None:
        cues = [dictation.Cue(start=10.0, end=20.0, text="aaaa bbbb cccc dddd eeee")]

        words = dictation.cue_words(cues)

        self.assertAlmostEqual(words[0].start, 10.0)
        self.assertAlmostEqual(words[-1].end, 20.0)
        self.assertAlmostEqual(words[2].start, 14.0)

    def test_srt_counters_and_a_blank_line_of_spaces_do_not_leak_into_cues(self) -> None:
        srt = (
            "1\n00:00:01,000 --> 00:00:02,000\nHello there\n \n"
            "2\n00:00:02,000 --> 00:00:03,000\nfriend\n"
        )

        cues = dictation.parse_vtt(srt)

        self.assertEqual([cue.text for cue in cues], ["Hello there", "friend"])

    def test_sentences_follow_punctuation_when_the_captions_have_any(self) -> None:
        sentences = dictation.lesson_sentences(MANUAL_VTT)

        self.assertEqual(len(sentences), 2)
        self.assertEqual(sentences[0]["text"], "Hello everyone, and welcome back to the channel.")
        self.assertEqual(sentences[0]["words"], 8)
        self.assertEqual(sentences[1]["index"], 1)
        # The play window has a little air on both sides of the cue.
        self.assertAlmostEqual(sentences[0]["start"], 1.0 - config.DICTATION_PAD_SECONDS)
        self.assertAlmostEqual(sentences[0]["end"], 5.0 + config.DICTATION_PAD_SECONDS)

    def test_unpunctuated_captions_are_cut_at_a_comma_or_the_cap(self) -> None:
        cues = [dictation.Cue(start=0.0, end=20.0, text=" ".join(f"word{i}" for i in range(25)))]

        sentences = dictation.sentences_from_cues(cues, min_words=4, max_words=10)

        self.assertEqual([s["words"] for s in sentences], [10, 10, 5])
        self.assertEqual(sentences[0]["text"].split()[0], "word0")

    def test_a_tail_too_short_to_stand_alone_joins_the_sentence_before_it(self) -> None:
        cues = [dictation.Cue(start=0.0, end=9.0, text="one two three four five. six seven")]

        sentences = dictation.sentences_from_cues(cues, min_words=4, max_words=12)

        self.assertEqual(len(sentences), 1)
        self.assertTrue(sentences[0]["text"].endswith("six seven"))

    def test_an_empty_or_unparseable_file_yields_no_sentences(self) -> None:
        self.assertEqual(dictation.lesson_sentences(""), [])
        self.assertEqual(dictation.lesson_sentences("not a subtitle file"), [])


class CheckingTests(unittest.TestCase):
    def test_case_and_punctuation_are_ignored(self) -> None:
        self.assertEqual(dictation.normalize_word("Don't,"), "dont")
        self.assertEqual(dictation.normalize_word("«Привет»"), "привет")
        self.assertEqual(dictation.normalize_word("—"), "")

    def test_only_word_tokens_are_typed(self) -> None:
        tokens = dictation.tokenize("Don't stop — it's 2024!")

        words = [t["text"] for t in tokens if t["word"]]
        self.assertEqual(words, ["Don't", "stop", "it's", "2024"])
        self.assertEqual([t["text"] for t in tokens if not t["word"]], ["—", "!"])

    def test_grading_counts_right_wrong_and_hinted_words(self) -> None:
        result = dictation.grade_sentence(
            "Hello everyone, and welcome back.",
            ["hello", "everone", "and", "welcome", "back"],
            hinted=[2],
        )

        self.assertEqual(result["total_words"], 5)
        self.assertEqual(result["incorrect_words"], ["everyone"])
        self.assertEqual(result["hint_words"], ["and"])
        self.assertFalse(result["completed"])
        self.assertAlmostEqual(result["accuracy"], 0.8)

    def test_a_hinted_word_is_correct_but_never_clean(self) -> None:
        answers = ["one", "two", "three", "four"]
        result = dictation.grade_sentence("one two three four", answers, [1])

        self.assertTrue(result["completed"])
        self.assertFalse(result["clean"])

    def test_a_missing_answer_is_simply_wrong(self) -> None:
        result = dictation.grade_sentence("one two three four", ["one"])

        self.assertEqual(result["incorrect_words"], ["two", "three", "four"])

    def test_prefix_check_drives_the_live_feedback(self) -> None:
        self.assertTrue(dictation.is_prefix("dep", "deploy"))
        self.assertFalse(dictation.is_prefix("dex", "deploy"))


def result_record(sentence: int, **fields: Any) -> Dict[str, Any]:
    record = {
        "v": 1,
        "ts": fields.pop("ts", "2026-09-20T10:00:00"),
        "sentence": sentence,
        "total_words": fields.pop("total_words", 4),
        "correct_words": [],
        "incorrect_words": [],
        "hint_words": [],
        "completed": True,
    }
    record.update(fields)
    return record


class ReadModelTests(unittest.TestCase):
    def test_progress_uses_the_last_attempt_per_sentence(self) -> None:
        records = [
            result_record(
                0, correct_words=["a", "b"], incorrect_words=["c", "d"], completed=False
            ),
            result_record(0, correct_words=["a", "b", "c", "d"]),
            result_record(
                1,
                correct_words=["a", "b", "c"],
                hint_words=["c"],
                incorrect_words=["d"],
                completed=False,
            ),
        ]

        progress = dictation.lesson_progress(5, records)

        self.assertEqual(progress["done"], 1)
        self.assertEqual(progress["started"], 2)
        self.assertEqual(progress["hints"], 1)
        self.assertEqual(progress["next_index"], 1)
        self.assertAlmostEqual(progress["accuracy"], 7 / 8)

    def test_tricky_words_rank_misses_and_hints_together(self) -> None:
        records = [
            result_record(0, incorrect_words=["Deploy"], hint_words=["schedule"]),
            result_record(1, incorrect_words=["deploy,"], correct_words=["schedule"]),
            result_record(2, incorrect_words=["once"]),
        ]

        tricky = dictation.tricky_words(records, min_misses=2)

        self.assertEqual([row["key"] for row in tricky], ["deploy"])
        self.assertEqual(tricky[0]["missed"], 2)

    def test_daily_counts_and_streak_days_come_from_the_results(self) -> None:
        records = [
            result_record(0, ts="2026-09-19T20:00:00", correct_words=["a", "b", "c", "d"]),
            result_record(
                1,
                ts="2026-09-20T09:00:00",
                correct_words=["a", "b"],
                incorrect_words=["c", "d"],
                completed=False,
            ),
        ]

        counts = dictation.daily_counts(records)

        self.assertEqual(counts["2026-09-19"]["sentences"], 1)
        self.assertEqual(counts["2026-09-20"]["sentences"], 0)
        self.assertAlmostEqual(counts["2026-09-20"]["accuracy"], 0.5)
        self.assertEqual(
            dictation.active_days(records), {dt.date(2026, 9, 19), dt.date(2026, 9, 20)}
        )
        self.assertEqual(dictation.done_on(records, dt.date(2026, 9, 19)), 1)


class UrlTests(unittest.TestCase):
    def test_every_youtube_link_shape_yields_the_video_id(self) -> None:
        for url in (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ?t=30",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "dQw4w9WgXcQ",
        ):
            self.assertEqual(youtube.video_id_from_url(url), "dQw4w9WgXcQ", url)

    def test_anything_else_is_refused(self) -> None:
        for url in ("https://vimeo.com/12345", "", "not a url"):
            with self.assertRaises(youtube.UnsupportedUrlError):
                youtube.video_id_from_url(url)


class SubtitleChoiceTests(unittest.TestCase):
    def test_manual_captions_win(self) -> None:
        info = {"subtitles": {"en-US": []}, "automatic_captions": {"en": []}}

        self.assertEqual(youtube.pick_subtitle_track(info, ["en"]), ("en-US", youtube.MANUAL))

    def test_the_original_automatic_track_wins_over_a_translated_one(self) -> None:
        translated = [{"ext": "vtt", "url": "https://x/api/timedtext?lang=en-orig&tlang=en"}]
        original = [{"ext": "vtt", "url": "https://x/api/timedtext?lang=en"}]
        info = {
            "subtitles": {},
            "automatic_captions": {"en": translated, "en-orig": original},
            "language": "en-US",
        }
        self.assertEqual(youtube.pick_subtitle_track(info, ["en"]), ("en-orig", youtube.AUTOMATIC))
        info["automatic_captions"] = {"en": translated}
        with self.assertRaises(youtube.NoSubtitlesError):
            youtube.pick_subtitle_track(info, ["en"])

    def test_automatic_captions_are_the_fallback(self) -> None:
        info = {"subtitles": {}, "automatic_captions": {"en": []}, "language": "en"}

        self.assertEqual(youtube.pick_subtitle_track(info, ["en"]), ("en", youtube.AUTOMATIC))

    def test_machine_translated_captions_are_refused(self) -> None:
        # A Russian video offering automatic English captions: those are a
        # translation, not what is being said.
        info = {"subtitles": {}, "automatic_captions": {"en": [], "ru": []}, "language": "ru"}

        with self.assertRaises(youtube.NoSubtitlesError):
            youtube.pick_subtitle_track(info, ["en"])

    def test_no_track_at_all_is_refused(self) -> None:
        with self.assertRaises(youtube.NoSubtitlesError):
            youtube.pick_subtitle_track({"subtitles": {}, "automatic_captions": {}}, ["en"])


class FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL: records the options it was built with."""

    def __init__(self, options: Dict[str, Any], info: Dict[str, Any], target: Path) -> None:
        self.options = options
        self._info = info
        self._target = target

    def extract_info(self, url: str, download: bool = False) -> Dict[str, Any]:
        if download:
            (self._target / "audio.m4a").write_bytes(b"fake-audio")
            (self._target / "audio.en.vtt").write_text(MANUAL_VTT, encoding="utf-8")
        return self._info


class FetcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = Path(self._tmp.name)
        self.info = {
            "title": "Deploying on Fridays",
            "uploader": "Some Channel",
            "duration": 300,
            "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "subtitles": {"en": []},
            "automatic_captions": {},
        }
        self.calls: list = []

    def _fetcher(self, **kwargs: Any) -> youtube.YouTubeFetcher:
        def factory(options: Dict[str, Any]) -> FakeYoutubeDL:
            self.calls.append(options)
            return FakeYoutubeDL(options, self.info, self.target)

        return youtube.YouTubeFetcher(ydl_factory=factory, **kwargs)

    def test_a_video_with_subtitles_is_saved_with_its_caption_track(self) -> None:
        fetched = self._fetcher().fetch(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", self.target, ["en"]
        )

        self.assertEqual(fetched.video_id, "dQw4w9WgXcQ")
        self.assertEqual(fetched.audio_filename, "audio.m4a")
        self.assertEqual(fetched.subtitle_kind, youtube.MANUAL)
        self.assertIn("welcome back", fetched.subtitles)
        # The VTT itself is handed over as text, not left lying around.
        self.assertEqual(list(self.target.glob("*.vtt")), [])
        # Nothing is transcoded: no postprocessor, so FFmpeg is not needed.
        self.assertEqual(self.calls[-1]["postprocessors"], [])
        self.assertTrue(self.calls[-1]["writesubtitles"])
        self.assertFalse(self.calls[-1]["writeautomaticsub"])

    def test_a_long_video_is_refused_before_anything_is_downloaded(self) -> None:
        self.info["duration"] = 3600

        with self.assertRaises(youtube.VideoTooLongError):
            self._fetcher(max_seconds=1200).fetch(
                "https://youtu.be/dQw4w9WgXcQ", self.target, ["en"]
            )

        self.assertEqual(len(self.calls), 1)  # the metadata pass only
        self.assertEqual(list(self.target.iterdir()), [])

    def test_a_video_without_subtitles_is_refused(self) -> None:
        self.info["subtitles"] = {}

        with self.assertRaises(youtube.NoSubtitlesError):
            self._fetcher().fetch("https://youtu.be/dQw4w9WgXcQ", self.target, ["en"])

    def test_a_failing_download_becomes_a_typed_error(self) -> None:
        def factory(options: Dict[str, Any]) -> Any:
            raise RuntimeError("HTTP Error 403: Forbidden")

        fetcher = youtube.YouTubeFetcher(ydl_factory=factory)
        with self.assertRaises(youtube.DownloadFailedError):
            fetcher.fetch("https://youtu.be/dQw4w9WgXcQ", self.target, ["en"])


class PlanPartsTests(unittest.TestCase):
    @staticmethod
    def sizes(parts: Any) -> list:
        return [part["end"] - part["first"] for part in parts]

    def test_the_models_breaks_are_kept_when_they_fit(self) -> None:
        parts = dictation.plan_parts(24, [8, 16])
        self.assertEqual(
            parts,
            [{"first": 0, "end": 8}, {"first": 8, "end": 16}, {"first": 16, "end": 24}],
        )

    def test_junk_breaks_are_ignored(self) -> None:
        parts = dictation.plan_parts(20, [0, 10, 10, 99, -3, "x"])
        self.assertEqual(self.sizes(parts), [10, 10])

    def test_an_oversized_part_is_cut_evenly(self) -> None:
        self.assertEqual(self.sizes(dictation.plan_parts(40, [])), [14, 13, 13])
        self.assertEqual(self.sizes(dictation.plan_parts(16, [])), [8, 8])

    def test_a_tiny_part_is_merged_into_its_shorter_neighbour(self) -> None:
        # 2 | 12 | 6: the two-sentence part joins the 12 -> 14, then 6 stays.
        parts = dictation.plan_parts(20, [2, 14])
        self.assertEqual(self.sizes(parts), [14, 6])

    def test_a_merge_that_overflows_is_cut_again_and_covers_everything(self) -> None:
        parts = dictation.plan_parts(19, [3, 18])  # 3 | 15 | 1
        self.assertEqual(sum(self.sizes(parts)), 19)
        self.assertTrue(all(5 <= size <= 15 for size in self.sizes(parts)), parts)
        self.assertEqual(parts[0]["first"], 0)
        self.assertEqual(parts[-1]["end"], 19)

    def test_a_short_lesson_is_one_part_and_an_empty_one_has_none(self) -> None:
        self.assertEqual(dictation.plan_parts(3, [1, 2]), [{"first": 0, "end": 3}])
        self.assertEqual(dictation.plan_parts(0, [1]), [])

    def test_every_shape_gives_contiguous_parts_within_limits(self) -> None:
        for count in range(5, 80):
            for starts in ([], [count // 2], [1, 2, 3], list(range(1, count, 3))):
                parts = dictation.plan_parts(count, starts)
                self.assertEqual(parts[0]["first"], 0)
                self.assertEqual(parts[-1]["end"], count)
                self.assertTrue(all(a["end"] == b["first"] for a, b in zip(parts, parts[1:])))
                self.assertTrue(all(size <= 15 for size in self.sizes(parts)), (count, starts))
                if len(parts) > 1:
                    self.assertTrue(all(size >= 5 for size in self.sizes(parts)), (count, starts))


if __name__ == "__main__":
    unittest.main()
