// «Занятия»: every activity, plus the free ($0) drills.
//   #/practice          - hub: activities, today's cards, topics
//   #/practice/<topic>  - one topic: its cards, AI sets, reference links
// Cards come from the learner model (daily queue / due items of a topic);
// drills themselves run in js/drill.js.
window.Views = window.Views || {};

Views.practice = (() => {
  let root = null;

  async function render(container, topicKey) {
    root = container;
    container.innerHTML = `<div class="card"><p class="muted">Загрузка...</p></div>`;
    try {
      if (topicKey) await renderTopic(container, topicKey);
      else await renderHub(container);
    } catch (err) {
      container.innerHTML = `<div class="card"><p class="muted">Не удалось загрузить: ${escapeHtml(err.message)}</p></div>`;
    }
  }

  function rerender(topicKey) {
    if (root) render(root, topicKey);
  }

  // ---------------------------------------------------------------- hub
  async function renderHub(container) {
    const [queue, mastery, usage] = await Promise.all([
      Api.getQueue(),
      Api.getLearnerTopics(),
      Api.getUsage().catch(() => null),
    ]);

    const cards = queue.reviews.concat(queue.new);
    container.innerHTML = `
      ${renderActivities(usage)}
      <div class="card">
        <h2>Карточки на сегодня</h2>
        ${renderQueueSummary(queue, cards.length)}
      </div>
      <div class="card">
        <h2>Тренажёры по темам</h2>
        ${renderTopicList(mastery)}
      </div>`;

    const start = container.querySelector('[data-role="start-daily"]');
    if (start) {
      start.addEventListener("click", () =>
        Drill.runCards(container, cards, {
          title: "Карточки на сегодня",
          context: "daily",
          onFinish: () => rerender(),
        })
      );
    }
  }

  // Live activities. Each price is the real average from the usage log, so
  // the cost of a click is visible before it is made.
  function renderActivities(usage) {
    const price = (purpose) => {
      const entry = usage && usage.by_purpose[`anthropic:${purpose}`];
      return entry && entry.avg_cost_usd
        ? ` · анализ ≈ ${Math.round(entry.avg_cost_usd * 100 * 10) / 10} ¢`
        : "";
    };
    return `
      <div class="card">
        <h2>Занятия</h2>
        <div class="activity-grid">
          <a class="activity" href="#/roadmap">
            <span class="activity-icon">${Icons.compass}</span>
            <span><strong>Курс A2 → C1</strong><br/>
              <span class="topic-meta">Уроки по порядку: грамматика, лексика, общение · продолжить с того места, где остановились</span></span>
          </a>
          <a class="activity" href="#/record">
            <span class="activity-icon tone-gold">${Icons.mic}</span>
            <span><strong>Монолог</strong><br/>
              <span class="topic-meta">Свободная речь, запись и разбор${escapeHtml(price("analysis"))}</span></span>
          </a>
          <a class="activity" href="#/picture">
            <span class="activity-icon tone-orange">${Icons.image}</span>
            <span><strong>Описание картинки</strong><br/>
              <span class="topic-meta">Голосом или текстом; что упущено и слова для сцены${escapeHtml(price("picture_analysis"))}</span></span>
          </a>
          <a class="activity" href="#/talk">
            <span class="activity-icon tone-gold">${Icons.timer}</span>
            <span><strong>60 секунд</strong><br/>
              <span class="topic-meta">Минута на тему, три раза подряд: темп, паразиты, паузы · без ИИ ассистента</span></span>
          </a>
          <a class="activity" href="#/dictation">
            <span class="activity-icon tone-teal">${Icons.headphones}</span>
            <span><strong>Диктант</strong><br/>
              <span class="topic-meta">Видео с YouTube: набрать на слух по субтитрам · бесплатно</span></span>
          </a>
          <a class="activity" href="#/translate">
            <span class="activity-icon tone-teal">${Icons.translate}</span>
            <span><strong>Перевод текста</strong><br/>
              <span class="topic-meta">Английский текст по уклону или свой → перевод на русский → разбор Sonnet и фразы в карточки</span></span>
          </a>
          <a class="activity" href="#/verbs">
            <span class="activity-icon tone-orange">${Icons.keyboard}</span>
            <span><strong>Неправильные глаголы</strong><br/>
              <span class="topic-meta">Таблица по частоте и тренировка: перевод → три формы · без ИИ ассистента</span></span>
          </a>
          <a class="activity" href="#/shadowing">
            <span class="activity-icon tone-plum">${Icons.speak}</span>
            <span><strong>Shadowing</strong><br/>
              <span class="topic-meta">Прочитать вслух свою «улучшенную версию» и увидеть, что не прозвучало · без ИИ ассистента</span></span>
          </a>
        </div>
      </div>`;
  }

  function renderQueueSummary(queue, total) {
    if (!total) {
      const nothingYet = queue.new_waiting === 0;
      return nothingYet
        ? `<p class="muted">Карточек пока нет. Они появляются из анализа записей («Анализировать»), из ошибок в наборах упражнений и из слов, которые вы отметили после набора.</p>`
        : `<p>На сегодня всё.</p><p class="muted">Новые карточки (${queue.new_waiting}) ждут следующих дней.</p>`;
    }
    return `
      <p>Повторить: <strong>${queue.reviews.length}</strong> · новых: <strong>${queue.new.length}</strong>
        <span class="muted">(лимит новых в день: ${queue.new_limit}, начато сегодня: ${queue.new_started_today}; новых слов: до ${queue.new_words_limit}, начато: ${queue.new_words_started_today})</span></p>
      ${queue.new_waiting ? `<p class="muted">Ещё ${queue.new_waiting} новых ждут следующих дней.</p>` : ""}
      <div class="button-row"><button data-role="start-daily">Начать (${total})</button></div>`;
  }

  // Topics grouped under their area (taxonomy v2), areas in priority order.
  function renderTopicList(mastery) {
    const shown = mastery.topics.filter((row) => row.items || row.weakness_score);
    if (!shown.length) {
      return `<p class="muted">Темы появятся после анализа первой записи.</p>`;
    }
    return mastery.areas
      .map((area) => {
        const rows = shown.filter((row) => row.area === area.key);
        if (!rows.length) return "";
        return `
      <h3 class="area-heading">${escapeHtml(area.label)}</h3>
      <ul class="topic-list">
        ${rows
          .map(
            (row) => `
          <li class="topic-row">
            <div>
              <strong>${escapeHtml(row.label)}</strong><br/>
              <span class="topic-meta">карточек: ${row.items} · к повторению: ${row.due_items}${
                row.accuracy == null ? "" : ` · точность: ${Math.round(row.accuracy * 100)}%`
              }</span>
            </div>
            <a href="#/practice/${encodeURIComponent(row.key)}"><button class="secondary">Открыть</button></a>
          </li>`
          )
          .join("")}
      </ul>`;
      })
      .join("");
  }

  // -------------------------------------------------------------- topic
  async function renderTopic(container, topicKey) {
    const [topicsData, itemsData, allItems] = await Promise.all([
      Api.getTopics(),
      Api.getLearnerItems({ topic: topicKey, dueOnly: true }),
      Api.getLearnerItems({ topic: topicKey }),
    ]);
    const topic = topicsData.topics.find((t) => t.key === topicKey) || {
      key: topicKey,
      label: topicKey,
      description: "",
      area_label: "",
      level: null,
      resources: [],
    };
    const place = [topic.area_label, topic.level ? topic.level.toUpperCase() : ""].filter(Boolean);
    // Topics a set cannot train (delivery, "other") answer 400: no set card.
    const sets = await Api.listSets(topicKey).catch(() => null);
    // A roadmap lesson (its id is the topic key): status, marks, theory.
    const lesson = topic.level ? await Api.getRoadmapLesson(topicKey).catch(() => null) : null;
    const due = itemsData.items;
    const closed = allItems.items.filter((i) => i.state.closed).length;

    container.innerHTML = `
      <p><a href="#/practice">← Занятия</a>${topic.level ? ` · <a href="#/roadmap">Курс</a>` : ""}</p>
      <div class="card">
        ${place.length ? `<p class="topic-meta">${escapeHtml(place.join(" · "))}</p>` : ""}
        <h2>${escapeHtml(topic.label)}</h2>
        ${lesson ? `<div data-role="lesson-status">${renderLessonStatus(lesson)}</div>` : ""}
        <p class="muted">Типичные ошибки: ${escapeHtml(topic.description)}</p>
        ${
          topic.resources.length
            ? `<div class="deep-links">${topic.resources
                .map((r) => `<a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.title)} ↗</a>`)
                .join("")}</div>`
            : ""
        }
      </div>
      ${topic.level ? `<div class="card theory" data-role="theory"><p class="muted">Загрузка теории…</p></div>` : ""}
      ${sets ? `<div class="card" data-role="sets">${renderSets(sets, !!lesson)}</div>` : ""}
      ${lesson ? `<div class="card" data-role="spoken"><p class="muted">Загрузка…</p></div>` : ""}
      <div class="card">
        <h2>Карточки по теме</h2>
        <p class="muted">Всего: ${allItems.count} · к повторению сейчас: ${due.length} · выучено: ${closed}</p>
        ${
          due.length
            ? `<div class="button-row"><button data-role="start-topic">Начать (${due.length})</button></div>`
            : `<p class="muted">${allItems.count ? "Сейчас повторять нечего — карточки вернутся по графику." : "По этой теме карточек пока нет."}</p>`
        }
      </div>`;
    if (sets) wireSets(container, topicKey);
    if (lesson) wireLessonStatus(container, topicKey);
    const spokenCard = container.querySelector('[data-role="spoken"]');
    if (spokenCard) loadSpoken(container, spokenCard, topicKey);
    const theoryCard = container.querySelector('[data-role="theory"]');
    if (theoryCard) loadTheory(container, theoryCard, topicKey);

    const start = container.querySelector('[data-role="start-topic"]');
    if (start) {
      start.addEventListener("click", () =>
        Drill.runCards(container, due, {
          title: topic.label,
          context: "topic",
          onFinish: () => rerender(topicKey),
        })
      );
    }
  }

  // ------------------------------------------------------ lesson status
  const LESSON_STATUS = {
    not_started: "не начат",
    theory: "теория",
    practising: "практикуется",
    mastered: "освоен",
    skipped: "пропущен",
    known: "уже знаю",
  };

  // Where the lesson stands and what «освоен» still needs; the marks are the
  // roadmap's («Уже знаю» / «Пропустить»), never locks.
  function renderLessonStatus(lesson) {
    const need = lesson.mastery_runs;
    const progress =
      lesson.status === "mastered"
        ? "Урок освоен."
        : `До «освоен»: набор на ${Math.round(lesson.pass_score * 100)} % и выше в ${need} разных дня — есть ${Math.min(
            lesson.passed_days,
            need
          )} из ${need}.`;
    const marks =
      lesson.status === "mastered"
        ? ""
        : lesson.status === "known" || lesson.status === "skipped"
        ? `<button class="link-button" data-lesson-mark="">Снять отметку</button>`
        : `<button class="link-button" data-lesson-mark="known">Уже знаю</button>
           <button class="link-button" data-lesson-mark="skipped">Пропустить</button>`;
    return `
      <p class="lesson-status">
        <span class="pill lesson-${escapeHtml(lesson.status)}">${escapeHtml(LESSON_STATUS[lesson.status] || lesson.status)}</span>
        <span class="topic-meta">Урок курса · ${escapeHtml(lesson.level.label)} · ${escapeHtml(lesson.module.title)}</span>
      </p>
      <p class="muted">${progress}${lesson.runs ? ` Наборов пройдено: ${lesson.runs}, лучший результат ${Math.round(lesson.best_score * 100)} %.` : ""}${
        lesson.spoken_tasks
          ? ` Устных заданий: ${lesson.spoken_tasks}, лучшая оценка правила ${Math.round(lesson.best_spoken * 9 + 1)}/10.`
          : ""
      }</p>
      ${marks ? `<div class="button-row compact">${marks}</div>` : ""}`;
  }

  function wireLessonStatus(container, lessonId) {
    const host = container.querySelector('[data-role="lesson-status"]');
    host.querySelectorAll("[data-lesson-mark]").forEach((button) =>
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await Api.markLesson(lessonId, button.dataset.lessonMark || null);
          const lesson = await Api.getRoadmapLesson(lessonId);
          if (root !== container) return;
          host.innerHTML = renderLessonStatus(lesson);
          wireLessonStatus(container, lessonId);
        } catch (err) {
          button.textContent = `Ошибка: ${err.message}`;
        }
      })
    );
  }

  // ------------------------------------------------------- spoken task
  // A lesson's spoken task (Stage 8, R6): tasks Claude wrote for the chosen
  // context («уклон»); «Записать» opens the recorder on one of them.
  async function loadSpoken(container, card, lessonId) {
    card.innerHTML = `
      <h2>Устное задание</h2>
      <p class="muted">1–2 минуты речи на задание, где без правила урока не обойтись. ИИ ассистент разберёт
        запись как обычный монолог и отдельно оценит, как вы применили правило; ошибки попадут в карточки.</p>
      <div data-role="spoken-theme"></div>
      <div data-role="spoken-tasks"><p class="muted">Загрузка…</p></div>`;
    const list = card.querySelector('[data-role="spoken-tasks"]');
    let data;
    try {
      data = await Api.getLessonTasks(lessonId);
    } catch (err) {
      list.innerHTML = `<p class="muted">Не удалось загрузить задания: ${escapeHtml(err.message)}</p>`;
      return;
    }
    let picker = null;
    const draw = () => {
      if (root !== container) return;
      const theme = picker ? picker.current() : null;
      const same = (t) =>
        theme && t.theme && (theme.key ? t.theme.key === theme.key : !t.theme.key && t.theme.label === theme.label);
      const tasks = data.tasks.filter(same);
      const others = data.tasks.length - tasks.length;
      list.innerHTML = `
        ${
          tasks.length
            ? `<ul class="topic-list">${tasks
                .map(
                  (t) => `
              <li class="topic-row">
                <div>
                  <strong>${escapeHtml(t.hint)}</strong><br/>
                  <span>${escapeHtml(t.question)}</span><br/>
                  ${t.use ? `<span class="topic-meta">Используйте: ${escapeHtml(t.use)}</span>` : ""}
                </div>
                <a href="#/speak/${encodeURIComponent(`${lessonId}:${t.id}`)}"><button>Записать</button></a>
              </li>`
                )
                .join("")}</ul>`
            : `<p class="muted">Для этого уклона заданий ещё нет.</p>`
        }
        ${others ? `<p class="muted">В других уклонах ещё ${others} — выберите уклон выше.</p>` : ""}
        ${
          data.anthropic_configured
            ? `<div class="button-row"><button class="${tasks.length ? "secondary" : ""}" data-role="write-tasks">
                 ${tasks.length ? "Ещё задания" : "Придумать задания"} · ≈ 2 ¢</button></div>`
            : `<p class="muted">Чтобы ИИ ассистент придумал задания, нужен ANTHROPIC_API_KEY.</p>`
        }
        <p class="muted" data-role="tasks-status"></p>`;
      const write = list.querySelector('[data-role="write-tasks"]');
      if (write) {
        write.addEventListener("click", async () => {
          const status = list.querySelector('[data-role="tasks-status"]');
          write.disabled = true;
          status.textContent = "ИИ ассистент придумывает задания…";
          try {
            data = await Api.writeLessonTasks(lessonId, picker ? picker.value() : null);
            draw();
          } catch (err) {
            status.textContent = `Не удалось: ${err.message}`;
            write.disabled = false;
          }
        });
      }
    };
    try {
      picker = await ThemePicker.mount(card.querySelector('[data-role="spoken-theme"]'), {
        onChange: draw,
      });
      // A one-off line has no onChange of its own: follow the select and typing.
      card.querySelector('[data-role="theme-select"]').addEventListener("change", draw);
      card.querySelector('[data-role="theme-one-off-input"]').addEventListener("input", draw);
    } catch (err) {
      picker = null;
    }
    draw();
  }

  // ------------------------------------------------------------ theory
  // A lesson's theory (Stage 8, R4): written by Claude on a click, every
  // version kept. Nothing is generated until the button is pressed.
  async function loadTheory(container, card, lessonId, version = null) {
    let data;
    try {
      data = await Api.getTheory(lessonId, version);
    } catch (err) {
      card.innerHTML = `<p class="muted">Не удалось загрузить теорию: ${escapeHtml(err.message)}</p>`;
      return;
    }
    if (root !== container) return;
    card.innerHTML = renderTheory(data);
    wireTheory(container, card, lessonId);
  }

  function renderTheory(data) {
    const price = `≈ ${formatCents(data.cost_estimate_usd)}`;
    const t = data.theory;
    if (!t) {
      return `
        <h2>Теория</h2>
        <p class="muted">ИИ ассистент объяснит правило простыми словами на уровне урока, с примерами и типичными
          ошибками, и разберёт ваши собственные ошибки по этой теме из записей.</p>
        ${
          data.anthropic_configured
            ? `<div class="button-row"><button data-role="theory-write">Написать теорию · ${price}</button></div>`
            : `<p class="muted">Чтобы ИИ ассистент написал теорию, нужен ANTHROPIC_API_KEY.</p>`
        }
        <p class="muted" data-role="theory-status"></p>`;
    }
    const versions = data.versions.length > 1
      ? `<label class="theory-versions">Версия
           <select data-role="theory-version">${data.versions
             .map(
               (v) => `<option value="${v.index}"${v.index === data.version ? " selected" : ""}>${escapeHtml(
                 (v.created_at || "").slice(0, 16).replace("T", " ")
               )}</option>`
             )
             .join("")}</select>
         </label>`
      : "";
    const examples = (list) =>
      list && list.length
        ? `<ul class="theory-examples">${list
            .map(
              (e) => `<li><span class="phrase">${escapeHtml(e.english)}</span>
                <span class="muted"> — ${escapeHtml(e.russian)}</span></li>`
            )
            .join("")}</ul>`
        : "";
    const paragraphs = (text) =>
      String(text || "")
        .split(/\n\s*\n/)
        .map((p) => `<p>${escapeHtml(p.trim())}</p>`)
        .join("");
    return `
      <div class="today-head">
        <h2>Теория</h2>
        ${versions}
      </div>
      <p class="theory-summary">${escapeHtml(t.summary)}</p>
      ${t.sections
        .map(
          (section) => `
        <h3>${escapeHtml(section.heading)}</h3>
        ${paragraphs(section.text)}
        ${examples(section.examples)}`
        )
        .join("")}
      ${
        t.typical_mistakes.length
          ? `<h3>Типичные ошибки</h3>
             ${t.typical_mistakes
               .map(
                 (m) => `
               <div class="issue-card">
                 <p class="quote">✕ ${escapeHtml(m.wrong)}</p>
                 <p class="correction">✓ ${escapeHtml(m.right)}</p>
                 <p class="muted">${escapeHtml(m.why)}</p>
               </div>`
               )
               .join("")}`
          : ""
      }
      ${
        t.own_mistakes.length
          ? `<h3>Ваши ошибки из записей</h3>
             ${t.own_mistakes
               .map(
                 (m) => `
               <div class="issue-card">
                 <p class="quote">✕ «${escapeHtml(m.said)}»</p>
                 <p class="correction">✓ ${escapeHtml(m.correct)}</p>
                 <p>${escapeHtml(m.comment)}</p>
               </div>`
               )
               .join("")}`
          : ""
      }
      ${
        t.remember.length
          ? `<div class="strengths"><strong>Запомнить</strong>
               <ul>${t.remember.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul></div>`
          : ""
      }
      ${
        data.anthropic_configured
          ? `<div class="button-row">
               <button class="secondary" data-role="theory-write">Сгенерировать заново · ${price}</button>
             </div>
             <p class="muted">Новая версия не заменит старую — прежние останутся в списке «Версия».</p>`
          : ""
      }
      <p class="muted" data-role="theory-status"></p>`;
  }

  function wireTheory(container, card, lessonId) {
    const select = card.querySelector('[data-role="theory-version"]');
    if (select) {
      select.addEventListener("change", () => loadTheory(container, card, lessonId, Number(select.value)));
    }
    const write = card.querySelector('[data-role="theory-write"]');
    if (!write) return;
    write.addEventListener("click", async () => {
      const status = card.querySelector('[data-role="theory-status"]');
      write.disabled = true;
      status.textContent = "ИИ ассистент пишет теорию — обычно 20–60 секунд…";
      try {
        await Api.writeTheory(lessonId);
        if (root === container) loadTheory(container, card, lessonId);
      } catch (err) {
        status.textContent = `Не удалось: ${err.message}`;
        write.disabled = false;
      }
    });
  }

  // ------------------------------------------------------ AI exercise sets
  // A set costs money, so the price is on the button and a generated but
  // unstarted set is always offered first (it is already paid for).
  // Every set stays: the history lists them all (the latest SETS_SHOWN at
  // first), and any of them can be redone for free.
  const SETS_SHOWN = 5;

  function renderSets(data, isLesson = false) {
    const waiting = data.sets.find((s) => !s.runs);
    const done = data.sets.filter((s) => s.runs);
    const canGenerate = data.anthropic_configured;
    const newButton = canGenerate
      ? `<button class="${waiting ? "secondary" : ""}" data-set-new>Новый набор · ≈ ${formatCents(data.cost_estimate_usd)}</button>`
      : "";
    return `
      <h2>${isLesson ? "Упражнения урока" : "AI-набор упражнений"}</h2>
      <p class="muted">8–10 новых предложений на ваших ошибках и правилах этой темы, в выбранном уклоне:
        вставить пропущенное, исправить ошибку, перевести с русского. Переводы проверяет ИИ ассистент.
        Ошибки становятся карточками.${
          isLesson ? " Если к уроку написана теория, набор тренирует именно её, на уровне урока." : ""
        } Новый набор не повторяет предложения прежних.</p>
      ${canGenerate ? `<div data-role="set-theme"></div>` : `<p class="muted">Чтобы составлять наборы, нужен ANTHROPIC_API_KEY.</p>`}
      <div class="button-row">
        ${waiting ? `<button data-set-start="${escapeHtml(waiting.id)}">Начать набор (уже составлен)</button>` : ""}
        ${newButton}
      </div>
      <p class="muted" data-role="set-status"></p>
      ${
        done.length
          ? `<h3 class="area-heading">История наборов (${done.length})</h3>
             <ul class="topic-list">${done
              .map(
                (s, i) => `
            <li class="topic-row"${i >= SETS_SHOWN ? ' data-extra-set hidden' : ""}>
              <div>
                <strong>${escapeHtml((s.created_at || "").slice(0, 16).replace("T", " "))}</strong>${
                  s.theme ? ` <span class="topic-meta">· ${escapeHtml(s.theme.label)}</span>` : ""
                }${s.theory_version != null ? ` <span class="topic-meta">· по теории</span>` : ""}<br/>
                <span class="topic-meta">последний раз: ${Math.round(s.last_score * 100)}% · лучший: ${Math.round(
                  s.best_score * 100
                )}% · пройден ${s.runs} раз(а)</span>
              </div>
              <button class="secondary" data-set-start="${escapeHtml(s.id)}">Пройти ещё раз</button>
            </li>`
              )
              .join("")}</ul>
             ${
               done.length > SETS_SHOWN
                 ? `<button class="link-button" data-role="more-sets">Показать все (${done.length})</button>`
                 : ""
             }
             <p class="muted">Повтор набора бесплатный; платной бывает только проверка новых вариантов перевода (доли цента).</p>`
          : ""
      }`;
  }

  function wireSets(container, topicKey) {
    const card = container.querySelector('[data-role="sets"]');
    const status = card.querySelector('[data-role="set-status"]');
    const run = (exerciseSet) => {
      if (root !== container) return; // navigated away while it was loading
      Drill.runSet(container, exerciseSet, { context: "topic", onFinish: () => rerender(topicKey) });
    };

    card.querySelectorAll("[data-set-start]").forEach((button) =>
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          run((await Api.getSet(button.dataset.setStart)).set);
        } catch (err) {
          status.textContent = `Не удалось открыть набор: ${err.message}`;
          button.disabled = false;
        }
      })
    );
    const more = card.querySelector('[data-role="more-sets"]');
    if (more) {
      more.addEventListener("click", () => {
        card.querySelectorAll("[data-extra-set]").forEach((row) => (row.hidden = false));
        more.remove();
      });
    }
    const create = card.querySelector("[data-set-new]");
    const themeHost = card.querySelector('[data-role="set-theme"]');
    let picker = null;
    if (themeHost) {
      ThemePicker.mount(themeHost)
        .then((p) => (picker = p))
        .catch(() => (themeHost.textContent = ""));
    }
    if (create) {
      create.addEventListener("click", async () => {
        card.querySelectorAll("button").forEach((b) => (b.disabled = true));
        status.textContent = "ИИ ассистент составляет набор — обычно 10–30 секунд…";
        try {
          run((await Api.createSet(topicKey, true, picker ? picker.value() : null)).set);
        } catch (err) {
          status.textContent = `Не удалось составить набор: ${err.message}`;
          card.querySelectorAll("button").forEach((b) => (b.disabled = false));
        }
      });
    }
  }

  function dispose() {
    root = null;
  }

  return { render, dispose };
})();
