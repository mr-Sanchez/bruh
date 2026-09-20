// Small SVG charts for the score history (no chart library, no build step).
//   Charts.sparkline(values)            - SVG string for a stat tile
//   Charts.lineChart(host, points, opts) - one skill over recordings, with a
//                                          crosshair + tooltip on hover/focus
// Scores are 1..10, so every chart shares the fixed 0..10 scale; one series
// per chart (small multiples), so no legend is needed - the title names it.
const Charts = (() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const MAX = 10;

  function sparkline(values, { width = 96, height = 28 } = {}) {
    const points = values.filter((v) => v != null).slice(-12);
    if (points.length < 2) return "";
    const pad = 4;
    const x = (i) => pad + (i * (width - 2 * pad)) / (points.length - 1);
    const y = (v) => pad + (1 - v / MAX) * (height - 2 * pad);
    const path = points.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
    const last = points.length - 1;
    return `
      <svg class="sparkline" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" aria-hidden="true">
        <path d="${path}" fill="none" stroke="var(--spark-line)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
        <circle cx="${x(last)}" cy="${y(points[last])}" r="4" fill="var(--series-1)" stroke="var(--surface)" stroke-width="2"/>
      </svg>`;
  }

  function el(name, attrs) {
    const node = document.createElementNS(SVG_NS, name);
    Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  }

  // points: [{label, value}] in time order. The tooltip text is set with
  // textContent - labels come from data files.
  function lineChart(host, points, { title }) {
    host.innerHTML = "";
    host.classList.add("chart");
    const data = points.filter((p) => p.value != null);
    const heading = document.createElement("div");
    heading.className = "chart-title";
    heading.textContent = title;
    const latest = document.createElement("span");
    latest.className = "chart-latest";
    latest.textContent = data.length ? `${data[data.length - 1].value}/10` : "—";
    heading.appendChild(latest);
    host.appendChild(heading);
    if (data.length < 2) {
      const note = document.createElement("p");
      note.className = "muted chart-empty";
      note.textContent = "Нужно хотя бы две оценённые записи.";
      host.appendChild(note);
      return;
    }

    const width = 320;
    const height = 140;
    const m = { top: 10, right: 12, bottom: 22, left: 24 };
    const plotW = width - m.left - m.right;
    const plotH = height - m.top - m.bottom;
    const x = (i) => m.left + (i * plotW) / (data.length - 1);
    const y = (v) => m.top + (1 - v / MAX) * plotH;

    const svg = el("svg", {
      viewBox: `0 0 ${width} ${height}`,
      class: "chart-svg",
      role: "img",
      "aria-label": `${title}: ${data.map((p) => `${p.label} — ${p.value}`).join(", ")}`,
      tabindex: "0",
    });
    [0, 5, 10].forEach((tick) => {
      svg.appendChild(el("line", { x1: m.left, x2: width - m.right, y1: y(tick), y2: y(tick), class: "chart-gridline" }));
      const text = el("text", { x: m.left - 6, y: y(tick) + 4, class: "chart-tick", "text-anchor": "end" });
      text.textContent = String(tick);
      svg.appendChild(text);
    });
    [0, data.length - 1].forEach((i) => {
      const text = el("text", {
        x: x(i),
        y: height - 6,
        class: "chart-tick",
        "text-anchor": i === 0 ? "start" : "end",
      });
      text.textContent = data[i].label;
      svg.appendChild(text);
    });

    const path = data.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
    svg.appendChild(el("path", { d: `${path}L${x(data.length - 1)},${y(0)}L${x(0)},${y(0)}Z`, class: "chart-area" }));
    svg.appendChild(el("path", { d: path, class: "chart-line" }));
    const last = data.length - 1;
    svg.appendChild(el("circle", { cx: x(last), cy: y(data[last].value), r: 4, class: "chart-dot" }));

    const crosshair = el("line", { y1: m.top, y2: m.top + plotH, class: "chart-crosshair", visibility: "hidden" });
    const marker = el("circle", { r: 4, class: "chart-dot", visibility: "hidden" });
    svg.appendChild(crosshair);
    svg.appendChild(marker);
    host.appendChild(svg);

    const tip = document.createElement("div");
    tip.className = "chart-tooltip";
    tip.hidden = true;
    const tipValue = document.createElement("strong");
    const tipLabel = document.createElement("span");
    tip.append(tipValue, tipLabel);
    host.appendChild(tip);

    let active = null;
    function show(i) {
      active = i;
      const cx = x(i);
      const cy = y(data[i].value);
      crosshair.setAttribute("x1", cx);
      crosshair.setAttribute("x2", cx);
      marker.setAttribute("cx", cx);
      marker.setAttribute("cy", cy);
      crosshair.setAttribute("visibility", "visible");
      marker.setAttribute("visibility", "visible");
      tipValue.textContent = `${data[i].value}/10`;
      tipLabel.textContent = data[i].label;
      tip.hidden = false;
      const box = svg.getBoundingClientRect();
      const scale = box.width / width;
      const left = Math.min(Math.max(cx * scale - tip.offsetWidth / 2, 0), box.width - tip.offsetWidth);
      tip.style.left = `${left}px`;
      tip.style.top = `${Math.max(cy * scale - tip.offsetHeight - 10, 0)}px`;
    }
    function hide() {
      active = null;
      crosshair.setAttribute("visibility", "hidden");
      marker.setAttribute("visibility", "hidden");
      tip.hidden = true;
    }
    svg.addEventListener("pointermove", (event) => {
      const box = svg.getBoundingClientRect();
      const px = ((event.clientX - box.left) / box.width) * width;
      const i = Math.round(((px - m.left) / plotW) * (data.length - 1));
      show(Math.min(data.length - 1, Math.max(0, i)));
    });
    svg.addEventListener("pointerleave", hide);
    svg.addEventListener("focus", () => show(last));
    svg.addEventListener("blur", hide);
    svg.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const step = event.key === "ArrowLeft" ? -1 : 1;
      show(Math.min(data.length - 1, Math.max(0, (active == null ? last : active) + step)));
    });
  }

  // Score history entries, oldest first (they come keyed by recording time).
  function sortedHistory(history) {
    return (history || []).slice().sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
  }

  const SKILLS = [["overall", "Итого"]].concat(SCORE_LABELS);

  // KPI row: latest score per skill, change vs the previous recording, trend.
  function scoreTiles(history) {
    const entries = sortedHistory(history);
    if (!entries.length) return "";
    return `<div class="stat-tiles">${SKILLS.map(([key, label]) => {
      const values = entries.map((e) => e[key]).filter((v) => v != null);
      if (!values.length) return "";
      const value = values[values.length - 1];
      const delta = values.length > 1 ? Math.round((value - values[values.length - 2]) * 10) / 10 : null;
      const deltaText =
        delta == null || delta === 0
          ? ""
          : `<span class="stat-delta ${delta > 0 ? "up" : "down"}">${delta > 0 ? "▲ +" : "▼ −"}${Math.abs(delta)}</span>`;
      return `
        <div class="stat-tile">
          <div class="stat-label">${label}</div>
          <div class="stat-value">${value}<span class="stat-unit">/10</span> ${deltaText}</div>
          ${sparkline(values)}
        </div>`;
    }).join("")}</div>`;
  }

  return { sparkline, lineChart, scoreTiles, sortedHistory, SKILLS };
})();
