"""Tkinter GUI: record, save, transcribe, review.

The GUI owns no long-running work. Recording happens on PortAudio's own
thread, transcription on a worker thread; both report back through a queue
that the Tk main loop polls, so the window never freezes.
"""

from __future__ import annotations

import datetime as dt
import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, List, Optional, Tuple

from app import config, utils
from app.recorder import (
    AudioRecorder,
    InputDevice,
    RecorderError,
    default_input_device_index,
    list_input_devices,
)
from app.transcriber import (
    DeepgramTranscriber,
    TranscriptionError,
    TranscriptionResult,
)
from app.utils import Session

logger = logging.getLogger(__name__)

STATE_READY = "Ready"
STATE_RECORDING = "Recording..."
STATE_TRANSCRIBING = "Transcribing..."
STATE_DONE = "Done"
STATE_ERROR = "Error"

STATE_COLORS = {
    STATE_READY: "#1a7f37",
    STATE_RECORDING: "#c62828",
    STATE_TRANSCRIBING: "#0b62c4",
    STATE_DONE: "#1a7f37",
    STATE_ERROR: "#c62828",
}

TICK_MS = 100


class App:
    """Main application window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.recorder = AudioRecorder()
        self.devices: List[InputDevice] = []
        self.session: Optional[Session] = None
        self.state = STATE_READY
        self._events: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None

        self.root.title(config.APP_NAME)
        self.root.geometry("760x620")
        self.root.minsize(680, 560)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_widgets()
        self._select_startup_language()
        self.refresh_devices()
        self._resume_unfinished_session()
        self._set_state(self.state)
        self._check_api_key()
        self._tick()

    # ------------------------------------------------------------------ UI
    def _build_widgets(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        header = ttk.Label(
            outer, text=config.APP_NAME, font=("Segoe UI", 15, "bold")
        )
        header.grid(row=0, column=0, sticky="w")
        ttk.Label(
            outer,
            text="Faithful transcription for speaking practice - fillers, "
            "hesitations and mistakes are kept on purpose, never corrected.",
            foreground="#555555",
            wraplength=700,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(0, 12))

        # --- input: microphone + language ---------------------------------
        input_frame = ttk.LabelFrame(outer, text="Input", padding=10)
        input_frame.grid(row=2, column=0, sticky="ew")
        input_frame.columnconfigure(1, weight=1)

        ttk.Label(input_frame, text="Microphone:").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            input_frame, textvariable=self.device_var, state="readonly"
        )
        self.device_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.refresh_button = ttk.Button(
            input_frame, text="Refresh", width=10, command=self.refresh_devices
        )
        self.refresh_button.grid(row=0, column=2)

        ttk.Label(input_frame, text="Language:").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0)
        )
        self.language_var = tk.StringVar()
        self.language_combo = ttk.Combobox(
            input_frame,
            textvariable=self.language_var,
            state="readonly",
            values=[profile.label for profile in config.LANGUAGE_PROFILES],
        )
        self.language_combo.grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=(8, 0)
        )
        self.language_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._on_language_changed()
        )

        self.language_note = ttk.Label(
            input_frame,
            text="",
            foreground="#555555",
            wraplength=640,
            justify="left",
        )
        self.language_note.grid(
            row=2, column=1, columnspan=2, sticky="w", pady=(6, 0)
        )

        # --- status -------------------------------------------------------
        status_frame = ttk.Frame(outer, padding=(0, 14, 0, 6))
        status_frame.grid(row=3, column=0, sticky="ew")
        status_frame.columnconfigure(1, weight=1)

        self.status_label = ttk.Label(
            status_frame, text=STATE_READY, font=("Segoe UI", 12, "bold")
        )
        self.status_label.grid(row=0, column=0, sticky="w")

        self.mic_label = ttk.Label(
            status_frame, text="", font=("Segoe UI", 10, "bold"), foreground="#c62828"
        )
        self.mic_label.grid(row=0, column=1, sticky="w", padx=12)

        self.timer_label = ttk.Label(
            status_frame, text="00:00", font=("Consolas", 22, "bold")
        )
        self.timer_label.grid(row=0, column=2, sticky="e")

        self.level = ttk.Progressbar(
            outer, orient="horizontal", mode="determinate", maximum=100
        )
        self.level.grid(row=4, column=0, sticky="ew", pady=(0, 12))

        # --- main buttons -------------------------------------------------
        buttons = ttk.Frame(outer)
        buttons.grid(row=5, column=0, sticky="ew")
        for column in range(3):
            buttons.columnconfigure(column, weight=1)

        self.start_button = ttk.Button(
            buttons, text="Start Recording", command=self.start_recording
        )
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 6), ipady=6)

        self.stop_button = ttk.Button(
            buttons, text="Stop Recording", command=self.stop_recording, state="disabled"
        )
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=6, ipady=6)

        self.retry_button = ttk.Button(
            buttons,
            text="Retry Transcription",
            command=self.retry_transcription,
            state="disabled",
        )
        self.retry_button.grid(row=0, column=2, sticky="ew", padx=(6, 0), ipady=6)

        # --- secondary buttons -------------------------------------------
        actions = ttk.Frame(outer, padding=(0, 10, 0, 0))
        actions.grid(row=6, column=0, sticky="ew")
        for column in range(4):
            actions.columnconfigure(column, weight=1)

        self.copy_button = ttk.Button(
            actions, text="Copy Transcript", command=self.copy_transcript, state="disabled"
        )
        self.copy_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.open_transcript_button = ttk.Button(
            actions, text="Open Transcript", command=self.open_transcript, state="disabled"
        )
        self.open_transcript_button.grid(row=0, column=1, sticky="ew", padx=4)

        self.play_button = ttk.Button(
            actions, text="Play Recording", command=self.play_recording, state="disabled"
        )
        self.play_button.grid(row=0, column=2, sticky="ew", padx=4)

        self.folder_button = ttk.Button(
            actions, text="Open recordings folder", command=self.open_recordings_folder
        )
        self.folder_button.grid(row=0, column=3, sticky="ew", padx=(4, 0))

        # --- message + preview -------------------------------------------
        self.message_label = ttk.Label(
            outer, text="", wraplength=700, justify="left", foreground="#333333"
        )
        self.message_label.grid(row=7, column=0, sticky="ew", pady=(12, 6))

        preview_frame = ttk.LabelFrame(outer, text="Transcript", padding=6)
        preview_frame.grid(row=8, column=0, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        outer.rowconfigure(8, weight=1)

        self.preview = tk.Text(
            preview_frame, wrap="word", height=8, font=("Segoe UI", 10)
        )
        self.preview.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            preview_frame, orient="vertical", command=self.preview.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.preview.configure(yscrollcommand=scrollbar.set, state="disabled")

    # -------------------------------------------------------------- helpers
    def _set_state(self, state: str, message: str = "") -> None:
        self.state = state
        self.status_label.configure(text=state, foreground=STATE_COLORS.get(state, "#000000"))
        self.message_label.configure(
            foreground="#c62828" if state == STATE_ERROR else "#333333"
        )
        if message:
            self.message_label.configure(text=message)

        recording = state == STATE_RECORDING
        busy = state == STATE_TRANSCRIBING
        self.start_button.configure(state="disabled" if recording or busy else "normal")
        self.stop_button.configure(state="normal" if recording else "disabled")
        self.device_combo.configure(state="disabled" if recording or busy else "readonly")
        self.refresh_button.configure(state="disabled" if recording or busy else "normal")
        # The language matters only when the audio is sent, so it stays
        # changeable during a recording - and can be corrected before a retry.
        self.language_combo.configure(state="disabled" if busy else "readonly")

        has_transcript = bool(self.session and self.session.transcript)
        self.copy_button.configure(state="normal" if has_transcript and not busy else "disabled")
        self.open_transcript_button.configure(
            state="normal"
            if self.session and self.session.transcript_path.exists() and not busy
            else "disabled"
        )
        self.play_button.configure(
            state="normal"
            if self.session and self.session.wav_path.exists() and not recording
            else "disabled"
        )

        # Retry needs nothing but an existing WAV and an idle app: after a
        # failure, after a restart, or simply to transcribe the take again.
        can_retry = (
            not recording
            and not busy
            and self.session is not None
            and self.session.wav_path.exists()
        )
        self.retry_button.configure(state="normal" if can_retry else "disabled")

        if not recording:
            self.mic_label.configure(text="")
            self.level.configure(value=0)

    def _set_preview(self, text: str) -> None:
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    def _check_api_key(self) -> None:
        if config.get_api_key() is None:
            warning = (
                f"{config.MISSING_API_KEY_MESSAGE} Recording still works and your "
                "WAV is always saved; set the key in .env and press "
                "'Retry Transcription'."
            )
            existing = self.message_label.cget("text")
            self.message_label.configure(
                text=f"{warning}\n{existing}" if existing else warning,
                foreground="#c62828",
            )
            logger.warning("%s", config.MISSING_API_KEY_MESSAGE)
        else:
            logger.info("Deepgram API key found in the environment")

    def _selected_profile(self) -> config.LanguageProfile:
        index = self.language_combo.current()
        if 0 <= index < len(config.LANGUAGE_PROFILES):
            return config.LANGUAGE_PROFILES[index]
        return config.default_profile()

    def _on_language_changed(self) -> None:
        profile = self._selected_profile()
        self.language_note.configure(text=profile.note)
        logger.info(
            "Language set to %s (model=%s, language=%s, filler_words=%s)",
            profile.key,
            profile.model,
            profile.language,
            profile.filler_words,
        )

    def _selected_device_index(self) -> Optional[int]:
        selection = self.device_combo.current()
        if selection < 0 or selection >= len(self.devices):
            return None
        return self.devices[selection].index

    def _select_startup_language(self) -> None:
        startup = config.default_profile()
        index = next(
            (
                i
                for i, profile in enumerate(config.LANGUAGE_PROFILES)
                if profile.key == startup.key
            ),
            0,
        )
        self.language_combo.current(index)
        self.language_note.configure(text=config.LANGUAGE_PROFILES[index].note)

    def _resume_unfinished_session(self) -> None:
        """Pick up a recording from a previous run that has no transcript yet.

        A crash, a dead connection or a closed window must never mean a lost
        recording: the WAV is still there, so make 'Retry Transcription' work
        on it straight away.
        """
        root = config.recordings_dir()
        if not root.is_dir():
            return
        try:
            candidates = sorted(
                (path for path in root.iterdir() if path.is_dir()),
                reverse=True,
            )
        except OSError:
            return

        for directory in candidates[:20]:
            wav = directory / config.WAV_FILENAME
            transcript = directory / config.TRANSCRIPT_FILENAME
            if not wav.is_file() or transcript.exists():
                continue
            duration = utils.wav_duration_seconds(wav)
            if duration < config.MIN_RECORDING_SECONDS:
                continue
            try:
                started_at = dt.datetime.strptime(
                    directory.name[:19], utils.SESSION_DIR_FORMAT
                )
            except ValueError:
                started_at = dt.datetime.fromtimestamp(wav.stat().st_mtime)
            self.session = Session(
                directory=directory,
                started_at=started_at,
                duration_seconds=duration,
            )
            self.timer_label.configure(text=utils.format_duration(duration))
            self.message_label.configure(
                text=(
                    f"Found an earlier recording without a transcript "
                    f"({utils.format_duration(duration)}, {directory.name}). "
                    "Press 'Retry Transcription' to transcribe it."
                )
            )
            logger.info("Resumed untranscribed recording: %s", wav)
            return

    # ------------------------------------------------------------- devices
    def refresh_devices(self) -> None:
        previous = self._selected_device_index()
        try:
            self.devices = list_input_devices()
        except RecorderError as exc:
            self.devices = []
            self._set_state(STATE_ERROR, str(exc))
            return

        labels = [device.label for device in self.devices]
        self.device_combo.configure(values=labels)

        if not self.devices:
            self.device_var.set("")
            self._set_state(STATE_ERROR, "No microphone was found. Connect one and press Refresh.")
            return

        target = previous if previous is not None else default_input_device_index()
        index = next(
            (i for i, device in enumerate(self.devices) if device.index == target), 0
        )
        self.device_combo.current(index)
        logger.info("Found %d input device(s); selected: %s", len(self.devices), labels[index])
        if self.state == STATE_ERROR and self.session is None:
            self._set_state(STATE_READY)

    # ----------------------------------------------------------- recording
    def start_recording(self) -> None:
        if self.recorder.is_recording:
            return
        if not self.devices:
            self._set_state(STATE_ERROR, "No microphone selected. Press Refresh and pick one.")
            return

        try:
            session = utils.create_session()
        except OSError as exc:
            logger.exception("Could not create the session directory")
            self._set_state(STATE_ERROR, f"Could not create the recordings folder: {exc}")
            return

        self.recorder = AudioRecorder(self._selected_device_index())
        try:
            self.recorder.start(session.wav_path)
        except RecorderError as exc:
            logger.error("Recording could not start: %s", exc)
            self._set_state(STATE_ERROR, str(exc))
            return

        self.session = session
        self._set_preview("")
        note = ""
        if (
            self.recorder.sample_rate != config.SAMPLE_RATE
            or self.recorder.channels != config.CHANNELS
        ):
            note = (
                f"  (device format: {self.recorder.sample_rate} Hz, "
                f"{self.recorder.channels} ch)"
            )
        self._set_state(
            STATE_RECORDING,
            f"Recording to: {session.wav_path}{note}",
        )

    def stop_recording(self, *, reason: str = "") -> None:
        if not self.recorder.is_recording:
            return
        try:
            result = self.recorder.stop()
        except RecorderError as exc:
            logger.error("Problem while stopping the recording: %s", exc)
            session = self.session
            duration = utils.wav_duration_seconds(session.wav_path) if session else 0.0
            if session is not None:
                session.duration_seconds = duration
            self._set_state(
                STATE_ERROR,
                f"{exc} The audio recorded so far is kept at "
                f"{session.wav_path if session else 'the session folder'}.",
            )
            return

        session = self.session
        if session is None:  # pragma: no cover - defensive
            self._set_state(STATE_ERROR, "Internal error: no active session.")
            return
        session.duration_seconds = result.duration_seconds
        self.timer_label.configure(text=utils.format_duration(result.duration_seconds))

        if result.duration_seconds < config.MIN_RECORDING_SECONDS:
            self._set_state(
                STATE_ERROR,
                "The recording is empty (no audio was captured). Check that the "
                f"right microphone is selected. File kept at: {session.wav_path}",
            )
            return

        prefix = f"{reason} " if reason else ""
        self._start_transcription(
            f"{prefix}Saved {utils.format_duration(result.duration_seconds)} to "
            f"{session.wav_path}"
        )

    # -------------------------------------------------------- transcription
    def retry_transcription(self) -> None:
        if self.session is None or not self.session.wav_path.exists():
            self._set_state(STATE_ERROR, "There is no recording to transcribe.")
            return
        if self.session.duration_seconds <= 0:
            self.session.duration_seconds = utils.wav_duration_seconds(
                self.session.wav_path
            )
        if self.session.duration_seconds < config.MIN_RECORDING_SECONDS:
            self._set_state(
                STATE_ERROR,
                "That recording is empty, there is nothing to transcribe.",
            )
            return
        self._start_transcription(f"Retrying transcription of {self.session.wav_path}")

    def _start_transcription(self, message: str) -> None:
        session = self.session
        if session is None:
            return
        if self._worker is not None and self._worker.is_alive():
            return

        api_key = config.get_api_key()
        if api_key is None:
            self._set_state(
                STATE_ERROR,
                f"{config.MISSING_API_KEY_MESSAGE} Your recording is safe at "
                f"{session.wav_path}. Add the key to .env, restart the app and "
                "press 'Retry Transcription'.",
            )
            return

        profile = self._selected_profile()
        self._set_state(
            STATE_TRANSCRIBING, f"{message}\nLanguage: {profile.label}"
        )
        transcriber = DeepgramTranscriber(
            api_key, profile=profile, mip_opt_out=config.mip_opt_out()
        )
        self._worker = threading.Thread(
            target=self._transcription_worker,
            args=(transcriber, session),
            name="deepgram-worker",
            daemon=True,
        )
        self._worker.start()

    def _transcription_worker(
        self, transcriber: DeepgramTranscriber, session: Session
    ) -> None:
        """Runs off the Tk thread; results come back through the queue."""
        try:
            result = transcriber.transcribe(session.wav_path)
            utils.write_json(session.response_path, result.raw_response)
            utils.write_transcript(session, result.transcript, transcriber.profile)
            self._events.put(("done", (session, result)))
        except TranscriptionError as exc:  # includes MissingApiKeyError
            logger.error("Transcription failed: %s", exc)
            self._events.put(("failed", str(exc)))
        except OSError as exc:
            logger.exception("Could not write the transcript files")
            self._events.put(("failed", f"Could not save the transcript: {exc}"))
        except Exception as exc:  # pragma: no cover - unexpected
            logger.exception("Unexpected error during transcription")
            self._events.put(("failed", f"Unexpected error: {exc}"))

    def _on_transcription_done(
        self, session: Session, result: TranscriptionResult
    ) -> None:
        session.transcript = result.transcript
        self.session = session
        self._set_preview(result.transcript or "(Deepgram returned an empty transcript.)")
        details = (
            f"Files saved in {session.directory}"
            f"   |   language: {self._selected_profile().language}"
        )
        if result.request_id:
            details += f"\nDeepgram request ID: {result.request_id}"
        if result.confidence is not None:
            details += f"   |   confidence: {result.confidence:.3f}"
        self._set_state(STATE_DONE, details)

    # ------------------------------------------------------------- actions
    def copy_transcript(self) -> None:
        if self.session is None or not self.session.transcript:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.session.transcript)
        self.root.update_idletasks()
        self.message_label.configure(text="Transcript copied to the clipboard.")

    def open_transcript(self) -> None:
        self._open(self.session.transcript_path if self.session else None)

    def play_recording(self) -> None:
        self._open(self.session.wav_path if self.session else None)

    def open_recordings_folder(self) -> None:
        directory = config.recordings_dir()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.message_label.configure(text=f"Could not create {directory}: {exc}")
            return
        self._open(directory)

    def _open(self, path: Optional[Path]) -> None:
        if path is None or not path.exists():
            self.message_label.configure(text="That file does not exist (yet).")
            return
        try:
            utils.open_path(path)
        except OSError as exc:
            logger.exception("Could not open %s", path)
            self.message_label.configure(text=f"Could not open {path}: {exc}")

    # ---------------------------------------------------------------- loop
    def _tick(self) -> None:
        """Single 100 ms heartbeat: timer, level meter, watchdog, results."""
        if self.recorder.is_recording:
            elapsed = self.recorder.elapsed_seconds
            self.timer_label.configure(text=utils.format_duration(elapsed))
            self.level.configure(value=min(100, self.recorder.consume_peak() * 140))
            self.mic_label.configure(
                text="MIC LIVE" if int(elapsed * 2) % 2 == 0 else "MIC LIVE  *"
            )
            if self.recorder.write_error is not None:
                self.stop_recording(reason="Recording stopped after a disk error.")
            elif self.recorder.seconds_since_audio > config.MIC_STALL_TIMEOUT_SECONDS:
                logger.error("Microphone stopped delivering audio; stopping recording")
                self.stop_recording(
                    reason="The microphone stopped responding (unplugged?)."
                )

        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "done":
                session, result = payload
                self._on_transcription_done(session, result)
            elif kind == "failed":
                self._set_state(
                    STATE_ERROR,
                    f"{payload}\nYour recording is safe: "
                    f"{self.session.wav_path if self.session else ''}",
                )

        self.root.after(TICK_MS, self._tick)

    def _on_close(self) -> None:
        if self.recorder.is_recording:
            if not messagebox.askokcancel(
                config.APP_NAME,
                "A recording is in progress. Stop it and quit?\n"
                "The audio recorded so far will be saved.",
            ):
                return
            try:
                self.recorder.stop()
            except RecorderError:
                logger.exception("Error stopping the recording on exit")
        elif self._worker is not None and self._worker.is_alive():
            if not messagebox.askokcancel(
                config.APP_NAME,
                "A transcription is still running. Quit anyway?\n"
                "The recording is already saved and can be transcribed later "
                "with 'Retry Transcription'.",
            ):
                return
        logger.info("Application closing")
        self.root.destroy()
