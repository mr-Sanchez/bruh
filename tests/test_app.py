"""Offline tests: no microphone, no network, no Deepgram account needed.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, utils  # noqa: E402
from app.transcriber import (  # noqa: E402
    DeepgramTranscriber,
    MissingApiKeyError,
    TranscriptionError,
)

SAMPLE_RESPONSE: Dict[str, Any] = {
    "metadata": {
        "request_id": "11111111-2222-3333-4444-555555555555",
        "model_info": {"name": "nova-3"},
        "duration": 12.4,
    },
    "results": {
        "channels": [
            {
                "alternatives": [
                    {
                        "transcript": (
                            "Yesterday I go... um... I went to shop and I "
                            "don't know... uh... how I can explain this."
                        ),
                        "confidence": 0.93,
                        "words": [],
                    }
                ]
            }
        ]
    },
}


class FakeRawClient:
    def __init__(self, response: Any, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.captured: Dict[str, Any] = {}

    def transcribe_file(self, **kwargs: Any) -> Any:
        self.captured = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class FakeHttpResponse:
    """Mimics the SDK's HttpResponse wrapper (headers + parsed data)."""

    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload
        self.headers = {"dg-request-id": "header-request-id"}

    @property
    def data(self) -> Any:
        class _Model:
            def __init__(self, payload: Dict[str, Any]) -> None:
                self._payload = payload

            def json(self) -> str:
                return json.dumps(self._payload)

        return _Model(self._payload)


class FakeClient:
    def __init__(self, raw_client: FakeRawClient) -> None:
        self.listen = self  # type: ignore[assignment]
        self.v1 = self  # type: ignore[assignment]
        self.media = self  # type: ignore[assignment]
        self.with_raw_response = raw_client


def make_transcriber(response: Any = None, error: Exception | None = None):
    raw = FakeRawClient(response if response is not None else FakeHttpResponse(SAMPLE_RESPONSE), error)
    transcriber = DeepgramTranscriber(
        "fake-key", client_factory=lambda key: FakeClient(raw)
    )
    return transcriber, raw


def write_wav(path: Path, seconds: float = 1.0, rate: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


class TranscriberTests(unittest.TestCase):
    def test_missing_api_key_raises_the_documented_message(self) -> None:
        transcriber = DeepgramTranscriber(None, client_factory=lambda key: None)
        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "audio.wav")
            with self.assertRaises(MissingApiKeyError) as ctx:
                transcriber.transcribe(wav)
        self.assertEqual(str(ctx.exception), config.MISSING_API_KEY_MESSAGE)

    def test_faithful_options_are_sent_for_english(self) -> None:
        transcriber, raw = make_transcriber()
        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "audio.wav")
            transcriber.transcribe(wav)
        self.assertEqual(raw.captured["model"], "nova-3")
        self.assertEqual(raw.captured["language"], "en-US")
        self.assertTrue(raw.captured["filler_words"])
        self.assertTrue(raw.captured["punctuate"])
        self.assertFalse(raw.captured["smart_format"])
        for forbidden in ("diarize", "summarize", "sentiment", "intents", "topics"):
            self.assertNotIn(forbidden, raw.captured)

    def test_russian_profile_drops_the_english_only_filler_words(self) -> None:
        raw = FakeRawClient(FakeHttpResponse(SAMPLE_RESPONSE))
        transcriber = DeepgramTranscriber(
            "fake-key",
            profile=config.profile_by_key("ru"),
            client_factory=lambda key: FakeClient(raw),
        )
        with tempfile.TemporaryDirectory() as tmp:
            transcriber.transcribe(write_wav(Path(tmp) / "audio.wav"))
        self.assertEqual(raw.captured["model"], "nova-3")
        self.assertEqual(raw.captured["language"], "ru")
        self.assertFalse(raw.captured["smart_format"])
        self.assertTrue(raw.captured["punctuate"])
        # Deepgram documents filler_words as English-only.
        self.assertNotIn("filler_words", raw.captured)

    def test_multilingual_profile_uses_language_multi(self) -> None:
        raw = FakeRawClient(FakeHttpResponse(SAMPLE_RESPONSE))
        transcriber = DeepgramTranscriber(
            "fake-key",
            profile=config.profile_by_key("multi"),
            client_factory=lambda key: FakeClient(raw),
        )
        with tempfile.TemporaryDirectory() as tmp:
            transcriber.transcribe(write_wav(Path(tmp) / "audio.wav"))
        self.assertEqual(raw.captured["language"], "multi")
        self.assertEqual(raw.captured["model"], "nova-3")

    def test_unknown_language_key_falls_back_to_english(self) -> None:
        self.assertEqual(config.profile_by_key("klingon").key, "en-US")

    def test_transcript_is_returned_verbatim(self) -> None:
        transcriber, _ = make_transcriber()
        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "audio.wav")
            result = transcriber.transcribe(wav)
        expected = SAMPLE_RESPONSE["results"]["channels"][0]["alternatives"][0]
        self.assertEqual(result.transcript, expected["transcript"])
        self.assertEqual(result.confidence, 0.93)
        self.assertEqual(result.request_id, "11111111-2222-3333-4444-555555555555")
        self.assertEqual(result.raw_response, SAMPLE_RESPONSE)

    def test_request_id_falls_back_to_the_response_header(self) -> None:
        payload = json.loads(json.dumps(SAMPLE_RESPONSE))
        payload["metadata"].pop("request_id")
        transcriber, _ = make_transcriber(FakeHttpResponse(payload))
        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "audio.wav")
            result = transcriber.transcribe(wav)
        self.assertEqual(result.request_id, "header-request-id")

    def test_api_error_is_mapped_to_a_readable_message(self) -> None:
        error = Exception("unauthorized")
        setattr(error, "status_code", 401)
        setattr(error, "body", {"err_msg": "Invalid credentials"})
        transcriber, _ = make_transcriber(error=error)
        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "audio.wav")
            with self.assertRaises(TranscriptionError) as ctx:
                transcriber.transcribe(wav)
        message = str(ctx.exception)
        self.assertIn("401", message)
        self.assertIn("DEEPGRAM_API_KEY", message)

    def test_network_error_is_mapped(self) -> None:
        import httpx

        transcriber, _ = make_transcriber(error=httpx.ConnectError("no route"))
        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "audio.wav")
            with self.assertRaises(TranscriptionError) as ctx:
                transcriber.transcribe(wav)
        self.assertIn("internet", str(ctx.exception).lower())

    def test_audio_file_survives_a_failed_transcription(self) -> None:
        transcriber, _ = make_transcriber(error=RuntimeError("boom"))
        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "audio.wav")
            with self.assertRaises(TranscriptionError):
                transcriber.transcribe(wav)
            self.assertTrue(wav.exists())
            self.assertGreater(wav.stat().st_size, 44)


