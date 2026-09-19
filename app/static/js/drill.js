// Drill runners for the free ($0) exercises: Leitner cards and cloze texts.
// Everything is checked here in the browser; each answer is written to the
// attempts log (POST /api/learner/attempts), which moves the card's Leitner box
// and topic accuracy on the server. The exercise format of a card is chosen by
// the server (learner_model.card_exercise) - this file only renders it.
const Drill = (() => {
  // ------------------------------------------------------------ checking
  // Case, quotes, punctuation and spacing do not count; apostrophes do
  // ("its" vs "it's"). Mirrors learner_model.normalize_text, minus punctuation.
  function normalize(text) {
    return String(text || "")
      .replace(/[’‘]/g, "'")
      .toLowerCase()
      .replace(/[^\p{L}\p{N}'\s]/gu, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  // Words as written (for display) with their normalised form (for comparing).
  function words(text) {
    return String(text || "")
      .split(/\s+/)
      .map((word) => ({ word, key: normalize(word) }))
      .filter((w) => w.key);
  }

  function matches(answer, accepted) {
    const value = normalize(answer);
    return !!value && accepted.some((candidate) => normalize(candidate) === value);
  }

  // Word-level LCS diff: which of the learner's words are extra, which of the
  // expected words are missing.
  function wordDiff(given, expected) {
    const a = words(given);
    const b = words(expected);
    const table = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
    for (let i = a.length - 1; i >= 0; i--) {
      for (let j = b.length - 1; j >= 0; j--) {
        table[i][j] = a[i].key === b[j].key ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
      }
    }
    const givenMarks = [];
    const expectedMarks = [];
    let i = 0;
    let j = 0;
    while (i < a.length || j < b.length) {
      if (i < a.length && j < b.length && a[i].key === b[j].key) {
        givenMarks.push({ word: a[i++].word, ok: true });
        expectedMarks.push({ word: b[j++].word, ok: true });
      } else if (j < b.length && (i >= a.length || table[i][j + 1] >= table[i + 1][j])) {
        expectedMarks.push({ word: b[j++].word, ok: false });
      } else {
        givenMarks.push({ word: a[i++].word, ok: false });
      }
    }
    return { given: givenMarks, expected: expectedMarks, common: table[0][0] };
  }

  function closest(answer, accepted) {
    let best = accepted[0];
    let bestCommon = -1;
    accepted.forEach((candidate) => {
      const common = wordDiff(answer, candidate).common;
      if (common > bestCommon) {
        best = candidate;
        bestCommon = common;
      }
    });
    return best;
  }

  function renderDiff(answer, expected) {
    const diff = wordDiff(answer, expected);
    const line = (marks, cls) =>
      marks.map((m) => (m.ok ? escapeHtml(m.word) : `<span class="${cls}">${escapeHtml(m.word)}</span>`)).join(" ");
    return `
      <div class="diff">
        <div><span class="muted">Ваш ответ:</span> ${line(diff.given, "diff-extra") || "—"}</div>
        <div><span class="muted">Правильно:</span> ${line(diff.expected, "diff-missing")}</div>
      </div>`;
  }

  function shuffle(list) {
    const copy = list.slice();
    for (let i = copy.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [copy[i], copy[j]] = [copy[j], copy[i]];
    }
    return copy;
  }

  // ------------------------------------------------------- deep links
  // Free references for an English phrase; formulas ("help + verb-ing") and
  // non-English items get none.
  function phraseLinks(item) {
    if (!/^(en|multi)/i.test(item.language || "")) return "";
    const query = (item.content.phrase || "").replace(/\s*\([^)]*\)/g, "").trim();
    if (!query || /[+/]/.test(query)) return "";
    const q = encodeURIComponent(query);
    const links = [
      ["YouGlish", `https://youglish.com/pronounce/${q}/english`],
      ["Cambridge Dictionary", `https://dictionary.cambridge.org/search/english/?q=${q}`],
      ["SkELL", `https://skell.sketchengine.eu/#result?lang=en&query=${q}&f=concordance`],
    ];
    return `<div class="deep-links">${links
      .map(([title, url]) => `<a href="${url}" target="_blank" rel="noopener">${title} ↗</a>`)
      .join("")}</div>`;
  }

  // ------------------------------------------------------------- cards
  const KIND_LABELS = { fix: "Ошибка", pattern: "Правило", phrase: "Фраза" };

  function cardPrompt(item) {
    const c = item.content || {};
    const type = item.exercise.type;
    if (item.kind === "fix") {
      const task = {
        scramble: "Соберите исправленный вариант из слов",
        type: "Напишите исправленный вариант",
        self: "Скажите или напишите исправленный вариант, затем проверьте себя",
      }[type];
      return `<p class="quote">❌ «${escapeHtml(c.quote)}»</p><p class="task">${task}</p>`;
    }
    if (item.kind === "pattern") {
      return `
        <div class="pattern-box"><div class="pattern-rule">${escapeHtml(c.rule)}</div></div>
        <p class="task">Придумайте свой пример на это правило, затем сравните с образцами</p>`;
    }
    if (type === "gap") {
      return `<p>${escapeHtml(c.meaning)}</p><p class="task">Впишите фразу в пропуск</p>`;
    }
    return `<p>${escapeHtml(c.meaning)}</p><p class="task">Вспомните фразу, затем проверьте себя</p>`;
  }

  function cardAnswerArea(item) {
    const ex = item.exercise;
    if (ex.type === "scramble") {
      const tokens = ex.answer.split(/\s+/).filter(Boolean);
      let order = shuffle(tokens);
      for (let tries = 0; tries < 5 && tokens.length > 1 && order.join(" ") === tokens.join(" "); tries++) {
        order = shuffle(tokens);
      }
      return `
        <div class="scramble-answer" data-role="answer"></div>
        <div class="scramble-pool" data-role="pool">
          ${order.map((t) => `<button type="button" class="chip">${escapeHtml(t)}</button>`).join("")}
        </div>`;
    }
    if (ex.type === "gap") {
      return `
        <p class="gap-sentence">${escapeHtml(ex.before)}<input class="gap-input" data-role="input"
          autocomplete="off" spellcheck="false" />${escapeHtml(ex.after)}</p>`;
    }
    const placeholder = ex.type === "self" ? "Необязательно: запишите свой вариант" : "Ваш вариант";
    return `<textarea data-role="input" rows="2" placeholder="${placeholder}" spellcheck="false"></textarea>`;
  }

  function cardBack(item) {
    const c = item.content || {};
    if (item.kind === "fix") {
      const better = (c.better_versions || []).filter(Boolean);
      return `
        <p class="correction">✅ ${escapeHtml(c.correction)}</p>
        ${c.explanation ? `<p>${escapeHtml(c.explanation)}</p>` : ""}
        ${
          better.length
            ? `<div class="better-versions"><span class="muted">Проще / естественнее:</span>
                 <ul>${better.map((v) => `<li>${escapeHtml(v)}</li>`).join("")}</ul></div>`
            : ""
        }`;
    }
    if (item.kind === "pattern") {
      return `<div class="pattern-box">
        ${(c.examples || []).map((e) => `<div class="pattern-example">${escapeHtml(e)}</div>`).join("")}
      </div>`;
    }
    return `
      <p><span class="phrase">${escapeHtml(c.phrase)}</span> <span class="muted">— ${escapeHtml(c.meaning)}</span></p>
      ${c.example ? `<p class="phrase-example">${escapeHtml(c.example)}</p>` : ""}
      ${phraseLinks(item)}`;
  }

  // Runs a list of cards one by one. `context` goes into the attempts log
  // ("daily", "topic") so it is clear later where an answer was given.
  function runCards(container, cards, { title, context, onFinish }) {
    let index = 0;
    let right = 0;

    function show() {
      if (index >= cards.length) return finish();
      const item = cards[index];
      const topicPart = item.topic ? ` · <span data-role="topic"></span>` : "";
      container.innerHTML = `
        <div class="card drill-card">
          <div class="drill-head">
            <h2>${escapeHtml(title)}</h2>
            <span class="muted">${index + 1} / ${cards.length}</span>
          </div>
          <div class="progress-bar"><div style="width:${(index / cards.length) * 100}%"></div></div>
          <p class="muted drill-meta">${KIND_LABELS[item.kind] || ""}${item.state.is_new ? " · новая" : ` · коробка ${item.state.box}`}${topicPart}</p>
          ${cardPrompt(item)}
          <div data-role="area">${cardAnswerArea(item)}</div>
          <div data-role="result"></div>
          <div class="button-row" data-role="buttons"></div>
        </div>`;
      if (item.topic) {
        topicLabel(item.topic).then((label) => {
          const slot = container.querySelector('[data-role="topic"]');
          if (slot) slot.textContent = label;
        });
      }
      if (item.exercise.type === "scramble") wireScramble(item);
      else if (item.exercise.type === "self") askSelf(item);
      else askTyped(item);
    }

    function buttons(list) {
      const row = container.querySelector('[data-role="buttons"]');
      row.innerHTML = "";
      list.forEach(([label, cls, handler]) => {
        const button = document.createElement("button");
        button.textContent = label;
        if (cls) button.className = cls;
        button.addEventListener("click", handler);
        row.appendChild(button);
      });
      const first = row.querySelector("button");
      if (first) first.focus();
    }

    function wireScramble(item) {
      const answerBox = container.querySelector('[data-role="answer"]');
      const pool = container.querySelector('[data-role="pool"]');
      const move = (event) => {
        const chip = event.target.closest(".chip");
        if (!chip) return;
        (chip.parentElement === pool ? answerBox : pool).appendChild(chip);
      };
      answerBox.addEventListener("click", move);
      pool.addEventListener("click", move);
      buttons([
        [
          "Проверить",
          "",
          () => {
            const answer = Array.from(answerBox.querySelectorAll(".chip")).map((c) => c.textContent).join(" ");
            answerBox.style.pointerEvents = pool.style.pointerEvents = "none";
            judged(item, answer, matches(answer, [item.exercise.answer]), item.exercise.answer);
          },
        ],
      ]);
    }

    function askTyped(item) {
      const input = container.querySelector('[data-role="input"]');
      const check = () => {
        const answer = input.value;
        input.readOnly = true;
        const accepted = item.exercise.accept;
        judged(item, answer, matches(answer, accepted), closest(answer, accepted));
      };
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey && !input.readOnly) {
          event.preventDefault();
          check();
        }
      });
      buttons([["Проверить", "", check]]);
      input.focus();
    }

    function askSelf(item) {
      const input = container.querySelector('[data-role="input"]');
      buttons([
        [
          "Показать ответ",
          "",
          () => {
            input.readOnly = true;
            container.querySelector('[data-role="result"]').innerHTML = `<div class="card-back">${cardBack(item)}</div>`;
            buttons([
              ["Помню", "success", () => record(item, true, input.value)],
              ["Не помню", "danger", () => record(item, false, input.value)],
            ]);
          },
        ],
      ]);
    }

    function judged(item, answer, ok, expected) {
      const result = container.querySelector('[data-role="result"]');
      result.innerHTML = `
        <p class="verdict ${ok ? "verdict-ok" : "verdict-bad"}">${ok ? "Верно!" : "Не совсем"}</p>
        ${ok ? "" : renderDiff(answer, expected)}
        <div class="card-back">${cardBack(item)}</div>`;
      const next = [["Дальше", "", () => record(item, ok, answer)]];
      // Exact matching is strict: a typo or an equally good wording is the
      // learner's call, and it is still one attempt in the log.
      if (!ok) next.push(["Засчитать как верный", "secondary", () => record(item, true, answer)]);
      buttons(next);
    }

    async function record(item, correct, answer) {
      container.querySelectorAll('[data-role="buttons"] button').forEach((b) => (b.disabled = true));
      try {
        await Api.postAttempt({
          item_id: item.id,
          exercise: `card_${item.exercise.type}`,
          correct,
          answer: answer ? answer.slice(0, 4000) : null,
          context,
        });
      } catch (err) {
        container.querySelector('[data-role="result"]').insertAdjacentHTML(
          "beforeend",
          `<p class="verdict-bad">Не удалось сохранить ответ: ${escapeHtml(err.message)}</p>`
        );
        container.querySelectorAll('[data-role="buttons"] button').forEach((b) => (b.disabled = false));
        return;
      }
      if (correct) right += 1;
      index += 1;
      show();
    }

    function finish() {
      container.innerHTML = `
        <div class="card drill-card">
          <h2>${escapeHtml(title)} — готово</h2>
          <p class="drill-score">${right} из ${cards.length} верно</p>
          <p class="muted">Карточки с ошибками вернутся завтра, верные — позже, по графику повторений.</p>
          <div class="button-row"><button data-role="done">Готово</button></div>
        </div>`;
      container.querySelector('[data-role="done"]').addEventListener("click", onFinish);
    }

    show();
  }

  // ------------------------------------------------------------- cloze
  const CLOZE_TITLES = { articles: "Артикли", prepositions: "Предлоги" };

  // One improved_version text with function words blanked out. The whole text
  // is one topic attempt; its share of right gaps is the score.
  function runCloze(container, text, topic, { onFinish }) {
    let gapIndex = 0;
    const body = text.segments
      .map((segment) => {
        if (segment.gap === undefined) return escapeHtml(segment.text);
        const i = gapIndex++;
        const width = Math.max(3, segment.gap.length + 1);
        return `<input class="gap-input" data-gap="${i}" style="width:${width + 1}ch"
          autocomplete="off" spellcheck="false" aria-label="Пропуск ${i + 1}" />`;
      })
      .join("");
    const answers = text.segments.filter((s) => s.gap !== undefined).map((s) => s.gap);

    container.innerHTML = `
      <div class="card drill-card">
        <div class="drill-head">
          <h2>Пропуски: ${escapeHtml(CLOZE_TITLES[topic] || topic)}</h2>
          <span class="muted">${escapeHtml(text.recorded_at.slice(0, 10))} · пропусков: ${answers.length}</span>
        </div>
        <p class="muted">Это ваша «улучшенная версия» монолога. Впишите пропущенные ${
          topic === "articles" ? "артикли (a / an / the)" : "предлоги"
        }.</p>
        <div class="cloze-text">${body}</div>
        <div data-role="result"></div>
        <div class="button-row" data-role="buttons">
          <button data-role="check">Проверить</button>
          <button class="secondary" data-role="back">Назад</button>
        </div>
      </div>`;

    const inputs = Array.from(container.querySelectorAll(".gap-input"));
    inputs.forEach((input, i) => {
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          (inputs[i + 1] || container.querySelector('[data-role="check"]')).focus();
        }
      });
    });
    if (inputs[0]) inputs[0].focus();
    container.querySelector('[data-role="back"]').addEventListener("click", onFinish);

    const checkButton = container.querySelector('[data-role="check"]');
    checkButton.addEventListener("click", async () => {
      checkButton.disabled = true;
      let right = 0;
      inputs.forEach((input, i) => {
        const ok = normalize(input.value) === normalize(answers[i]);
        if (ok) right += 1;
        input.readOnly = true;
        input.classList.add(ok ? "gap-ok" : "gap-bad");
        if (!ok) {
          input.insertAdjacentHTML("afterend", `<span class="gap-fix">${escapeHtml(answers[i])}</span>`);
        }
      });
      const score = answers.length ? right / answers.length : 1;
      const result = container.querySelector('[data-role="result"]');
      result.innerHTML = `<p class="drill-score">${right} из ${answers.length} верно</p>`;
      try {
        await Api.postAttempt({
          topic,
          exercise: "cloze",
          score,
          session_id: text.session_id,
          answer: inputs.map((input) => input.value.trim() || "_").join(" | ").slice(0, 4000),
        });
      } catch (err) {
        result.insertAdjacentHTML(
          "beforeend",
          `<p class="verdict-bad">Не удалось сохранить результат: ${escapeHtml(err.message)}</p>`
        );
      }
      checkButton.remove();
      container.querySelector('[data-role="back"]').textContent = "Готово";
    });
  }

  return { runCards, runCloze, normalize, matches, wordDiff, CLOZE_TITLES };
})();
