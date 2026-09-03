"""Per-position size: the minimum of five caps.

docs/05 section 3. A SizingDecision that breaches a cap cannot be constructed -
the constructor raises. Guardrails written as prompt text are suggestions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from core.contracts.money import BASE_CURRENCY, Money

# docs/09 section 8: ~50-100 logged outcomes before Kelly inputs mean anything.
KELLY_MIN_TRADES = 50
KELLY_FRACTION = Decimal("0.25")  # quarter-Kelly
IMPLAUSIBLE_EDGE = Decimal("0.30")  # a claimed 30% edge means the model is broken
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

#: The upper bound of `cost_floor_value`'s bisection, and its answer when NO
#: position satisfies the floor. It is a sentinel, not a position requirement:
#: a schedule whose asymptotic cost already exceeds its floor can never come
#: down, so the search converges on its own ceiling. `stress/run.py` and
#: `qa/phase1/test_p1_invariants.py` both detect that state by inspecting this
#: value, which is why it is a named constant and not an exception - raising
#: here would turn two working detectors into crashes.
COST_FLOOR_CEILING = Decimal("100000000")
COST_FLOOR_BPS_BY_MIC: dict[str, Decimal] = {
    "XKLS": Decimal("60"),  # asymptote ~46 bps
    "XNAS": Decimal("5"),  # asymptote ~0.6 bps - ZERO-COMMISSION account; see brokers.py
    "XSES": Decimal("30"),  # asymptote ~24 bps before the SGD 600 clearing cap binds
    "XHKG": Decimal("95"),  # asymptote ~72 bps - the WORST of the seven, see below
    "XTKS": Decimal("55"),  # asymptote ~40 bps; the tick, not the fee, is the cost
    "XLON": Decimal("75"),  # asymptote ~70 bps, almost all of it one-way stamp duty
    "XASX": Decimal("25"),  # asymptote ~20 bps - the cheapest after XNAS
    "XNSE": Decimal("35"),  # asymptote ~26 bps; STT alone is 20 of them
    "XTAI": Decimal("70"),  # asymptote ~59 bps, and 30 are paid ONLY on exit
    "XKRX": Decimal("55"),  # asymptote ~45 bps; sell-side tax, half Taiwan's
    "XETR": Decimal("25"),  # asymptote ~20 bps - no transaction tax at all
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


def cost_floor_bps(mic: str | None, broker: str | None = None) -> Decimal:
    """The market's floor, resolved through the alias map.

    Resolution is not a nicety. Instrument ids in this repo say `MYX`, the table
    is keyed `XKLS`, and a straight dict lookup silently returned the 30 bps
    DEFAULT for every Bursa position - half the real 60. A wrong floor does not
    crash; it just sizes positions that can never pay their own spread.
    """
    if mic is None:
        return COST_FLOOR_BPS_DEFAULT
    from markets.registry import resolve_mic

    canonical = resolve_mic(mic)
    if broker is not None:
        from markets.brokers import BROKER_FLOOR_BPS

        # A broker floor is a DIFFERENT number, not an adjustment to the venue's.
        # moomoo MY pays 6 bps of commission before anything else, which is above
        # the 5 bps XNAS tolerance on its own - so the venue entry is not a
        # starting point that can be nudged, it is unreachable.
        if (broker, canonical) in BROKER_FLOOR_BPS:
            return BROKER_FLOOR_BPS[(broker, canonical)]
    return COST_FLOOR_BPS_BY_MIC.get(canonical, COST_FLOOR_BPS_DEFAULT)


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


class CurrencyMismatch(ValueError):
    """Two amounts in different currencies met without a rate to join them.

    Raised rather than guessed. The whole point of this class is that the
    alternative is a finite, plausible, correctly-typed, wrong number.
    """


def to_quote(
    base_amount: Decimal, quote: str, base_per_quote: Decimal | None, asof=None
) -> Decimal:
    """A MYR amount expressed in `quote`. Refuses without an explicit rate.

    `base_per_quote` is MYR per ONE unit of `quote` - the direction a Malaysian
    quotes it aloud ("the dollar is 4.20"), and the direction
    `FxStore.rate_asof(quote, BASE_CURRENCY, d)` returns.
    """
    quote = quote.upper()
    if quote == BASE_CURRENCY:
        return base_amount
    if base_per_quote is None:
        raise CurrencyMismatch(
            f"a {BASE_CURRENCY} amount cannot be compared with a {quote} one without an "
            f"FX rate. Pass the {BASE_CURRENCY}-per-{quote} rate and the date it was "
            f"struck; the alternative is comparing {BASE_CURRENCY} {base_amount:,.2f} "
            f"against {quote} as bare numbers, which is off by the rate and looks fine."
        )
    if base_per_quote <= 0:
        raise CurrencyMismatch(f"{BASE_CURRENCY}-per-{quote} rate {base_per_quote} is not positive")
    return base_amount / base_per_quote


def to_base(
    quote_amount: Decimal, quote: str, base_per_quote: Decimal | None, asof: datetime | None = None
) -> Decimal:
    """The reverse of `to_quote`, routed through Money so one rule guards both.

    Money.convert already refuses a non-positive rate and treats same-currency
    conversion as an identity that ignores the rate; reusing it means those two
    decisions are made once rather than re-argued here.
    """
    quote = quote.upper()
    if quote == BASE_CURRENCY:
        return quote_amount
    if base_per_quote is None:
        raise CurrencyMismatch(
            f"a {quote} amount cannot be reported in {BASE_CURRENCY} without an FX rate. "
            f"Pass the {BASE_CURRENCY}-per-{quote} rate and the date it was struck."
        )
    return (
        Money(amount=quote_amount, currency=quote)
        .convert(BASE_CURRENCY, base_per_quote, asof)
        .amount
    )


@dataclass(frozen=True)
class CapSet:
    """Five caps, and the ONE currency all five are denominated in.

    The currency field is not decoration. `binding()` is a `min()` across the
    five, and until this field existed three of them came from the portfolio
    (MYR) while two came from the market (its own currency): `liquidity` from
    local turnover, `cost_floor` from a local fee schedule. `min()` then compared
    MYR against USD as bare numbers and returned whichever was numerically
    smaller regardless of unit.

    That is not a rounding error. A thinly traded US name with USD 300k of daily
    value gives a USD 15,000 liquidity cap; against an MYR 40,000 concentration
    cap, `min` picks 15,000 and the system deploys roughly MYR 63,000 - a 58%
    overshoot of the single-name limit it had just computed, reported as
    compliant. Declaring the currency makes the mistake unconstructable instead
    of undetectable.
    """

    risk: Decimal
    kelly: Decimal | None
    concentration: Decimal
    liquidity: Decimal
    cost_floor: Decimal
    currency: str = BASE_CURRENCY

    def __post_init__(self) -> None:
        c = str(self.currency).upper()
        if len(c) != 3 or not c.isalpha():
            raise CurrencyMismatch(
                f"{self.currency!r} is not a currency code; a CapSet must name the one "
                "currency all five of its caps are denominated in"
            )
        object.__setattr__(self, "currency", c)

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


def risk_budget_cap(
    portfolio_value: Decimal, risk_per_trade: Decimal, stop_distance_frac: Decimal
) -> Decimal:
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


def cost_floor_source(mic: str | None, broker: str | None = None) -> str:
    """Which table the floor actually came from, for output that names it.

    A broker that does not price this venue falls through to the venue's own
    entry, and a finding that still said "60 bps round trip on moomoo_my" would
    be naming an account for a number Bursa supplied. The label is derived from
    the same lookup as the number so the two cannot drift apart.
    """
    if broker is not None and mic is not None:
        from markets.brokers import BROKER_FLOOR_BPS
        from markets.registry import resolve_mic

        if (broker, resolve_mic(mic)) in BROKER_FLOOR_BPS:
            return broker
    return mic or "default"


def cost_floor_unreachable(floor_value: Decimal) -> bool:
    """True when no position of any size satisfies the floor.

    The bisection cannot report this in its return type - it returns a Decimal
    either way - so the caller has to ask. `ask.py` asks before printing a
    number that would otherwise read as "you need USD 100,000,000".
    """
    return floor_value >= COST_FLOOR_CEILING


def cost_floor_value(
    round_trip_cost_at, mic: str | None = None, broker: str | None = None
) -> Decimal:
    """Smallest position whose round-trip cost stays within the market's floor.

    Bisection, because fee schedules have minimums and caps and are not smooth.

    Returns COST_FLOOR_CEILING when the floor is unreachable; ask
    `cost_floor_unreachable` rather than reading that as a position size.
    """
    limit = cost_floor_bps(mic, broker)
    lo, hi = Decimal("1"), COST_FLOOR_CEILING
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
