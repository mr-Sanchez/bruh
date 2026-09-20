"""Offline tests for the spoken-drill measurements (app/speech_drills.py)."""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, learner_model, speech_drills  # noqa: E402


def timed_words(
    text: str, *, step: float = 0.5, gaps: Optional[Dict[int, float]] = None,
    confidence: Optional[Dict[int, float]] = None,
) -> List[Dict]:
    """Words of `text` as response_words() returns them, one every `step` s;
    `gaps` adds silence before a word index, `confidence` overrides one."""
    words, clock = [], 0.0
    for index, token in enumerate(text.split()):
        clock += (gaps or {}).get(index, 0.0)
        words.append(
            {
                "word": token.strip(".,!?").lower(),
                "text": token,
                "start": clock,
                "end": clock + step * 0.8,
                "confidence": (confidence or {}).get(index, 0.99),
            }
        )
        clock += step
    return words


def deepgram_payload(words: Sequence[Dict]) -> Dict:
    return {
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "transcript": " ".join(w["text"] for w in words),
                            "words": [
                                {
                                    "word": w["word"],
                                    "punctuated_word": w["text"],
                                    "start": w["start"],
                                    "end": w["end"],
                                    "confidence": w["confidence"],
                                }
                                for w in words
                            ],
                        }
                    ]
                }
            ]
        }
    }


class ResponseWordsTests(unittest.TestCase):
    def test_reads_timed_words_and_skips_broken_ones(self) -> None:
        payload = deepgram_payload(timed_words("Hello there."))
        payload["results"]["channels"][0]["alternatives"][0]["words"].append({"word": "x"})
        words = speech_drills.response_words(payload)
        self.assertEqual([w["text"] for w in words], ["Hello", "there."])
        self.assertEqual(words[1]["word"], "there")

    def test_a_response_without_words_gives_none(self) -> None:
        self.assertEqual(speech_drills.response_words({"ok": True}), [])
        self.assertEqual(speech_drills.response_words(None), [])


class SpeechMetricsTests(unittest.TestCase):
    def test_counts_fillers_pauses_and_pace_over_speaking_time(self) -> None:
        # 12 tokens, 2 of them fillers, one 3-second pause; every token 0.5 s.
        words = timed_words(
            "So uh I think we um should ship it today and then", gaps={5: 3.0}
        )
        metrics = speech_drills.speech_metrics(words, fillers_detected=True)
        self.assertEqual(metrics["fillers"], 2)
        self.assertEqual(metrics["filler_breakdown"], {"uh": 1, "um": 1})
        self.assertEqual(metrics["words"], 10)
        self.assertEqual(metrics["long_pauses"], 1)
        # 11 steps of 0.5 s + 3 s pause + the last word's 0.4 s.
        self.assertAlmostEqual(metrics["speaking_seconds"], 8.9, places=1)
        self.assertEqual(metrics["wpm"], round(10 / (8.9 / 60), 1))
        self.assertIsNotNone(metrics["fluency_score"])

    def test_fillers_are_unknown_without_filler_detection(self) -> None:
        metrics = speech_drills.speech_metrics(timed_words("Я думаю что да"), False)
        self.assertIsNone(metrics["fillers"])
        self.assertIsNone(metrics["fillers_per_min"])
        self.assertIsNone(metrics["fluency_score"])
        self.assertEqual(metrics["words"], 4)

    def test_counts_immediate_repeats(self) -> None:
        metrics = speech_drills.speech_metrics(timed_words("I I think the the plan"), True)
        self.assertEqual(metrics["repeats"], 2)

    def test_no_words_gives_empty_metrics(self) -> None:
        metrics = speech_drills.speech_metrics([], True)
        summary = (metrics["words"], metrics["wpm"], metrics["fluency_score"])
        self.assertEqual(summary, (0, None, None))

    def test_fluency_score_scale(self) -> None:
        self.assertEqual(speech_drills.fluency_score(0.0), 1.0)
        self.assertEqual(speech_drills.fluency_score(config.FLUENCY_TARGET_PER_MIN), 1.0)
        self.assertEqual(speech_drills.fluency_score(config.FLUENCY_ZERO_PER_MIN), 0.0)
        self.assertEqual(speech_drills.fluency_score(4.0), config.DRILL_PASS_SCORE)
        self.assertEqual(speech_drills.fluency_score(40.0), 0.0)

    def test_timeline_marks_fillers_and_long_pauses(self) -> None:
        tokens = speech_drills.timeline(timed_words("Well uh okay", gaps={2: 2.5}), True)
        self.assertEqual(tokens[1], {"text": "uh", "filler": True})
        self.assertIn("pause", tokens[2])
        self.assertEqual(tokens[3], {"text": "okay"})


