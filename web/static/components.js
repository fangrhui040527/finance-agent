/* Shared DOM builders. Everything that shows tool or model text uses
 * textContent - display data is never markup. */

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") node.textContent = v;
    else if (k === "class") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c);
  return node;
}

export function head(title, sub) {
  const wrap = el("div", { class: "head" });
  const left = el("div");
  left.append(el("h1", { text: title }));
  if (sub) left.append(el("p", { text: sub }));
  wrap.append(left);
  return wrap;
}

export function card(children = [], cls = "card") {
  const c = el("div", { class: cls });
  for (const child of children) c.append(child);
  return c;
}

export function chip(text, kind = "n") {
  return el("span", { class: `chip ${kind}`, text });
}

/* The terminal pane: tool text, monospaced, verbatim. */
export function termPane(text, title = "") {
  const term = el("div", { class: "term" });
  const bar = el("div", { class: "term-bar" });
  bar.append(el("i"), el("i"), el("i"), el("b", { text: title || "output" }));
  term.append(bar);
  const pre = el("pre", { class: "term-body mono" });
  pre.textContent = text;
  term.append(pre);
  return term;
}

export function refusalCard(reason, fullText = "") {
  const box = el("div", { class: "box stop" });
  box.append(el("span", { class: "lbl", text: "Refused — and that is an answer" }));
  box.append(el("p", { text: reason }));
  const wrap = el("div", { class: "refusal" });
  wrap.append(box);
  if (fullText && fullText.trim() !== reason.trim()) {
    wrap.append(termPane(fullText, "full answer"));
  }
  return wrap;
}

export function errorCard(message) {
  const box = el("div", { class: "box warn" });
  box.append(el("span", { class: "lbl", text: "Error" }));
  box.append(el("p", { text: message }));
  return box;
}

export function disclaimerFooter(text) {
  return el("p", { class: "disclaimer", text: text.trim() });
}

export function table(headers, rows) {
  const t = el("table", { class: "tbl" });
  const thead = el("thead");
  const tr = el("tr");
  for (const h of headers) tr.append(el("th", { text: h }));
  thead.append(tr);
  t.append(thead);
  const tbody = el("tbody");
  for (const row of rows) {
    const r = el("tr");
    for (const cell of row) {
      const td = el("td");
      if (cell instanceof Node) td.append(cell);
      else td.textContent = String(cell);
      r.append(td);
    }
    tbody.append(r);
  }
  t.append(tbody);
  return t;
}

/* A signed horizontal bar, for decomposition-style views. */
export function signedBar(value, scale) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 200 12");
  svg.setAttribute("class", "signed-bar");
  const mid = 100;
  const width = scale > 0 ? Math.min(95, (Math.abs(value) / scale) * 95) : 0;
  const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  rect.setAttribute("y", "2");
  rect.setAttribute("height", "8");
  rect.setAttribute("x", value < 0 ? String(mid - width) : String(mid));
  rect.setAttribute("width", String(Math.max(width, 0.5)));
  rect.setAttribute("class", value < 0 ? "bar-neg" : "bar-pos");
  const axis = document.createElementNS("http://www.w3.org/2000/svg", "line");
  axis.setAttribute("x1", String(mid));
  axis.setAttribute("x2", String(mid));
  axis.setAttribute("y1", "0");
  axis.setAttribute("y2", "12");
  axis.setAttribute("class", "bar-axis");
  svg.append(rect, axis);
  return svg;
}

/* A close-price sparkline/line chart. Pure SVG, no library. */
export function lineChart(points, { width = 640, height = 160 } = {}) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("class", "line-chart");
  if (!points.length) return svg;
  const lo = Math.min(...points);
  const hi = Math.max(...points);
  const span = hi - lo || 1;
  const step = width / Math.max(points.length - 1, 1);
  const path = points
    .map((p, i) => {
      const x = (i * step).toFixed(2);
      const y = (height - 8 - ((p - lo) / span) * (height - 16)).toFixed(2);
      return `${i === 0 ? "M" : "L"}${x},${y}`;
    })
    .join(" ");
  const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
  line.setAttribute("d", path);
  line.setAttribute("class", "chart-line");
  line.setAttribute("fill", "none");
  svg.append(line);
  return svg;
}

export function statRow(pairs) {
  const row = el("div", { class: "stat-row" });
  for (const [label, value] of pairs) {
    const cell = el("div", { class: "stat" });
    cell.append(el("span", { class: "eyebrow", text: label }));
    cell.append(el("b", { class: "mono", text: String(value) }));
    row.append(cell);
  }
  return row;
}

export function field(labelText, inputEl) {
  const wrap = el("label", { class: "field" });
  wrap.append(el("span", { class: "eyebrow", text: labelText }), inputEl);
  return wrap;
}

export function input(name, value = "", attrs = {}) {
  return el("input", { class: "in mono", name, value: String(value), ...attrs });
}

export function button(text, onClick, cls = "btn") {
  const b = el("button", { class: cls, text });
  b.addEventListener("click", onClick);
  return b;
}
