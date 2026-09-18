"""Entry point.

Run with either:
    python -m app.main
    python app/main.py

This starts a local web server (FastAPI/uvicorn) and opens it in your
default browser at http://127.0.0.1:<port>. Recording happens in the
browser (MediaRecorder); there is no desktop GUI any more.

Pass --selftest to check a build (imports, mocked Deepgram + Claude request
paths, FastAPI app construction) without starting a server or opening a
browser. The result is written to logs/app.log.
"""

from __future__ import annotations

import sys
import threading
import time
import webbrowser
from pathlib import Path

# Allow "python app/main.py" to import the `app` package by putting the
# project root on sys.path.
if __package__ in (None, ""):  # pragma: no cover - script-mode bootstrap
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging  # noqa: E402
import os  # noqa: E402

from app import __version__, config  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8420


def self_test() -> int:
    """Smoke-check this build without a network call, a browser or a port.

    Exercises the Deepgram and Claude request paths against fake clients, and
    makes sure the FastAPI app builds - useful for catching a packaging or
    import problem before it shows up interactively.
    """
    import tempfile
    import wave

    logger = logging.getLogger("app.selftest")
    ok = True

    try:
        import httpx
        from deepgram import DeepgramClient

        from app.transcriber import DeepgramTranscriber

        canned = {
            "metadata": {"request_id": "selftest"},
            "results": {
                "channels": [{"alternatives": [{"transcript": "ok", "confidence": 1.0}]}]
            },
        }

        def handler(request: httpx.Request) -> httpx.Response:
            logger.info("selftest: request %s %s", request.method, request.url)
            return httpx.Response(200, json=canned)

        def factory(api_key: str) -> object:
            return DeepgramClient(
                api_key=api_key,
                httpx_client=httpx.Client(transport=httpx.MockTransport(handler)),
            )

        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "selftest.wav"
            with wave.open(str(audio_path), "wb") as handle:
                handle.setnchannels(config.CHANNELS)
                handle.setsampwidth(config.SAMPLE_WIDTH_BYTES)
                handle.setframerate(config.SAMPLE_RATE)
                handle.writeframes(b"\x00\x00" * config.SAMPLE_RATE)
            result = DeepgramTranscriber(
                "selftest-key", client_factory=factory
            ).transcribe(audio_path)
        assert result.transcript == "ok"
        logger.info("selftest: Deepgram SDK request path OK")
    except Exception:
        logger.exception("selftest: Deepgram SDK FAILED")
        ok = False

    try:
        from app.analyzer import ClaudeAnalyzer, SpeechAnalysis

        class _FakeResponse:
            stop_reason = "end_turn"
            _request_id = "selftest"
            parsed_output = SpeechAnalysis(summary="ok", issues=[])

        class _FakeMessages:
            def parse(self, **kwargs: object) -> _FakeResponse:
                logger.info("selftest: analyzer request built (model=%s)", kwargs.get("model"))
                return _FakeResponse()

        class _FakeClient:
            messages = _FakeMessages()

        result = ClaudeAnalyzer(
            "selftest-key", client_factory=lambda key: _FakeClient()
        ).analyze("test transcript", config.default_profile(), 1.0)
        assert result.summary == "ok"
        logger.info("selftest: Claude analyzer request path OK")
    except Exception:
        logger.exception("selftest: Claude analyzer FAILED")
        ok = False

    try:
        from app.server import create_app

        create_app()
        logger.info("selftest: FastAPI app builds OK")
    except Exception:
        logger.exception("selftest: FastAPI app FAILED")
        ok = False

    logger.info("selftest: %s", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


def _open_browser_soon(url: str, logger: logging.Logger) -> None:
    time.sleep(1.0)
    try:
        webbrowser.open(url)
    except Exception:
        logger.warning("Could not open a browser automatically. Open %s yourself.", url)


def main() -> int:
    config.load_environment()
    log_path = config.setup_logging()
    logger = logging.getLogger("app.main")
    logger.info(
        "%s v%s starting (base dir: %s, log: %s)",
        config.APP_NAME,
        __version__,
        config.base_dir(),
        log_path,
    )
    # Never log the keys themselves - only whether one is present.
    logger.info("DEEPGRAM_API_KEY configured: %s", config.get_api_key() is not None)
    logger.info("ANTHROPIC_API_KEY configured: %s", config.get_anthropic_api_key() is not None)

    if "--selftest" in sys.argv[1:]:
        return self_test()

    for directory in (config.recordings_dir(), config.logs_dir(), config.data_dir()):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception("Could not create %s", directory)

    host = os.environ.get("APP_HOST", DEFAULT_HOST)
    port = int(os.environ.get("APP_PORT", str(DEFAULT_PORT)))
    url = f"http://{host}:{port}"

    try:
        import uvicorn

        from app.server import app as fastapi_app
    except Exception as exc:  # pragma: no cover - import-time failure
        logger.exception("Could not start the web server")
        print(f"Fatal error: {exc}\nSee {log_path}", file=sys.stderr)
        return 1

    threading.Thread(target=_open_browser_soon, args=(url, logger), daemon=True).start()

    logger.info("Serving at %s (press Ctrl+C to stop)", url)
    try:
        uvicorn.run(fastapi_app, host=host, port=port, log_level="info")
    except Exception:
        logger.exception("Fatal error")
        return 1

    logger.info("Application stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
