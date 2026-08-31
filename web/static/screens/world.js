/* World monitor: which feeds are live, keyless, and overridable. */

import { card, chip, el, head, table } from "/components.js";

export async function world(root, { api }) {
  root.replaceChildren();
  root.classList.add("screen");
  root.append(
    head("World monitor", "What can flow in: one live news net, a two-source price chain, no keys anywhere.")
  );

  const result = await api("/world");
  if (!result.ok) {
    root.append(el("p", { class: "history", text: result.reason }));
    return;
  }
  const feeds = result.envelope.data.feeds || [];
  root.append(
    card([
      table(
        ["feed", "kind", "keyless", "enabled", "notes"],
        feeds.map((f) => [
          f.name,
          f.kind,
          f.keyless ? chip("keyless", "ok") : chip("key", "wait"),
          f.enabled ? chip("enabled", "ok") : chip("off", "n"),
          f.endpoint_override ? "endpoint overridden via GDELT_DOC_API" : "",
        ])
      ),
      el("p", {
        class: "history",
        text: "A disabled or failing feed says so; an empty list is never read as a quiet news day.",
      }),
    ])
  );
}
