"""Entry point.

Run with either:
    python -m app.main
    python app/main.py

Pass --selftest to check a build (imports, audio backend, Deepgram request
path) without opening a window. The result is written to logs/app.log.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow "python app/main.py" (and the PyInstaller entry point) to import the
# `app` package by putting the project root on sys.path.
if __package__ in (None, ""):  # pragma: no cover - script-mode bootstrap
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging  # noqa: E402
import tkinter as tk  # noqa: E402
from tkinter import messagebox  # noqa: E402

from app import __version__, config  # noqa: E402
from app.gui import App  # noqa: E402


def self_test() -> int:
    """Smoke-check this build without a GUI, a microphone or an API key.

    Useful mainly for the packaged .exe, where a missing lazily-imported
    module would otherwise only show up the first time you press Stop.
    """
    import tempfile
    import wave

    logger = logging.getLogger("app.selftest")
    ok = True

    try:
        from app.recorder import list_input_devices

        logger.info("selftest: audio backend OK, %d input device(s)", len(list_input_devices()))
    except Exception:
        logger.exception("selftest: audio backend FAILED")
        ok = False

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
            wav_path = Path(tmp) / "selftest.wav"
            with wave.open(str(wav_path), "wb") as handle:
                handle.setnchannels(config.CHANNELS)
                handle.setsampwidth(config.SAMPLE_WIDTH_BYTES)
                handle.setframerate(config.SAMPLE_RATE)
                handle.writeframes(b"\x00\x00" * config.SAMPLE_RATE)
            result = DeepgramTranscriber(
                "selftest-key", client_factory=factory
            ).transcribe(wav_path)
        assert result.transcript == "ok"
        logger.info("selftest: Deepgram SDK request path OK")
    except Exception:
        logger.exception("selftest: Deepgram SDK FAILED")
        ok = False

    logger.info("selftest: %s", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


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
    # Never log the key itself - only whether one is present.
    logger.info("DEEPGRAM_API_KEY configured: %s", config.get_api_key() is not None)

    if "--selftest" in sys.argv[1:]:
        return self_test()

    try:
        config.recordings_dir().mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("Could not create the recordings directory")

    try:
        root = tk.Tk()
        App(root)
        root.mainloop()
    except Exception as exc:  # pragma: no cover - last-resort handler
        logger.exception("Fatal error")
        try:
            messagebox.showerror(config.APP_NAME, f"Fatal error: {exc}\nSee {log_path}")
        except Exception:
            pass
        return 1

    logger.info("Application stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
