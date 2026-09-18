// Session view: one recording's transcript, audio player, and analysis.
window.Views = window.Views || {};

Views.session = (() => {
  async function render(container, sessionId) {
    container.innerHTML = `<div class="card"><p class="muted">Загрузка...</p></div>`;
    if (!sessionId) {
      container.innerHTML = `<div class="card"><p class="muted">Сессия не указана.</p></div>`;
      return;
    }

    let session;
    try {
      session = await Api.getSession(sessionId);
    } catch (err) {
      container.innerHTML = `<div class="card"><p class="muted">Не удалось загрузить сессию: ${escapeHtml(err.message)}</p></div>`;
      return;
    }

    container.innerHTML = `
      <div class="card">
        <h2>${escapeHtml(session.started_at)}</h2>
        <p class="muted">
          Язык: ${escapeHtml(session.language)} ·
          Длительность: ${formatDuration(session.duration_seconds)} ·
          Статус: ${escapeHtml(session.status)}
        </p>
        ${session.error_message ? `<p class="muted">${escapeHtml(session.error_message)}</p>` : ""}
        ${session.audio_url ? `<audio controls src="${session.audio_url}" style="width:100%"></audio>` : ""}
      </div>
      <div class="card">
        <h2>Транскрипт</h2>
        <div class="transcript-box">${escapeHtml(session.transcript || "(нет транскрипта)")}</div>
        <div class="button-row">
          <button id="analyze-btn" class="secondary">
            ${session.analysis ? "Анализировать повторно" : "Анализировать (Claude)"}
          </button>
        </div>
        <div id="analysis-slot"></div>
      </div>
    `;

    const slot = container.querySelector("#analysis-slot");
    await renderAnalysis(slot, session.analysis);

    const analyzeBtn = container.querySelector("#analyze-btn");
    analyzeBtn.addEventListener("click", async () => {
      analyzeBtn.disabled = true;
      analyzeBtn.textContent = "Анализируем...";
      try {
        const result = await Api.analyzeSession(session.id, !!session.analysis);
        session.analysis = result.analysis;
        await renderAnalysis(slot, result.analysis);
      } catch (err) {
        slot.innerHTML = `<p class="muted">Ошибка анализа: ${escapeHtml(err.message)}</p>`;
      } finally {
        analyzeBtn.disabled = false;
        analyzeBtn.textContent = session.analysis ? "Анализировать повторно" : "Анализировать (Claude)";
      }
    });
  }

  function dispose() {}

  return { render, dispose };
})();
