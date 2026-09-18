/* Paper book: the hypothetical USD 1,000 ledger (docs/22).
 *
 * Every figure on this screen is read off /paper's data - the same Status the
 * CLI prints and the model reads through paper_status. The screen computes
 * two things only: the decided-minus-control gap in percentage points, and
 * the phase calendar's dates from the day the book opened. Nothing here
 * places anything anywhere. */

import { card, chip, el, head, statRow, table, termPane } from "/components.js";

/* Dates are ISO day strings; the arithmetic is done in UTC so a local
 * timezone cannot shift a phase boundary by a day. */
function addDays(iso, n) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

const isNum = (x) => typeof x === "number" && Number.isFinite(x);
const usd = (x) => (isNum(x) ? `USD ${x.toFixed(2)}` : "n/a");
const pct = (x, dp = 2) => (isNum(x) ? `${(x * 100).toFixed(dp)}%` : "n/a");
const fixed = (x, dp) => (isNum(x) ? x.toFixed(dp) : "n/a");
/* Strings the API carried over from Decimals (avg cost, close, open P&L). */
const dec = (s, dp) => (s === null || s === undefined || s === "" ? "n/a" : Number(s).toFixed(dp));

/* core.market.calendar.price_state: a settled close, the session so far, or unknowable. */
const PRICE_STATE_KIND = { settled: "ok", provisional: "wait", unknown: "n" };

function section(title, children, note = "") {
  const kids = [el("span", { class: "eyebrow", text: title })];
  if (note) kids.push(el("p", { class: "history", text: note }));
  return card(kids.concat(children));
}

/* (a) where the book stands. */
function tiles(d) {
  let gap = "n/a";
  if (isNum(d.return_to_date) && isNum(d.control_return_to_date)) {
    const pp = (d.return_to_date - d.control_return_to_date) * 100;
    gap = `${pp >= 0 ? "+" : ""}${pp.toFixed(2)} pp`;
  }
  const kids = [];
  if (d.halted) {
    const flag = el("div", { class: "form-row" });
    flag.append(
      chip("HALTED", "no"),
      el("p", {
        class: "history",
        text: "drawdown from peak reached the halt: no target may raise a weight; reductions and stops still run",
      })
    );
    kids.push(flag);
  }
  kids.push(
    statRow([
      ["equity", usd(d.equity_usd)],
      ["cash", usd(d.cash_usd)],
      ["control equity", usd(d.control_equity_usd)],
      ["decided − control", gap],
      ["drawdown", pct(d.drawdown)],
      ["max drawdown", pct(d.max_drawdown)],
      ["phase", `${d.phase} - week ${d.week}`],
      ["marks", d.marked_on ? `${d.marks} (last ${d.marked_on})` : `${d.marks} (not yet marked)`],
    ]),
    el("p", { class: "history", text: d.phase_note })
  );
  for (const n of d.notes || []) kids.push(el("p", { class: "history", text: `note: ${n}` }));
  return card(kids);
}

/* (b) docs/22 section 5: from the start date, weeks 1-2 observe, 3-6 ramp,
 * week 7 on full, week 14 the P16 review. The dates are arithmetic on the day
 * the book opened; which row is current is the phase the API named. */
function calendar(d) {
  if (!d.started) {
    return section("phase calendar", [el("p", { class: "history", text: "no start date recorded" })]);
  }
  const rows = [
    ["observe", "weeks 1-2", d.started, addDays(d.started, 13), "decisions are logged and graded, never applied"],
    ["ramp", "weeks 3-6", addDays(d.started, 14), addDays(d.started, 41), "targets apply; invested weight under the ramp ceiling"],
    ["full", "week 7 on", addDays(d.started, 42), "", "full caps; the cash floor holds"],
    ["review", "week 14", addDays(d.started, 91), "", "P16 review due: the thirteen-week record is read"],
  ];
  const order = ["observe", "ramp", "full"];
  const state = (name) => {
    if (name === "review") return d.week >= 14 ? chip("due", "ok") : chip("ahead", "n");
    if (name === d.phase) return chip(`now - week ${d.week}`, "ok");
    return order.indexOf(name) < order.indexOf(d.phase) ? chip("done", "n") : chip("ahead", "n");
  };
  return section(
    "phase calendar",
    [table(["phase", "weeks", "from", "to", "what changes", "state"], rows.map((r) => [...r, state(r[0])]))],
    `from the ${d.started} start; the caps do not change at week 14, the review is added to the page`
  );
}

