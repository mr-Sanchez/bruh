// Practice view: the free ($0) drills.
//   #/practice          - hub: today's cards, cloze texts, topics to train
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
    const [queue, mastery, topicsData] = await Promise.all([
      Api.getQueue(),
      Api.getLearnerTopics(),
      Api.getTopics(),
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
      <div class="card">
        <h2>Карточки на сегодня</h2>
        ${renderQueueSummary(queue, cards.length)}
      </div>
      <div class="card">
        <h2>Пропуски в тексте</h2>
        ${renderClozeTable(clozeTopics, clozeTexts)}
      </div>
      <div class="card">
        <h2>Темы</h2>
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
    const due = itemsData.items;
    const closed = allItems.items.filter((i) => i.state.closed).length;

    container.innerHTML = `
      <p><a href="#/practice">← Все упражнения</a></p>
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
      ${
        topic.has_cloze
          ? `<div class="card">
               <h2>Пропуски в тексте</h2>
               ${renderClozeTable([topicKey], { [topicKey]: texts })}
             </div>`
          : ""
      }`;

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

  function dispose() {
    root = null;
  }

  return { render, dispose };
})();
