// Spoken activities, measured from Deepgram's word timings and the silences
// in the audio (Recorder.findSilences) - the measuring itself needs no Claude:
//   #/speaking[/<prompt>]        «Говорение»: an own or a suggested topic, an
//                                optional time limit, one take or a series;
//                                pace, fillers and pauses per take, analysis on
//                                a click (#/record and #/talk are older links)
//   #/shadowing[/<session>:<n>]  read a passage of your own improved_version
//                                aloud; missed / misheard / unclear words marked
// Speech.* renders the measurements; the session page uses it too.
window.Views = window.Views || {};

const Speech = (() => {
  const EXERCISE_NAMES = { monologue: "Говорение", talk: "60 секунд", shadowing: "Shadowing" };

  function tile(label, value, note) {
    return `
      <div class="stat-tile">
        <div class="stat-label">${escapeHtml(label)}</div>
        <div class="stat-value">${value}</div>
        ${note ? `<div class="stat-note muted">${note}</div>` : ""}
      </div>`;
  }

  function fillerNote(breakdown) {
    const parts = Object.entries(breakdown || {})
      .sort((a, b) => b[1] - a[1])
      .map(([word, count]) => `${escapeHtml(word)} ×${count}`);
    return parts.join(", ");
  }

  function renderMetrics(m) {
    if (!m || !m.words) return `<p class="muted">Deepgram не расслышал ни одного слова.</p>`;
    const num = (v) => (v == null ? "—" : escapeHtml(v));
    return `
      <div class="stat-tiles">
        ${tile("Темп", `${num(m.wpm)} <small>сл/мин</small>`, "живая речь ≈ 120–150")}
        ${tile(
          "Паразиты",
          m.fillers_per_min == null ? "—" : `${num(m.fillers_per_min)} <small>/мин</small>`,
          m.fillers_per_min == null ? "считаются только в английском" : fillerNote(m.filler_breakdown) || "ни одного"
        )}
        ${tile(
          "Паузы",
          num(m.long_pauses),
          `${m.longest_pause ? `самая длинная ${escapeHtml(m.longest_pause)} с` : "без долгих пауз"}
           · ≥ 1 с внутри фразы, ≥ 2 с между${m.pauses_from_audio === false ? " · по словам Deepgram, неточно" : ""}`
        )}
        ${tile(
          "Повторы",
          num(m.repeats),
          (m.repeat_examples || []).map((r) => `«${escapeHtml(r)}»`).join(", ") || "без повторов"
        )}
        ${m.fluency_score == null ? "" : tile("Беглость", `${Math.round(m.fluency_score * 100)}%`, "паразиты + паузы в минуту")}
      </div>`;
  }

  // The transcript with fillers highlighted and long pauses shown in place.
  function renderTimeline(tokens) {
    if (!tokens || !tokens.length) return "";
    return `
      <div class="transcript-box speech-timeline">${tokens
        .map((t) =>
          t.pause != null
            ? `<span class="speech-pause" title="Пауза">⏸ ${escapeHtml(t.pause)} с</span> `
            : t.filler
              ? `<mark class="speech-filler">${escapeHtml(t.text)}</mark> `
              : `${escapeHtml(t.text)} `
        )
        .join("")}</div>`;
  }

  function youglish(word) {
    return `https://youglish.com/pronounce/${encodeURIComponent(word.toLowerCase())}/english`;
  }

  // A shadowing take: the passage with every word marked, plus YouGlish links
  // for the words that did not come through.
  function renderReading(reading) {
    if (!reading) return "";
    const text = reading.segments
      .map((s) => {
        if (s.text != null) return escapeHtml(s.text);
        if (s.extra != null) return `<span class="read-extra" title="Лишнее">+${escapeHtml(s.extra)}</span> `;
        const word = escapeHtml(s.word);
        if (s.status === "wrong") {
          return `<span class="read-wrong" title="Услышано: ${escapeHtml(s.heard)}">${word}<small>${escapeHtml(s.heard)}</small></span>`;
        }
        if (s.status === "missed") return `<span class="read-missed" title="Пропущено">${word}</span>`;
        if (s.status === "unclear") {
          return `<span class="read-unclear" title="Распознано неуверенно — проверьте произношение">${word}</span>`;
        }
        return `<span class="read-${s.status}">${word}</span>`;
      })
      .join("");
    const trouble = [
      ...new Set(
        reading.segments
          .filter((s) => ["wrong", "missed", "unclear"].includes(s.status))
          .map((s) => s.word.toLowerCase())
      ),
    ];
    return `
      <p><strong>${Math.round(reading.score * 100)}%</strong> слов прочитано верно
        <span class="muted">· ${reading.ok + reading.unclear} из ${reading.scored_words}
        · не так: ${reading.wrong} · пропущено: ${reading.missed}
        · неуверенно: ${reading.unclear}${reading.extra ? ` · лишних: ${reading.extra}` : ""}</span></p>
      <div class="reading-text">${text}</div>
      <p class="muted reading-legend">
        <span class="read-wrong">не так</span> · <span class="read-missed">пропущено</span> ·
        <span class="read-unclear">неуверенно</span> · <span class="read-extra">+лишнее</span></p>
      ${
        trouble.length
          ? `<p class="muted">Как это произносят носители:
              ${trouble
                .slice(0, 12)
                .map((w) => `<a href="${youglish(w)}" target="_blank" rel="noopener">${escapeHtml(w)} ↗</a>`)
                .join(" · ")}</p>`
          : ""
      }`;
  }

  // Everything the session page shows for a spoken take.
  function renderReport(session) {
    const speech = session.speech;
    if (!speech) return "";
    const reading = session.kind === "shadowing" ? renderReading(speech.reading) : "";
    return `
      ${reading}
      ${renderMetrics(speech.metrics)}
      ${session.kind === "shadowing" ? "" : renderTimeline(speech.timeline)}`;
  }

  // Recording controls shared by the spoken activities; `extraField` is
  // one more field next to the microphone (the language of «Говорение»).
  function controlsHtml(startLabel, extraField = "") {
    return `
      <div class="row">
        <div>
          <label for="mic-select">Микрофон</label>
          <select id="mic-select"></select>
        </div>
        ${extraField}
      </div>
      <div class="status-line">
        <span id="status-badge" class="status-badge status-ready">Готово</span>
        <span id="timer" class="timer">00:00</span>
      </div>
      <div class="level-meter"><div id="level-bar"></div></div>
      <div class="button-row">
        <button id="start-btn">${escapeHtml(startLabel)}</button>
        <button id="stop-btn" class="danger" disabled>Остановить</button>
      </div>
      <p id="message" class="muted"></p>`;
  }

  // Wires the controls in `root` to a Recorder: records, finds the silences,
  // uploads with `upload(blob, seconds, silences)`, polls the session and
  // hands it to `onDone`. `silences` is a JSON string, or null if unmeasured.
  // `maxSeconds` may be a function, read at every start (0 = no limit); with
  // a limit and `countdown` the timer counts down.
  function wireRecorder(root, { maxSeconds, countdown, upload, onDone, onStart }) {
    const badge = root.querySelector("#status-badge");
    const timer = root.querySelector("#timer");
    const bar = root.querySelector("#level-bar");
    const startBtn = root.querySelector("#start-btn");
    const stopBtn = root.querySelector("#stop-btn");
    const micSelect = root.querySelector("#mic-select");
    const message = root.querySelector("#message");
    let pollTimer = null;
    let alive = true;
    const status = (text, cls) => {
      badge.textContent = text;
      badge.className = `status-badge status-${cls}`;
    };
    Recorder.fillMics(micSelect);
    let recorder = null;

    const createRecorder = () => {
      const limit = (typeof maxSeconds === "function" ? maxSeconds() : maxSeconds) || 0;
      const down = countdown && limit > 0;
      timer.textContent = formatDuration(down ? limit : 0);
      return Recorder.create({
        maxSeconds: limit || undefined,
        onTick: (elapsed, level) => {
          timer.textContent = formatDuration(down ? Math.max(0, limit - elapsed) + 0.999 : elapsed);
          bar.style.width = `${Math.min(100, level * 160)}%`;
        },
        onStop,
      });
    };

    async function onStop({ blob, durationSeconds }) {
      stopBtn.disabled = true;
      micSelect.disabled = false;
      bar.style.width = "0%";
      status("Распознаётся...", "transcribing");
      try {
        const spans = await Recorder.findSilences(blob);
        const result = await upload(blob, durationSeconds, spans && JSON.stringify(spans));
        if (result.status === "error") throw new Error(result.detail || "Не удалось обработать запись.");
        poll(result.session_id);
      } catch (err) {
        status("Ошибка", "error");
        message.textContent = err.message;
        startBtn.disabled = false;
      }
    }

    function poll(id) {
      pollTimer = setTimeout(async () => {
        if (!alive) return;
        let session;
        try {
          session = await Api.getSession(id);
        } catch (err) {
          message.textContent = `Не удалось получить статус: ${err.message}`;
          startBtn.disabled = false;
          return;
        }
        if (session.status === "transcribing") return poll(id);
        startBtn.disabled = false;
        if (session.status === "error") {
          status("Ошибка", "error");
          message.textContent = session.error_message || "Не удалось распознать запись.";
          return;
        }
        status("Готово", "done");
        onDone(session);
      }, 1200);
    }

    startBtn.addEventListener("click", async () => {
      message.textContent = "";
      if (recorder) recorder.dispose();
      recorder = createRecorder();
      try {
        await recorder.start(micSelect.value);
      } catch (err) {
        message.textContent = `Не удалось получить доступ к микрофону: ${err.message}`;
        return;
      }
      Recorder.fillMics(micSelect);
      if (onStart) onStart();
      status("Запись...", "recording");
      startBtn.disabled = true;
      stopBtn.disabled = false;
      micSelect.disabled = true;
    });
    stopBtn.addEventListener("click", () => recorder && recorder.stop());

    return {
      setStartLabel: (text) => (startBtn.textContent = text),
      disable: (value) => (startBtn.disabled = value),
      dispose: () => {
        alive = false;
        clearTimeout(pollTimer);
        if (recorder) recorder.dispose();
      },
    };
  }

  return { renderMetrics, renderTimeline, renderReading, renderReport, controlsHtml, wireRecorder, EXERCISE_NAMES };
})();

