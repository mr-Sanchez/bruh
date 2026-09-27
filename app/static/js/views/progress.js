// Progress view: the course (per level, lessons in progress), speech scores
// over time (one small chart per skill), topic mastery (speech + exercises),
// spoken-drill measurements, the dictation's tricky words, the mistake bank
// and what the APIs cost.
window.Views = window.Views || {};

Views.progress = (() => {
  async function render(container) {
    container.innerHTML = `<div class="card"><p class="muted">Загрузка...</p></div>`;
    let progress;
    let mastery;
    let fixes;
    let usage;
    let talks;
    let dictation;
    let course;
    try {
      [progress, mastery, fixes, usage, talks, dictation, course] = await Promise.all([
        Api.getProgress(),
        Api.getLearnerTopics(),
        Api.getLearnerItems({ kind: "fix" }),
        Api.getUsage().catch(() => null),
        Api.getTalks().catch(() => null),
        Api.getDictationStats().catch(() => null),
        Api.getRoadmap().catch(() => null),
      ]);
    } catch (err) {
      container.innerHTML = `<div class="card"><p class="muted">Не удалось загрузить прогресс: ${escapeHtml(err.message)}</p></div>`;
      return;
    }

    if (!progress.sessions_analyzed && !progress.score_history.length) {
      container.innerHTML = `<div class="card"><h2>Прогресс</h2><div class="empty-state">
        Пока нет данных. Запишите и проанализируйте монолог — здесь появятся оценки, темы и банк ошибок.
      </div></div>${renderCourse(course)}`;
      return;
    }

    const labels = {};
    mastery.topics.forEach((t) => (labels[t.key] = t.label));
    container.innerHTML = `
      ${renderCourse(course)}
      <div class="card">
        <h2>Оценки речи</h2>
        <p class="muted">Проанализировано записей: ${progress.sessions_analyzed}. Оценки 1–10 по каждой записи.</p>
        ${Charts.scoreTiles(progress.score_history)}
        <div class="chart-grid" id="charts"></div>
        ${renderScoreTable(progress.score_history)}
      </div>
      <div class="card">
        <h2>Темы</h2>
        <p class="muted">«В речи» — сколько раз тема встречалась в записях; «точность» — последние 20 ответов в упражнениях.</p>
        ${renderTopics(mastery, progress.topics)}
      </div>
      ${renderTalks(talks)}
      ${renderDictation(dictation)}
      <div class="card">
        <h2>Банк ошибок</h2>
        ${renderMistakes(fixes.items, labels)}
      </div>
      ${renderUsage(usage)}`;

    const charts = container.querySelector("#charts");
    const history = Charts.sortedHistory(progress.score_history);
    if (history.length < 2) {
      charts.innerHTML = `<p class="muted">Графики появятся после второй оценённой записи.</p>`;
      return;
    }
    Charts.SKILLS.forEach(([key, title]) => {
      const host = document.createElement("div");
      charts.appendChild(host);
      Charts.lineChart(
        host,
        history.map((e) => ({ label: e.at.slice(0, 10), value: e[key] })),
        { title }
      );
    });
  }

  // The course by level (mastered + «уже знаю» out of all), and the lessons
  // being worked on now, so progress in the roadmap sits next to the scores.
  function renderCourse(course) {
    if (!course) return "";
    const touched = course.counts.mastered + course.counts.known + course.counts.practising + course.counts.theory;
    const inWork = course.levels
      .flatMap((level) => level.modules.flatMap((m) => m.lessons))
      .filter((l) => l.status === "practising" || l.status === "theory");
    return `
      <div class="card">
        <div class="today-head">
          <h2>Курс</h2>
          <a href="#/roadmap">Открыть курс →</a>
        </div>
        ${
          touched
            ? ""
            : `<p class="muted">Уроки ещё не начаты. Курс A2 → C1 — на вкладке «Занятия».</p>`
        }
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr><th>Уровень</th><th>Освоено / уже знаю</th><th>В работе</th><th></th></tr></thead>
            <tbody>${course.levels
              .map((level) => {
                const lessons = level.modules.flatMap((m) => m.lessons);
                const working = lessons.filter((l) => l.status === "practising" || l.status === "theory").length;
                return `<tr>
                  <td>${escapeHtml(level.label)}</td>
                  <td class="num">${level.done} из ${level.total}</td>
                  <td class="num">${working}</td>
                  <td><div class="progress-bar"><div style="width: ${Math.round((level.done / level.total) * 100)}%"></div></div></td>
                </tr>`;
              })
              .join("")}</tbody>
          </table>
        </div>
        ${
          inWork.length
            ? `<h3 class="area-heading">Сейчас в работе</h3>
               <ul class="topic-list">${inWork
                 .map(
                   (l) => `
                 <li class="topic-row">
                   <div><strong>${escapeHtml(l.label)}</strong><br/>
                     <span class="topic-meta">наборов: ${l.runs}${
                       l.best_score != null ? `, лучший ${Math.round(l.best_score * 100)} %` : ""
                     } · дней с ≥ ${Math.round(course.pass_score * 100)} %: ${l.passed_days} из ${course.mastery_runs}${
                       l.spoken_tasks ? ` · устных заданий: ${l.spoken_tasks}` : ""
                     }</span></div>
                   <a href="#/practice/${encodeURIComponent(l.id)}"><button class="secondary">К уроку</button></a>
                 </li>`
                 )
                 .join("")}</ul>`
            : ""
        }
      </div>`;
  }

  function renderScoreTable(history) {
    const entries = Charts.sortedHistory(history).reverse();
    if (!entries.length) return "";
    return `
      <details class="table-view">
        <summary>Таблица оценок</summary>
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr><th>Запись</th>${Charts.SKILLS.map(([, label]) => `<th>${label}</th>`).join("")}</tr></thead>
            <tbody>${entries
              .map(
                (e) => `<tr>
                  <td><a href="#/session/${encodeURIComponent(e.session_id)}">${escapeHtml(e.at.slice(0, 16).replace("T", " "))}</a></td>
                  ${Charts.SKILLS.map(([key]) => `<td class="num">${e[key] == null ? "—" : escapeHtml(e[key])}</td>`).join("")}
                </tr>`
              )
              .join("")}</tbody>
          </table>
        </div>
      </details>`;
  }

  // Two levels (taxonomy v2): an area row with its totals, then its topics.
  function renderTopics(mastery, speechTopics) {
    const speech = {};
    speechTopics.forEach((t) => (speech[t.key] = t.count));
    const active = (row) => row.items || row.weakness_score || row.recent_attempts;
    const shown = mastery.topics.filter(active);
    if (!shown.length) return `<p class="muted">Темы появятся после анализа первой записи.</p>`;
    const accuracy = (row) => (row.accuracy == null ? "—" : `${Math.round(row.accuracy * 100)}%`);
    const cells = (row, count) => `
                <td class="num">${count}</td>
                <td class="num">${accuracy(row)}</td>
                <td class="num">${row.closed_items} / ${row.items}</td>
                <td class="num">${row.due_items}</td>`;
    const body = mastery.areas
      .filter(active)
      .map((area) => {
        const rows = shown.filter((row) => row.area === area.key);
        const count = rows.reduce((sum, row) => sum + (speech[row.key] || 0), 0);
        return `<tr class="area-row"><th scope="rowgroup">${escapeHtml(area.label)}</th>${cells(area, count)}</tr>
          ${rows
            .map(
              (row) => `<tr>
                <td class="topic-cell"><a href="#/practice/${encodeURIComponent(row.key)}">${escapeHtml(row.label)}</a></td>
                ${cells(row, speech[row.key] || 0)}
              </tr>`
            )
            .join("")}`;
      })
      .join("");
    return `
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th>Раздел / тема</th><th>В речи</th><th>Точность</th><th>Выучено</th><th>К повторению</th></tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  // «60 секунд»: every series, its first (spontaneous) take against its last.
  // Dictation is not part of the topic taxonomy (it trains listening and
  // spelling), so it gets its own numbers plus the words that keep going
  // wrong - each with the same free references as a phrase card.
  function renderDictation(stats) {
    if (!stats || !stats.sentences) return "";
    const words = stats.tricky_words || [];
    return `
      <div class="card">
        <h2>Диктант</h2>
        <p class="muted">Уроков: ${stats.lessons} · надиктовано предложений: ${stats.sentences}
          · слов: ${stats.words}${
            stats.accuracy == null ? "" : ` · точность ${Math.round(stats.accuracy * 100)}%`
          } · подсказок: ${stats.hints}</p>
        ${
          words.length
            ? `<h3>Сложные слова</h3>
               <p class="muted">Не расслышаны или набраны с подсказкой не меньше двух раз.</p>
               <ul class="tricky-words">
                 ${words.map(trickyWord).join("")}
               </ul>`
            : `<p class="muted">Слов, которые стабильно не даются, пока нет.</p>`
        }
      </div>`;
  }

  function trickyWord(row) {
    const q = encodeURIComponent(row.word);
    return `
      <li>
        <span class="phrase">${escapeHtml(row.word)}</span>
        <span class="muted"> — не расслышано ${row.missed}, с подсказкой ${row.hinted}</span>
        <span class="deep-links">
          <a href="https://youglish.com/pronounce/${q}/english" target="_blank" rel="noopener">YouGlish ↗</a>
          <a href="https://dictionary.cambridge.org/search/english/?q=${q}" target="_blank" rel="noopener">Cambridge ↗</a>
        </span>
      </li>`;
  }

  function renderTalks(talks) {
    const series = ((talks && talks.series) || []).filter((s) =>
      s.rounds.some((r) => r.metrics && r.metrics.words)
    );
    if (!series.length) return "";
    const cell = (round, pick) =>
      round && round.metrics && pick(round.metrics) != null ? escapeHtml(pick(round.metrics)) : "—";
    return `
      <div class="card">
        <h2>Речевая разминка</h2>
        <p class="muted">«60 секунд»: первая попытка — спонтанная речь (она идёт в зачёт темы
          «беглость»), последняя — после двух повторов той же мысли.</p>
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr><th>Дата</th><th>Попыток</th>
              <th>Темп 1 → N</th><th>Паразиты/мин 1 → N</th><th>Паузы 1 → N</th></tr></thead>
            <tbody>${series
              .map((entry) => {
                const done = entry.rounds.filter((r) => r.metrics && r.metrics.words);
                const first = done[0];
                const last = done[done.length - 1];
                const pair = (pick) => `${cell(first, pick)} → ${cell(last, pick)}`;
                return `<tr>
                  <td><a href="#/session/${encodeURIComponent(first.session_id)}">${escapeHtml(
                    entry.started_at.slice(0, 10)
                  )}</a></td>
                  <td class="num">${done.length}</td>
                  <td class="num">${pair((m) => m.wpm)}</td>
                  <td class="num">${pair((m) => m.fillers_per_min)}</td>
                  <td class="num">${pair((m) => m.long_pauses)}</td>
                </tr>`;
              })
              .join("")}</tbody>
          </table>
        </div>
      </div>`;
  }

  function stateText(state) {
    if (state.closed) return "выучено";
    if (state.is_new) return "новая";
    return `коробка ${state.box} из 5`;
  }

  // Every concrete mistake, grouped by topic: still-open ones first (lowest
  // Leitner box first), learned ones last.
  function renderMistakes(items, labels) {
    if (!items.length) return `<p class="muted">Ошибок пока нет.</p>`;
    const groups = {};
    items.forEach((item) => (groups[item.topic || "other"] = groups[item.topic || "other"] || []).push(item));
    const rank = (item) => (item.state.closed ? 9 : item.state.is_new ? 0 : item.state.box);
    const closed = items.filter((i) => i.state.closed).length;
    return `
      <p class="muted">Всего: ${items.length} · выучено: ${closed}. Ошибка снова появится здесь «новой», если повторится в речи.</p>
      ${Object.keys(groups)
        .sort((a, b) => groups[b].length - groups[a].length)
        .map(
          (topic) => `
        <details class="mistake-group">
          <summary><strong>${escapeHtml(labels[topic] || topic)}</strong> <span class="muted">· ${groups[topic].length}</span></summary>
          <ul class="mistake-list">${groups[topic]
            .sort((a, b) => rank(a) - rank(b))
            .map(
              (item) => `
            <li class="${item.state.closed ? "is-closed" : ""}">
              <span class="quote">«${escapeHtml(item.content.quote)}»</span> →
              <span class="correction">${escapeHtml(item.content.correction)}</span>
              <span class="topic-meta"> · ${stateText(item.state)}</span>
            </li>`
            )
            .join("")}</ul>
        </details>`
        )
        .join("")}`;
  }

  function renderUsage(usage) {
    if (!usage || !usage.total_usd) return "";
    const cents = (usd) => `${(usd * 100).toFixed(1)} ¢`;
    const analysis = usage.by_purpose["anthropic:analysis"];
    const picture = usage.by_purpose["anthropic:picture_analysis"];
    const sets = usage.by_purpose["anthropic:exercise_set"];
    const grading = usage.by_purpose["anthropic:exercise_grading"];
    return `
      <div class="card">
        <h2>Расходы на API</h2>
        <p>Всего: <strong>$${usage.total_usd.toFixed(2)}</strong>
          <span class="muted">· ИИ ассистент ${cents(usage.by_service_usd.anthropic || 0)}
          · Deepgram ${cents(usage.by_service_usd.deepgram || 0)}</span></p>
        ${analysis ? `<p class="muted">Анализ монолога в среднем: ${cents(analysis.avg_cost_usd)} (${analysis.calls} шт.)</p>` : ""}
        ${picture ? `<p class="muted">Анализ описания картинки в среднем: ${cents(picture.avg_cost_usd)} (${picture.calls} шт.)</p>` : ""}
        ${
          usage.by_purpose["deepgram:speech_drill"]
            ? `<p class="muted">Речевые тренажёры (только Deepgram): ${cents(
                usage.by_purpose["deepgram:speech_drill"].cost_usd
              )} за ${usage.by_purpose["deepgram:speech_drill"].calls} записей</p>`
            : ""
        }
        ${sets ? `<p class="muted">AI-набор в среднем: ${cents(sets.avg_cost_usd)} за составление (${sets.calls} шт.)${
          grading ? ` + ${cents(grading.avg_cost_usd)} за проверку переводов` : ""
        }</p>` : ""}
      </div>`;
  }

  function dispose() {}

  return { render, dispose };
})();
