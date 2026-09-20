// «Занятия»: every activity, plus the free ($0) drills.
//   #/practice          - hub: activities, today's cards, cloze texts, topics
//   #/practice/<topic>  - one topic: its cards, its cloze drill, reference links
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
    const [queue, mastery, topicsData, usage] = await Promise.all([
      Api.getQueue(),
      Api.getLearnerTopics(),
      Api.getTopics(),
      Api.getUsage().catch(() => null),
    ]);
    const clozeTopics = topicsData.topics.filter((t) => t.has_cloze).map((t) => t.key);
    const clozeTexts = {};
    await Promise.all(
      clozeTopics.map(async (key) => {
        clozeTexts[key] = (await Api.getPracticeTexts(key)).texts;
      })
    );

    const cards = queue.reviews.concat(queue.new);
    container.innerHTML = `
      ${renderActivities(usage)}
      <div class="card">
        <h2>Карточки на сегодня</h2>
        ${renderQueueSummary(queue, cards.length)}
      </div>
      <div class="card">
        <h2>Пропуски в тексте</h2>
        ${renderClozeTable(clozeTopics, clozeTexts)}
      </div>
      <div class="card">
        <h2>Тренажёры по темам</h2>
        ${renderTopicList(mastery.topics)}
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
    wireCloze(container, clozeTexts, () => rerender());
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
          <a class="activity" href="#/record">
            <span class="activity-icon" aria-hidden="true">🎙️</span>
            <span><strong>Монолог</strong><br/>
              <span class="topic-meta">Свободная речь, запись и разбор${escapeHtml(price("analysis"))}</span></span>
          </a>
          <a class="activity" href="#/picture">
            <span class="activity-icon" aria-hidden="true">🖼️</span>
            <span><strong>Описание картинки</strong><br/>
              <span class="topic-meta">Голосом или текстом; что упущено и слова для сцены${escapeHtml(price("picture_analysis"))}</span></span>
          </a>
          <a class="activity" href="#/talk">
            <span class="activity-icon" aria-hidden="true">⏱️</span>
            <span><strong>60 секунд</strong><br/>
              <span class="topic-meta">Минута на тему, три раза подряд: темп, паразиты, паузы · без Claude</span></span>
          </a>
          <a class="activity" href="#/shadowing">
            <span class="activity-icon" aria-hidden="true">🗣️</span>
            <span><strong>Shadowing</strong><br/>
              <span class="topic-meta">Прочитать вслух свою «улучшенную версию» и увидеть, что не прозвучало · без Claude</span></span>
          </a>
        </div>
      </div>`;
  }

  function renderQueueSummary(queue, total) {
    if (!total) {
      const nothingYet = queue.new_waiting === 0;
      return nothingYet
        ? `<p class="muted">Карточек пока нет. Они появляются из анализа записей — запишите монолог и нажмите «Анализировать».</p>`
        : `<p>На сегодня всё 🎉</p><p class="muted">Новые карточки (${queue.new_waiting}) ждут следующих дней.</p>`;
    }
    return `
      <p>Повторить: <strong>${queue.reviews.length}</strong> · новых: <strong>${queue.new.length}</strong>
        <span class="muted">(лимит новых в день: ${queue.new_limit}, начато сегодня: ${queue.new_started_today})</span></p>
      ${queue.new_waiting ? `<p class="muted">Ещё ${queue.new_waiting} новых ждут следующих дней.</p>` : ""}
      <div class="button-row"><button data-role="start-daily">Начать (${total})</button></div>`;
  }

  function renderClozeTable(topics, textsByTopic) {
    const sessions = [];
    const seen = new Set();
    topics.forEach((topic) =>
      (textsByTopic[topic] || []).forEach((text) => {
        if (!seen.has(text.session_id)) {
          seen.add(text.session_id);
          sessions.push(text);
        }
      })
    );
    if (!sessions.length) {
      return `<p class="muted">Нужен хотя бы один проанализированный английский монолог — упражнение строится на его «улучшенной версии».</p>`;
    }
    return `
      <p class="muted">Текст — «улучшенная версия» вашего монолога с пропущенными служебными словами.</p>
      <ul class="topic-list">
        ${sessions
          .map(
            (s) => `
          <li class="topic-row">
            <div>
              <strong>${escapeHtml(s.recorded_at.slice(0, 16).replace("T", " "))}</strong><br/>
              <span class="topic-meta">${escapeHtml(s.text.slice(0, 90))}…</span>
            </div>
            <div class="button-row compact">
              ${topics.map((topic) => clozeButton(topic, textsByTopic[topic], s.session_id)).join("")}
            </div>
          </li>`
          )
          .join("")}
      </ul>`;
  }

  function clozeButton(topic, texts, sessionId) {
    const text = (texts || []).find((t) => t.session_id === sessionId);
    if (!text || !text.gaps) return "";
    const best = text.best_score == null ? "" : ` · ${Math.round(text.best_score * 100)}%`;
    return `<button class="secondary" data-cloze-topic="${topic}" data-cloze-session="${escapeHtml(sessionId)}">
      ${escapeHtml(Drill.CLOZE_TITLES[topic] || topic)}${best}</button>`;
  }

  function wireCloze(container, textsByTopic, onFinish) {
    container.querySelectorAll("[data-cloze-topic]").forEach((button) => {
      button.addEventListener("click", () => {
        const topic = button.dataset.clozeTopic;
        const text = textsByTopic[topic].find((t) => t.session_id === button.dataset.clozeSession);
        Drill.runCloze(container, text, topic, { onFinish });
      });
    });
  }

  function renderTopicList(rows) {
    const shown = rows.filter((row) => row.items || row.weakness_score);
    if (!shown.length) {
      return `<p class="muted">Темы появятся после анализа первой записи.</p>`;
    }
    return `
      <ul class="topic-list">
        ${shown
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
      resources: [],
      has_cloze: false,
    };
    const texts = topic.has_cloze ? (await Api.getPracticeTexts(topicKey)).texts : [];
    // Topics a set cannot train (fillers, "other") answer 400: no set card.
    const sets = await Api.listSets(topicKey).catch(() => null);
    const due = itemsData.items;
    const closed = allItems.items.filter((i) => i.state.closed).length;

    container.innerHTML = `
      <p><a href="#/practice">← Занятия</a></p>
      <div class="card">
        <h2>${escapeHtml(topic.label)}</h2>
        <p class="muted">${escapeHtml(topic.description)}</p>
        ${
          topic.resources.length
            ? `<div class="deep-links">${topic.resources
                .map((r) => `<a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.title)} ↗</a>`)
                .join("")}</div>`
            : ""
        }
      </div>
      <div class="card">
        <h2>Карточки по теме</h2>
        <p class="muted">Всего: ${allItems.count} · к повторению сейчас: ${due.length} · выучено: ${closed}</p>
        ${
          due.length
            ? `<div class="button-row"><button data-role="start-topic">Начать (${due.length})</button></div>`
            : `<p class="muted">${allItems.count ? "Сейчас повторять нечего — карточки вернутся по графику." : "По этой теме карточек пока нет."}</p>`
        }
      </div>
      ${sets ? `<div class="card" data-role="sets">${renderSets(sets)}</div>` : ""}
      ${
        topic.has_cloze
          ? `<div class="card">
               <h2>Пропуски в тексте</h2>
               ${renderClozeTable([topicKey], { [topicKey]: texts })}
             </div>`
          : ""
      }`;
    if (sets) wireSets(container, topicKey);

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
    wireCloze(container, { [topicKey]: texts }, () => rerender(topicKey));
  }

  // ------------------------------------------------------ AI exercise sets
  // A set costs money, so the price is on the button and a generated but
  // unstarted set is always offered first (it is already paid for).
  function renderSets(data) {
    const waiting = data.sets.find((s) => !s.runs);
    const done = data.sets.filter((s) => s.runs).slice(0, 5);
    const canGenerate = data.anthropic_configured;
    const newButton = canGenerate
      ? `<button class="${waiting ? "secondary" : ""}" data-set-new>Новый набор · ≈ ${formatCents(data.cost_estimate_usd)}</button>`
      : "";
    return `
      <h2>AI-набор упражнений</h2>
      <p class="muted">8–10 новых предложений на ваших ошибках и правилах этой темы, в рабочем контексте:
        вставить пропущенное, исправить ошибку, перевести с русского. Переводы проверяет Claude.
        Ошибки становятся карточками.</p>
      ${canGenerate ? "" : `<p class="muted">Чтобы составлять наборы, нужен ANTHROPIC_API_KEY.</p>`}
      <div class="button-row">
        ${waiting ? `<button data-set-start="${escapeHtml(waiting.id)}">Начать набор (уже составлен)</button>` : ""}
        ${newButton}
      </div>
      <p class="muted" data-role="set-status"></p>
      ${
        done.length
          ? `<ul class="topic-list">${done
              .map(
                (s) => `
            <li class="topic-row">
              <div>
                <strong>${escapeHtml((s.created_at || "").slice(0, 16).replace("T", " "))}</strong><br/>
                <span class="topic-meta">последний раз: ${Math.round(s.last_score * 100)}% · лучший: ${Math.round(
                  s.best_score * 100
                )}% · пройден ${s.runs} раз(а)</span>
              </div>
              <button class="secondary" data-set-start="${escapeHtml(s.id)}">Пройти ещё раз</button>
            </li>`
              )
              .join("")}</ul>
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
    const create = card.querySelector("[data-set-new]");
    if (create) {
      create.addEventListener("click", async () => {
        card.querySelectorAll("button").forEach((b) => (b.disabled = true));
        status.textContent = "Claude составляет набор — обычно 10–30 секунд…";
        try {
          run((await Api.createSet(topicKey, true)).set);
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
