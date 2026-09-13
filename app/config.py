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

APP_NAME: Final[str] = "English Speech Recorder"

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

# --- File names ----------------------------------------------------------
WAV_FILENAME: Final[str] = "audio.wav"
TRANSCRIPT_FILENAME: Final[str] = "transcript.txt"
RESPONSE_FILENAME: Final[str] = "deepgram_response.json"


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
