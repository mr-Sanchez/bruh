"""Application configuration: paths, audio/Deepgram constants, env loading, logging.

Everything the rest of the app needs to know about "where things live" and
"which Deepgram settings we use" is defined here, so there is exactly one place
to change it.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final, Optional

APP_NAME: Final[str] = "English Coach"

# --- Audio ---------------------------------------------------------------
# 16 kHz / mono / 16-bit PCM is what Deepgram's models actually consume:
# everything sent to the API is resampled to 16 kHz internally, so recording
# higher only inflates the file (and the upload time) without improving
# accuracy. Mono because there is exactly one speaker.
SAMPLE_RATE: Final[int] = 16_000
CHANNELS: Final[int] = 1
SAMPLE_WIDTH_BYTES: Final[int] = 2  # 16-bit PCM
# ~100 ms per callback: low CPU, still responsive level metering.
BLOCK_SIZE: Final[int] = 1600

# Recordings shorter than this are treated as "empty" (nothing worth sending).
MIN_RECORDING_SECONDS: Final[float] = 0.5
# If the microphone stops delivering audio for this long, assume it was
# unplugged / disabled and stop the recording (keeping the WAV).
MIC_STALL_TIMEOUT_SECONDS: Final[float] = 4.0

# --- Deepgram ------------------------------------------------------------
# A 20-minute WAV is ~38 MB; upload + transcription needs a generous timeout.
TRANSCRIPTION_TIMEOUT_SECONDS: Final[int] = 900


@dataclass(frozen=True)
class LanguageProfile:
    """One entry of the language dropdown, and the API options behind it."""

    key: str
    label: str
    model: str
    language: str
    filler_words: bool
    model_display: str
    note: str = ""


# Deepgram's `filler_words` feature (uh, um, mhmm, uh-huh, ...) is documented
# as English-only, so it is sent for English only. For Russian the request is
# otherwise identical - still no smart_format, still no post-processing.
LANGUAGE_PROFILES: Final[tuple] = (
    LanguageProfile(
        key="en-US",
        label="English (US) - filler words preserved",
        model="nova-3",
        language="en-US",
        filler_words=True,
        model_display="Deepgram Nova-3",
        note="filler_words=true: uh, um, mhmm and hesitations are kept.",
    ),
    LanguageProfile(
        key="ru",
        label="Русский (Russian)",
        model="nova-3",
        language="ru",
        filler_words=False,
        model_display="Deepgram Nova-3",
        note=(
            "Deepgram supports filler_words for English only, so Russian "
            "hesitations may be dropped. Everything else is unchanged: no "
            "smart_format, no rewriting."
        ),
    ),
    LanguageProfile(
        key="multi",
        label="Mixed speech / code-switching (RU + EN)",
        model="nova-3",
        language="multi",
        filler_words=False,
        model_display="Deepgram Nova-3 Multilingual",
        note=(
            "Use when you switch between Russian and English in one take. "
            "No filler_words, and this model build lags the single-language "
            "one, so prefer a specific language when you can."
        ),
    ),
)

DEFAULT_LANGUAGE_KEY: Final[str] = "en-US"


def profile_by_key(key: str) -> LanguageProfile:
    """Look up a language profile, falling back to the default."""
    for profile in LANGUAGE_PROFILES:
        if profile.key == key:
            return profile
    return LANGUAGE_PROFILES[0]


def default_profile() -> LanguageProfile:
    """Startup language: DEEPGRAM_LANGUAGE if it names a known profile."""
    return profile_by_key(
        os.environ.get("DEEPGRAM_LANGUAGE", DEFAULT_LANGUAGE_KEY).strip()
        or DEFAULT_LANGUAGE_KEY
    )

API_KEY_ENV_VAR: Final[str] = "DEEPGRAM_API_KEY"
MISSING_API_KEY_MESSAGE: Final[str] = "DEEPGRAM_API_KEY is not configured."

# --- Claude (speech analysis / feedback) ----------------------------------
ANTHROPIC_API_KEY_ENV_VAR: Final[str] = "ANTHROPIC_API_KEY"
MISSING_ANTHROPIC_API_KEY_MESSAGE: Final[str] = "ANTHROPIC_API_KEY is not configured."

# Sonnet 5 keeps the cost of a daily analysis pass low (roughly $0.05-0.10 per
# session) while still following the structured-feedback instructions well.
# Swap to a different model here if quality ever needs to outweigh cost.
ANALYSIS_MODEL: Final[str] = "claude-sonnet-5"
# The coach-style feedback (per-mistake alternatives, an improved retelling,
# takeaways, scores) is several times longer than a bare list of issues, and
# adaptive thinking shares this budget - 16k leaves headroom without streaming.
ANALYSIS_MAX_TOKENS: Final[int] = 16_000
ANALYSIS_TIMEOUT_SECONDS: Final[int] = 300
DEFAULT_ANALYSIS_EFFORT: Final[str] = "medium"


def get_anthropic_api_key() -> Optional[str]:
    """Return the Anthropic API key, or None when it is not configured."""
    key = os.environ.get(ANTHROPIC_API_KEY_ENV_VAR, "").strip()
    return key or None


def analysis_effort() -> str:
    """Thinking effort for the analysis call - a cost/quality knob.

    Overridable via ANALYSIS_EFFORT (low|medium|high|xhigh|max) without a
    code change.
    """
    return os.environ.get("ANALYSIS_EFFORT", DEFAULT_ANALYSIS_EFFORT).strip() or DEFAULT_ANALYSIS_EFFORT


# --- File names ----------------------------------------------------------
# Kept for the WAV-specific helpers/tests; the actual per-session audio file
# name is whatever the browser recorded (see extension_for_mime below).
WAV_FILENAME: Final[str] = "audio.wav"
DEFAULT_AUDIO_FILENAME: Final[str] = "audio.webm"
TRANSCRIPT_FILENAME: Final[str] = "transcript.txt"
RESPONSE_FILENAME: Final[str] = "deepgram_response.json"
ANALYSIS_FILENAME: Final[str] = "analysis.json"
SESSION_META_FILENAME: Final[str] = "session.json"
PROGRESS_FILENAME: Final[str] = "progress.json"
# Picture description (Stage 4): the picture a description is about. The
# browser downscales it before upload; the extension follows its real type.
IMAGE_FILENAME_STEM: Final[str] = "image"
# Learner model (see app/learner_store.py). The item bank is a derived cache;
# attempts and usage are append-only logs and are authoritative.
ITEM_BANK_FILENAME: Final[str] = "item_bank.json"
ATTEMPTS_FILENAME: Final[str] = "attempts.jsonl"
USAGE_FILENAME: Final[str] = "usage.jsonl"

# --- Learner model -------------------------------------------------------
# Leitner boxes 1..5 and the review interval (days) after landing in each.
LEITNER_INTERVALS_DAYS: Final[tuple] = (1, 2, 4, 8, 16)
# An item in the last box is "closed" once this many later analysed
# recordings in its language went by without the mistake coming back.
LEITNER_CLOSE_AFTER_RECORDINGS: Final[int] = 3
# New cards introduced per day (decided 2026-09-19: 7-10). The lower number
# applies once more than REVIEW_BACKLOG_THRESHOLD reviews are due.
NEW_ITEMS_PER_DAY_MAX: Final[int] = 10
NEW_ITEMS_PER_DAY_MIN: Final[int] = 7
REVIEW_BACKLOG_THRESHOLD: Final[int] = 30
# Word cards (the learner's own picks from a set's vocabulary) have their own
# daily allowance of new ones (decided 2026-09-27), so they never crowd out
# mistake cards and vice versa. Reviews of both are always shown in full.
NEW_WORDS_PER_DAY: Final[int] = 5
# Topic accuracy is measured over this many most recent attempts.
TOPIC_ACCURACY_WINDOW: Final[int] = 20

# --- Cards -------------------------------------------------------------------
# A mistake card is a new Russian sentence to say in English on the same
# construction (decided 2026-09-26): the analysis writes this many per English
# mistake, and each review takes the next one, so an answer is never learned by
# heart. Mistakes analysed before that have no such sentences and are retired.
DRILLS_PER_ISSUE: Final[int] = 3
# Claude (GRADING_MODEL) checks each typed answer; verdicts are cached here per
# card sentence + answer, so the same answer twice is never paid for twice.
CARD_VERDICTS_FILENAME: Final[str] = "card_verdicts.jsonl"
# A card answer can be spoken instead of typed (2026-09-27): the clip goes to
# Deepgram (English, no filler words - it is an answer, not a fluency take)
# and the text lands in the answer field. Not kept as a recording; ≈ 0.05 ¢.
CARD_DICTATION_MAX_SECONDS: Final[int] = 60
CARD_DICTATION_MAX_BYTES: Final[int] = 3 * 1024 * 1024
# A topic drill (an AI set run, a spoken drill) counts as "correct" in the
# attempts log at this score; the exact score feeds topic accuracy.
DRILL_PASS_SCORE: Final[float] = 0.8
# Words and phrases the learner picked for cards from a set's vocabulary:
# append-only, AUTHORITATIVE (the choice cannot be rebuilt), the newest record
# per (set, word) wins - "add" or "remove".
WORD_PICKS_FILENAME: Final[str] = "word_picks.jsonl"

# --- Roadmap (Stage 8) --------------------------------------------------------
# The learner's own marks on lessons («Пропустить», «Уже знаю»): append-only,
# authoritative like attempts.jsonl - the latest mark on a lesson wins.
ROADMAP_MARKS_FILENAME: Final[str] = "roadmap_marks.jsonl"
# A lesson is mastered after this many set runs at DRILL_PASS_SCORE or better,
# on different days (decided 2026-09-26, a first guess).
LESSON_MASTERY_RUNS: Final[int] = 2

# --- Irregular verbs ----------------------------------------------------------
# Every checked verb of the free drill: append-only, AUTHORITATIVE like
# attempts.jsonl (the learner's answers cannot be rebuilt). Kept out of the
# learner model, like dictation; counts for the day streak.
IRREGULAR_VERBS_FILENAME: Final[str] = "irregular_verbs.jsonl"

# --- «Сегодня» daily workout (Stage 3) -----------------------------------
# The workout aims at ~10 minutes: cards, one live activity, dictation.
# Cards past WORKOUT_MAX_CARDS stay in the queue and can be done on «Занятия».
WORKOUT_MAX_CARDS: Final[int] = 15
WORKOUT_MINUTES_PER_CARD: Final[float] = 0.5
WORKOUT_MINUTES_MONOLOGUE: Final[float] = 3.0
# How many days of exercise history the «История» tab shows.
ACTIVITY_HISTORY_DAYS: Final[int] = 60

# Speaking prompts (monologue, «60 секунд») live in app/themes.py since
# 2026-09-26: one list per context («уклон»), one prompt per day.

# --- Lesson theory (Stage 8, R4) ----------------------------------------------
# A roadmap lesson's theory: one Claude call on a click, kept per lesson in
# data/theory/<lesson>.json. «Сгенерировать заново» adds a version and keeps
# the old ones (paid for, never rewritten). Not tied to a context «уклон».
THEORY_MODEL: Final[str] = "claude-sonnet-5"
THEORY_EFFORT: Final[str] = "low"
THEORY_MAX_TOKENS: Final[int] = 8_000
THEORY_DIRNAME: Final[str] = "theory"
# The learner's own mistakes on the topic the theory comments on.
THEORY_OWN_MISTAKES: Final[int] = 6
# Shown next to the button until the usage log has a real average.
THEORY_COST_ESTIMATE_USD: Final[float] = 0.03

# --- Lesson spoken task (Stage 8, R6) ---------------------------------------------
# 1-2 minutes of speech that needs the lesson's rule. Sonnet writes the tasks
# on a click (a few per context, kept in data/lesson_tasks/<lesson>.json); the
# take is an ordinary monologue analysed with the rule in focus, and its rule
# score is logged once as a topic attempt of this exercise.
# 2026-09-27: Haiku -> Sonnet. A task the rule can be dodged in makes the rule
# score meaningless; tasks are written rarely and kept, so the price is noise.
LESSON_TASK_MODEL: Final[str] = "claude-sonnet-5"
LESSON_TASK_EFFORT: Final[str] = "low"
LESSON_TASKS_PER_CALL: Final[int] = 3
LESSON_TASK_MAX_TOKENS: Final[int] = 4_000
LESSON_TASKS_DIRNAME: Final[str] = "lesson_tasks"
LESSON_TASK_EXERCISE: Final[str] = "lesson_task"

# --- Module entry test (Stage 8, R7) -----------------------------------------------
# A short test over a whole module, to mark what the learner already knows
# honestly. Claude writes it on a click (kept, redone for free); every answer
# is a choice or a gap, so it is checked on the server without a model call.
MODULE_TEST_MODEL: Final[str] = "claude-sonnet-5"
MODULE_TEST_EFFORT: Final[str] = "low"
MODULE_TEST_MAX_TOKENS: Final[int] = 6_000
MODULE_TEST_DIRNAME: Final[str] = "module_tests"
MODULE_TEST_EXERCISE: Final[str] = "module_test"
# One multiple-choice question and one gap per lesson (app/module_test.py); a
# lesson counts as known when all of its questions are right.
MODULE_TEST_COST_ESTIMATE_USD: Final[float] = 0.03

# --- Contexts «уклон» (Stage 8, R3) ------------------------------------------
# The learner's own contexts, the last one used and the prompts Claude wrote
# for own contexts (paid, so kept; app/theme_store.py).
THEMES_FILENAME: Final[str] = "themes.json"
THEMES_MAX_CUSTOM: Final[int] = 30
# Speaking prompts for an own context: one Haiku call on a click, ~0.1 cent.
THEME_PROMPTS_MODEL: Final[str] = "claude-haiku-4-5"
THEME_PROMPTS_COUNT: Final[int] = 8
THEME_PROMPTS_MAX_TOKENS: Final[int] = 2_000

# --- Activities & picture description (Stage 4) ---------------------------
# session.json `kind`: which activity produced the session. Sessions written
# before Stage 4 have no kind and are monologues.
KIND_MONOLOGUE: Final[str] = "monologue"
KIND_PICTURE: Final[str] = "picture"
# Spoken drills (Stage 6): measured from Deepgram's word timings, never sent
# to Claude - so they are not analysed and never feed the item bank.
KIND_TALK: Final[str] = "talk"
KIND_SHADOWING: Final[str] = "shadowing"
DRILL_KINDS: Final[tuple] = (KIND_TALK, KIND_SHADOWING)
SESSION_KINDS: Final[tuple] = (KIND_MONOLOGUE, KIND_PICTURE) + DRILL_KINDS
# session.json `input_mode`: spoken (Deepgram transcript) or typed by hand.
# A typed text is stored as transcript.txt verbatim and never goes to Deepgram.
INPUT_VOICE: Final[str] = "voice"
INPUT_TEXT: Final[str] = "text"
TYPED_TEXT_MAX_CHARS: Final[int] = 10_000
# Image types Claude accepts, by media type -> file extension. The type is
# sniffed from the bytes, never taken from the browser's claim.
IMAGE_EXTENSIONS_BY_MEDIA_TYPE: Final[dict] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
# Claude's per-image API limit is 5 MB; a ~1000 px JPEG from the browser is
# ~100-300 KB, so hitting this means the downscale did not happen.
IMAGE_MAX_BYTES: Final[int] = 5 * 1024 * 1024
# The browser scales the long side down to this before upload: ~1000x750 px
# is ~1k input tokens (about 0.2 cents), and enough detail to describe.
IMAGE_MAX_SIDE_PX: Final[int] = 1000

# --- AI exercise sets (Stage 5, ~2-4 cents) --------------------------------
# One set is 8-10 new exercises on one topic, generated on an explicit click
# from the learner's own mistakes and rules on it, then kept on disk so it can
# be redone for free. Gaps and fixes are checked in the browser; translations
# are graded by Claude in one call at the end of the set (decided 2026-09-19).
EXERCISE_SET_MODEL: Final[str] = "claude-sonnet-5"
EXERCISE_SET_EFFORT: Final[str] = "low"
EXERCISE_SET_MAX_TOKENS: Final[int] = 10_000
# Grading translations (a set's, and a card's on click) - Sonnet since
# 2026-09-26: Haiku failed right answers ("a call" for "the call") and named
# the wrong mistake, and its comment becomes the card's explanation. It also
# tags each mistake with its own topic. ~1 cent per set; low effort is enough.
GRADING_MODEL: Final[str] = "claude-sonnet-5"
GRADING_EFFORT: Final[str] = "low"
GRADING_MAX_TOKENS: Final[int] = 6_000
EXERCISE_API_TIMEOUT_SECONDS: Final[int] = 180
# How many of each exercise a set asks for (easy to hard, in this order).
SET_GAPS: Final[int] = 3
SET_FIXES: Final[int] = 2
SET_TRANSLATIONS: Final[int] = 4
# Useful words and phrases written along with a set (2026-09-27, same call,
# ≈ +0.2 ¢), shown after the run; the learner picks which become RU -> EN cards.
SET_VOCABULARY: Final[int] = 8
# The learner's word cards listed in a set request, so new ones are suggested.
SET_KNOWN_WORDS: Final[int] = 80
# The learner's own items on the topic shown to the generator as seeds, and
# earlier set sentences on the topic it is told not to repeat - enough for
# ~10 sets (≈ 1.5k input tokens); an exercise that still repeats any earlier
# sentence of the topic is dropped after generation (R5, 2026-09-26).
SET_SEED_ITEMS: Final[int] = 8
SET_AVOID_SENTENCES: Final[int] = 90
# Shown next to the button until the usage log has a real average.
SET_COST_ESTIMATE_USD: Final[float] = 0.03
WORKOUT_MINUTES_SET: Final[float] = 6.0
# The set files; like analysis.json they are paid for and cannot be rebuilt.
PRACTICE_DIRNAME: Final[str] = "practice"

# --- Spoken drills (Stage 6, Deepgram only) --------------------------------
# «60 секунд»: the same prompt TALK_ROUNDS times in a row, a minute each (the
# 4-3-2 idea: each retelling gets easier). Only round 1 - the spontaneous
# take - is logged as an attempt; rounds 2-3 are practice (decided 2026-09-19).
TALK_SECONDS: Final[int] = 60
TALK_ROUNDS: Final[int] = 3
# Both drills log a topic attempt on this topic (decided 2026-09-19).
FLUENCY_TOPIC: Final[str] = "filler_words_fluency"
# Deepgram's documented filler tokens (English only, filler_words=true).
FILLER_TOKENS: Final[frozenset] = frozenset(
    {"uh", "um", "mhmm", "mm-mm", "uh-uh", "uh-huh", "nuh-uh", "hmm", "mm"}
)
# A silence between two words at least this long counts as a hesitation;
# shorter ones are ordinary breaths and sentence breaks.
LONG_PAUSE_SECONDS: Final[float] = 2.0
# Talk score: hesitations (fillers + long pauses) per speaking minute. At or
# under the first number the score is 1.0, at the second it is 0; linear in
# between, so the 0.8 pass mark sits at 4 per minute.
FLUENCY_TARGET_PER_MIN: Final[float] = 2.0
FLUENCY_ZERO_PER_MIN: Final[float] = 12.0
# Shadowing reads the improved_version aloud in passages of whole sentences,
# about this many words each.
SHADOWING_MIN_WORDS: Final[int] = 20
SHADOWING_MAX_WORDS: Final[int] = 50
# A matched word Deepgram heard with less confidence than this is marked as
# unclear - often a sign of a pronunciation problem.
UNCLEAR_CONFIDENCE: Final[float] = 0.6
WORKOUT_MINUTES_SPEECH: Final[float] = 4.0

# --- Listening dictation (Stage 7, $0: YouTube subtitles only) ------------
# A lesson is one YouTube video: its audio plus the reference text, which
# always comes from the video's own subtitles (manual ones first, otherwise
# YouTube's automatic ones). Nothing here calls Deepgram or Claude - a video
# without usable subtitles is refused instead (decided 2026-09-20).
DICTATION_DIRNAME: Final[str] = "dictation"
LESSON_META_FILENAME: Final[str] = "lesson.json"
LESSON_SUBTITLES_FILENAME: Final[str] = "subtitles.vtt"
# Append-only, authoritative: every dictated sentence, like attempts.jsonl.
LESSON_RESULTS_FILENAME: Final[str] = "results.jsonl"
DICTATION_AUDIO_STEM: Final[str] = "audio"
LESSON_SCHEMA_VERSION: Final[int] = 1
# lesson.json `status` while the import runs in the background.
LESSON_STATUS_IMPORTING: Final[str] = "importing"
LESSON_STATUS_READY: Final[str] = "ready"
LESSON_STATUS_ERROR: Final[str] = "error"
# Longer videos mean a long download and a lesson nobody finishes.
DICTATION_MAX_SECONDS: Final[int] = 20 * 60
# Subtitle languages accepted per language profile, best first. An automatic
# caption track is accepted only in the video's own language - YouTube also
# offers machine translations, which do not match what is being said.
DICTATION_SUBTITLE_LANGUAGES: Final[dict] = {
    "en-US": ("en",),
    "ru": ("ru",),
    "multi": ("en", "ru"),
}
# Sentences are built from the subtitle cues: shorter ones are joined with
# the next, longer ones are split at a comma (or hard-capped).
DICTATION_MIN_WORDS: Final[int] = 4
DICTATION_MAX_WORDS: Final[int] = 18
# Subtitle timings are tight; a little air on both sides keeps the first and
# last syllable of a sentence audible when only that segment is played.
DICTATION_PAD_SECONDS: Final[float] = 0.25
# The daily workout's dictation step is done at this many sentences.
DICTATION_DAILY_SENTENCES: Final[int] = 5
WORKOUT_MINUTES_DICTATION: Final[float] = 4.0
# «Сложные слова» on «Прогресс»: words missed or hinted at least this often.
DICTATION_TRICKY_MIN_MISSES: Final[int] = 2
DICTATION_TRICKY_LIMIT: Final[int] = 20

# --- Dictation translation (about a cent per call) ------------------------
# After the dictation, a lesson can be translated part by part (English ->
# Russian). Two explicit clicks pay for it: one Haiku call cuts the lesson
# into parts at natural breaks (decided 2026-09-26 - a model, not pauses), one
# Sonnet call per part reviews the learner's translation. Both results are
# kept on disk. Mistakes stay in the lesson: nothing goes to the item bank.
TRANSLATION_SPLIT_MODEL: Final[str] = "claude-haiku-4-5"
# 2026-09-27: Haiku -> Sonnet, like GRADING_MODEL - judging a learner's answer
# is where a wrong verdict teaches the wrong thing.
TRANSLATION_REVIEW_MODEL: Final[str] = "claude-sonnet-5"
TRANSLATION_REVIEW_EFFORT: Final[str] = "low"
TRANSLATION_SPLIT_MAX_TOKENS: Final[int] = 1_500
TRANSLATION_REVIEW_MAX_TOKENS: Final[int] = 6_000
# A part is this many sentences; a lesson that fits in one part needs no call.
TRANSLATION_PART_MIN_SENTENCES: Final[int] = 5
TRANSLATION_PART_MAX_SENTENCES: Final[int] = 15
LESSON_PARTS_FILENAME: Final[str] = "parts.json"
# Append-only like results.jsonl: every submitted translation with its review.
LESSON_TRANSLATIONS_FILENAME: Final[str] = "translations.jsonl"

# --- Text translation (2026-09-27, a few cents per text) -----------------
# «Перевод текста»: an English text - written by Claude in a context
# («уклон») at a chosen size, or pasted by the learner - is translated into
# Russian (typed or dictated) and reviewed by Sonnet on a click: accuracy,
# mistakes, unnatural spots, a final version, and useful phrases the learner
# may pick as word cards. Both calls are explicit and kept on disk.
TEXT_WRITE_MODEL: Final[str] = "claude-sonnet-5"
TEXT_WRITE_EFFORT: Final[str] = "low"
TEXT_WRITE_MAX_TOKENS: Final[int] = 6_000
TEXT_REVIEW_MODEL: Final[str] = "claude-sonnet-5"
TEXT_REVIEW_EFFORT: Final[str] = "low"
TEXT_REVIEW_MAX_TOKENS: Final[int] = 12_000
# A generated text takes 5-15 minutes to translate (the user's brief): size
# key -> (label, minutes, words). About 17-20 words a minute for a written
# EN -> RU translation.
TEXT_SIZES: Final[dict] = {
    "short": ("Короткий", 5, (80, 110)),
    "medium": ("Средний", 10, (160, 200)),
    "long": ("Длинный", 15, (240, 290)),
}
TEXT_DEFAULT_SIZE: Final[str] = "medium"
TEXT_WORDS_PER_MINUTE: Final[int] = 18
# A pasted text: long enough to be a text, short enough for one review.
TEXT_CUSTOM_MIN_CHARS: Final[int] = 20
TEXT_CUSTOM_MAX_CHARS: Final[int] = 4_000
TEXT_TRANSLATION_MAX_CHARS: Final[int] = 8_000
# Useful phrases the review suggests as word cards (the learner picks).
TEXT_PHRASES: Final[int] = 8
# Earlier titles in the same context, so a new text is about something else.
TEXT_AVOID_TITLES: Final[int] = 20
# Shown next to the buttons until the usage log has real averages.
TEXT_WRITE_COST_ESTIMATE_USD: Final[float] = 0.01
TEXT_REVIEW_COST_ESTIMATE_USD: Final[float] = 0.03
TEXTS_DIRNAME: Final[str] = "translate"

# --- Assistant (2026-09-27, a cent or two per answer) -------------------
# «Спросить ИИ»: the learner selects text on any screen, asks about it, and
# talks it over with Claude in a panel above the page. The selection, the
# card around it and the question open a chat; every answer is a click on
# «Отправить», and every chat is kept (data/assistant/<id>.json).
ASSISTANT_MODEL: Final[str] = "claude-sonnet-5"
ASSISTANT_EFFORT: Final[str] = "low"
ASSISTANT_MAX_TOKENS: Final[int] = 4_000
ASSISTANT_SELECTION_MAX_CHARS: Final[int] = 2_000
ASSISTANT_CONTEXT_MAX_CHARS: Final[int] = 6_000
ASSISTANT_MESSAGE_MAX_CHARS: Final[int] = 2_000
# The whole chat is sent with every answer, so a chat has a length limit.
ASSISTANT_MAX_MESSAGES: Final[int] = 40
ASSISTANT_COST_ESTIMATE_USD: Final[float] = 0.015
ASSISTANT_DIRNAME: Final[str] = "assistant"

# --- Pricing (estimates for the usage log, USD) --------------------------
# Per million tokens: (input, output). Cache writes bill at 1.25x input,
# cache reads at 0.1x input. Unknown models are logged with cost_usd = None.
CLAUDE_PRICE_PER_MTOK: Final[dict] = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-5": (5.00, 25.00),
}
CLAUDE_CACHE_WRITE_MULTIPLIER: Final[float] = 1.25
CLAUDE_CACHE_READ_MULTIPLIER: Final[float] = 0.10
# Deepgram pre-recorded pay-as-you-go, per audio minute, by language profile.
DEEPGRAM_PRICE_PER_MINUTE: Final[dict] = {"en-US": 0.0043, "ru": 0.0043, "multi": 0.0052}

# MediaRecorder mime types we expect from a browser, mapped to a file
# extension. Deepgram auto-detects the container from the bytes, so this
# mapping only needs to be good enough to give the file a sensible name.
_AUDIO_EXTENSIONS_BY_MIME_PREFIX: Final[tuple] = (
    ("audio/webm", ".webm"),
    ("audio/ogg", ".ogg"),
    ("audio/wav", ".wav"),
    ("audio/x-wav", ".wav"),
    ("audio/mp4", ".m4a"),
    ("audio/mpeg", ".mp3"),
)


def extension_for_mime(mime_type: Optional[str]) -> str:
    """Best-effort file extension for a browser-reported audio mime type."""
    value = (mime_type or "").split(";", 1)[0].strip().lower()
    for prefix, extension in _AUDIO_EXTENSIONS_BY_MIME_PREFIX:
        if value == prefix:
            return extension
    logging.getLogger(__name__).warning(
        "Unrecognised audio mime type %r; saving with a generic extension", mime_type
    )
    return ".audio"


def base_dir() -> Path:
    """Root directory for recordings/, logs/ and .env.

    When frozen by PyInstaller this is the folder holding the .exe, so the
    user's data sits next to the program instead of inside a temp folder.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def recordings_dir() -> Path:
    return base_dir() / "recordings"


