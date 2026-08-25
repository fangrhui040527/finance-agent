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
from datetime import datetime, timezone

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
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
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

    def to_claim(self) -> Claim:
        return Claim(text=self.text, citations=list(self.citations))


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

    def retrieve(self, corpus: str, query: str, **kw):
        from knowledge.retrieval.pipeline import retrieve as _retrieve
        self._guard_tool("retrieve", {"corpus": corpus})
        return _retrieve(self.agent_id, corpus, query, self.ctx.router,
                         now=self.ctx.now, **kw)

    def emit(self, findings: list[Finding], chunk_lookup, confidence: float) -> Answer:
        """Findings -> verified Answer. Unsupported claims are dropped, not hedged."""
        return verify_answer([f.to_claim() for f in findings], chunk_lookup,
                             self.ctx.now, confidence)

    @abstractmethod
    def run(self, **kwargs) -> list[Finding]: ...


def cite(source: str, chunk_id: str, span: str, trust: TrustTier, as_of: datetime) -> Citation:
    return Citation(source=source, chunk_id=chunk_id, quoted_span=span,
                    trust=trust, as_of=as_of)
