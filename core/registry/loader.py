"""Capability registry. P17, the L1 growth surface.

docs/01 section 10: adding an agent, tool or market is an entry in
agents/registry.yaml plus an adapter class - never a change to the orchestrator.
This module is what makes that claim enforceable rather than aspirational.

The ratchet: nothing registers without an eval suite. A capability whose suite
is missing, empty, or has no negative cases is REFUSED at load time, because a
suite with no negatives measures enthusiasm, not skill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.contracts.provenance_marker import Author, ProvenanceMarker

#: Names that may never appear as a tool, whatever a YAML file says.
FORBIDDEN_TOOLS = frozenset({
    "place_order", "submit_order", "execute_trade", "buy", "sell", "cancel_order",
    "modify_order", "short", "close_position", "broker_connect", "send_order",
})

MIN_EVAL_CASES = 5
MIN_NEGATIVE_CASES = 2


class RegistryError(Exception):
    """A malformed registry is a startup failure, never a warning."""


class RatchetError(RegistryError):
    """Registered without a usable eval suite."""


@dataclass(frozen=True)
class AgentSpec:
    id: str
    layer: str
    tools: tuple[str, ...]
    knowledge: tuple[str, ...]
    tier_hint: str
    eval_suite: str


@dataclass(frozen=True)
class KnowledgeSpec:
    name: str
    created_by: Author
    managed: bool

    def marker(self, created_at) -> ProvenanceMarker:
        return ProvenanceMarker(created_by=self.created_by, created_at=created_at,
                                pinned=not self.managed and self.created_by is Author.AGENT)


@dataclass
class Registry:
    version: int
    agents: dict[str, AgentSpec] = field(default_factory=dict)
    knowledge: dict[str, KnowledgeSpec] = field(default_factory=dict)

    def agent(self, agent_id: str) -> AgentSpec:
        try:
            return self.agents[agent_id]
        except KeyError:
            raise RegistryError(
                f"{agent_id!r} is not registered. Adding it means an entry in "
                "agents/registry.yaml and an eval suite, not a code change here."
            ) from None

    def may_use(self, agent_id: str, tool: str) -> bool:
        return tool in self.agent(agent_id).tools

    def may_write(self, agent_id: str, store: str) -> bool:
        """docs/13: human-created knowledge is read-only to every agent, forever."""
        spec = self.knowledge.get(store)
        if spec is None:
            return False
        return spec.managed and spec.created_by is Author.AGENT


def load(path: str | Path, evals_root: str | Path | None = None,
         enforce_ratchet: bool = True) -> Registry:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict) or "version" not in raw:
        raise RegistryError(f"{path} is not a capability registry")

    reg = Registry(version=int(raw["version"]))
    root = Path(evals_root) if evals_root else Path(path).parent.parent

    for entry in raw.get("agents") or []:
        spec = _agent(entry)
        if spec.id in reg.agents:
            raise RegistryError(f"duplicate agent id {spec.id!r}")
        if enforce_ratchet:
            check_suite(root / spec.eval_suite, spec.id)
        reg.agents[spec.id] = spec

    for name, meta in (raw.get("knowledge") or {}).items():
        created_by = Author(str(meta.get("created_by", "human")))
        managed = bool(meta.get("managed", False))
        if managed and created_by is Author.HUMAN:
            raise RegistryError(
                f"{name}: managed=true with created_by=human. Human-authored knowledge "
                "is never agent-editable (docs/13 section 2.1)."
            )
        reg.knowledge[name] = KnowledgeSpec(name, created_by, managed)
    return reg


def _agent(entry: dict) -> AgentSpec:
    for required in ("id", "tools", "eval_suite"):
        if required not in entry:
            raise RegistryError(f"agent entry missing {required!r}: {entry}")
    tools = tuple(entry["tools"])
    banned = sorted(set(tools) & FORBIDDEN_TOOLS)
    if banned:
        raise RegistryError(
            f"{entry['id']} declares execution tools {banned}. This system has no broker "
            "connection by design and the registry will not create one."
        )
    return AgentSpec(
        id=str(entry["id"]),
        layer=str(entry.get("layer", "unknown")),
        tools=tools,
        knowledge=tuple(entry.get("knowledge") or []),
        tier_hint=str(entry.get("tier_hint", "balanced")),
        eval_suite=str(entry["eval_suite"]),
    )


def check_suite(path: Path, agent_id: str) -> dict:
    """The ratchet. Refuses to register a capability that cannot be measured."""
    if not path.exists():
        raise RatchetError(
            f"{agent_id} has no eval suite at {path}. Nothing registers without one "
            "(docs/07 P17): a capability you cannot measure is a capability you cannot "
            "safely change later."
        )
    suite = yaml.safe_load(path.read_text()) or {}
    cases = suite.get("cases") or []
    if len(cases) < MIN_EVAL_CASES:
        raise RatchetError(
            f"{agent_id}: eval suite has {len(cases)} cases, minimum is {MIN_EVAL_CASES}"
        )
    negatives = [c for c in cases if c.get("expect") in ("refuse", "no_lesson", "not_significant",
                                                         "market_driven", "no_position",
                                                         "no_identified_catalyst", "no_view")
                 or c.get("negative") is True]
    if len(negatives) < MIN_NEGATIVE_CASES:
        raise RatchetError(
            f"{agent_id}: only {len(negatives)} negative cases. A suite where every case "
            f"expects an answer measures enthusiasm, not skill; minimum is "
            f"{MIN_NEGATIVE_CASES} (docs/03 section 8)."
        )
    return suite


@dataclass(frozen=True)
class EvalResult:
    agent_id: str
    passed: int
    failed: int
    near_miss_failed: int
    details: tuple[tuple[str, bool, str], ...] = ()

    @property
    def total(self) -> int:
        return self.passed + self.failed

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def ok(self, threshold: float = 0.8) -> bool:
        """Near-miss failures are disqualifying regardless of the headline rate.
        docs/03 section 8: the near-misses are the whole test."""
        return self.rate >= threshold and self.near_miss_failed == 0


def run_suite(path: Path, agent_id: str, runner) -> EvalResult:
    """runner(case) -> str. Compared against case['expect']."""
    suite = check_suite(path, agent_id)
    passed = failed = near_miss_failed = 0
    details: list[tuple[str, bool, str]] = []
    for case in suite["cases"]:
        expect = case.get("expect")
        try:
            got = runner(case)
        except Exception as e:                    # a crash is a failure, not an error
            got = f"error: {type(e).__name__}: {e}"
        hit = got == expect
        if hit:
            passed += 1
        else:
            failed += 1
            if case.get("near_miss"):
                near_miss_failed += 1
        details.append((str(case.get("name", "?")), hit, f"expected {expect!r}, got {got!r}"))
    return EvalResult(agent_id, passed, failed, near_miss_failed, tuple(details))
