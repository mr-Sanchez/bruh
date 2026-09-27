# Plan & progress

Working plan for the English Coach. Keep items short; move them to "Done"
with a date when they ship.

## Direction (decided 2026-09-19)

The app is growing step by step into a language-learning **platform**. Recording and
analysing a monologue is no longer the centre of the app; it is one *activity* among
several (monologue, picture description, topic drills, spoken drills, dictation). Every activity
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
картинки, Диктант, Тренажёры по темам) · `История` (all activities) · `Прогресс`
(score charts, topic mastery, mistake bank).

## Done

- **2026-09-27 — «Монолог» and «60 секунд» merged into «Говорение».** Asked for by the
  user (the two barely differed). One activity (`#/speaking`, speech.js) with three switches,
  remembered in the browser: topic «своя» / «предложенная» (the same prompt and «уклон»
  picker), time «без лимита» / 1 / 2 / 3 min (the recorder stops itself), takes «один» /
  «серия ×3». Every take is a `kind=monologue` session; its `drill` holds the prompt (None for
  an own topic), `time_limit`, `series`, `round` and `silences`; later rounds copy the first
  take's prompt and limit. Every take shows pace / fillers / pauses and has its own
  «Анализировать» button — the latest (smoothest) one is highlighted (decided with the
  user); the series' takes are compared in a table. **The first take of any English
  monologue** (a lesson task too, not a picture) now logs the `filler_words_fluency`
  attempt (`exercise=monologue`), not only a timed one (decided with the user); later
  rounds are practice. `kind=talk` is no longer accepted; older talk takes stay readable
  (session page, «Прогресс» series, not analysable). The «Сегодня» warm-up is done by a
  second round of a series (or shadowing); `#/record` and `#/talk` still open the view
  (`#/talk` presets 1 min ×3). A plain monologue's session page now shows its measurements.
  `record.js` keeps only the picture description and the lesson task.

- **2026-09-27 — «Спросить ИИ»: a chat about any selected text.** Asked for by the user (a
  review said «нужно прошедшее время» where the English has present simple, and there was no
  way to ask why). Selecting text anywhere in the app shows a «Спросить ИИ» bubble; the panel
  on the right opens with the selection, the card around it (its visible text: the quote,
  «Вы написали», the correction, the mistake kind) and the screen title, which all go to
  Claude with the question. Then an ordinary chat. Sonnet 5 low, plain text with light
  Markdown, always Russian (≈ 0.4 ¢ for the first answer; each answer resends the chat).
  Every chat is kept in `data/assistant/` (decided with the user: «храним историю»), listed
  under «История» in the panel; a question is saved before Claude is asked. Usage kind
  `assistant`. «Ассистент» at the bottom of the sidebar opens and closes the panel (the
  open chat, or the history) without selecting anything.

- **2026-09-27 — «Перевод текста» (EN → RU).** Asked for by the user: an English text is
  either written by Claude (Sonnet 5 low, ≈ 1 ¢) in the chosen «уклон» at a size — short /
  medium / long ≈ 5 / 10 / 15 minutes of translating (80–110 / 160–200 / 240–290 words) — and
  level A2–C1, or pasted by the learner (free). The text sits beside the translation field;
  the translation is typed or dictated in Russian (`/api/learner/dictate` with `language=ru`,
  up to 120 s per clip, appended; usage `text_dictation`). «Проверить» → one Sonnet call
  (≈ 3 ¢): accuracy 0–100 + summary, mistakes by kind (grammar, meaning, omission, addition,
  word choice, spelling) with the fix, «звучит неестественно» with a native alternative, a
  final translation that keeps the learner's good wording, and 8 useful phrases of the text
  that the learner ticks into RU → EN word cards (same pick log and card kind as a set's
  vocabulary). Files in `data/translate/` (paid, append-only attempts); the same text is never
  reviewed twice; a failed call keeps the translation. Not in the learner model, the streak or
  «Сегодня» («больше ничего» — the user). The direction is stored (`en-ru`) so RU → EN can be
  added later. **Still to check:** text and review quality and the real cost on the API.

