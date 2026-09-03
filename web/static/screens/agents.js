/* Agents: the sixteen, their tools and private corpora, from the registry. */

import { card, chip, el, head, table } from "/components.js";

export async function agents(root, { api }) {
  root.replaceChildren();
  root.classList.add("screen");
  root.append(
    head("Agents", "Sixteen specialists. Tools come from the registry, and a missing eval suite refuses to register.")
  );

  const result = await api("/agents");
  if (!result.ok) {
    root.append(el("p", { class: "history", text: result.reason }));
    return;
  }
  const rows = result.envelope.data || [];
  root.append(
    card([
      table(
        ["agent", "layer", "tools", "knowledge", "tier hint", "eval suite"],
        rows.map((a) => [
          a.id,
          a.layer,
          a.tools.join(", "),
          a.knowledge.join(", ") || "—",
          chip(a.tier_hint, "n"),
          a.eval_suite,
        ])
      ),
      el("p", {
        class: "history",
        text: "llm_complete is granted to exactly four agents; every number elsewhere is engine-computed.",
      }),
    ])
  );
}
