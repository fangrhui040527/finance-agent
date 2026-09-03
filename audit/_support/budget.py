"""A hard spend ceiling for the live phase, checked BEFORE the wire.

Two rules, both load-bearing:

  * **Haiku or nothing.** Every live call this suite makes resolves to
    `claude-haiku-4-5`. The model is pinned here rather than requested, so a
    tier table that starts routing somewhere more expensive fails the audit
    instead of quietly billing for it.
  * **The cap is checked before sending, not after.** A budget that is
    reconciled afterwards is a receipt, not a limit. `guard()` prices the
    request's worst case against what has already been spent and raises rather
    than send the call that would cross the line.

A suite that can quietly run up a bill is a suite people stop running.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal

#: The only model this suite is permitted to call.
CHEAP_MODEL = "claude-haiku-4-5"

#: USD per million tokens for that model, first-party rates.
IN_RATE = Decimal("1.00")
OUT_RATE = Decimal("5.00")
CACHE_READ_MULT = Decimal("0.1")
CACHE_WRITE_MULT = Decimal("1.25")
MILLION = Decimal(1_000_000)


class BudgetExceeded(RuntimeError):
    """Raised instead of sending a call that would cross the ceiling."""


def max_usd() -> Decimal:
    return Decimal(os.environ.get("EVAL_MAX_USD", "0.05"))


def live_enabled() -> bool:
    """Two deliberate opt-ins: the flag, and a key that is actually present.

    A key in the environment for some other reason must not start spending.
    """
    return os.environ.get("EVAL_LIVE") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))


@dataclass
class Call:
    label: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read: int = 0
    cache_write: int = 0

    @property
    def usd(self) -> Decimal:
        base = Decimal(self.input_tokens) * IN_RATE
        base += Decimal(self.cache_read) * IN_RATE * CACHE_READ_MULT
        base += Decimal(self.cache_write) * IN_RATE * CACHE_WRITE_MULT
        base += Decimal(self.output_tokens) * OUT_RATE
        return base / MILLION


@dataclass
class Meter:
    calls: list[Call] = field(default_factory=list)

    @property
    def spent(self) -> Decimal:
        return sum((c.usd for c in self.calls), Decimal(0))

    def guard(self, label: str, max_tokens: int, prompt_tokens: int = 2000) -> None:
        """Refuse the call whose WORST case would cross the ceiling."""
        worst = Call(label, CHEAP_MODEL, prompt_tokens, max_tokens).usd
        if self.spent + worst > max_usd():
            raise BudgetExceeded(
                f"{label}: worst case USD {worst:.5f} on top of USD {self.spent:.5f} "
                f"already spent would cross the USD {max_usd():.5f} ceiling"
            )

    def record(self, label: str, model: str, usage) -> Call:
        call = Call(
            label=label,
            model=model,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_read=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            cache_write=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        )
        self.calls.append(call)
        return call

    def summary(self) -> dict:
        return {
            "calls": len(self.calls),
            "spent_usd": f"{self.spent:.5f}",
            "ceiling_usd": f"{max_usd():.5f}",
            "models": sorted({c.model for c in self.calls}),
        }


#: One meter for the whole run, so the ceiling is a run ceiling and not a
#: per-test one that eleven tests can each spend in full.
METER = Meter()


def client():
    """A real SDK client, or None when the live phase is not enabled."""
    if not live_enabled():
        return None
    import anthropic

    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0)


def ask(label: str, system: str, prompt: str, max_tokens: int = 400) -> tuple[str, Call]:
    """One metered Haiku call. Raises BudgetExceeded rather than overspend."""
    c = client()
    if c is None:
        raise RuntimeError("live phase is not enabled")
    METER.guard(label, max_tokens, prompt_tokens=len(system + prompt) // 3)
    msg = c.messages.create(
        model=CHEAP_MODEL,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": prompt}],
    )
    call = METER.record(label, getattr(msg, "model", CHEAP_MODEL), msg.usage)
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return text, call
