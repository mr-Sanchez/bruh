# English Coach

A local web app for learning spoken English (and practising Russian). You speak, and
Deepgram turns what you said into a **verbatim transcript**. On request, Claude reviews it
and explains your mistakes in Russian. Every mistake becomes a practice card, and one
learner model uses all of them to plan what you train next: the daily «Сегодня» workout,
spaced-repetition cards, AI exercise sets and a fixed A2 → C1 course.

Everything runs on your own PC: a small Python backend serves the page in your browser and
keeps its data in plain files. There is no hosting, no database and no account. You only
need your own Deepgram and Anthropic API keys.

Two rules shape the whole app:

* **The transcript is never touched.** No grammar fixes, no rephrasing, no removal of
  fillers, repeats or unfinished sentences. `smart_format` is off on purpose, and
  `punctuate=true` adds sentence punctuation without reformatting words. Typed text is
  stored exactly as typed.
* **Nothing calls Claude on its own.** Every paid call is a button click, with its
  approximate price on the button. Every result is kept on disk, so pressing the button
  again (or redoing an exercise) costs nothing.

---

## 1. Quick start

### 1.1 API keys

1. **Deepgram** (speech → text): sign up at <https://console.deepgram.com/signup> (new
   accounts get free credit), then go to **API Keys → Create a New API Key**.
2. **Anthropic** (feedback and exercises): create a key at
   <https://console.anthropic.com/> under **API Keys**.

Copy `.env.example` to `.env` and paste both keys:

```
DEEPGRAM_API_KEY=your_deepgram_api_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

`.env` is gitignored. Real environment variables with the same names take priority over
it. Optional settings, all described in `.env.example`:

| Variable | Meaning |
| --- | --- |
| `DEEPGRAM_LANGUAGE` | language selected at start: `en-US` (default), `ru`, `multi` |
| `ANALYSIS_EFFORT` | analysis depth vs. cost: `low`, `medium` (default), `high`, `xhigh`, `max` |
| `DEEPGRAM_MIP_OPT_OUT` | `true` opts out of Deepgram's Model Improvement Program (paid plans) |
| `APP_HOST` / `APP_PORT` | listen address, default `127.0.0.1:8420` |

### 1.2 Install and run

Requires **Python 3.11+** (3.12 recommended).

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
run.bat                       :: or: python -m app.main
```

The server starts, and your browser opens `http://127.0.0.1:8420`. Keep the console
window open: closing it or pressing Ctrl+C stops the server. Always use that URL and never
open `app/static/index.html` directly, because browsers only allow microphone access on a
real server origin.

If a key is missing, the rest of the app still works. Without Deepgram, a recording is
saved with a clear error status. Without Anthropic, transcripts and every free exercise
still work, and the paid buttons explain what is missing.

---

## 2. What's inside

The sidebar has four sections.

### Сегодня: the daily workout

The home screen is assembled by code, without any model call:

1. **Cards due today**: reviews plus 7–10 new mistake cards and up to 5 new word cards.
2. **Dictation**: 5 sentences from an imported YouTube lesson (about 4 minutes).
3. **A live step**: a monologue on the day's prompt, or a picture description.
4. **«Урок дня»** (optional, paid actions): the course lesson your own mistakes point to
   most, or else the next lesson of the course, together with its next action (theory,
   exercise set or spoken task).
5. **«Речевая разминка»** (optional): a spoken drill.

It also shows the topic to focus on, the minutes left, a day streak and score tiles.

### Занятия: activities

* **Монолог.** Record yourself on any prompt, then press **Анализировать** to get a
  coach-style review in Russian. For each mistake you get the verbatim quote, an
  explanation, a minimal correction, more natural alternatives and the reusable
  construction behind the fix, all tagged with a topic. The review also includes the
  whole monologue retold the way it could have sounded (your transcript stays as it was),
  words you were looking for, phrases to remember and 1–10 scores for grammar, vocabulary,
  fluency and naturalness.
* **Описание картинки.** Pick, drop or paste a picture, then describe it by voice or by
  typing. The review also lists what you did not mention and gives vocabulary for the
  scene.
* **«60 секунд»** and **Shadowing.** Spoken drills measured only from Deepgram's word
  timings, with no Claude call. «60 секунд» means three one-minute takes on the same
  prompt, compared by pace, fillers per minute and long pauses. In Shadowing you read a
  passage of your own improved text aloud and it is aligned word by word with what was
  heard.
