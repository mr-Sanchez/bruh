// Session view: one recording's transcript, audio player, and analysis.
// A spoken take also shows its pace and pauses; a spoken drill (shadowing, an
// older «60 секунд» take) is shown with its measurements only: it is never
// sent to Claude.
window.Views = window.Views || {};

Views.session = (() => {
  const TITLES = {
    picture: "Описание картинки · ",
    talk: "60 секунд · ",
    shadowing: "Shadowing · ",
  };

  // A drill take: what it practised and how it sounded, no analyse button.
  function renderDrill(session) {
    const drill = session.drill || {};
    const what =
      session.kind === "talk"
        ? `Попытка ${drill.round || 1} из серии${
            drill.round === 1 ? " (она и идёт в зачёт темы «беглость»)" : " — тренировочная"
          }`
        : `Отрывок ${(drill.passage || 0) + 1} монолога
           <a href="#/session/${encodeURIComponent(drill.source_session_id || "")}">от ${escapeHtml(
             (drill.source_session_id || "").slice(0, 10)
           )}</a>`;
    return `
      <div class="card">
        <h2>Разбор</h2>
        <p class="muted">${what}</p>
        ${Speech.renderReport(session)}
        <details class="table-view">
          <summary>Транскрипт как есть</summary>
          <div class="transcript-box">${escapeHtml(session.transcript || "(нет транскрипта)")}</div>
        </details>
      </div>`;
  }

  // A «Говорение» take: its prompt and place in its series.
  function renderSpeaking(session) {
    const drill = session.drill;
    if (!drill) return "";
    const limit = drill.time_limit ? ` · лимит ${drill.time_limit / 60} мин` : "";
    const round = `Дубль ${drill.round || 1}${drill.round > 1 ? " серии — тренировочный" : ""}${limit}`;
    return `
      <div class="speaking-prompt">
        <div class="muted">${drill.question ? `Тема: ${escapeHtml(drill.hint)} · ` : "Своя тема · "}${round}</div>
        ${drill.question ? `<p class="speaking-question">${escapeHtml(drill.question)}</p>` : ""}
      </div>`;
  }

  function renderMeasurements(session) {
    if (!session.speech) return "";
    return `
      <div class="card">
        <h2>Темп и паузы</h2>
        ${Speech.renderReport(session)}
      </div>`;
  }

  function renderTranscript(session, typed) {
    return `
      <div class="card">
        <h2>${typed ? "Ваш текст" : "Транскрипт"}</h2>
        <div class="transcript-box">${escapeHtml(session.transcript || "(нет транскрипта)")}</div>
        <div class="button-row">
          <button id="analyze-btn" class="secondary">
            ${session.analysis ? "Анализировать повторно" : "Анализировать (ИИ ассистент)"}
          </button>
        </div>
        <div id="analyze-theme"></div>
        <div id="analysis-slot"></div>
      </div>`;
  }

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

    const typed = session.input_mode === "text";
    const title = TITLES[session.kind] || (session.drill ? "Говорение · " : "");
    const drill = session.kind === "talk" || session.kind === "shadowing";
    container.innerHTML = `
      <div class="card">
        <h2>${title}${escapeHtml(session.started_at)}</h2>
        <p class="muted">
          Язык: ${escapeHtml(session.language)} ·
          ${typed ? "Набрано текстом" : `Длительность: ${formatDuration(session.duration_seconds)}`} ·
          Статус: ${escapeHtml(session.status)}
        </p>
        ${
          session.lesson && session.lesson.task
            ? `<div class="speaking-prompt">
                 <div class="muted">Устное задание урока
                   <a href="#/practice/${encodeURIComponent(session.lesson.id)}">«${escapeHtml(session.lesson.label)}»</a></div>
                 <p class="speaking-question">${escapeHtml(session.lesson.task.question)}</p>
                 ${session.lesson.task.use ? `<p class="muted">Используйте: ${escapeHtml(session.lesson.task.use)}</p>` : ""}
               </div>`
            : ""
        }
        ${session.kind === "monologue" ? renderSpeaking(session) : ""}
        ${session.error_message ? `<p class="muted">${escapeHtml(session.error_message)}</p>` : ""}
        ${session.image_url ? `<img class="session-picture" src="${session.image_url}" alt="Картинка, которую вы описывали" />` : ""}
        ${session.audio_url ? `<audio controls src="${session.audio_url}" style="width:100%"></audio>` : ""}
      </div>
      ${drill ? renderDrill(session) : renderMeasurements(session) + renderTranscript(session, typed)}
    `;

    if (drill) return;
    const slot = container.querySelector("#analysis-slot");
    await renderAnalysis(slot, session.analysis);
    const picker = await ThemePicker.mountForAnalysis(
      container.querySelector("#analyze-theme"),
      session.language
    );

    const analyzeBtn = container.querySelector("#analyze-btn");
    analyzeBtn.addEventListener("click", async () => {
      analyzeBtn.disabled = true;
      analyzeBtn.textContent = "Анализируем...";
      try {
        const theme = picker ? picker.value() : null;
        const result = await Api.analyzeSession(session.id, !!session.analysis, theme);
        session.analysis = result.analysis;
        await renderAnalysis(slot, result.analysis);
      } catch (err) {
        slot.innerHTML = `<p class="muted">Ошибка анализа: ${escapeHtml(err.message)}</p>`;
      } finally {
        analyzeBtn.disabled = false;
        analyzeBtn.textContent = session.analysis ? "Анализировать повторно" : "Анализировать (ИИ ассистент)";
      }
    });
  }

  function dispose() {}

  return { render, dispose };
})();
