/* Overview: the daily brief, which backend answers, and a place to ask. */

import { button, card, chip, el, head, input, termPane } from "/components.js";

export async function main(root, { api, renderEnvelope }) {
  root.replaceChildren();
  root.classList.add("screen");
  root.append(
    head("Overview", "What needs a decision, what is spending money, and who is answering.")
  );

  const [brief, backend] = await Promise.all([api("/brief"), api("/backend")]);

  if (backend.ok) {
    const d = backend.envelope.data;
    const row = el("div", { class: "form-row" });
    row.append(
      chip(d.backend, d.is_stub ? "wait" : "ok"),
      d.cheap_capped ? chip("FINPLANET_CHEAP: every tier → cheapest model", "n") : "",
      el("p", { class: "history", text: backend.envelope.text })
    );
    root.append(card([el("span", { class: "eyebrow", text: "model backend" }), row]));
  }

  const briefBox = el("div");
  root.append(briefBox);
  renderEnvelope(briefBox, brief, (container, env) => {
    container.append(termPane(env.text, "daily brief"));
  });

  // Ask: routed through the supervisor, refusals rendered as content.
  const q = input("question", "", { placeholder: "why did maybank fall today", size: "48" });
  const out = el("div");
  const form = el("div", { class: "form-row" });
  form.append(
    q,
    button("plan it", async () => {
      const result = await api("/plan", { body: { question: q.value } });
      renderEnvelope(out, result, (container, env) => {
        container.append(termPane(env.text, "plan"));
      });
    })
  );
  root.append(
    card([el("span", { class: "eyebrow", text: "ask the supervisor" }), form, out])
  );
}
