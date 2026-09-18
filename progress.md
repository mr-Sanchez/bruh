# Plan & progress

Working plan for the Voice Practice Coach. Keep items short; move them to "Done"
with a date when they ship.

## Done

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

## Next: progress tracking (Progress tab)

Goal: see over time which mistakes actually disappear and how the scores move —
"recording #3 → #10 → #20".

- [ ] **Score history.** Per-skill scores are already stored in every v2
      `analysis.json`; aggregate them into `data/progress.json` (bump
      `progress_store.SCHEMA_VERSION`, extend `rebuild_from_sessions()`), keyed by
      session id + date. v1 analyses have no scores — skip them.
- [ ] **Progress tab UI.** A per-skill trend (grammar / vocabulary / fluency /
      naturalness / overall) across recordings, e.g. a small line chart or
      sparklines, plus the latest vs. first vs. rolling average.
- [ ] **Recurring vs. fixed mistakes.** Per topic: show when it was last seen and
      whether it is trending down (the recency-decayed weakness score exists;
      surface the trend, not just the rank).
- [ ] **Takeaways review.** Collect `takeaways` and `vocabulary` from all sessions
      into one "phrases to remember" list; later feeds the Practice tab.
- [ ] **Score consistency.** Scores come from the model per recording; consider
      giving Claude the previous overall/skill scores as context so the scale
      stays stable across sessions (keep it optional and cached-analysis-safe).
- [ ] Decide whether re-analysing a session (`force=true`) should replace or
      keep its earlier scores in the history.

## Later

- [ ] Practice tab (`#/practice/<topic>`): quizzes, translation and spoken drills
      built from recurring topics and saved takeaways.
- [ ] PyInstaller packaging (deferred — a persistent server doesn't fit onefile).
