# CLAUDE.md

Voice Practice Coach — a local-only web app for practising spoken English/Russian.
Record in the browser (a monologue, or a description of a picture — spoken or typed) →
verbatim Deepgram transcript → on-demand Claude feedback in Russian, tagged by topic and
aggregated across sessions.

Python FastAPI backend + plain HTML/CSS/JS frontend (no build step, no framework).
Everything runs on the user's PC; state lives in flat files, there is no database.

## Commands

```bat
run.bat                                    :: start server + open browser (http://127.0.0.1:8420)
python -m app.main                         :: same, by hand
python -m app.main --selftest              :: offline smoke check (no server/browser/network)
python -m unittest discover -s tests -v    :: full test suite (offline, no API keys needed)
python -m unittest tests.test_api -v       :: one module
```

Requires Python 3.11+ (`analyzer.py` uses `Literal[*TOPIC_KEYS]`, a 3.11 syntax);
the checked-in venv is 3.12. `.venv\Scripts\activate`, then `pip install -r requirements.txt`.
`APP_HOST` / `APP_PORT` override the listen address.

## Architecture

```
app/main.py           entry point: env, logging, mkdirs, uvicorn, browser, --selftest
app/server.py         FastAPI factory — /api router FIRST, then StaticFiles("/") catch-all
app/api.py            all /api/* routes; thin HTTP glue only
app/config.py         single source of truth for paths, constants, LANGUAGE_PROFILES, env, logging
app/utils.py          Session dataclass (owns every per-session file path), fs + formatting helpers
app/transcriber.py    Deepgram layer  — knows nothing about HTTP/FastAPI
app/analyzer.py       Claude layer    — structured output, Russian feedback, topic tagging
app/exercise_sets.py  Claude layer    — AI exercise sets: generation (Sonnet 5, low) + grading
                      of translations (Haiku 4.5), one call each
app/speech_drills.py  pure speech measurements from Deepgram word timings: pace, fillers,
                      pauses, shadowing alignment (no I/O, no Claude)
app/progress_store.py cross-session topic aggregation + score history → data/progress.json
app/learner_model.py  pure learner-model rules: item bank, Leitner state, topic mastery (no I/O)
app/learner_store.py  the ONLY place learner-model files are read/written; usage/cost log
app/server.py         (static files are served with Cache-Control: no-cache - no build step
                      means no fingerprinted names, so the browser must revalidate)
app/static/           frontend: index.html, css/app.css, js/api.js, js/app.js (hash router),
                      js/drill.js (card + cloze runners, checked in the browser),
                      js/charts.js (SVG sparklines, score charts, stat tiles),
                      js/recorder.js (MediaRecorder + level meter, shared by every
                      spoken activity),
                      js/views/{today,practice,record,speech,history,session,progress}.js
                      (record.js also registers Views.picture: the recorder in picture mode;
                      speech.js registers Views.talk and Views.shadowing)
```

Layering rule: `api.py` orchestrates; `transcriber.py` / `analyzer.py` / `progress_store.py` /
`learner_*.py` stay framework-agnostic and are constructed through factory dependencies
(`get_transcriber_factory`, `get_analyzer_factory`) so tests can override them.

### Data on disk (created at runtime, all gitignored)

```
recordings/<YYYY-MM-DD_HH-MM-SS>/   audio.webm, session.json, transcript.txt,
                                    deepgram_response.json, analysis.json,
                                    image.jpg (picture descriptions only)
                                    spoken drills are sessions too; their results are
                                    derived from deepgram_response.json on every read
data/progress.json                  derived cache — analysis.json files stay authoritative
data/item_bank.json                 derived cache — items from every analysis.json
data/attempts.jsonl                 append-only, AUTHORITATIVE — every exercise answer
                                    (card: item_id; topic drill: topic + score)
data/usage.jsonl                    append-only — tokens/minutes + estimated cost per paid call
data/practice/<set-id>.json         AI exercise sets + every run and Claude verdict; paid for,
                                    NOT rebuildable; wrong answers are a 2nd source of bank items
logs/app.log                        rotating, 1 MB × 4
```

`progress.json` and `item_bank.json` are fully rebuilt after every analysis
(`learner_store.refresh_after_analysis()`), so a forced re-analysis replaces that session's
counts, scores and items; both also rebuild themselves when missing or on a schema bump.
`utils.write_json` is atomic (temp file + `os.replace`).
New per-session filenames belong in `config.py` + a `Session` property in `utils.py`,
never hard-coded at a call site.

### Request flow

`record.js` (MediaRecorder) → `POST /api/sessions` (multipart) → session dir written,
status `transcribing`, Deepgram called in a FastAPI `BackgroundTask` → the frontend polls
`GET /api/sessions/<id>` until `done`/`error`. `POST /api/sessions/<id>/analyze` is a
separate, explicit user action.

Spoken drills («60 секунд» `#/talk`, shadowing `#/shadowing`) post to the same route with
`kind=talk` / `kind=shadowing` plus what they practise (`prompt_index` + `series`, or
`source_session_id` + `passage`), stored as `session.json`'s `drill`. They are measured, not
analysed: after transcription the background task logs one topic attempt on
`filler_words_fluency` (`speech_drills` computes it), and `/analyze` refuses them.

A picture description (`#/picture`) uses the same route with `kind=picture` + an `image`
(downscaled to ≤ 1000 px JPEG in the browser; the server sniffs the real type). Instead of
audio it may send `text`: the typed text is written verbatim as the transcript, status is
`done` at once and Deepgram is never called. `session.json` carries `kind`, `input_mode`
and `image_filename`; sessions without them are voice monologues. Analysis then sends the
image to Claude (`PictureAnalysis` schema: + `not_mentioned`, `scene_vocabulary`) and logs
usage as `picture_analysis`.

