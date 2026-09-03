"""A session cost meter for the live phase.

Two jobs.

The first is a budget stop. Every live call is recorded, and a QA run that would
exceed `QA_MAX_USD` raises before sending rather than after spending. A test
suite that can quietly run up a bill is a test suite people stop running.

The second is reconciliation. The product prices a call from its own
`PRICING_USD` table using only `input_tokens`, `output_tokens` and
`cache_read_input_tokens`. The live API also reports `cache_creation_input_tokens`,
which is billed at 1.25x the base input rate and which the product never reads.
This meter prices from the full payload, so the gap between the two numbers is a
measurable quantity rather than an argument about the source.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

#: USD per million tokens, first-party rates, keyed by the model the API says
#: actually answered - not by the tier the caller asked for.
RATES: dict[str, tuple[Decimal, Decimal]] = {
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
    "claude-opus-5": (Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5": (Decimal("2.00"), Decimal("10.00")),
}

#: Cache reads bill at 0.1x input; a 5-minute cache write bills at 1.25x input.
CACHE_READ_MULTIPLIER = Decimal("0.1")
CACHE_WRITE_MULTIPLIER = Decimal("1.25")

MILLION = Decimal(1_000_000)


def rate_for(model_id: str) -> tuple[Decimal, Decimal]:
    """Rates for a model id, tolerating a dated snapshot suffix."""
    for prefix, rates in RATES.items():
        if model_id.startswith(prefix):
            return rates
    return Decimal("1.00"), Decimal("5.00")


class BudgetStop(RuntimeError):
    """The QA session hit its spend ceiling. Raised before the next call."""


@dataclass
class Meter:
    """Records live calls, prices them, and refuses to overspend."""

    max_usd: Decimal = Decimal(os.environ.get("QA_MAX_USD", "0.50"))
    calls: list[dict] = field(default_factory=list)

    def guard(self) -> None:
        """Called before a live request. Raises rather than overspending."""
        if self.total_usd >= self.max_usd:
            raise BudgetStop(
                f"QA session spent USD {self.total_usd:.6f} of its "
                f"USD {self.max_usd} ceiling; raise QA_MAX_USD to continue"
            )

    def record(self, result) -> None:
        usage = result.usage or {}
        model = result.payload.get("model") or result.request_body.get("model", "")
        in_rate, out_rate = rate_for(model)

        total_in = int(usage.get("input_tokens", 0) or 0)
        cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
        cache_write = int(usage.get("cache_creation_input_tokens", 0) or 0)
        out = int(usage.get("output_tokens", 0) or 0)

        # The API reports input_tokens as the UNCACHED portion; cache reads and
        # cache writes are reported separately and are not included in it.
        fresh = Decimal(total_in) / MILLION * in_rate
        read = Decimal(cache_read) / MILLION * in_rate * CACHE_READ_MULTIPLIER
        write = Decimal(cache_write) / MILLION * in_rate * CACHE_WRITE_MULTIPLIER
        outc = Decimal(out) / MILLION * out_rate

        self.calls.append(
            {
                "model": model,
                "status": result.status,
                "input_tokens": total_in,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
                "output_tokens": out,
                "usd": str(fresh + read + write + outc),
                "elapsed_ms": round(result.elapsed_ms, 1),
            }
        )

    @property
    def total_usd(self) -> Decimal:
        return sum((Decimal(c["usd"]) for c in self.calls), Decimal(0))

    def summary(self) -> str:
        return (
            f"{len(self.calls)} live calls, "
            f"USD {self.total_usd:.6f} of a USD {self.max_usd} ceiling"
        )

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "calls": self.calls,
                    "total_usd": str(self.total_usd),
                    "ceiling_usd": str(self.max_usd),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
