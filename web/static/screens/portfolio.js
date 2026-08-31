/* Portfolio: concentration, heat, effective bets. Positions live in this
 * browser only (localStorage) - the server never stores a book. */

import { button, card, el, field, head, input, table, termPane } from "/components.js";

const KEY = "finplanet.positions.v1";

function loadPositions() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "[]");
  } catch {
    return [];
  }
}

function savePositions(rows) {
  try {
    localStorage.setItem(KEY, JSON.stringify(rows));
  } catch {
    /* private windows may refuse; the screen still works for this visit */
  }
}

export async function portfolio(root, { api, renderEnvelope }) {
  root.replaceChildren();
  root.classList.add("screen");
  root.append(
    head("Portfolio", "The book's shape: concentration, heat, effective bets. Positions stay in this browser.")
  );

  let rows = loadPositions();
  const out = el("div");
  const listBox = el("div");

  function renderList() {
    listBox.replaceChildren();
    if (!rows.length) {
      listBox.append(el("p", { class: "history", text: "no positions yet - add one below" }));
      return;
    }
    listBox.append(
      table(
        ["instrument", "weight", "sector", "country", "risk to stop", ""],
        rows.map((r, i) => [
          r.instrument,
          r.weight,
          r.sector,
          r.country,
          r.risk_to_stop,
          button("remove", () => {
            rows.splice(i, 1);
            savePositions(rows);
            renderList();
          }, "btn"),
        ])
      )
    );
  }

  const f = {
    instrument: input("instrument", "MYX:1155"),
    weight: input("weight", "0.10", { size: "6" }),
    sector: input("sector", "bank"),
    country: input("country", "MY", { size: "4" }),
    risk: input("risk_to_stop", "0.008", { size: "6" }),
  };
  const form = el("div", { class: "form-row" });
  form.append(
    field("instrument", f.instrument),
    field("weight", f.weight),
    field("sector", f.sector),
    field("country", f.country),
    field("risk to stop", f.risk),
    button("add", () => {
      rows.push({
        instrument: f.instrument.value,
        weight: parseFloat(f.weight.value),
        sector: f.sector.value,
        country: f.country.value,
        risk_to_stop: parseFloat(f.risk.value || "0"),
      });
      savePositions(rows);
      renderList();
    }),
    button("check the book", async () => {
      const result = await api("/portfolio/risk", { body: { positions: rows } });
      renderEnvelope(out, result, (container, env) => {
        container.append(termPane(env.text, "risk"));
      });
    })
  );

  renderList();
  root.append(card([listBox]), card([form]), out);
}
