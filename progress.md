# Plan & progress

Working plan for the Voice Practice Coach. Keep items short; move them to "Done"
with a date when they ship.

## Direction (decided 2026-09-19)

The app is growing step by step into a language-learning **platform**. Recording and
analysing a monologue is no longer the centre of the app; it is one *activity* among
several (monologue, picture description, topic drills, later dictation). Every activity
writes its results into **one shared learner model**, and that model decides what to
train next.

Constraints for every new exercise:

- **Budget: at most ~8–10 US cents of Claude API per concrete exercise.** Cheapest first:
  1. **$0**: exercises built from data already in `analysis.json` (`quote`/`correction`,
     `better_versions`, `pattern`, `vocabulary`, `takeaways`, `improved_version`), checked
     in the browser;
  2. **~2–4 ¢**: LLM-generated sets (Sonnet 5, `effort: low`, structured output),
     generated only on an explicit click, cached to disk, cost shown next to the button;
  3. **Deepgram only**: spoken drills measured locally (no Claude call).
- Nothing calls Claude automatically (same rule as analysis).
- External resources are online deep links built from the phrase or topic (YouGlish,
  Cambridge Dictionary / Cambridge Grammar, SkELL). They cost nothing. **No textbook
  references** (decided 2026-09-19: the learner does not study from books).
- The topic taxonomy and external resources are English-centric; for Russian sessions,
  offer only the $0 layer.

Target navigation: `Сегодня` (new home: daily workout) · `Занятия` (Монолог, Описание
картинки, Тренажёры по темам, later Диктант) · `История` (all activities) · `Прогресс`
(score charts, topic mastery, mistake bank).

## Done

- **2026-09-20 — Stage 6: spoken drills (Deepgram only).** Two activities that cost no
  Claude call at all: their whole result is computed from the word timings Deepgram
  already returns (`app/speech_drills.py`, pure functions; nothing decodes audio).
  - **«60 секунд»** (`#/talk`): one prompt, **three one-minute takes in a row** (decided
    2026-09-19, the 4-3-2 idea — the same thought said again gets smoother). The recorder
    stops itself at 60 s. Per take: words per minute, fillers per minute (English only —
    `filler_words`), pauses ≥ 2 s, immediate repeats, and the transcript with fillers
    highlighted and pauses shown in place; the takes of a series are compared in a table.
    The prompt is half the list away from the day's monologue, so the two never coincide.
  - **Shadowing** (`#/shadowing`): a passage of the learner's own `improved_version`
    (whole sentences, 20–50 words), optionally read out by the browser's own voice
    (`speechSynthesis`, free), then read aloud and aligned word by word with what Deepgram
    heard: missed / misheard (with what was heard) / unclear (confidence < 0.6) words are
    marked, extra words shown, and every problem word gets a YouGlish link. Words with
    digits are not scored (no `smart_format` means "2024" comes back as words).
  - Both are ordinary sessions (`kind` = `talk` / `shadowing`, `session.json` gains
    `drill`), so the audio is kept like any other recording; **they are never analysed by
    Claude** (`/analyze` answers 400) and never fill the day's live step.
  - Learner model (decided 2026-09-19): each logs a topic attempt on «Слова-паразиты и
    беглость» — a talk with `fluency_score` (fillers + long pauses per minute: 1.0 at ≤ 2,
    0 at 12, so the 0.8 pass sits at 4/min), shadowing with its share of words read right.
    **Only round 1 of a talk counts** (spontaneous speech); rounds 2–3 are practice. A talk
    in a language without filler detection is not scored at all.
  - «Сегодня» gains an optional 5th step «Речевая разминка» (Deepgram key needed, does not
    count towards "all done" or the minutes); «Прогресс» shows first-take → last-take
    per series and the Deepgram spend (purpose `speech_drill`); «История» lists the runs.
  - Every spoken take (monologue and picture description too) now shows its pace and
    hesitations on the session page. Microphone capture moved to `js/recorder.js`, shared
    by all four activities. 159 tests, all offline.
  - **Still to check:** the numbers on real takes — the 2/12 per-minute fluency scale and
    the 2 s pause threshold are a first guess and may need tuning after a week of use.

