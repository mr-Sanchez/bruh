// Contexts «уклон» (Stage 8, R3): the situations generated sentences come from.
// Chosen before every generation - an AI set, the drills of an analysis, the
// speaking prompts - and defaulting to the last one used (kept on the server).
//
//   ThemePicker.mount(host, { oneOff, onChange }) -> { value(), current() }
//     A select: built-in contexts, the learner's own, «Свой вариант…» (a
//     one-off typed line; `oneOff: false` hides it) and «Добавить свой…».
//     value() is what a request sends: {key} or {label}.
//   ThemePicker.mountPrompts(host, { promptId, onPrompt }) -> { current(), lock() }
//     The picker plus a speaking prompt of the chosen context, «Другая тема»,
//     and «Придумать темы» (Haiku) for an own context without prompts yet.
const ThemePicker = (() => {
  const ONE_OFF = "__one_off";
  const ADD = "__add";

  function optionsHtml(data, oneOff) {
    const builtin = data.builtin
      .map((t) => `<option value="${escapeHtml(t.key)}">${escapeHtml(t.label)}</option>`)
      .join("");
    const custom = data.custom.length
      ? `<optgroup label="Свои">${data.custom
          .map((t) => `<option value="${escapeHtml(t.key)}">${escapeHtml(t.label)}</option>`)
          .join("")}</optgroup>`
      : "";
    return `${builtin}${custom}
      ${oneOff ? `<option value="${ONE_OFF}">Свой вариант на один раз…</option>` : ""}
      <option value="${ADD}">+ Добавить свой уклон…</option>`;
  }

  async function mount(host, { oneOff = true, onChange = null } = {}) {
    let data = await Api.getThemes();
    let selected = null; // the value before «Добавить…» was picked

    host.innerHTML = `
      <div class="theme-picker">
        <label>Уклон
          <select data-role="theme-select"></select>
        </label>
        <button type="button" class="link-button" data-role="theme-delete" hidden>удалить</button>
        <span class="theme-extra" data-role="theme-one-off" hidden>
          <input type="text" maxlength="60" placeholder="например: ремонт машины" data-role="theme-one-off-input" />
        </span>
        <span class="theme-extra" data-role="theme-add" hidden>
          <input type="text" maxlength="60" placeholder="название уклона" data-role="theme-add-input" />
          <button type="button" class="secondary" data-role="theme-add-save">Сохранить</button>
          <button type="button" class="link-button" data-role="theme-add-cancel">отмена</button>
        </span>
        <span class="muted" data-role="theme-status"></span>
      </div>`;
    const select = host.querySelector('[data-role="theme-select"]');
    const del = host.querySelector('[data-role="theme-delete"]');
    const oneOffBox = host.querySelector('[data-role="theme-one-off"]');
    const oneOffInput = host.querySelector('[data-role="theme-one-off-input"]');
    const addBox = host.querySelector('[data-role="theme-add"]');
    const addInput = host.querySelector('[data-role="theme-add-input"]');
    const status = host.querySelector('[data-role="theme-status"]');

    const isCustom = (key) => data.custom.some((t) => t.key === key);
    const labelOf = (key) => {
      const all = data.builtin.concat(data.custom);
      const found = all.find((t) => t.key === key);
      return found ? found.label : key;
    };

    function fill(value) {
      select.innerHTML = optionsHtml(data, oneOff);
      select.value = value;
      if (select.value !== value) select.value = data.builtin[0].key;
      sync();
    }

    function sync() {
      const value = select.value;
      del.hidden = !isCustom(value);
      oneOffBox.hidden = value !== ONE_OFF;
      addBox.hidden = value !== ADD;
    }

    function current() {
      if (select.value === ONE_OFF) {
        const label = oneOffInput.value.trim();
        return label ? { key: null, label } : null;
      }
      if (select.value === ADD) return selected ? { key: selected, label: labelOf(selected) } : null;
      return { key: select.value, label: labelOf(select.value) };
    }

    function changed() {
      sync();
      if (select.value === ADD) {
        addInput.focus();
        return;
      }
      selected = select.value;
      if (onChange && select.value !== ONE_OFF) onChange(current());
    }

    // The last theme used; a one-off opens as the typed line again.
    const last = data.last || {};
    if (last.key) {
      fill(last.key);
    } else if (oneOff && last.label) {
      fill(ONE_OFF);
      oneOffInput.value = last.label;
    } else {
      fill(data.builtin[0].key);
    }
    selected = select.value;

    select.addEventListener("change", changed);
    host.querySelector('[data-role="theme-add-cancel"]').addEventListener("click", () => {
      fill(selected || data.builtin[0].key);
      if (onChange) onChange(current());
    });
    host.querySelector('[data-role="theme-add-save"]').addEventListener("click", async () => {
      const label = addInput.value.trim();
      if (!label) return;
      try {
        const result = await Api.addTheme(label);
        data = result;
        addInput.value = "";
        fill(result.theme.key);
        selected = result.theme.key;
        status.textContent = "";
        if (onChange) onChange(current());
      } catch (err) {
        status.textContent = err.message;
      }
    });
    del.addEventListener("click", async () => {
      const key = select.value;
      try {
        data = await Api.deleteTheme(key);
        fill(data.last && data.last.key ? data.last.key : data.builtin[0].key);
        selected = select.value;
        if (onChange) onChange(current());
      } catch (err) {
        status.textContent = err.message;
      }
    });

    return {
      current,
      // What a generation request sends; null lets the server use the last one.
      value: () => {
        const theme = current();
        if (!theme) return null;
        return theme.key ? { key: theme.key } : { label: theme.label };
      },
      select: (key) => {
        fill(key);
        selected = select.value;
      },
      disable: (off) => {
        host.querySelectorAll("select, input, button").forEach((el) => (el.disabled = off));
      },
    };
  }

  // ------------------------------------------------------- speaking prompts
  async function mountPrompts(host, { promptId = null, onPrompt = null, note = "" } = {}) {
    host.innerHTML = `
      <div class="speaking-prompt">
        <div data-role="picker"></div>
        <div data-role="prompt"></div>
      </div>`;
    const promptHost = host.querySelector('[data-role="prompt"]');
    let prompts = [];
    let shown = 0;
    let locked = false;
    let generating = false;
    let meta = null;

    const picker = await mount(host.querySelector('[data-role="picker"]'), {
      oneOff: false,
      onChange: async (theme) => {
        if (!theme || !theme.key) return;
        Api.setLastTheme({ key: theme.key }).catch(() => {});
        await load(theme.key);
        draw();
      },
    });

    async function load(key, wantedId = null) {
      meta = await Api.getThemePrompts(key);
      prompts = meta.prompts;
      const index = wantedId ? prompts.findIndex((p) => p.id === wantedId) : -1;
      shown = index >= 0 ? index : 0;
      return index >= 0;
    }

    function draw() {
      const prompt = prompts[shown];
      if (onPrompt) onPrompt(prompt || null);
      if (!prompt) {
        const canWrite = meta && meta.can_generate && meta.anthropic_configured;
        promptHost.innerHTML = `
          <p class="muted">Для этого уклона тем ещё нет.${
            canWrite ? " Claude придумает 8 тем для рассказа." : " Нужен ANTHROPIC_API_KEY, чтобы Claude их придумал."
          }</p>
          ${canWrite ? `<button type="button" class="secondary" data-role="write">Придумать темы · ≈ 0.1 ¢</button>` : ""}
          <span class="muted" data-role="write-status"></span>`;
        const write = promptHost.querySelector('[data-role="write"]');
        if (write) write.addEventListener("click", () => writePrompts(write));
        return;
      }
      promptHost.innerHTML = `
        <div class="muted">Тема: ${escapeHtml(prompt.hint)}</div>
        <p class="speaking-question">${escapeHtml(prompt.question)}</p>
        ${note ? `<p class="muted">${escapeHtml(note)}</p>` : ""}
        ${
          locked
            ? ""
            : `<div class="button-row compact">
                 <button type="button" class="secondary" data-role="next-prompt">Другая тема</button>
                 ${
                   meta && meta.can_generate && meta.anthropic_configured
                     ? `<button type="button" class="link-button" data-role="write">Придумать заново · ≈ 0.1 ¢</button>`
                     : ""
                 }
               </div>`
        }`;
      const next = promptHost.querySelector('[data-role="next-prompt"]');
      if (next) {
        next.addEventListener("click", () => {
          shown = (shown + 1) % prompts.length;
          draw();
        });
      }
      const write = promptHost.querySelector('[data-role="write"]');
      if (write) write.addEventListener("click", () => writePrompts(write));
    }

    async function writePrompts(button) {
      if (generating) return;
      generating = true;
      button.disabled = true;
      button.textContent = "Claude придумывает…";
      try {
        meta = await Api.writeThemePrompts(meta.theme.key);
        prompts = meta.prompts;
        shown = 0;
      } catch (err) {
        button.textContent = `Не удалось: ${err.message}`;
        generating = false;
        return;
      }
      generating = false;
      draw();
    }

    // Open on the prompt that was asked for: in the picker's context when it
    // is there, else in the context the prompt belongs to.
    const theme = picker.current();
    const found = theme && theme.key ? await load(theme.key, promptId) : false;
    if (promptId && !found) {
      const key = promptId.split(":")[0];
      try {
        if (await load(key, promptId)) picker.select(key);
      } catch (err) {
        // an own context deleted since: stay on the picker's list
        if (theme && theme.key) await load(theme.key);
      }
    }
    draw();

    return {
      current: () => prompts[shown] || null,
      // After the first take of a series: the prompt is fixed.
      lock: () => {
        locked = true;
        picker.disable(true);
        draw();
      },
    };
  }

  // Next to «Анализировать»: the context of the practice sentences the
  // analysis writes for each English mistake. A Russian take gets none.
  async function mountForAnalysis(host, language) {
    if (!host) return null;
    if ((language || "").startsWith("ru")) {
      host.innerHTML = "";
      return null;
    }
    try {
      const picker = await mount(host);
      host.insertAdjacentHTML(
        "beforeend",
        `<p class="muted">Уклон — для новых предложений-карточек, которые Claude напишет к каждой ошибке.</p>`
      );
      return picker;
    } catch (err) {
      host.innerHTML = "";
      return null;
    }
  }

  return { mount, mountPrompts, mountForAnalysis };
})();
