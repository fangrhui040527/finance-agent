/* Thesis: compose, red-team, and optionally let the model narrate - labelled. */

import { button, card, chip, el, field, head, input, termPane } from "/components.js";

export async function thesis(root, { api, renderEnvelope }) {
  root.replaceChildren();
  root.classList.add("screen");
  root.append(
    head("Thesis", "A stance needs at least two machine-checkable breakers, then it faces the red team.")
  );

  const f = {
    instrument: input("instrument", "MYX:1155"),
    stance: input("stance", "accumulate"),
    horizon: input("horizon_months", "12", { size: "4" }),
    b1s: input("b1_statement", "NIM falls under 2.0%"),
    b1q: input("b1_query", "nim < 2.0"),
    b1st: input("b1_store", "kb_filings"),
    b2s: input("b2_statement", "CASA under 22%"),
    b2q: input("b2_query", "casa < 22"),
    b2st: input("b2_store", "kb_filings"),
    ev: input("evidence", "", { placeholder: "a1_fundamentals=CASA fell to 24%", size: "44" }),
  };
  const narrate = el("input", { type: "checkbox" });
  const out = el("div");

  const form = el("div", { class: "form-row" });
  form.append(
    field("instrument", f.instrument),
    field("stance", f.stance),
    field("horizon (months)", f.horizon)
  );
  const b1 = el("div", { class: "form-row" });
  b1.append(field("breaker 1", f.b1s), field("query", f.b1q), field("store", f.b1st));
  const b2 = el("div", { class: "form-row" });
  b2.append(field("breaker 2", f.b2s), field("query", f.b2q), field("store", f.b2st));
  const evRow = el("div", { class: "form-row" });
  const narrateLabel = el("label", { class: "field" });
  narrateLabel.append(el("span", { class: "eyebrow", text: "narrate (model prose, labelled)" }), narrate);
  evRow.append(
    field("evidence (agent=text)", f.ev),
    narrateLabel,
    button("compose + attack", async () => {
      const body = {
        instrument: f.instrument.value,
        stance: f.stance.value,
        horizon_months: parseInt(f.horizon.value || "12", 10),
        breakers: [
          { statement: f.b1s.value, query: f.b1q.value, store: f.b1st.value },
          { statement: f.b2s.value, query: f.b2q.value, store: f.b2st.value },
        ].filter((b) => b.statement.trim()),
        evidence: [],
        narrate: narrate.checked,
      };
      const ev = f.ev.value.trim();
      if (ev.includes("=")) {
        const [agent, ...rest] = ev.split("=");
        body.evidence.push({ agent: agent.trim(), text: rest.join("=").trim() });
      }
      const result = await api("/thesis", { body });
      renderEnvelope(out, result, (container, env) => {
        container.append(termPane(env.text, "thesis + red team"));
        const n = env.data && env.data.narrative;
        if (n) {
          if (n.refused) {
            container.append(card([chip("model refused", "no"), el("p", { text: n.reason || "" })]));
          } else if (n.blocked) {
            container.append(card([chip("blocked by the output rail", "no"), el("p", { text: n.reason })]));
          } else {
            const pane = card([
              el("span", { class: "eyebrow", text: `narrative — ${n.backend}` }),
              el("p", { text: n.text }),
            ]);
            container.append(pane);
          }
        }
      });
    })
  );

  root.append(card([form, b1, b2, evRow]), out);
}
