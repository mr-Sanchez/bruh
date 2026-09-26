// Listening dictation (Stage 7), free: a YouTube video becomes a lesson -
// its own subtitles are the reference text, the audio is played segment by
// segment and typed word by word.
//   #/dictation            lessons: import a link, see progress, continue one
//   #/dictation/<videoId>  the workspace: one sentence at a time
// Under the typing card, translation.js offers the optional translation task
// (Haiku, on explicit clicks). Checking is entirely local (the same rules as app/dictation.py: case and
// punctuation are ignored); each finished sentence is logged to the server,
// which grades it again and owns the statistics.
window.Views = window.Views || {};

Views.dictation = (() => {
  let root = null;
  let pollTimer = null;
  let workspace = null;

  // Playback options, ear2finger style; kept per browser, not per lesson.
  const SPEEDS = [0.5, 0.6, 0.75, 0.9, 1, 1.25, 1.5, 2];
  const REPEATS = [0, 1, 3, 5, 10, Infinity];
  const PAUSES = [0, 3, 5, 10];
  const PREFS_KEY = "dictation-prefs";

  function loadPrefs() {
    const defaults = { speed: 1, repeat: 1, pause: 0 };
    try {
      return { ...defaults, ...JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") };
    } catch (err) {
      return defaults;
    }
  }

  function savePrefs(prefs) {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    } catch (err) {
      /* a private window: the defaults are fine */
    }
  }

  // --------------------------------------------------------------- routing
  async function render(container, lessonId) {
    root = container;
    disposeWorkspace();
    container.innerHTML = `<div class="card"><p class="muted">Загрузка...</p></div>`;
    try {
      if (lessonId) await renderLesson(container, lessonId);
      else await renderList(container);
    } catch (err) {
      container.innerHTML = `<div class="card"><p class="muted">Не удалось загрузить: ${escapeHtml(err.message)}</p></div>`;
    }
  }

  // ---------------------------------------------------------------- список
  async function renderList(container) {
    const data = await Api.listLessons();
    if (root !== container) return;
    const importing = data.lessons.some((l) => l.status === "importing");
    container.innerHTML = `
      <div class="card">
        <h2>Диктант</h2>
        <p class="muted">Вставьте ссылку на видео с YouTube — приложение возьмёт его субтитры как
          эталон и разобьёт на предложения. Наберите каждое на слух: проверка идёт по буквам,
          подсказка по клавише Tab. Сам диктант бесплатный: ни Deepgram, ни Claude тут не участвуют.
          Перевод услышанного по частям — по кнопке под диктантом, это копейки на Haiku.</p>
        <form class="dictation-import" data-role="import">
          <input type="url" name="url" placeholder="https://www.youtube.com/watch?v=..."
            aria-label="Ссылка на видео" required />
          <button type="submit">Добавить урок</button>
        </form>
        <p class="muted">Видео до ${data.max_minutes} минут, обязательно с субтитрами
          (свои или автоматические на том же языке).</p>
        <p class="muted" data-role="import-status" aria-live="polite"></p>
      </div>
      <div class="card">
        <div class="today-head">
          <h2>Уроки</h2>
          <span class="muted">сегодня надиктовано: ${data.done_today} из ${data.daily_target}</span>
        </div>
        ${data.lessons.length ? data.lessons.map(lessonRow).join("") : `<p class="muted">Уроков пока нет.</p>`}
      </div>`;

    wireImport(container);
    if (importing) schedulePoll(container);
  }

  function lessonRow(lesson) {
    const progress = lesson.progress;
    const share = progress.sentences ? Math.round((progress.done / progress.sentences) * 100) : 0;
    const status =
      lesson.status === "importing"
        ? `<span class="muted">скачивается…</span>`
        : lesson.status === "error"
        ? `<span class="pill pill-major">${escapeHtml(lesson.error_message || "ошибка")}</span>`
        : `<span class="muted">${progress.done} из ${progress.sentences} ${sentencesWord(
            progress.sentences
          )}${
            progress.accuracy == null ? "" : ` · точность ${Math.round(progress.accuracy * 100)}%`
          }</span>`;
    const action =
      lesson.status === "ready"
        ? `<a href="#/dictation/${encodeURIComponent(lesson.id)}"><button>${
            progress.started ? "Продолжить" : "Начать"
          }</button></a>`
        : "";
    return `
      <div class="lesson-row">
        <div>
          <div class="lesson-title">${escapeHtml(lesson.title || lesson.id)}</div>
          <div class="lesson-meta">
            ${escapeHtml(lesson.uploader || "")}${lesson.uploader ? " · " : ""}
            ${formatDuration(lesson.duration_seconds)} ·
            субтитры: ${lesson.subtitle_kind === "manual" ? "свои" : "автоматические"}
          </div>
          <div class="lesson-meta">${status}</div>
          ${
            lesson.status === "ready" && progress.sentences
              ? `<div class="lesson-bar"><span style="width:${share}%"></span></div>`
              : ""
          }
        </div>
        <div class="lesson-actions">
          ${action}
          <button class="secondary" data-role="delete" data-id="${escapeHtml(lesson.id)}">Удалить</button>
        </div>
      </div>`;
  }

  function wireImport(container) {
    const form = container.querySelector('[data-role="import"]');
    const status = container.querySelector('[data-role="import-status"]');
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const url = form.elements.url.value.trim();
      if (!url) return;
      form.querySelector("button").disabled = true;
      status.textContent = "Скачиваем видео и субтитры — обычно 10–60 секунд…";
      try {
        await Api.importLesson(url);
        form.reset();
        schedulePoll(container, 1000);
      } catch (err) {
        status.textContent = `Не удалось: ${err.message}`;
      } finally {
        form.querySelector("button").disabled = false;
      }
    });
    container.querySelectorAll('[data-role="delete"]').forEach((button) => {
      button.addEventListener("click", async () => {
        const row = button.closest(".lesson-row");
        row.classList.add("is-removing");
        try {
          await Api.deleteLesson(button.dataset.id);
          await renderList(container);
        } catch (err) {
          row.classList.remove("is-removing");
        }
      });
    });
  }

  // An import runs in a background task on the server; poll until it settles.
  function schedulePoll(container, delay = 2000) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(() => {
      if (root === container) renderList(container);
    }, delay);
  }

  // ------------------------------------------------------------ workspace
  async function renderLesson(container, lessonId) {
    const lesson = await Api.getLesson(lessonId);
    if (root !== container) return;
    if (lesson.status !== "ready") {
      container.innerHTML = `
        <div class="card">
          <p><a href="#/dictation">← Все уроки</a></p>
          <p class="muted">${
            lesson.status === "importing"
              ? "Урок ещё скачивается."
              : escapeHtml(lesson.error_message || "Урок не готов.")
          }</p>
        </div>`;
      if (lesson.status === "importing") {
        clearTimeout(pollTimer);
        pollTimer = setTimeout(() => {
          if (root === container) renderLesson(container, lessonId);
        }, 2000);
      }
      return;
    }
    workspace = createWorkspace(container, lesson);
  }

  function createWorkspace(container, lesson) {
    const prefs = loadPrefs();
    const sentences = lesson.sentences;
    let index = Math.min(lesson.progress.next_index || 0, sentences.length - 1);
    let answers = [];
    let hints = new Set();
    let errorChars = 0;
    let startedAt = Date.now();
    let repeatsLeft = prefs.repeat;
    let logged = false;
    let advanceTimer = null; // the hop to the next sentence after a correct one
    let repeatTimer = null; // the wait before a sentence is played again
    let watchTimer = null; // stops the audio at the end of the sentence
    let userPaused = false;

    container.innerHTML = `
      <div class="card">
        <div class="today-head">
          <div>
            <h2>${escapeHtml(lesson.title || lesson.id)}</h2>
            <p class="muted"><a href="#/dictation">← Все уроки</a> ·
              <a href="${escapeHtml(lesson.url)}" target="_blank" rel="noopener">видео ↗</a></p>
          </div>
          <div class="today-meta">
            <div class="muted" data-role="counter"></div>
          </div>
        </div>
        <audio data-role="audio" preload="metadata"
          src="/api/dictation/lessons/${encodeURIComponent(lesson.id)}/audio"></audio>
        <div class="dictation-controls">
          <button data-role="play">▶︎ Слушать</button>
          <button class="secondary" data-role="hold" aria-live="polite">⏸ Пауза</button>
          <button class="secondary" data-role="prev">◀ Пред.</button>
          <button class="secondary" data-role="next">След. ▶</button>
          <label>Скорость
            <select data-role="speed">
              ${SPEEDS.map((s) => `<option value="${s}" ${s === prefs.speed ? "selected" : ""}>${s}×</option>`).join("")}
            </select>
          </label>
          <label>Повторов
            <select data-role="repeat">
              ${REPEATS.map(
                (r) =>
                  `<option value="${r}" ${r === prefs.repeat ? "selected" : ""}>${
                    r === Infinity ? "∞" : r
                  }</option>`
              ).join("")}
            </select>
          </label>
          <label>Между повторами
            <select data-role="pause">
              ${PAUSES.map((p) => `<option value="${p}" ${p === prefs.pause ? "selected" : ""}>${p} с</option>`).join("")}
            </select>
          </label>
        </div>
        <div class="dictation-line" data-role="line"></div>
        <p class="dictation-verdict muted" data-role="verdict" aria-live="polite"></p>
        <p class="muted dictation-help">Enter — слушать снова · Esc — пауза / продолжить ·
          Space — следующее слово ·
          Tab — подсказка · Backspace в пустом поле — назад · [ и ] — соседние предложения</p>
      </div>
      <div class="card" data-role="translation"></div>`;

    const audio = container.querySelector('[data-role="audio"]');
    const line = container.querySelector('[data-role="line"]');
    const verdict = container.querySelector('[data-role="verdict"]');
    const counter = container.querySelector('[data-role="counter"]');
    const translation = Translation.mount(
      container.querySelector('[data-role="translation"]'),
      lesson
    );

    // ------------------------------------------------------------ helpers
    const sentence = () => sentences[index];
    const wordTokens = () => sentence().tokens.filter((t) => t.word);
    const normalize = (value) =>
      Array.from(String(value || "").toLowerCase())
        .filter((ch) => /[\p{L}\p{N}]/u.test(ch))
        .join("");
    const isCorrect = (i) => normalize(answers[i]) === normalize(wordTokens()[i].text);
    const allCorrect = () => wordTokens().every((_, i) => isCorrect(i));

    function renderSentence() {
      const done = lesson.results[index];
      let wordIndex = -1;
      line.innerHTML = sentence()
        .tokens.map((token) => {
          if (!token.word) return `<span class="dict-punct">${escapeHtml(token.text)}</span>`;
          wordIndex += 1;
          const size = Math.max(3, token.text.length);
          return `<input class="dict-word" data-index="${wordIndex}" size="${size}"
            autocomplete="off" autocorrect="off" spellcheck="false"
            aria-label="Слово ${wordIndex + 1}" />`;
        })
        .join(" ");
      counter.textContent = `Предложение ${index + 1} из ${sentences.length}`;
      verdict.textContent = done && done.completed ? "Это предложение уже набрано верно." : "";
      verdict.className = "dictation-verdict muted";
      line.querySelectorAll(".dict-word").forEach(wireWord);
      focusWord(0);
    }

    function focusWord(target) {
      const inputs = [...line.querySelectorAll(".dict-word")];
      const next = inputs.find((input, i) => i >= target && !isCorrect(i)) || inputs[target] || inputs[0];
      if (next) next.focus();
    }

    function wireWord(input) {
      const i = Number(input.dataset.index);
      input.addEventListener("input", () => {
        const target = wordTokens()[i].text;
        const typed = normalize(input.value);
        const previous = normalize(answers[i] || "");
        answers[i] = input.value;
        // One error character per keystroke that leaves the right spelling.
        if (typed.length > previous.length && !normalize(target).startsWith(typed)) errorChars += 1;
        paint(input, i);
        if (allCorrect()) finishSentence();
      });
      input.addEventListener("keydown", (event) => onKey(event, input, i));
      input.addEventListener("focus", () => input.select());
    }

    function paint(input, i) {
      const target = normalize(wordTokens()[i].text);
      const typed = normalize(input.value);
      input.classList.toggle("is-correct", Boolean(typed) && typed === target);
      input.classList.toggle("is-wrong", Boolean(typed) && !target.startsWith(typed));
      input.classList.toggle("is-hinted", hints.has(i));
    }

    function onKey(event, input, i) {
      const inputs = [...line.querySelectorAll(".dict-word")];
      if (event.key === "Enter") {
        event.preventDefault();
        play();
      } else if (event.key === "Escape") {
        event.preventDefault();
        toggleHold();
      } else if (event.key === " " || event.key === "Spacebar") {
        event.preventDefault();
        (inputs[i + 1] || inputs[i]).focus();
      } else if (event.key === "Tab" && !event.shiftKey && !isCorrect(i)) {
        event.preventDefault();
        showHint(input, i);
      } else if (event.key === "Backspace" && !input.value && inputs[i - 1]) {
        event.preventDefault();
        inputs[i - 1].focus();
      } else if (event.key === "[" && !input.value) {
        event.preventDefault();
        go(index - 1);
      } else if (event.key === "]" && !input.value) {
        event.preventDefault();
        go(index + 1);
      }
    }

    // The hint fills the word in and marks it: it still has to be typed over,
    // and the word goes into the lesson's «сложные слова».
    function showHint(input, i) {
      hints.add(i);
      input.value = wordTokens()[i].text;
      answers[i] = input.value;
      paint(input, i);
      if (allCorrect()) finishSentence();
    }

    // Every stored sentence goes through here: the server grades the answers
    // again and owns the statistics; `error_chars` is the only number that
    // can be counted in the browser alone.
    async function saveResult() {
      const saved = await Api.postLessonResult(lesson.id, {
        sentence: index,
        answers: wordTokens().map((_, i) => answers[i] || ""),
        hints: [...hints],
        error_chars: errorChars,
        seconds: (Date.now() - startedAt) / 1000,
      });
      lesson.results[index] = saved.result;
      lesson.progress = saved.progress;
      translation.refresh(); // a part opens when its last sentence is done
      return saved;
    }

    async function finishSentence() {
      if (logged) return;
      logged = true;
      const inputs = [...line.querySelectorAll(".dict-word")];
      inputs.forEach((input) => paint(input, Number(input.dataset.index)));
      const clean = hints.size === 0 && errorChars === 0;
      verdict.textContent = clean
        ? "✓ Верно, без единой ошибки"
        : `✓ Верно · подсказок: ${hints.size} · ошибок при наборе: ${errorChars}`;
      verdict.className = `dictation-verdict ${clean ? "is-clean" : "is-ok"}`;
      // Done: no more repeats of this sentence.
      repeatsLeft = 0;
      clearTimeout(repeatTimer);
      repeatTimer = null;
      try {
        await saveResult();
      } catch (err) {
        verdict.textContent += ` · результат не сохранён: ${err.message}`;
      }
      if (index < sentences.length - 1) {
        clearTimeout(advanceTimer);
        advanceTimer = setTimeout(() => go(index + 1), 900);
      }
    }

    function go(target) {
      if (target < 0 || target >= sentences.length) return;
      // Leaving a half-typed sentence still logs it: the words given up on
      // are exactly the ones worth showing as «сложные».
      if (!logged && answers.some((answer) => answer && answer.trim())) {
        logged = true;
        saveResult().catch(() => {});
      }
      clearTimeout(advanceTimer);
      index = target;
      answers = [];
      hints = new Set();
      errorChars = 0;
      logged = false;
      startedAt = Date.now();
      repeatsLeft = prefs.repeat;
      renderSentence();
      play();
    }

    // ----------------------------------------------------------- playback
    const holdButton = container.querySelector('[data-role="hold"]');

    // «Пауза» while it plays (or waits to repeat), «Продолжить» otherwise.
    function refreshHold() {
      const active = !audio.paused || repeatTimer !== null;
      holdButton.textContent = active ? "⏸ Пауза" : "▶︎ Продолжить";
    }

    // `timeupdate` only fires ~4 times a second, so the end of the sentence is
    // watched on a short interval: the audio stops where the sentence does.
    function stopWatch() {
      clearInterval(watchTimer);
      watchTimer = null;
    }

    function startWatch() {
      stopWatch();
      watchTimer = setInterval(() => {
        if (audio.paused || audio.currentTime < sentence().end) return;
        audio.pause();
        onSegmentEnd();
      }, 40);
    }

    function onSegmentEnd() {
      stopWatch();
      if (userPaused || allCorrect() || repeatsLeft <= 0) return;
      if (repeatsLeft !== Infinity) repeatsLeft -= 1;
      clearTimeout(repeatTimer);
      repeatTimer = setTimeout(() => {
        repeatTimer = null;
        play();
      }, prefs.pause * 1000);
      refreshHold();
    }

    function play() {
      clearTimeout(advanceTimer);
      clearTimeout(repeatTimer);
      repeatTimer = null;
      userPaused = false;
      audio.playbackRate = prefs.speed;
      audio.currentTime = sentence().start;
      startWatch();
      audio.play().catch(() => {});
    }

    // Pause keeps the position: «Продолжить» goes on from where it stopped,
    // or from the start of the sentence if it had already played to the end.
    function toggleHold() {
      if (!audio.paused || repeatTimer !== null) {
        userPaused = true;
        clearTimeout(repeatTimer);
        repeatTimer = null;
        stopWatch();
        audio.pause();
        refreshHold();
        return;
      }
      const at = audio.currentTime;
      if (at > sentence().start && at < sentence().end) {
        userPaused = false;
        audio.playbackRate = prefs.speed;
        startWatch();
        audio.play().catch(() => {});
      } else {
        play();
      }
    }

    audio.addEventListener("play", refreshHold);
    audio.addEventListener("pause", refreshHold);
    audio.addEventListener("ended", onSegmentEnd);
    holdButton.addEventListener("click", () => {
      toggleHold();
      focusWord(0);
    });
    container.querySelector('[data-role="play"]').addEventListener("click", play);
    container.querySelector('[data-role="prev"]').addEventListener("click", () => go(index - 1));
    container.querySelector('[data-role="next"]').addEventListener("click", () => go(index + 1));
    const bind = (role, key, cast) =>
      container.querySelector(`[data-role="${role}"]`).addEventListener("change", (event) => {
        prefs[key] = cast(event.target.value);
        if (key === "repeat") repeatsLeft = prefs[key];
        if (key === "speed") audio.playbackRate = prefs.speed;
        savePrefs(prefs);
      });
    bind("speed", "speed", Number);
    bind("repeat", "repeat", (value) => (value === "Infinity" ? Infinity : Number(value)));
    bind("pause", "pause", Number);

    refreshHold();

    renderSentence();

    return {
      dispose() {
        clearTimeout(advanceTimer);
        clearTimeout(repeatTimer);
        stopWatch();
        audio.pause();
        audio.removeAttribute("src");
        audio.load();
      },
    };
  }

  function disposeWorkspace() {
    if (workspace) workspace.dispose();
    workspace = null;
  }

  function dispose() {
    clearTimeout(pollTimer);
    disposeWorkspace();
    root = null;
  }

  return { render, dispose };
})();
