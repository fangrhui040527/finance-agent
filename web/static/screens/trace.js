/* Trace: run list, event tree, methodology manifest - the anatomy of a run. */

import { card, chip, el, head, table, termPane } from "/components.js";

export async function trace(root, { api }) {
  root.replaceChildren();
  root.classList.add("screen");
  root.append(
    head("Trace", "Every run's anatomy: spans, model calls, refusals, and the methodology hash.")
  );

  const listBox = el("div");
  const detail = el("div");
  root.append(listBox, detail);

  const runs = await api("/trace/runs");
  if (!runs.ok) {
    listBox.append(el("p", { class: "history", text: runs.reason }));
    return;
  }
  const rows = runs.envelope.data || [];
  if (!rows.length) {
    listBox.append(
      card([el("p", { class: "history", text: "no trace runs under debug/ - run `run trace` first" })])
    );
    return;
  }

  async function open(runId) {
    detail.replaceChildren(el("p", { class: "loading", text: "loading run…" }));
    const result = await api(`/trace/runs/${encodeURIComponent(runId)}`);
    detail.replaceChildren();
    if (!result.ok) {
      detail.append(el("p", { class: "history", text: result.reason }));
      return;
    }
    const d = result.envelope.data;
    const m = d.manifest || {};
    if (m.manifest_hash) {
      detail.append(
        card([
          el("span", { class: "eyebrow", text: "methodology manifest" }),
          el("p", { class: "mono", text: `hash ${m.manifest_hash}` }),
          el("p", {
            class: "history",
            text: "equal hash = equal methodology; a diff names what changed",
          }),
        ])
      );
    }
    const lines = d.events
      .map((e) => {
        const pad = "  ".repeat(e.depth || 0);
        const dur = e.duration_ms != null ? `  ${e.duration_ms.toFixed(1)}ms` : "";
        const err = e.error ? `  ERROR: ${e.error}` : "";
        return `${pad}${e.kind}  ${e.name}${dur}${err}`;
      })
      .join("\n");
    detail.append(termPane(lines, `events — ${d.run_id}`));
  }

  listBox.append(
    card([
      table(
        ["run", "events", "cost (RM)", "refusals", ""],
        rows.map((r) => {
          const s = r.summary || {};
          const llm = s.llm || {};
          const openBtn = el("a", { href: "#/trace", text: "open", class: "mono" });
          openBtn.addEventListener("click", (e) => {
            e.preventDefault();
            open(r.run_id);
          });
          return [
            r.run_id,
            s.events ?? "?",
            llm.cost_myr ?? "?",
            s.refusals != null ? chip(String(s.refusals), "n") : "?",
            openBtn,
          ];
        })
      ),
    ])
  );
}
