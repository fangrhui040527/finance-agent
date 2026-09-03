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

  // --- the money questions, in the order they have to be asked ---------------
  // How much may be invested at all, then how it splits across names YOU
  // nominate, then what changes against what is already held. None of these
  // choose a name: the screen says so where the user is about to ask.
  const capitalOut = el("div");
  const capitalPane = card([
    el("h3", { text: "1. How much may be invested at all" }),
    el("p", {
      class: "history",
      text:
        "From [capital] in config.toml: liquid assets, less the emergency floor, " +
        "near-term goals inside 24 months and debt above the hurdle. The first " +
        "three are locked and nothing here reduces them.",
    }),
    button("investable capital", async () => {
      const result = await api("/capital");
      renderEnvelope(capitalOut, result, (container, env) => {
        container.append(termPane(env.text, "capital"));
      });
    }),
    capitalOut,
  ]);

  const allocOut = el("div");
  const names = el("textarea", {
    rows: "6",
    class: "names",
    placeholder:
      "one per line   MIC:CODE:PRICE:STOP:ADV:SECTOR   e.g. MYX:1155:10.68:9.90:20000000:bank",
  });
  const allocValue = input("portfolio_value", "", { size: "10" });
  const allocPane = card([
    el("h3", { text: "2. Split a budget across names you nominate" }),
    el("p", {
      class: "history",
      text:
        "This does not choose the names - it sizes and bounds the ones you bring. " +
        "Leave the capital blank to take it from the plan above. Expect cash left " +
        "over: the note says which limit stopped the deployment.",
    }),
    names,
    el("div", { class: "form-row" }, [
      field("capital (blank = from plan)", allocValue),
      button("allocate", async () => {
        const list = names.value
          .split(/[\r\n]+/)
          .map((x) => x.trim())
          .filter(Boolean);
        const body = { names: list };
        if (allocValue.value.trim()) body.portfolio_value = parseFloat(allocValue.value);
        const result = await api("/allocate", { body });
        renderEnvelope(allocOut, result, (container, env) => {
          container.append(termPane(env.text, "allocation"));
        });
      }),
    ]),
    allocOut,
  ]);

  const rebalOut = el("div");
  const rebalValue = input("portfolio_value", "", { size: "10" });
  const rebalPane = card([
    el("h3", { text: "3. What to change versus what you hold" }),
    el("p", {
      class: "history",
      text:
        "The book comes from account.holdings in config.toml - it cannot be typed " +
        "here, because a book you never stated is not your book. A trade worth less " +
        "than its own round trip comes back as hold, with the number.",
    }),
    el("div", { class: "form-row" }, [
      field("capital (blank = the book itself)", rebalValue),
      button("rebalance", async () => {
        const body = {};
        if (rebalValue.value.trim()) body.portfolio_value = parseFloat(rebalValue.value);
        const result = await api("/rebalance", { body });
        renderEnvelope(rebalOut, result, (container, env) => {
          container.append(termPane(env.text, "rebalance"));
        });
      }),
    ]),
    rebalOut,
  ]);

  root.append(
    capitalPane,
    allocPane,
    rebalPane,
    el("h3", { text: "The shape of a book you type here" }),
    card([listBox]),
    card([form]),
    out
  );
}
