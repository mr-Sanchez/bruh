// History view: past sessions, newest first, linking into the session view.
window.Views = window.Views || {};

Views.history = (() => {
  const STATUS_LABELS = {
    recording: "Запись",
    transcribing: "Транскрибируется",
    done: "Готово",
    error: "Ошибка",
  };

  async function render(container) {
    container.innerHTML = `<div class="card"><h2>История записей</h2><div id="list"></div></div>`;
    const list = container.querySelector("#list");

    let data;
    try {
      data = await Api.listSessions();
    } catch (err) {
      list.innerHTML = `<p class="muted">Не удалось загрузить историю: ${escapeHtml(err.message)}</p>`;
      return;
    }

    if (!data.sessions.length) {
      list.innerHTML = `<div class="empty-state">Пока нет ни одной записи. Перейдите на вкладку «Запись», чтобы начать.</div>`;
      return;
    }

    list.innerHTML = `
      <ul class="session-list">
        ${data.sessions
          .map(
            (s) => `
          <li>
            <a href="#/session/${encodeURIComponent(s.id)}">
              <span>${escapeHtml(s.started_at)} · ${escapeHtml(s.language)}</span>
              <span class="muted">
                ${formatDuration(s.duration_seconds)}
                · ${escapeHtml(STATUS_LABELS[s.status] || s.status)}
                ${s.has_analysis ? " · 🧠 есть анализ" : ""}
              </span>
            </a>
          </li>`
          )
          .join("")}
      </ul>
    `;
  }

  function dispose() {}

  return { render, dispose };
})();
