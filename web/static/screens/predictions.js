/* Predictions and hypotheses: the forward record and the ideas above it. */

import { button, card, chip, el, field, head, input, table, termPane } from "/components.js";

export async function predictions(root, { api, renderEnvelope }) {
  root.replaceChildren();
  root.classList.add("screen");
  root.append(
    head("Predictions", "Write the call down before the outcome; grade it when the clock says so.")
  );

  const listing = el("div");
  const calBox = el("div");
  const hypBox = el("div");

  async function refresh() {
    const [preds, cal, hyps] = await Promise.all([
      api("/predictions"),
      api("/calibration"),
      api("/hypotheses"),
    ]);
    listing.replaceChildren();
    if (preds.ok) {
      const d = preds.envelope.data;
      listing.append(
        card([
          el("span", { class: "eyebrow", text: `pending (${d.pending.length})` }),
          d.pending.length
            ? table(
                ["id", "instrument", "dir", "conf", "grade on", "statement"],
                d.pending.map((p) => [
                  p.prediction_id,
                  p.instrument_id,
                  p.direction > 0 ? "+1" : String(p.direction),
                  p.confidence,
                  p.grade_on,
                  p.statement,
                ])
              )
            : el("p", { class: "history", text: "nothing pending" }),
        ])
      );
      if (d.graded.length) {
        listing.append(
          card([
            el("span", { class: "eyebrow", text: `graded (${d.graded.length})` }),
            table(
              ["id", "graded on", "return", "benchmark", "verdict"],
              d.graded.map((o) => [
                o.prediction_id,
                o.graded_on,
                o.realised_return,
                o.benchmark_return,
                chip(o.correct ? "right" : "wrong", o.correct ? "ok" : "no"),
              ])
            ),
          ])
        );
      }
    }
    renderEnvelope(calBox, cal, (container, env) => {
      container.append(termPane(env.text, "calibration"));
    });
    hypBox.replaceChildren();
    if (hyps.ok) {
      const rows = hyps.envelope.data || [];
      hypBox.append(
        card([
          el("span", { class: "eyebrow", text: `hypotheses (${rows.length})` }),
          rows.length
            ? table(
                ["id", "status", "title", "thesis", "predictions"],
                rows.map((h) => [
                  h.hypothesis_id,
                  chip(h.status, h.status === "rejected" ? "no" : h.status === "validated" ? "ok" : "n"),
                  h.title,
                  h.thesis,
                  h.predictions.length,
                ])
              )
            : el("p", { class: "history", text: "no hypotheses registered yet" }),
        ])
      );
    }
  }

  const f = {
    instrument: input("instrument", "MYX:1155"),
    direction: input("direction", "1", { size: "3" }),
    horizon: input("horizon_days", "63", { size: "4" }),
    confidence: input("confidence", "0.6", { size: "5" }),
    thesis: input("thesis", "", { placeholder: "what has to be true", size: "36" }),
  };
  const logOut = el("div");
  const logForm = el("div", { class: "form-row" });
  logForm.append(
    field("instrument", f.instrument),
    field("direction", f.direction),
    field("horizon (days)", f.horizon),
    field("confidence", f.confidence),
    field("thesis", f.thesis),
    button("log it", async () => {
      const result = await api("/predictions", {
        body: {
          instrument: f.instrument.value,
          direction: parseInt(f.direction.value, 10),
          horizon_days: parseInt(f.horizon.value, 10),
          confidence: parseFloat(f.confidence.value),
          thesis: f.thesis.value,
        },
      });
      renderEnvelope(logOut, result, (container, env) => {
        container.append(termPane(env.text, "logged"));
      });
      await refresh();
    })
  );

  const h = { title: input("title", ""), thesis: input("h_thesis", "", { size: "40" }) };
  const hypForm = el("div", { class: "form-row" });
  const hypOut = el("div");
  hypForm.append(
    field("hypothesis title", h.title),
    field("falsifiable thesis", h.thesis),
    button("register idea", async () => {
      const result = await api("/hypotheses", {
        body: { title: h.title.value, thesis: h.thesis.value },
      });
      renderEnvelope(hypOut, result, (container, env) => {
        container.append(termPane(env.text, "registered"));
      });
      await refresh();
    })
  );

  root.append(card([logForm, logOut]), listing, calBox, card([hypForm, hypOut]), hypBox);
  await refresh();
}
