/* Settings: read-only in v1. The file is the interface; the screen shows it. */

import { card, el, head, table, termPane } from "/components.js";

export async function settings(root, { api, renderEnvelope }) {
  root.replaceChildren();
  root.classList.add("screen");
  root.append(
    head(
      "Settings",
      "Read-only: values come from config.toml (config.local.toml wins). Edit the file; the bounds below cannot be widened by any file."
    )
  );

  const [cfg, doctor] = await Promise.all([api("/config"), api("/doctor?offline=true")]);

  const box = el("div");
  renderEnvelope(box, cfg, (container, env) => {
    container.append(termPane(env.text, "config"));
    const bounds = env.data.hard_bounds || [];
    if (bounds.length) {
      container.append(
        card([
          el("span", { class: "eyebrow", text: "hard bounds (in code - a config file cannot widen these)" }),
          table(
            ["field", "bound", "why"],
            bounds.map((b) => [b.field, b.bound, b.why])
          ),
        ])
      );
    }
  });
  root.append(box);

  const docBox = el("div");
  renderEnvelope(docBox, doctor, (container, env) => {
    container.append(termPane(env.text, "preflight (offline)"));
  });
  root.append(docBox);
}
