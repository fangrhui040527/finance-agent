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
from core.llm.tiers import (
    MODEL_IDS,
    REQUEST_PROFILES,
    TaskClass,
    Tier,
    Usage,
    effective_tier,
    route,
)
from core.provenance.ledger import DEFAULT_FX_MYR_PER_USD, ProvenanceLedger


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage
    tier: Tier
    model_id: str
    cost_myr: Decimal
    #: Set when the model DECLINED (stop_reason=refusal). The refusal is content
    #: - "no, and here is why" - not an exception, mirroring every other refusal
    #: surface in the system. text is empty; the reason is here.
    refused: bool = False
    refusal_reason: str | None = None
    request_id: str | None = None


class Backend(Protocol):
    """Implemented per provider. The only place a vendor SDK may be imported."""

    def complete(
        self, model_id: str, prompt: str, system: str | None, profile=None
    ) -> tuple[str, Usage]: ...


class EchoBackend:
    """Deterministic stand-in so P0 is testable with no network and no keys.

    Mirrors devpulse_ai/verify.py (docs/12 section 2.5): the pipeline must be
    verifiable on mock data in under a second, or CI ends up depending on live
    vendor feeds and failing for reasons unrelated to the code.
    """

    def complete(
        self, model_id: str, prompt: str, system: str | None, profile=None
    ) -> tuple[str, Usage]:
        return (
            f"[{model_id}] {prompt[:80]}",
            Usage(input_tokens=len(prompt) // 4 or 1, output_tokens=20),
        )


class BudgetExceeded(RuntimeError):
    """docs/08 section 8: truncation is disclosed, never a silent downgrade."""


def BackendError_for_parse(schema, text: str, cause=None):
    """A reply that does not validate is a BackendError, never a guessed object."""
    from core.llm.backends import BackendError

    preview = text[:200].replace("\n", " ")
    return BackendError(
        f"model reply does not validate against {schema.__name__}: {cause or 'no JSON object found'}"
        f" (reply began: {preview!r})"
    )


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
        tier = effective_tier(route(task))  # callers never choose this; the cheap cap may lower it
        model_id = MODEL_IDS[tier]
        profile = REQUEST_PROFILES[tier]

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
            text, usage = self.backend.complete(model_id, prompt, system, profile=profile)
        except Exception as e:
            # A refusal is an ANSWER: ledger the spend, then hand back a
            # Completion the caller renders as content - the same semantics as
            # every REFUSED string in the MCP tools. Nothing retries it and
            # nothing re-routes it to a model that might comply (commitment 6).
            from core.llm.backends import Declined

            if isinstance(e, Declined) and e.usage is not None:
                elapsed = (time.perf_counter() - t0) * 1000
                rec = self._record(
                    agent,
                    task,
                    tier,
                    model_id,
                    prompt,
                    system,
                    e.usage,
                    elapsed,
                    text="",
                    error=str(e),
                    stop_reason="refusal",
                )
                reason = str(e)
                if e.category or e.explanation:
                    reason = f"{e.category or 'unspecified'}: {e.explanation or str(e)}"
                return Completion(
                    "",
                    e.usage,
                    tier,
                    model_id,
                    rec.cost_myr,
                    refused=True,
                    refusal_reason=reason,
                    request_id=getattr(self.backend, "last_request_id", None),
                )
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
                    stop_reason=type(e).__name__.lower(),
                )
            raise
        elapsed = (time.perf_counter() - t0) * 1000
        rec = self._record(
            agent,
            task,
            tier,
            model_id,
            prompt,
            system,
            usage,
            elapsed,
            text,
            stop_reason="end_turn",
        )
        return Completion(
            text,
            usage,
            tier,
            model_id,
            rec.cost_myr,
            request_id=getattr(self.backend, "last_request_id", None),
        )

    def complete_structured(
        self,
        agent: str,
        task: TaskClass,
        prompt: str,
        schema,
        system: str | None = None,
    ):
        """complete(), then validate the reply against a pydantic schema.

        JSON-instruction + validate rather than a server-side format constraint,
        because the cheap tier (where every dev call lands under the cap) does
        not take output_config.format - and a malformed reply must be a
        BackendError, never a guessed object.

        Returns (model, completion); on a refusal, (None, completion) with
        completion.refused set - a refusal is not a parse failure.
        """
        import json as _json

        from pydantic import ValidationError

        hint = (
            "\n\nAnswer with a single JSON object matching this schema, and"
            " nothing else:\n" + _json.dumps(schema.model_json_schema(), sort_keys=True)
        )
        completion = self.complete(agent, task, prompt + hint, system)
        if completion.refused:
            return None, completion
        text = completion.text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise BackendError_for_parse(schema, completion.text)
        try:
            model = schema.model_validate_json(text[start : end + 1])
        except ValidationError as e:
            raise BackendError_for_parse(schema, completion.text, e) from e
        return model, completion

    def _record(
        self,
        agent,
        task,
        tier,
        model_id,
        prompt,
        system,
        usage,
        elapsed,
        text,
        error=None,
        stop_reason="",
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
            # Same reasoning, same mistake, found later: how a turn ENDED and
            # the vendor's id for it reached the trace only, so nothing reading
            # the durable record could count a truncation or a refusal.
            stop_reason=stop_reason,
            request_id=getattr(self.backend, "last_request_id", None) or "",
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
                request_id=getattr(self.backend, "last_request_id", None),
                error=error,
            )
        return rec
