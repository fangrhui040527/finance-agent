/* Prices: the chain-fed daily bars, charted, with NO DATA as a first-class state. */

import { button, card, el, field, head, input, lineChart, statRow, termPane } from "/components.js";

export async function prices(root, { api, renderEnvelope }) {
  root.replaceChildren();
  root.classList.add("screen");
  root.append(head("Prices", "Daily bars from the source chain (Stooq, then Yahoo). Cached for the trading day."));

  const inst = input("instrument", "XNAS:NVDA");
  const bars = input("bars", "60", { size: "5" });
  const asAt = input("as_at", "", { placeholder: "YYYY-MM-DD (point in time)" });
  const out = el("div");

  async function load() {
    const [market, code] = inst.value.split(":");
    if (!market || !code) return;
    const qs = new URLSearchParams({ bars: bars.value || "60" });
    if (asAt.value.trim()) qs.set("as_at", asAt.value.trim());
    const result = await api(`/prices/${encodeURIComponent(market)}/${encodeURIComponent(code)}?${qs}`);
    renderEnvelope(out, result, (container, env) => {
      const d = env.data;
      if (d && d.bars && d.bars.length) {
        container.append(
          card([
            el("span", { class: "eyebrow", text: `${d.instrument} · close` }),
            lineChart(d.bars.map((b) => b.close)),
            statRow([
              ["bars shown", d.bars.length],
              ["last close", d.bars[d.bars.length - 1].close.toFixed(3)],
              ["20d ADV", Math.round(d.adv_20d).toLocaleString()],
              ["20d ATR", d.atr_20d.toFixed(4)],
            ]),
          ])
        );
      }
      container.append(termPane(env.text, "bars"));
    });
  }

  const form = el("div", { class: "form-row" });
  form.append(field("instrument", inst), field("bars", bars), field("as at", asAt), button("fetch", load));
  root.append(card([form]), out);
  await load();
}
