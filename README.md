# English Speech Recorder

A tiny Windows desktop app for practising speaking, built for English and
also usable for Russian dictation.

Record 5–20 minutes of your own speech, keep the original WAV, and get back a
transcript that reflects **what you actually said** — fillers, hesitations,
false starts, self-corrections, wrong tenses and all. That transcript is the
raw material you later hand to an LLM for analysis.

The app deliberately does **not** fix your English:

* no grammar correction, no rephrasing, no style improvement
* no removal of repeats, fillers or unfinished sentences
* no LLM post-processing of any kind
* `smart_format` is off on purpose — `punctuate=true` gives sentence
  punctuation without reformatting the words

Transcription is done by **Deepgram Nova-3** in the cloud (`filler_words=true`),
so nothing heavy runs on your laptop.

---

## 1. Quick start

### 1.1 Create a Deepgram account and get an API key

1. Sign up at <https://console.deepgram.com/signup> (new accounts get free credit).
2. In the console open **API Keys → Create a New API Key**.
3. Copy the key — it is shown only once.

### 1.2 Create your `.env`

In the project folder, copy `.env.example` to `.env` and paste your key:

```
DEEPGRAM_API_KEY=your_api_key_here
```

`.env` is listed in `.gitignore` and is never committed or bundled into the
`.exe`. The key is also never written to the log file.

You can instead set a normal environment variable named `DEEPGRAM_API_KEY`;
a real environment variable takes priority over `.env`.

### 1.3 Install Python dependencies

Requires Python 3.10+ (3.12 recommended).

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 1.4 Run the app

```bat
python -m app.main
```

or just double-click `run.bat`.

### 1.5 Record

1. Pick your microphone in the dropdown (press **Refresh** if you plugged one in).
2. Press **Start Recording** — the status turns red, `MIC LIVE` blinks, the
   level bar moves and the timer counts up.
3. Speak English for as long as you like.
4. Press **Stop Recording**.

The app then saves the WAV, uploads it to Deepgram, writes the transcript and
shows you where everything is.

### 1.6 What you get

```
recordings/2026-09-02_18-35-20/audio.wav                (your original recording)
recordings/2026-09-02_18-35-20/transcript.txt           (the transcript)
recordings/2026-09-02_18-35-20/deepgram_response.json   (full raw API response)
```

`transcript.txt` looks like this:

```
Recording: 2026-09-02 18:35:20
Duration: 12:43
Model: Deepgram Nova-3
Language: en-US

---

Yesterday I go... um... I went to shop and I don't know... uh... how I can explain this.
```

Everything after `---` is exactly the string Deepgram returned, written in
UTF-8, untouched.

The raw JSON is kept so you can inspect per-word confidence scores and
timings later, or diagnose recognition mistakes.

---

## 2. Language

A dropdown next to the microphone picks what gets sent to Deepgram:

| Choice | Request | Filler words |
| --- | --- | --- |
| `English (US)` | `model=nova-3&language=en-US` | **yes** (`filler_words=true`) |
| `Русский (Russian)` | `model=nova-3&language=ru` | no |
| `Mixed speech (RU + EN)` | `model=nova-3&language=multi` | no |

**Important, and the reason English is still the default:** Deepgram documents
`filler_words` as an **English-only** feature. Nova-3 transcribes Russian well,
but `э-э`, `мм`, `ну` and similar hesitations are not specially preserved the
way `uh` / `um` are in English. Everything else is identical for all three
choices — `punctuate=true`, `smart_format=false`, no rewriting, no LLM
post-processing — so a Russian transcript is still raw, just without the
guaranteed filler markup. The app shows this note under the dropdown.

Use `Mixed speech (RU + EN)` only when you actually switch languages inside one
take: it runs Deepgram's code-switching build, which lags the single-language
one by several months.

The language is applied **when the audio is sent**, not when it is recorded.
So if you transcribe a take with the wrong language selected, just change the
dropdown and press `Retry Transcription` on the same WAV — no re-recording.

Set `DEEPGRAM_LANGUAGE=ru` in `.env` to start the app on Russian.

The chosen language is recorded in the `transcript.txt` header:

```
Model: Deepgram Nova-3
Language: ru
```

---

## 3. The buttons

| Button | What it does |
| --- | --- |
| `Start Recording` | Starts capturing the selected microphone. |
| `Stop Recording` | Stops, saves the WAV, then transcribes it. |
| `Retry Transcription` | Re-sends the **existing** WAV to Deepgram — you never have to record again. Available after any failure, and also on the next start of the app: if the newest recording has no `transcript.txt`, the app picks it up automatically and tells you so. |
| `Copy Transcript` | Copies the transcript to the clipboard (ready to paste into an LLM). |
| `Open Transcript` | Opens `transcript.txt`. |
| `Play Recording` | Opens `audio.wav` in your default player. |
| `Open recordings folder` | Opens the `recordings/` directory. |

States shown in the window: `Ready`, `Recording...`, `Transcribing...`,
`Done`, `Error`.

---

## 4. Audio format, and why

Recording is **16 kHz, mono, 16-bit PCM WAV** (`linear16`).

* Deepgram's models work at 16 kHz internally — anything sent at a higher rate
  is downsampled on their side, so recording at 44.1/48 kHz gives no accuracy
  benefit and only makes the file (and the upload) 3× larger.
  20 minutes at 16 kHz mono is about 38 MB.
