"""The choke point. No agent imports a vendor SDK; everything calls complete().

docs/01 section 9. Three things happen here and nowhere else:
  1. Tier is derived from TaskClass. Callers cannot pick a tier.
  2. Every call is enforced through the tool rail before it is made.
  3. Every call writes tokens and cost into the provenance ledger.

The Backend protocol keeps the vendor SDK behind one seam, so swapping provider
touches this file only.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from core.guardrails.policy import Action, PolicyEngine, Rail
from core.llm.tiers import MODEL_IDS, TaskClass, Tier, Usage, route
from core.provenance.ledger import DEFAULT_FX_MYR_PER_USD, ProvenanceLedger


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage
    tier: Tier
    model_id: str
    cost_myr: Decimal


class Backend(Protocol):
    """Implemented per provider. The only place a vendor SDK may be imported."""

    def complete(self, model_id: str, prompt: str, system: str | None) -> tuple[str, Usage]: ...


class EchoBackend:
    """Deterministic stand-in so P0 is testable with no network and no keys.

    Mirrors devpulse_ai/verify.py (docs/12 section 2.5): the pipeline must be
    verifiable on mock data in under a second, or CI ends up depending on live
    vendor feeds and failing for reasons unrelated to the code.
    """

    def complete(self, model_id: str, prompt: str, system: str | None) -> tuple[str, Usage]:
        return (
            f"[{model_id}] {prompt[:80]}",
            Usage(input_tokens=len(prompt) // 4 or 1, output_tokens=20),
        )


class BudgetExceeded(RuntimeError):
    """docs/08 section 8: truncation is disclosed, never a silent downgrade."""


class InferenceClient:
    def __init__(
        self,
        backend: Backend,
        engine: PolicyEngine,
        ledger: ProvenanceLedger,
        daily_budget_myr: Decimal | None = None,
        fx_rate: Decimal = DEFAULT_FX_MYR_PER_USD,
    ) -> None:
        self.backend = backend
        self.engine = engine
        self.ledger = ledger
        self.daily_budget_myr = daily_budget_myr
        self.fx_rate = fx_rate

    def complete(
        self, agent: str, task: TaskClass, prompt: str, system: str | None = None
    ) -> Completion:
        tier = route(task)  # callers never choose this
        model_id = MODEL_IDS[tier]

        self.engine.enforce(
            Action(
                name="llm_complete",
                rail=Rail.TOOL,
                agent=agent,
                payload={"task_class": task.value, "tier": tier.value},
            )
        )

        if self.daily_budget_myr is not None:
            spent = self.ledger.total_cost_myr()
            if spent >= self.daily_budget_myr:
                raise BudgetExceeded(
                    f"daily budget RM {self.daily_budget_myr} exhausted (spent RM {spent:.2f}); "
                    "plan truncated and disclosed, not downgraded silently"
                )

        text, usage = self.backend.complete(model_id, prompt, system)
        rec = self.ledger.record_call(
            agent=agent, task_class=task, tier=tier, model_id=model_id,
            prompt=prompt, usage=usage, fx_rate=self.fx_rate,
        )
        return Completion(text, usage, tier, model_id, rec.cost_myr)
