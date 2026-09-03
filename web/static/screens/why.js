/* Why it moved: decomposition before narrative, unexplained share always shown. */

import { button, card, el, field, head, input, termPane } from "/components.js";

export async function why(root, { api, renderEnvelope }) {
  root.replaceChildren();
  root.classList.add("screen");
  root.append(
    head(
      "Why it moved",
      "Statistical decomposition first; a story is only allowed after the split."
    )
  );

  const f = {
    instrument: input("instrument", "MYX:1155"),
    proxy: input("market_proxy", "", { placeholder: "e.g. XNAS:SPY (measure legs)" }),
    ret: input("instrument_return", "", { placeholder: "-0.03 (typed)" }),
    market: input("market_return", "", { placeholder: "-0.01 (typed)" }),
    bars: input("bars", "1", { size: "4" }),
  };
  const out = el("div");
  const form = el("div", { class: "form-row" });
  form.append(
    field("instrument", f.instrument),
    field("market proxy (measured)", f.proxy),
    field("instrument return (typed)", f.ret),
    field("market return (typed)", f.market),
    field("bars", f.bars),
    button("decompose", async () => {
      const body = { instrument: f.instrument.value, bars: parseInt(f.bars.value || "1", 10) };
      if (f.proxy.value.trim()) {
        body.market_proxy = f.proxy.value.trim();
      } else {
        if (f.ret.value.trim()) body.instrument_return = parseFloat(f.ret.value);
        if (f.market.value.trim()) body.market_return = parseFloat(f.market.value);
      }
      const result = await api("/why", { body });
      renderEnvelope(out, result, (container, env) => {
        container.append(termPane(env.text, "decomposition"));
      });
    })
  );
  root.append(
    card([
      el("span", { class: "eyebrow", text: "either type both returns, or give a proxy and both legs are measured" }),
      form,
    ]),
    out
  );
}
