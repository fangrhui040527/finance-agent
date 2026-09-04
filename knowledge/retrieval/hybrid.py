"""Hybrid retrieval: BM25 + dense, fused with RRF, then reranked.

docs/02 section 3. Dense-only retrieval loses half the job: finance queries are
half semantic ("margin compression risk") and half exact-token ("MYR", "Q3 FY25",
"0011.KL"). docs/09 section 6 records that BM25 fused with dense via reciprocal
rank fusion beats either alone, and a cross-encoder rerank adds meaningfully on
hard sets.

The embedding backend here is a deterministic hashing projection so the whole
stack is testable with no network and no keys. Swap it for a real embedder at the
EmbeddingBackend seam; nothing else changes.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from knowledge.chunking.parent_child import Chunk

TOKEN = re.compile(r"[a-z0-9][a-z0-9.\-]*", re.IGNORECASE)
RRF_K = 60


def tokenize(text: str) -> list[str]:
    """Keeps dots and hyphens so `0011.KL` and `Q3-FY25` survive as one token."""
    return [t.lower() for t in TOKEN.findall(text)]


class EmbeddingBackend(Protocol):
    dimensions: int

    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Deterministic, offline, dependency-free. Good enough for exact-ish recall
    and for testing the plumbing; not a substitute for a real embedder."""

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for tok in tokenize(text):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % self.dimensions] += 1.0 if (h >> 8) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    sparse_rank: int | None = None
    dense_rank: int | None = None


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
        self.embedder = embedder or HashingEmbedder()
        self.bm25 = BM25()
        self._vectors: list[tuple[Chunk, list[float]]] = []
        self._by_id: dict[str, Chunk] = {}

    def add(self, chunk: Chunk) -> None:
        if chunk.corpus != self.name:
            raise ValueError(
                f"chunk belongs to corpus {chunk.corpus!r}, not collection {self.name!r}"
            )
        self.bm25.add(chunk)
        self._vectors.append((chunk, self.embedder.embed(chunk.text)))
        self._by_id[chunk.chunk_id] = chunk

    def add_all(self, chunks: list[Chunk]) -> None:
        for c in chunks:
            self.add(c)

    def __len__(self) -> int:
        return len(self._by_id)

    def find_chunk(self, source: str, chunk_id: str) -> str | None:
        """The lookup post-hoc citation verification depends on."""
        c = self._by_id.get(chunk_id)
        return c.text if c else None

    def dense(self, query: str, limit: int = 20) -> list[tuple[Chunk, float]]:
        qv = self.embedder.embed(query)
        scored = [(c, cosine(qv, v)) for c, v in self._vectors]
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
        sparse = self.bm25.search(query, limit * 4)
        dns = self.dense(query, limit * 4)

        ranks: dict[str, dict] = {}
        for i, (c, _) in enumerate(sparse):
            ranks.setdefault(c.chunk_id, {"chunk": c})["sparse"] = i + 1
        for i, (c, _) in enumerate(dns):
            ranks.setdefault(c.chunk_id, {"chunk": c})["dense"] = i + 1

        fused: list[Hit] = []
        for _cid, r in ranks.items():
            chunk = r["chunk"]
            # Hard filters, not rerank hints (docs/02 section 3 property 2).
            if max_age is not None and chunk.as_of is not None:
                if (now or datetime.now(chunk.as_of.tzinfo)) - chunk.as_of > max_age:
                    continue
            if entity and entity not in chunk.metadata.get("instruments", [entity]):
                continue
            if licence_exclude and chunk.metadata.get("licence") == licence_exclude:
                continue
            score = 0.0
            if "sparse" in r:
                score += 1.0 / (RRF_K + r["sparse"])
            if "dense" in r:
                score += 1.0 / (RRF_K + r["dense"])
            fused.append(Hit(chunk, score, r.get("sparse"), r.get("dense")))

        fused.sort(key=lambda h: -h.score)
        return fused[:limit]


def rerank(query: str, hits: list[Hit], top_k: int | None = None) -> list[Hit]:
    """Stand-in cross-encoder: lexical overlap plus exact-phrase bonus.

    docs/08 section 7: run the reranker locally. A hosted reranker at this scale
    buys nothing a local cross-encoder does not.
    """
    q = set(tokenize(query))
    ql = query.lower()
    out = []
    for h in hits:
        toks = set(tokenize(h.chunk.text))
        overlap = len(q & toks) / (len(q) or 1)
        phrase = 0.25 if ql in h.chunk.text.lower() else 0.0
        out.append(Hit(h.chunk, overlap + phrase, h.sparse_rank, h.dense_rank))
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
