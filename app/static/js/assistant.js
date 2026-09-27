// «Спросить ИИ»: select text anywhere on a screen, press the bubble that
// appears next to it, ask - and talk it over with Claude in a panel above the
// page. The selection goes with the card around it (its text as the screen
// shows it: labels, the quote, the correction) and the screen's title, so the
// model sees what the learner sees. Every chat is kept on the server; the
// panel's «История» lists them. Only «Отправить» spends money.
//
// Not a view: it lives beside the router for the whole page.
//   Assistant.open()        - the panel on its history (a future side button)
//   Assistant.openChat(id)  - one stored chat
const Assistant = (() => {
  // The innermost of these around a selection is its context - the smallest
  // block that still means something on its own.
  const CONTEXT_SELECTOR = [
    ".issue-card",
    ".drill-card",
    ".card-back",
    ".card-source",
    ".pattern-box",
    ".translation-part",
    ".lesson-row",
    ".card",
  ].join(", ");
  const SELECTION_MAX = 2000;
  const CONTEXT_MAX = 6000;

  let bubble = null;
  let panel = null;
  let pending = null; // what the bubble would ask about: {selection, context, screen}
  let draft = null; // a chat not yet sent: {selection, context, screen}
  let chat = null; // the open stored chat
  let busy = false;
  let error = "";
  let costUsd = null;

  // ---------------------------------------------------------- selection
  function selectionInfo() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
    const text = selection.toString().trim();
    if (!text) return null;
    const range = selection.getRangeAt(0);
    let node = range.commonAncestorContainer;
    if (node.nodeType !== Node.ELEMENT_NODE) node = node.parentElement;
    const app = document.getElementById("app");
    if (!node || !app || !app.contains(node)) return null;
    if (node.closest("input, textarea, select")) return null;
    const rect = range.getBoundingClientRect();
    if (!rect.width && !rect.height) return null;
    return {
      rect,
      selection: text.slice(0, SELECTION_MAX),
      context: contextText(node.closest(CONTEXT_SELECTOR), text),
      screen: screenInfo(),
    };
  }

  // The card's visible text; a long one is cut to a window around the selection.
  function contextText(element, selected) {
    if (!element) return "";
    const full = (element.innerText || "").replace(/\n{3,}/g, "\n\n").trim();
    if (full.length <= CONTEXT_MAX) return full;
    const at = Math.max(0, full.indexOf(selected.slice(0, 80)));
    const start = Math.max(0, Math.min(at - CONTEXT_MAX / 2, full.length - CONTEXT_MAX));
    return `${start > 0 ? "…" : ""}${full.slice(start, start + CONTEXT_MAX)}…`;
  }

  function screenInfo() {
    const heading = document.querySelector("#app .page-title");
    return {
      title: ((heading && heading.textContent) || document.title || "").trim().slice(0, 200),
      route: location.hash.slice(0, 200),
    };
  }

  function showBubble(info) {
    pending = info;
    bubble.hidden = false;
    const width = bubble.offsetWidth;
    const left = Math.min(
      Math.max(8, info.rect.right - width),
      document.documentElement.clientWidth - width - 8
    );
    bubble.style.left = `${left + window.scrollX}px`;
    bubble.style.top = `${info.rect.bottom + window.scrollY + 8}px`;
  }

  function hideBubble() {
    if (bubble) bubble.hidden = true;
    pending = null;
  }

  function onSelectionEnd(event) {
    if (event && bubble.contains(event.target)) return;
    // Let the browser settle the selection (a click inside it collapses it).
    setTimeout(() => {
      const info = selectionInfo();
      if (info) showBubble(info);
      else hideBubble();
    }, 0);
  }

  // -------------------------------------------------------------- panel
  function open() {
    showPanel();
    if (!chat && !draft) showHistory();
    else renderChat();
  }

  async function openChat(id) {
    showPanel();
    await loadChat(id);
  }

  function askAboutPending() {
    if (!pending) return;
    draft = { selection: pending.selection, context: pending.context, screen: pending.screen };
    chat = null;
    error = "";
    hideBubble();
    window.getSelection().removeAllRanges();
    showPanel();
    renderChat();
    ensureCost();
  }

  function showPanel() {
    panel.hidden = false;
    document.body.classList.add("assistant-open");
  }

  function close() {
    panel.hidden = true;
    document.body.classList.remove("assistant-open");
  }

  async function ensureCost() {
    if (costUsd != null) return;
    try {
      costUsd = (await Api.listChats()).cost_usd;
      renderComposerHint();
    } catch (err) {
      /* the hint just stays without a price */
    }
  }

  async function loadChat(id) {
    body().innerHTML = `<p class="muted assistant-pad">Загрузка...</p>`;
    try {
      chat = await Api.getChat(id);
      costUsd = chat.cost_usd;
      draft = null;
      error = "";
      renderChat();
    } catch (err) {
      body().innerHTML = `<p class="muted assistant-pad">Не удалось открыть: ${escapeHtml(err.message)}</p>`;
    }
  }

  function body() {
    return panel.querySelector("[data-role=body]");
  }

  async function showHistory() {
    panel.querySelector("[data-role=title]").textContent = "История вопросов";
    body().innerHTML = `<p class="muted assistant-pad">Загрузка...</p>`;
    let data;
    try {
      data = await Api.listChats();
    } catch (err) {
      body().innerHTML = `<p class="muted assistant-pad">Не удалось загрузить: ${escapeHtml(err.message)}</p>`;
      return;
    }
    costUsd = data.cost_usd;
    const rows = data.chats
      .map((c) => {
        const meta = [
          (c.screen && c.screen.title) || "",
          (c.updated_at || "").replace("T", " ").slice(0, 16),
          `${c.messages} сообщ.`,
        ].filter(Boolean);
        return `
          <button type="button" class="assistant-chat-row" data-chat="${escapeHtml(c.id)}">
            <span class="assistant-chat-title">${escapeHtml(c.title)}</span>
            <span class="muted">${escapeHtml(meta.join(" · "))}</span>
          </button>`;
      })
      .join("");
    body().innerHTML = `
      <div class="assistant-pad">
        <p class="muted">Выделите текст на любом экране и нажмите «Спросить ИИ» — вопрос
          уйдёт вместе с карточкой вокруг выделения.</p>
        ${rows || `<p class="muted">Пока вопросов не было.</p>`}
      </div>`;
    body()
      .querySelectorAll("[data-chat]")
      .forEach((row) => row.addEventListener("click", () => loadChat(row.dataset.chat)));
  }

  function renderChat() {
    const source = chat || draft;
    panel.querySelector("[data-role=title]").textContent = "Спросить ИИ";
    const messages = (chat ? chat.messages : [])
      .map(
        (m) => `
          <div class="assistant-message is-${m.role === "assistant" ? "assistant" : "user"}">
            ${m.role === "assistant" ? formatAnswer(m.text) : `<p>${escapeHtml(m.text)}</p>`}
          </div>`
      )
      .join("");
    const context =
      source.context && source.context !== source.selection
        ? `<details class="assistant-context">
             <summary>Контекст, который увидит ИИ</summary>
             <pre>${escapeHtml(source.context)}</pre>
           </details>`
        : "";
    const awaits = chat && chat.awaits_answer;
    body().innerHTML = `
      <div class="assistant-pad">
        ${source.screen && source.screen.title ? `<p class="muted">${escapeHtml(source.screen.title)}</p>` : ""}
        ${source.selection ? `<blockquote class="assistant-selection">${escapeHtml(source.selection)}</blockquote>` : ""}
        ${context}
        <div class="assistant-messages">${messages}</div>
        ${busy ? `<p class="muted assistant-thinking">ИИ думает...</p>` : ""}
        ${
          error
            ? `<p class="assistant-error">${escapeHtml(error)}</p>
               ${awaits && !busy ? `<button type="button" class="secondary" data-role="retry">Повторить</button>` : ""}`
            : awaits && !busy
              ? `<button type="button" class="secondary" data-role="retry">Получить ответ</button>`
              : ""
        }
      </div>
      ${
        chat && chat.full
          ? `<p class="muted assistant-pad">Диалог слишком длинный — выделите текст и начните новый.</p>`
          : `<div class="assistant-composer">
               <textarea rows="3" data-role="input" placeholder="${
                 chat ? "Уточните или спросите ещё..." : "Что непонятно? Например: почему здесь прошедшее время?"
               }" ${busy || awaits ? "disabled" : ""}></textarea>
               <div class="assistant-composer-row">
                 <span class="muted" data-role="hint"></span>
                 <button type="button" data-role="send" ${busy || awaits ? "disabled" : ""}>Отправить</button>
               </div>
             </div>`
      }`;
    renderComposerHint();
    const input = body().querySelector("[data-role=input]");
    const send = body().querySelector("[data-role=send]");
    const retry = body().querySelector("[data-role=retry]");
    if (send) send.addEventListener("click", () => submit(input.value));
    if (input) {
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          submit(input.value);
        }
      });
      if (!busy) input.focus();
    }
    if (retry) retry.addEventListener("click", () => answer());
    const last = body().querySelector(".assistant-message:last-child");
    if (last && chat && !chat.awaits_answer) last.scrollIntoView({ block: "start" });
    else body().scrollTop = body().scrollHeight;
  }

  function renderComposerHint() {
    const hint = panel && panel.querySelector("[data-role=hint]");
    if (!hint) return;
    const price = costUsd != null ? ` · ≈ ${(costUsd * 100).toFixed(1)} ¢ за ответ` : "";
    hint.textContent = `Enter — отправить, Shift+Enter — новая строка${price}`;
  }

  async function submit(value) {
    const text = (value || "").trim();
    if (!text || busy) return;
    busy = true;
    error = "";
    try {
      chat = chat
        ? await Api.addChatMessage(chat.id, text)
        : await Api.createChat({ question: text, ...draft });
      draft = null;
    } catch (err) {
      busy = false;
      error = err.message;
      renderChat();
      return;
    }
    busy = false;
    await answer();
  }

  async function answer() {
    if (!chat || busy) return;
    busy = true;
    error = "";
    renderChat();
    try {
      chat = await Api.replyInChat(chat.id);
      costUsd = chat.cost_usd;
    } catch (err) {
      error = err.message;
    }
    busy = false;
    renderChat();
  }

  // Claude writes light Markdown (asked for in the prompt): paragraphs,
  // "- " lists, **bold**, *italics*, `code`. Escaped first, so it stays safe.
  function formatAnswer(text) {
    const inline = (line) =>
      escapeHtml(line)
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
    return String(text || "")
      .split(/\n\s*\n/)
      .map((block) => {
        const lines = block.split("\n").filter((l) => l.trim());
        if (lines.length && lines.every((l) => /^\s*[-•*]\s+/.test(l))) {
          return `<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*[-•*]\s+/, ""))}</li>`).join("")}</ul>`;
        }
        return `<p>${lines.map(inline).join("<br>")}</p>`;
      })
      .join("");
  }

  // -------------------------------------------------------------- setup
  function build() {
    bubble = document.createElement("button");
    bubble.type = "button";
    bubble.className = "ask-bubble";
    bubble.hidden = true;
    bubble.innerHTML = `${Icons.svg("sparkle", 16)}<span>Спросить ИИ</span>`;
    // Keep the selection: a mousedown on a button would otherwise clear it.
    bubble.addEventListener("mousedown", (event) => event.preventDefault());
    bubble.addEventListener("click", askAboutPending);
    document.body.appendChild(bubble);

    panel = document.createElement("aside");
    panel.className = "assistant-panel";
    panel.hidden = true;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Ассистент");
    panel.innerHTML = `
      <header class="assistant-header">
        <h2 data-role="title">Спросить ИИ</h2>
        <button type="button" class="secondary" data-role="history">История</button>
        <button type="button" class="secondary assistant-close" data-role="close" aria-label="Закрыть">${Icons.svg("close", 18)}</button>
      </header>
      <div class="assistant-body" data-role="body"></div>`;
    panel.querySelector("[data-role=close]").addEventListener("click", close);
    panel.querySelector("[data-role=history]").addEventListener("click", () => {
      if (busy) return;
      chat = null;
      draft = null;
      showHistory();
    });
    // The page's own hotkeys (R to dictate, Enter to check) stay out of the panel.
    panel.addEventListener("keydown", (event) => {
      if (event.key === "Escape") close();
      event.stopPropagation();
    });
    document.body.appendChild(panel);

    document.addEventListener("mouseup", onSelectionEnd);
    document.addEventListener("keyup", (event) => {
      if (event.shiftKey || event.key === "Shift") onSelectionEnd(event);
    });
    document.addEventListener("mousedown", (event) => {
      if (!bubble.contains(event.target)) hideBubble();
    });
    window.addEventListener("hashchange", hideBubble);
  }

  window.addEventListener("DOMContentLoaded", build);

  return { open, openChat, close };
})();
