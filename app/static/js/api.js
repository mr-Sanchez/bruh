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

    uploadSession: (blob, { language, durationSeconds, mimeType }) => {
      const form = new FormData();
      const extension = (mimeType || "").includes("ogg") ? "webm" : "webm";
      form.append("file", blob, `audio.${extension}`);
      form.append("language", language);
      form.append("client_duration_seconds", String(durationSeconds));
      form.append("mime_type", mimeType || blob.type || "");
      return request("/api/sessions", { method: "POST", body: form });
    },

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
// v2 section below is skipped when its field is missing or empty.
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
    ${renderPhraseSection("Слова и выражения", analysis.vocabulary)}
    ${renderImprovedVersion(analysis.improved_version)}
    ${renderPhraseSection("Что запомнить из этой записи", analysis.takeaways)}
    ${renderScores(analysis.scores, analysis.overall_score)}
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

function renderScores(scores, overall) {
  if (!scores) return "";
  const rows = SCORE_LABELS.filter(([key]) => scores[key])
    .map(
      ([key, label]) => `
      <tr>
        <td>${label}</td>
        <td class="score">${escapeHtml(scores[key].score)}/10</td>
        <td>${escapeHtml(scores[key].comment)}</td>
      </tr>`
    )
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