- **2026-09-19 — Stage 5: AI exercise sets.** A paid activity, always on an explicit click
  with its price on the button. Decided with the user: a mixed set, translations graded
  once at the end, wrong answers become cards, offered on the topic page and «Сегодня».
  - A set is 9 exercises on one topic (3 gaps, 2 «fix the mistake», 4 RU→EN translations),
    built around the learner's own fixes and rules on the topic (up to 8, English only), in
    an IT/work context; sentences of earlier sets are passed as "do not repeat". Sonnet 5,
    effort low, structured output. Not for fillers/repetitions/«Прочее».
  - Gaps and fixes are checked in the browser (with «Засчитать как верный»). Translations
    go to Haiku 4.5 in one call when the set is handed in; an exact match of the reference
    or a blank answer needs no call, and verdicts are cached per answer, so redoing a set is
    free. If grading fails (no key, network), the learner grades the translations against
    the reference and the run still counts.
  - Stored in `data/practice/<set-id>.json` with every run. A generated but unstarted set is
    handed back instead of generating a new one (no double charge). A run is a topic attempt
    (`exercise: "ai_set"`, `score`, `set_id`) and feeds topic accuracy; every wrong answer
    becomes a `fix` card of the learner's own sentence (`origin: "ai_set"`, item bank v2),
    under the usual 7–10 new cards a day.
  - «Сегодня»: an optional 4th step on the main topic (not counted in minutes or "all done");
    shown with a key, or when a paid set is waiting. «Прогресс» shows the average price;
    «История» lists runs. Usage purposes `exercise_set` / `exercise_grading`.
  - **Still to check:** the real price per set (estimate ≈ 3 ¢) and the quality of the
    generated sentences on real data — not yet run against the real API. 135 tests.
- **2026-09-19 — Stage 4: picture description.** «Описание картинки» (`#/picture`) is live
  under «Занятия»: pick, drop or paste (Ctrl+V) a picture, then describe it **by voice**
  (default) or **by text**.
  - The browser redraws the picture at ≤ 1000 px on the long side and re-encodes it as
    JPEG (≈ 1k input tokens, EXIF stripped). The server checks the real type from the
    bytes (JPEG/PNG/WebP/GIF, ≤ 5 MB).
  - It is a regular session: `session.json` gains `kind` (`monologue` | `picture`),
    `input_mode` (`voice` | `text`) and `image_filename` (`image.jpg`). Older sessions
    read as voice monologues. A typed text is stored verbatim as `transcript.txt` and never
    goes to Deepgram; the audio of a spoken take is kept as usual.
  - Claude gets the picture and the transcript: the usual feedback plus `not_mentioned`
    (what is in the picture but not in the description, with a Russian note and a simple
    sentence to say it) and `scene_vocabulary` (words for the scene). `analysis.json` is
    schema v3 (`kind`, `input_mode`, the two picture fields). Issues feed the same topics;
    scene words become phrase cards.
  - Typed text: no filler/repetition topics; fluency is still scored but left out of the
    overall score and out of the score history (not comparable with speech).
  - Usage purpose `picture_analysis`, so its real average price shows on «Занятия» and
    «Прогресс» next to the monologue. **Still to check:** the 5–8 ¢ target on real
    descriptions.
  - «Сегодня»: a picture description done today counts as the day's live step
    (`activity: "picture"`), and the monologue step links to it as an alternative.
  - Static files are served with `Cache-Control: no-cache`, so the browser picks up a new
    version of the frontend after an update. 116 tests, all offline.
- **2026-09-19 — Stage 3: «Сегодня» + new navigation.** Tabs are now `Сегодня` (home) ·
  `Занятия` · `История` · `Прогресс`; the recorder lives under «Занятия» (`#/record`).
  - «Сегодня» (`#/today`, `GET /api/learner/today`) is assembled by code, no LLM:
    1) cards from the daily queue (at most 15 in the workout, the rest stays on «Занятия»);
    2) one cloze on the cloze topic with the highest priority, on a text not practised yet,
       else the one with the lowest best score; 3) a monologue on the day's prompt
       (20 English prompts, mostly IT/work, one per day, «Другая тема» cycles them).
    Each step is done/todo from the attempts log and today's recordings (a recording that
    is not analysed yet links to its session). Also: the main topic now (highest
    priority) with its theory link, minutes left (~0.4 min/card, 3 min cloze, 3 min
    monologue), and a day streak (any exercise or recording counts; today not yet active
    does not break it).
  - Score tiles with sparklines on «Сегодня»; «Прогресс» has a chart per skill (small
    multiples, hover/keyboard tooltip, table view), topic mastery (in speech / accuracy /
    learned / due), the mistake bank grouped by topic with Leitner state, and API spend.
  - «Занятия» hub: activities (Монолог with its real average analysis cost; «Описание
    картинки» marked «Скоро»), then cards, cloze texts and topic drills.
  - «История» adds exercise results per day (`GET /api/learner/history`).
  - `/api/config` carries `speaking_prompts`. 104 tests, all offline.
