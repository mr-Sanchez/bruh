// Spoken drills (Stage 6) - measured from Deepgram's word timings and the
// silences in the audio (Recorder.findSilences), no Claude:
//   #/talk[/<prompt>]            «60 секунд»: one prompt, three one-minute takes
//                                in a row; pace, fillers and pauses per take
//   #/shadowing[/<session>:<n>]  read a passage of your own improved_version
//                                aloud; missed / misheard / unclear words marked
// Speech.* renders the measurements; the session page uses it too.
window.Views = window.Views || {};

const Speech = (() => {
  const EXERCISE_NAMES = { talk: "60 секунд", shadowing: "Shadowing" };

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

  // Recording controls shared by both drills.
  function controlsHtml(startLabel) {
    return `
      <div class="row">
        <div>
          <label for="mic-select">Микрофон</label>
          <select id="mic-select"></select>
        </div>
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

    const recorder = Recorder.create({
      maxSeconds,
      onTick: (elapsed, level) => {
        timer.textContent = formatDuration(countdown ? Math.max(0, maxSeconds - elapsed) + 0.999 : elapsed);
        bar.style.width = `${Math.min(100, level * 160)}%`;
      },
      onStop: async ({ blob, durationSeconds }) => {
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
      },
    });

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
    stopBtn.addEventListener("click", () => recorder.stop());

    return {
      setStartLabel: (text) => (startBtn.textContent = text),
      disable: (value) => (startBtn.disabled = value),
      dispose: () => {
        alive = false;
        clearTimeout(pollTimer);
        recorder.dispose();
      },
    };
  }

  return { renderMetrics, renderTimeline, renderReading, renderReport, controlsHtml, wireRecorder, EXERCISE_NAMES };
})();

// «60 секунд»: the same prompt three times, a minute each, compared side by side.
Views.talk = (() => {
  let controls = null;

  async function render(container, param) {
    let cfg;
    let talks;
    try {
      [cfg, talks] = await Promise.all([Api.getConfig(), Api.getTalks()]);
    } catch (err) {
      container.innerHTML = `<div class="card"><p class="muted">Не удалось загрузить: ${escapeHtml(err.message)}</p></div>`;
      return;
    }
    // #/talk/<prompt id> from «Сегодня»; a plain #/talk opens the day's prompt.
    const promptId = param ? decodeURIComponent(param) : talks.prompt.id;
    const rounds = cfg.talk_rounds;
    const takes = [];
    let series = null;

    container.innerHTML = `
      <p><a href="${param ? "#/today" : "#/practice"}">← ${param ? "Сегодня" : "Занятия"}</a></p>
      <div class="card">
        <h2>60 секунд</h2>
        <div id="prompt-slot"></div>
        <p class="muted">Говорите ровно минуту — запись остановится сама. Потом ещё
          ${rounds - 1} раза на ту же тему: с каждым разом должно получаться глаже. Считаем темп,
          слова-паразиты (Deepgram) и паузы (по самой записи) — без ИИ ассистента.
          Первая попытка идёт в тему «Слова-паразиты и беглость».</p>
        ${cfg.deepgram_configured ? "" : `<p class="muted">DEEPGRAM_API_KEY не настроен — запись не распознается.</p>`}
        ${Speech.controlsHtml(`Попытка 1 из ${rounds}`)}
      </div>
      <div id="takes"></div>`;

    const slot = container.querySelector("#prompt-slot");
    let prompts = null;
    try {
      prompts = await ThemePicker.mountPrompts(slot, { promptId });
    } catch (err) {
      slot.innerHTML = `<p class="muted">Темы не загрузились: ${escapeHtml(err.message)}</p>`;
    }

    const takesHost = container.querySelector("#takes");
    controls = Speech.wireRecorder(container, {
      maxSeconds: cfg.talk_seconds,
      countdown: true,
      // The series keeps its prompt: no switching once the first take starts.
      onStart: () => {
        if (prompts) prompts.lock();
      },
      upload: async (blob, seconds, silences) => {
        const prompt = prompts && prompts.current();
        if (!prompt && !series) throw new Error("Сначала выберите тему для рассказа.");
        const result = await Api.uploadSession(blob, {
          language: "en-US",
          durationSeconds: seconds,
          mimeType: blob.type,
          kind: "talk",
          drill: { prompt_id: prompt ? prompt.id : null, series, silences },
        });
        if (!series) series = result.session_id;
        return result;
      },
      onDone: (session) => {
        takes.push(session);
        takesHost.innerHTML = renderTakes(takes, rounds);
        if (takes.length >= rounds) {
          controls.setStartLabel("Ещё попытка");
        } else {
          controls.setStartLabel(`Попытка ${takes.length + 1} из ${rounds}`);
        }
      },
    });
  }

  function renderTakes(takes, rounds) {
    const latest = takes[takes.length - 1];
    const compare = takes.length > 1 ? renderComparison(takes) : "";
    const done = takes.length >= rounds;
    return `
      ${compare}
      <div class="card">
        <h2>Попытка ${takes.length}</h2>
        ${Speech.renderMetrics(latest.speech && latest.speech.metrics)}
        ${Speech.renderTimeline(latest.speech && latest.speech.timeline)}
        <p class="muted">${
          done
            ? "Серия готова. Сравните попытки выше — меньше пауз и паразитов значит, мысль уже «уложилась»."
            : "Теперь ещё раз о том же: постарайтесь сказать то же самое, но ровнее и с меньшим числом «uh»."
        }</p>
        <p><a href="#/session/${encodeURIComponent(latest.id)}">Запись и транскрипт →</a></p>
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
        <h2>Сравнение попыток</h2>
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
