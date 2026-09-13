"""Microphone capture straight to a 16-bit PCM WAV file.

Design notes:
  * Audio arrives on PortAudio's callback thread; the callback only copies
    bytes into a queue (no file I/O, no allocation-heavy work) so CPU cost
    stays negligible even on a slow laptop.
  * A separate writer thread drains the queue into the WAV file while the
    recording goes on, so audio is on disk continuously and a crash can never
    lose more than a fraction of a second.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import sounddevice as sd

from app import config

logger = logging.getLogger(__name__)

_SENTINEL = None  # tells the writer thread to finish


class RecorderError(RuntimeError):
    """Raised for any problem starting, running or finishing a recording."""


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    host_api: str
    max_input_channels: int
    default_samplerate: float

    @property
    def label(self) -> str:
        return f"[{self.host_api}] {self.name}"


@dataclass(frozen=True)
class RecordingResult:
    path: Path
    duration_seconds: float
    sample_rate: int
    channels: int
    frames: int


def list_input_devices() -> List[InputDevice]:
    """Every microphone-capable device PortAudio can see."""
    try:
        host_apis = sd.query_hostapis()
        devices = sd.query_devices()
    except Exception as exc:  # pragma: no cover - depends on the audio stack
        raise RecorderError(f"Could not query audio devices: {exc}") from exc

    result: List[InputDevice] = []
    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        host_index = int(device.get("hostapi", 0))
        host_name = "?"
        if 0 <= host_index < len(host_apis):
            host_name = str(host_apis[host_index].get("name", "?"))
        name = str(device.get("name", "")).strip() or f"Device {index}"
        result.append(
            InputDevice(
                index=index,
                name=name,
                host_api=host_name,
                max_input_channels=int(device["max_input_channels"]),
                default_samplerate=float(device.get("default_samplerate", 0.0) or 0.0),
            )
        )
    return result


def default_input_device_index() -> Optional[int]:
    """Index of the system default input device, if there is one."""
    try:
        index = sd.default.device[0]
    except Exception:  # pragma: no cover
        return None
    return int(index) if isinstance(index, int) and index >= 0 else None


def peak_level(data: bytes, stride: int = 16) -> float:
    """Rough peak amplitude (0.0-1.0) of 16-bit little-endian PCM.

    Only every `stride`-th sample is inspected - plenty for a level meter and
    cheap enough to run on the audio callback thread.
    """
    peak = 0
    step = stride * 2
    for offset in range(0, len(data) - 1, step):
        value = int.from_bytes(data[offset : offset + 2], "little", signed=True)
        if value < 0:
            value = -value
        if value > peak:
            peak = value
    return min(peak / 32768.0, 1.0)


class AudioRecorder:
    """Records the selected microphone into a WAV file until stopped."""

    def __init__(self, device_index: Optional[int] = None) -> None:
        self._device_index = device_index
        self._stream: Optional[sd.RawInputStream] = None
        self._wave: Optional[wave.Wave_write] = None
        self._queue: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._writer: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._path: Optional[Path] = None
        self._sample_rate = config.SAMPLE_RATE
        self._channels = config.CHANNELS
        self._frames = 0
        self._peak = 0.0
        self._started_at = 0.0
        self._last_audio_at = 0.0
        self._write_error: Optional[BaseException] = None
        self._recording = False

    # ---------------------------------------------------------------- state
    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def elapsed_seconds(self) -> float:
        if not self._started_at:
            return 0.0
        return time.monotonic() - self._started_at

    def consume_peak(self) -> float:
        """Peak level since the previous call (so the meter falls back)."""
        peak = self._peak
        self._peak = 0.0
        return peak

    @property
    def seconds_since_audio(self) -> float:
        """How long the microphone has been silent at the driver level."""
        if not self._last_audio_at:
            return 0.0
        return time.monotonic() - self._last_audio_at

    @property
    def write_error(self) -> Optional[BaseException]:
        return self._write_error

    # ---------------------------------------------------------------- start
    def _resolve_settings(self) -> None:
        """Pick a (sample rate, channels) pair the device actually accepts.

        Preferred: 16 kHz mono. Some Windows devices refuse a non-native rate
        or mono capture, so fall back to the device default rather than fail.
        """
        try:
            if self._device_index is not None:
                info = sd.query_devices(self._device_index)
            else:
                info = sd.query_devices(kind="input")
            device_default = float(info.get("default_samplerate", 0.0) or 0.0)
            max_channels = max(1, int(info.get("max_input_channels", 1)))
        except Exception as exc:
            raise RecorderError(f"Selected microphone is not available: {exc}") from exc

        rates = [config.SAMPLE_RATE]
        if device_default and int(device_default) not in rates:
            rates.append(int(device_default))
        channel_options = [config.CHANNELS]
        if max_channels > config.CHANNELS:
            channel_options.append(max_channels)

        last_error: Optional[Exception] = None
        for channels in channel_options:
            for rate in rates:
                try:
                    sd.check_input_settings(
                        device=self._device_index,
                        channels=channels,
                        samplerate=rate,
                        dtype="int16",
                    )
                except Exception as exc:  # PortAudioError and friends
                    last_error = exc
                    continue
                self._sample_rate = rate
                self._channels = channels
                if rate != config.SAMPLE_RATE or channels != config.CHANNELS:
                    logger.info(
                        "Falling back to %d Hz / %d channel(s) for this device",
                        rate,
                        channels,
                    )
                return

        raise RecorderError(
            f"The selected microphone does not support recording ({last_error})."
        )

    def start(self, path: Path) -> RecordingResult:
        """Open the WAV file and start capturing. Returns the chosen format."""
        with self._lock:
            if self._recording:
                raise RecorderError("A recording is already in progress.")

            self._resolve_settings()

            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                wave_file = wave.open(str(path), "wb")
                wave_file.setnchannels(self._channels)
                wave_file.setsampwidth(config.SAMPLE_WIDTH_BYTES)
                wave_file.setframerate(self._sample_rate)
            except (OSError, wave.Error) as exc:
                raise RecorderError(f"Could not create the WAV file: {exc}") from exc

            self._wave = wave_file
            self._path = path
            self._frames = 0
            self._peak = 0.0
            self._write_error = None
            self._queue = queue.Queue()

            self._writer = threading.Thread(
                target=self._writer_loop, name="wav-writer", daemon=True
            )
            self._writer.start()

            try:
                self._stream = sd.RawInputStream(
                    samplerate=self._sample_rate,
                    blocksize=config.BLOCK_SIZE,
                    device=self._device_index,
                    channels=self._channels,
                    dtype="int16",
                    callback=self._audio_callback,
                )
                self._stream.start()
            except Exception as exc:
                self._queue.put(_SENTINEL)
                if self._writer is not None:
                    self._writer.join(timeout=2.0)
                    self._writer = None
                self._close_wave()
                self._stream = None
                self._path = None
                raise RecorderError(f"Could not open the microphone: {exc}") from exc

            now = time.monotonic()
            self._started_at = now
            self._last_audio_at = now
            self._recording = True

        logger.info(
            "Recording started: %s (%d Hz, %d channel(s), device=%s)",
            path,
            self._sample_rate,
            self._channels,
            self._device_index,
        )
        return RecordingResult(
            path=path,
            duration_seconds=0.0,
            sample_rate=self._sample_rate,
            channels=self._channels,
            frames=0,
        )

    # ------------------------------------------------------------ internals
    def _audio_callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            # Overflows are non-fatal: note them and keep recording.
            logger.debug("Audio callback status: %s", status)
        data = bytes(indata)
        self._queue.put(data)
        self._last_audio_at = time.monotonic()
        level = peak_level(data)
        if level > self._peak:
            self._peak = level

    def _writer_loop(self) -> None:
        while True:
            chunk = self._queue.get()
            if chunk is _SENTINEL:
                return
            wave_file = self._wave
            if wave_file is None:
                return
            try:
                wave_file.writeframes(chunk)
                self._frames += len(chunk) // (
                    config.SAMPLE_WIDTH_BYTES * self._channels
                )
            except (OSError, ValueError, wave.Error) as exc:  # disk full, etc.
                self._write_error = exc
                logger.exception("Failed writing audio to disk")
                return

    def _close_wave(self) -> None:
        if self._wave is not None:
            try:
                self._wave.close()
            except (OSError, wave.Error):
                logger.exception("Failed closing the WAV file")
            self._wave = None

    # ----------------------------------------------------------------- stop
    def stop(self) -> RecordingResult:
        """Stop capturing and finalise the WAV file.

        The WAV is always closed properly, even when the device errored out,
        so a recording is never lost.
        """
        with self._lock:
            if not self._recording:
                raise RecorderError("No recording is in progress.")
            self._recording = False

            stream, self._stream = self._stream, None
            if stream is not None:
                for action in (stream.stop, stream.close):
                    try:
                        action()
                    except Exception:  # e.g. device unplugged mid-recording
                        logger.exception("Error while closing the audio stream")

            self._queue.put(_SENTINEL)
            if self._writer is not None:
                self._writer.join(timeout=10.0)
                self._writer = None

            self._close_wave()

            path = self._path
            frames = self._frames
            self._path = None
            self._started_at = 0.0
            self._last_audio_at = 0.0

        if path is None:  # pragma: no cover - defensive
            raise RecorderError("Recording produced no file.")

        duration = frames / self._sample_rate if self._sample_rate else 0.0
        logger.info(
            "Recording stopped: %s (%.2f s, %d frames)", path, duration, frames
        )

        if self._write_error is not None:
            raise RecorderError(
                f"Audio was not fully written to disk: {self._write_error}"
            )

        return RecordingResult(
            path=path,
            duration_seconds=duration,
            sample_rate=self._sample_rate,
            channels=self._channels,
            frames=frames,
        )
