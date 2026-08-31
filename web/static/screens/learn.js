/* Learn: the curriculum, in the order it enforces. */

import { button, card, el, field, head, input, termPane } from "/components.js";

export async function learn(root, { api, renderEnvelope }) {
  root.replaceChildren();
  root.classList.add("screen");
  root.append(head("Learn", "Thirty concepts with enforced prerequisites - the order is the point."));

  const listing = el("div");
  root.append(listing);
  const concepts = await api("/learn/concepts");
  renderEnvelope(listing, concepts, (container, env) => {
    container.append(termPane(env.text, "curriculum"));
  });

  const concept = input("concept", "", { placeholder: "e.g. position_sizing" });
  const mastered = input("mastered", "", { placeholder: "comma-separated concepts you know" });
  const out = el("div");
  const form = el("div", { class: "form-row" });
  form.append(
    field("concept", concept),
    field("already mastered", mastered),
    button("explain", async () => {
      const result = await api("/learn/explain", {
        body: {
          concept: concept.value.trim(),
          mastered: mastered.value
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
        },
      });
      renderEnvelope(out, result, (container, env) => {
        container.append(termPane(env.text, "explanation"));
      });
    })
  );
  root.append(card([form]), out);
}
