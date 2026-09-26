// «Курс» (#/roadmap): the fixed A2 → C1 course - levels → modules → lessons,
// each lesson with its status. Every lesson is open; «Продолжить» goes to the
// first one still to do, and «Уже знаю» / «Пропустить» are marks, never locks
// (Stage 8, decided 2026-09-26). Statuses are derived on the server
// (app/roadmap.py); this page costs nothing.
window.Views = window.Views || {};

Views.roadmap = (() => {
  const STATUS_LABELS = {
    not_started: "не начат",
    theory: "теория",
    practising: "практикуется",
    mastered: "освоен",
    skipped: "пропущен",
    known: "уже знаю",
  };

  let root = null;
  // Levels the learner opened or closed by hand survive a re-render after a mark.
  let openLevels = null;

  async function render(container) {
    root = container;
    openLevels = null;
    container.innerHTML = `<div class="card"><p class="muted">Загрузка...</p></div>`;
    try {
      draw(container, await Api.getRoadmap());
    } catch (err) {
      container.innerHTML = `<div class="card"><p class="muted">Не удалось загрузить курс: ${escapeHtml(err.message)}</p></div>`;
    }
  }

  function draw(container, data) {
    if (root !== container) return; // navigated away meanwhile
    const lessons = {};
    data.levels.forEach((level) =>
      level.modules.forEach((module) =>
        module.lessons.forEach((lesson) => (lessons[lesson.id] = { lesson, level }))
      )
    );
    const next = data.continue ? lessons[data.continue] : null;
    if (!openLevels) {
      // First draw: open the level the learner continues in (or the first one).
      openLevels = new Set([next ? next.level.key : data.levels[0].key]);
    }

    container.innerHTML = `
      <p><a href="#/practice">← Занятия</a></p>
      <div class="card">
        <h2>Курс A2 → C1</h2>
        ${renderSummary(data)}
        ${
          next
            ? `<div class="button-row">
                 <a href="${lessonHref(next.lesson.id)}"><button>Продолжить: ${escapeHtml(next.lesson.label)}</button></a>
               </div>
               <p class="topic-meta">${escapeHtml(next.level.label)} · ${escapeHtml(STATUS_LABELS[next.lesson.status])}</p>`
            : `<p>Все уроки курса освоены или отмечены 🎉</p>`
        }
        <p class="muted">Все уроки открыты — можно идти не по порядку. Урок освоен, когда набор упражнений
          пройден на ${Math.round(data.pass_score * 100)} % и выше ${data.mastery_runs} раза в разные дни.
          «Уже знаю» и «Пропустить» — только отметки: если потом заниматься уроком, статус снова покажет, как идут дела.</p>
      </div>
      ${renderRecommended(data.recommended || [])}
      ${data.levels.map(renderLevel).join("")}`;

    container.querySelectorAll("details[data-level]").forEach((details) =>
      details.addEventListener("toggle", () => {
        if (details.open) openLevels.add(details.dataset.level);
        else openLevels.delete(details.dataset.level);
      })
    );
    container.querySelectorAll("[data-mark]").forEach((button) =>
      button.addEventListener("click", () => mark(container, button))
    );
  }

  // Lessons the learner's own recordings ask for (R8): whatever their status
  // or mark, if the mistakes keep coming, the lesson is worth doing.
  function renderRecommended(rows) {
    if (!rows.length) return "";
    return `
      <div class="card">
        <h2>По вашим ошибкам</h2>
        <p class="muted">Эти темы чаще всего встречаются в ваших записях и ещё не закреплены упражнениями.</p>
        <ul class="topic-list">${rows.map(renderLesson).join("")}</ul>
      </div>`;
  }

  function renderSummary(data) {
    const c = data.counts;
    const parts = [
      `освоено: <strong>${c.mastered}</strong>`,
      `уже знаю: <strong>${c.known}</strong>`,
      `в работе: <strong>${c.practising + c.theory}</strong>`,
    ];
    if (c.skipped) parts.push(`пропущено: <strong>${c.skipped}</strong>`);
    const done = c.mastered + c.known;
    return `
      <p>${parts.join(" · ")} <span class="muted">из ${data.total} уроков</span></p>
      <div class="progress-bar"><div style="width: ${Math.round((done / data.total) * 100)}%"></div></div>`;
  }

  function renderLevel(level) {
    const open = openLevels.has(level.key) ? " open" : "";
    return `
      <details class="card roadmap-level" data-level="${escapeHtml(level.key)}"${open}>
        <summary>
          <h2>${escapeHtml(level.label)}</h2>
          <span class="topic-meta">${level.done} / ${level.total}</span>
        </summary>
        ${level.modules.map(renderModule).join("")}
      </details>`;
  }

  function renderModule(module) {
    return `
      <h3 class="area-heading">${escapeHtml(module.title)} <span class="topic-meta">· ${module.done} / ${module.total} ·
        <a href="#/moduletest/${encodeURIComponent(module.key)}">тест модуля</a></span></h3>
      <ul class="topic-list">${module.lessons.map(renderLesson).join("")}</ul>`;
  }

  function renderLesson(lesson) {
    const meta = [lesson.area_label];
    if (lesson.runs) {
      meta.push(`наборов пройдено: ${lesson.runs}, лучший: ${Math.round(lesson.best_score * 100)} %`);
    }
    if (lesson.speech_mistakes) {
      meta.push(
        `в вашей речи: ${lesson.speech_mistakes} ${pluralRu(lesson.speech_mistakes, "ошибка", "ошибки", "ошибок")}`
      );
    }
    return `
      <li class="topic-row roadmap-lesson">
        <div>
          <span class="pill lesson-${escapeHtml(lesson.status)}">${escapeHtml(STATUS_LABELS[lesson.status] || lesson.status)}</span>
          <strong>${escapeHtml(lesson.label)}</strong><br/>
          <span class="topic-meta">${escapeHtml(meta.join(" · "))}</span>
        </div>
        <div class="button-row">
          ${renderMarkButtons(lesson)}
          <a href="${lessonHref(lesson.id)}"><button class="secondary">Открыть</button></a>
        </div>
      </li>`;
  }

  // A mastered lesson needs no mark; a marked one can drop its mark.
  function renderMarkButtons(lesson) {
    if (lesson.status === "mastered") return "";
    const id = escapeHtml(lesson.id);
    if (lesson.status === "known" || lesson.status === "skipped") {
      return `<button class="link-button" data-mark="" data-lesson="${id}">Снять отметку</button>`;
    }
    return `
      <button class="link-button" data-mark="known" data-lesson="${id}">Уже знаю</button>
      <button class="link-button" data-mark="skipped" data-lesson="${id}">Пропустить</button>`;
  }

  async function mark(container, button) {
    container.querySelectorAll("[data-mark]").forEach((b) => (b.disabled = true));
    try {
      const data = await Api.markLesson(button.dataset.lesson, button.dataset.mark || null);
      const scroll = window.scrollY;
      draw(container, data);
      window.scrollTo(0, scroll);
    } catch (err) {
      container.querySelectorAll("[data-mark]").forEach((b) => (b.disabled = false));
      button.textContent = `Ошибка: ${err.message}`;
    }
  }

  // Until a lesson has its own page (theory, R4), it opens its topic's page:
  // cards and AI sets on the same topic key.
  function lessonHref(id) {
    return `#/practice/${encodeURIComponent(id)}`;
  }

  function dispose() {
    root = null;
  }

  return { render, dispose };
})();
