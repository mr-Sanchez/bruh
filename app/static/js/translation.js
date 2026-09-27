// The translation task under a dictation lesson: the lesson is cut into parts
// of 5-15 sentences (one Haiku call, on a click), and each part is translated
// from memory of what was heard and reviewed by Sonnet (one call per part, on a
// click). Both results live on the server; this file only draws them.
//   Translation.mount(card, lesson) -> { refresh() }
// `lesson.translation` is the server's payload (see dictation_store.py).
window.Translation = (() => {
  const QUALITY = {
    good: ["Хороший перевод", "pill-minor"],
    fair: ["Есть неточности", "pill-moderate"],
    weak: ["Много ошибок", "pill-major"],
  };

  function mount(card, initial) {
    let lesson = initial;
    let openPart = null; // index of the part being translated
    let busy = false;
    let message = "";
    const drafts = {}; // part index -> text typed but not yet reviewed

    // The server's per-part count is stale once sentences are dictated here.
    const dictated = (part) => {
      let count = 0;
      for (let i = part.first; i < part.end; i += 1) {
        if (lesson.results[i] && lesson.results[i].completed) count += 1;
      }
      return count;
    };

    const data = () => lesson.translation;

    async function run(job) {
      if (busy) return;
      busy = true;
      message = "";
      draw();
      try {
        await job();
      } catch (err) {
        message = err.message;
      } finally {
        busy = false;
        draw();
      }
    }

    // ------------------------------------------------------------ drawing
    function draw() {
      const info = data();
      if (!info) return;
      const body = info.needs_split ? splitBlock() : partsBlock(info);
      card.innerHTML = `
        <h2>Перевод</h2>
        ${body}
        <p class="muted translation-error" aria-live="polite">${escapeHtml(message)}</p>`;
      wire();
    }

    function splitBlock() {
      return `
        <p class="muted">После диктанта можно перевести услышанное на русский по частям: урок
          длинный, поэтому сначала модель Haiku разобьёт текст на смысловые части по 5–15
          предложений (один запрос, около 0,5 ¢). Потом каждую часть вы переводите по памяти, и
          Sonnet разбирает перевод (около 2 ¢ за часть). Ничего не запускается само.</p>
        <button data-role="split" ${busy ? "disabled" : ""}>${
          busy ? "Разбиваем…" : "Разбить на части"
        }</button>`;
    }

    function partsBlock(info) {
      const rows = info.parts.map(partRow).join("");
      return `
        <p class="muted">Каждая часть открывается, когда предложения в ней набраны до конца.
          Переводите по памяти, не подглядывая в текст сразу — он показан над полем для
          проверки.</p>
        <div class="translation-parts">${rows}</div>`;
    }

    function partRow(part) {
      const done = dictated(part);
      const ready = done >= part.sentences;
      const last = part.translation;
      const review = last && last.review;
      const badge = review
        ? `<span class="pill ${QUALITY[review.quality][1]}">${QUALITY[review.quality][0]}</span>`
        : last
        ? `<span class="pill pill-moderate">не проверено</span>`
        : "";
      const range =
        part.sentences === 1 ? `${part.first + 1}` : `${part.first + 1}–${part.end}`;
      const status = ready
        ? badge
        : `<span class="muted">набрано ${done} из ${part.sentences}</span>`;
      const opened = openPart === part.index;
      return `
        <div class="translation-part ${opened ? "is-open" : ""}">
          <div class="translation-part-head">
            <div>
              <div class="lesson-title">Часть ${part.index + 1}</div>
              <div class="lesson-meta">предложения ${range} · ${status}</div>
            </div>
            ${
              ready
                ? `<button class="${opened ? "secondary" : ""}" data-role="toggle"
                    data-part="${part.index}">${
                    opened ? "Свернуть" : last ? "Открыть" : "Перевести"
                  }</button>`
                : ""
            }
          </div>
          ${opened ? partBody(part) : ""}
        </div>`;
    }

    function partBody(part) {
      const last = part.translation;
      const review = last && last.review;
      return `
        <div class="translation-source" lang="en">${escapeHtml(part.text)}</div>
        <textarea rows="7" data-role="text" data-part="${part.index}"
          placeholder="Ваш перевод этой части…"
          ${busy ? "disabled" : ""}>${escapeHtml(
          drafts[part.index] != null ? drafts[part.index] : last ? last.text : ""
        )}</textarea>
        <div class="translation-actions">
          <button data-role="review" data-part="${part.index}" ${busy ? "disabled" : ""}>${
            busy ? "Проверяем…" : review ? "Проверить заново" : "Проверить перевод"
          }</button>
          <span class="muted">Sonnet, около 2 ¢. Один и тот же текст второй раз не оплачивается.</span>
        </div>
        ${review ? reviewBlock(review) : ""}`;
    }

    function reviewBlock(review) {
      const issues = review.issues
        .map(
          (issue) => `
          <div class="issue-card">
            <p class="muted" lang="en">${escapeHtml(issue.source)}</p>
            ${
              issue.quote
                ? `<p class="quote">Вы написали: ${escapeHtml(issue.quote)}</p>`
                : `<p class="quote">Пропущено в переводе.</p>`
            }
            <p>${escapeHtml(issue.problem)}</p>
            ${issue.better ? `<p class="correction">${escapeHtml(issue.better)}</p>` : ""}
          </div>`
        )
        .join("");
      return `
        <div class="translation-review">
          <p><span class="pill ${QUALITY[review.quality][1]}">${
            QUALITY[review.quality][0]
          }</span> ${escapeHtml(review.summary)}</p>
          ${issues}
          <details class="better-versions">
            <summary>Как перевёл бы ИИ ассистент</summary>
            <p>${escapeHtml(review.model_translation)}</p>
          </details>
        </div>`;
    }

    // ------------------------------------------------------------- events
    function wire() {
      const split = card.querySelector('[data-role="split"]');
      if (split) {
        split.addEventListener("click", () =>
          run(async () => {
            lesson.translation = await Api.splitLesson(lesson.id);
          })
        );
      }
      card.querySelectorAll('[data-role="toggle"]').forEach((button) =>
        button.addEventListener("click", () => {
          const index = Number(button.dataset.part);
          openPart = openPart === index ? null : index;
          message = "";
          draw();
        })
      );
      const box = card.querySelector('[data-role="text"]');
      if (box) {
        box.addEventListener("input", () => {
          drafts[Number(box.dataset.part)] = box.value;
        });
      }
      const review = card.querySelector('[data-role="review"]');
      if (review) {
        review.addEventListener("click", () => {
          const index = Number(review.dataset.part);
          const text = box.value.trim();
          if (!text) {
            message = "Сначала напишите перевод.";
            draw();
            return;
          }
          drafts[index] = text;
          run(async () => {
            const saved = await Api.postPartTranslation(lesson.id, index, text);
            data().parts[index].translation = saved.translation;
            delete drafts[index];
          });
        });
      }
    }

    draw();

    return {
      // Called when a dictated sentence is saved, so that a part opens the
      // moment its last sentence is done.
      refresh() {
        const box = card.querySelector('[data-role="text"]');
        // Never redraw over a translation that is being typed.
        if (!box || !box.value.trim()) draw();
      },
    };
  }

  return { mount };
})();
