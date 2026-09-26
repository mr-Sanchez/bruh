# CLAUDE.md

Voice Practice Coach — a local-only web app for practising spoken English/Russian.
Record in the browser (a monologue, or a picture description — spoken or typed) → verbatim
Deepgram transcript → on-demand Claude feedback in Russian, tagged by topic and aggregated
across sessions. Listening goes the other way: a YouTube video becomes a dictation lesson.
FastAPI backend + plain HTML/CSS/JS frontend (no build step, no framework); everything runs
on the user's PC in flat files, no database.

## Commands

```bat
run.bat                                    :: server + browser (http://127.0.0.1:8420)
python -m app.main --selftest              :: offline smoke check
python -m unittest discover -s tests -v    :: full suite (offline, no API keys)
python -m unittest tests.test_api -v       :: one module
```

Python 3.11+ (`Literal[*curriculum.TOPIC_KEYS]` in `analyzer.py`); the venv is 3.12.
`APP_HOST` / `APP_PORT` override the listen address.

## Architecture

```
app/main.py             entry point: env, logging, mkdirs, uvicorn, browser, --selftest
app/server.py           /api router FIRST, then StaticFiles("/") catch-all (Cache-Control: no-cache)
app/api.py              all /api/* routes; thin HTTP glue, orchestrates everything below
app/config.py           single source of truth: paths, constants, LANGUAGE_PROFILES, env, logging
app/curriculum.py       pure data: areas → topics (taxonomy v2), levels → modules → lessons
app/themes.py           pure data: contexts «уклон» + hand-written speaking prompts
app/utils.py            Session dataclass (owns every per-session file path), fs helpers
app/transcriber.py      Deepgram layer
app/analyzer.py         Claude: monologue / picture analysis, topic tagging
app/exercise_sets.py    Claude: AI exercise sets (generate + grade: Sonnet 5 low)
app/dictation_translation.py  Claude (Haiku): cut a lesson into parts, review a translation
app/theory.py           Claude: a roadmap lesson's theory (Sonnet 5 low)
app/speech_drills.py    pure: pace, fillers, pauses, shadowing alignment from word timings
app/dictation.py        pure: WebVTT parsing, sentences, word checking, translation parts
app/learner_model.py    pure: item bank, Leitner state, topic mastery
app/module_test.py      pure: a module entry test - assemble, hide answers, grade
app/roadmap.py          pure: roadmap lesson statuses, «Продолжить»
app/youtube.py          the only place that talks to YouTube (yt-dlp)
app/dictation_store.py  the ONLY reader/writer of dictation lessons
app/learner_store.py    the ONLY reader/writer of learner-model files; usage/cost log
app/progress_store.py   topic aggregation + score history → data/progress.json
app/theme_store.py      the ONLY reader/writer of data/themes.json (own contexts, last used)
app/static/js/          app.js (hash router), api.js, drill.js, recorder.js (shared by every
                        spoken activity), themes.js (the «уклон» picker), charts.js,
                        translation.js, views/*.js
```

Library modules stay framework-agnostic and are built through factory dependencies
(`get_transcriber_factory`, `get_analyzer_factory`, `get_fetcher_factory`) so tests can
override them. Some views register several `Views.*` (record.js → picture; speech.js → talk,
shadowing).

### Data on disk (runtime, gitignored)

```
recordings/<YYYY-MM-DD_HH-MM-SS>/  audio.webm, session.json, transcript.txt,
                                   deepgram_response.json, analysis.json, image.jpg
data/attempts.jsonl                AUTHORITATIVE, append-only — every exercise answer
data/progress.json, item_bank.json derived caches, rebuilt from analysis.json files
data/usage.jsonl                   append-only — tokens/minutes + cost per paid call
data/card_verdicts.jsonl           append-only cache of Claude's card checks
data/roadmap_marks.jsonl           AUTHORITATIVE, append-only — «уже знаю» / «пропустить»
data/themes.json                   own contexts, last used, Claude-written prompts (paid)
data/practice/<set-id>.json        AI sets + runs + verdicts; paid, NOT rebuildable
data/theory/<lesson>.json          every version of a lesson's theory; paid, NOT rebuildable
data/lesson_tasks/<lesson>.json    spoken tasks Claude wrote for a lesson; paid, append-only
data/module_tests/<module>.json    module entry tests + every run; paid, append-only
data/dictation/<video-id>/         lesson.json, audio.<ext>, subtitles.vtt, parts.json,
                                   results.jsonl (AUTHORITATIVE), translations.jsonl (paid)
logs/app.log                       rotating, 1 MB × 4
```

Derived caches rebuild after every analysis (`learner_store.refresh_after_analysis()`),
when missing, or on a schema bump. `utils.write_json` is atomic. New per-session filenames go
in `config.py` + a `Session` property, never hard-coded at a call site.

### Flows

* **Recording:** `POST /api/sessions` (multipart) → status `transcribing`, Deepgram in a
  `BackgroundTask` → frontend polls `GET /api/sessions/<id>` until `done`/`error`.
  `POST .../analyze` is a separate user action.
* **Picture** (`kind=picture` + `image`, ≤ 1000 px JPEG from the browser, type sniffed
  server-side): may send `text` instead of audio → stored verbatim, `done` at once, no Deepgram.
* **Lesson spoken task:** a monologue with `lesson_id` + `task_id` (`session.json` `lesson`);
  `/analyze` checks the lesson's rule (`analysis.json` `lesson.check`) and logs one
  `lesson_task` attempt per take.