class FileLayoutTests(unittest.TestCase):
    def test_session_directory_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            when = dt.datetime(2026, 9, 2, 18, 35, 20)
            session = utils.create_session(Path(tmp), when)
            self.assertEqual(session.directory.name, "2026-09-02_18-35-20")
            self.assertTrue(session.directory.is_dir())

            # A second session in the same second must not collide.
            second = utils.create_session(Path(tmp), when)
            self.assertNotEqual(session.directory, second.directory)

            session.duration_seconds = 763.0
            transcript = "Yesterday I go... um... I went to shop — тест."
            path = utils.write_transcript(session, transcript)

            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("Recording: 2026-09-02 18:35:20\n"))
            self.assertIn("Duration: 12:43\n", text)
            self.assertIn("Model: Deepgram Nova-3\n", text)
            self.assertIn("Language: en-US\n", text)
            self.assertIn("\n---\n\n", text)
            self.assertEqual(utils.read_transcript_body(path), transcript)

            utils.write_json(session.response_path, SAMPLE_RESPONSE)
            self.assertEqual(
                json.loads(session.response_path.read_text(encoding="utf-8")),
                SAMPLE_RESPONSE,
            )

    def test_russian_transcript_header_and_utf8_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = utils.create_session(
                Path(tmp), dt.datetime(2026, 9, 2, 18, 35, 20)
            )
            session.duration_seconds = 95.0
            spoken = (
                "Я думаю... э-э... "
                "ну, это сложно "
                "объяснить."
            )
            path = utils.write_transcript(
                session, spoken, config.profile_by_key("ru")
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn("Language: ru\n", text)
            self.assertIn("Model: Deepgram Nova-3\n", text)
            self.assertEqual(utils.read_transcript_body(path), spoken)
            # Written as UTF-8, not escaped or transliterated.
            self.assertIn(spoken.encode("utf-8"), path.read_bytes())

    def test_wav_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "audio.wav", seconds=2.0)
            self.assertAlmostEqual(utils.wav_duration_seconds(wav), 2.0, places=3)
            self.assertEqual(utils.wav_duration_seconds(Path(tmp) / "nope.wav"), 0.0)

    def test_format_duration(self) -> None:
        self.assertEqual(utils.format_duration(0), "00:00")
        self.assertEqual(utils.format_duration(763), "12:43")
        self.assertEqual(utils.format_duration(3723), "01:02:03")

    def test_session_meta_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = utils.create_session(
                Path(tmp), dt.datetime(2026, 9, 2, 18, 35, 20)
            )
            session.duration_seconds = 12.5
            session.audio_filename = "audio.webm"
            session.language_key = "ru"
            session.status = utils.STATUS_DONE
            utils.write_session_meta(session)

            loaded = utils.read_session_meta(session.directory)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.audio_filename, "audio.webm")
            self.assertEqual(loaded.language_key, "ru")
            self.assertEqual(loaded.status, utils.STATUS_DONE)
            self.assertAlmostEqual(loaded.duration_seconds, 12.5)
            self.assertEqual(loaded.started_at, session.started_at)

    def test_list_sessions_skips_directories_without_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = utils.create_session(root, dt.datetime(2026, 9, 2, 18, 35, 20))
            utils.write_session_meta(session)
            (root / "not-a-session").mkdir()

            sessions = utils.list_sessions(root)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].directory, session.directory)


