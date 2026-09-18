// Progress view: topics ranked by a recency-decayed weakness score, each
// linking to its (stub) Practice screen.
window.Views = window.Views || {};

Views.progress = (() => {
  async function render(container) {
    container.innerHTML = `<div class="card"><h2>Прогресс</h2><div id="list"></div></div>`;
    const list = container.querySelector("#list");

    let data;
    try {
      data = await Api.getProgress();
    } catch (err) {
      list.innerHTML = `<p class="muted">Не удалось загрузить прогресс: ${escapeHtml(err.message)}</p>`;
      return;
    }

    if (!data.topics.length) {
      list.innerHTML = `<div class="empty-state">
        Пока нет данных. Проанализируйте хотя бы одну запись на вкладке «Запись» или «История» — здесь появятся темы, которые стоит подтянуть.
      </div>`;
      return;
    }

    list.innerHTML = `
      <p class="muted">Проанализировано сессий: ${data.sessions_analyzed}</p>
      <ul class="topic-list">
        ${data.topics
          .map(
            (t) => `
          <li class="topic-row">
            <div>
              <strong>${escapeHtml(t.label)}</strong><br/>
              <span class="topic-meta">упоминаний: ${t.count} · последний раз: ${escapeHtml(
                (t.last_seen || "").slice(0, 10)
              )}</span>
            </div>
            <a href="#/practice/${encodeURIComponent(t.key)}"><button class="secondary">Практика</button></a>
          </li>`
          )
          .join("")}
      </ul>
    `;
  }

  function dispose() {}

  return { render, dispose };
})();