* **Spoken drills** (`kind=talk` / `kind=shadowing`, details in `session.json`'s `drill`):
  measured, not analysed; one `filler_words_fluency` attempt is logged, `/analyze` refuses them.
* **Dictation:** `POST /api/dictation/lessons` (YouTube link) → background yt-dlp import →
  poll until `ready`/`error`. Each sentence is posted to `.../results` and graded **again**
  server-side. Afterwards an optional translation task: `POST .../parts`, then
  `POST .../parts/<n>/translation`; text is saved before Claude is asked, same text reuses
  the stored review.

## Invariants — do not break these

* **The transcript is never modified** — no fixing, rephrasing, filler removal or LLM
  post-processing; typed text stored as typed. `smart_format=False`, `punctuate=True` are
  deliberate. (`improved_version` lives in `analysis.json`, not in `transcript.txt`.)
* **`filler_words=True` is English-only**, sent from the language profile.
* **Nothing calls Claude automatically.** Every call is an explicit click and cached on disk;
  `analyze` reuses `analysis.json` unless `force=true`.
* **API keys are never logged** (only whether configured). Real env vars beat `.env`.
* **Claude's output is always Russian.** Quotes verbatim; corrections in the quote's language.
* **Original audio and pictures are never deleted.**
* **Missing key ⇒ graceful degradation:** no Deepgram key → saved with a clear error status;
  no Anthropic key → transcript still works.
* **`attempts.jsonl` is never regenerated, rewritten or truncated.** Item ids are content
  hashes (`learner_model.item_id`) so attempts survive re-analysis.
* **Session ids come from the URL** — `_session_directory()` rejects `/`, `\`, `.`, `..`;
  keep that guard on any new session-scoped route.
* **Spoken drills never call Claude.** Only the first round of a «60 секунд» series is an
  attempt; a talk without filler detection (non-English) is not scored.
* **Dictation reference = the video's own subtitles**: manual first, else automatic in the
  video's own language (never a machine translation); neither → refused (2026-09-20), never
  Deepgram. Audio stored as served (no FFmpeg); sentences shown as the captions spell them.
* **Dictation is free; only its translation task calls Claude** (Haiku; the split is a model
  call, 2026-09-26). Usage kinds `dictation_split` / `dictation_translation`.
* **Dictation stays out of the learner model** — results and translation mistakes live only in
  the lesson dir, never `attempts.jsonl` / item bank. They surface as «сложные слова» and count
  for the streak and the daily workout.
* **A mistake card is a new sentence, never the quote** (2026-09-26): built from
  `issues[].drills`, checked by Sonnet (`card_grading`) on click. A fix item without drills is
  retired (`learner_model.is_retired`): kept, never queued. Rule (`pattern`) items are retired
  too — the rule and its examples show on the mistake card after the answer — and so are
  phrases that are grammar notes with no gap (a formula, or a takeaway only).
* **Every generation takes a context «уклон»** (a `theme`, 2026-09-26): AI sets, analysis
  drills, own-theme speaking prompts; default = the last one used (IT / бэкенд first).
  Theory never follows it. `context` in attempts means the screen, not the theme.
* **`curriculum.py` is the closed catalogue** (taxonomy v2, 2026-09-26): 15 areas → 76
  topics; a roadmap lesson teaches one topic and its id **is** the topic key. It drives the
  analysis/grading schema and prompts, `progress.json`, the item bank and every label.
  Authored by hand, never generated; keys are stable — changing one is a migration, and
  any change bumps `ANALYSIS_SCHEMA_VERSION`, `progress_store.SCHEMA_VERSION` and
  `BANK_SCHEMA_VERSION`. Unknown (older) keys count as area «other».

## Conventions

* Python: `from __future__ import annotations`, full type hints, `Final` constants, module
  docstrings that explain *why*, 100-char lines, stdlib + the 7 pinned deps only.
* SDK imports (`anthropic`, `deepgram`, `yt_dlp`) are lazy, inside factories.
* Typed exceptions (`TranscriptionError`, `AnalysisError`, …) carry a human message;
  `api.py` maps them to 400 (missing key) / 502 (upstream failure).
* Code and comments in **English**; UI text and Claude's feedback in **Russian**.
* Frontend: no bundler, no deps. Each view is an IIFE registering `window.Views.<name>` with
  `render(container, param)` and optional `dispose()`. New routes → `app.js`'s
  `TAB_FOR_ROUTE`; new endpoints → `api.js`.
* Recording duration is measured client-side on purpose — no server-side audio decoding.

## Testing

Fully offline: no mic, browser, network or real keys; never add a test that touches the
network. Inject fakes via `client_factory` (library) or `app.dependency_overrides` (routes).
`tests/test_app.py` drives the **real** Deepgram SDK through `httpx.MockTransport` to pin
the exact request; `tests/test_speech_drills.py`'s `timed_words` / `deepgram_payload` build
word-level responses for other tests. Keep `--selftest` in sync when a request path changes.

## Plan

`progress.md` is the working plan — read it before feature work. Stages 1–8 are done (8: the
A2 → C1 roadmap - lessons with theory, sets, a spoken task, module tests, «Урок дня»); next
candidates are under «Later». All activities feed one learner model (item bank +
`attempts.jsonl` + Leitner) behind the daily «Сегодня» workout. Budget: ≤ ~8–10 ¢ of Claude
per exercise; 7–10 new cards a day. PyInstaller packaging is deferred, but
`config.base_dir()` still handles a frozen build.
