"""A0, the supervisor.

docs/01 section 4. The supervisor plans, routes, budgets and refuses. It does
NOT analyse - the moment an orchestrator starts forming views, the evidence
seam is gone and nobody can tell which agent's knowledge backed a claim.

Its most valuable output is a refusal. A question the evidence layer cannot
answer must come back as "I cannot answer that and here is why", never as a
plausible paragraph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from agents.base import Agent, Finding
from core.contracts.money import Money
from core.guardrails.policy import Action, Rail
from core.llm.tiers import ROUTING, TaskClass, Tier


class Intent(str, Enum):
    WHY_IT_MOVED = "why_it_moved"
    SHOULD_I_BUY = "should_i_buy"
    WHAT_DO_I_OWN = "what_do_i_own"
    EXPLAIN_CONCEPT = "explain_concept"
    SCREEN = "screen"
    DAILY_BRIEF = "daily_brief"
    RISK_CHECK = "risk_check"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True)
class Refusal:
    reason: str
    what_would_help: str


@dataclass
class Plan:
    intent: Intent
    instrument_ids: tuple[str, ...]
    agents: tuple[str, ...]
    task_classes: tuple[TaskClass, ...]
    estimated_cost: Money
    refusal: Refusal | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.refusal is None


#: Which evidence agents each intent needs. Fixed, so a plan is auditable.
PLAYBOOK: dict[Intent, tuple[str, ...]] = {
    Intent.WHY_IT_MOVED: (
        "a3_price_technical",
        "a4_news_narrative",
        "a5_catalyst_events",
        "a6_macro_regime",
        "a9_attribution",
    ),
    Intent.SHOULD_I_BUY: (
        "a1_fundamentals",
        "a2_valuation",
        "a3_price_technical",
        "a4_news_narrative",
        "a5_catalyst_events",
        "a6_macro_regime",
        "a7_sector_technology",
        "a8_ownership_flow",
        "a10_thesis",
        "a11_red_team",
        "a12_portfolio_risk",
        "a13_sizing",
    ),
    Intent.WHAT_DO_I_OWN: ("a12_portfolio_risk", "a9_attribution"),
    Intent.EXPLAIN_CONCEPT: ("a14_teacher",),
    # Nothing. a1 and a2 are per-INSTRUMENT agents and a screen has no
    # instrument, so routing here ran two agents against nothing at all.
    # The boundary is stated in run() instead.
    Intent.SCREEN: (),
    Intent.DAILY_BRIEF: ("a4_news_narrative", "a5_catalyst_events", "a12_portfolio_risk"),
    Intent.RISK_CHECK: ("a12_portfolio_risk",),
    Intent.OUT_OF_SCOPE: (),
}

#: Cheap, deterministic first pass. The model is only asked when this is unsure.
_PATTERNS: tuple[tuple[Intent, str], ...] = (
    (
        Intent.WHY_IT_MOVED,
        r"\bwhy\b.*\b(up|down|fall|fell|falling|rose|rise|rising|drop|dropp"
        r"|jump|crash|rall|mov|surg|slump|slid|slump|tank|spike|plunge|gain|los)",
    ),
    (Intent.WHY_IT_MOVED, r"\b(what happened|what's going on)\b"),
    # Ahead of SHOULD_I_BUY on purpose: "give me a list of banks worth buying"
    # is a request for a LIST, and classified as should-i-buy it refused with
    # "name the company" - which does not answer what was asked.
    (Intent.SCREEN, r"\b(screen|find (me )?(stocks|companies)|list of)\b"),
    (Intent.SHOULD_I_BUY, r"\b(should i|worth) (buy|buying|add|accumulat|invest|enter)"),
    (Intent.SHOULD_I_BUY, r"\b(good|bad) (buy|entry|investment)\b"),
    (Intent.WHAT_DO_I_OWN, r"\b(my (portfolio|holdings|position)|what do i own|how am i doing)\b"),
    (Intent.RISK_CHECK, r"\b(too concentrat|risk check|am i (over)?exposed|diversif)"),
    (Intent.DAILY_BRIEF, r"\b(brief|what should i (know|watch)|morning)\b"),
    (Intent.EXPLAIN_CONCEPT, r"\b(what (is|are|does)|explain|how do(es)? .* work|teach me)\b"),
)

#: Things this system will not do, whatever the phrasing.
_OUT_OF_SCOPE: tuple[tuple[str, str, str], ...] = (
    (
        r"\b(buy|sell|short|execut|place|submit).{0,20}\b(order|trade|shares|lots?)\b.*\bfor me\b",
        "This system cannot place orders. It has no broker connection by design.",
        "Ask for the analysis instead; you place the order yourself.",
    ),
    (
        r"\b(guarantee|sure thing|can't lose|risk[- ]free|100% )",
        "No outcome in markets is guaranteed and this system will not imply one.",
        "Ask for the base rate and the range of outcomes.",
    ),
    (
        # Every verb a point forecast hides behind. The live QA pass found
        # "what price will it hit next month" routed to the teacher: this
        # pattern only knew "be worth".
        r"\b(price target|exactly how much|what will .* be (worth|at)"
        r"|what price will .{0,40}\b(hit|reach|be|close)"
        r"|how (high|low|far) (will|would|can) .{0,40}\b(go|get|rise|fall|climb|drop)"
        r"|where will .{0,40}\b(price|stock|share|it) be) (in|by|next|this|over|before|at)\b",
        "Point price forecasts are not produced; they are false precision.",
        "Ask for the valuation range and what has to be true for each end of it.",
    ),
    (
        r"\b(insider|non[- ]public|leaked?)\b.*\b(info|information|tip)\b",
        "This system works only from published, citable sources.",
        "Ask what the public filings and disclosed flows show.",
    ),
)


class A0Supervisor(Agent):
    """Plan, route, budget, refuse. Never analyse."""

    agent_id = "a0_supervisor"
    collections = ()
    tools = ("plan", "budget", "route", "refuse")
    tier = TaskClass.INTENT_ROUTING

    #: Rough per-agent call cost in MYR at the planning FX rate. Used to refuse
    #: before spending, not to bill.
    UNIT_COST_MYR = {
        Tier.REASON: Decimal("0.62"),
        Tier.BALANCED: Decimal("0.12"),
        Tier.CHEAP: Decimal("0.01"),
        Tier.LOCAL: Decimal("0.00"),
        Tier.EMBED: Decimal("0.00"),
    }

    def retrieve(self, *a, **kw):  # noqa: D102 - deliberate hard block
        raise PermissionError(
            "a0_supervisor may not retrieve. It routes to agents that own knowledge; "
            "if it read evidence itself no one could audit which store backed a claim."
        )

    def run(
        self, question: str, budget_myr: Decimal | None = None, instrument_ids: tuple[str, ...] = ()
    ) -> list[Finding]:
        plan = self.plan(question, budget_myr, instrument_ids)
        if plan.refusal is not None:
            return [
                Finding(
                    self.agent_id,
                    "refusal",
                    plan.refusal.reason,
                    caveats=[plan.refusal.what_would_help],
                )
            ]
        return [
            Finding(
                self.agent_id,
                "plan",
                f"{plan.intent.value}: routing to {len(plan.agents)} agents",
                numbers={"estimated_cost_myr": float(plan.estimated_cost.amount)},
                caveats=plan.notes,
            )
        ]

    def plan(
        self, question: str, budget_myr: Decimal | None = None, instrument_ids: tuple[str, ...] = ()
    ) -> Plan:
        self._guard_tool("plan")
        q = question.lower()

        refusal = self.scope_check(q)
        if refusal is not None:
            return Plan(
                Intent.OUT_OF_SCOPE,
                instrument_ids,
                (),
                (),
                Money(amount=Decimal(0), currency="MYR"),
                refusal=refusal,
            )

        intent = self.classify(q)
        agents = PLAYBOOK[intent]
        notes: list[str] = []

        if intent is Intent.SCREEN:
            # A stated boundary beats agents run against nothing. This system
            # evaluates names brought TO it; generating candidates is a
            # different product with a different failure mode - a ranked list
            # carries an implicit recommendation no evidence chain supports.
            return Plan(
                intent,
                (),
                (),
                (),
                Money(amount=Decimal(0), currency="MYR"),
                refusal=Refusal(
                    "This system does not screen for stocks or generate candidates. "
                    "It evaluates names you bring to it.",
                    "Name the companies you are considering and ask why one moved, "
                    "what it is worth, or how much of it you could hold. To split a "
                    "budget across several, use allocate; to compare against what you "
                    "already own, use rebalance.",
                ),
            )

        if intent in (Intent.WHY_IT_MOVED, Intent.SHOULD_I_BUY) and not instrument_ids:
            return Plan(
                intent,
                (),
                (),
                (),
                Money(amount=Decimal(0), currency="MYR"),
                refusal=Refusal(
                    "No instrument could be resolved from the question.",
                    "Name the company or give the ticker with its market, "
                    "e.g. MAYBANK on Bursa or NVDA on NASDAQ.",
                ),
            )

        classes = tuple(self.task_class_for(a) for a in agents)
        cost = self.estimate(classes)

        if budget_myr is not None and cost.amount > budget_myr:
            trimmed = self.trim(intent, agents, budget_myr)
            if trimmed is None:
                return Plan(
                    intent,
                    instrument_ids,
                    (),
                    (),
                    cost,
                    refusal=Refusal(
                        f"The minimum useful plan for this question costs about "
                        f"RM {cost.amount:.2f}, above the RM {budget_myr:.2f} budget.",
                        "Raise the budget or ask a narrower question (one agent, one instrument).",
                    ),
                )
            agents = trimmed
            classes = tuple(self.task_class_for(a) for a in agents)
            cost = self.estimate(classes)
            notes.append(f"plan trimmed to fit RM {budget_myr:.2f}")

        if intent is Intent.SHOULD_I_BUY:
            notes.append("output is analysis, not a recommendation to transact")
        return Plan(intent, instrument_ids, agents, classes, cost, notes=notes)

    # -- pieces -------------------------------------------------------------

    def scope_check(self, q: str) -> Refusal | None:
        for pattern, reason, help_ in _OUT_OF_SCOPE:
            if re.search(pattern, q):
                self.ctx.engine.enforce(
                    Action(
                        name="refuse",
                        rail=Rail.INPUT,
                        agent=self.agent_id,
                        payload={"reason": reason},
                    )
                )
                return Refusal(reason, help_)
        return None

    @staticmethod
    def classify(q: str) -> Intent:
        for intent, pattern in _PATTERNS:
            if re.search(pattern, q):
                return intent
        return Intent.EXPLAIN_CONCEPT

    @staticmethod
    def task_class_for(agent_id: str) -> TaskClass:
        return {
            "a1_fundamentals": TaskClass.FUNDAMENTALS_READ,
            "a2_valuation": TaskClass.VALUATION_COMMENT,
            "a3_price_technical": TaskClass.ADHOC_QUERY,
            "a4_news_narrative": TaskClass.NEWS_TRIAGE,
            "a5_catalyst_events": TaskClass.CATALYST_MATCH,
            "a6_macro_regime": TaskClass.MACRO_READ,
            "a7_sector_technology": TaskClass.SECTOR_READ,
            "a8_ownership_flow": TaskClass.FLOW_READ,
            "a9_attribution": TaskClass.ATTRIBUTION_HARD,
            "a10_thesis": TaskClass.THESIS_SYNTHESIS,
            "a11_red_team": TaskClass.RED_TEAM,
            "a12_portfolio_risk": TaskClass.RISK_COMMENT,
            "a13_sizing": TaskClass.RISK_COMMENT,
            "a14_teacher": TaskClass.ADHOC_QUERY,
            "a15_reflection": TaskClass.REFLECTION_DEEP,
        }.get(agent_id, TaskClass.ADHOC_QUERY)

    def estimate(self, classes: tuple[TaskClass, ...]) -> Money:
        self._guard_tool("budget")
        total = sum((self.UNIT_COST_MYR[ROUTING[c]] for c in classes), Decimal(0))
        return Money(amount=total, currency="MYR")

    def trim(
        self, intent: Intent, agents: tuple[str, ...], budget: Decimal
    ) -> tuple[str, ...] | None:
        """Drop the most expensive agents first, but never below the floor that
        makes the answer honest. docs/01 section 4.3: a cheap wrong answer is
        worse than a refusal."""
        floor = {
            Intent.WHY_IT_MOVED: ("a9_attribution",),
            Intent.SHOULD_I_BUY: ("a1_fundamentals", "a2_valuation", "a10_thesis", "a11_red_team"),
            Intent.WHAT_DO_I_OWN: ("a12_portfolio_risk",),
            Intent.RISK_CHECK: ("a12_portfolio_risk",),
        }.get(intent, agents[:1])

        keep = list(agents)
        order = sorted(keep, key=lambda a: -self.UNIT_COST_MYR[ROUTING[self.task_class_for(a)]])
        for a in order:
            if a in floor:
                continue
            if self.estimate(tuple(self.task_class_for(x) for x in keep)).amount <= budget:
                break
            keep.remove(a)
        if self.estimate(tuple(self.task_class_for(x) for x in keep)).amount > budget:
            return None
        return tuple(keep)
