// «Неправильные глаголы»: the table (by frequency) and a typed drill. Free, no Claude.
//   #/verbs        - the table with a search box
//   #/verbs/drill  - pick how many verbs, then type the three forms of each
// Answers are graded (and logged) on the server; the drill picks last
// mistakes first, then new verbs from the top of the frequency list.
window.Views = window.Views || {};

Views.verbs = (() => {
  const COUNTS = [5, 10, 20, 30];
  const FORM_LABELS = ["Infinitive", "Past Simple", "Past Participle"];
  let root = null;

  async function render(container, mode) {
    root = container;
    container.innerHTML = `<div class="card"><p class="muted">Загрузка...</p></div>`;
    try {
      if (mode === "drill") await renderDrillSetup(container);
      else await renderTable(container);
    } catch (err) {
      container.innerHTML = `<div class="card"><p class="muted">Не удалось загрузить: ${escapeHtml(err.message)}</p></div>`;
    }
  }

  function header(mode) {
    return `
      <p><a href="#/practice">← Занятия</a></p>
      <h1 class="page-title">Неправильные глаголы</h1>
      <div class="button-row verb-modes">
        <a href="#/verbs"><button class="${mode === "table" ? "" : "secondary"}">Таблица</button></a>
        <a href="#/verbs/drill"><button class="${mode === "drill" ? "" : "secondary"}">Тренировка</button></a>
      </div>`;
  }

  // ---------------------------------------------------------------- table
  async function renderTable(container) {
    const data = await Api.getIrregularVerbs();
    const s = data.summary;
    container.innerHTML = `
      ${header("table")}
      <div class="card">
        <p class="muted">По частоте употребления: самые нужные — сверху. Тренировано: ${s.trained} из ${s.total} · выучено: ${s.learned}${
          s.to_fix ? ` · ошибка в последний раз: ${s.to_fix}` : ""
        }</p>
        <input type="search" class="verb-search" data-role="search" placeholder="Поиск: go, went, идти…" autocomplete="off" />
        <div class="table-wrap">
          <table class="verb-table">
            <thead><tr><th class="num">#</th><th>Infinitive</th><th>Past Simple</th><th>Past Participle</th><th>Перевод</th><th></th></tr></thead>
            <tbody>${data.verbs.map(renderRow).join("")}</tbody>
          </table>
        </div>
        <p class="muted" data-role="empty" hidden>Ничего не найдено.</p>
      </div>`;

    const search = container.querySelector('[data-role="search"]');
    const rows = Array.from(container.querySelectorAll("tbody tr"));
    const empty = container.querySelector('[data-role="empty"]');
    search.addEventListener("input", () => {
      const query = search.value.trim().toLowerCase();
      let shown = 0;
      rows.forEach((row) => {
        const hit = !query || row.dataset.search.includes(query);
        row.hidden = !hit;
        if (hit) shown += 1;
      });
      empty.hidden = shown > 0;
    });
    search.focus();
  }

  function renderRow(verb) {
    const forms = [verb.base, verb.past, verb.participle];
    const haystack = forms.flat().concat(verb.translation).join(" ").toLowerCase();
    return `
      <tr data-search="${escapeHtml(haystack)}">
        <td class="num muted">${verb.rank}</td>
        ${forms.map((variants) => `<td class="verb-form">${escapeHtml(variants.join(" / "))}</td>`).join("")}
        <td>${escapeHtml(verb.translation)}</td>
        <td>${renderMark(verb.stats)}</td>
      </tr>`;
  }

  function renderMark(stats) {
    if (!stats) return "";
    if (stats.learned) return `<span class="verb-mark is-learned" title="Выучен: ${stats.streak} раза подряд верно">${Icons.svg("check", 16)}</span>`;
    if (stats.last_correct === false) return `<span class="verb-mark is-wrong" title="В последний раз с ошибкой">•</span>`;
    return `<span class="verb-mark" title="Верно ${stats.correct} из ${stats.attempts}">•</span>`;
  }

  // ---------------------------------------------------------------- drill
  async function renderDrillSetup(container) {
    const data = await Api.getIrregularVerbs();
    const s = data.summary;
    container.innerHTML = `
      ${header("drill")}
      <div class="card">
        <h2>Сколько глаголов?</h2>
        <p class="muted">Показывается перевод — нужно написать три формы. Сначала идут глаголы с ошибкой в прошлый раз, потом новые — от самых частых. Несколько вариантов («learned / learnt») можно писать через «/», достаточно одного.</p>
        <div class="button-row">
          ${COUNTS.map((n) => `<button class="secondary" data-count="${n}">${n}</button>`).join("")}
        </div>
        <p class="muted">Тренировано: ${s.trained} из ${s.total} · выучено: ${s.learned}${s.to_fix ? ` · к исправлению: ${s.to_fix}` : ""}</p>
      </div>`;
    container.querySelectorAll("[data-count]").forEach((button) =>
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const drill = await Api.getIrregularVerbDrill(Number(button.dataset.count));
          runDrill(container, shuffle(drill.verbs));
        } catch (err) {
          button.disabled = false;
          alertIn(container, err.message);
        }
      })
    );
  }

  function runDrill(container, verbs) {
    const results = [];
    let index = 0;

    function showVerb() {
      const verb = verbs[index];
      container.innerHTML = `
        ${header("drill")}
        <div class="card verb-drill">
          <p class="topic-meta">${index + 1} из ${verbs.length}</p>
          <div class="word-front">${escapeHtml(verb.translation)}</div>
          <form data-role="form" autocomplete="off">
            <div class="verb-inputs">
              ${FORM_LABELS.map(
                (label, i) => `
                <label>${label}
                  <input type="text" data-form="${i}" spellcheck="false" autocapitalize="off" />
                  <span class="verb-expected" data-expected="${i}"></span>
                </label>`
              ).join("")}
            </div>
            <div class="button-row">
              <button type="submit" data-role="check"><span>Проверить</span>${keyHint("Enter")}</button>
            </div>
          </form>
        </div>`;

      const form = container.querySelector('[data-role="form"]');
      const inputs = Array.from(form.querySelectorAll("[data-form]"));
      inputs[0].focus();
      // Enter moves to the next form; on the last one it checks.
      inputs.slice(0, 2).forEach((input, i) =>
        input.addEventListener("keydown", (event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            inputs[i + 1].focus();
          }
        })
      );
      let checked = false;
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (checked) return next();
        const answers = inputs.map((input) => input.value);
        if (answers.every((a) => !a.trim())) return inputs[0].focus();
        const button = form.querySelector('[data-role="check"]');
        button.disabled = true;
        try {
          const graded = await Api.checkIrregularVerb(verb.key, answers);
          checked = true;
          results.push({ verb, graded });
          showVerdict(form, inputs, graded);
          button.firstElementChild.textContent = index + 1 < verbs.length ? "Дальше" : "Итог";
          button.disabled = false;
          button.focus();
        } catch (err) {
          button.disabled = false;
          alertIn(form, err.message);
        }
      });
    }

    function next() {
      index += 1;
      if (index < verbs.length) showVerb();
      else showSummary();
    }

    function showSummary() {
      const wrong = results.filter((r) => !r.graded.correct);
      const right = results.length - wrong.length;
      container.innerHTML = `
        ${header("drill")}
        <div class="card">
          <h2>Верно: ${right} из ${results.length}</h2>
          ${
            wrong.length
              ? `<p class="muted">Ошибки — они первыми попадут в следующую тренировку:</p>
                 <ul class="verb-mistakes">${wrong.map(renderMistake).join("")}</ul>`
              : `<p>Без ошибок.</p>`
          }
          <div class="button-row">
            ${wrong.length ? `<button data-role="retry">Повторить ошибки (${wrong.length})</button>` : ""}
            <button class="secondary" data-role="again">Новая тренировка</button>
            <a href="#/verbs"><button class="secondary">Таблица</button></a>
          </div>
        </div>`;
      const retry = container.querySelector('[data-role="retry"]');
      if (retry) retry.addEventListener("click", () => runDrill(container, shuffle(wrong.map((r) => r.verb))));
      container.querySelector('[data-role="again"]').addEventListener("click", () => rerender());
      (retry || container.querySelector('[data-role="again"]')).focus();
    }

    showVerb();
  }

  function showVerdict(form, inputs, graded) {
    graded.forms.forEach((result, i) => {
      const input = inputs[i];
      input.readOnly = true;
      input.classList.add(result.correct ? "is-correct" : "is-wrong");
      const expected = form.querySelector(`[data-expected="${i}"]`);
      expected.textContent = result.correct && result.expected.length === 1 ? "" : result.expected.join(" / ");
    });
  }

  function renderMistake({ verb, graded }) {
    const forms = graded.forms
      .map((f) =>
        f.correct
          ? escapeHtml(f.expected.join(" / "))
          : `<span class="correction">${escapeHtml(f.expected.join(" / "))}</span>${
              f.answer.trim() ? ` <s class="muted">${escapeHtml(f.answer)}</s>` : ""
            }`
      )
      .join(" — ");
    return `<li><strong>${escapeHtml(verb.translation)}</strong>: ${forms}</li>`;
  }

  function alertIn(parent, message) {
    let box = parent.querySelector('[data-role="error"]');
    if (!box) {
      box = document.createElement("p");
      box.className = "muted";
      box.dataset.role = "error";
      parent.appendChild(box);
    }
    box.textContent = `Ошибка: ${message}`;
  }

  function shuffle(items) {
    const copy = items.slice();
    for (let i = copy.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      [copy[i], copy[j]] = [copy[j], copy[i]];
    }
    return copy;
  }

  function rerender() {
    if (root) render(root, "drill");
  }

  return { render };
})();
