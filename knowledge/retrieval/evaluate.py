"""Does retrieval actually find the right document? Measured, not asserted.

Every other quality claim in this system rests on evidence. Retrieval did not:
the pipeline was built, wired and tested for SHAPE - does it return hits, does
the freshness filter bite, does an empty collection grade INSUFFICIENT - and
never once for ACCURACY. "The search is good" was a design intention that no
number had ever been asked to defend.

This is the number. A small human-labelled set of questions, each pinned to the
articles in `data/corpus.db` that answer it, run through the four retrieval legs
separately so each one's contribution is visible:

  bm25      exact-token search alone
  dense     vector search alone
  fused     both, merged by reciprocal rank fusion - what `Collection.search` is
  reranked  fused, then put through `rerank`

The one column that matters most is the last: DENSE LIFT, the number of
questions where the dense leg found a relevant article that BM25's own list did
not contain. A dense leg that lifts nothing is a second lexical search wearing a
vector's clothes, and the fusion is paying rank-fusion overhead for a duplicate.

RECALL HERE IS A LOWER BOUND. The labels name articles verified to answer the
question; a corpus of 1,342 headlines certainly contains others nobody labelled,
so an unlabelled hit is scored as a miss. That makes the absolute numbers
pessimistic and the COMPARISON between legs sound, which is what a change to the
embedder needs to be judged on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from knowledge.chunking.parent_child import Chunk
from knowledge.retrieval.hybrid import Collection, Hit, rerank

#: The labelled questions. Human-written, dated, and pinned to doc ids.
GOLD = Path(__file__).parent / "data" / "retrieval_gold.yaml"

#: How deep each leg is allowed to look. Ten is the honest depth for this
#: system: `Collection.search` returns 8 by default and the pipeline generates
#: from fewer still, so a relevant document at rank 40 is not found in any sense
#: a reader would recognise.
DEPTH = 10

LEGS = ("bm25", "dense", "fused", "reranked")


@dataclass(frozen=True)
class Case:
    """One question and the documents known to answer it."""

    query: str
    relevant: tuple[str, ...]
    kind: str = "semantic"
    note: str = ""
    #: Titles as they read when the label was written. Not used for scoring -
    #: used to say WHICH label went missing when the corpus drifts, because
    #: "recall fell" and "three labelled articles aged out" look identical in
    #: a score and are opposite problems.
    titles: tuple[str, ...] = ()


@dataclass
class LegScore:
    leg: str
    cases: int = 0
    hit_at_1: int = 0
    hit_at_5: int = 0
    hit_at_10: int = 0
    reciprocal: float = 0.0

    def _share(self, n: float) -> float:
        return n / self.cases if self.cases else 0.0

    @property
    def recall_at_1(self) -> float:
        return self._share(self.hit_at_1)

    @property
    def recall_at_5(self) -> float:
        return self._share(self.hit_at_5)

    @property
    def recall_at_10(self) -> float:
        return self._share(self.hit_at_10)

    @property
    def mrr(self) -> float:
        """Mean reciprocal rank: 1.0 when the right article is always first,
        0.5 when it is always second, 0 when it is never in the list at all."""
        return self._share(self.reciprocal)

    def observe(self, ranks: list[int]) -> None:
        """`ranks` are the 1-based positions of relevant documents, ascending."""
        self.cases += 1
        if not ranks:
            return
        best = ranks[0]
        self.hit_at_1 += best <= 1
        self.hit_at_5 += best <= 5
        self.hit_at_10 += best <= 10
        self.reciprocal += 1.0 / best


@dataclass
class Report:
    legs: dict[str, LegScore]
    dense_lift: int = 0
    dense_lift_cases: list[str] = field(default_factory=list)
    by_kind: dict[str, dict[str, LegScore]] = field(default_factory=dict)
    missing_labels: list[str] = field(default_factory=list)
    cases: int = 0
    corpus_size: int = 0

    @property
    def best_leg(self) -> str:
        return max(LEGS, key=lambda leg: (self.legs[leg].mrr, self.legs[leg].recall_at_10))

    def table(self) -> str:
        rows = [f"{'leg':10} {'r@1':>7} {'r@5':>7} {'r@10':>7} {'MRR':>7}"]
        for leg in LEGS:
            s = self.legs[leg]
            rows.append(
                f"{leg:10} {s.recall_at_1:7.1%} {s.recall_at_5:7.1%} "
                f"{s.recall_at_10:7.1%} {s.mrr:7.3f}"
            )
        return "\n".join(rows)

    def summary(self) -> str:
        out = [
            f"{self.cases} labelled questions over {self.corpus_size} indexed chunks",
            "",
            self.table(),
        ]
        for kind in sorted(self.by_kind):
            out.append("")
            out.append(f"  {kind} questions only")
            for leg in LEGS:
                s = self.by_kind[kind][leg]
                out.append(
                    f"    {leg:10} {s.recall_at_10:7.1%} r@10   {s.mrr:7.3f} MRR   "
                    f"({s.cases} cases)"
                )
        out.append("")
        out.append(
            f"dense lift: {self.dense_lift} of {self.cases} questions where the vector leg "
            f"found a relevant article BM25's top {DEPTH} did not"
        )
        if self.dense_lift_cases:
            for q in self.dense_lift_cases[:5]:
                out.append(f"    + {q}")
        if self.missing_labels:
            out.append("")
            out.append(
                f"{len(self.missing_labels)} labelled documents are no longer in the corpus "
                "- the score below is against a shrunken gold set:"
            )
            for m in self.missing_labels[:8]:
                out.append(f"    ? {m}")
        return "\n".join(out)


def _ranks(results: list[Chunk] | list[Hit], relevant: set[str]) -> list[int]:
    out: list[int] = []
    for i, item in enumerate(results, start=1):
        chunk = item.chunk if isinstance(item, Hit) else item
        if chunk.chunk_id in relevant or chunk.metadata.get("doc_id") in relevant:
            out.append(i)
    return out


def _ids(results: list[Chunk] | list[Hit]) -> set[str]:
    out: set[str] = set()
    for item in results:
        chunk = item.chunk if isinstance(item, Hit) else item
        out.add(chunk.chunk_id)
    return out


def run_case(collection: Collection, case: Case, depth: int = DEPTH) -> dict[str, list[int]]:
    """The same question down all four legs, with no filters in the way.

    Deliberately no freshness or entity filter: this measures the SEARCH, and a
    hard filter that removes a relevant document would be scored as a retrieval
    miss it is not responsible for.
    """
    relevant = set(case.relevant)
    sparse = [c for c, _ in collection.bm25.search(case.query, depth)]
    dense = [c for c, _ in collection.dense(case.query, depth)]
    fused = collection.search(case.query, limit=depth, licence_exclude=None)
    ranked = rerank(case.query, fused, depth)
    return {
        "bm25": _ranks(sparse, relevant),
        "dense": _ranks(dense, relevant),
        "fused": _ranks(fused, relevant),
        "reranked": _ranks(ranked, relevant),
        "_dense_only": [1] if (_ids(dense) & relevant) - _ids(sparse) else [],
    }


def evaluate(collection: Collection, cases: list[Case], depth: int = DEPTH) -> Report:
    report = Report(
        legs={leg: LegScore(leg) for leg in LEGS},
        cases=len(cases),
        corpus_size=len(collection),
    )
    indexed = set(collection._by_id) or None
    for case in cases:
        if indexed is not None:
            for doc, title in zip(case.relevant, case.titles or case.relevant):
                if doc not in indexed:
                    report.missing_labels.append(f"{doc[:60]} ({title[:50]})")
        got = run_case(collection, case, depth)
        kind = report.by_kind.setdefault(case.kind, {leg: LegScore(leg) for leg in LEGS})
        for leg in LEGS:
            report.legs[leg].observe(got[leg])
            kind[leg].observe(got[leg])
        if got["_dense_only"]:
            report.dense_lift += 1
            report.dense_lift_cases.append(case.query)
    return report


def load_gold(path: str | Path = GOLD) -> list[Case]:
    """The labelled questions, or an empty list where the file is absent."""
    import yaml

    p = Path(path)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: list[Case] = []
    for row in raw.get("questions", []):
        docs = row.get("relevant") or []
        out.append(
            Case(
                query=row["query"],
                relevant=tuple(d["doc_id"] for d in docs),
                kind=row.get("kind", "semantic"),
                note=row.get("note", ""),
                titles=tuple(d.get("title", "") for d in docs),
            )
        )
    return out


def report_for(
    corpus_path: str | Path = "data/corpus.db",
    gold_path: str | Path = GOLD,
    depth: int = DEPTH,
    embedder=None,
) -> Report:
    """The whole measurement, from paths, for the CLI and the tests.

    Indexes the WHOLE corpus, not `build_router`'s 120-day retrieval window: a
    label that aged out of the window would be scored as a retrieval failure
    when it is an indexing boundary, and the two are opposite problems.
    """
    from knowledge.corpus import Corpus
    from knowledge.graph.extractors.gdelt import entity_index
    from knowledge.retrieval.index import news_collection

    cases = load_gold(gold_path)
    with Corpus(corpus_path) as corpus:
        articles = corpus.articles(limit=100_000)
    col = news_collection(articles, entity_index(), embedder=embedder)
    return evaluate(col, cases, depth)
