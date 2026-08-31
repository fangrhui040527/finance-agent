"""The choke point. No agent imports a vendor SDK; everything calls complete().

docs/01 section 9. Three things happen here and nowhere else:
  1. Tier is derived from TaskClass. Callers cannot pick a tier.
  2. Every call is enforced through the tool rail before it is made.
  3. Every call writes tokens and cost into the provenance ledger.

The Backend protocol keeps the vendor SDK behind one seam, so swapping provider
touches this file only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
        budget_window: timedelta = timedelta(days=1),
    ) -> None:
        self.backend = backend
        self.engine = engine
        self.ledger = ledger
        self.daily_budget_myr = daily_budget_myr
        self.fx_rate = fx_rate
        self.budget_window = budget_window

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
            # Windowed, not lifetime. This compared total_cost_myr() - a sum over
            # every row ever written - against a field named daily_budget_myr.
            # Harmless while every ledger was in-memory and died with the process,
            # because lifetime and today were the same number. Against a durable
            # ledger it is a one-way cap: once cumulative spend passes the daily
            # budget the client raises forever and never recovers, which for an
            # unattended job means it stops and nothing says why.
            since = datetime.now(UTC) - self.budget_window
            spent = self.ledger.cost_since(since)
            if spent >= self.daily_budget_myr:
                raise BudgetExceeded(
                    f"budget RM {self.daily_budget_myr} exhausted for the last "
                    f"{self.budget_window} (spent RM {spent:.2f}); "
                    "plan truncated and disclosed, not downgraded silently"
                )

        t0 = time.perf_counter()
        try:
            text, usage = self.backend.complete(model_id, prompt, system)
        except Exception as e:
            # A backend error carrying a Usage is a call the model ANSWERED and
            # billed - truncated, or refused - whose text cannot be used. The
            # spend is real, so it is ledgered and traced before the error goes
            # up. A transport failure carries no usage and costs nothing.
            spent = getattr(e, "usage", None)
            if isinstance(spent, Usage):
                self._record(
                    agent,
                    task,
                    tier,
                    model_id,
                    prompt,
                    system,
                    spent,
                    (time.perf_counter() - t0) * 1000,
                    text="",
                    error=str(e),
                )
            raise
        elapsed = (time.perf_counter() - t0) * 1000
        rec = self._record(agent, task, tier, model_id, prompt, system, usage, elapsed, text)
        return Completion(text, usage, tier, model_id, rec.cost_myr)

    def _record(
        self, agent, task, tier, model_id, prompt, system, usage, elapsed, text, error=None
    ):
        from core.trace import emit, is_tracing

        rec = self.ledger.record_call(
            agent=agent,
            task_class=task,
            tier=tier,
            model_id=model_id,
            prompt=prompt,
            usage=usage,
            fx_rate=self.fx_rate,
            # Measured either way. It used to reach the trace and nowhere else,
            # so an ordinary run - the only kind that happens in production -
            # threw away the number docs/01 section 10 asks for.
            latency_ms=elapsed,
        )
        if is_tracing():
            # The ledger keeps prompt_hash only, by design. The trace keeps the
            # text, because "what exactly did we send it" is the first question
            # of every debugging session and a hash cannot answer it.
            emit(
                "llm_call",
                agent,
                agent=agent,
                task_class=task.value,
                tier=tier.value,
                model_id=model_id,
                backend=type(self.backend).__name__,
                system=system or "",
                prompt=prompt,
                response=text,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_tokens=usage.cached_input_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                cost_myr=str(rec.cost_myr),
                prompt_hash=rec.prompt_hash,
                latency_ms=round(elapsed, 2),
                error=error,
            )
        return rec
