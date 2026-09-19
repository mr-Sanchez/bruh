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

APP_NAME: Final[str] = "Voice Practice Coach"

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
# Topic accuracy is measured over this many most recent attempts.
TOPIC_ACCURACY_WINDOW: Final[int] = 20

# --- Free drills (Stage 2, $0) ---------------------------------------------
# A fix card is a sentence scramble while the item is new or in box 1 and its
# correction has SCRAMBLE_MIN..SCRAMBLE_MAX words; typed recall after that.
SCRAMBLE_MIN_WORDS: Final[int] = 3
SCRAMBLE_MAX_WORDS: Final[int] = 16
# Typed answers longer than this are compared by the learner, not by code:
# exact matching a 20-word sentence fails on harmless variations.
TYPED_CHECK_MAX_WORDS: Final[int] = 16
# A topic drill (cloze) counts as "correct" in the attempts log at this share
# of right gaps; its exact share is stored as `score` and feeds topic accuracy.
DRILL_PASS_SCORE: Final[float] = 0.8
# Cloze on improved_version: which words become gaps, per topic. "to" is left
# out of prepositions on purpose - it is mostly the infinitive marker.
CLOZE_WORDS: Final[dict] = {
    "articles": ("a", "an", "the"),
    "prepositions": (
        "about", "after", "at", "before", "by", "during", "for", "from", "in",
        "into", "of", "on", "over", "since", "through", "under", "until", "with",
    ),
}
CLOZE_MAX_GAPS: Final[int] = 15

# External references per topic: free online deep links, no API calls. No
# textbook references - decided 2026-09-19, the learner does not study from books.
_CAMBRIDGE_GRAMMAR: Final[str] = "https://dictionary.cambridge.org/grammar/british-grammar/"
TOPIC_RESOURCES: Final[dict] = {
    "verb_tense": (
        ("Cambridge Grammar: Tenses and time", _CAMBRIDGE_GRAMMAR + "tenses-and-time"),
        ("Cambridge Grammar: Conditionals", _CAMBRIDGE_GRAMMAR + "conditionals-and-wishes"),
    ),
    "articles": (("Cambridge Grammar: A/an and the", _CAMBRIDGE_GRAMMAR + "a-an-and-the"),),
    "prepositions": (("Cambridge Grammar: Prepositions", _CAMBRIDGE_GRAMMAR + "prepositions"),),
    "word_order": (("Cambridge Grammar: Word order", _CAMBRIDGE_GRAMMAR + "word-order-and-focus"),),
    "subject_verb_agreement": (
        ("Cambridge Grammar: Subject-verb agreement", _CAMBRIDGE_GRAMMAR + "subject-verb-agreement"),
    ),
    "sentence_structure": (
        ("Cambridge Grammar: Relative clauses", _CAMBRIDGE_GRAMMAR + "relative-clauses"),
    ),
}

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
