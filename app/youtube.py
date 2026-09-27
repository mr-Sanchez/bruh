"""Importing a YouTube video as a dictation lesson: audio + its subtitles.

The only part of the app that talks to YouTube. It knows nothing about HTTP
or FastAPI: `YouTubeFetcher.fetch()` is handed a target directory, puts the
audio file in it and returns the subtitle track as text.

Two deliberate limits (decided 2026-09-20):

  * **subtitles or nothing.** The reference text a dictation is checked
    against is the video's own caption track - manual first, YouTube's
    automatic one otherwise. A video without one is refused; the app never
    pays Deepgram to invent a reference.
  * **no transcoding.** The audio is taken as YouTube serves it (m4a/webm,
    both of which a browser plays), so FFmpeg is not needed.

yt-dlp is imported lazily inside the default factory: it is the one heavy,
optional dependency, and the rest of the app must keep working without it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from app import config

logger = logging.getLogger(__name__)

# youtu.be/<id>, /watch?v=<id>, /shorts/<id>, /embed/<id>, /live/<id>.
_URL_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"youtu\.be/(?P<id>[A-Za-z0-9_-]{6,20})"),
    re.compile(r"[?&]v=(?P<id>[A-Za-z0-9_-]{6,20})"),
    re.compile(r"youtube\.com/(?:shorts|embed|live|v)/(?P<id>[A-Za-z0-9_-]{6,20})"),
)
_BARE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SUBTITLE_SUFFIX = ".vtt"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

MANUAL = "manual"
AUTOMATIC = "automatic"


class YouTubeError(Exception):
    """Base class; the message is meant to be shown to the learner (Russian)."""


class MissingYouTubeToolError(YouTubeError):
    """yt-dlp is not installed - the dictation activity cannot import."""


class UnsupportedUrlError(YouTubeError):
    """The link is not a YouTube video."""


class VideoTooLongError(YouTubeError):
    """Longer than DICTATION_MAX_SECONDS."""


class NoSubtitlesError(YouTubeError):
    """No usable subtitle track in a language we practise."""


class DownloadFailedError(YouTubeError):
    """YouTube (or the network) refused; nothing was saved."""


@dataclass(frozen=True)
class FetchedVideo:
    """What one import produced: the saved audio, plus the reference text."""

    video_id: str
    url: str
    title: str
    uploader: str
    duration_seconds: float
    audio_filename: str
    subtitles: str
    subtitle_language: str
    subtitle_kind: str


def video_id_from_url(url: str) -> str:
    """The video id in a YouTube link (or a bare id), else UnsupportedUrlError."""
    value = (url or "").strip()
    if _BARE_ID.match(value):
        return value
    for pattern in _URL_PATTERNS:
        match = pattern.search(value)
        if match:
            return match.group("id")
    raise UnsupportedUrlError("Нужна ссылка на видео YouTube (youtube.com или youtu.be).")


def _language_matches(track: str, wanted: str) -> bool:
    """'en' matches 'en', 'en-US' and yt-dlp's 'en-orig'."""
    track = (track or "").lower()
    return track == wanted or track.startswith(f"{wanted}-")


def pick_subtitle_track(
    info: Dict[str, Any], languages: Sequence[str]
) -> Tuple[str, str]:
    """(track key, kind) of the subtitles to download, best first.

    Manual captions in any wanted language win. Automatic captions are only
    accepted in the video's own language: YouTube offers machine
    translations of them into every language, and those do not match a
    single word of what is actually being said. Even in the video's own
    language the plain track ("en") is often such a translation of the
    original recognition ("en-orig", its URL carries `tlang=`) - and YouTube
    answers those with 429 Too Many Requests (2026-09-27) - so the original
    track comes first and a translated one is never taken.
    """
    manual = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    for wanted in languages:
        for track in manual:
            if _language_matches(track, wanted):
                return track, MANUAL
    spoken = (info.get("language") or "").lower()
    for wanted in languages:
        if spoken and not _language_matches(spoken, wanted):
            continue
        tracks = [
            track
            for track in automatic
            if _language_matches(track, wanted) and not _is_translation(automatic[track])
        ]
        # "en-orig" before "en": the untouched recognition of what is said.
        tracks.sort(key=lambda track: not track.lower().endswith("-orig"))
        if tracks:
            return tracks[0], AUTOMATIC
    raise NoSubtitlesError(
        "У этого видео нет субтитров на нужном языке — возьмите другое видео."
    )


def _is_translation(formats: Any) -> bool:
    """A caption track YouTube machine-translates from another one (tlang=)."""
    return any(
        "tlang=" in str(fmt.get("url") or "")
        for fmt in (formats or [])
        if isinstance(fmt, dict)
    )


def _default_ydl_factory(options: Dict[str, Any]) -> Any:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:  # yt-dlp is the optional dictation dependency
        raise MissingYouTubeToolError(
            "Для диктанта нужен yt-dlp: установите зависимости "
            "(pip install -r requirements.txt) и перезапустите приложение."
        ) from exc
    return YoutubeDL(options)


