"""Filesystem helpers, formatting and the per-recording Session model."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app import config

logger = logging.getLogger(__name__)

SESSION_DIR_FORMAT = "%Y-%m-%d_%H-%M-%S"
HUMAN_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_duration(seconds: float) -> str:
    """Format seconds as MM:SS, or HH:MM:SS once past an hour."""
    if seconds < 0 or seconds != seconds:  # negative or NaN
        seconds = 0.0
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


@dataclass
class Session:
    """One recording session and the files that belong to it."""

    directory: Path
    started_at: dt.datetime
    duration_seconds: float = 0.0
    transcript: Optional[str] = None

    @property
    def wav_path(self) -> Path:
        return self.directory / config.WAV_FILENAME

    @property
    def transcript_path(self) -> Path:
        return self.directory / config.TRANSCRIPT_FILENAME

    @property
    def response_path(self) -> Path:
        return self.directory / config.RESPONSE_FILENAME

    @property
    def started_at_text(self) -> str:
        return self.started_at.strftime(HUMAN_TIME_FORMAT)


def create_session(root: Optional[Path] = None, when: Optional[dt.datetime] = None) -> Session:
    """Create recordings/YYYY-MM-DD_HH-MM-SS/ and return the Session for it."""
    root = root or config.recordings_dir()
    when = when or dt.datetime.now()
    directory = root / when.strftime(SESSION_DIR_FORMAT)
    # Two sessions inside the same second would otherwise collide.
    suffix = 1
    unique = directory
    while unique.exists():
        suffix += 1
        unique = root / f"{when.strftime(SESSION_DIR_FORMAT)}_{suffix}"
    unique.mkdir(parents=True)
    return Session(directory=unique, started_at=when)


def write_transcript(
    session: Session,
    transcript: str,
    profile: Optional[config.LanguageProfile] = None,
) -> Path:
    """Write transcript.txt: a short header, a '---' separator, then the words.

    The transcript body is written exactly as Deepgram returned it — no
    clean-up, no re-wrapping, no post-processing.
    """
    profile = profile or config.default_profile()
    header = (
        f"Recording: {session.started_at_text}\n"
        f"Duration: {format_duration(session.duration_seconds)}\n"
        f"Model: {profile.model_display}\n"
        f"Language: {profile.language}\n"
        f"\n---\n\n"
    )
    path = session.transcript_path
    path.write_text(header + transcript + "\n", encoding="utf-8")
    return path


def write_json(path: Path, payload: Any) -> Path:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def read_transcript_body(path: Path) -> str:
    """Read back only the part of transcript.txt after the '---' separator."""
    text = path.read_text(encoding="utf-8")
    marker = "\n---\n"
    index = text.find(marker)
    if index == -1:
        return text.strip()
    return text[index + len(marker) :].strip()


def wav_duration_seconds(path: Path) -> float:
    """Duration of a WAV file, or 0.0 if it cannot be read."""
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
        return frames / rate if rate else 0.0
    except (OSError, wave.Error):
        logger.warning("Could not read duration of %s", path)
        return 0.0


def open_path(path: Path) -> None:
    """Open a file or folder with the OS default handler."""
    target = str(path)
    if sys.platform == "win32":
        os.startfile(target)  # noqa: S606  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])
