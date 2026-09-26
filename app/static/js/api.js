// Thin fetch wrapper around the /api/* endpoints in app/api.py.
const Api = (() => {
  async function request(path, options = {}) {
    const response = await fetch(path, options);
    let body = null;
    try {
      body = await response.json();
    } catch (err) {
      body = null;
    }
    if (!response.ok) {
      const detail = (body && body.detail) || response.statusText || "Request failed";
      throw new Error(detail);
    }
    return body;
  }

  return {
    getConfig: () => request("/api/config"),
    listSessions: () => request("/api/sessions"),
    getSession: (id) => request(`/api/sessions/${encodeURIComponent(id)}`),
    getProgress: () => request("/api/progress"),
    getTopics: () => request("/api/topics"),
    getCurriculum: () => request("/api/curriculum"),
    // A lesson's theory (latest version unless `version`); writing one is paid.
    getTheory: (lessonId, version = null) =>
      request(
        `/api/lessons/${encodeURIComponent(lessonId)}/theory${version == null ? "" : `?version=${version}`}`
      ),
    writeTheory: (lessonId) =>
      request(`/api/lessons/${encodeURIComponent(lessonId)}/theory`, { method: "POST" }),
    // A module's entry test: writing one is paid; answers are graded on the server.
    getModuleTest: (moduleKey, testId = null) =>
      request(
        `/api/modules/${encodeURIComponent(moduleKey)}/test${testId ? `?test=${encodeURIComponent(testId)}` : ""}`
      ),
    createModuleTest: (moduleKey) =>
      request(`/api/modules/${encodeURIComponent(moduleKey)}/test`, { method: "POST" }),
    submitModuleTest: (moduleKey, testId, answers) =>
      request(
        `/api/modules/${encodeURIComponent(moduleKey)}/test/${encodeURIComponent(testId)}/submit`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ answers }),
        }
      ),
    markModuleKnown: (moduleKey, lessons) =>
      request(`/api/modules/${encodeURIComponent(moduleKey)}/mark-known`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lessons }),
      }),
    // A lesson's spoken tasks; writing more is a paid Haiku click in a context.
    getLessonTasks: (lessonId) => request(`/api/lessons/${encodeURIComponent(lessonId)}/tasks`),
    writeLessonTasks: (lessonId, theme) =>
      request(`/api/lessons/${encodeURIComponent(lessonId)}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme }),
      }),
    // The roadmap with lesson statuses; a mark is "skipped", "known" or null (clear).
    getRoadmap: () => request("/api/roadmap"),
    getRoadmapLesson: (lessonId) => request(`/api/roadmap/lessons/${encodeURIComponent(lessonId)}`),
    markLesson: (lessonId, mark) =>
      request(`/api/roadmap/lessons/${encodeURIComponent(lessonId)}/mark`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mark }),
      }),

    // `kind` is "monologue", "picture", "talk" or "shadowing"; a picture take
    // also sends `image`, a drill take what it practises (`drill`: talk -
    // prompt_id and, from round 2, series; shadowing - source_session_id
    // and passage).
    // A lesson's spoken task also sends `lesson` ({lesson_id, task_id}).
    uploadSession: (blob, { language, durationSeconds, mimeType, kind, image, drill, lesson }) => {
      const form = new FormData();
      const extension = (mimeType || "").includes("ogg") ? "webm" : "webm";
      form.append("file", blob, `audio.${extension}`);
      form.append("language", language);
      form.append("client_duration_seconds", String(durationSeconds));
      form.append("mime_type", mimeType || blob.type || "");
      if (kind) form.append("kind", kind);
      if (image) form.append("image", image, "image.jpg");
      Object.entries({ ...(drill || {}), ...(lesson || {}) }).forEach(([key, value]) => {
        if (value != null) form.append(key, String(value));
      });
      return request("/api/sessions", { method: "POST", body: form });
    },
    // A typed take: stored verbatim as the transcript, no speech recognition.
    submitText: ({ text, language, kind, image }) => {
      const form = new FormData();
      form.append("text", text);
      form.append("language", language);
      if (kind) form.append("kind", kind);
      if (image) form.append("image", image, "image.jpg");
      return request("/api/sessions", { method: "POST", body: form });
    },

    // Spoken drills: passages to shadow, «60 секунд» series with measurements.
    getPassages: () => request("/api/speech/passages"),
    getTalks: () => request("/api/speech/talks"),

    getToday: () => request("/api/learner/today"),
    getActivityHistory: () => request("/api/learner/history"),
    getUsage: () => request("/api/usage"),
    getQueue: () => request("/api/learner/queue"),
    getLearnerTopics: () => request("/api/learner/topics"),
    getLearnerItems: ({ topic, dueOnly, kind } = {}) => {
      const params = new URLSearchParams();
      if (topic) params.set("topic", topic);
      if (kind) params.set("kind", kind);
      if (dueOnly) params.set("due_only", "true");
      return request(`/api/learner/items?${params}`);
    },
    // Claude checks one translation card's answer (the attempt is posted separately).
    checkCard: (itemId, drill, answer) =>
      request(`/api/learner/cards/${encodeURIComponent(itemId)}/check`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ drill, answer }),
      }),
    postAttempt: (attempt) =>
      request("/api/learner/attempts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(attempt),
      }),

    // AI exercise sets: creating one is a paid call (an unstarted set on the
    // topic is handed back instead unless `force`); submitting grades the
    // translations in one more call.
    listSets: (topic) =>
      request(`/api/practice/sets${topic ? `?topic=${encodeURIComponent(topic)}` : ""}`),
    getSet: (id) => request(`/api/practice/sets/${encodeURIComponent(id)}`),
    // `theme` is the picker's choice ({key} or {label}); null = the last one used.
    createSet: (topic, force = false, theme = null) =>
      request("/api/practice/sets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic, force, theme }),
      }),
    submitSet: (id, answers, context) =>
      request(`/api/practice/sets/${encodeURIComponent(id)}/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answers, context: context || null }),
      }),

    // Dictation (Stage 7, free): importing a lesson runs in the background on
    // the server, so the list is polled until it is ready.
    listLessons: () => request("/api/dictation/lessons"),
    getLesson: (id) => request(`/api/dictation/lessons/${encodeURIComponent(id)}`),
    importLesson: (url, language) =>
      request("/api/dictation/lessons", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, language: language || "en-US" }),
      }),
    deleteLesson: (id) =>
      request(`/api/dictation/lessons/${encodeURIComponent(id)}`, { method: "DELETE" }),
    postLessonResult: (id, result) =>
      request(`/api/dictation/lessons/${encodeURIComponent(id)}/results`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(result),
      }),
    getDictationStats: () => request("/api/dictation/stats"),
    // The translation task after a dictation: two explicit Haiku calls.
    splitLesson: (id) =>
      request(`/api/dictation/lessons/${encodeURIComponent(id)}/parts`, { method: "POST" }),
    postPartTranslation: (id, part, text) =>
      request(`/api/dictation/lessons/${encodeURIComponent(id)}/parts/${part}/translation`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      }),

    analyzeSession: (id, force = false, theme = null) =>
      request(`/api/sessions/${encodeURIComponent(id)}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force, theme }),
      }),

    // Contexts «уклон» (js/themes.js): built-in and own ones, the last used.
    getThemes: () => request("/api/themes"),
    addTheme: (label) =>
      request("/api/themes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label }),
      }),
    deleteTheme: (key) => request(`/api/themes/${encodeURIComponent(key)}`, { method: "DELETE" }),
    setLastTheme: (theme) =>
      request("/api/themes/last", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(theme),
      }),
    getThemePrompts: (key) => request(`/api/themes/${encodeURIComponent(key)}/prompts`),
    // Paid (Haiku, ~0.1 ¢): speaking prompts for an own context.
    writeThemePrompts: (key) =>
      request(`/api/themes/${encodeURIComponent(key)}/prompts`, { method: "POST" }),
  };
})();

// Shared helpers used by more than one view.
function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds || 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

// Russian plural: pluralRu(2, "предложение", "предложения", "предложений").
function pluralRu(count, one, few, many) {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function sentencesWord(count) {
  return pluralRu(count, "предложение", "предложения", "предложений");
}

// "2.8 ¢" from a USD amount; one decimal is enough at this price range.
function formatCents(usd) {
  return `${Math.round((usd || 0) * 1000) / 10} ¢`;
}

let _topicLabelsCache = null;
async function topicLabel(key) {
  if (!_topicLabelsCache) {
    try {
      const data = await Api.getTopics();
      _topicLabelsCache = {};
      data.topics.forEach((t) => {
        _topicLabelsCache[t.key] = t.label;
      });
    } catch (err) {
      _topicLabelsCache = {};
    }
  }
  return _topicLabelsCache[key] || key;
}

const SEVERITY_LABELS = { minor: "Незначительно", moderate: "Заметно", major: "Существенно" };

const SCORE_LABELS = [
  ["grammar", "Грамматика"],
  ["vocabulary", "Словарный запас"],
  ["fluency", "Беглость"],
  ["naturalness", "Естественность"],
];

// Renders analysis.json. Schema v1 files only have summary + issues; every
// later section below is skipped when its field is missing or empty (the
// picture sections exist only for picture descriptions, schema v3).
async function renderAnalysis(container, analysis) {
  if (!analysis) {
    container.innerHTML = '<p class="muted">Анализ ещё не выполнялся — нажмите «Анализировать».</p>';
    return;
  }
  const issues = analysis.issues || [];
  const issueCards = await Promise.all(issues.map(renderIssue));

  container.innerHTML = `
    <h3>Обратная связь</h3>
    <p>${escapeHtml(analysis.summary)}</p>
    ${renderStrengths(analysis.strengths)}
    <h3>Разбор</h3>
    ${issueCards.join("") || '<p class="muted">Заметных ошибок не найдено — отличная запись!</p>'}
    ${renderLessonCheck(analysis.lesson)}
    ${renderNotMentioned(analysis.not_mentioned)}
    ${renderPhraseSection("Полезные слова для этой сцены", analysis.scene_vocabulary)}
    ${renderPhraseSection("Слова и выражения", analysis.vocabulary)}
    ${renderImprovedVersion(analysis.improved_version)}
    ${renderPhraseSection("Что запомнить из этой записи", analysis.takeaways)}
    ${renderScores(analysis.scores, analysis.overall_score, analysis.input_mode === "text")}
  `;
}

// A lesson's spoken task (Stage 8, R6): how well the lesson's rule was used.
function renderLessonCheck(lesson) {
  const check = lesson && lesson.check;
  if (!check) return "";
  return `
    <h3>Правило урока</h3>
    <div class="issue-card">
      <p><strong>${escapeHtml(check.score)}/10</strong> — ${escapeHtml(check.verdict)}</p>
      ${
        check.good_uses && check.good_uses.length
          ? `<p class="muted">Получилось:</p><ul>${check.good_uses
              .map((q) => `<li class="correction">✓ «${escapeHtml(q)}»</li>`)
              .join("")}</ul>`
          : ""
      }
      ${
        check.missed && check.missed.length
          ? `<p class="muted">Здесь правило было нужно:</p>${check.missed
              .map(
                (m) => `<p class="quote">✕ «${escapeHtml(m.quote)}»</p>
                        <p class="correction">✓ ${escapeHtml(m.better)}</p>`
              )
              .join("")}`
          : ""
      }
      ${lesson.id ? `<p><a href="#/practice/${encodeURIComponent(lesson.id)}">← К уроку</a></p>` : ""}
    </div>`;
}

function renderNotMentioned(items) {
  if (!items || !items.length) return "";
  return `
    <h3>Что вы не упомянули</h3>
    <ul class="scene-list">
      ${items
        .map(
          (item) => `
        <li>
          <span class="muted">${escapeHtml(item.detail)}</span>
          <div class="phrase">${escapeHtml(item.phrase)}</div>
        </li>`
        )
        .join("")}
    </ul>
  `;
}

async function renderIssue(issue) {
  const label = await topicLabel(issue.topic);
  const severityClass = `pill-${issue.severity || "minor"}`;
  const severityText = SEVERITY_LABELS[issue.severity] || issue.severity || "";
  const betterVersions = (issue.better_versions || []).filter(Boolean);
  const pattern = issue.pattern && issue.pattern.rule ? issue.pattern : null;
  return `
    <div class="issue-card">
      <span class="pill ${severityClass}">${escapeHtml(severityText)}</span>
      <span class="muted"> · <a href="#/practice/${encodeURIComponent(issue.topic)}"
        title="Теория, упражнения и карточки по этой теме">${escapeHtml(label)}</a></span>
      <p class="quote">✕ «${escapeHtml(issue.quote)}»</p>
      <p>${escapeHtml(issue.explanation)}</p>
      <p class="correction">✓ ${escapeHtml(issue.correction)}</p>
      ${
        betterVersions.length
          ? `<div class="better-versions">
               <span class="muted">Проще / естественнее:</span>
               <ul>${betterVersions.map((v) => `<li>${escapeHtml(v)}</li>`).join("")}</ul>
             </div>`
          : ""
      }
      ${
        pattern
          ? `<div class="pattern-box">
               <div class="pattern-rule">${escapeHtml(pattern.rule)}</div>
               ${(pattern.examples || []).map((e) => `<div class="pattern-example">${escapeHtml(e)}</div>`).join("")}
             </div>`
          : ""
      }
    </div>
  `;
}

function renderStrengths(strengths) {
  if (!strengths || !strengths.length) return "";
  return `
    <div class="strengths">
      <strong>Что получилось хорошо</strong>
      <ul>${strengths.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ul>
    </div>
  `;
}

function renderPhraseSection(title, items) {
  if (!items || !items.length) return "";
  return `
    <h3>${escapeHtml(title)}</h3>
    <ul class="phrase-list">
      ${items
        .map(
          (item) => `
        <li>
          <span class="phrase">${escapeHtml(item.phrase)}</span>
          <span class="muted"> — ${escapeHtml(item.meaning)}</span>
          ${item.example ? `<div class="phrase-example">${escapeHtml(item.example)}</div>` : ""}
        </li>`
        )
        .join("")}
    </ul>
  `;
}

function renderImprovedVersion(text) {
  if (!text) return "";
  return `
    <h3>Как могло бы звучать</h3>
    <p class="muted">Не нужно повторять слово в слово — это ориентир чуть выше текущего уровня.</p>
    <blockquote class="improved-version">${escapeHtml(text)}</blockquote>
  `;
}

// A typed text has no delivery: its fluency score is shown but is not part
// of the overall score (nor of the score history).
function renderScores(scores, overall, typed = false) {
  if (!scores) return "";
  const rows = SCORE_LABELS.filter(([key]) => scores[key])
    .map(([key, label]) => {
      const excluded = typed && key === "fluency";
      return `
      <tr${excluded ? ' class="muted"' : ""}>
        <td>${label}</td>
        <td class="score">${escapeHtml(scores[key].score)}/10</td>
        <td>${escapeHtml(scores[key].comment)}${
          excluded ? " <em>(текст набран вручную — не входит в итог)</em>" : ""
        }</td>
      </tr>`;
    })
    .join("");
  const overallRow =
    overall != null
      ? `<tr class="overall"><td>Итого</td><td class="score">${escapeHtml(overall)}/10</td><td></td></tr>`
      : "";
  return `
    <h3>Оценка</h3>
    <div class="table-wrap">
      <table class="score-table"><tbody>${rows}${overallRow}</tbody></table>
    </div>
  `;
}
