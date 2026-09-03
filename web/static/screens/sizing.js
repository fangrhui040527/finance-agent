/* Sizing: five caps, the binding one, and NO POSITION as a first-class outcome. */

import { button, card, el, field, head, input, termPane } from "/components.js";

export async function sizing(root, { api, renderEnvelope }) {
  root.replaceChildren();
  root.classList.add("screen");
  root.append(
    head("Sizing", "Five caps computed in code; the answer is lots, or NO POSITION with the cap that said so.")
  );

  const f = {
    instrument: input("instrument", "MYX:1155"),
    pv: input("portfolio_value", "200000"),
    price: input("price", "6.20"),
    stop: input("stop_price", "5.60"),
    adv: input("adv_20d", "900000"),
    risk: input("risk_per_trade", "0.0075", { size: "7" }),
    fx: input("fx_myr_per_unit", "", { placeholder: "needed off-MYR markets" }),
  };
  const out = el("div");
  const form = el("div", { class: "form-row" });
  form.append(
    field("instrument", f.instrument),
    field("portfolio (MYR)", f.pv),
    field("entry (market ccy)", f.price),
    field("stop", f.stop),
    field("20d ADV", f.adv),
    field("risk/trade", f.risk),
    field("fx MYR per unit", f.fx),
    button("size it", async () => {
      const body = {
        instrument: f.instrument.value,
        portfolio_value: parseFloat(f.pv.value),
        price: parseFloat(f.price.value),
        stop_price: parseFloat(f.stop.value),
        adv_20d: parseFloat(f.adv.value),
        risk_per_trade: parseFloat(f.risk.value),
      };
      if (f.fx.value.trim()) body.fx_myr_per_unit = parseFloat(f.fx.value);
      const result = await api("/sizing", { body });
      renderEnvelope(out, result, (container, env) => {
        container.append(termPane(env.text, "sizing"));
      });
    })
  );
  root.append(card([el("span", { class: "eyebrow", text: "prices in the market's currency; the book in MYR" }), form]), out);
}
