// Record view: browser microphone capture (MediaRecorder) -> upload -> poll
// for the transcript -> on-demand Claude analysis. Replaces the old
// sounddevice/Tkinter recording loop; the same overall states apply
// (ready -> recording -> transcribing -> done/error).
window.Views = window.Views || {};

Views.record = (() => {
  let stream = null;
  let mediaRecorder = null;
  let chunks = [];
  let audioCtx = null;
  let analyser = null;
  let rafId = null;
  let timerId = null;
  let pollTimer = null;
  let startTime = null;
  let container = null;

  function el(html) {
    // Returns a DocumentFragment so multiple top-level sibling elements in
    // `html` (the recording card + the transcript card) all survive - a
    // plain firstElementChild would silently drop everything after the
    // first one.
    const wrapper = document.createElement("div");
    wrapper.innerHTML = html.trim();
    const fragment = document.createDocumentFragment();
    while (wrapper.firstChild) fragment.appendChild(wrapper.firstChild);
    return fragment;
  }

  function pickMimeType() {
    const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/ogg"];
    for (const type of candidates) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(type)) return type;
    }
    return "";
  }

  function setStatus(text, cls) {
    const badge = container.querySelector("#status-badge");
    badge.textContent = text;
    badge.className = `status-badge status-${cls}`;
  }

  function setLevel(percent) {
    const bar = container.querySelector("#level-bar");
    if (bar) bar.style.width = `${Math.min(100, Math.max(0, percent))}%`;
  }

  async function render(root) {
    container = root;
    container.appendChild(
      el(`
      <div class="card">
        <h2>Запись</h2>
        <p class="muted">Говорите свободно — слова-паразиты, паузы, ошибки и незаконченные фразы
          сохраняются как есть, это важно для последующего анализа.</p>
        <div class="row">
          <div>
            <label for="mic-select">Микрофон</label>
            <select id="mic-select"></select>
          </div>
          <div>
            <label for="lang-select">Язык практики</label>
            <select id="lang-select"></select>
          </div>
        </div>
        <div class="status-line">
          <span id="status-badge" class="status-badge status-ready">Готово</span>
          <span id="timer" class="timer">00:00</span>
        </div>
        <div class="level-meter"><div id="level-bar"></div></div>
        <div class="button-row">
          <button id="start-btn">Начать запись</button>
          <button id="stop-btn" class="danger" disabled>Остановить</button>
        </div>
        <p id="message" class="muted"></p>
      </div>
      <div class="card" id="transcript-card" hidden>
        <h2>Транскрипт</h2>
        <div id="transcript" class="transcript-box"></div>
        <div class="button-row">
          <button id="analyze-btn" class="secondary">Анализировать (Claude)</button>
          <button id="copy-btn" class="secondary">Скопировать</button>
        </div>
        <div id="analysis-slot"></div>
      </div>
    `)
    );

    const micSelect = container.querySelector("#mic-select");
    const langSelect = container.querySelector("#lang-select");
    const startBtn = container.querySelector("#start-btn");
    const stopBtn = container.querySelector("#stop-btn");
    const message = container.querySelector("#message");

    let cfg;
    try {
      cfg = await Api.getConfig();
    } catch (err) {
      message.textContent = `Не удалось загрузить настройки: ${err.message}`;
      return;
    }

    langSelect.innerHTML = cfg.language_profiles
      .map((p) => `<option value="${escapeHtml(p.key)}">${escapeHtml(p.label)}</option>`)
      .join("");
    langSelect.value = cfg.default_language;

    const warnings = [];
    if (!cfg.deepgram_configured) {
      warnings.push("DEEPGRAM_API_KEY не настроен — распознавание речи не будет работать.");
    }
    if (!cfg.anthropic_configured) {
      warnings.push("ANTHROPIC_API_KEY не настроен — кнопка «Анализировать» не будет работать.");
    }
    message.textContent = warnings.join(" ");

    async function populateMics() {
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const mics = devices.filter((d) => d.kind === "audioinput");
        micSelect.innerHTML = mics.length
          ? mics.map((d, i) => `<option value="${d.deviceId}">${escapeHtml(d.label || "Микрофон " + (i + 1))}</option>`).join("")
          : `<option value="">Микрофон не найден</option>`;
      } catch (err) {
        micSelect.innerHTML = `<option value="">(нет доступа к устройствам)</option>`;
      }
    }
    await populateMics();

    function tick() {
      const elapsed = (Date.now() - startTime) / 1000;
      container.querySelector("#timer").textContent = formatDuration(elapsed);
      if (analyser) {
        const data = new Uint8Array(analyser.frequencyBinCount);
        analyser.getByteTimeDomainData(data);
        let peak = 0;
        for (let i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i] - 128) / 128);
        setLevel(peak * 160);
      }
      rafId = requestAnimationFrame(tick);
    }

    startBtn.addEventListener("click", async () => {
      message.textContent = "";
      try {
        const deviceId = micSelect.value;
        stream = await navigator.mediaDevices.getUserMedia({
          audio: deviceId ? { deviceId: { exact: deviceId } } : true,
        });
      } catch (err) {
        message.textContent = `Не удалось получить доступ к микрофону: ${err.message}`;
        return;
      }
      await populateMics(); // device labels only appear once permission is granted

      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const source = audioCtx.createMediaStreamSource(stream);
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);

      chunks = [];
      const mimeType = pickMimeType();
      mediaRecorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      mediaRecorder.addEventListener("dataavailable", (event) => {
        if (event.data && event.data.size > 0) chunks.push(event.data);
      });
      mediaRecorder.addEventListener("stop", onRecordingStopped);
      mediaRecorder.start();

      startTime = Date.now();
      rafId = requestAnimationFrame(tick);

      setStatus("Запись...", "recording");
      startBtn.disabled = true;
      stopBtn.disabled = false;
      micSelect.disabled = true;
      langSelect.disabled = true;
      container.querySelector("#transcript-card").hidden = true;
    });

    stopBtn.addEventListener("click", () => {
      if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
    });

    async function onRecordingStopped() {
      cancelAnimationFrame(rafId);
      const durationSeconds = (Date.now() - startTime) / 1000;
      stream.getTracks().forEach((t) => t.stop());
      if (audioCtx) {
        try {
          await audioCtx.close();
        } catch (err) {
          /* already closed */
        }
      }

      startBtn.disabled = false;
      stopBtn.disabled = true;
      micSelect.disabled = false;
      langSelect.disabled = false;
      setLevel(0);
      setStatus("Транскрибируется...", "transcribing");

      const blob = new Blob(chunks, { type: (mediaRecorder && mediaRecorder.mimeType) || "audio/webm" });
      try {
        const result = await Api.uploadSession(blob, {
          language: langSelect.value,
          durationSeconds,
          mimeType: blob.type,
        });
        if (result.status === "error") {
          setStatus("Ошибка", "error");
          message.textContent = result.detail || "Не удалось обработать запись.";
          return;
        }
        pollSession(result.session_id);
      } catch (err) {
        setStatus("Ошибка", "error");
        message.textContent = `Не удалось загрузить запись: ${err.message}`;
      }
    }

    function pollSession(id) {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(async () => {
        let session;
        try {
          session = await Api.getSession(id);
        } catch (err) {
          message.textContent = `Не удалось получить статус: ${err.message}`;
          return;
        }
        if (session.status === "transcribing") {
          pollSession(id);
          return;
        }
        if (session.status === "error") {
          setStatus("Ошибка", "error");
          message.textContent = session.error_message || "Не удалось получить транскрипт.";
          return;
        }
        setStatus("Готово", "done");
        message.textContent = "";
        await showTranscript(session);
      }, 1500);
    }

    async function showTranscript(session) {
      const card = container.querySelector("#transcript-card");
      card.hidden = false;
      container.querySelector("#transcript").textContent = session.transcript || "(пустой транскрипт)";

      const analyzeBtn = container.querySelector("#analyze-btn");
      const copyBtn = container.querySelector("#copy-btn");
      const slot = container.querySelector("#analysis-slot");

      await renderAnalysis(slot, session.analysis);

      analyzeBtn.onclick = async () => {
        analyzeBtn.disabled = true;
        analyzeBtn.textContent = "Анализируем...";
        try {
          const result = await Api.analyzeSession(session.id, !!session.analysis);
          session.analysis = result.analysis;
          await renderAnalysis(slot, result.analysis);
        } catch (err) {
          slot.innerHTML = `<p class="muted">Ошибка анализа: ${escapeHtml(err.message)}</p>`;
        } finally {
          analyzeBtn.disabled = false;
          analyzeBtn.textContent = "Анализировать (Claude)";
        }
      };

      copyBtn.onclick = async () => {
        try {
          await navigator.clipboard.writeText(session.transcript || "");
          copyBtn.textContent = "Скопировано!";
          setTimeout(() => (copyBtn.textContent = "Скопировать"), 1500);
        } catch (err) {
          message.textContent = "Не удалось скопировать в буфер обмена.";
        }
      };
    }
  }

  function dispose() {
    clearTimeout(pollTimer);
    cancelAnimationFrame(rafId);
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      mediaRecorder.removeEventListener("stop", () => {});
      try {
        mediaRecorder.stop();
      } catch (err) {
        /* ignore */
      }
    }
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (audioCtx) {
      try {
        audioCtx.close();
      } catch (err) {
        /* ignore */
      }
    }
  }

  return { render, dispose };
})();
