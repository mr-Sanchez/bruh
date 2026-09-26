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

    // `kind` is "monologue", "picture", "talk" or "shadowing"; a picture take
    // also sends `image`, a drill take what it practises (`drill`: talk -
    // prompt_index and, from round 2, series; shadowing - source_session_id
    // and passage).
    uploadSession: (blob, { language, durationSeconds, mimeType, kind, image, drill }) => {
      const form = new FormData();
      const extension = (mimeType || "").includes("ogg") ? "webm" : "webm";
      form.append("file", blob, `audio.${extension}`);
      form.append("language", language);
      form.append("client_duration_seconds", String(durationSeconds));
      form.append("mime_type", mimeType || blob.type || "");
      if (kind) form.append("kind", kind);
      if (image) form.append("image", image, "image.jpg");
      Object.entries(drill || {}).forEach(([key, value]) => {
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
    createSet: (topic, force = false) =>
      request("/api/practice/sets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic, force }),
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

    analyzeSession: (id, force = false) =>
      request(`/api/sessions/${encodeURIComponent(id)}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force }),
      }),
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
    ${renderNotMentioned(analysis.not_mentioned)}
    ${renderPhraseSection("Полезные слова для этой сцены", analysis.scene_vocabulary)}
    ${renderPhraseSection("Слова и выражения", analysis.vocabulary)}
    ${renderImprovedVersion(analysis.improved_version)}
    ${renderPhraseSection("Что запомнить из этой записи", analysis.takeaways)}
    ${renderScores(analysis.scores, analysis.overall_score, analysis.input_mode === "text")}
  `;
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
      <span class="muted"> · ${escapeHtml(label)}</span>
      <p class="quote">❌ «${escapeHtml(issue.quote)}»</p>
      <p>${escapeHtml(issue.explanation)}</p>
      <p class="correction">✅ ${escapeHtml(issue.correction)}</p>
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
