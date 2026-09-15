"""Retrieval: BM25 selects, `rerank` orders. The vector leg is NOT wired in.

docs/02 section 3 and docs/09 section 6 specify BM25 fused with a dense leg by
reciprocal rank fusion, on the argument that finance queries are half semantic
("margin compression risk") and half exact-token ("MYR", "Q3 FY25", "0011.KL")
and that fusing the two beats either alone. That was the design. It was built,
and then - on 2026-09-14, against the 23-question gold set and a 3,481-chunk
corpus - it was MEASURED, and the measurement did not agree:

    leg         r@10      what it is
    bm25       47.8%      exact-token search alone
    dense      39.1%      vector search alone
    fused      43.5%      the two merged by RRF - what this module shipped
    reranked   43.5%      fused, then reordered

The fusion did not beat its better half. It landed BETWEEN the two legs, which
is what rank fusion does when one leg carries no information the other lacks:
it pays a duplicate's overhead by spending ranks on it. The decisive number is
DENSE LIFT - questions where the vector leg found a relevant article BM25's own
top ten did not - and it was ZERO for both offline backends, the corpus-fitted
`DistributionalEmbedder` (0 of 23) and the `HashingEmbedder` before it. A leg
that lifts nothing is a second lexical search wearing a vector's clothes, and
this one was displacing real BM25 hits to seat its duplicates.

So the fusion is gone from `Collection.search` and BM25 selects alone. It went
in two steps by two hands: #68 switched it off behind a `FUSE_DENSE = False`
class flag, keeping the RRF code gated so a future embedder could flip it back;
this removes the flag and the code under it. The gate was the right first move
and a poor resting place - it left the RRF path unexercised by anything that
ships, the `fused` column in `evaluate.py` silently re-measuring `bm25`, and
that column's guard test comparing BM25 against itself. Fifteen lines of rank
fusion are cheaper to rewrite than to keep honest unused.

This is a RETRACTION OF A MEASUREMENT, not of the design: the docs' argument may well
be right about a real embedding model, and nothing here has tested one. What
was tested is the two backends that run without a key, and neither earns a
place in the path a reader's question actually takes.

The apparatus to reverse this is deliberately intact. `Collection.dense`,
`_index_dense` and every backend in `knowledge/retrieval/embedding.py` still
work and are still scored as their own leg by `knowledge/retrieval/evaluate.py`;
`ask.py retrieval --embedder api` and `.github/workflows/embedding-probe.yml`
run that comparison against a hosted model, which is the one variant the
verdict above does not cover. Bring the fusion back when, and only when, dense
lift on that leg is above zero. Until then production never embeds anything -
`Collection.embedder` is not even constructed until something asks for a vector.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from knowledge.chunking.parent_child import Chunk
from knowledge.retrieval.embedding import HashingEmbedder, default_embedder, tokenize

__all__ = [
    "BM25",
    "Collection",
    "EmbeddingBackend",
    "HashingEmbedder",
    "Hit",
    "cosine",
    "expand_to_parents",
    "rerank",
    "tokenize",
]


class EmbeddingBackend(Protocol):
    """What a `Collection` needs from a source of vectors.

    `fit` is optional and deliberately absent from the protocol: a backend that
    learns from the corpus declares it, one that does not never sees the call.
    `Collection` checks for it rather than requiring it, so a three-line test
    embedder stays three lines.
    """

    dimensions: int

    def embed(self, text: str) -> list[float]: ...


#: Dimensions kept per stored document vector. A vector's mass concentrates in
#: a few dimensions and the rest is noise being multiplied over the whole
#: collection on every question: dropping it left recall at ten and the semantic
#: family unchanged on the gold set, moved MRR by 0.006 - a fraction of one
#: question's rank, well inside the noise of a 24-question set - and made the
#: dense scan almost four times faster. At the 15,000 chunks a season of sweeps
#: puts in the retrieval window that is the difference between 290ms and 77ms
#: per question, which is the difference between a tool call that feels
#: instant and one that does not.
DOC_NONZEROS = 96


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _sparse(vec: list[float], keep: int = DOC_NONZEROS) -> list[tuple[int, float]]:
    """The `keep` largest components of a unit vector, renormalised."""
    if keep >= len(vec):
        return list(enumerate(vec))
    top = sorted(range(len(vec)), key=lambda i: -abs(vec[i]))[:keep]
    top.sort()
    norm = math.sqrt(sum(vec[i] * vec[i] for i in top)) or 1.0
    return [(i, vec[i] / norm) for i in top]


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    #: 1-based position in the BM25 list this hit was selected from, before the
    #: hard filters thinned it and before `rerank` reordered it. The only rank
    #: there is now; the `dense_rank` that sat beside it went with the fusion.
    sparse_rank: int | None = None


class BM25:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._docs: list[list[str]] = []
        self._chunks: list[Chunk] = []
        self._df: Counter[str] = Counter()
        self._avg_len = 0.0
        self._total_len = 0

    def __len__(self) -> int:
        return len(self._docs)

    def add(self, chunk: Chunk) -> None:
        toks = tokenize(chunk.text)
        self._docs.append(toks)
        self._chunks.append(chunk)
        for t in set(toks):
            self._df[t] += 1
        # A running total, not a re-sum: the old form made indexing a corpus
        # quadratic, which nobody noticed at 4 test chunks and everybody would
        # have at the 10,000 articles a season of sweeps produces.
        self._total_len += len(toks)
        self._avg_len = self._total_len / len(self._docs)

    def search(self, query: str, limit: int = 20) -> list[tuple[Chunk, float]]:
        n = len(self._docs)
        if not n:
            return []
        q = tokenize(query)
        scored: list[tuple[Chunk, float]] = []
        for doc, chunk in zip(self._docs, self._chunks):
            tf = Counter(doc)
            dl = len(doc) or 1
            s = 0.0
            for term in q:
                if term not in tf:
                    continue
                idf = math.log(1 + (n - self._df[term] + 0.5) / (self._df[term] + 0.5))
                num = tf[term] * (self.k1 + 1)
                den = tf[term] + self.k1 * (1 - self.b + self.b * dl / self._avg_len)
                s += idf * num / den
            if s > 0:
                scored.append((chunk, s))
        scored.sort(key=lambda x: -x[1])
        return scored[:limit]


class Collection:
    """One named corpus. An agent may query only the collections it owns.

    docs/02 section 1: no agent queries a global undifferentiated index.
    Heterogeneous corpora poison each other.
    """

    def __init__(self, name: str, embedder: EmbeddingBackend | None = None) -> None:
        self.name = name
        # Deferred, because `search` no longer embeds and `build_router`
        # registers twenty of these per process. Constructing a backend the
        # production path never reaches is work nobody asked for, and holding
        # one there would make "does retrieval embed?" unanswerable by
        # inspection. `self.embedder` builds the default on first touch.
        self._embedder = embedder
        self.bm25 = BM25()
        self._order: list[Chunk] = []
        self._vectors: list[tuple[Chunk, list[tuple[int, float]]]] | None = None
        self._by_id: dict[str, Chunk] = {}

    def add(self, chunk: Chunk) -> None:
        if chunk.corpus != self.name:
            raise ValueError(
                f"chunk belongs to corpus {chunk.corpus!r}, not collection {self.name!r}"
            )
        self.bm25.add(chunk)
        self._order.append(chunk)
        self._by_id[chunk.chunk_id] = chunk
        self._vectors = None  # a new document changes what the old ones mean

    @property
    def embedder(self) -> EmbeddingBackend:
        """The vector backend, built on first use. Only `dense` reaches it."""
        if self._embedder is None:
            self._embedder = default_embedder()
        return self._embedder

    def add_all(self, chunks: list[Chunk]) -> None:
        for c in chunks:
            self.add(c)

    def __len__(self) -> int:
        return len(self._by_id)

    def find_chunk(self, source: str, chunk_id: str) -> str | None:
        """The lookup post-hoc citation verification depends on."""
        c = self._by_id.get(chunk_id)
        return c.text if c else None

    def _index_dense(self) -> list[tuple[Chunk, list[tuple[int, float]]]]:
        """Vectors, built on first use and rebuilt whenever a document arrives.

        Deferred on purpose, for two reasons. An embedder that learns from the
        corpus cannot embed the first document until it has seen the last one,
        so an eager `add` would have had to fit on a corpus of one. And
        `build_router` registers twenty collections of which a given question
        queries one - embedding the other nineteen was work nobody asked for.
        """
        if self._vectors is None:
            fit = getattr(self.embedder, "fit", None)
            if callable(fit):
                fit([c.text for c in self._order])
            self._vectors = [(c, _sparse(self.embedder.embed(c.text))) for c in self._order]
        return self._vectors

    def dense(self, query: str, limit: int = 20) -> list[tuple[Chunk, float]]:
        vectors = self._index_dense()
        qv = self.embedder.embed(query)
        scored = [(c, sum(qv[i] * x for i, x in v)) for c, v in vectors]
        scored.sort(key=lambda x: -x[1])
        return [(c, s) for c, s in scored[:limit] if s > 0]

    def search(
        self,
        query: str,
        limit: int = 8,
        max_age: timedelta | None = None,
        now: datetime | None = None,
        entity: str | None = None,
        licence_exclude: str | None = "link_only",
    ) -> list[Hit]:
        """Candidates for `rerank`, chosen by BM25 and cut by the hard filters.

        No vectors. See the module docstring for the measurement that removed
        them; the short version is that the dense leg found nothing BM25 had
        missed on any question in the gold set, so fusing it in only cost real
        hits their seats.

        Still four times `limit` from BM25, and for the reason that predates
        the fusion: the filters below run AFTER selection, so a narrow pool
        would let one stale or link-only document spend a slot the caller asked
        to have filled. Over-fetching is what keeps `limit` a promise about
        results rather than about candidates.
        """
        hits: list[Hit] = []
        for i, (chunk, score) in enumerate(self.bm25.search(query, limit * 4)):
            # Hard filters, not rerank hints (docs/02 section 3 property 2).
            if max_age is not None and chunk.as_of is not None:
                if (now or datetime.now(chunk.as_of.tzinfo)) - chunk.as_of > max_age:
                    continue
            if entity and entity not in chunk.metadata.get("instruments", [entity]):
                continue
            if licence_exclude and chunk.metadata.get("licence") == licence_exclude:
                continue
            hits.append(Hit(chunk, score, i + 1))
            if len(hits) == limit:
                break
        return hits


def rerank(query: str, hits: list[Hit], top_k: int | None = None) -> list[Hit]:
    """Local reranker: lexical overlap plus an exact-phrase bonus.

    docs/08 section 7: run the reranker locally. A hosted reranker at this scale
    buys nothing a local cross-encoder does not.

    A richer version was built and MEASURED AWAY, which is worth recording so
    nobody rebuilds it. Blending in the query-to-document cosine and the
    fusion's own RRF score looked obviously better - the stage runs last, so it
    decides the order a reader sees, and scoring a paraphrased question on word
    overlap alone scores it on roughly nothing. On
    `knowledge/retrieval/data/retrieval_gold.yaml` it was worse at every weight
    tried, monotonically: MRR 0.495 at zero semantic weight, 0.478 at 0.3, 0.474
    at 0.6, 0.466 at 2.5, with the fusion term making no difference at any
    setting.

    The reason first written down here was double counting: these hits were the
    FUSED list, so the dense signal had already been spent selecting them and
    spending it again on their order added noise without information. THAT
    PREMISE IS GONE - the fusion was removed on 2026-09-14 and `search` now
    hands this function a BM25 list that no vector has touched. The result
    survives the premise, on the simpler reading the removal itself rests on:
    dense lift is zero on this corpus, so the cosine carries no information to
    double-count in the first place. It was noise at the selection stage and it
    is noise here.

    Which is why this stays lexical. If a hosted embedder ever earns the dense
    leg back (module docstring), re-run this weight sweep before assuming the
    answer: both findings above were measured against backends that had nothing
    to say, and neither is evidence about one that does.
    """
    q = set(tokenize(query))
    ql = query.lower()
    out = []
    for h in hits:
        toks = set(tokenize(h.chunk.text))
        overlap = len(q & toks) / (len(q) or 1)
        phrase = 0.25 if ql in h.chunk.text.lower() else 0.0
        out.append(Hit(h.chunk, overlap + phrase, h.sparse_rank))
    out.sort(key=lambda h: -h.score)
    return out[: top_k or len(out)]


def expand_to_parents(hits: list[Hit], parents: dict[str, Chunk]) -> list[Chunk]:
    """Retrieve on children, generate with the parent (docs/06 section 5.1)."""
    seen: set[str] = set()
    out: list[Chunk] = []
    for h in hits:
        pid = h.chunk.parent_id
        target = parents.get(pid) if pid else h.chunk
        if target is None:
            target = h.chunk
        if target.chunk_id not in seen:
            seen.add(target.chunk_id)
            out.append(target)
    return out