def logs_dir() -> Path:
    return base_dir() / "logs"


def data_dir() -> Path:
    """Cross-session app data: progress.json and the learner model files."""
    return base_dir() / "data"


def practice_dir() -> Path:
    """AI exercise sets (data/practice/<set id>.json)."""
    return data_dir() / PRACTICE_DIRNAME


def theory_dir() -> Path:
    """Lesson theory, every version (data/theory/<lesson id>.json)."""
    return data_dir() / THEORY_DIRNAME


def lesson_tasks_dir() -> Path:
    """Spoken tasks of roadmap lessons (data/lesson_tasks/<lesson id>.json)."""
    return data_dir() / LESSON_TASKS_DIRNAME


def module_tests_dir() -> Path:
    """Module entry tests and their runs (data/module_tests/<module key>.json)."""
    return data_dir() / MODULE_TEST_DIRNAME


def dictation_dir() -> Path:
    """Dictation lessons (data/dictation/<video id>/: audio, subtitles, results)."""
    return data_dir() / DICTATION_DIRNAME


def texts_dir() -> Path:
    """Texts to translate with every translation and review (data/translate/<id>.json)."""
    return data_dir() / TEXTS_DIRNAME


def assistant_dir() -> Path:
    """Chats with the assistant, every message kept (data/assistant/<chat id>.json)."""
    return data_dir() / ASSISTANT_DIRNAME


def load_environment() -> None:
    """Load a .env file next to the app, if python-dotenv is installed.

    Real environment variables always win over .env values.
    """
    env_path = base_dir() / ".env"
    try:
        from dotenv import load_dotenv
    except ImportError:  # dotenv is optional
        return
    if env_path.is_file():
        load_dotenv(env_path, override=False)


def get_api_key() -> Optional[str]:
    """Return the Deepgram API key, or None when it is not configured."""
    key = os.environ.get(API_KEY_ENV_VAR, "").strip()
    return key or None


def mip_opt_out() -> bool:
    """Opt out of Deepgram's Model Improvement Program (paid plans only)."""
    return os.environ.get("DEEPGRAM_MIP_OPT_OUT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def setup_logging() -> Path:
    """Configure logging to logs/app.log (rotating) plus stderr."""
    directory = logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "app.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if sys.stderr is not None:  # None in a PyInstaller --windowed build
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    # The SDK's HTTP layer is chatty at DEBUG and can echo request headers.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return log_path
