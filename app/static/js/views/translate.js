// «Перевод текста»: an English text to translate into Russian.
//   #/translate       - a new text (Claude writes one in a context at a size,
//                       or the learner pastes their own) and the list of texts
//   #/translate/<id>  - the text beside the translation field (typed or
//                       dictated in Russian), «Проверить» -> Sonnet's review:
//                       accuracy, mistakes, unnatural spots, a final version,
//                       and useful phrases to pick as word cards.
// Both Claude calls are explicit clicks; the server keeps every result, and the
// same translation sent twice is not paid for again.
window.Views = window.Views || {};

Views.translate = (() => {
  const PREFS_KEY = "translate-prefs";
  const DRAFT_PREFIX = "translate-draft:";
  const DICTATION_SECONDS = 120;
  const LEVEL_LABELS = { a2: "A2", b1: "B1", b2: "B2", c1: "C1" };
  const MISTAKE_KINDS = {
    grammar: "Грамматика",
    meaning: "Смысл",
    omission: "Пропуск",
    addition: "Лишнее",
    word_choice: "Выбор слова",
    spelling: "Орфография",
  };
  let stopDictation = null;

  // Per-viewer conveniences only; the page works without storage.
  function readStore(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw == null ? fallback : JSON.parse(raw);
    } catch (err) {
      return fallback;
    }
  }
  function writeStore(key, value) {
    try {
      if (value == null) localStorage.removeItem(key);
      else localStorage.setItem(key, JSON.stringify(value));
    } catch (err) {
      /* storage unavailable: nothing to remember */
    }
  }

  async function render(container, id) {
    container.innerHTML = `<div class="card"><p class="muted">Загрузка...</p></div>`;
    try {
      if (id) await renderText(container, id);
      else await renderHome(container);
    } catch (err) {
      container.innerHTML = `<div class="card"><p class="muted">Не удалось загрузить: ${escapeHtml(err.message)}</p></div>`;
    }
  }

  function dispose() {
    if (stopDictation) stopDictation();
    stopDictation = null;
  }

  function paragraphs(text) {
    return String(text || "")
      .split(/\n\s*\n/)
      .map((p) => p.trim())
      .filter(Boolean)
      .map((p) => `<p>${escapeHtml(p).replace(/\n/g, "<br/>")}</p>`)
      .join("");
  }

  function minutesFor(words) {
    return Math.max(1, Math.round(words / 18));
  }

  // ----------------------------------------------------------------- home
  async function renderHome(container) {
    const data = await Api.listTexts();
    const prefs = readStore(PREFS_KEY, {}) || {};
    const mode = prefs.mode === "custom" ? "custom" : "generate";
    const size = data.sizes.some((s) => s.key === prefs.size) ? prefs.size : data.default_size;
    const level = data.levels.includes(prefs.level) ? prefs.level : data.default_level;
    const cost = formatCents(data.costs.write_usd);

    container.innerHTML = `
      <p><a href="#/practice">← Занятия</a></p>
      <h1 class="page-title">Перевод текста</h1>
      <div class="card">
        <h2>Новый текст</h2>
        <p class="muted">Английский текст → ваш перевод на русский (набрать или надиктовать) →
          разбор Sonnet: насколько точно, ошибки, где звучит неестественно, итоговый вариант и
          полезные фразы в карточки.</p>
        <div class="segmented" role="radiogroup" aria-label="Откуда текст">
          <label><input type="radio" name="text-mode" value="generate" ${mode === "generate" ? "checked" : ""} /> Сгенерировать</label>
          <label><input type="radio" name="text-mode" value="custom" ${mode === "custom" ? "checked" : ""} /> Свой текст</label>
        </div>
        <div class="translate-new" data-panel="generate" ${mode === "generate" ? "" : "hidden"}>
          <div data-role="theme"></div>
          <div class="translate-options">
            <label>Размер
              <select data-role="size">
                ${data.sizes
                  .map(
                    (s) => `<option value="${s.key}" ${s.key === size ? "selected" : ""}>${escapeHtml(s.label)} · ≈ ${s.minutes} мин (${s.words[0]}–${s.words[1]} слов)</option>`
                  )
                  .join("")}
              </select>
            </label>
            <label>Уровень
              <select data-role="level">
                ${data.levels
                  .map((l) => `<option value="${l}" ${l === level ? "selected" : ""}>${LEVEL_LABELS[l] || l}</option>`)
                  .join("")}
              </select>
            </label>
          </div>
          <div class="button-row">
            <button data-role="generate" ${data.anthropic_configured ? "" : "disabled"}>Сгенерировать · ≈ ${cost}</button>
            <span class="muted" data-role="generate-status">${
              data.anthropic_configured ? "" : "Нужен ключ Anthropic (ANTHROPIC_API_KEY)."
            }</span>
          </div>
        </div>
        <div class="translate-new" data-panel="custom" ${mode === "custom" ? "" : "hidden"}>
          <textarea rows="8" lang="en" data-role="custom-text" maxlength="4000"
            placeholder="Вставьте английский текст: статью, письмо, абзац из книги…"></textarea>
          <div class="button-row">
            <button data-role="custom-start">Начать перевод</button>
            <span class="muted" data-role="custom-status">Бесплатно — платной будет только проверка перевода.</span>
          </div>
        </div>
      </div>
      <div class="card">
        <h2>Ваши тексты</h2>
        ${
          data.texts.length
            ? data.texts.map(textRow).join("")
            : `<p class="muted">Пока пусто — сгенерируйте или вставьте первый текст.</p>`
        }
      </div>`;

    wireHome(container, data);
  }

  function textRow(t) {
    const meta = [
      t.origin === "custom" ? "свой текст" : t.theme && t.theme.label ? t.theme.label : "ИИ ассистент",
      t.level ? LEVEL_LABELS[t.level] : "",
      `${t.words} слов · ≈ ${t.minutes} мин`,
      (t.created_at || "").slice(0, 10),
    ].filter(Boolean);
    const status =
      t.last_accuracy != null
        ? `точность ${t.last_accuracy}% · проверок: ${t.attempts}`
        : t.attempts
        ? "перевод не проверен"
        : "не переведён";
    return `
      <div class="lesson-row">
        <div>
          <div class="lesson-title">${escapeHtml(t.title)}</div>
          <div class="lesson-meta">${escapeHtml(meta.join(" · "))}</div>
          <div class="lesson-meta">${escapeHtml(status)}</div>
        </div>
        <div class="lesson-actions">
          <a href="#/translate/${encodeURIComponent(t.id)}"><button class="${t.attempts ? "secondary" : ""}">${
            t.attempts ? "Открыть" : "Перевести"
          }</button></a>
        </div>
      </div>`;
  }

  function wireHome(container, data) {
    const prefs = () => readStore(PREFS_KEY, {}) || {};
    const remember = (patch) => writeStore(PREFS_KEY, { ...prefs(), ...patch });

    container.querySelectorAll('input[name="text-mode"]').forEach((radio) =>
      radio.addEventListener("change", () => {
        container.querySelectorAll("[data-panel]").forEach((panel) => {
          panel.hidden = panel.dataset.panel !== radio.value;
        });
        remember({ mode: radio.value });
      })
    );

    let picker = null;
    ThemePicker.mount(container.querySelector('[data-role="theme"]'))
      .then((p) => (picker = p))
      .catch(() => (container.querySelector('[data-role="theme"]').textContent = ""));

    const size = container.querySelector('[data-role="size"]');
    const level = container.querySelector('[data-role="level"]');
    size.addEventListener("change", () => remember({ size: size.value }));
    level.addEventListener("change", () => remember({ level: level.value }));

    const generate = container.querySelector('[data-role="generate"]');
    const generateStatus = container.querySelector('[data-role="generate-status"]');
    generate.addEventListener("click", async () => {
      generate.disabled = true;
      if (picker) picker.disable(true);
      generateStatus.textContent = "ИИ ассистент пишет текст — обычно 10–30 секунд…";
      try {
        const text = await Api.createText(size.value, level.value, picker ? picker.value() : null);
        location.hash = `#/translate/${encodeURIComponent(text.id)}`;
      } catch (err) {
        generateStatus.textContent = `Не удалось: ${err.message}`;
        generate.disabled = false;
        if (picker) picker.disable(false);
      }
    });

    const custom = container.querySelector('[data-role="custom-text"]');
    const customStatus = container.querySelector('[data-role="custom-status"]');
    const start = container.querySelector('[data-role="custom-start"]');
    custom.addEventListener("input", () => {
      const words = custom.value.trim() ? custom.value.trim().split(/\s+/).length : 0;
      customStatus.textContent = words
        ? `${words} слов · перевод ≈ ${minutesFor(words)} мин`
        : "Бесплатно — платной будет только проверка перевода.";
    });
    start.addEventListener("click", async () => {
      const text = custom.value.trim();
      if (!text) {
        customStatus.textContent = "Сначала вставьте текст.";
        return;
      }
      start.disabled = true;
      try {
        const saved = await Api.createCustomText(text);
        location.hash = `#/translate/${encodeURIComponent(saved.id)}`;
      } catch (err) {
        customStatus.textContent = `Не удалось: ${err.message}`;
        start.disabled = false;
      }
    });
  }

  // ----------------------------------------------------------------- text
  async function renderText(container, id) {
    let doc = await Api.getText(id);
    const draftKey = DRAFT_PREFIX + id;
    const last = (doc.attempts || [])[doc.attempts.length - 1];
    const initial = readStore(draftKey, null) ?? (last ? last.text : "");
    const meta = [
      doc.origin === "custom" ? "Свой текст" : `ИИ ассистент · ${doc.theme && doc.theme.label ? doc.theme.label : ""}`,
      doc.level ? LEVEL_LABELS[doc.level] : "",
      `${doc.words} слов · перевод ≈ ${doc.minutes} мин`,
    ].filter(Boolean);

    container.innerHTML = `
      <p><a href="#/translate">← Перевод текста</a></p>
      <h1 class="page-title">${escapeHtml(doc.title)}</h1>
      <p class="muted">${escapeHtml(meta.join(" · "))}</p>
      <div class="card">
        <div class="translate-grid">
          <div>
            <h3>Текст</h3>
            <div class="translate-source" lang="en">${paragraphs(doc.text)}</div>
          </div>
          <div>
            <h3>Ваш перевод</h3>
            <textarea rows="14" lang="ru" data-role="translation" maxlength="8000"
              placeholder="Переведите текст на русский — наберите или надиктуйте…">${escapeHtml(initial)}</textarea>
            <div class="dictate-row">
              <button type="button" class="secondary dictate-button" data-role="dictate">${Icons.svg("mic", 18)}<span>Надиктовать</span>${keyHint("R")}</button>
              <span class="muted" data-role="dictate-status">По-русски, кусками до ${DICTATION_SECONDS} с — текст добавится в конец.</span>
            </div>
            <div class="translation-actions">
              <button data-role="review" ${doc.anthropic_configured ? "" : "disabled"}>Проверить · ≈ ${formatCents(doc.costs.review_usd)}</button>
              <span class="muted">Sonnet. Тот же текст повторно не оплачивается.</span>
            </div>
            <p class="muted translation-error" data-role="error" aria-live="polite">${
              doc.anthropic_configured ? "" : "Нужен ключ Anthropic (ANTHROPIC_API_KEY) — перевод сохранится, но проверить его нельзя."
            }</p>
          </div>
        </div>
      </div>
      <div data-role="review-host"></div>`;

    const box = container.querySelector('[data-role="translation"]');
    const button = container.querySelector('[data-role="review"]');
    const error = container.querySelector('[data-role="error"]');
    const host = container.querySelector('[data-role="review-host"]');
    const saveDraft = () => writeStore(draftKey, box.value.trim() ? box.value : null);

    box.addEventListener("input", saveDraft);
    stopDictation = Drill.wireDictation(container, box, {
      language: "ru",
      maxSeconds: DICTATION_SECONDS,
      doneHint: "Поправьте, если что-то не так расслышано. R — продиктовать дальше.",
      onText: saveDraft,
    });

    const showReview = (cached) => drawReview(host, doc, cached);
    showReview(false);

    button.addEventListener("click", async () => {
      const text = box.value.trim();
      if (!text) {
        error.textContent = "Сначала напишите перевод.";
        return;
      }
      button.disabled = true;
      box.readOnly = true;
      const label = button.textContent;
      button.textContent = "Проверяем…";
      error.textContent = "Sonnet читает перевод — обычно 20–60 секунд.";
      try {
        doc = await Api.reviewText(id, text);
        writeStore(draftKey, null);
        error.textContent = "";
        showReview(doc.cached);
        host.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (err) {
        error.textContent = `Не удалось проверить: ${err.message}. Перевод сохранён.`;
      } finally {
        button.textContent = label;
        button.disabled = !doc.anthropic_configured;
        box.readOnly = false;
      }
    });
  }

  function drawReview(host, doc, cached) {
    const attempt = [...(doc.attempts || [])].reverse().find((a) => a.review);
    if (!attempt) {
      host.innerHTML = "";
      return;
    }
    const r = attempt.review;
    const band =
      r.accuracy >= 85
        ? ["Точный перевод", "pill-minor"]
        : r.accuracy >= 60
        ? ["Есть неточности", "pill-moderate"]
        : ["Много ошибок", "pill-major"];
    const mistakes = (r.mistakes || [])
      .map(
        (m) => `
        <div class="issue-card">
          <p><span class="pill pill-moderate">${escapeHtml(MISTAKE_KINDS[m.kind] || m.kind)}</span></p>
          ${m.source ? `<p class="muted" lang="en">${escapeHtml(m.source)}</p>` : ""}
          <p class="quote">${m.quote ? `Вы написали: ${escapeHtml(m.quote)}` : "Пропущено в переводе."}</p>
          <p>${escapeHtml(m.problem)}</p>
          ${m.correction ? `<p class="correction">✓ ${escapeHtml(m.correction)}</p>` : ""}
        </div>`
      )
      .join("");
    const unnatural = (r.unnatural || [])
      .map(
        (u) => `
        <div class="issue-card">
          <p class="quote">${escapeHtml(u.quote)}</p>
          ${u.why ? `<p>${escapeHtml(u.why)}</p>` : ""}
          <p class="correction">→ ${escapeHtml(u.suggestion)}</p>
        </div>`
      )
      .join("");
    const reviewed = (doc.attempts || []).filter((a) => a.review).length;

    host.innerHTML = `
      <div class="card">
        <h2>Разбор</h2>
        <p class="muted">${
          cached ? "Этот перевод уже проверялся — показан сохранённый разбор. " : ""
        }Проверка № ${reviewed} · ${escapeHtml((attempt.at || "").replace("T", " ").slice(0, 16))}</p>
        <div class="translate-score">
          <span class="translate-accuracy">${r.accuracy}%</span>
          <span class="pill ${band[1]}">${band[0]}</span>
        </div>
        <p>${escapeHtml(r.summary)}</p>
        <h3>Ошибки</h3>
        ${mistakes || `<p class="muted">Серьёзных ошибок нет.</p>`}
        <h3>Звучит неестественно</h3>
        ${unnatural || `<p class="muted">Всё звучит естественно.</p>`}
        <h3>Итоговый перевод</h3>
        <div class="translate-final" lang="ru">${paragraphs(r.final_translation)}</div>
        <div data-role="phrases"></div>
      </div>`;

    // Only this review's phrases are shown; picks from earlier reviews of the
    // text are sent along untouched, so saving here never takes them away.
    const shown = new Set((attempt.phrases || []).map((p) => p.id));
    let others = (doc.picked || []).filter((pid) => !shown.has(pid));
    Drill.vocabularyPicker(host.querySelector('[data-role="phrases"]'), {
      vocabulary: attempt.phrases || [],
      picked: (doc.picked || []).filter((pid) => shown.has(pid)),
      perDay: doc.words_per_day,
      sourceLabel: "из этого разбора",
      save: async (chosen) => {
        const result = await Api.saveTextPhrases(doc.id, [...others, ...chosen]);
        others = result.picked.filter((pid) => !shown.has(pid));
        doc.picked = result.picked;
        return { ...result, picked: result.picked.filter((pid) => shown.has(pid)) };
      },
    });
  }

  return { render, dispose };
})();
