// Drill runners: Leitner cards and AI exercise sets. Each card answer is
// written to the attempts log (POST /api/learner/attempts), which moves the
// card's Leitner box and topic accuracy on the server. The exercise format of a
// card is chosen by the server (learner_model.card_exercise) - this file only
// renders it. A mistake card is a new Russian sentence to say in English; its
// free-text answer is checked by Claude (POST /api/learner/cards/<id>/check),
// phrase gaps are checked here in the browser, the rest is self-graded.
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

  // Replaces the [data-role="buttons"] row with [label, className, handler]
  // buttons and focuses the first, so Enter moves the drill on.
  function setButtons(container, list) {
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

  // ------------------------------------------------------- deep links
  // Free references for an English phrase; formulas ("help + verb-ing") and
  // non-English items get none.
  function phraseLinks(item) {
    if (!/^(en|multi)/i.test(item.language || "")) return "";
    const text = item.content.phrase || item.content.english || "";
    const query = text.replace(/\s*\([^)]*\)/g, "").trim();
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
  const KIND_LABELS = { fix: "Ошибка", phrase: "Фраза", word: "Слово" };
  // Longest spoken answer on a card (the server's CARD_DICTATION_MAX_SECONDS).
  const DICTATION_MAX_SECONDS = 60;

  function cardPrompt(item) {
    const c = item.content || {};
    const ex = item.exercise;
    const type = ex.type;
    if (type === "translate") {
      return `
        <p class="task">Скажите по-английски</p>
        <p class="set-russian">${escapeHtml(ex.russian)}</p>
        ${
          ex.focus
            ? `<details class="card-hint"><summary>Подсказка</summary>${escapeHtml(ex.focus)}</details>`
            : ""
        }`;
    }
    if (type === "gap") {
      return `<p>${escapeHtml(c.meaning)}</p><p class="task">Впишите фразу в пропуск</p>`;
    }
    if (type === "flip") {
      return `
        <p class="task">Как сказать по-английски?</p>
        <p class="word-front">${escapeHtml(c.russian)}</p>`;
    }
    return `<p>${escapeHtml(c.meaning)}</p><p class="task">Вспомните фразу, затем проверьте себя</p>`;
  }

  function cardAnswerArea(item) {
    const ex = item.exercise;
    if (ex.type === "gap") {
      return `
        <p class="gap-sentence">${escapeHtml(ex.before)}<input class="gap-input" data-role="input"
          autocomplete="off" spellcheck="false" />${escapeHtml(ex.after)}</p>`;
    }
    const placeholder = {
      self: "Необязательно: запишите свой вариант",
      flip: "Необязательно: запишите свой вариант",
      translate: "Ваш перевод — напишите или надиктуйте",
    }[ex.type] || "Ваш вариант";
    const textarea = `<textarea data-role="input" rows="2" placeholder="${placeholder}" spellcheck="false"></textarea>`;
    if (ex.type !== "translate") return textarea;
    return `${textarea}
      <div class="dictate-row">
        <button type="button" class="secondary dictate-button" data-role="dictate">${Icons.svg("mic", 18)}<span>Надиктовать</span></button>
        <span class="muted" data-role="dictate-status"></span>
      </div>`;
  }

  // Speak the answer instead of typing it: the clip goes to Deepgram
  // (POST /api/learner/dictate) and the text lands in the field, where it can
  // still be corrected before «Проверить». Returns a stop() for the caller.
  function wireDictation(container, input) {
    const button = container.querySelector('[data-role="dictate"]');
    const status = container.querySelector('[data-role="dictate-status"]');
    const label = button.querySelector("span");
    let recorder = null;
    const reset = () => {
      recorder = null;
      button.classList.remove("recording");
      label.textContent = "Надиктовать";
    };
    const transcribe = async ({ blob, durationSeconds }) => {
      reset();
      if (durationSeconds < 0.5) {
        status.textContent = "Слишком коротко — нажмите и говорите, потом «Стоп».";
        return;
      }
      button.disabled = true;
      status.textContent = "Распознаём…";
      try {
        const { text } = await Api.dictate(blob, durationSeconds);
        if (!text) {
          status.textContent = "Ничего не расслышали. Попробуйте ещё раз.";
        } else {
          input.value = input.value.trim() ? `${input.value.trim()} ${text}` : text;
          status.textContent = "Проверьте текст и нажмите «Проверить».";
          input.focus();
        }
      } catch (err) {
        status.textContent = `Не удалось распознать: ${err.message}`;
      }
      button.disabled = input.readOnly;
    };
    button.addEventListener("click", async () => {
      if (recorder) {
        recorder.stop();
        return;
      }
      recorder = Recorder.create({
        maxSeconds: DICTATION_MAX_SECONDS,
        onTick: (elapsed) => {
          label.textContent = `Стоп · ${Math.floor(elapsed)} с`;
        },
        onStop: transcribe,
      });
      try {
        await recorder.start();
      } catch (err) {
        reset();
        status.textContent = `Нет доступа к микрофону: ${err.message}`;
        return;
      }
      button.classList.add("recording");
      status.textContent = `Говорите по-английски (до ${DICTATION_MAX_SECONDS} с).`;
    });
    return () => {
      if (recorder) recorder.dispose();
      reset();
      button.disabled = true;
    };
  }

  // Where a mistake card came from: the learner's own mistake and the rule
  // behind it, shown only after the answer - the card itself is a new
  // sentence on the same rule.
  function cardSource(item) {
    const c = item.content || {};
    const first = (item.occurrences || [])[0] || {};
    const date = escapeHtml((first.at || "").slice(0, 10));
    const origin = first.session_id
      ? `Из вашей записи от ${date} · <a href="#/session/${encodeURIComponent(first.session_id)}">открыть</a>`
      : `Из AI-набора от ${date}`;
    const better = (c.better_versions || []).filter(Boolean);
    const examples = (c.focus_examples || []).filter(Boolean);
    return `
      <div class="card-source">
        <p class="muted">${origin}</p>
        <p class="quote">✕ «${escapeHtml(c.quote)}»</p>
        <p class="correction">✓ ${escapeHtml(c.correction)}</p>
        ${c.explanation ? `<p>${escapeHtml(c.explanation)}</p>` : ""}
        ${
          c.focus
            ? `<div class="pattern-box">
                 <div class="pattern-rule">Правило: ${escapeHtml(c.focus)}</div>
                 ${examples.map((e) => `<div class="pattern-example">${escapeHtml(e)}</div>`).join("")}
               </div>`
            : ""
        }
        ${
          better.length
            ? `<div class="better-versions"><span class="muted">Проще / естественнее:</span>
                 <ul>${better.map((v) => `<li>${escapeHtml(v)}</li>`).join("")}</ul></div>`
            : ""
        }
      </div>`;
  }

  // A word card's back: the English, an example in the set's context with
  // its translation, a short remark, and the free references.
  function wordBack(item) {
    const c = item.content || {};
    return `
      <p class="word-back">${escapeHtml(c.english)}</p>
      ${
        c.example
          ? `<p class="phrase-example">${escapeHtml(c.example)}</p>
             ${c.example_russian ? `<p class="muted">${escapeHtml(c.example_russian)}</p>` : ""}`
          : ""
      }
      ${c.note ? `<p class="word-note">${escapeHtml(c.note)}</p>` : ""}
      ${phraseLinks(item)}`;
  }

  function cardBack(item) {
    const c = item.content || {};
    if (item.kind === "fix") return cardSource(item);
    if (item.kind === "word") return wordBack(item);
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
      if (item.exercise.type === "translate") askTranslate(item);
      else if (item.exercise.type === "self" || item.exercise.type === "flip") askSelf(item);
      else askTyped(item);
    }

    const buttons = (list) => setButtons(container, list);

    // Free text, so Claude checks it. If the check fails (no key, network),
    // the learner compares with the reference and grades the answer.
    function askTranslate(item) {
      const input = container.querySelector('[data-role="input"]');
      const result = container.querySelector('[data-role="result"]');
      const ex = item.exercise;
      const stopDictation = wireDictation(container, input);
      const check = async () => {
        if (input.readOnly) return;
        const answer = input.value;
        input.readOnly = true;
        stopDictation();
        buttons([]);
        result.innerHTML = `<p class="muted">Claude проверяет…</p>`;
        let verdict;
        try {
          verdict = await Api.checkCard(item.id, ex.drill, answer);
        } catch (err) {
          result.innerHTML = `
            <p class="verdict-bad">Не удалось проверить: ${escapeHtml(err.message)}</p>
            <p><span class="muted">Образец:</span> ${escapeHtml(ex.reference)}</p>
            ${cardSource(item)}
            <p class="muted">Сравните с образцом и оцените себя сами.</p>`;
          buttons([
            ["Верно", "success", () => record(item, true, answer)],
            ["Неверно", "danger", () => record(item, false, answer)],
          ]);
          return;
        }
        const ok = !!verdict.correct;
        const fixed =
          !ok && verdict.corrected && normalize(verdict.corrected) !== normalize(answer)
            ? `<p class="correction">✓ ${escapeHtml(verdict.corrected)}</p>`
            : "";
        const sameAsReference = normalize(answer) === normalize(ex.reference);
        result.innerHTML = `
          <p class="verdict ${ok ? "verdict-ok" : "verdict-bad"}">${ok ? "Верно!" : "Не совсем"}</p>
          ${verdict.comment ? `<p>${escapeHtml(verdict.comment)}</p>` : ""}
          ${fixed}
          ${sameAsReference ? "" : `<p><span class="muted">Образец:</span> ${escapeHtml(ex.reference)}</p>`}
          ${cardSource(item)}`;
        const next = [["Дальше", "", () => record(item, ok, answer)]];
        // Claude can be wrong too; overruling it is still one attempt in the log.
        if (!ok) next.push(["Засчитать как верный", "secondary", () => record(item, true, answer)]);
        buttons(next);
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

  // ------------------------------------------------------ AI exercise set
  // Gaps and fixes are checked here as they are answered; translations are
  // collected and graded by Claude in one call when the set is handed in
  // (POST /api/practice/sets/<id>/submit). Nothing is saved until then.
  const SET_TASKS = {
    gap: "Вставьте пропущенное",
    fix: "Найдите и исправьте ошибку — напишите предложение целиком",
    translate: "Переведите на английский",
  };

  function runSet(container, exerciseSet, { onFinish, context }) {
    const exercises = exerciseSet.exercises;
    const answers = {};
    let index = 0;

    function show() {
      if (index >= exercises.length) return submit();
      const ex = exercises[index];
      container.innerHTML = `
        <div class="card drill-card">
          <div class="drill-head">
            <h2>AI-набор: <span data-role="topic"></span></h2>
            <span class="muted">${index + 1} / ${exercises.length}</span>
          </div>
          <div class="progress-bar"><div style="width:${(index / exercises.length) * 100}%"></div></div>
          ${index === 0 && exerciseSet.intro ? `<p class="set-intro">${escapeHtml(exerciseSet.intro)}</p>` : ""}
          <p class="task">${SET_TASKS[ex.type]}</p>
          ${setPrompt(ex)}
          <div data-role="result"></div>
          <div class="button-row" data-role="buttons"></div>
        </div>`;
      topicLabel(exerciseSet.topic).then((label) => {
        const slot = container.querySelector('[data-role="topic"]');
        if (slot) slot.textContent = label;
      });
      const input = container.querySelector('[data-role="input"]');
      const act = ex.type === "translate" ? () => keep(ex, input) : () => check(ex, input);
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey && !input.readOnly) {
          event.preventDefault();
          act();
        }
      });
      setButtons(container, [[ex.type === "translate" ? "Дальше" : "Проверить", "", act]]);
      input.focus();
      if (ex.type === "fix") input.setSelectionRange(input.value.length, input.value.length);
    }

    function setPrompt(ex) {
      if (ex.type === "gap") {
        return `
          <p class="gap-sentence">${escapeHtml(ex.before)}<input class="gap-input" data-role="input"
            autocomplete="off" spellcheck="false" aria-label="Пропуск" />${escapeHtml(ex.after)}</p>
          ${ex.hint ? `<p class="muted">Подсказка: ${escapeHtml(ex.hint)}</p>` : ""}`;
      }
      if (ex.type === "fix") {
        return `
          <p class="quote">✕ «${escapeHtml(ex.sentence)}»</p>
          <textarea data-role="input" rows="2" spellcheck="false">${escapeHtml(ex.sentence)}</textarea>`;
      }
      return `
        <p class="set-russian">${escapeHtml(ex.russian)}</p>
        ${ex.focus ? `<p class="muted">Используйте: ${escapeHtml(ex.focus)}</p>` : ""}
        <textarea data-role="input" rows="2" placeholder="Ваш перевод" spellcheck="false"></textarea>
        <p class="muted">Переводы проверит Claude в конце набора.</p>`;
    }

    function fullSentence(ex, filler) {
      return `${ex.before}${filler}${ex.after}`;
    }

    function check(ex, input) {
      const answer = input.value;
      input.readOnly = true;
      const ok = matches(answer, ex.accept);
      const expected = closest(answer, ex.accept);
      const result = container.querySelector('[data-role="result"]');
      const diff =
        ex.type === "gap"
          ? renderDiff(fullSentence(ex, answer || "___"), fullSentence(ex, expected))
          : renderDiff(answer, expected);
      result.innerHTML = `
        <p class="verdict ${ok ? "verdict-ok" : "verdict-bad"}">${ok ? "Верно!" : "Не совсем"}</p>
        ${ok ? "" : diff}
        ${ex.explanation ? `<p>${escapeHtml(ex.explanation)}</p>` : ""}`;
      const next = [["Дальше", "", () => keep(ex, input, ok)]];
      if (!ok) next.push(["Засчитать как верный", "secondary", () => keep(ex, input, true)]);
      setButtons(container, next);
    }

    function keep(ex, input, correct) {
      answers[ex.id] = { exercise_id: ex.id, answer: input.value.slice(0, 1000) };
      if (correct !== undefined) answers[ex.id].correct = correct;
      index += 1;
      show();
    }

    async function submit(selfGrades) {
      container.innerHTML = `
        <div class="card drill-card">
          <h2>Проверяем…</h2>
          <p class="muted">Claude проверяет переводы — это несколько секунд.</p>
        </div>`;
      const list = exercises.map((ex) => {
        const entry = { ...answers[ex.id] };
        if (selfGrades && ex.id in selfGrades) entry.correct = selfGrades[ex.id];
        return entry;
      });
      try {
        const result = await Api.submitSet(exerciseSet.id, list, context);
        showResults(result);
      } catch (err) {
        askSelfGrades(err.message);
      }
    }

    // Claude could not grade (no key, network, a timeout): the learner grades
    // the translations against the reference instead, so the run still counts.
    function askSelfGrades(message) {
      const translations = exercises.filter((ex) => ex.type === "translate");
      container.innerHTML = `
        <div class="card drill-card">
          <h2>Не удалось проверить переводы</h2>
          <p class="verdict-bad">${escapeHtml(message)}</p>
          <p class="muted">Можно попробовать ещё раз или оценить переводы самостоятельно.</p>
          <ul class="set-results">
            ${translations
              .map(
                (ex) => `
              <li>
                <p class="set-russian">${escapeHtml(ex.russian)}</p>
                <p><span class="muted">Ваш ответ:</span> ${escapeHtml(answers[ex.id].answer) || "—"}</p>
                <p><span class="muted">Образец:</span> ${escapeHtml(ex.reference)}</p>
                <label class="self-grade"><input type="checkbox" data-self="${ex.id}" /> Мой перевод верный</label>
              </li>`
              )
              .join("")}
          </ul>
          <div class="button-row" data-role="buttons"></div>
        </div>`;
      setButtons(container, [
        ["Проверить ещё раз", "", () => submit()],
        [
          "Сохранить с моей оценкой",
          "secondary",
          () => {
            const grades = {};
            container.querySelectorAll("[data-self]").forEach((box) => {
              grades[box.dataset.self] = box.checked;
            });
            submit(grades);
          },
        ],
      ]);
    }

    function showResults({ run, new_cards: newCards, picked_vocabulary: picked, words_per_day: perDay }) {
      const byId = {};
      exercises.forEach((ex) => (byId[ex.id] = ex));
      const rows = run.results
        .map((r) => {
          const ex = byId[r.exercise_id];
          const mark = r.correct ? "✓" : "✕";
          if (ex.type === "translate") {
            const fixed = r.corrected && !r.correct && normalize(r.corrected) !== normalize(r.answer);
            return `
              <li>
                <p class="set-russian">${mark} ${escapeHtml(ex.russian)}</p>
                <p><span class="muted">Ваш ответ:</span> ${escapeHtml(r.answer) || "—"}</p>
                ${r.comment ? `<p>${escapeHtml(r.comment)}</p>` : ""}
                ${fixed ? `<p class="correction">✓ ${escapeHtml(r.corrected)}</p>` : ""}
                <p class="muted">Образец: ${escapeHtml(ex.reference)}</p>
              </li>`;
          }
          const right = ex.type === "gap" ? fullSentence(ex, ex.accept[0]) : ex.accept[0];
          return `
            <li>
              <p>${mark} ${escapeHtml(right)}</p>
              ${r.correct ? "" : `<p class="muted">Ваш ответ: ${escapeHtml(r.answer) || "—"}</p>`}
            </li>`;
        })
        .join("");
      container.innerHTML = `
        <div class="card drill-card">
          <h2>AI-набор — готово</h2>
          <p class="drill-score">${run.correct} из ${run.total} верно</p>
          <p class="muted">${
            newCards
              ? `Ошибки стали карточками (${newCards}) — они придут в очередь повторений вместе с остальными новыми.`
              : "Новых карточек нет."
          }</p>
          <ul class="set-results">${rows}</ul>
          <div data-role="vocabulary"></div>
          <div class="button-row" data-role="buttons"></div>
        </div>`;
      setButtons(container, [["Готово", "", onFinish]]);
      showVocabulary(container.querySelector('[data-role="vocabulary"]'), picked || [], perDay);
    }

    // The set's useful words and phrases: the learner ticks which become
    // Russian -> English word cards. Saving sends the whole choice, so an
    // unticked entry that was a card stops being one. Sets written before
    // vocabulary existed have none, and the block stays empty.
    function showVocabulary(host, picked, perDay) {
      const vocabulary = exerciseSet.vocabulary || [];
      if (!vocabulary.length) return;
      let saved = new Set(picked);
      host.innerHTML = `
        <div class="vocab-block">
          <h3>Полезные слова и фразы</h3>
          <p class="muted">Отметьте, что хотите учить: карточка покажет русский, вы вспоминаете английский.
            ${perDay ? `Новых слов в день — не больше ${perDay}, остальные подождут.` : ""}</p>
          <ul class="vocab-list">${vocabulary
            .map(
              (v) => `
            <li>
              <label class="vocab-row">
                <input type="checkbox" value="${escapeHtml(v.id)}"${saved.has(v.id) ? " checked" : ""} />
                <span>
                  <span class="phrase">${escapeHtml(v.english)}</span>
                  <span class="muted">— ${escapeHtml(v.russian)}</span>
                  ${v.example ? `<span class="phrase-example">${escapeHtml(v.example)}</span>` : ""}
                  ${v.note ? `<span class="word-note">${escapeHtml(v.note)}</span>` : ""}
                </span>
              </label>
            </li>`
            )
            .join("")}</ul>
          <div class="button-row">
            <button class="secondary" data-role="save-words">Сохранить в карточки</button>
            <span class="muted" data-role="words-status"></span>
          </div>
        </div>`;
      const boxes = [...host.querySelectorAll('input[type="checkbox"]')];
      const save = host.querySelector('[data-role="save-words"]');
      const status = host.querySelector('[data-role="words-status"]');
      const chosen = () => boxes.filter((b) => b.checked).map((b) => b.value);
      const refresh = () => {
        const now = chosen();
        const changed = now.length !== saved.size || now.some((id) => !saved.has(id));
        save.disabled = !changed;
        if (changed) status.textContent = "";
      };
      boxes.forEach((b) => b.addEventListener("change", refresh));
      save.addEventListener("click", async () => {
        save.disabled = true;
        status.textContent = "Сохраняем…";
        try {
          const result = await Api.saveWordPicks(exerciseSet.id, chosen());
          saved = new Set(result.picked);
          const parts = [];
          if (result.added) parts.push(`добавлено: ${result.added}`);
          if (result.removed) parts.push(`убрано: ${result.removed}`);
          status.textContent = `Сохранено${parts.length ? ` — ${parts.join(", ")}` : ""}. В карточках из этого набора: ${saved.size}.`;
        } catch (err) {
          status.textContent = `Не удалось сохранить: ${err.message}`;
        }
        refresh();
      });
      refresh();
    }

    show();
  }

  return { runCards, runSet, normalize, matches, wordDiff };
})();
