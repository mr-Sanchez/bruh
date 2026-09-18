// Practice view: PURE STUB. Placeholder exercise entry points per topic -
// no working exercise logic yet, just the navigable shell for future work
// (grammar quiz / translation / spoken practice). No backend call.
window.Views = window.Views || {};

Views.practice = (() => {
  const EXERCISE_STUBS = [
    { icon: "📝", title: "Грамматическое упражнение" },
    { icon: "🌐", title: "Практика перевода" },
    { icon: "🎤", title: "Устная практика" },
  ];

  async function render(container, topicKey) {
    container.innerHTML = `<div class="card"><p class="muted">Загрузка...</p></div>`;

    let label = topicKey;
    try {
      const data = await Api.getTopics();
      const match = data.topics.find((t) => t.key === topicKey);
      if (match) label = match.label;
    } catch (err) {
      /* fall back to the raw key */
    }

    container.innerHTML = `
      <div class="card">
        <h2>Практика: ${escapeHtml(label)}</h2>
        <p class="muted">Упражнения по этой теме появятся здесь позже. Пока это только черновой макет разделов.</p>
        <div class="practice-grid">
          ${EXERCISE_STUBS.map(
            (stub) => `
            <div class="practice-card">
              <div style="font-size:1.8rem">${stub.icon}</div>
              <div>${escapeHtml(stub.title)}</div>
              <span class="soon">Скоро появится</span>
            </div>`
          ).join("")}
        </div>
      </div>
      <p><a href="#/progress">← Назад к прогрессу</a></p>
    `;
  }

  function dispose() {}

  return { render, dispose };
})();
