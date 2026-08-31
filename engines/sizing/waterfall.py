"""Investable capital waterfall.

docs/05 section 2. Before any question about which stock, there is a question
about how much money is allowed to be in stocks at all.

Three hard rules, and there is no override flag in this API:
  - the emergency floor is never breachable
  - near-term goals hold cash, regardless of how good a signal looks
  - debt above the hurdle always outranks equity - paying off an 18% card is a
    guaranteed 18% return that no equity forecast can honestly promise
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class FloorBreach(ValueError):
    """The emergency floor cannot be reduced by any API path."""


@dataclass(frozen=True)
class Liability:
    name: str
    balance: Decimal
    annual_rate: Decimal


@dataclass(frozen=True)
class Goal:
    name: str
    amount: Decimal
    months_away: int


@dataclass(frozen=True)
class WaterfallStep:
    label: str
    deduction: Decimal
    remaining: Decimal
    locked: bool = False


@dataclass(frozen=True)
class Waterfall:
    liquid_assets: Decimal
    steps: tuple[WaterfallStep, ...]
    investable: Decimal

    def explain(self) -> str:
        lines = [f"Liquid assets{'':<22}{self.liquid_assets:>12,.2f}"]
        for s in self.steps:
            lock = "  [locked]" if s.locked else ""
            lines.append(f"  - {s.label:<30}{s.deduction:>10,.2f}{lock}")
        lines.append(f"= Investable{'':<24}{self.investable:>12,.2f}")
        return "\n".join(lines)


def compute(
    liquid_assets: Decimal,
    essential_monthly_spend: Decimal,
    goals: list[Goal],
    liabilities: list[Liability],
    planned_monthly_contribution: Decimal = Decimal(0),
    emergency_months: int = 6,
    debt_hurdle: Decimal = Decimal("0.08"),
) -> Waterfall:
    remaining = liquid_assets
    steps: list[WaterfallStep] = []

    floor = essential_monthly_spend * emergency_months
    remaining -= floor
    steps.append(WaterfallStep(f"Emergency floor ({emergency_months}m)", floor, remaining, True))

    near = sum((g.amount for g in goals if g.months_away <= 24), Decimal(0))
    remaining -= near
    steps.append(WaterfallStep("Near-term goals (<=24m)", near, remaining, True))

    payoff = sum((l.balance for l in liabilities if l.annual_rate > debt_hurdle), Decimal(0))
    remaining -= payoff
    steps.append(WaterfallStep(f"Debt above {debt_hurdle:.0%} hurdle", payoff, remaining, True))

    buffer = planned_monthly_contribution
    remaining -= buffer
    steps.append(WaterfallStep("Cash buffer (1m contributions)", buffer, remaining))

    investable = max(remaining, Decimal(0))
    if remaining < 0:
        # The floor and reservations already consumed everything. The correct
        # answer is no investable capital, never a partial raid on the floor.
        investable = Decimal(0)
    return Waterfall(liquid_assets, tuple(steps), investable)