- **2026-09-27 — A translation card's answer can be dictated.** «Надиктовать» under the
  answer field records up to 60 s (the shared `Recorder`), `POST /api/learner/dictate` sends
  the clip to Deepgram (English profile with `filler_words` off — it is an answer, not a
  fluency take) and the text is added to the field; the learner can fix a misheard word and
  then «Проверить» as usual. The clip is not kept (not a recording, never in «История»);
  Deepgram time is logged as usage `card_dictation` (≈ 0.05 ¢ per answer). Decided by me.

- **2026-09-27 — Word cards picked from a set's vocabulary.** Decided with the user: the
  set's own call also writes `SET_VOCABULARY` (8) useful words / phrases for the topic and
  context (English, Russian, an example + its translation, an optional Russian note;
  ≈ +0.2 ¢, no extra click), stored as the set's `vocabulary`. After every run the results
  screen lists them with checkboxes; «Сохранить в карточки» saves the whole choice
  (`PUT /api/practice/sets/<id>/vocabulary`, $0) into `data/word_picks.jsonl` (append-only,
  authoritative, newest record per set + word wins, so unticking takes the card away).
  Each pick is a bank item of kind `word` (bank v6): Russian front, recall, «Показать
  ответ», Anki-style self-grade «Помню / Не помню»; the back has the English, the example
  with its translation, the note and YouGlish / Cambridge / SkELL. Same Leitner boxes, but a
  word is never reset by speech and closes after a correct review in box 5. **Own daily
  allowance** of new words (`NEW_WORDS_PER_DAY` = 5), apart from the 7–10 mistake cards.
  Words carry no topic (they do not move topic accuracy), only `lesson_id`. A new set's
  request lists the learner's word cards so its vocabulary suggests others. Sets written
  before have no vocabulary. **Still to check:** the vocabulary's quality on the real API.

- **2026-09-27 — Translation review and lesson tasks move to Sonnet.** Decided with the
  user: the dictation's translation review is the same kind of judgement as card grading, so
  it goes to Sonnet 5 (`effort: low`, ≈ 2 ¢ per part instead of ≈ 0.5 ¢); the split into
  parts stays on Haiku (`TRANSLATION_SPLIT_MODEL` / `TRANSLATION_REVIEW_MODEL`). A lesson's
  spoken tasks too (≈ 2 ¢ per click, rare and kept): a task the rule can be dodged in makes
  the rule score meaningless. Own-context speaking prompts stay on Haiku.

- **2026-09-26 — Translations graded by Sonnet; a mistake is filed under its own topic.**
  Haiku failed a right answer («before a call» for «before the call»), named the wrong
  mistake («missing article before people» for «it was» → «there were») and nitpicked
  synonyms — and its comment becomes the card's explanation. Decided with the user: AI-set
  and card checks move to Sonnet 5 (`effort: low`, ≈ 1 ¢ per set instead of ≈ 0.5 ¢), with a
  stricter prompt (acceptable variants are not mistakes, name the real one). Each verdict
  also carries `topic` from the closed taxonomy, and a wrong translation's card goes to that
  topic, not the set's: an «it was / there were» slip in an articles set trains «Структура
  предложения». Runs graded earlier keep the set's topic. No new topics (no taxonomy change);
  the set's own score still counts for the set's topic. Dictation translation stays on Haiku.

- **2026-09-26 — Rule cards switched off.** The «Правило» card («придумайте свой пример»,
  then compare with the examples) never said whether the learner's own example was right,
  and it drilled the same rule as the mistake card built from the same issue. Decided with
  the user: rule items are retired (`learner_model.is_retired`) — kept in the bank as
  history and as AI-set seeds, never queued or listed. The mistake card now shows the rule
  with its examples after the answer (`content.focus_examples`). Item bank schema v4.
  Same fate for **grammar notes among phrases** — a phrase that cannot be blanked out of
  its example and is a formula (`+`, `/`) or a takeaway only («If + Present Simple, will +
  verb», «parallel structure in lists»): «вспомните фразу» from a paraphrased rule is not a
  task. A plain word with no gap («windowsill») is still recalled from its meaning.

