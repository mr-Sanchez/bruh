// «Тест модуля» (#/moduletest/<module>): a short entry test over a roadmap
// module (Stage 8, R7). One choice and one gap per lesson; answers are
// checked on the server (the page never has them before submitting), and
// the lessons passed can be marked «уже знаю» in one click. Claude writes a
// test only on a click; a written test is retaken for free.
window.Views = window.Views || {};

Views.moduletest = (() => {
  const STATUS = {
    not_started: "не начат",
    theory: "теория",
    practising: "практикуется",
    mastered: "освоен",
    skipped: "пропущен",
    known: "уже знаю",
  };
  let root = null;
  let moduleKey = null;

  async function render(container, param) {
    root = container;
    moduleKey = decodeURIComponent(param || "");
    container.innerHTML = `<div class="card"><p class="muted">Загрузка...</p></div>`;
    try {
      draw(container, await Api.getModuleTest(moduleKey), false);
    } catch (err) {
      container.innerHTML = `<div class="card"><p class="muted">Не удалось загрузить тест: ${escapeHtml(err.message)}</p></div>`;
    }
  }

  // `retake`: show the questions even when the test already has a run.
  function draw(container, data, retake) {
    if (root !== container) return;
    const test = data.test;
    const showResults = test && test.last_run && !retake;
    container.innerHTML = `
      <p><a href="#/roadmap">← Курс</a></p>
      <div class="card">
        <p class="topic-meta">${escapeHtml(data.module.level)}</p>
        <h2>Тест модуля · ${escapeHtml(data.module.title)}</h2>
        <p class="muted">По два вопроса на каждый урок модуля. Урок засчитывается как известный, если оба ответа верны —
          такие уроки можно сразу отметить «уже знаю», а остальные тест покажет ссылками.</p>
        ${renderTestPicker(data)}
        ${
          test
            ? ""
            : data.anthropic_configured
            ? `<div class="button-row"><button data-role="new-test">Составить тест · ≈ ${formatCents(data.cost_estimate_usd)}</button></div>`
            : `<p class="muted">Чтобы Claude составил тест, нужен ANTHROPIC_API_KEY.</p>`
        }
        <p class="muted" data-role="status"></p>
      </div>
      ${test ? (showResults ? renderResults(data) : renderQuestions(data)) : ""}`;
    wire(container, data);
  }

  function renderTestPicker(data) {
    if (data.tests.length < 2) return "";
    return `
      <label class="theory-versions">Тест
        <select data-role="test-select">${data.tests
          .map(
            (t) => `<option value="${escapeHtml(t.id)}"${t.id === data.test.id ? " selected" : ""}>${escapeHtml(
              (t.created_at || "").slice(0, 16).replace("T", " ")
            )}${t.best_score != null ? ` · лучший ${Math.round(t.best_score * 100)} %` : ""}</option>`
          )
          .join("")}</select>
      </label>`;
  }

  function lessonLabel(data, key) {
    const lesson = data.lessons.find((l) => l.key === key);
    return lesson ? lesson.label : key;
  }

  function renderQuestions(data) {
    return `
      <div class="card">
        <form data-role="test-form">
          ${data.test.questions
            .map((q, i) => {
              const head = `<p class="topic-meta">${i + 1}. ${escapeHtml(lessonLabel(data, q.lesson))}</p>`;
              if (q.type === "choice") {
                return `
                <div class="test-question">
                  ${head}
                  <p>${escapeHtml(q.question)}</p>
                  <div class="test-options">${q.options
                    .map(
                      (option, n) => `
                    <label><input type="radio" name="${escapeHtml(q.id)}" value="${n}" /> ${escapeHtml(option)}</label>`
                    )
                    .join("")}</div>
                </div>`;
              }
              return `
                <div class="test-question">
                  ${head}
                  <p class="gap-sentence">${escapeHtml(q.before)}<input class="gap-input" name="${escapeHtml(
                    q.id
                  )}" autocomplete="off" spellcheck="false" />${escapeHtml(q.after)}
                    ${q.hint ? `<span class="muted">${escapeHtml(q.hint)}</span>` : ""}</p>
                </div>`;
            })
            .join("")}
          <div class="button-row"><button type="submit">Проверить</button></div>
        </form>
      </div>`;
  }

  function renderResults(data) {
    const run = data.test.last_run;
    const byQuestion = Object.fromEntries(data.test.questions.map((q) => [q.id, q]));
    const lessons = data.lessons.map((lesson) => ({ ...lesson, result: run.by_lesson[lesson.key] }));
    const passed = lessons.filter((l) => l.result && l.result.known);
    const toMark = passed.filter((l) => l.status !== "known" && l.status !== "mastered");
    const missed = lessons.filter((l) => l.result && !l.result.known);
    return `
      <div class="card">
        <div class="today-head">
          <h2>Результат: ${Math.round(run.score * 100)} %</h2>
          <span class="topic-meta">${escapeHtml((run.at || "").slice(0, 16).replace("T", " "))}</span>
        </div>
        <ul class="topic-list">${lessons
          .map(
            (l) => `
          <li class="topic-row">
            <div>
              <strong>${escapeHtml(l.label)}</strong><br/>
              <span class="topic-meta">${
                l.result ? `верно ${l.result.right} из ${l.result.total}` : "не проверялся"
              } · сейчас: ${escapeHtml(STATUS[l.status] || l.status || "")}</span>
            </div>
            ${
              l.result && l.result.known
                ? `<span class="pill lesson-known">знаете</span>`
                : `<a href="#/practice/${encodeURIComponent(l.key)}"><button class="secondary">К уроку</button></a>`
            }
          </li>`
          )
          .join("")}</ul>
        <div class="button-row">
          ${
            toMark.length
              ? `<button data-role="mark-known" data-lessons="${escapeHtml(toMark.map((l) => l.key).join(","))}">
                   Отметить «уже знаю»: ${toMark.length} ${pluralRu(toMark.length, "урок", "урока", "уроков")}</button>`
              : ""
          }
          <button class="secondary" data-role="retake">Пройти ещё раз</button>
          ${
            data.anthropic_configured
              ? `<button class="secondary" data-role="new-test">Новый тест · ≈ ${formatCents(data.cost_estimate_usd)}</button>`
              : ""
          }
        </div>
        ${missed.length ? `<p class="muted">Стоит пройти: ${missed.map((l) => escapeHtml(l.label)).join(", ")}.</p>` : ""}
      </div>
      <div class="card">
        <h2>Ответы</h2>
        ${run.results
          .map((r) => {
            const q = byQuestion[r.id] || {};
            const text = q.type === "choice" ? q.question : `${q.before || ""}___${q.after || ""}`;
            const given = q.type === "choice" && r.answer !== "" ? (q.options || [])[Number(r.answer)] : r.answer;
            return `
            <div class="issue-card">
              <p class="topic-meta">${escapeHtml(lessonLabel(data, r.lesson))}</p>
              <p>${escapeHtml(text)}</p>
              ${
                r.correct
                  ? `<p class="correction">✓ ${escapeHtml(r.right_answer)}</p>`
                  : `<p class="quote">✕ ${escapeHtml(given || "(нет ответа)")}</p>
                     <p class="correction">✓ ${escapeHtml(r.right_answer)}</p>`
              }
              ${r.explanation ? `<p class="muted">${escapeHtml(r.explanation)}</p>` : ""}
            </div>`;
          })
          .join("")}
      </div>`;
  }

  function wire(container, data) {
    const status = container.querySelector('[data-role="status"]');
    const select = container.querySelector('[data-role="test-select"]');
    if (select) {
      select.addEventListener("change", async () => {
        draw(container, await Api.getModuleTest(moduleKey, select.value), false);
      });
    }
    container.querySelectorAll('[data-role="new-test"]').forEach((button) =>
      button.addEventListener("click", async () => {
        button.disabled = true;
        status.textContent = "Claude составляет тест — обычно 20–40 секунд…";
        status.scrollIntoView({ block: "center" });
        try {
          draw(container, await Api.createModuleTest(moduleKey), true);
        } catch (err) {
          status.textContent = `Не удалось: ${err.message}`;
          button.disabled = false;
        }
      })
    );
    const retake = container.querySelector('[data-role="retake"]');
    if (retake) retake.addEventListener("click", () => draw(container, data, true));
    const mark = container.querySelector('[data-role="mark-known"]');
    if (mark) {
      mark.addEventListener("click", async () => {
        mark.disabled = true;
        try {
          draw(container, await Api.markModuleKnown(moduleKey, mark.dataset.lessons.split(",")), false);
        } catch (err) {
          mark.textContent = `Ошибка: ${err.message}`;
        }
      });
    }
    const form = container.querySelector('[data-role="test-form"]');
    if (form) {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const answers = {};
        data.test.questions.forEach((q) => {
          const field =
            q.type === "choice"
              ? form.querySelector(`input[name="${q.id}"]:checked`)
              : form.querySelector(`input[name="${q.id}"]`);
          answers[q.id] = field ? field.value : "";
        });
        const submit = form.querySelector('button[type="submit"]');
        submit.disabled = true;
        try {
          draw(container, await Api.submitModuleTest(moduleKey, data.test.id, answers), false);
          window.scrollTo(0, 0);
        } catch (err) {
          status.textContent = `Не удалось проверить: ${err.message}`;
          submit.disabled = false;
        }
      });
    }
  }

  function dispose() {
    root = null;
  }

  return { render, dispose };
})();