- **2026-09-19 — Stage 2: free drills ($0).** New «Тренировка» tab (`#/practice`) and a
  topic page (`#/practice/<topic>`); the old stub cards are gone.
  - **Cards** from the daily queue or from one topic. Format chosen by code
    (`learner_model.card_exercise`): a fix is a **scramble** of the correction while new /
    in box 1, **typed recall** after that (accepts correction + better_versions); a phrase
    is a **gap** in its own example when it literally occurs there, otherwise a
    self-graded flashcard; patterns and anything longer than 16 words are self-graded.
    Checking is in the browser (case/punctuation-insensitive), with a word diff and a
    «Засчитать как верный» override (still one attempt in the log).
  - **Cloze on `improved_version`** for articles and prepositions (≤ 15 gaps, "to" left
    out). English/mixed recordings only. Logged as a **topic attempt** (`topic` + `score`,
    no `item_id`); `correct` is derived server-side (score ≥ 0.8) and the score feeds
    topic accuracy.
  - Deep links: Cambridge Grammar per topic; YouGlish / Cambridge Dictionary / SkELL on
    English phrase cards. No textbook references (user decision).
  - API: `GET /api/learner/texts[?topic]`; `POST /api/learner/attempts` accepts topic
    drills; items and queue carry `exercise`; `/api/topics` carries `resources` and
    `has_cloze`. 96 tests, all offline.
- **2026-09-19 — Stage 1: learner model foundation (backend only, zero tokens).**
  - Item bank `data/item_bank.json` (derived): every analysis yields `fix` (quote →
    correction), `pattern` (the rule behind a fix) and `phrase` (vocabulary + takeaways)
    items. Ids are content hashes, so the same pattern/phrase in a later recording merges
    into one item with a second occurrence. Fixes on filler/repetition topics are not
    cards (delivery, not recall; left to spoken drills).
  - Attempts log `data/attempts.jsonl` (append-only, authoritative; includes `answer`).
  - Leitner state derived on read (boxes 1–5, 1/2/4/8/16 days): correct on/after the due
    date → next box; early correct → no change (no cramming); wrong → box 1; the item
    appearing in a recording made *after* it was first practised → box 1; closed after
    box 5 + 3 later analysed recordings in its language without it.
  - Topic mastery: `priority = speech weakness × (1.5 − accuracy of the last 20 attempts)`.
  - Score history in `progress.json` (schema v2), keyed by session. Re-analysis
    **replaces** a session's scores, counts and items (decided 2026-09-19): caches are
    fully rebuilt after every analysis. Topic dates are now the recording date.
  - Usage log `data/usage.jsonl`: tokens + estimated cost per Claude call, audio seconds
    + cost per Deepgram call; the analysis cost is also stored in `analysis.json`.
  - API: `GET /api/learner/items[?due_only&topic&kind]`, `POST /api/learner/attempts`,
    `GET /api/learner/topics`, `GET /api/usage`; `/api/progress` gains `score_history`.
  - JSON writes are atomic. Tests: `test_learner_model.py`, `test_learner_store.py`, more
    API tests (75 total, all offline).
  - First real data point: one recording → 17 items (6 fix, 5 pattern, 6 phrase).
- **2026-09-19 — Daily card queue** (`GET /api/learner/queue`). Decided with the user:
  **7–10 new cards a day**. All due reviews always; new cards: 10/day, dropping to 7 once
  more than 30 reviews are due; cards started today count against the allowance. New cards
  come from the highest-priority topics first (major mistakes first), 2 mistake cards : 1
  phrase. Unshown new cards wait for later days. Stage 2 drills and «Сегодня» take cards
  from this queue.

- **2026-09-18 — Coach-style feedback (analysis.json schema v2).** Claude feedback is
  now shaped like a speaking coach's review instead of a bare error list:
  - per mistake: verbatim quote ❌, Russian explanation, minimal correction ✅,
    0–2 simpler / more natural `better_versions`, and an optional reusable
    `pattern` (e.g. "help someone + verb") with examples;
  - `strengths` (what went well), `vocabulary` (words the speaker was searching
    for or confused, e.g. employee / employer / recruiter);
  - `improved_version` — the whole monologue retold naturally, slightly above the
    current level (a separate field; the transcript itself is never modified);
  - `takeaways` — 3–5 constructions to remember;
  - `scores` — grammar / vocabulary / fluency / naturalness, 1–10 each with a
    comment, plus a computed `overall_score` (mean, rounded to 0.5).
  - Old v1 analyses still render (only summary + issues).

## Stage 5 — AI exercise sets (~2–4 ¢)

Done 2026-09-19 — see «Done» above.

## Stage 6 — Spoken drills (Deepgram only)

Done 2026-09-20 — see «Done» above.

## Later

- [ ] **Listening dictation from YouTube (ear2finger-style).** Paste a link, get the audio
      split into segments, type a dictation of each one, check it. Explicitly deferred
      by the user. Open questions: fetching YouTube audio or captions needs a new
      dependency (e.g. yt-dlp) beyond the pinned set; the reference text could come from
      captions or from a Deepgram transcript.
- [ ] Score consistency: optionally give Claude the previous scores as context so the
      scale stays stable across sessions.
- [ ] PyInstaller packaging (deferred — a persistent server doesn't fit onefile).
