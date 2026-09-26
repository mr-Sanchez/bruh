// Record view: browser microphone capture (MediaRecorder) -> upload -> poll
// for the transcript -> on-demand Claude analysis. Replaces the old
// sounddevice/Tkinter recording loop; the same overall states apply
// (ready -> recording -> transcribing -> done/error).
//
// The same view runs the picture description (#/picture, Views.picture at the
// bottom): a picture is chosen, downscaled in the browser and uploaded with
// the take, which can be spoken (default) or typed.
window.Views = window.Views || {};

Views.record = (() => {
  let recorder = null;
  let pollTimer = null;
  let container = null;
  // Picture mode: the downscaled JPEG to upload with every take.
  let pictureBlob = null;
  let pictureUrl = null;
  let onPaste = null;

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

  function setStatus(text, cls) {
    const badge = container.querySelector("#status-badge");
    badge.textContent = text;
    badge.className = `status-badge status-${cls}`;
  }

  function setLevel(percent) {
    const bar = container.querySelector("#level-bar");
    if (bar) bar.style.width = `${Math.min(100, Math.max(0, percent))}%`;
  }

  // Draws the image onto a canvas with its long side at most `maxSide` px and
  // re-encodes it as JPEG: fewer tokens for Claude, and no EXIF data leaves
  // the browser. Transparent areas become white instead of black.
  function downscaleImage(file, maxSide) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(url);
        const scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(
          (blob) => (blob ? resolve(blob) : reject(new Error("не удалось сжать картинку"))),
          "image/jpeg",
          0.85
        );
      };
      img.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new Error("браузер не смог открыть этот файл как картинку"));
      };
      img.src = url;
    });
  }

  function pictureCard() {
    return `
      <div class="picture-picker">
        <div id="picture-drop" class="picture-drop">
          <img id="picture-preview" class="picture-preview" alt="Картинка для описания" hidden />
          <div id="picture-empty" class="picture-empty">
            <span class="activity-icon tone-orange">${Icons.image}</span>
            <p>Перетащите картинку сюда или вставьте из буфера (Ctrl+V)</p>
            <label class="button-like">Выбрать файл
              <input type="file" id="picture-input" accept="image/*" hidden /></label>
          </div>
        </div>
        <div class="picture-toolbar">
          <label class="button-like secondary" id="picture-change" hidden>Другая картинка
            <input type="file" id="picture-input-2" accept="image/*" hidden /></label>
          <div class="segmented" role="radiogroup" aria-label="Как описывать">
            <label><input type="radio" name="input-mode" value="voice" checked /> Голосом</label>
            <label><input type="radio" name="input-mode" value="text" /> Текстом</label>
          </div>
        </div>
      </div>
      <p class="muted">Опишите картинку: кто на ней, где это, что происходит, какое настроение.
        1–2 минуты. После анализа Claude подскажет, что вы не упомянули, и даст слова для этой сцены.</p>`;
  }

  // `param` is a speaking-prompt id when the view is opened from «Сегодня»
  // (#/record/<theme>:<n>); a plain #/record is a free monologue. `mode` is
  // "picture" for the picture description.
  async function render(root, param, mode) {
    container = root;
    const picture = mode === "picture";
    // A lesson's spoken task (#/speak/<lesson>:<task>): a monologue on that task.
    const [lessonId, taskId] =
      mode === "lesson" && param ? decodeURIComponent(param).split(":") : [null, null];
    const promptId = !picture && !lessonId && param ? decodeURIComponent(param) : null;
    container.appendChild(
      el(`
      ${promptId == null ? "" : `<p><a href="#/today">← Сегодня</a></p>`}
      ${picture ? `<p><a href="#/practice">← Занятия</a></p>` : ""}
      ${lessonId ? `<p><a href="#/practice/${encodeURIComponent(lessonId)}">← К уроку</a></p>` : ""}
      <div class="card">
        <h2>${picture ? "Описание картинки" : lessonId ? "Устное задание урока" : "Монолог"}</h2>
        <div id="prompt-slot"></div>
        ${picture ? pictureCard() : ""}
        <p class="muted" id="voice-hint">Говорите свободно — слова-паразиты, паузы, ошибки и незаконченные фразы
          сохраняются как есть, это важно для последующего анализа.</p>
        <div class="row">
          <div id="mic-field">
            <label for="mic-select">Микрофон</label>
            <select id="mic-select"></select>
          </div>
          <div>
            <label for="lang-select">Язык практики</label>
            <select id="lang-select"></select>
          </div>
        </div>
        <div id="voice-controls">
          <div class="status-line">
            <span id="status-badge" class="status-badge status-ready">Готово</span>
            <span id="timer" class="timer">00:00</span>
          </div>
          <div class="level-meter"><div id="level-bar"></div></div>
          <div class="button-row">
            <button id="start-btn">Начать запись</button>
            <button id="stop-btn" class="danger" disabled>Остановить</button>
          </div>
        </div>
        <div id="text-controls" hidden>
          <label for="typed-text">Ваше описание</label>
          <textarea id="typed-text" rows="7" maxlength="10000"
            placeholder="In this picture I can see..."></textarea>
          <p class="muted">Текст сохраняется как есть — опечатки и ошибки тоже, их разберёт анализ.</p>
          <div class="button-row"><button id="send-text-btn">Отправить</button></div>
        </div>
        <p id="message" class="muted"></p>
      </div>
      <div class="card" id="transcript-card" hidden>
        <h2 id="transcript-title">Транскрипт</h2>
        <div id="transcript" class="transcript-box"></div>
        <div class="button-row">
          <button id="analyze-btn" class="secondary">Анализировать (Claude)</button>
          <button id="copy-btn" class="secondary">Скопировать</button>
        </div>
        <div id="analyze-theme"></div>
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

    let lessonTask = null;
    if (lessonId) {
      const slot = container.querySelector("#prompt-slot");
      try {
        const data = await Api.getLessonTasks(lessonId);
        lessonTask = data.tasks.find((t) => t.id === taskId) || null;
        slot.innerHTML = lessonTask
          ? `<div class="speaking-prompt">
               <div class="muted">${escapeHtml(data.lesson.label)} · ${escapeHtml(lessonTask.hint)}</div>
               <p class="speaking-question">${escapeHtml(lessonTask.question)}</p>
               ${lessonTask.use ? `<p><strong>Используйте:</strong> ${escapeHtml(lessonTask.use)}</p>` : ""}
               <p class="muted">Говорите 1–2 минуты. После «Анализировать» Claude отдельно оценит,
                 как вы применили правило урока; ошибки попадут в карточки.</p>
             </div>`
          : `<p class="muted">Задание не найдено — вернитесь к уроку.</p>`;
      } catch (err) {
        slot.innerHTML = `<p class="muted">Задание не загрузилось: ${escapeHtml(err.message)}</p>`;
      }
      if (!lessonTask) startBtn.disabled = true;
    }

    if (promptId != null) {
      ThemePicker.mountPrompts(container.querySelector("#prompt-slot"), {
        promptId,
        note: "Говорите 1–3 минуты. Потом нажмите «Анализировать» — ошибки попадут в карточки.",
      }).catch((err) => {
        container.querySelector("#prompt-slot").innerHTML = `<p class="muted">Темы не загрузились: ${escapeHtml(err.message)}</p>`;
      });
    }

    const warnings = [];
    if (!cfg.deepgram_configured) {
      warnings.push("DEEPGRAM_API_KEY не настроен — распознавание речи не будет работать.");
    }
    if (!cfg.anthropic_configured) {
      warnings.push("ANTHROPIC_API_KEY не настроен — кнопка «Анализировать» не будет работать.");
    }
    message.textContent = warnings.join(" ");

    if (picture) setupPicture(cfg);

    await Recorder.fillMics(micSelect);

    function setupPicture(cfg) {
      const preview = container.querySelector("#picture-preview");
      const empty = container.querySelector("#picture-empty");
      const change = container.querySelector("#picture-change");
      const drop = container.querySelector("#picture-drop");
      const sendText = container.querySelector("#send-text-btn");

      const refresh = () => {
        const ready = !!pictureBlob;
        preview.hidden = !ready;
        empty.hidden = ready;
        change.hidden = !ready;
        startBtn.disabled = !ready || (recorder && recorder.recording);
        sendText.disabled = !ready;
      };

      async function usePicture(file) {
        if (!file || !file.type.startsWith("image/")) {
          message.textContent = "Это не картинка — выберите файл изображения.";
          return;
        }
        try {
          pictureBlob = await downscaleImage(file, cfg.image_max_side || 1000);
        } catch (err) {
          message.textContent = `Не удалось открыть картинку: ${err.message}`;
          return;
        }
        if (pictureUrl) URL.revokeObjectURL(pictureUrl);
        pictureUrl = URL.createObjectURL(pictureBlob);
        preview.src = pictureUrl;
        message.textContent = "";
        container.querySelector("#transcript-card").hidden = true;
        refresh();
      }

      container.querySelectorAll('input[type="file"]').forEach((input) =>
        input.addEventListener("change", () => {
          usePicture(input.files[0]);
          input.value = "";
        })
      );
      drop.addEventListener("dragover", (event) => {
        event.preventDefault();
        drop.classList.add("is-dragover");
      });
      drop.addEventListener("dragleave", () => drop.classList.remove("is-dragover"));
      drop.addEventListener("drop", (event) => {
        event.preventDefault();
        drop.classList.remove("is-dragover");
        usePicture(event.dataTransfer.files[0]);
      });
      // Paste anywhere on the page, unless the user is typing into the text box.
      onPaste = (event) => {
        if (event.target && event.target.id === "typed-text") return;
        const item = [...(event.clipboardData ? event.clipboardData.items : [])].find((i) =>
          i.type.startsWith("image/")
        );
        if (item) {
          event.preventDefault();
          usePicture(item.getAsFile());
        }
      };
      document.addEventListener("paste", onPaste);

      container.querySelectorAll('input[name="input-mode"]').forEach((radio) =>
        radio.addEventListener("change", () => {
          const typed = radio.value === "text" && radio.checked;
          container.querySelector("#voice-controls").hidden = typed;
          container.querySelector("#mic-field").hidden = typed;
          container.querySelector("#voice-hint").hidden = typed;
          container.querySelector("#text-controls").hidden = !typed;
        })
      );

      sendText.addEventListener("click", async () => {
        const text = container.querySelector("#typed-text").value;
        if (!text.trim()) {
          message.textContent = "Напишите описание перед отправкой.";
          return;
        }
        sendText.disabled = true;
        message.textContent = "Сохраняем...";
        try {
          const result = await Api.submitText({
            text,
            language: langSelect.value,
            kind: "picture",
            image: pictureBlob,
          });
          message.textContent = "";
          await showTranscript(await Api.getSession(result.session_id));
        } catch (err) {
          message.textContent = `Не удалось сохранить текст: ${err.message}`;
        } finally {
          sendText.disabled = !pictureBlob;
        }
      });

      refresh();
    }

    startBtn.addEventListener("click", async () => {
      message.textContent = "";
      if (picture && !pictureBlob) {
        message.textContent = "Сначала выберите картинку.";
        return;
      }
      recorder = Recorder.create({
        onTick: (elapsed, level) => {
          container.querySelector("#timer").textContent = formatDuration(elapsed);
          setLevel(level * 160);
        },
        onStop: onRecordingStopped,
      });
      try {
        await recorder.start(micSelect.value);
      } catch (err) {
        recorder = null;
        message.textContent = `Не удалось получить доступ к микрофону: ${err.message}`;
        return;
      }
      await Recorder.fillMics(micSelect); // device labels only appear once permission is granted

      setStatus("Запись...", "recording");
      startBtn.disabled = true;
      stopBtn.disabled = false;
      micSelect.disabled = true;
      langSelect.disabled = true;
      container.querySelector("#transcript-card").hidden = true;
    });

    stopBtn.addEventListener("click", () => {
      if (recorder) recorder.stop();
    });

    async function onRecordingStopped({ blob, durationSeconds }) {
      startBtn.disabled = picture && !pictureBlob;
      stopBtn.disabled = true;
      micSelect.disabled = false;
      langSelect.disabled = false;
      setLevel(0);
      setStatus("Транскрибируется...", "transcribing");

      try {
        const result = await Api.uploadSession(blob, {
          language: langSelect.value,
          durationSeconds,
          mimeType: blob.type,
          kind: picture ? "picture" : "monologue",
          image: picture ? pictureBlob : null,
          lesson: lessonTask ? { lesson_id: lessonId, task_id: lessonTask.id } : null,
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
      container.querySelector("#transcript-title").textContent =
        session.input_mode === "text" ? "Ваш текст" : "Транскрипт";
      container.querySelector("#transcript").textContent = session.transcript || "(пустой транскрипт)";

      const analyzeBtn = container.querySelector("#analyze-btn");
      const copyBtn = container.querySelector("#copy-btn");
      const slot = container.querySelector("#analysis-slot");

      await renderAnalysis(slot, session.analysis);
      const picker = await ThemePicker.mountForAnalysis(container.querySelector("#analyze-theme"), session.language);

      analyzeBtn.onclick = async () => {
        analyzeBtn.disabled = true;
        analyzeBtn.textContent = "Анализируем...";
        try {
          const theme = picker ? picker.value() : null;
          const result = await Api.analyzeSession(session.id, !!session.analysis, theme);
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
    if (onPaste) document.removeEventListener("paste", onPaste);
    onPaste = null;
    if (pictureUrl) URL.revokeObjectURL(pictureUrl);
    pictureUrl = null;
    pictureBlob = null;
    clearTimeout(pollTimer);
    if (recorder) recorder.dispose();
    recorder = null;
  }

  return { render, dispose };
})();

// «Описание картинки» (#/picture): the recorder in picture mode.
Views.speak = {
  render: (container, param) => Views.record.render(container, param, "lesson"),
  dispose: () => Views.record.dispose(),
};

Views.picture = {
  render: (container, param) => Views.record.render(container, param, "picture"),
  dispose: () => Views.record.dispose(),
};
