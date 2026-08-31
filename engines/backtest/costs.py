"""Cost model, applied to every simulated fill.

docs/04 section 6.3 and docs/05 section 9. A strategy that survives realistic
costs is rare; one that only works gross is not a strategy.

  round_trip = commission + half-spread x2 + participation slippage
             + stamp duty + clearing + FX conversion spread (cross-border)
             + dividend withholding over the holding period
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from markets.contract import MarketAdapter


@dataclass(frozen=True)
class FillCost:
    exchange_fees: Decimal
    spread: Decimal
    slippage: Decimal
    fx_spread: Decimal

    @property
    def total(self) -> Decimal:
        return self.exchange_fees + self.spread + self.slippage + self.fx_spread

    def bps_of(self, consideration: Decimal) -> Decimal:
        return self.total / consideration * Decimal(10_000) if consideration > 0 else Decimal(0)


def participation_slippage(
    consideration: Decimal, adv: Decimal, coefficient: Decimal = Decimal("0.10")
) -> Decimal:
    """Square-root market impact: cost scales with sqrt(participation rate).

    Trading 5% of ADV costs meaningfully more than 0.5%, and the relationship is
    concave rather than linear.
    """
    if adv <= 0:
        return consideration * Decimal("0.02")  # unknown liquidity: assume bad
    rate = float(consideration / adv)
    return consideration * coefficient * Decimal(str(math.sqrt(max(rate, 0.0))))


def fill_cost(
    adapter: MarketAdapter,
    consideration: Decimal,
    adv: Decimal,
    half_spread_bps: Decimal = Decimal("5"),
    cross_border: bool = False,
    fx_spread_bps: Decimal = Decimal("50"),
) -> FillCost:
    return FillCost(
        exchange_fees=adapter.fee_schedule.one_side(consideration),
        spread=consideration * half_spread_bps / Decimal(10_000),
        slippage=participation_slippage(consideration, adv),
        fx_spread=(consideration * fx_spread_bps / Decimal(10_000)) if cross_border else Decimal(0),
    )


def round_trip_cost(
    adapter: MarketAdapter,
    consideration: Decimal,
    adv: Decimal,
    half_spread_bps: Decimal = Decimal("5"),
    cross_border: bool = False,
    fx_spread_bps: Decimal = Decimal("50"),
    holding_years: float = 0.0,
    dividend_yield: Decimal = Decimal(0),
    holder_country: str = "MY",
) -> Decimal:
    one = fill_cost(adapter, consideration, adv, half_spread_bps, cross_border, fx_spread_bps)
    total = one.total * 2
    if holding_years > 0 and dividend_yield > 0:
        wht = adapter.withholding("dividend", holder_country)
        total += consideration * dividend_yield * wht * Decimal(str(holding_years))
    return total


def sqrt_impact(quantity: float, adv: float, daily_vol: float, eta: float = 0.5) -> float:
    """Square-root market impact: eta * sigma * sqrt(q / ADV), as a fraction.

    The linear participation model understates the cost of big prints: impact
    grows with the square root of participation empirically, not linearly.
    Used above the participation band where linear stops being honest.
    """
    if adv <= 0:
        raise ValueError("adv must be positive")
    if quantity < 0:
        raise ValueError("quantity cannot be negative")
    return eta * daily_vol * math.sqrt(quantity / adv)


def impact_model_for(participation: float) -> str:
    """Which impact model is honest at this %ADV. Named so sizing can cite it.

    < 0.5% of ADV: fees dominate, impact is noise - 'fixed'.
    0.5% - 5%:     the linear participation model holds - 'linear'.
    > 5%:          square-root regime; linear understates - 'sqrt'.
    """
    if participation < 0:
        raise ValueError("participation cannot be negative")
    if participation < 0.005:
        return "fixed"
    if participation <= 0.05:
        return "linear"
    return "sqrt"
