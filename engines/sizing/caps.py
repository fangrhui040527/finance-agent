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
    "XHKG": Decimal("95"),   # asymptote ~72 bps - the WORST of the seven, see below
    "XTKS": Decimal("55"),   # asymptote ~40 bps; the tick, not the fee, is the cost
    "XLON": Decimal("75"),   # asymptote ~70 bps, almost all of it one-way stamp duty
    "XASX": Decimal("25"),   # asymptote ~20 bps - the cheapest after XNAS
    "XNSE": Decimal("35"),   # asymptote ~26 bps; STT alone is 20 of them
    "XTAI": Decimal("70"),   # asymptote ~59 bps, and 30 are paid ONLY on exit
    "XKRX": Decimal("55"),   # asymptote ~45 bps; sell-side tax, half Taiwan's
    "XETR": Decimal("25"),   # asymptote ~20 bps - no transaction tax at all
}
# XHKG is the entry that contradicts the intuition. Hong Kong is a developed
# market and is nonetheless the most expensive here: 0.25% retail brokerage is
# 50 bps round trip on its own, and stamp duty is 0.1% on BOTH sides with NO cap,
# unlike Bursa's RM 1,000. Cost therefore never falls below ~72 bps at any size,
# where Bursa reaches ~46 and XNAS ~0.6. Sorting markets by how developed they
# are gets the cost ranking backwards.
# XTKS is the entry where the FEE table lies. Tokyo's round trip is ~40 bps, but
# a JPY 4,000 stock ticks in JPY 5 - 12.5 bps of spread per tick, against ~0.5 bps
# on a USD 200 US name. The floor here is set above the fee asymptote because the
# fee asymptote is not what a Tokyo position actually costs to enter and leave.
# XLON is ~70 bps and roughly fifty of those are Stamp Duty Reserve Tax, charged
# on the BUY LEG ONLY. Doubling it - which FeeSchedule.round_trip did for every
# leg until this market arrived - reads as prudence and is not: an overstated
# floor refuses positions that would have cleared the real one.
# XTAI and XKRX both levy their transaction tax on the SELL SIDE ONLY, which the
# per_side field now expresses. Taiwan's 0.3% is 30 bps a round trip pays exactly
# once - doubling it would put the floor 30 bps too high and refuse positions
# that clear the real one. It also means the cost is paid on EXIT, so a position
# never closed never pays it. That is a bad reason to hold and is named here so
# nobody rediscovers it as a feature.
# XETR is the cheapest European market here because Germany levies no financial
# transaction tax. That is a fact with a shelf life; if one arrives, this entry
# and markets/xetr.py are what need revisiting.
# XSES happens to land on the same number as the generic default, and that is
# precisely why it is written down. An entry that agrees with the default by
# coincidence is a decision; a missing entry that falls back to it is an
# accident, and the next market added would inherit the accident silently.


def cost_floor_bps(mic: str | None) -> Decimal:
    """The market's floor, resolved through the alias map.

    Resolution is not a nicety. Instrument ids in this repo say `MYX`, the table
    is keyed `XKLS`, and a straight dict lookup silently returned the 30 bps
    DEFAULT for every Bursa position - half the real 60. A wrong floor does not
    crash; it just sizes positions that can never pay their own spread.
    """
    if mic is None:
        return COST_FLOOR_BPS_DEFAULT
    from markets.registry import resolve_mic

    return COST_FLOOR_BPS_BY_MIC.get(resolve_mic(mic), COST_FLOOR_BPS_DEFAULT)


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
    """You must be able to exit in a bad week, when volume falls too.

    Negative or non-positive ADV is refused rather than passed through. A
    negative cap is the smallest of the five and therefore always wins
    CapSet.binding(), which would carry a negative target size downstream -
    a cap that inverts the thing it is meant to bound.
    """
    if adv_20d < 0:
        raise ValueError(
            f"average daily value cannot be negative (got {adv_20d}); a negative liquidity "
            "cap would win binding() and invert the constraint"
        )
    if participation <= 0 or participation > 1:
        raise ValueError(f"participation must be in (0, 1], got {participation}")
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