class YouTubeFetcher:
    """Downloads one video's audio and subtitle track into a directory."""

    def __init__(
        self,
        *,
        ydl_factory: Callable[[Dict[str, Any]], Any] = _default_ydl_factory,
        max_seconds: int = config.DICTATION_MAX_SECONDS,
    ) -> None:
        self._ydl_factory = ydl_factory
        self._max_seconds = max_seconds

    # -------------------------------------------------------------- public
    def fetch(self, url: str, target_dir: Path, languages: Sequence[str]) -> FetchedVideo:
        """Save the audio in `target_dir` and return it with its subtitles."""
        video_id = video_id_from_url(url)
        info = self._extract(url, {}, download=False)
        duration = float(info.get("duration") or 0.0)
        if self._max_seconds and duration > self._max_seconds:
            raise VideoTooLongError(
                f"Видео длиннее {self._max_seconds // 60} минут — возьмите отрывок покороче."
            )
        track, kind = pick_subtitle_track(info, languages)
        logger.info(
            "Dictation import %s: %.0f s, %s subtitles (%s)", video_id, duration, kind, track
        )

        target_dir.mkdir(parents=True, exist_ok=True)
        stem = str(target_dir / config.DICTATION_AUDIO_STEM)
        self._extract(
            url,
            {
                # No transcoding: whatever YouTube serves is what a browser plays.
                "format": "bestaudio[ext=m4a]/bestaudio/best",
                "outtmpl": {"default": f"{stem}.%(ext)s"},
                "writesubtitles": kind == MANUAL,
                "writeautomaticsub": kind == AUTOMATIC,
                "subtitleslangs": [track],
                "subtitlesformat": "vtt",
                "postprocessors": [],
                "overwrites": True,
            },
            download=True,
        )
        audio_path = self._saved_audio(target_dir)
        subtitles = self._saved_subtitles(target_dir)
        return FetchedVideo(
            video_id=video_id,
            url=info.get("webpage_url") or url,
            title=str(info.get("title") or video_id),
            uploader=str(info.get("uploader") or ""),
            duration_seconds=duration,
            audio_filename=audio_path.name,
            subtitles=subtitles,
            subtitle_language=track,
            subtitle_kind=kind,
        )

    # ------------------------------------------------------------- private
    def _extract(self, url: str, options: Dict[str, Any], *, download: bool) -> Dict[str, Any]:
        merged: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            # yt-dlp's ANSI colours would end up in the learner's error message.
            "color": {"stdout": "never", "stderr": "never"},
            "noplaylist": True,
            "retries": 3,
            "socket_timeout": 30,
            "skip_download": not download,
            **options,
        }
        try:
            downloader = self._ydl_factory(merged)
            info = downloader.extract_info(url, download=download)
        except YouTubeError:
            raise
        except Exception as exc:  # yt-dlp raises its own hierarchy
            logger.warning("yt-dlp failed for %s: %s", url, exc)
            message = _ANSI.sub("", str(exc)).removeprefix("ERROR: ").strip()
            if "429" in message:
                raise DownloadFailedError(
                    "YouTube временно ограничил загрузки (429 Too Many Requests). "
                    "Подождите несколько минут и попробуйте ещё раз."
                ) from exc
            raise DownloadFailedError(f"Не удалось скачать видео: {message}") from exc
        if not isinstance(info, dict):
            raise DownloadFailedError("YouTube не вернул данные о видео.")
        if info.get("entries"):  # a link to a playlist resolved to its first video
            info = info["entries"][0]
        return info

    @staticmethod
    def _saved_audio(target_dir: Path) -> Path:
        candidates = [
            path
            for path in target_dir.glob(f"{config.DICTATION_AUDIO_STEM}.*")
            if path.suffix != _SUBTITLE_SUFFIX and path.is_file()
        ]
        if not candidates:
            raise DownloadFailedError("Аудио не скачалось — попробуйте ещё раз.")
        return max(candidates, key=lambda path: path.stat().st_size)

    @staticmethod
    def _saved_subtitles(target_dir: Path) -> str:
        files = sorted(target_dir.glob(f"{config.DICTATION_AUDIO_STEM}*{_SUBTITLE_SUFFIX}"))
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            finally:
                # The cleaned-up copy is kept by the store as subtitles.vtt.
                path.unlink(missing_ok=True)
            if text.strip():
                return text
        raise NoSubtitlesError(
            "Субтитры не скачались — возможно, YouTube их не отдал. Попробуйте другое видео."
        )


def create_fetcher(max_seconds: Optional[int] = None) -> YouTubeFetcher:
    """The factory app.api depends on (overridable in tests)."""
    return YouTubeFetcher(
        max_seconds=config.DICTATION_MAX_SECONDS if max_seconds is None else max_seconds
    )
