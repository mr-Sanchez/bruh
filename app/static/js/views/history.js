// History view: every activity, newest first - recordings (linking into the
// session view) and exercise results per day from the attempts log.
window.Views = window.Views || {};

Views.history = (() => {
  const KIND_ICONS = { picture: "🖼️ ", talk: "⏱️ ", shadowing: "🗣️ ", monologue: "🎙️ " };

  const DRILL_NAMES = { ai_set: "AI-набор", cloze: "пропуски", talk: "60 секунд", shadowing: "shadowing" };

  const STATUS_LABELS = {
    recording: "Запись",
    transcribing: "Транскрибируется",
    done: "Готово",
    error: "Ошибка",
  };

  async function render(container) {
    container.innerHTML = `
      <div class="card"><h2>Записи</h2><div id="list"></div></div>
      <div class="card"><h2>Упражнения</h2><div id="exercises"></div></div>`;
    const list = container.querySelector("#list");
    const exercises = container.querySelector("#exercises");

    let data;
    let days;
    try {
      [data, { days }] = await Promise.all([Api.listSessions(), Api.getActivityHistory()]);
    } catch (err) {
      list.innerHTML = `<p class="muted">Не удалось загрузить историю: ${escapeHtml(err.message)}</p>`;
      return;
    }

    list.innerHTML = data.sessions.length
      ? `<ul class="session-list">
          ${data.sessions
            .map(
              (s) => `
            <li>
              <a href="#/session/${encodeURIComponent(s.id)}">
                <span>${KIND_ICONS[s.kind] || "🎙️ "}${escapeHtml(s.started_at)} · ${escapeHtml(s.language)}</span>
                <span class="muted">
                  ${s.input_mode === "text" ? "текстом" : formatDuration(s.duration_seconds)}
                  · ${escapeHtml(STATUS_LABELS[s.status] || s.status)}
                  ${s.has_analysis ? " · 🧠 есть анализ" : ""}
                </span>
              </a>
            </li>`
            )
            .join("")}
        </ul>`
      : `<div class="empty-state">Пока нет ни одной записи. Начните с вкладки «Сегодня».</div>`;

    exercises.innerHTML = days.length
      ? `<ul class="session-list">${days.map(renderDay).join("")}</ul>`
      : `<p class="muted">Здесь появятся результаты карточек и упражнений.</p>`;
  }

  function renderDay(day) {
    const parts = [];
    if (day.cards) parts.push(`карточки: ${day.cards_correct} из ${day.cards} верно`);
    day.drills.forEach((drill) => {
      const name = DRILL_NAMES[drill.exercise] || drill.exercise;
      parts.push(`${name} · ${escapeHtml(drill.label)}: ${Math.round(drill.score * 100)}%`);
    });
    return `
      <li class="history-day">
        <span>${escapeHtml(day.date)}</span>
        <span class="muted">${parts.join(" · ")}</span>
      </li>`;
  }

  function dispose() {}

  return { render, dispose };
})();
