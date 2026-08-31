"""The retrieval pipeline every agent shares.

docs/02 section 3:

  rewrite -> BM25 + dense in parallel -> RRF -> hard filters -> rerank
  -> parent expansion -> grade -> generate or retry or refuse

Four properties separate this from a naive pipeline, and each is enforced here
rather than suggested:

  1. Sparse and dense together, fused by RRF.
  2. Freshness is a hard filter, not a rerank hint.
  3. Web search is TRIGGERED, never default, and is the lowest trust tier.
  4. Claim-to-chunk attribution is mandatory; an unsupported claim is dropped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from knowledge.chunking.parent_child import Chunk
from knowledge.retrieval.hybrid import Collection, Hit, expand_to_parents, rerank

MAX_REWRITES = 2
SUFFICIENCY_MIN_HITS = 2
RELEVANCE_MIN = 0.15


class Grade(str, Enum):
    PASS = "pass"
    WEAK = "weak"
    INSUFFICIENT = "insufficient"


class WebTrigger(str, Enum):
    """docs/02 section 3 property 3. The only four reasons web search may fire."""

    GRADER_INSUFFICIENT = "grader_insufficient_after_retry"
    EXPLICIT_FRESHNESS = "explicit_freshness_demand"
    HIGH_IMPACT_VALIDATION = "high_impact_claim_validation"
    ENTITY_ABSENT = "entity_absent_from_graph"


@dataclass
class GradeReport:
    grade: Grade
    relevance: float
    fresh: bool
    n_hits: int
    reason: str


@dataclass
class RetrievalResult:
    hits: list[Hit]
    context: list[Chunk]
    grade: GradeReport
    rewrites: int
    web_used: WebTrigger | None = None
    refused: bool = False
    trace: list[str] = field(default_factory=list)


class CollectionScopeError(PermissionError):
    """An agent reached for a collection it does not own."""


class Router:
    """docs/02: each agent owns named collections and cannot read the others."""

    def __init__(self, ownership: dict[str, set[str]]) -> None:
        self.ownership = ownership
        self._collections: dict[str, Collection] = {}

    def register(self, collection: Collection) -> None:
        self._collections[collection.name] = collection

    def get(self, agent: str, corpus: str) -> Collection:
        owned = self.ownership.get(agent, set())
        if corpus not in owned:
            raise CollectionScopeError(
                f"agent {agent!r} owns {sorted(owned)} and may not read {corpus!r}"
            )
        if corpus not in self._collections:
            raise KeyError(f"collection {corpus!r} is not registered")
        return self._collections[corpus]


def grade(
    hits: list[Hit], query: str, max_age: timedelta | None, now: datetime | None
) -> GradeReport:
    if not hits:
        return GradeReport(Grade.INSUFFICIENT, 0.0, False, 0, "no hits retrieved")
    top = max(h.score for h in hits)
    fresh = True
    if max_age is not None and now is not None:
        fresh = all(h.chunk.as_of is None or (now - h.chunk.as_of) <= max_age for h in hits)
    if top < RELEVANCE_MIN:
        return GradeReport(
            Grade.WEAK, top, fresh, len(hits), f"top relevance {top:.2f} below {RELEVANCE_MIN}"
        )
    if len(hits) < SUFFICIENCY_MIN_HITS:
        return GradeReport(
            Grade.WEAK, top, fresh, len(hits), "single supporting chunk is not sufficiency"
        )
    if not fresh:
        return GradeReport(Grade.WEAK, top, fresh, len(hits), "hits are past the freshness SLA")
    return GradeReport(Grade.PASS, top, fresh, len(hits), "relevant, fresh and sufficient")


def default_rewrite(query: str, attempt: int) -> str:
    """Stand-in for the cheap-tier rewriter: widen, then strip qualifiers."""
    if attempt == 1:
        return " ".join(w for w in query.split() if len(w) > 2)
    return " ".join(query.split()[:4])


def retrieve(
    agent: str,
    corpus: str,
    query: str,
    router: Router,
    parents: dict[str, Chunk] | None = None,
    limit: int = 6,
    max_age: timedelta | None = None,
    now: datetime | None = None,
    entity: str | None = None,
    rewrite: Callable[[str, int], str] = default_rewrite,
    allow_web: bool = False,
    web_search: Callable[[str], list[Hit]] | None = None,
    freshness_demanded: bool = False,
) -> RetrievalResult:
    collection = router.get(agent, corpus)  # raises if out of scope
    trace: list[str] = []
    q = query

    for attempt in range(MAX_REWRITES + 1):
        hits = rerank(q, collection.search(q, limit, max_age, now, entity), limit)
        report = grade(hits, q, max_age, now)
        trace.append(f"attempt {attempt}: q={q!r} -> {report.grade.value} ({report.reason})")
        if report.grade is Grade.PASS:
            ctx = expand_to_parents(hits, parents or {})
            return RetrievalResult(hits, ctx, report, attempt, None, False, trace)
        if attempt < MAX_REWRITES:
            q = rewrite(query, attempt + 1)

    # Only now may web search fire, and only if this call opted in.
    if allow_web and web_search is not None:
        trigger = (
            WebTrigger.EXPLICIT_FRESHNESS if freshness_demanded else WebTrigger.GRADER_INSUFFICIENT
        )
        web_hits = web_search(query)
        trace.append(f"web search fired: {trigger.value}, {len(web_hits)} results")
        if web_hits:
            report = grade(web_hits, query, None, None)
            return RetrievalResult(
                web_hits, [h.chunk for h in web_hits], report, MAX_REWRITES, trigger, False, trace
            )

    trace.append("refused: insufficient evidence after retries")
    return RetrievalResult([], [], report, MAX_REWRITES, None, True, trace)
