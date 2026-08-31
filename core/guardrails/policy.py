"""Deterministic policy engine.

Adapted from advanced_ai_agents/single_agent_apps/ai_agent_governance at
awesome-llm-apps 11a4bc33 (Apache-2.0), per docs/12 section 2.1.

The two additions docs/12 specifies:
  - NoExecutionPolicy: a rail that can never be satisfied, so "there is no
    execution tool" is enforced rather than commented (docs/05 section 8.1).
  - StalenessPolicy: reads as_of off every fact against its corpus SLA
    (docs/02 section 4).

Constraints live in code, not in prompts. docs/05 opens by saying a guardrail
written as prompt text is a suggestion.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class Rail(str, Enum):
    """docs/05 section 8. Every request passes all five, in order."""

    INPUT = "input"
    RETRIEVAL = "retrieval"
    TOOL = "tool"
    OUTPUT = "output"
    PUBLICATION = "publication"


@dataclass(frozen=True)
class Action:
    name: str
    rail: Rail
    agent: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    policy_name: str
    reason: str
    terminal: bool = True


@dataclass(frozen=True)
class AuditEntry:
    """The provenance ledger's action half (docs/12 section 2.1)."""

    at: datetime
    action: Action
    decision: Decision
    policy_name: str
    reason: str


class PolicyViolation(Exception):
    """Raised, never warned. A breaching action cannot proceed."""

    def __init__(self, result: PolicyResult, action: Action) -> None:
        super().__init__(f"{result.policy_name} denied {action.name}: {result.reason}")
        self.result = result
        self.action = action


class PolicyRule:
    name = "policy"
    rails: tuple[Rail, ...] = ()

    def evaluate(self, action: Action) -> PolicyResult | None:
        raise NotImplementedError


class NoExecutionPolicy(PolicyRule):
    """docs/05 section 10: never place, route, or schedule an order.

    Deny-by-name, so the guarantee survives someone adding a broker client later.
    """

    name = "no_execution"
    rails = (Rail.TOOL,)
    FORBIDDEN = frozenset(
        {
            "place_order",
            "submit_order",
            "route_order",
            "schedule_order",
            "cancel_order",
            "modify_order",
            "buy",
            "sell",
            "execute_trade",
            "broker_connect",
            "broker_login",
        }
    )

    def evaluate(self, action: Action) -> PolicyResult | None:
        if action.rail is not Rail.TOOL:
            return None
        if action.name.lower() in self.FORBIDDEN:
            return PolicyResult(
                Decision.DENY,
                self.name,
                "the system is a one-way door; it has no execution capability",
            )
        return None


class ToolAllowlistPolicy(PolicyRule):
    """docs/02 section 5: each agent may call only its own tools."""

    name = "tool_allowlist"
    rails = (Rail.TOOL,)

    def __init__(self, allowed: dict[str, set[str]]) -> None:
        self.allowed = allowed

    def evaluate(self, action: Action) -> PolicyResult | None:
        if action.rail is not Rail.TOOL:
            return None
        permitted = self.allowed.get(action.agent)
        if permitted is None:
            return PolicyResult(
                Decision.DENY, self.name, f"agent {action.agent!r} has no registered toolset"
            )
        if action.name not in permitted:
            return PolicyResult(
                Decision.DENY,
                self.name,
                f"agent {action.agent!r} may not call {action.name!r}",
            )
        return PolicyResult(Decision.ALLOW, self.name, "tool in allowlist", terminal=False)


class TenantIsolationPolicy(PolicyRule):
    """docs/05 section 8.1 rule 5: personal data never leaves its boundary."""

    name = "tenant_isolation"
    rails = (Rail.RETRIEVAL, Rail.TOOL)
    PERSONAL_KEYS = frozenset({"holdings", "ledger", "goals", "positions", "net_worth", "salary"})
    EGRESS_TOOLS = frozenset({"web_search", "web_fetch", "http_get", "http_post"})

    def evaluate(self, action: Action) -> PolicyResult | None:
        if action.name in self.EGRESS_TOOLS:
            leaked = self.PERSONAL_KEYS & set(action.payload)
            if leaked:
                return PolicyResult(
                    Decision.DENY,
                    self.name,
                    f"outbound query carries personal data: {sorted(leaked)}",
                )
        if (
            action.rail is Rail.RETRIEVAL
            and action.payload.get("shared_index")
            and action.payload.get("tenant_scoped")
        ):
            return PolicyResult(
                Decision.DENY, self.name, "tenant data may not enter a shared index"
            )
        return None