- **2026-09-26 — Mistake cards rebuilt: new sentences, checked by Claude.** The old cards
  showed the verbatim quote (a scramble / typed correction, or a self-graded long fragment):
  a week later a short quote had no context left, and a long one hid which problem was meant.
  Decided with the user:
  - Every English mistake in an analysis now carries 3 **new practice sentences**
    (`issues[].drills`, analysis schema v4): a Russian sentence in a different situation whose
    natural English needs exactly the construction that went wrong. Written in the same
    analysis call (no extra call, a bit more output). A card is **«Скажите по-английски»**;
    each attempt moves to the next sentence, and the pattern rule is a hidden «Подсказка».
  - The answer is checked by **Claude (Haiku 4.5; Sonnet 5 since the same day, see above)** — `POST /api/learner/cards/<id>/check`,
    usage purpose `card_grading`, a fraction of a cent. An exact match of the reference or an
    empty answer costs nothing; verdicts are cached in `data/card_verdicts.jsonl` by sentence +
    answer. The browser still logs the attempt, so «Засчитать как верный» overrules Claude;
    if the check fails (no key, network) the learner grades against the reference.
  - Only after answering, the card shows where it came from: «Из вашей записи от …» with the
    quote ❌, the correction ✅, the explanation and a link to the recording.
  - **Old mistake cards are switched off** (the user's choice): a fix without drills — every
    analysis before today, Russian mistakes, gap/fix mistakes of AI sets — stays in the bank as
    history (its attempts still count for topic accuracy) but never reaches a queue or a list.
    A wrong **translation** in an AI set becomes a live card with the set's own Russian sentence.
    Item bank schema v3.
  - **Cloze on `improved_version` removed** (from «Сегодня», «Занятия» and the topic page,
    `GET /api/learner/texts`, `has_cloze`): gaps in a text you remember test memory of that
    text, not the rule. Old cloze attempts stay in the log and in «История».
  - Scramble and typed-recall card formats are gone; phrase gaps and pattern/phrase
    self-graded cards are unchanged. 228 tests, all offline.
  - **Still to check:** the quality of generated sentences and Haiku's verdicts on real
    answers; the real extra cost of drills in the analysis. Re-analysing an old recording
    (`force`) gives it cards again, at the price of an analysis.

- **2026-09-20 — Stage 7: listening dictation from YouTube ($0).** «Диктант» (`#/dictation`)
  under «Занятия»: paste a YouTube link, and the video becomes a lesson — its audio plus the
  sentences of its **own subtitle track**. Modelled on ear2finger (researched with the user
  on 2026-09-20), rebuilt on this app's own architecture.
  - **Import** (`app/youtube.py`, yt-dlp, lazily imported): one metadata pass, then audio +
    captions. Decided with the user: **subtitles or nothing** — manual captions first,
    automatic ones otherwise, and a video with neither is refused instead of being sent to
    Deepgram. Automatic captions count only in the video's own language (YouTube's machine
    translations do not match the audio). Videos longer than 20 minutes are refused. No
    transcoding, so **FFmpeg is not needed**; `yt-dlp` is the 7th pinned dependency and its
    absence degrades gracefully (a clear message, the rest of the app unaffected).
  - **Sentences** (`app/dictation.py`, pure): the caption cues are parsed, the rolling
    repetition of automatic captions is removed, and words are glued into 4–18-word
    sentences at sentence punctuation (or, failing that, at a comma), each with the play
    window it needs (± 0.25 s of air).
  - **Workspace**: one sentence at a time, **one input per word** (punctuation is shown, not
    typed), with ear2finger's keys — Enter listens again, Space jumps to the next word, Tab
    reveals the current word, Backspace in an empty field steps back, `[`/`]` move between
    sentences. Speed 0.5–2×, repeat 0/1/3/5/10/∞ (∞ = until it is right), pause 0/3/5/10 s
    between sentences. Checking ignores case and punctuation, live per keystroke; mistyped
    characters are counted the way they are made ("ошибкой считать как у них" — the user).
  - **Results** are logged per sentence to `data/dictation/<id>/results.jsonl` (append-only,
    authoritative) and **graded again on the server**, so the stored numbers never depend on
    the browser. A half-typed sentence that is left behind is logged too — those are the
    words worth showing later.
  - **Learner model**: dictation stays out of `attempts.jsonl` and the item bank (decided
    2026-09-20 — mishearing a word is not one of the taxonomy's speaking mistakes). It gets
    its own «Сложные слова» list on «Прогресс» (missed or hinted ≥ 2 times, with YouGlish /
    Cambridge links), a line per day in «История», and it keeps the day streak alive.
  - **«Сегодня»**: a **mandatory** step (the user's choice), done at 5 sentences a day,
    ≈ 4 minutes; with no lesson imported yet it shows as "empty" and invites an import
    instead of blocking the workout. 202 tests, all offline (a fake yt-dlp, no network).
  - **Still to check:** the sentence length (4–18 words) and the daily 5 on real videos, and
    how readable automatic captions are as a reference — both are first guesses.

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
    go to Haiku 4.5 (Sonnet 5 since 2026-09-26) in one call when the set is handed in; an exact match of the reference
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

- **2026-09-26 — Dictation translation task (Haiku, ≈ 0.5 ¢ per call).** Under a dictation
  lesson: translate what you heard, part by part, and get a review. Decided with the user:
  the cut into parts is a **model call** (Haiku returns only the sentence indices where a new
  part starts; `dictation.plan_parts` repairs them into 5–15-sentence parts; a lesson that
  fits one part costs nothing), the direction is English → Russian (Russian video → English),
  and mistakes are **not** sent anywhere — no item bank, no attempts — they stay in the lesson.
  - One review call per part: the source sentences + the learner's translation in, a quality
    label, a Russian summary, issues (source / your words / problem / better) and a model
    translation out. No reference translation is generated first. The text is saved before the
    call; the same text again reuses the stored review.
  - Files: `parts.json`, `translations.jsonl` in the lesson folder. API: `POST
    /api/dictation/lessons/<id>/parts`, `POST .../parts/<n>/translation`. UI: `js/translation.js`.

## Stage 7 — Listening dictation from YouTube

Done 2026-09-20 — see «Done» above.

## Stage 8 — Learning roadmap (planned and built 2026-09-26)

All parts R1–R8 are done. **Still to check on the real API:** theory, lesson sets that
follow it, spoken tasks and their rule check, module tests — quality and real prices (none
of them has been run against Claude yet).

A fixed A2 → C1 course of lessons; the model writes only a lesson's *content*, on a click.
Decided with the user on 2026-09-26:

- **The roadmap itself is fixed** (authored once, in code): levels → modules → lessons, with
  stable ids, so progress has something to attach to. Everything the learner needs: grammar,
  vocabulary (phrasal verbs, collocations, word formation), functional English (meetings,
  disagreeing politely, small talk, e-mails…).
- **Free movement.** Every lesson is open. «Продолжить» goes to the first lesson not yet
  mastered; «Пропустить» / «Уже знаю» are marks, never locks. A lesson has a status: не начат /
  теория / практикуется / освоен / пропущен / уже знаю. **Mastered = 2 set runs ≥ 80 % on
  different days** (first guess, may change).
- **Taxonomy v2, rebuilt cleanly** (the user: «делать нужно чисто и хорошо»): two levels,
  **area** (≈ 12–15) → **topic** (≈ 60–80, what a roadmap lesson teaches). Analysis tags a
  mistake with the fine topic; stats, priorities and «Прогресс» show both levels. No real data
  exists yet (no recordings, attempts or sets on 2026-09-26), so no migration of old files.
- **Theory** — a click, Claude, cached; neutral examples plus the learner's own mistakes on the
  topic; «Сгенерировать заново» keeps the old version.
- **Exercise sets per lesson** — the Stage 5 machinery with the lesson and its theory as
  context; any number of sets, old ones redone for free, new ones never repeat old sentences.
  Runs are topic attempts (with `lesson_id`), wrong answers become cards — the same learner
  model as every other activity.
- **Context («уклон»)** — chosen before *every* generation, everywhere something is generated
  (lesson sets, topic sets, analysis drills, monologue / «60 секунд» prompts). Default = the
  last one used, first IT / backend (the user is a backend developer). Starting list: IT /
  бэкенд, созвоны и митинги, собеседование, путешествия, покупки, ресторан, жильё, здоровье,
  small talk, хобби и спорт, новости, «Вперемешку». The learner adds own contexts in the UI
  (saved) or types a one-off. The context is stored with the set and shown in its history.
  Theory does **not** follow the context.
- **Spoken task per lesson** — 1–2 min of speech using the lesson's rule, analysed by Claude
  with that rule in focus; mistakes go to the learner model.
- **Module entry test** — a short test to mark a whole module «уже знаю» honestly; missed
  questions point at their lessons.

Parts, in order:

- [x] **R1 — Curriculum + taxonomy v2** (2026-09-26). `app/curriculum.py`: 15 areas → 76
      topics; 4 levels (A2 20 lessons, B1 28, B2 18, C1 7) → 16 modules → 73 lessons. A lesson
      teaches exactly one topic and its id is the topic key; only delivery (fillers,
      restarts) and «other» have no lesson. Checked at import (unique keys, known areas and
      levels, one lesson per topic). The analysis and grading prompts list the topics under
      area headings (≈ 3k more input tokens, ≈ 0.6 ¢ per call); analysis schema v5,
      progress.json v3, item bank v5. `GET /api/curriculum`; `/api/topics` and
      `/api/learner/topics` carry the area and level, the latter also `areas` (topic
      mastery rolled up: sums, accuracy weighted by answers). «Занятия» groups topics by
      area, «Прогресс» shows area rows with their topics. Cambridge links kept only where
      they were already verified; the rest wait for R4's theory.
- [x] **R2 — Roadmap page ($0)** (2026-09-26). `#/roadmap` («Курс A2 → C1», first tile under
      «Занятия»): levels (collapsible, the current one open) → modules → lessons with a status
      pill, set runs / best score and the number of the learner's own speech mistakes on
      the topic. Statuses are derived on read (`app/roadmap.py`, pure): **mastered** = 2 set
      runs ≥ 80 % on different days, whatever the mark; a **mark** («Уже знаю» / «Пропустить»,
      «Снять отметку») stands until the lesson is worked on again after it (a later set run
      or theory), so «уже знаю» + a 50 % run shows «практикуется»; then practising (any set
      run) / theory (hook for R4) / not started. Marks live in `data/roadmap_marks.jsonl`
      (append-only, authoritative, the newest wins). «Продолжить» = the first lesson in
      course order that is not mastered / known / skipped. Until R4 a lesson opens its topic
      page (cards + AI sets), which links back to the course. `GET /api/roadmap`,
      `POST /api/roadmap/lessons/<id>/mark`. 259 tests.
- [x] **R3 — Contexts «уклон»** (2026-09-26). In code a *theme* ({key, label}; «context» already
      names the screen of an attempt). `app/themes.py`: 12 built-in themes (IT / бэкенд first,
      the default), each with a model-facing description and **8 hand-written speaking prompts**
      ($0; «Вперемешку» pools them all; the 20 old prompts were spread over them). Own themes,
      the last one used and Claude-written prompts live in `data/themes.json`
      (`app/theme_store.py`, authoritative). Decided with the user: prompts for an **own** theme
      are written by **Haiku on a click** («Придумать темы», ≈ 0.1 ¢, usage purpose
      `theme_prompts`, kept; «Придумать заново» replaces them). One picker (`js/themes.js`)
      everywhere: topic-page and «Сегодня» AI sets, «Анализировать» (the analysis `drills`;
      hidden for Russian takes), the monologue and «60 секунд» prompts. It offers the built-in
      themes, own ones (add / delete), and — for generations only — a one-off typed line.
      Default = the last theme used by any generation, take or prompt picker. The theme is
      stored with a set (`theme`, shown in its history), in `analysis.json` (`theme`), and in a
      talk take's `drill` (prompt id, question, hint, theme — the prompt as shown). Prompt ids
      are `<theme>:<n>` (`#/record/<id>`, `#/talk/<id>`) instead of indexes; the day's prompt
      comes from the last-used theme, or «Вперемешку» when that theme has no prompts. API:
      `GET/POST /api/themes`, `DELETE /api/themes/<key>`, `PUT /api/themes/last`,
      `GET/POST /api/themes/<key>/prompts`; `theme` on set creation and `/analyze`;
      `/api/config` lost `speaking_prompts`, `/api/speech/talks` returns `prompt`. Theory
      (R4) will not follow the theme. 269 tests.
- [x] **R4 — Theory** (2026-09-26, paid, kept). On a lesson's page (for now the topic page
      `#/practice/<lesson>`, which also holds its cards and AI sets) a «Теория» card: «Написать
      теорию · ≈ N ¢» → one Sonnet 5 call (`effort: low`, `app/theory.py`, usage purpose
      `theory`; the price on the button is the real average once there is one, ≈ 3 ¢ until
      then). Structured: a two-sentence summary, 2–5 sections with neutral EN examples + RU
      translation, 3–5 typical mistakes of Russian speakers, a comment on each of the learner's
      **own** recorded mistakes on the topic (up to 6 fix items from speech, as the request
      shows them), and «Запомнить». Written for the lesson's level (A2…C1); the context «уклон»
      is not used. Every version is kept in `data/theory/<lesson>.json`; «Сгенерировать заново»
      adds one, the «Версия» select reopens older ones. The latest version's date feeds the
      roadmap status «теория». API: `GET /api/lessons/<id>/theory[?version=n]`,
      `POST /api/lessons/<id>/theory`. 275 tests. **Still to check:** the quality and real price
      on the real API (not run yet).
- [x] **R5 — Lesson exercise sets** (2026-09-26). Every set-able topic is a roadmap lesson, so
      the topic page is the lesson page: status pill + «Урок курса · level · module», what
      «освоен» still needs («есть 1 из 2» days at ≥ 80 %), runs and best score, and the
      «Уже знаю» / «Пропустить» / «Снять отметку» marks (`GET /api/roadmap/lessons/<id>`);
      then «Теория», then «Упражнения урока», then cards. A lesson's set gets the lesson's
      **level and latest theory** in the request (summary, sections with 2 examples each,
      «Запомнить»; «do not copy the theory's examples»), and stores `lesson_id` +
      `theory_version` (shown as «по теории» in the history). Its runs log `lesson_id` in
      `attempts.jsonl`. **No repeats:** the request lists up to 90 earlier sentences of the
      topic (was 16), and after generation any exercise whose sentence an earlier set had (or
      one repeated within the set; case/punctuation aside) is dropped
      (`exercise_sets.drop_repeats`); a set left empty answers 502, the call still logged. The
      set history lists every set (5 at first, «Показать все»); redo stays free. 280 tests.
- [x] **R6 — Spoken task** of a lesson (2026-09-26). Decided by me (code-level, same pattern
      the user chose for own-context prompts): tasks are written by **Haiku on a click**
      («Придумать задания» / «Ещё задания», ≈ 0.1 ¢, usage purpose `lesson_tasks`), 3 per click
      in the chosen context «уклон», each built so the answer cannot avoid the lesson's
      construction: English `question`, Russian `hint`, and `use` («Past Perfect для того, что
      случилось раньше: I had already…»). Kept, append-only, in `data/lesson_tasks/<lesson>.json`
      (ids t1, t2…); the lesson page lists the chosen context's tasks. «Записать» opens
      `#/speak/<lesson>:<task>` — the ordinary monologue recorder with the task on top; the
      take is a normal monologue (`session.json` gains `lesson`: id + the task as shown), so
      cards, «Сегодня»'s live step and «История» work unchanged. **Analysis** with the lesson in
      focus (`LessonAnalysis`, the lesson's rule and task in the request) adds `lesson_check`:
      1–10 score, Russian verdict, correct uses (verbatim) and missed places (quote → better);
      stored as `analysis.json` `lesson`, shown as «Правило урока». Mistakes go to the learner
      model as usual. The rule score is logged **once per take** as a topic attempt
      (`exercise: "lesson_task"`, score (n−1)/9, `lesson_id`; a forced re-analysis adds none).
      It makes the lesson «практикуется» and shows on the lesson page (count, best x/10), but
      **«освоен» still counts set runs only**. 286 tests. **Still to check:** the tasks' and
      the check's quality on the real API.
- [x] **R7 — Module entry test** (2026-09-26). «тест модуля» next to every module on «Курс» →
      `#/moduletest/<module>`. Claude (Sonnet 5 low, usage purpose `module_test`, ≈ 3 ¢ until
      the log has a real average) writes on a click **one multiple-choice question (4 options,
      wrong ones = typical Russian-speaker mistakes) and one gap per lesson**, each tagged with
      its lesson; `app/module_test.py` keeps only well-formed ones and refuses a test that
      misses a lesson (502, call still logged). No context «уклон» (a check of knowledge,
      like theory). The page gets the questions **without answers**; the server grades
      (choice = option index, gap case/punctuation-insensitive) — no model call, so a retake
      is free. A lesson is known when both its answers are right. Results: per lesson «знаете»
      or «К уроку», every answer with the right one and a Russian explanation, «Отметить «уже
      знаю»: N уроков» (plain roadmap marks, only on that click, only lessons not already
      known/mastered), «Пройти ещё раз», «Новый тест» (avoids every earlier test's sentences;
      older tests stay selectable). Each run is kept in `data/module_tests/<module>.json` and
      logs one `module_test` attempt per lesson (share right, `lesson_id`) — it feeds topic
      accuracy but not the lesson's status. API: `GET/POST /api/modules/<key>/test`,
      `POST .../test/<id>/submit`, `POST /api/modules/<key>/mark-known`. 292 tests.
- [x] **R8 — Integration** (2026-09-26). Decided with the user: **«Урок дня» replaces the
      AI-set step** on «Сегодня» (optional, like the set was: its actions are paid). The lesson
      is the one the learner's own mistakes ask for most (`roadmap.recommend`: topic priority
      from `learner_model.topic_mastery` > 0, not mastered — a «уже знаю» / «пропущен» mark
      does not hide it, the recordings say otherwise), else the roadmap's «Продолжить». The
      step says why («по вашим ошибкам в речи (N)» / «следующий урок курса») and the next action
      (`roadmap.next_action`: theory → set → spoken task → sets until mastered), with its
      price; a set is started (a waiting one, free) or written (with the context picker) right
      there, theory / spoken task open the lesson page. Done once any lesson's set or spoken
      task was finished today. «Курс» shows «По вашим ошибкам» (top 3, `GET /api/roadmap`
      gains `recommended`); «Прогресс» opens with a «Курс» card (per level: mastered + known of
      all, in work, a bar; the lessons in work with runs, passing days, spoken tasks); every
      mistake in an analysis links to its topic / lesson page. 294 tests.

## Later

- [ ] Dictation follow-ups, once there is real usage: a lesson's tricky words could become
      listening cards (replaying their own sentence), and a lesson could be limited to a
      chosen time range instead of the whole video.
- [ ] Score consistency: optionally give Claude the previous scores as context so the
      scale stays stable across sessions.
- [ ] PyInstaller packaging (deferred — a persistent server doesn't fit onefile).