* **Диктант.** Paste a YouTube link, and the video becomes a lesson made of its audio and
  the sentences of **its own subtitles**. A video without usable subtitles is refused;
  nothing is sent to Deepgram. You type each sentence while it replays: Enter listens
  again, Space moves to the next word, Tab reveals it, and `[` / `]` switch sentences. The
  dictation itself is free. Afterwards you can translate the lesson part by part and get
  each translation reviewed (paid).
* **Курс A2 → C1.** A fixed course of 73 lessons in 16 modules. Every lesson is open;
  «Продолжить» goes to the first unfinished one, and «Уже знаю» / «Пропустить» are marks,
  not locks. A lesson page has:
  * **Теория**: Claude writes it for the lesson's level, including comments on your own
    recorded mistakes on the topic. Every version is kept.
  * **Упражнения урока**: AI exercise sets.
  * **A spoken task**: a 1–2 minute monologue that needs the lesson's rule; the analysis
    scores how you used the rule.

  A lesson counts as **mastered** after two set runs of at least 80 % on different days.
  Each module has an **entry test** (one choice question and one gap per lesson). It is
  graded on the server, so retakes are free, and it can mark the lessons you already know.
* **Topic pages.** 15 areas → 76 topics, each with its cards, AI sets and links to
  Cambridge Grammar, YouGlish and SkELL.

**AI exercise sets.** A set has 9 exercises on one topic: 3 gaps, 2 «find the mistake» and
4 Russian → English translations. It is built around your own mistakes on the topic and
never repeats a sentence from earlier sets. Gaps and fixes are checked in the browser;
translations are graded by Claude when you hand in the set. Every wrong answer becomes a
card. A set also suggests 8 words for the topic, and you choose which of them become word
cards.

**Context «уклон».** Everything generated (sets, analysis drills, speaking prompts and
lesson tasks) follows a context you pick: IT / бэкенд (the default), созвоны, собеседование,
путешествия and more, or one of your own. Theory and module tests do not use it.

### Cards and repetition

* A **mistake card** is never your quote itself. It is a *new* Russian sentence whose
  natural English needs the construction you got wrong («Скажите по-английски»). You can
  type or dictate the answer, and Claude checks it. After you answer, the card shows the
  original quote, the correction, the rule and a link to the recording.
* A **word card** has Russian on the front. You recall the English and grade yourself
  «Помню / Не помню».
* Cards move through **Leitner boxes** (1/2/4/8/16 days). A wrong answer sends a card back
  to box 1, and so does repeating the mistake in a later recording.

### История and Прогресс

**История** lists every recording, exercise, set and dictation day. **Прогресс** shows score
charts, course progress per level, topic mastery by area, the mistake bank with its Leitner
state, tricky dictation words, the speech drill trend and **API spend** per activity.

---

## 3. Language

The language picker decides what is sent to Deepgram (model `nova-3`):

| Choice | Request | Filler words |
| --- | --- | --- |
| English (US) | `language=en-US` | **yes** (`filler_words=true`) |
| Русский | `language=ru` | no |
| Mixed (RU + EN) | `language=multi` | no |

Deepgram's filler-word detection is English-only, so fluency drills are scored only for
English. Claude's feedback is **always in Russian**; quotes stay verbatim and corrections
are in the quote's language. Topics, cards and exercises are English-centric.

---

## 4. Models and cost

| What | Model | Approx. cost |
| --- | --- | --- |
| Monologue / picture analysis | Claude Sonnet 5 (effort `medium`) | 5–10 ¢ |
| AI exercise set (incl. its vocabulary) | Sonnet 5, `low` | ≈ 3 ¢ |
| Grading a set's translations, checking a card | Sonnet 5, `low` | ≈ 1 ¢ / under 1 ¢ |
| Lesson theory, module entry test | Sonnet 5, `low` | ≈ 3 ¢ each |
| Spoken tasks for a lesson (3 per click) | Sonnet 5, `low` | ≈ 2 ¢ |
| Dictation translation review, per part | Sonnet 5, `low` | ≈ 2 ¢ |
| Cutting a dictation lesson into parts | Claude Haiku 4.5 | ≈ 0.5 ¢ |
| Speaking prompts for your own context | Haiku 4.5 | ≈ 0.1 ¢ |
| Speech → text | Deepgram Nova-3 | about 0.4 ¢ per minute |

The prices are estimates. Once the usage log (`data/usage.jsonl`) holds real calls, the
buttons show the real average price. The budget target is at most 8–10 ¢ of Claude per
exercise. An exact or empty answer is never sent for checking, and every verdict is cached.
Models and limits live in `app/config.py`.

---

## 5. Data on disk

Everything is created at runtime and is gitignored. Back up `recordings/` and `data/`: files
marked *paid* would cost money to regenerate, and files marked *authoritative* cannot be
regenerated at all.

