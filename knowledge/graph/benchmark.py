"""Does the graph earn its place, measured in the currency the budget counts?

The claim a knowledge graph makes is that answering a multi-hop question from a
small subgraph beats stuffing the corpus into a prompt. That is a claim about
tokens and therefore about money, so it should be measured rather than asserted -
and it should be allowed to come out badly.

ESTIMATED, NOT COUNTED. Exact token counts need the Anthropic count_tokens
endpoint, which needs a key and a network; this repo runs offline. The estimate
is `len(text) // 4`, the same heuristic core/llm/client.EchoBackend already uses,
so the two agree with each other. It is good to roughly +/-15% on English prose
and is not a billing figure - the ledger records real usage from real responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from knowledge.graph.entity_graph import EntityGraph, Path
from knowledge.graph.evidence import CuratedCorpus

#: Same divisor as EchoBackend, deliberately. Two offline estimates that
#: disagree are worse than one that is honestly approximate.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


@dataclass(frozen=True)
class Benchmark:
    question: str
    subgraph_tokens: int
    corpus_tokens: int
    hops: int
    answered: bool

    @property
    def ratio(self) -> float:
        return self.corpus_tokens / self.subgraph_tokens if self.subgraph_tokens else 0.0

    def cost_myr(self, per_mtok_usd: Decimal, fx: Decimal) -> tuple[Decimal, Decimal]:
        def rm(t: int) -> Decimal:
            return Decimal(t) / Decimal(1_000_000) * per_mtok_usd * fx

        return rm(self.subgraph_tokens), rm(self.corpus_tokens)

    def describe(self) -> str:
        verdict = "answered" if self.answered else "NOT answered"
        return (
            f"{self.question}\n"
            f"    subgraph {self.subgraph_tokens:>7} tokens ({self.hops} hops, {verdict})\n"
            f"    corpus   {self.corpus_tokens:>7} tokens\n"
            f"    ratio    {self.ratio:>7.1f}x"
        )


def path_context(graph: EntityGraph, path: Path, corpus: CuratedCorpus) -> str:
    """Exactly what a model would be given to answer from this path: the chain,
    and the text behind each hop. Nothing else - that is the point."""
    lines = [path.describe()]
    for hop in path.hops:
        doc = hop.edge.source_doc_id
        text = corpus.chunk("kb_supply_chain", doc) if doc else None
        lines.append(f"[{doc}] {text or '(no text)'}")
    return "\n".join(lines)


def corpus_context(corpus: CuratedCorpus) -> str:
    """The alternative: hand over everything and hope attention finds the link."""
    return "\n".join(corpus.all_chunks())


def run(
    graph: EntityGraph, corpus: CuratedCorpus, *, asof: date, questions: list[tuple[str, str, str]]
) -> list[Benchmark]:
    """questions is [(label, start_node, target_node)]."""
    whole = estimate_tokens(corpus_context(corpus))
    out: list[Benchmark] = []
    for label, start, target in questions:
        paths = graph.traverse(start, asof=asof, target=target)
        if not paths:
            out.append(Benchmark(label, whole, whole, 0, False))
            continue
        best = paths[0]
        out.append(
            Benchmark(
                label, estimate_tokens(path_context(graph, best, corpus)), whole, best.n_hops, True
            )
        )
    return out


def describe(results: list[Benchmark]) -> str:
    lines = [
        "TOKEN BENCHMARK - subgraph vs whole corpus",
        "(estimated at 4 chars/token, offline; not a billing figure)",
        "",
    ]
    lines += [r.describe() for r in results]
    answered = [r for r in results if r.answered]
    if answered:
        avg = sum(r.ratio for r in answered) / len(answered)
        lines += [
            "",
            f"{len(answered)}/{len(results)} answerable from a path, "
            f"averaging {avg:.1f}x fewer tokens than the whole corpus.",
        ]
    unanswered = [r for r in results if not r.answered]
    if unanswered:
        lines.append(
            f"{len(unanswered)} not answerable: the graph saves nothing on a "
            "question it cannot reach, and says so rather than guessing."
        )
    return "\n".join(lines)