* Uncompressed PCM avoids the artefacts that lossy codecs introduce exactly
  where it matters here — quiet fillers (`uh`, `um`) and mumbled endings.
* Mono, because there is one speaker; stereo would double the size for nothing.

If your microphone refuses 16 kHz or mono (some Windows drivers do), the app
automatically falls back to the device's native rate/channel count and records
that instead — the WAV header records the real format and Deepgram reads it
from the file. The window tells you when this happens.

The WAV is written continuously **while** you speak, so even a crash or a
disconnected microphone leaves you with everything recorded up to that moment.
**The WAV is never deleted, including after a successful transcription.**

---

## 5. Error handling

| Situation | What happens |
| --- | --- |
| `DEEPGRAM_API_KEY` missing | Message: `DEEPGRAM_API_KEY is not configured.` Recording still works; add the key and press `Retry Transcription`. |
| No internet | Clear message; the WAV is kept; `Retry Transcription` is enabled. |
| Wrong / revoked API key | HTTP 401/403 reported in plain language. |
| Deepgram API error, timeout, rate limit | Reported with the status code; retry available. |
| No microphone found | Reported; press `Refresh` after plugging one in. |
| Microphone unplugged mid-recording | Detected within ~4 s; recording is stopped and the audio so far is saved. |
| Cannot write the WAV (disk full, permissions) | Reported; recording stops safely. |
| Empty recording (< 0.5 s of audio) | Reported instead of being uploaded. |

Nothing that happens to the network can ever cost you a recording.

---

## 6. Logging

`logs/app.log` (rotating, 1 MB × 4) records: app start, recording start/stop,
the WAV path, transcription start, success, the Deepgram request ID, and any
errors.

**The API key is never logged.** Only whether one is configured.

The Deepgram request ID is worth keeping — it is what their support asks for.

---

## 7. Building `EnglishSpeechRecorder.exe`

```bat
.venv\Scripts\activate
pip install pyinstaller
build_exe.bat
```

or the same command by hand:

```bat
pyinstaller --noconfirm --clean --name EnglishSpeechRecorder --onefile --windowed ^
    --collect-all deepgram --collect-all sounddevice app\main.py
```

The result is `dist\EnglishSpeechRecorder.exe`. It runs on a machine **without
Python installed**.

To check a build without opening the window (no microphone, no network and no
API key needed):

```bat
dist\EnglishSpeechRecorder.exe --selftest
```

It verifies that the audio backend loads, that the Deepgram SDK is fully
packaged and that the request it builds is the right one, then writes
`selftest: PASSED` (exit code 0) to `logs/app.log`.

Important:

* The API key is **not** inside the `.exe`. Put your `.env` file next to the
  executable, or set the `DEEPGRAM_API_KEY` environment variable on that machine.
* `recordings/` and `logs/` are created next to the `.exe`, so keep it in a
  folder you can write to (not `C:\Program Files`).
* `--collect-all deepgram` is required: the SDK imports submodules lazily and
  PyInstaller cannot find them by static analysis alone. `--collect-all
  sounddevice` bundles the PortAudio DLL.
* Windows SmartScreen may warn about an unsigned executable the first time.

---

## 8. Project structure

```
app/
    __init__.py
    main.py          entry point, logging setup
    gui.py           Tkinter window, state machine, background worker
    recorder.py      microphone -> WAV (PortAudio via sounddevice)
    transcriber.py   Deepgram API layer (mockable, no GUI knowledge)
    config.py        paths, audio constants, language profiles, .env, logging
    utils.py         Session model, transcript formatting, filesystem helpers
tests/
    test_app.py      offline tests (no mic, no network, no API key needed)
recordings/          created at runtime
logs/                created at runtime
requirements.txt
.env.example
.gitignore
run.bat
build_exe.bat
```

---

## 9. Tests

```bat
python -m unittest discover -s tests -v
```

No microphone, no network and no Deepgram account are needed. The suite
includes a contract test that drives the **real** Deepgram SDK through an
`httpx` mock transport and asserts the exact request the app sends:

```
POST https://api.deepgram.com/v1/listen
     ?model=nova-3&language=en-US&filler_words=true&punctuate=true&smart_format=false
```

and the Russian variant:

```
POST https://api.deepgram.com/v1/listen
     ?model=nova-3&language=ru&punctuate=true&smart_format=false
```

It also checks that the transcript string is stored verbatim (including
Cyrillic, written as UTF-8), that the raw JSON body is saved unchanged, that
`filler_words` is sent for English and omitted for Russian, that no `diarize` /
`summarize` / `sentiment` / `intents` / `topics` option is ever sent, and that
the WAV survives a failed API call.

`python -m app.main --selftest` runs the same check on a source checkout.

---

## 10. Using the transcript with an LLM

Press `Copy Transcript` and paste it with a prompt such as:

> This is a raw, unedited transcript of me speaking English. Do not rewrite it.
> Analyse it and list: grammar mistakes, wrong tenses, wrong word order,
> unnatural constructions, repetitions, filler words and hesitations,
> unfinished sentences, self-corrections, and fluency problems. For each,
> quote what I said and show a natural alternative.

For a Russian recording, the same idea:

> Это сырая, неотредактированная расшифровка моей устной речи. Не переписывай
> её. Проанализируй: грамматические ошибки, порядок слов, неестественные
> конструкции, повторы, слова-паразиты, незаконченные предложения,
> самокоррекции и проблемы с беглостью.

The value of this app is that the transcript still contains all of that.