```
recordings/<YYYY-MM-DD_HH-MM-SS>/  one folder per recording
    audio.webm                     original audio, never deleted
    image.jpg                      the picture (picture description)
    session.json                   status and metadata
    transcript.txt                 the verbatim transcript
    deepgram_response.json         the raw Deepgram response
    analysis.json                  Claude's review (paid)
data/
    attempts.jsonl                 every exercise answer - authoritative, append-only
    word_picks.jsonl               words chosen as cards - authoritative
    roadmap_marks.jsonl            «уже знаю» / «пропустить» - authoritative
    usage.jsonl                    tokens / minutes and cost of every paid call
    card_verdicts.jsonl            cached card checks (paid)
    themes.json                    your contexts and their prompts (paid)
    practice/<set-id>.json         AI sets with every run (paid)
    theory/<lesson>.json           every version of a lesson's theory (paid)
    lesson_tasks/<lesson>.json     spoken tasks of a lesson (paid)
    module_tests/<module>.json     module tests and every run (paid)
    progress.json, item_bank.json  caches, rebuilt from analysis.json files
    dictation/<video-id>/          lesson.json, audio as YouTube served it, subtitles.vtt,
                                   results.jsonl (authoritative), parts.json and
                                   translations.jsonl (paid)
logs/app.log                       rotating, 1 MB × 4
```

Recording duration is measured in the browser, and audio is stored exactly as the browser
recorded it (usually WebM/Opus). Deepgram detects the container itself, so neither FFmpeg
nor server-side audio decoding is needed.

---

## 6. Errors and logging

| Situation | What happens |
| --- | --- |
| A key is missing | See 1.2: the rest of the app works, and the affected action says what is missing. |
| Wrong / revoked key | Reported in plain language. |
| Claude rate limit, timeout, refusal | A retry-friendly message; nothing already saved is lost. |
| No internet | A clear message; you can retry from «История» later. |
| No microphone / permission denied | Reported by the browser; press Refresh in the microphone list after fixing it. |
| YouTube video without subtitles or over 20 min | Refused with the reason. |

`logs/app.log` records uploads, transcription, analysis, imports and errors. **API keys are
never logged**; only whether each key is configured.

---

## 7. Project structure

```
app/
    main.py              entry point: env, logging, uvicorn, browser; --selftest
    server.py            FastAPI app: /api first, then the static frontend
    api.py               every /api/* route (thin glue)
    config.py            paths, constants, models, language profiles, .env, logging
    curriculum.py        the course and topic catalogue (areas, topics, levels, lessons)
    themes.py            built-in contexts «уклон» and their speaking prompts
    utils.py             Session (per-recording files), filesystem helpers
    transcriber.py       Deepgram layer
    analyzer.py          Claude: monologue / picture analysis
    exercise_sets.py     Claude: AI sets, grading, lesson tasks, module tests, prompts
    theory.py            Claude: a lesson's theory
    dictation_translation.py  Claude: cutting a dictation lesson, reviewing a translation
    speech_drills.py     pace, fillers, pauses, shadowing alignment (pure)
    dictation.py         subtitles -> sentences, word checking (pure)
    learner_model.py     item bank, Leitner state, topic mastery (pure)
    module_test.py       assembling and grading module tests (pure)
    roadmap.py           lesson statuses, «Продолжить», «Урок дня» (pure)
    youtube.py           the YouTube import (yt-dlp)
    *_store.py           the only readers/writers of their files: learner, progress,
                         theme and dictation data
    static/              plain HTML/CSS/JS, no build step
        index.html, css/app.css
        js/app.js (router), api.js, recorder.js, drill.js, themes.js, translation.js,
        charts.js, icons.js
        js/views/{today,practice,roadmap,moduletest,record,speech,dictation,
                  history,session,progress}.js
tests/                   one test module per app module, fully offline
```

Architecture and invariants: `CLAUDE.md`; the plan and decision history: `progress.md`.

---

## 8. Tests

```bat
python -m unittest discover -s tests -v
python -m app.main --selftest
```

The tests need no microphone, browser, network or real API keys. Deepgram, Claude and
yt-dlp are replaced with fakes. One contract test drives the **real** Deepgram SDK through
an `httpx` mock transport to check the exact HTTP request it builds. `--selftest` is a
quick smoke check: it builds the Deepgram and Claude requests against fake clients and makes
sure the app assembles, without starting a server or touching the network.

**Packaging:** a PyInstaller `.exe` is deferred (a local server doesn't fit it); use `run.bat`.