class SdkContractTests(unittest.TestCase):
    """Exercises the REAL Deepgram SDK, with httpx MockTransport instead of
    the network, so the request it builds is verified without an account."""

    CANNED = {
        "metadata": {
            "request_id": "real-sdk-req-id",
            "duration": 2.9,
            "channels": 1,
            "model_info": {"m": {"name": "nova-3", "version": "1", "arch": "nova-3"}},
        },
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "transcript": "Yesterday I go... um... I went to shop.",
                            "confidence": 0.987,
                            "words": [],
                        }
                    ]
                }
            ]
        },
    }

    def test_request_is_built_as_documented(self) -> None:
        import httpx
        from deepgram import DeepgramClient

        captured: Dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["path"] = request.url.path
            captured["params"] = dict(request.url.params)
            captured["authorized"] = any(
                key.lower() == "authorization" for key in request.headers
            )
            captured["body_len"] = len(request.read())
            return httpx.Response(
                200, json=self.CANNED, headers={"dg-request-id": "hdr-id"}
            )

        def factory(api_key: str) -> Any:
            return DeepgramClient(
                api_key=api_key,
                httpx_client=httpx.Client(transport=httpx.MockTransport(handler)),
            )

        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "audio.wav", seconds=1.0)
            transcriber = DeepgramTranscriber("fake-key", client_factory=factory)
            result = transcriber.transcribe(wav)

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/v1/listen")
        self.assertEqual(
            captured["params"],
            {
                "model": "nova-3",
                "language": "en-US",
                "filler_words": "true",
                "punctuate": "true",
                "smart_format": "false",
            },
        )
        self.assertTrue(captured["authorized"])
        self.assertGreater(captured["body_len"], 32000)

        # The stored JSON is the untouched body Deepgram sent.
        self.assertEqual(result.raw_response, self.CANNED)
        self.assertEqual(
            result.transcript, "Yesterday I go... um... I went to shop."
        )
        self.assertEqual(result.request_id, "real-sdk-req-id")

    def test_russian_request_is_built_as_documented(self) -> None:
        import httpx
        from deepgram import DeepgramClient

        seen: Dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return httpx.Response(200, json=self.CANNED)

        def factory(api_key: str) -> Any:
            return DeepgramClient(
                api_key=api_key,
                httpx_client=httpx.Client(transport=httpx.MockTransport(handler)),
            )

        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "audio.wav", seconds=0.5)
            DeepgramTranscriber(
                "fake-key",
                profile=config.profile_by_key("ru"),
                client_factory=factory,
            ).transcribe(wav)

        self.assertEqual(
            seen,
            {
                "model": "nova-3",
                "language": "ru",
                "punctuate": "true",
                "smart_format": "false",
            },
        )

    def test_mip_opt_out_is_only_sent_when_asked(self) -> None:
        import httpx
        from deepgram import DeepgramClient

        seen: Dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return httpx.Response(200, json=self.CANNED)

        def factory(api_key: str) -> Any:
            return DeepgramClient(
                api_key=api_key,
                httpx_client=httpx.Client(transport=httpx.MockTransport(handler)),
            )

        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "audio.wav", seconds=0.5)
            DeepgramTranscriber(
                "fake-key", mip_opt_out=True, client_factory=factory
            ).transcribe(wav)

        self.assertEqual(seen.get("mip_opt_out"), "true")


if __name__ == "__main__":
    unittest.main()
