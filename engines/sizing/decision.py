"""SizingDecision - unconstructable if it breaches a cap.

docs/05 section 3: size = min(five caps) -> volatility scalar -> round DOWN to a
board lot. If lot rounding drops it under the cost floor, the answer is no
position, and the system says why.

binding_cap is surfaced because knowing WHICH constraint bound the size is more
instructive than the number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal

from core.contracts.money import BASE_CURRENCY
from engines.risk.concentration import Breach, Limits, Position, check
from engines.sizing.caps import (
    Band,
    BindingCap,
    CapBreach,
    CapSet,
    CurrencyMismatch,
    cost_floor_bps,
    to_base,
    vol_target_scalar,
)


@dataclass(frozen=True)
class Tranche:
    ordinal: int
    value: Decimal
    not_before: date


@dataclass(frozen=True)
class SizingDecision:
    instrument_id: str
    band: Band
    investable_capital: Decimal
    binding_cap: BindingCap
    target_value: Decimal
    target_units: int
    lot_size: int
    stop_price: Decimal
    thesis_breakers: tuple[str, ...]
    time_stop: date
    tranches: tuple[Tranche, ...] = ()
    notes: tuple[str, ...] = ()
    currency: str = BASE_CURRENCY
    #: `target_value` converted to MYR. Equal to it for a MYR-denominated market,
    #: and the only number that may be compared with another position's, summed
    #: into a portfolio, or shown to the holder of an MYR book.
    target_value_base: Decimal | None = None

    @property
    def base_value(self) -> Decimal:
        """The position in MYR. Falls back to the native value only when the
        market IS MYR, so this never silently returns a foreign number."""
        if self.target_value_base is not None:
            return self.target_value_base
        if self.currency.upper() == BASE_CURRENCY:
            return self.target_value
        raise CurrencyMismatch(
            f"{self.instrument_id} is priced in {self.currency} and no "
            f"{BASE_CURRENCY} value was recorded; there is no rate here to invent one"
        )

    def __post_init__(self) -> None:
        if self.target_units % self.lot_size != 0:
            raise CapBreach(
                f"{self.target_units} units is not a whole multiple of the "
                f"{self.lot_size}-share board lot"
            )
        if self.target_value < 0:
            raise CapBreach("target value cannot be negative")
        if self.band is Band.ACCUMULATE:
            if self.target_units <= 0:
                raise CapBreach("an accumulate band requires a positive size")
            if not self.thesis_breakers:
                raise CapBreach(
                    "a position cannot be constructed without falsifiable thesis breakers "
                    "(docs/04 section 7)"
                )
            if len(self.thesis_breakers) < 2:
                raise CapBreach("docs/04 requires 2-4 breakers, written before entry")


class NoPosition(Exception):
    """Not an error. A legitimate and common outcome."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def size(
    instrument_id: str,
    band: Band,
    investable: Decimal,
    caps: CapSet,
    price: Decimal,
    lot_size: int,
    stop_price: Decimal,
    breakers: tuple[str, ...],
    time_stop: date,
    round_trip_cost_at,
    realised_vol: Decimal = Decimal("0.20"),
    target_vol: Decimal = Decimal("0.20"),
    existing: list[Position] | None = None,
    candidate_meta: dict | None = None,
    limits: Limits | None = None,
    corr: list[list[float]] | None = None,
    mic: str | None = None,
    currency: str = BASE_CURRENCY,
    fx_base_per_quote: Decimal | None = None,
    fx_asof=None,
) -> SizingDecision:
    """`price`, `lot_size` and `round_trip_cost_at` speak the MARKET's currency.

    `investable` speaks MYR, because a book has one currency and it is the
    holder's. Those two facts met in `units = value / price` with nothing
    naming either side, so a USD 200 stock sized against an MYR cap bought 4.2x
    the intended exposure and a JPY name bought a thirty-eighth of it - both
    reported as the cap that bound them.

    The seam is now explicit: `caps` must be denominated in `currency`, the
    whole size computation happens there (it is where lots, ticks and fee
    minimums live), and only the RESULT crosses back into MYR, once, at a rate
    the caller supplies. Cross without a rate and this raises.
    """
    currency = str(currency).upper()
    if caps.currency != currency:
        raise CurrencyMismatch(
            f"caps are in {caps.currency} but {instrument_id} is priced in {currency}. "
            f"Sizing against a cap in another currency is off by the exchange rate and "
            f"produces a position that looks correctly sized."
        )
    if band is not Band.ACCUMULATE:
        return SizingDecision(
            instrument_id,
            band,
            investable,
            BindingCap.NONE,
            Decimal(0),
            0,
            lot_size,
            stop_price,
            breakers,
            time_stop,
            notes=(f"band is {band.value}; no new capital deployed",),
            currency=currency,
            target_value_base=Decimal(0),
        )

    binding, value = caps.binding()
    notes: list[str] = []

    scalar = vol_target_scalar(realised_vol, target_vol)
    if scalar < 1:
        value *= scalar
        notes.append(f"volatility overlay scaled size to {scalar:.0%} (may de-risk, never lever)")

    units = int((value / price).to_integral_value(rounding=ROUND_DOWN))
    lot_units = (units // lot_size) * lot_size
    if lot_units <= 0:
        raise NoPosition(
            f"at {price} per share and a {lot_size}-share lot, the binding "
            f"{binding.value} cap of {value:,.2f} does not fund one lot"
        )

    final_value = Decimal(lot_units) * price
    limit = cost_floor_bps(mic)
    bps = round_trip_cost_at(final_value) / final_value * Decimal(10_000)
    if bps > limit:
        raise NoPosition(
            f"round-trip cost is {bps:.0f} bps on a {final_value:,.2f} position, above the "
            f"{limit} bps floor for {mic or 'this market'}. At this capital level lot "
            f"granularity forces a "
            f"sub-economic size - fewer positions sized properly beats many sized by "
            f"rounding error (docs/05 section 3.4)"
        )

    # Both sides of the weight in MYR. `final_value` is the market's currency and
    # `investable` is the book's; dividing one by the other produced a weight off
    # by the exchange rate, which was then checked against the concentration
    # limits - so the last line of defence was fed the wrong number too.
    final_value_base = to_base(final_value, currency, fx_base_per_quote, fx_asof)

    # The portfolio AFTER this trade must clear every concentration limit.
    if existing is not None and limits is not None:
        meta = candidate_meta or {}
        port_value = investable if investable > 0 else final_value_base
        new_weight = float(final_value_base / port_value)
        risk_base = to_base(
            final_value * abs(price - stop_price) / price,
            currency,
            fx_base_per_quote,
            fx_asof,
        )
        prospective = list(existing) + [
            Position(
                instrument_id,
                new_weight,
                meta.get("sector", "unknown"),
                meta.get("country", "unknown"),
                meta.get("currency", currency),
                risk_to_stop=float(risk_base / port_value),
            )
        ]
        breaches: list[Breach] = check(prospective, corr, limits)
        if breaches:
            raise CapBreach(
                "the portfolio after this trade would breach: "
                + "; ".join(str(b) for b in breaches)
            )

    if lot_units * lot_size and value > 0:
        used = final_value / value
        if used < Decimal("0.75"):
            notes.append(
                f"lot rounding used only {used:.0%} of the binding cap; "
                "size is granularity-limited, not conviction-limited"
            )

    third = final_value / 3
    tranches = tuple(
        Tranche(i + 1, (third if i < 2 else final_value - third * 2), time_stop) for i in range(3)
    )

    return SizingDecision(
        instrument_id,
        band,
        investable,
        binding,
        final_value,
        lot_units,
        lot_size,
        stop_price,
        breakers,
        time_stop,
        tranches,
        tuple(notes),
        currency=currency,
        target_value_base=final_value_base,
    )
