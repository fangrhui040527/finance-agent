"""Per-position size: the minimum of five caps.

docs/05 section 3. A SizingDecision that breaches a cap cannot be constructed -
the constructor raises. Guardrails written as prompt text are suggestions.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

# docs/09 section 8: ~50-100 logged outcomes before Kelly inputs mean anything.
KELLY_MIN_TRADES = 50
KELLY_FRACTION = Decimal("0.25")          # quarter-Kelly
IMPLAUSIBLE_EDGE = Decimal("0.30")        # a claimed 30% edge means the model is broken
# docs/05 section 3 quotes a single 30 bps cost floor. Building the Bursa fee
# schedule showed that is unreachable there: 2 x (0.1% brokerage + 0.03% clearing
# + 0.1% stamp) is ~46 bps before the RM 8 minimum, and only falls below 30 bps
# above roughly RM 4m of consideration once the RM 1,000 caps bind. A single
# global floor would refuse every Bursa position ever.
#
# So the floor is per-market, set to roughly 1.3x the market's own asymptotic
# round-trip cost - the position must be large enough that fees are near their
# floor, not that fees are cheap in absolute terms.
COST_FLOOR_BPS_DEFAULT = Decimal("30")
COST_FLOOR_BPS_BY_MIC: dict[str, Decimal] = {
    "XKLS": Decimal("60"),   # asymptote ~46 bps
    "XNAS": Decimal("5"),    # asymptote ~0.6 bps
    "XSES": Decimal("30"),   # asymptote ~24 bps before the SGD 600 clearing cap binds
}
# XSES happens to land on the same number as the generic default, and that is
# precisely why it is written down. An entry that agrees with the default by
# coincidence is a decision; a missing entry that falls back to it is an
# accident, and the next market added would inherit the accident silently.


def cost_floor_bps(mic: str | None) -> Decimal:
    if mic is None:
        return COST_FLOOR_BPS_DEFAULT
    return COST_FLOOR_BPS_BY_MIC.get(mic.upper(), COST_FLOOR_BPS_DEFAULT)


class Band(str, Enum):
    ACCUMULATE = "accumulate"
    HOLD = "hold"
    TRIM = "trim"
    EXIT = "exit"
    NO_SIGNAL = "no_signal"


class BindingCap(str, Enum):
    RISK = "risk"
    KELLY = "kelly"
    CONCENTRATION = "concentration"
    LIQUIDITY = "liquidity"
    COST = "cost"
    NONE = "none"


class ImplausibleEdge(ValueError):
    """docs/09: a claimed 30% edge means the model is broken, not that you found something."""


class CapBreach(ValueError):
    """A decision that breaches a cap cannot exist."""


@dataclass(frozen=True)
class CapSet:
    risk: Decimal
    kelly: Decimal | None
    concentration: Decimal
    liquidity: Decimal
    cost_floor: Decimal

    def binding(self) -> tuple[BindingCap, Decimal]:
        options = [
            (BindingCap.RISK, self.risk),
            (BindingCap.CONCENTRATION, self.concentration),
            (BindingCap.LIQUIDITY, self.liquidity),
        ]
        if self.kelly is not None:
            options.append((BindingCap.KELLY, self.kelly))
        cap, value = min(options, key=lambda x: x[1])
        return cap, value


def risk_budget_cap(portfolio_value: Decimal, risk_per_trade: Decimal, stop_distance_frac: Decimal) -> Decimal:
    """Bounds the LOSS, not the position."""
    if stop_distance_frac <= 0:
        raise ValueError("stop distance must be positive")
    return (portfolio_value * risk_per_trade) / stop_distance_frac


def kelly_cap(
    portfolio_value: Decimal, p_win: Decimal, payoff_ratio: Decimal, trades_logged: int
) -> Decimal | None:
    """Quarter-Kelly, and disabled entirely below the trade-count gate.

    Below ~50 resolved decisions the win rate and payoff ratio are
    indistinguishable from noise, and a noisy Kelly is worse than no Kelly
    because it is confidently wrong in both directions.
    """
    if trades_logged < KELLY_MIN_TRADES:
        return None
    if payoff_ratio <= 0:
        raise ValueError("payoff ratio must be positive")
    q = Decimal(1) - p_win
    f = (p_win * payoff_ratio - q) / payoff_ratio
    if f > IMPLAUSIBLE_EDGE:
        raise ImplausibleEdge(
            f"computed Kelly fraction {f:.1%} exceeds the {IMPLAUSIBLE_EDGE:.0%} sanity ceiling; "
            "the model is broken, not lucky"
        )
    if f <= 0:
        return Decimal(0)
    return portfolio_value * f * KELLY_FRACTION


def concentration_cap(portfolio_value: Decimal, single_name_limit: Decimal) -> Decimal:
    return portfolio_value * single_name_limit


def liquidity_cap(adv_20d: Decimal, participation: Decimal = Decimal("0.05")) -> Decimal:
    """You must be able to exit in a bad week, when volume falls too."""
    return adv_20d * participation


def cost_floor_value(round_trip_cost_at, mic: str | None = None) -> Decimal:
    """Smallest position whose round-trip cost stays within the market's floor.

    Bisection, because fee schedules have minimums and caps and are not smooth.
    """
    limit = cost_floor_bps(mic)
    lo, hi = Decimal("1"), Decimal("100000000")
    for _ in range(100):
        mid = (lo + hi) / 2
        bps = round_trip_cost_at(mid) / mid * Decimal(10_000)
        if bps > limit:
            lo = mid
        else:
            hi = mid
    return hi


def vol_target_scalar(realised_vol: Decimal, target_vol: Decimal) -> Decimal:
    """docs/05 section 3.3: may de-risk, never lever up. Capped at 1.0."""
    if realised_vol <= 0:
        return Decimal(1)
    return min(max(target_vol / realised_vol, Decimal("0.5")), Decimal(1))