// «Говорение» (2026-09-27: «Монолог» and «60 секунд» merged into one
// activity) - three switches: an own or a suggested topic, a time limit (none
// or 1-3 minutes) and one take or a series of the same thought said again.
// Every take shows its pace, fillers and pauses (no Claude); any take can be
// analysed on a click, the latest one - the smoothest - is the one suggested.
Views.speaking = (() => {
  const SETTINGS_KEY = "speaking.settings";
  const DEFAULTS = { topic: "prompt", limit: 0, series: false };
  let controls = null;

  // The switches are a per-viewer convenience: remembered in the browser,
  // with the defaults when storage is unavailable.
  function loadSettings() {
    try {
      return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") };
    } catch (err) {
      return { ...DEFAULTS };
    }
  }

  function saveSettings(settings) {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    } catch (err) {
      // not remembered this time
    }
  }

  function segmented(name, label, options, value) {
    return `
      <div class="speaking-switch">
        <div class="field-label">${escapeHtml(label)}</div>
        <div class="segmented" role="radiogroup" aria-label="${escapeHtml(label)}">
          ${options
            .map(
              ([v, text]) => `<label><input type="radio" name="${name}" value="${v}"
                ${String(v) === String(value) ? "checked" : ""} /> ${escapeHtml(text)}</label>`
            )
            .join("")}
        </div>
      </div>`;
  }

  // `preset` (older links) overrides the remembered switches for this visit;
  // a prompt id in `param` always means a suggested topic.
  async function render(container, param, preset) {
    let cfg;
    try {
      cfg = await Api.getConfig();
    } catch (err) {
      container.innerHTML = `<div class="card"><p class="muted">Не удалось загрузить: ${escapeHtml(err.message)}</p></div>`;
      return;
    }
    const promptId = param ? decodeURIComponent(param) : null;
    const settings = { ...loadSettings(), ...(preset || {}) };
    if (promptId) settings.topic = "prompt";
    if (!cfg.speaking_time_limits.includes(Number(settings.limit))) settings.limit = 0;
    const rounds = cfg.speaking_rounds;
    const limits = [[0, "Без лимита"], ...cfg.speaking_time_limits.map((s) => [s, `${s / 60} мин`])];

    // The takes of this visit: in series mode the rounds of one series, else
    // the latest single take. Each keeps its analysis once it has one.
    let takes = [];
    let series = null;
    let prompts = null;
    let analysisPicker = null;

    const warnings = [];
    if (!cfg.deepgram_configured) warnings.push("DEEPGRAM_API_KEY не настроен — запись не распознается.");
    if (!cfg.anthropic_configured) warnings.push("ANTHROPIC_API_KEY не настроен — разбор ИИ ассистента недоступен, замеры работают.");
    const langField = `
      <div>
        <label for="lang-select">Язык практики</label>
        <select id="lang-select">${cfg.language_profiles
          .map((p) => `<option value="${escapeHtml(p.key)}">${escapeHtml(p.label)}</option>`)
          .join("")}</select>
      </div>`;

    container.innerHTML = `
      <p><a href="${param ? "#/today" : "#/practice"}">← ${param ? "Сегодня" : "Занятия"}</a></p>
      <div class="card">
        <h2>Говорение</h2>
        <div class="speaking-switches">
          ${segmented("topic", "Тема", [["own", "Своя"], ["prompt", "Предложенная"]], settings.topic)}
          ${segmented("limit", "Время", limits, settings.limit)}
          ${segmented("takes", "Дубли", [["one", "Один"], ["series", `Серия ×${rounds}`]], settings.series ? "series" : "one")}
        </div>
        <div id="prompt-slot"></div>
        <p class="muted" id="own-hint">Своя тема: говорите о чём угодно — о дне, работе, планах.</p>
        <p class="muted" id="mode-hint"></p>
        ${warnings.length ? `<p class="muted">${escapeHtml(warnings.join(" "))}</p>` : ""}
        ${Speech.controlsHtml("Начать запись", langField)}
        <p id="new-series-row" hidden>
          <button type="button" class="link-button" id="new-series-btn">Новая серия</button>
        </p>
      </div>
      <div id="takes-head"></div>
      <div id="takes"></div>`;

    const langSelect = container.querySelector("#lang-select");
    langSelect.value = cfg.default_language;
    const promptSlot = container.querySelector("#prompt-slot");
    const ownHint = container.querySelector("#own-hint");
    const modeHint = container.querySelector("#mode-hint");
    const takesHost = container.querySelector("#takes");
    const newSeriesRow = container.querySelector("#new-series-row");
    const switches = [...container.querySelectorAll(".speaking-switches input")];

    const read = () => ({
      topic: container.querySelector('input[name="topic"]:checked').value,
      limit: Number(container.querySelector('input[name="limit"]:checked').value),
      series: container.querySelector('input[name="takes"]:checked').value === "series",
    });

    async function syncTopic() {
      const suggested = read().topic === "prompt";
      promptSlot.hidden = !suggested;
      ownHint.hidden = suggested;
      if (suggested && !prompts) {
        try {
          prompts = await ThemePicker.mountPrompts(promptSlot, { promptId });
        } catch (err) {
          promptSlot.innerHTML = `<p class="muted">Темы не загрузились: ${escapeHtml(err.message)}</p>`;
        }
      }
    }

    function syncHint() {
      const s = read();
      const time = s.limit ? `Запись остановится сама через ${s.limit / 60} мин.` : "Говорите 1–3 минуты и остановите запись сами.";
      const takesText = s.series
        ? `Потом ещё ${rounds - 1} раза о том же: с каждым дублем должно получаться глаже.`
        : "";
      modeHint.textContent = `${time} ${takesText} Считаем темп, слова-паразиты и паузы; первый дубль
        английской записи идёт в тему «Слова-паразиты и беглость». Разбор ошибок — по кнопке.`;
      container.querySelector("#timer").textContent = formatDuration(s.limit);
    }

    function startLabel() {
      if (!read().series) return "Начать запись";
      if (takes.length >= rounds) return "Ещё дубль";
      return `Дубль ${takes.length + 1} из ${rounds}`;
    }

    // Once a series has started its switches, language and prompt are fixed
    // until «Новая серия».
    function lock(locked) {
      switches.forEach((input) => (input.disabled = locked));
      langSelect.disabled = locked;
      newSeriesRow.hidden = !locked;
      if (prompts) (locked ? prompts.lock : prompts.unlock)();
    }

    switches.forEach((input) =>
      input.addEventListener("change", () => {
        // Single takes and a series do not mix on screen.
        if (input.name === "takes") {
          takes = [];
          drawTakes();
        }
        syncTopic();
        syncHint();
        controls.setStartLabel(startLabel());
      })
    );
    container.querySelector("#new-series-btn").addEventListener("click", () => {
      series = null;
      takes = [];
      lock(false);
      drawTakes();
      controls.setStartLabel(startLabel());
    });

    controls = Speech.wireRecorder(container, {
      maxSeconds: () => read().limit,
      countdown: true,
      onStart: () => {
        const s = read();
        saveSettings(s);
        if (s.series) {
          lock(true);
        } else {
          // A single take: fixed only while it is being recorded.
          switches.forEach((input) => (input.disabled = true));
          langSelect.disabled = true;
        }
      },
      upload: async (blob, seconds, silences) => {
        const s = read();
        if (!s.series) {
          switches.forEach((input) => (input.disabled = false));
          langSelect.disabled = false;
        }
        const prompt = s.topic === "prompt" && prompts ? prompts.current() : null;
        if (s.topic === "prompt" && !prompt && !series) throw new Error("Сначала выберите тему для рассказа.");
        const result = await Api.uploadSession(blob, {
          language: langSelect.value,
          durationSeconds: seconds,
          mimeType: blob.type,
          kind: "monologue",
          drill: {
            prompt_id: series ? null : prompt && prompt.id,
            time_limit: series || !s.limit ? null : s.limit,
            series,
            silences,
          },
        });
        if (s.series && !series) series = result.session_id;
        return result;
      },
      onDone: (session) => {
        if (read().series) takes.push(session);
        else takes = [session];
        drawTakes();
        controls.setStartLabel(startLabel());
      },
    });

    // The analysis «уклон» is one picker for all the takes on screen.
    async function drawTakes() {
      const head = container.querySelector("#takes-head");
      if (!takes.length) {
        head.innerHTML = "";
        takesHost.innerHTML = "";
        return;
      }
      if (!analysisPicker || analysisPicker.language !== takes[0].language) {
        head.innerHTML = `<div class="card"><h2>Разбор ИИ ассистента</h2>
          <p class="muted">Каждый дубль можно разобрать отдельно; лучше всего — последний, он самый гладкий.
            Ошибки попадут в карточки.</p><div id="analyze-theme"></div></div>`;
        analysisPicker = {
          language: takes[0].language,
          picker: await ThemePicker.mountForAnalysis(head.querySelector("#analyze-theme"), takes[0].language),
        };
      }
      const seriesMode = read().series;
      const newest = [...takes].reverse();
      takesHost.innerHTML = `
        ${takes.length > 1 ? renderComparison(takes) : ""}
        ${newest.map((take, i) => renderTake(take, takes.length - i, i === 0, seriesMode)).join("")}`;
      for (const take of takes) {
        const slot = takesHost.querySelector(`[data-analysis="${take.id}"]`);
        if (take.analysis) await renderAnalysis(slot, take.analysis);
      }
      takesHost.querySelectorAll("[data-analyze]").forEach((button) =>
        button.addEventListener("click", () => analyze(button))
      );
    }

    async function analyze(button) {
      const take = takes.find((t) => t.id === button.dataset.analyze);
      const slot = takesHost.querySelector(`[data-analysis="${take.id}"]`);
      button.disabled = true;
      button.textContent = "Анализируем...";
      try {
        const theme = analysisPicker && analysisPicker.picker ? analysisPicker.picker.value() : null;
        const result = await Api.analyzeSession(take.id, !!take.analysis, theme);
        take.analysis = result.analysis;
        await renderAnalysis(slot, result.analysis);
      } catch (err) {
        slot.innerHTML = `<p class="muted">Ошибка анализа: ${escapeHtml(err.message)}</p>`;
      } finally {
        button.disabled = false;
        button.textContent = analyzeLabel(take);
      }
    }

    await syncTopic();
    syncHint();
    controls.setStartLabel(startLabel());
  }

  function analyzeLabel(take) {
    return take.analysis ? "Анализировать повторно" : "Анализировать (ИИ ассистент)";
  }

  function renderTake(take, number, latest, seriesMode) {
    const note = !seriesMode || !latest
      ? ""
      : number === 1
        ? "Теперь ещё раз о том же: постарайтесь сказать то же самое, но ровнее и с меньшим числом «uh»."
        : "Сравните дубли выше — меньше пауз и паразитов значит, мысль уже «уложилась».";
    return `
      <div class="card take-card${latest ? " is-latest" : ""}">
        <h2>${seriesMode ? `Дубль ${number}` : "Запись"}${latest && seriesMode && number > 1 ? ` <small class="muted">· последний</small>` : ""}</h2>
        ${Speech.renderMetrics(take.speech && take.speech.metrics)}
        ${Speech.renderTimeline(take.speech && take.speech.timeline)}
        ${note ? `<p class="muted">${note}</p>` : ""}
        <div class="button-row">
          <button data-analyze="${escapeHtml(take.id)}"${latest ? "" : ` class="secondary"`}>${analyzeLabel(take)}</button>
          <a href="#/session/${encodeURIComponent(take.id)}">Запись и транскрипт →</a>
        </div>
        <div data-analysis="${escapeHtml(take.id)}"></div>
      </div>`;
  }

  function renderComparison(takes) {
    const rows = [
      ["Темп, сл/мин", (m) => m.wpm],
      ["Паразиты / мин", (m) => m.fillers_per_min],
      ["Паузы", (m) => m.long_pauses],
      ["Повторы", (m) => m.repeats],
      ["Слов", (m) => m.words],
    ];
    const metrics = takes.map((t) => (t.speech && t.speech.metrics) || {});
    return `
      <div class="card">
        <h2>Сравнение дублей</h2>
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr><th></th>${takes.map((_, i) => `<th>${i + 1}</th>`).join("")}</tr></thead>
            <tbody>${rows
              .map(
                ([label, pick]) => `<tr><td>${label}</td>${metrics
                  .map((m) => `<td class="num">${pick(m) == null ? "—" : escapeHtml(pick(m))}</td>`)
                  .join("")}</tr>`
              )
              .join("")}</tbody>
          </table>
        </div>
      </div>`;
  }

  function dispose() {
    if (controls) controls.dispose();
    controls = null;
  }

  return { render, dispose };
})();