/* (c) each cap against its limit, drift included. */
function caps(d) {
  return section("caps (value / limit)", [
    table(
      ["cap", "value", "limit", "state"],
      d.caps.map((c) => [c.cap, c.value, c.limit, c.breached ? chip("breached", "no") : chip("ok", "ok")])
    ),
  ]);
}

/* (d) which names one lot can buy at this equity, and at what price. */
function fundable(d) {
  const rows = d.fundable.map((f) => {
    const state = el("span");
    state.append(chip(f.price_state || "unknown", PRICE_STATE_KIND[f.price_state] || "n"));
    if (!f.fundable) state.append(" ", chip("not fundable at this equity", "no"));
    return [
      f.instrument_id,
      String(f.lot),
      fixed(f.lot_usd, 2),
      pct(f.lot_weight, 1),
      String(f.max_lots),
      pct(f.round_trip),
      f.error ? `NO DATA: ${f.error.slice(0, 60)}` : fixed(f.price_local, 4),
      f.price_day || "n/a",
      state,
    ];
  });
  return section(
    "fundable at this equity",
    [table(["name", "one lot", "USD", "% equity", "lots allowed", "round trip", "price", "price day", "state"], rows)],
    "one lot at the last cached price; a provisional price is the session so far, not a close"
  );
}

/* (e) what is held and what is queued for the next bar. */
function holdings(d) {
  const held = d.positions.length
    ? table(
        ["instrument", "units", "avg cost", "close", "close day", "USD", "weight", "open P&L", ""],
        d.positions.map((p) => [
          p.instrument_id,
          String(p.units),
          `${dec(p.avg_cost, 4)} ${p.currency || ""}`.trim(),
          dec(p.close, 4),
          p.close_day || "n/a",
          fixed(p.value_usd, 2),
          pct(p.weight, 1),
          dec(p.pnl_open_usd, 2),
          p.stale ? chip("STALE", "wait") : "",
        ])
      )
    : el("p", { class: "history", text: "none (cash)" });
  const queued = d.pending.length
    ? table(
        ["instrument", "weight", "reason", "decided on"],
        d.pending.map((t) => [t.instrument_id, pct(t.weight), t.reason, t.decided_on])
      )
    : el("p", { class: "history", text: "none" });
  return [
    section("positions", [held]),
    section(
      "pending targets",
      [queued],
      "a decision recorded today applies at each market's first cached bar after today"
    ),
  ];
}

/* (f) turnover headroom, the FX quote and its source, cost drag to date. */
function money(d) {
  const fx = d.fx || {};
  const cost = d.cost || {};
  return section("turnover, fx and cost", [
    statRow([
      ["turnover used, 5 weekdays", `${usd(d.turnover_used_usd)} / ${fixed(d.turnover_cap_usd, 2)}`],
      ["fx MYR per USD", `${fixed(fx.rate, 4)} (${fx.source || "n/a"}, ${fx.date || "n/a"})`],
      ["cost to date", `USD ${cost.total ?? "n/a"} (${cost.pct_of_initial ?? "n/a"} of opening cash)`],
      ["fees", `USD ${cost.fees ?? "n/a"}`],
      ["fx spread", `USD ${cost.fx_spread ?? "n/a"}`],
      ["slippage", `USD ${cost.slippage ?? "n/a"}`],
    ]),
    el("p", {
      class: "history",
      text: "marks use the fx mid; entries buy ringgit below it and exits sell above it",
    }),
  ]);
}

export async function paper(root, { api, renderEnvelope }) {
  root.replaceChildren();
  root.classList.add("screen");
  root.append(
    head(
      "Paper book",
      "A hypothetical USD 1,000 ledger marked from cached bars. It places nothing; it records what the decider chose inside the caps."
    )
  );

  const statusBox = el("div");
  const reportBox = el("div");
  const textBox = el("div");
  root.append(statusBox, reportBox, textBox);

  const [status, report] = await Promise.all([api("/paper"), api("/paper/report?days=30")]);

  let statusText = "";
  renderEnvelope(statusBox, status, (container, env) => {
    const d = env.data;
    if (!d) {
      container.append(termPane(env.text, "no book"));
      return;
    }
    container.append(tiles(d), calendar(d), caps(d), fundable(d), ...holdings(d), money(d));
    statusText = env.text;
  });
  if (!statusText) return; // NO BOOK: the words above are the whole answer

  // (g) the report's words, then (h) the status page itself as the CLI prints it.
  renderEnvelope(reportBox, report, (container, env) => {
    container.append(termPane(env.text, "report"));
  });
  const details = el("details");
  details.append(
    el("summary", { class: "history", text: "status page, as `ask.py paper status` prints it" }),
    termPane(statusText, "status")
  );
  textBox.append(card([details]));
}
