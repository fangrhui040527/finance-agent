"""Agent base.

docs/01 section 3: evidence agents never talk to each other. They talk to their
own knowledge and emit typed facts. That is what stops the multi-agent failure
where two agents synthesise a claim neither's evidence supports.

Every agent here is thin. The engines do the work; the agent owns a collection,
a tool allowlist and an output contract, and cannot reach outside them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

from core.contracts.answer import Answer, Citation, Claim, TrustTier, verify_answer
from core.guardrails.policy import Action, PolicyEngine, Rail
from core.llm.client import InferenceClient
from core.llm.tiers import TaskClass
from knowledge.retrieval.pipeline import Router


@dataclass
class AgentContext:
    """Everything an agent is allowed to reach. Nothing else is in scope."""

    router: Router
    engine: PolicyEngine
    llm: InferenceClient | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    holdings: set[str] = field(default_factory=set)
    watchlist: set[str] = field(default_factory=set)


@dataclass
class Finding:
    """One typed, cited fact an agent emits. Never free text."""

    agent: str
    kind: str
    text: str
    citations: list[Citation] = field(default_factory=list)
    numbers: dict[str, float] = field(default_factory=dict)
    as_of: datetime | None = None
    caveats: list[str] = field(default_factory=list)
    #: True when the citations form a chain rather than redundant support, so
    #: losing one breaks the claim. See Claim.all_citations_required.
    all_citations_required: bool = False

    def to_claim(self) -> Claim:
        return Claim(
            text=self.text,
            citations=list(self.citations),
            all_citations_required=self.all_citations_required,
        )


class Agent(ABC):
    """id, owned collections, allowed tools, and one job."""

    agent_id: str
    collections: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    tier: TaskClass = TaskClass.ADHOC_QUERY

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    def _guard_tool(self, name: str, payload: dict | None = None) -> None:
        self.ctx.engine.enforce(
            Action(name=name, rail=Rail.TOOL, agent=self.agent_id, payload=payload or {})
        )

    def traced(self, method: str, **inputs):
        """Wrap one agent call so its inputs, outputs and timing are recorded.

        Findings are what agents hand each other - A9's decomposition becomes
        A10's evidence, which becomes A11's target. Tracing the boundary is how
        you answer "where did this claim come from", which is the question the
        whole evidence contract exists to make answerable.
        """
        from core.trace import span

        return span(
            f"{self.agent_id}.{method}",
            kind="agent",
            agent=self.agent_id,
            method=method,
            tier=self.tier.value,
            collections=list(self.collections),
            **inputs,
        )

    def retrieve(self, corpus: str, query: str, **kw):
        from core.trace import emit, is_tracing
        from knowledge.retrieval.pipeline import quarantine
        from knowledge.retrieval.pipeline import retrieve as _retrieve

        # Two rails, and they are not the same rail. The TOOL one asks whether
        # this agent may read this corpus at all; the RETRIEVAL one reads what
        # came back. Guarding only the first is what let a poisoned article
        # through - the payload there carries the corpus NAME, so the injection
        # scan was handed an empty string on every retrieval this system ever
        # made.
        self._guard_tool("retrieve", {"corpus": corpus})
        result = _retrieve(self.agent_id, corpus, query, self.ctx.router, now=self.ctx.now, **kw)
        result = quarantine(self.ctx.engine, self.agent_id, corpus, result)
        if is_tracing():
            emit(
                "retrieval",
                self.agent_id,
                agent=self.agent_id,
                corpus=corpus,
                query=query,
                n_hits=len(getattr(result, "hits", []) or []),
                n_context=len(getattr(result, "context", []) or []),
                quarantined=getattr(result, "quarantined", {}) or {},
                grade=getattr(getattr(result, "grade", None), "grade", None)
                and result.grade.grade.value,
                relevance=getattr(getattr(result, "grade", None), "relevance", None),
                reason=getattr(getattr(result, "grade", None), "reason", None),
            )
        return result

    def emit(self, findings: list[Finding], chunk_lookup, confidence: float) -> Answer:
        """Findings -> verified Answer. Unsupported claims are dropped, not hedged."""
        answer = verify_answer(
            [f.to_claim() for f in findings], chunk_lookup, self.ctx.now, confidence
        )
        from core.trace import emit as _emit
        from core.trace import is_tracing

        if is_tracing():
            # Which claims died, and why. The mechanical citation check is the
            # only real output gate in the system, so what it drops is the
            # single most informative thing in a trace.
            _emit(
                "verification",
                self.agent_id,
                agent=self.agent_id,
                proposed=len(findings),
                kept=len(answer.claims),
                dropped=[{"text": c.text[:300], "why": c.dropped_reason} for c in answer.dropped],
                answered=answer.answered,
                confidence=answer.confidence,
            )
        return answer

    @abstractmethod
    def run(self, **kwargs) -> list[Finding]: ...


def cite(source: str, chunk_id: str, span: str, trust: TrustTier, as_of: datetime) -> Citation:
    return Citation(source=source, chunk_id=chunk_id, quoted_span=span, trust=trust, as_of=as_of)


def quote_span(text: str, limit: int = 160) -> str:
    """The first sentence of a chunk, bounded, as the span a citation quotes.

    `verify_claim` needs the span verbatim inside the chunk (whitespace
    normalised); a sentence boundary keeps the quote readable in a memo.
    """
    t = " ".join(text.split())
    cut = t.find(". ")
    span = t[: cut + 1] if 0 < cut < limit else t[:limit]
    return span if len(span) >= 8 else t[:limit]