class LicenceFilterPolicy(PolicyRule):
    """docs/02 A15: a link_only chunk surfaces title and URL, never its body.
    Hard CI gate, not a metric."""

    name = "licence_filter"
    rails = (Rail.RETRIEVAL, Rail.OUTPUT)

    def evaluate(self, action: Action) -> PolicyResult | None:
        if action.payload.get("licence") == "link_only" and action.payload.get("emits_body"):
            return PolicyResult(
                Decision.DENY, self.name, "link_only source may not have its body emitted"
            )
        return None


class StalenessPolicy(PolicyRule):
    """docs/02 section 4: past its SLA, an agent says so or refuses."""

    name = "staleness"
    rails = (Rail.RETRIEVAL, Rail.OUTPUT)

    def __init__(self, sla: dict[str, timedelta], now=None) -> None:
        self.sla = sla
        self._now = now or (lambda: datetime.now(UTC))

    def evaluate(self, action: Action) -> PolicyResult | None:
        corpus = action.payload.get("corpus")
        as_of = action.payload.get("as_of")
        if corpus is None or as_of is None:
            return None
        limit = self.sla.get(corpus)
        if limit is None:
            return None
        age = self._now() - as_of
        if age > limit * 3:
            return PolicyResult(
                Decision.DENY,
                self.name,
                f"{corpus} is {age} old, beyond 3x its {limit} SLA",
            )
        if age > limit:
            return PolicyResult(
                Decision.REQUIRE_APPROVAL,
                self.name,
                f"{corpus} is stale ({age} > {limit}); disclose or refuse",
            )
        return None


class AdviceLanguagePolicy(PolicyRule):
    """docs/05 section 8.1 rule 2: bands, never verbs."""

    name = "advice_language"
    rails = (Rail.OUTPUT,)
    BANNED = (
        "you should buy",
        "you should sell",
        "i recommend",
        "i suggest you buy",
        "strong buy",
        "strong sell",
        "must buy",
        "must sell",
        "guaranteed return",
    )

    def evaluate(self, action: Action) -> PolicyResult | None:
        if action.rail is not Rail.OUTPUT:
            return None
        text = str(action.payload.get("text", "")).lower()
        for phrase in self.BANNED:
            if phrase in text:
                return PolicyResult(
                    Decision.DENY, self.name, f"advice language detected: {phrase!r}"
                )
        return None


class RateLimitPolicy(PolicyRule):
    """docs/08 section 8: per-user daily budget; truncation is disclosed, never silent."""

    name = "rate_limit"
    rails = (Rail.TOOL,)

    def __init__(self, max_calls: int, window_seconds: float, clock=time.monotonic) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._clock = clock
        self._hits: list[float] = []

    def evaluate(self, action: Action) -> PolicyResult | None:
        now = self._clock()
        self._hits = [t for t in self._hits if now - t < self.window]
        if len(self._hits) >= self.max_calls:
            return PolicyResult(
                Decision.DENY,
                self.name,
                f"rate limit {self.max_calls}/{self.window}s exhausted",
            )
        self._hits.append(now)
        return None


class PolicyEngine:
    def __init__(self, rules: list[PolicyRule] | None = None) -> None:
        self.rules: list[PolicyRule] = list(rules or [])
        self.audit_log: list[AuditEntry] = []

    def add_rule(self, rule: PolicyRule) -> PolicyEngine:
        self.rules.append(rule)
        return self

    def evaluate(self, action: Action) -> PolicyResult:
        verdict = PolicyResult(Decision.ALLOW, "default_allow", "no rule objected")
        for rule in self.rules:
            result = rule.evaluate(action)
            if result is None:
                continue
            if result.decision is Decision.DENY and result.terminal:
                verdict = result
                break
            if result.decision is Decision.REQUIRE_APPROVAL:
                verdict = result
        self.audit_log.append(
            AuditEntry(
                at=datetime.now(UTC),
                action=action,
                decision=verdict.decision,
                policy_name=verdict.policy_name,
                reason=verdict.reason,
            )
        )
        return verdict

    def enforce(self, action: Action) -> PolicyResult:
        """Evaluate and raise on denial. Callers get an exception, not a warning."""
        result = self.evaluate(action)
        from core.trace import emit, is_tracing

        if is_tracing():
            # Allowed decisions are traced too, not just denials. "Which rail let
            # this through" is as much a debugging question as "what blocked it",
            # and a log of only refusals cannot answer it.
            emit(
                "denied" if result.decision is Decision.DENY else "allowed",
                action.name,
                agent=action.agent,
                rail=action.rail.value,
                action=action.name,
                decision=result.decision.value,
                rule=result.policy_name,
                reason=result.reason,
                payload={k: str(v)[:200] for k, v in (action.payload or {}).items()},
            )
        if result.decision is Decision.DENY:
            raise PolicyViolation(result, action)
        return result
