/* The shell: hash router, rail, and the one fetch helper every screen uses.
 *
 * Two rules, both load-bearing:
 *   - Model or tool text is rendered with textContent, never innerHTML: the
 *     narrative and every quoted piece of evidence is untrusted display data.
 *   - A refusal in the envelope becomes a first-class card, not an error toast:
 *     "no, and here is why" is a successful answer everywhere in this system.
 */

import { el, refusalCard, disclaimerFooter, errorCard } from "/components.js";
import { screens } from "/screens/registry.js";

const NAV = [
  ["main", "Overview", "ask"],
  ["why", "Why it moved", "why"],
  ["prices", "Prices", "prices"],
  ["thesis", "Thesis", "thesis"],
  ["portfolio", "Portfolio", "port"],
  ["sizing", "Sizing", "size"],
  ["predictions", "Predictions", "pred"],
  ["trace", "Trace", "trace"],
  ["learn", "Learn", "learn"],
  ["settings", "Settings", "set"],
  null, // separator
  ["world", "World monitor", "world"],
  ["agents", "Agents", "agents"],
];

const ICONS = {
  ask: '<path d="M8 2 2 5v6l6 3 6-3V5L8 2Z"/><path d="M8 8v6M2 5l6 3 6-3"/>',
  why: '<path d="M2 12l3.5-4 2.5 2.5L14 4"/><path d="M14 8V4h-4"/>',
  prices: '<path d="M2 13h12"/><path d="M4 11V7M7 11V4M10 11V8M13 11V5"/>',
  thesis: '<path d="M4 2h6l3 3v9H4V2Z"/><path d="M10 2v3h3M6 8h4M6 11h3"/>',
  port: '<circle cx="8" cy="8" r="5.5"/><path d="M8 2.5v5.5l3.9 3.9"/>',
  size: '<path d="M2 8h12"/><path d="M5 5 2 8l3 3M11 5l3 3-3 3"/>',
  pred: '<path d="M2 8h3l2-4 2 8 2-4h3"/>',
  trace:
    '<circle cx="4" cy="4" r="1.8"/><circle cx="12" cy="8" r="1.8"/><circle cx="6" cy="12" r="1.8"/><path d="M5.5 5.2 10.4 7M10.6 9.2 7.3 11.1"/>',
  learn:
    '<path d="M2 4.5 8 2l6 2.5L8 7 2 4.5Z"/><path d="M4.5 6v4c0 1 1.7 2 3.5 2s3.5-1 3.5-2V6"/>',
  set: '<circle cx="8" cy="8" r="2.2"/><path d="M8 1.5v2M8 12.5v2M14.5 8h-2M3.5 8h-2M12.6 3.4l-1.4 1.4M4.8 11.2l-1.4 1.4M12.6 12.6l-1.4-1.4M4.8 4.8 3.4 3.4"/>',
  world:
    '<circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2c2.5 2.7 2.5 9.3 0 12M8 2c-2.5 2.7-2.5 9.3 0 12"/>',
  agents:
    '<circle cx="5" cy="5" r="2"/><circle cx="11" cy="5" r="2"/><circle cx="8" cy="11" r="2"/><path d="M6.2 6.5 7.4 9.3M9.8 6.5 8.6 9.3"/>',
};

/* ---- fetch helper -------------------------------------------------------- */

export async function api(path, options = {}) {
  const opts = { ...options };
  if (opts.body !== undefined) {
    opts.method = "POST";
    opts.headers = {
      "Content-Type": "application/json",
      "X-Requested-With": "FinPlanet",
      ...(opts.headers || {}),
    };
    opts.body = JSON.stringify(opts.body);
  }
  const resp = await fetch(`/api${path}`, opts);
  let envelope = null;
  try {
    envelope = await resp.json();
  } catch {
    envelope = null;
  }
  if (!resp.ok) {
    const reason =
      (envelope && (envelope.detail || (envelope.refusal && envelope.refusal.reason))) ||
      `HTTP ${resp.status}`;
    return { ok: false, status: resp.status, reason, envelope };
  }
  return { ok: true, status: resp.status, envelope };
}

/* Render an envelope into a container: refusal card first, then whatever the
 * screen builds from data/text, then the disclaimer. */
export function renderEnvelope(container, result, build) {
  container.replaceChildren();
  if (!result.ok) {
    container.append(errorCard(result.reason));
    return;
  }
  const env = result.envelope;
  if (env.refusal) {
    container.append(refusalCard(env.refusal.reason, env.text));
  } else {
    build(container, env);
  }
  if (env.disclaimer) container.append(disclaimerFooter(env.disclaimer));
}

/* ---- rail + router ------------------------------------------------------- */

function buildRail() {
  const rail = document.getElementById("rail");
  const brand = el("div", { class: "brand" });
  brand.append(el("span", { text: "FinPlanet" }), el("b", { text: "Analyst Mind" }));
  rail.append(brand);
  for (const item of NAV) {
    if (item === null) {
      rail.append(el("div", { class: "railsep" }));
      continue;
    }
    const [route, label, icon] = item;
    const a = el("a", { class: "nav", href: `#/${route}`, "data-route": route });
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.innerHTML = ICONS[icon]; // trusted: our own icon table, no user data
    a.append(svg, document.createTextNode(label));
    rail.append(a);
  }
}

function currentRoute() {
  const hash = window.location.hash.replace(/^#\//, "");
  return hash.split("/")[0] || "main";
}

async function render() {
  const route = currentRoute();
  for (const a of document.querySelectorAll(".nav")) {
    a.classList.toggle("on", a.dataset.route === route);
  }
  const main = document.getElementById("main");
  const screen = screens[route] || screens.main;
  main.replaceChildren(el("p", { class: "loading", text: "loading…" }));
  try {
    await screen(main, { api, renderEnvelope });
  } catch (e) {
    main.replaceChildren(errorCard(`screen failed: ${e.message}`));
  }
}

buildRail();
window.addEventListener("hashchange", render);
render();
