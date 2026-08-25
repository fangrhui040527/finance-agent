"""The five-rail chain.

docs/05 section 8: every request passes input -> retrieval -> tool -> output ->
publication. There is no debug path, no admin path, no quick-look path that skips
them. tests/test_guardrail_chain.py asserts that structurally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from core.guardrails.policy import (
    Action,
    Decision,
    PolicyEngine,
    PolicyResult,
    PolicyViolation,
    Rail,
)

RAIL_ORDER: tuple[Rail, ...] = (
    Rail.INPUT,
    Rail.RETRIEVAL,
    Rail.TOOL,
    Rail.OUTPUT,
    Rail.PUBLICATION,
)


@dataclass
class ChainResult:
    passed: bool
    results: list[PolicyResult] = field(default_factory=list)
    refusal_reason: str | None = None


class GuardrailChain:
    """The only way in. Every rail runs, in order, on every request."""

    def __init__(self, engine: PolicyEngine) -> None:
        self.engine = engine

    def run(
        self,
        agent: str,
        stages: dict[Rail, list[tuple[str, dict[str, Any]]]],
        on_refusal: Callable[[str], None] | None = None,
    ) -> ChainResult:
        results: list[PolicyResult] = []
        for rail in RAIL_ORDER:
            for name, payload in stages.get(rail, []):
                action = Action(name=name, rail=rail, agent=agent, payload=payload)
                try:
                    results.append(self.engine.enforce(action))
                except PolicyViolation as exc:
                    if on_refusal:
                        on_refusal(exc.result.reason)
                    return ChainResult(False, results, exc.result.reason)
        return ChainResult(True, results)

    def rails_covered(self) -> set[Rail]:
        """Which rails have at least one rule. Used by the P0 completeness test."""
        covered: set[Rail] = set()
        for rule in self.engine.rules:
            covered.update(rule.rails)
        return covered
