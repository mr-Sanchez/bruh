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

## Stage 3 — «Сегодня» screen (variant B) + new navigation

- [ ] ~10-minute workout assembled **by code, no LLM**: due Leitner items first, then a
      drill for the weakest topic, then one "live" activity (monologue / picture).
- [ ] Mini score trends (sparklines) on the home screen; full charts on `Прогресс`.
- [ ] New tab structure (see Direction).

## Stage 4 — Picture description

- [ ] The user uploads an image and describes it **by voice** (default) or by text.
- [ ] Stored as a regular session in `recordings/` with the image and
      `kind: "picture"` in `session.json` (new filename via `config.py` + a `Session`
      property). Reuses recording, Deepgram, history and analysis; all invariants hold
      (audio never deleted, transcript never modified).
- [ ] Claude gets image + transcript and returns the usual analysis plus "what you did not
      mention" and "useful words for this scene". Issues feed the same topics and the
      item bank.
- [ ] Downscale the image in the browser (~1000 px long side) to keep tokens low.
      Target ~5–8 ¢ per description; verify with the usage log.

## Stage 5 — AI exercise sets (~2–4 ¢)

- [ ] Generate 8–10 new items on the user's own `pattern` / topic (IT/work context).
- [ ] Grade free-form answers (Haiku 4.5 or Sonnet 5 at low effort).
- [ ] Cached under `data/practice/…`; explicit button; cost shown in the UI.

## Stage 6 — Spoken drills (Deepgram only)

- [ ] 60-second talk: fillers per minute and words per minute, counted locally from the
      verbatim transcript (`filler_words` is English-only).
- [ ] Shadowing: read `improved_version` aloud, word-level diff against it in the browser.

## Later

- [ ] **Listening dictation from YouTube (ear2finger-style).** Paste a link, get the audio
      split into segments, type a dictation of each one, check it. Explicitly deferred
      by the user. Open questions: fetching YouTube audio or captions needs a new
      dependency (e.g. yt-dlp) beyond the pinned set; the reference text could come from
      captions or from a Deepgram transcript.
- [ ] Score consistency: optionally give Claude the previous scores as context so the
      scale stays stable across sessions.
- [ ] PyInstaller packaging (deferred — a persistent server doesn't fit onefile).