## Invariants — do not break these

* **The transcript is never modified.** No grammar fixing, no rephrasing, no filler
  removal, no LLM post-processing — on either side. A typed description is stored exactly
  as typed. `smart_format=False` and
  `punctuate=True` are deliberate; do not "improve" the Deepgram options. (The
  analysis's `improved_version` is a separate model answer stored in `analysis.json`,
  not an edit of `transcript.txt`.)
* **`filler_words=True` is English-only** (Deepgram supports it only for English), sent
  from the language profile, never unconditionally.
* **Analysis is on-demand and cached.** `analyze` reuses `analysis.json` unless
  `force=true`, so a double-click never costs a second API call. Nothing calls Claude
  automatically.
* **API keys are never logged** — log only whether one is configured. `.env` is
  gitignored; real env vars take priority over `.env`.
* **Claude's output is always Russian**, whatever language was practiced. Quotes are
  verbatim from the transcript and corrections stay in the quote's own language.
* **Original audio is never deleted**, including after a successful transcribe/analyze.
  The same holds for a description's picture.
* **Missing key ⇒ graceful degradation.** A recording still uploads and is saved with a
  clear error status when `DEEPGRAM_API_KEY` is absent; the transcript still works when
  `ANTHROPIC_API_KEY` is absent.
* **`attempts.jsonl` is never regenerated, rewritten or truncated** — it is the only record
  that cannot be rebuilt. Item ids are content hashes (`learner_model.item_id`), so attempts
  stay linked across re-analysis.
* **Session ids are path segments from the URL** — `_session_directory()` rejects `/`,
  `\`, `.` and `..`. Keep that guard on any new session-scoped route.
* **Spoken drills never call Claude.** Their whole result comes from Deepgram's word
  timings (`speech_drills.py`); only the first round of a «60 секунд» series is logged as
  an attempt, and a talk without filler detection (any language but English) is not scored.
* **`TOPIC_TAXONOMY` is a closed set.** It drives the Claude schema (`Literal[TopicKey]`),
  the prompt text and `progress.json` at once — changing it is a coordinated migration
  and needs a `schema_version` bump.

## Conventions

* Python: `from __future__ import annotations`, full type hints, `Final` constants,
  module docstrings that explain *why*, 100-char lines, stdlib + the 6 pinned deps only.
* Heavy SDK imports (`anthropic`, `deepgram`) are lazy, inside factories, to keep startup fast.
* Errors surface as typed exceptions (`TranscriptionError`, `AnalysisError` and subclasses)
  carrying a message meant for a human; `api.py` maps them to HTTP status codes
  (400 missing key, 502 upstream failure).
* Code, comments and docstrings in **English**; all user-facing UI text and Claude's
  feedback in **Russian**.
* Frontend: no bundler, no dependencies. Each view is an IIFE registering
  `window.Views.<name>` with `render(container, param)` and optional `dispose()`.
  New routes go in `app.js`'s `TAB_FOR_ROUTE`, new endpoints in `api.js`.
* Recording duration is measured client-side on purpose — the app deliberately avoids an
  audio-decoding dependency, so do not parse durations server-side.

## Testing

Everything is offline: no microphone, no browser, no network, no real keys.

* `tests/test_app.py` — transcriber + session/file-layout; includes a contract test driving
  the **real** Deepgram SDK through an `httpx.MockTransport` to assert the exact request built.
* `tests/test_analyzer.py` — fake Anthropic client via `client_factory`.
* `tests/test_exercise_sets.py` — set generation / grading with a fake client.
* `tests/test_api.py` — `TestClient` + `app.dependency_overrides` with fake factories.
* `tests/test_progress_store.py` — topic aggregation, scoring, score history.
* `tests/test_learner_model.py` — pure bank / Leitner / mastery rules.
* `tests/test_learner_store.py` — bank rebuild, attempts and usage logs on disk.
* `tests/test_speech_drills.py` — pace/filler/pause metrics, passage splitting, reading
  alignment (its `timed_words` / `deepgram_payload` helpers build word-level responses for
  the API tests too).

Inject fakes through `client_factory` (library layer) or `dependency_overrides` (routes).
Never add a test that touches the network. Keep `--selftest` in sync when a request path changes.

## Not built yet

The working plan lives in `progress.md`: read it before starting feature work. The app is
turning into a learning platform where monologue analysis is one activity among several.
All activities feed a shared learner model (item bank + `data/attempts.jsonl` + Leitner
repetition), and a daily «Сегодня» workout is built from it. Budget: ≤ ~8–10 ¢ of Claude
per exercise; 7–10 new cards a day (`/api/learner/queue`). Stage 1 (learner model backend,
`/api/learner/*`, `/api/usage`), Stage 2 (free drills: cards, cloze), Stage 3 (the
«Сегодня» home screen, `/api/learner/today`, tabs Сегодня · Занятия · История · Прогресс)
Stage 4 (picture description, voice or text), Stage 5 (AI exercise sets,
`/api/practice/sets`) and Stage 6 (spoken drills, `/api/speech/*`) are done; the next
candidates live under «Later» in `progress.md` (YouTube dictation is explicitly deferred).
PyInstaller packaging is deferred (a persistent server doesn't fit a onefile build), but
`config.base_dir()` still handles a frozen build.