// Older links: #/record[/<prompt>] was «Монолог» (one take, no limit),
// #/talk[/<prompt>] was «60 секунд» (a series of one-minute takes). A plain
// #/record keeps the remembered switches.
Views.record = {
  render: (container, param) =>
    Views.speaking.render(container, param, param ? { limit: 0, series: false } : null),
  dispose: () => Views.speaking.dispose(),
};

Views.talk = {
  render: (container, param) =>
    Views.speaking.render(container, param, { topic: "prompt", limit: 60, series: true }),
  dispose: () => Views.speaking.dispose(),
};

// Shadowing: read a passage of your own improved_version aloud.
Views.shadowing = (() => {
  let controls = null;

  async function render(container, param) {
    let data;
    let cfg;
    try {
      [data, cfg] = await Promise.all([Api.getPassages(), Api.getConfig()]);
    } catch (err) {
      container.innerHTML = `<div class="card"><p class="muted">Не удалось загрузить: ${escapeHtml(err.message)}</p></div>`;
      return;
    }
    const back = `<p><a href="${param ? "#/today" : "#/practice"}">← ${param ? "Сегодня" : "Занятия"}</a></p>`;
    const passages = data.passages;
    if (!passages.length) {
      container.innerHTML = `${back}<div class="card"><h2>Shadowing</h2><div class="empty-state">
        Нужен хотя бы один проанализированный английский монолог — читать будем его «улучшенную версию».</div></div>`;
      return;
    }
    // Opened from «Сегодня» with "<session>:<n>"; otherwise the suggested one.
    const [wantedSession, wantedIndex] = param ? param.split(":") : [data.next.session_id, String(data.next.index)];
    const current0 = passages.findIndex((p) => p.session_id === wantedSession && String(p.index) === wantedIndex);
    let current = Math.max(0, current0);
    const canSpeak = "speechSynthesis" in window;

    container.innerHTML = `
      ${back}
      <div class="card">
        <h2>Shadowing</h2>
        <p class="muted">Прочитайте вслух отрывок из «улучшенной версии» своего монолога — так, как
          сказал бы носитель: в естественном темпе, связно. Deepgram распознает запись, и мы отметим
          слова, которые прозвучали не так, пропущены или распознаны неуверенно.</p>
        <div id="passage-slot"></div>
        ${cfg.deepgram_configured ? "" : `<p class="muted">DEEPGRAM_API_KEY не настроен — запись не распознается.</p>`}
        ${Speech.controlsHtml("Читать вслух")}
      </div>
      <div id="result"></div>`;

    const slot = container.querySelector("#passage-slot");
    const result = container.querySelector("#result");
    const showPassage = () => {
      const p = passages[current];
      const history = p.attempts
        ? `прочитан ${p.attempts} раз(а), лучший результат ${Math.round((p.best_score || 0) * 100)}%`
        : "ещё не читали";
      slot.innerHTML = `
        <blockquote class="improved-version shadow-passage" lang="en">${escapeHtml(p.text)}</blockquote>
        <p class="muted">Монолог от ${escapeHtml(p.recorded_at.slice(0, 10))} · отрывок ${p.index + 1} · ${history}</p>
        <div class="button-row">
          ${canSpeak ? `<button type="button" class="secondary" id="listen">▶ Послушать</button>` : ""}
          ${passages.length > 1 ? `<button type="button" class="secondary" id="next-passage">Другой отрывок</button>` : ""}
        </div>`;
      const listen = slot.querySelector("#listen");
      if (listen) listen.addEventListener("click", () => speak(p.text));
      const next = slot.querySelector("#next-passage");
      if (next) {
        next.addEventListener("click", () => {
          current = (current + 1) % passages.length;
          result.innerHTML = "";
          showPassage();
        });
      }
    };
    showPassage();

    controls = Speech.wireRecorder(container, {
      maxSeconds: 180,
      onStart: () => window.speechSynthesis && window.speechSynthesis.cancel(),
      upload: (blob, seconds, silences) =>
        Api.uploadSession(blob, {
          language: "en-US",
          durationSeconds: seconds,
          mimeType: blob.type,
          kind: "shadowing",
          drill: {
            source_session_id: passages[current].session_id,
            passage: passages[current].index,
            silences,
          },
        }),
      onDone: (session) => {
        const p = passages[current];
        const score = session.speech && session.speech.reading ? session.speech.reading.score : null;
        if (score != null) {
          p.attempts += 1;
          p.best_score = Math.max(p.best_score || 0, score);
        }
        controls.setStartLabel("Прочитать ещё раз");
        showPassage();
        result.innerHTML = `
          <div class="card">
            <h2>Результат</h2>
            ${Speech.renderReport(session)}
            <p><a href="#/session/${encodeURIComponent(session.id)}">Запись →</a></p>
          </div>`;
      },
    });
  }

  // The browser's own English voice reads the passage: free and offline on
  // most systems. Only a model to follow - the take is what gets checked.
  function speak(text) {
    const synth = window.speechSynthesis;
    synth.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "en-US";
    utterance.rate = 0.9;
    const voice = synth.getVoices().find((v) => v.lang && v.lang.startsWith("en"));
    if (voice) utterance.voice = voice;
    synth.speak(utterance);
  }

  function dispose() {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    if (controls) controls.dispose();
    controls = null;
  }

  return { render, dispose };
})();