class PassageTests(unittest.TestCase):
    def test_sentences_are_grouped_and_never_cut(self) -> None:
        sentence = "This is a sentence with exactly ten words in it."
        passages = speech_drills.split_passages(" ".join([sentence] * 9))
        self.assertTrue(all(p.endswith(".") for p in passages))
        for passage in passages:
            count = len(passage.split())
            self.assertGreaterEqual(count, config.SHADOWING_MIN_WORDS)
            self.assertLessEqual(count, config.SHADOWING_MAX_WORDS)
        self.assertEqual(sum(len(p.split()) for p in passages), 90)

    def test_a_short_tail_joins_the_passage_before(self) -> None:
        long = "One two three four five six seven eight nine ten eleven twelve. " * 3
        passages = speech_drills.split_passages(long + "Short one.")
        self.assertTrue(passages[-1].endswith("Short one."))
        self.assertGreaterEqual(len(passages[-1].split()), config.SHADOWING_MIN_WORDS)

    def test_a_short_text_is_one_passage(self) -> None:
        self.assertEqual(speech_drills.split_passages("I went home."), ["I went home."])
        self.assertEqual(speech_drills.split_passages(""), [])

    def test_pick_passage_prefers_unread_then_weakest(self) -> None:
        read = {"text": "a", "attempts": 2, "best_score": 0.9}
        weak = {"text": "b", "attempts": 1, "best_score": 0.5}
        fresh = {"text": "c", "attempts": 0, "best_score": None}
        self.assertIs(speech_drills.pick_passage([read, weak, fresh]), fresh)
        self.assertIs(speech_drills.pick_passage([read, weak]), weak)
        self.assertIsNone(speech_drills.pick_passage([]))


class AlignReadingTests(unittest.TestCase):
    REFERENCE = "Yesterday we shipped the new release to our clients."

    def statuses(self, result: Dict) -> Dict[str, str]:
        return {s["word"]: s["status"] for s in result["segments"] if "word" in s}

    def test_a_perfect_reading_scores_one_and_ignores_fillers(self) -> None:
        heard = timed_words("Yesterday uh we shipped the new release to our clients.")
        result = speech_drills.align_reading(self.REFERENCE, heard)
        self.assertEqual(result["score"], 1.0)
        self.assertEqual((result["ok"], result["extra"]), (9, 0))
        text = "".join(s.get("word", s.get("text", "")) for s in result["segments"])
        self.assertEqual(text, self.REFERENCE)

    def test_marks_missed_wrong_unclear_and_extra_words(self) -> None:
        heard = timed_words(
            "Yesterday we ship the new new release to clients.", confidence={6: 0.3}
        )
        result = speech_drills.align_reading(self.REFERENCE, heard)
        statuses = self.statuses(result)
        self.assertEqual(statuses["shipped"], "wrong")
        self.assertEqual(statuses["our"], "missed")
        self.assertEqual(statuses["release"], "unclear")
        wrong = next(s for s in result["segments"] if s.get("word") == "shipped")
        self.assertEqual(wrong["heard"], "ship")
        self.assertEqual(result["extra"], 1)
        self.assertEqual([s["extra"] for s in result["segments"] if "extra" in s], ["new"])
        # 7 of 9 right: the unclear word still counts.
        self.assertEqual(result["score"], round(7 / 9, 3))

    def test_numbers_are_not_scored(self) -> None:
        heard = timed_words("We started in twenty twenty four.")
        result = speech_drills.align_reading("We started in 2024.", heard)
        self.assertEqual(result["scored_words"], 3)
        self.assertEqual(result["score"], 1.0)
        self.assertEqual(self.statuses(result)["2024"], "skip")

    def test_contractions_match_as_one_word(self) -> None:
        heard = timed_words("I'll explore the codebase.")
        result = speech_drills.align_reading("I’ll explore the codebase.", heard)
        self.assertEqual(result["score"], 1.0)


class TalkPromptTests(unittest.TestCase):
    def test_talk_prompt_differs_from_the_monologue_prompt(self) -> None:
        for offset in range(30):
            day = dt.date(2026, 9, 1) + dt.timedelta(days=offset)
            self.assertNotEqual(
                speech_drills.talk_prompt_index(day), learner_model.speaking_prompt_index(day)
            )


if __name__ == "__main__":
    unittest.main()
