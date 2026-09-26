// «Сегодня» - the home screen: a ~10-minute workout assembled by the server
// from the learner model (GET /api/learner/today, no LLM involved):
//   1. today's cards (due reviews + new ones),
//   2. one live activity - a monologue on the day's prompt (a picture
//      description done today counts too);
//   3. a few sentences of listening dictation from a YouTube lesson ($0);
//   4. optional: «Урок дня» of the course - the lesson the learner's mistakes
//      ask for most (else «Продолжить»), with its next action (theory, a set,
//      the spoken task); it replaced the AI-set step on 2026-09-26;
//   5. optional, Deepgram only: a spoken warm-up («60 секунд» or shadowing).
// Each step's done/todo state comes from the attempts log and today's
// recordings, so the screen stays right after a reload.
window.Views = window.Views || {};

Views.today = (() => {
  let root = null;

  const STEP_TITLES = {
    cards: "Карточки",
    monologue: "Монолог",
    dictation: "Диктант на слух",
    lesson: "Урок дня",
    speech: "Речевая разминка",
  };

  async function render(container) {
    root = container;
    container.innerHTML = `<div class="card"><p class="muted">Загрузка...</p></div>`;
    let workout;
    let progress;
    try {
      [workout, progress] = await Promise.all([Api.getToday(), Api.getProgress()]);
    } catch (err) {
      container.innerHTML = `<div class="card"><p class="muted">Не удалось загрузить: ${escapeHtml(err.message)}</p></div>`;
      return;
    }
    if (root !== container) return;

    // Optional steps (the lesson's actions cost money) never hold back "all done".
    const allDone = workout.steps.every((s) => s.optional || s.status === "done" || s.status === "empty");
    container.innerHTML = `
      <div class="card">
        <div class="today-head">
          <div>
            <h2>Сегодня</h2>
            <p class="muted">${escapeHtml(formatDate(workout.today))}</p>
          </div>
          <div class="today-meta">
            ${workout.streak_days ? `<div class="streak">🔥 ${workout.streak_days} ${daysWord(workout.streak_days)} подряд</div>` : ""}
            <div class="muted">${allDone ? "Всё сделано 🎉" : `осталось ≈ ${Math.max(1, workout.minutes_left)} мин`}</div>
          </div>
        </div>
        ${renderFocus(workout.focus_topic)}
        <ol class="workout">
          ${workout.steps.map(renderStep).join("")}
        </ol>
        ${allDone ? `<p class="muted">На сегодня тренировка выполнена. Можно продолжить на вкладке «Занятия».</p>` : ""}
      </div>
      ${renderScores(progress.score_history)}`;

    wire(container, workout);
  }

  function formatDate(iso) {
    const date = new Date(`${iso}T12:00:00`);
    return date.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  }

  function daysWord(n) {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return "день";
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return "дня";
    return "дней";
  }

  function renderFocus(topic) {
    if (!topic) return "";
    const accuracy = topic.accuracy == null ? "" : ` · точность в упражнениях ${Math.round(topic.accuracy * 100)}%`;
    const link = topic.resources.length
      ? ` · <a href="${escapeHtml(topic.resources[0].url)}" target="_blank" rel="noopener">теория ↗</a>`
      : "";
    return `
      <p class="focus-topic">Главная тема сейчас:
        <a href="#/practice/${encodeURIComponent(topic.key)}"><strong>${escapeHtml(topic.label)}</strong></a>
        <span class="muted">${accuracy}${link}</span></p>`;
  }

  function stepShell(step, body, action) {
    const done = step.status === "done";
    const empty = step.status === "empty";
    return `
      <li class="workout-step ${done ? "is-done" : ""} ${empty ? "is-empty" : ""}">
        <span class="step-mark" aria-hidden="true">${done ? "✓" : ""}</span>
        <div class="step-body">
          <div class="step-title">${step.activity === "picture" ? "Описание картинки" : STEP_TITLES[step.kind]}
            ${step.minutes && !done ? `<span class="muted step-time">≈ ${step.minutes} мин</span>` : ""}
            ${step.optional && !done ? `<span class="muted step-time">· по желанию</span>` : ""}
            ${done ? `<span class="visually-hidden">— выполнено</span>` : ""}
          </div>
          ${body}
        </div>
        <div class="step-action">${action || ""}</div>
      </li>`;
  }

  function renderStep(step) {
    if (step.kind === "cards") return renderCards(step);
    if (step.kind === "dictation") return renderDictation(step);
    if (step.kind === "lesson") return renderLesson(step);
    if (step.kind === "speech") return renderSpeech(step);
    return renderMonologue(step);
  }

  // Free and mandatory: a handful of sentences typed from a YouTube lesson.
  function renderDictation(step) {
    if (step.status === "done") {
      const word = sentencesWord(step.done_today);
      return stepShell(step, `<p class="muted">Надиктовано сегодня: ${step.done_today} ${word}.</p>`);
    }
    if (step.status === "empty") {
      return stepShell(
        step,
        `<p class="muted">Уроков пока нет — добавьте ссылку на видео с YouTube, и диктант появится здесь.</p>`,
        `<a href="#/dictation"><button class="secondary">Добавить урок</button></a>`
      );
    }
    const lesson = step.lesson;
    const body = lesson
      ? `<p class="muted">${escapeHtml(lesson.title || "")} · ${step.done_today} из ${step.target} ${sentencesWord(step.target)}
           · в уроке пройдено ${lesson.progress.done} из ${lesson.progress.sentences}</p>`
      : `<p class="muted">${step.done_today} из ${step.target} ${sentencesWord(step.target)} за сегодня.</p>`;
    const href = lesson ? `#/dictation/${encodeURIComponent(lesson.id)}` : "#/dictation";
    return stepShell(step, body, `<a href="${href}"><button>${step.done_today ? "Продолжить" : "Начать"}</button></a>`);
  }

  // «Урок дня»: which lesson, why, and the one next thing to do in it.
  const ACTION_TEXT = {
    theory: "начните с теории",
    set: "пройдите набор упражнений",
    spoken: "запишите устное задание — правило в живой речи",
  };

  function renderLesson(step) {
    const lesson = step.lesson;
    const link = `<a href="#/practice/${encodeURIComponent(lesson.key)}">${escapeHtml(lesson.label)}</a>`;
    if (step.status === "done") {
      const what = step.exercise === "lesson_task" ? "устное задание" : "набор";
      return stepShell(
        step,
        `<p class="muted">${link}: ${what} — ${Math.round(step.score * 100)}%.</p>`
      );
    }
    const why =
      step.reason === "mistakes"
        ? `по вашим ошибкам в речи (${step.speech_mistakes})`
        : "следующий урок курса";
    const body = `
      <p class="muted">${link} · ${escapeHtml(lesson.level_label)} · ${why}</p>
      <p class="muted">Дальше: ${ACTION_TEXT[step.action]}${
        step.cost_usd ? ` · ≈ ${formatCents(step.cost_usd)}` : ""
      }.</p>
      ${step.action === "set" && !step.set_id && step.anthropic_configured ? `<div data-role="set-theme"></div>` : ""}
      <p class="muted" data-role="set-status"></p>`;
    let action = `<a href="#/practice/${encodeURIComponent(lesson.key)}"><button>К уроку</button></a>`;
    if (step.action === "set" && step.set_id) {
      action = `<button data-role="lesson-set">Начать набор</button>`;
    } else if (step.action === "set" && step.anthropic_configured) {
      action = `<button class="secondary" data-role="lesson-set">Составить · ≈ ${formatCents(step.cost_usd)}</button>`;
    }
    return stepShell(step, body, action);
  }

  // Deepgram only, so it is optional like «Урок дня»: «60 секунд» on the
  // day's prompt, or shadowing the passage that needs it most.
  function renderSpeech(step) {
    if (step.status === "done") {
      const what = step.activity === "shadowing" ? "Shadowing" : "«60 секунд»";
      return stepShell(
        step,
        `<p class="muted">${what}: сделано. Разбор — на странице записи.</p>`,
        `<a href="#/session/${encodeURIComponent(step.session_id)}"><button class="secondary">Открыть</button></a>`
      );
    }
    const passage = step.passage;
    const body = `
      <p class="muted">Минута на тему «${escapeHtml(step.prompt.hint)}» (${escapeHtml(step.prompt.theme.label)}) — три раза подряд;
        считаем темп, паразиты и паузы.</p>
      ${
        passage
          ? `<p class="muted">Или <a href="#/shadowing/${encodeURIComponent(
              `${passage.session_id}:${passage.index}`
            )}">прочитайте вслух отрывок</a> своей «улучшенной версии».</p>`
          : ""
      }`;
    return stepShell(
      step,
      body,
      `<a href="#/talk/${encodeURIComponent(step.prompt.id)}"><button>Начать</button></a>`
    );
  }

  function renderCards(step) {
    if (step.status === "empty") {
      const body = step.new_waiting
        ? `<p class="muted">На сегодня карточек нет. Новые (${step.new_waiting}) появятся завтра.</p>`
        : `<p class="muted">Карточек пока нет — они появятся из анализа монолога.</p>`;
      return stepShell(step, body);
    }
    if (step.status === "done") {
      return stepShell(
        step,
        `<p class="muted">Готово: ${step.correct_today} из ${step.answered_today} верно. Ошибки вернутся завтра.</p>`
      );
    }
    const shown = step.items.length;
    const rest = step.queue_total - shown;
    const body = `
      <p class="muted">Повторить: ${step.reviews} · новых: ${step.new}${
        step.answered_today ? ` · уже сделано сегодня: ${step.answered_today}` : ""
      }</p>
      ${rest > 0 ? `<p class="muted">Ещё ${rest} — после тренировки, на вкладке «Занятия».</p>` : ""}`;
    return stepShell(step, body, `<button data-role="cards">Начать (${shown})</button>`);
  }

  function renderMonologue(step) {
    const prompt = step.prompt;
    const question = `
      <p class="speaking-question">${escapeHtml(prompt.question)}</p>
      <p class="muted">${escapeHtml(prompt.hint)} · уклон «${escapeHtml(prompt.theme.label)}» · 1–3 минуты, затем анализ</p>
      <p class="muted">Или вместо монолога <a href="#/picture">опишите картинку</a>.</p>`;
    if (step.status === "done") {
      return stepShell(
        step,
        `<p class="muted">Записано и проанализировано. Новые ошибки уже в карточках.</p>`,
        `<a href="#/session/${encodeURIComponent(step.session_id)}"><button class="secondary">Открыть</button></a>`
      );
    }
    if (step.status === "analyze") {
      return stepShell(
        step,
        `<p class="muted">Запись есть — осталось проанализировать её, чтобы ошибки попали в карточки.</p>`,
        `<a href="#/session/${encodeURIComponent(step.session_id)}"><button>К записи</button></a>`
      );
    }
    return stepShell(
      step,
      question,
      `<a href="#/record/${encodeURIComponent(prompt.id)}"><button>Записать</button></a>`
    );
  }

  function renderScores(history) {
    if (!history || !history.length) return "";
    return `
      <div class="card">
        <div class="today-head">
          <h2>Оценки речи</h2>
          <a href="#/progress">Все графики →</a>
        </div>
        <p class="muted">Последняя оценённая запись и изменение к предыдущей.</p>
        ${Charts.scoreTiles(history)}
      </div>`;
  }

  function wire(container, workout) {
    const cards = workout.steps.find((s) => s.kind === "cards");
    const again = () => {
      if (root === container) render(container);
    };
    const cardsButton = container.querySelector('[data-role="cards"]');
    if (cardsButton) {
      cardsButton.addEventListener("click", () =>
        Drill.runCards(container, cards.items, { title: "Сегодня: карточки", context: "today", onFinish: again })
      );
    }
    // A paid click: an already generated set opens as is, otherwise one is
    // generated now (the server still hands back an unstarted set if any).
    const lessonStep = workout.steps.find((s) => s.kind === "lesson");
    const setButton = container.querySelector('[data-role="lesson-set"]');
    const themeHost = container.querySelector('[data-role="set-theme"]');
    let picker = null;
    if (themeHost) {
      ThemePicker.mount(themeHost)
        .then((p) => (picker = p))
        .catch(() => (themeHost.textContent = ""));
    }
    if (setButton) {
      setButton.addEventListener("click", async () => {
        const status = container.querySelector('[data-role="set-status"]');
        setButton.disabled = true;
        if (!lessonStep.set_id) status.textContent = "Claude составляет набор — обычно 10–30 секунд…";
        try {
          const data = lessonStep.set_id ? await Api.getSet(lessonStep.set_id) : await Api.createSet(lessonStep.lesson.key, false, picker ? picker.value() : null);
          if (root !== container) return;
          Drill.runSet(container, data.set, { context: "today", onFinish: again });
        } catch (err) {
          status.textContent = `Не удалось: ${err.message}`;
          setButton.disabled = false;
        }
      });
    }
  }

  function dispose() {
    root = null;
  }

  return { render, dispose };
})();
