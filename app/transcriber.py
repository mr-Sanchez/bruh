"""Deepgram speech-to-text (pre-recorded files).

The whole point of this module is a FAITHFUL transcript: whatever the speaker
actually said, mistakes included. That means:

  * model=nova-3, language from the selected profile (en-US, ru, multi)
  * filler_words=True   -> uh, um, mhmm, mm-mm, uh-uh, uh-huh, nuh-uh are kept
                           (Deepgram supports this for English only, so it is
                           sent only for the English profile)
  * punctuate=True      -> sentence punctuation only
  * smart_format=False  -> no prettifying / reformatting of the words
  * no diarization, no summarization, no sentiment/intents/topics
  * no LLM post-processing of any kind, here or anywhere else in the app

The transcript string returned by Deepgram is written to disk verbatim.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app import config

logger = logging.getLogger(__name__)

ClientFactory = Callable[[str], Any]


class TranscriptionError(RuntimeError):
    """A user-facing transcription failure (message is shown in the GUI)."""


class MissingApiKeyError(TranscriptionError):
    """The DEEPGRAM_API_KEY environment variable is not set."""


@dataclass
class TranscriptionResult:
    transcript: str
    raw_response: Dict[str, Any] = field(default_factory=dict)
    request_id: Optional[str] = None
    confidence: Optional[float] = None
    audio_duration: Optional[float] = None


def _default_client_factory(api_key: str) -> Any:
    """Build a real Deepgram client (imported lazily to keep startup fast)."""
    from deepgram import DeepgramClient

    return DeepgramClient(api_key=api_key)


def _response_to_dict(http_response: Any) -> Dict[str, Any]:
    """Best-effort recovery of the exact JSON body Deepgram sent.

    Preferred: the untouched body from the underlying httpx response.
    Fallback: serialise the parsed SDK model (it keeps unknown fields too).
    """
    inner = getattr(http_response, "_response", None)
    if inner is not None:
        try:
            payload = inner.json()
            if isinstance(payload, dict):
                return payload
        except Exception:  # pragma: no cover - malformed body
            logger.debug("Could not read the raw JSON body, using the parsed model")

    data = getattr(http_response, "data", http_response)
    for method in ("json", "model_dump_json"):
        dumper = getattr(data, method, None)
        if callable(dumper):
            try:
                payload = json.loads(dumper())
                if isinstance(payload, dict):
                    return payload
            except Exception:  # pragma: no cover
                continue
    if isinstance(data, dict):
        return data
    raise TranscriptionError("Deepgram returned a response that could not be read.")


def _extract(payload: Dict[str, Any]) -> TranscriptionResult:
    """Pull the transcript out of the response without touching its text."""
    try:
        alternative = payload["results"]["channels"][0]["alternatives"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise TranscriptionError(
            "Deepgram returned no transcript for this audio."
        ) from exc

    transcript = alternative.get("transcript")
    if transcript is None:
        raise TranscriptionError("Deepgram returned no transcript for this audio.")

    metadata = payload.get("metadata") or {}
    return TranscriptionResult(
        transcript=transcript,
        raw_response=payload,
        request_id=metadata.get("request_id"),
        confidence=alternative.get("confidence"),
        audio_duration=metadata.get("duration"),
    )


class DeepgramTranscriber:
    """Thin, testable wrapper around the Deepgram pre-recorded endpoint.

    Pass `client_factory` to substitute a mock client in tests - no network
    access is needed to exercise everything except the HTTP call itself.
    """

    def __init__(
        self,
        api_key: Optional[str],
        *,
        profile: Optional[config.LanguageProfile] = None,
        timeout_seconds: int = config.TRANSCRIPTION_TIMEOUT_SECONDS,
        mip_opt_out: bool = False,
        client_factory: Optional[ClientFactory] = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._profile = profile or config.default_profile()
        self._timeout_seconds = timeout_seconds
        self._mip_opt_out = mip_opt_out
        self._client_factory = client_factory or _default_client_factory

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    @property
    def profile(self) -> config.LanguageProfile:
        return self._profile

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        """Transcribe a local audio file. Never modifies or deletes the file.

        Deepgram auto-detects the container from the bytes, so this accepts
        WAV, WebM/Opus, Ogg/Opus or anything else it recognises - not just WAV.
        """
        if not self._api_key:
            raise MissingApiKeyError(config.MISSING_API_KEY_MESSAGE)

        try:
            audio = audio_path.read_bytes()
        except OSError as exc:
            raise TranscriptionError(f"Could not read the recording: {exc}") from exc
        if not audio:
            raise TranscriptionError("The recording is empty, nothing to transcribe.")

        profile = self._profile
        logger.info(
            "Transcription started: %s (%.1f MB, model=%s, language=%s, "
            "filler_words=%s, punctuate=True, smart_format=False)",
            audio_path,
            len(audio) / 1_048_576,
            profile.model,
            profile.language,
            profile.filler_words,
        )

        options: Dict[str, Any] = {
            "model": profile.model,
            "language": profile.language,
            "punctuate": True,
            # Explicitly off: smart_format rewrites/reformats the words, which
            # is exactly what must not happen to a faithful transcript.
            "smart_format": False,
        }
        # English only - Deepgram documents filler_words as an English feature,
        # so sending it elsewhere would just be noise in the request.
        if profile.filler_words:
            options["filler_words"] = True
        if self._mip_opt_out:
            options["mip_opt_out"] = True

        try:
            client = self._client_factory(self._api_key)
            http_response = client.listen.v1.media.with_raw_response.transcribe_file(
                request=audio,
                request_options={
                    "timeout_in_seconds": self._timeout_seconds,
                    "max_retries": 1,
                },
                **options,
            )
        except Exception as exc:  # mapped to a friendly message below
            raise self._to_friendly_error(exc) from exc

        payload = _response_to_dict(http_response)
        result = _extract(payload)

        if not result.request_id:
            headers = getattr(http_response, "headers", None) or {}
            try:
                result.request_id = headers.get("dg-request-id")
            except AttributeError:  # pragma: no cover
                result.request_id = None

        logger.info(
            "Transcription succeeded: request_id=%s, confidence=%s, characters=%d",
            result.request_id,
            result.confidence,
            len(result.transcript),
        )
        if not result.transcript.strip():
            logger.warning("Deepgram returned an empty transcript for %s", audio_path)
        return result

    # --------------------------------------------------------------- errors
    def _to_friendly_error(self, exc: Exception) -> TranscriptionError:
        """Turn SDK/network exceptions into something worth showing a human."""
        if isinstance(exc, TranscriptionError):
            return exc

        try:
            import httpx
        except ImportError:  # pragma: no cover
            httpx = None  # type: ignore[assignment]

        if httpx is not None:
            if isinstance(exc, httpx.TimeoutException):
                return TranscriptionError(
                    "Deepgram timed out. The recording is saved - press "
                    "'Retry Transcription' to try again."
                )
            if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
                return TranscriptionError(
                    "Could not reach Deepgram. Check your internet connection "
                    "and press 'Retry Transcription'."
                )
            if isinstance(exc, httpx.HTTPError):
                return TranscriptionError(f"Network error talking to Deepgram: {exc}")

        status = getattr(exc, "status_code", None)
        if status is not None:
            body = _short_error_body(getattr(exc, "body", None))
            if status in (401, 403):
                return TranscriptionError(
                    "Deepgram rejected the API key (HTTP "
                    f"{status}). Check DEEPGRAM_API_KEY. {body}".strip()
                )
            if status == 429:
                return TranscriptionError(
                    "Deepgram rate limit or quota reached (HTTP 429). "
                    f"{body}".strip()
                )
            return TranscriptionError(f"Deepgram API error (HTTP {status}). {body}".strip())

        logger.exception("Unexpected transcription failure")
        return TranscriptionError(f"Transcription failed: {exc}")


def _short_error_body(body: Any, limit: int = 300) -> str:
    """A compact, safe rendering of an API error body."""
    if body is None:
        return ""
    if isinstance(body, dict):
        for key in ("err_msg", "message", "error", "reason"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value[:limit]
        try:
            return json.dumps(body)[:limit]
        except (TypeError, ValueError):  # pragma: no cover
            return ""
    return str(body)[:limit]
