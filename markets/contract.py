"""The market adapter contract.

docs/06 section 2. Adding a market is filling this in and passing the seven
conformance tests. No orchestrator code changes - that is the whole point of the
L2 growth layer.

Every field is here because getting it wrong produces a silent, plausible-looking
error rather than a crash.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from core.market.calendar import SessionCalendar


class AccountingStandard(str, Enum):
    IFRS = "IFRS"
    US_GAAP = "US_GAAP"
    LOCAL = "LOCAL"


class KnownAtStrategy(str, Enum):
    VENDOR = "vendor"
    SELF_BUILT = "self_built"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class FeeLeg:
    """One charge. rate is a fraction; cap/minimum are in market currency."""

    name: str
    rate: Decimal = Decimal(0)
    minimum: Decimal = Decimal(0)
    cap: Decimal | None = None
    per_side: bool = True
    """False for a charge levied on ONE leg only.

    UK Stamp Duty Reserve Tax is the case that matters: 0.5% on purchases and
    nothing on sales. Every other charge in this repository is symmetric, which
    is why this field sat declared and unread until London arrived - and why
    round_trip used to double everything unconditionally.
    """

    per_share: Decimal = Decimal(0)
    """Charged per SHARE, so it depends on the price as well as the value.

    US settlement, trading-activity and audit-trail fees are quoted this way.
    A leg carrying one refuses to be costed without a price rather than
    contributing zero: an understated floor is still a number, and it funds
    positions that cannot pay for their own round trip.
    """

    flat: Decimal = Decimal(0)
    """Charged once per order, whatever the size.

    The shape that decides whether a small order is worth placing at all. A
    USD 0.99 platform fee is 198 bps round trip on a USD 100 position and 2 bps
    on a USD 10,000 one; no rate-only schedule can express that.
    """

    def charge(self, consideration: Decimal, price: Decimal | None = None) -> Decimal:
        amt = consideration * self.rate + self.flat
        if self.per_share:
            if price is None or price <= 0:
                raise ValueError(
                    f"fee leg {self.name!r} is charged per share and cannot be costed "
                    f"from a consideration alone; pass the price so the share count "
                    f"is known"
                )
            amt += (consideration / price) * self.per_share
        if self.cap is not None:
            amt = min(amt, self.cap)
        return max(amt, self.minimum)


@dataclass(frozen=True)
class FeeSchedule:
    legs: tuple[FeeLeg, ...]

    def one_side(self, consideration: Decimal, price: Decimal | None = None) -> Decimal:
        """Everything a single trade pays, one-way legs included - what a buy costs."""
        return sum((leg.charge(consideration, price) for leg in self.legs), Decimal(0))

    def one_way(self, consideration: Decimal, price: Decimal | None = None) -> Decimal:
        """Charges levied on a single leg only - the buy side, by convention."""
        return sum(
            (leg.charge(consideration, price) for leg in self.legs if not leg.per_side),
            Decimal(0),
        )

    def round_trip(self, consideration: Decimal, price: Decimal | None = None) -> Decimal:
        """docs/04 section 6.3: cost is computed before any signal is discussed.

        Symmetric legs are charged twice; one-way legs once. Doubling a buy-only
        stamp duty overstates the cost floor, which sounds conservative and is
        not: an overstated floor refuses positions that would have cleared it.
        """
        return sum(
            (leg.charge(consideration, price) for leg in self.legs if leg.per_side), Decimal(0)
        ) * 2 + self.one_way(consideration, price)

    def round_trip_bps(self, consideration: Decimal, price: Decimal | None = None) -> Decimal:
        if consideration <= 0:
            return Decimal(0)
        return self.round_trip(consideration, price) / consideration * Decimal(10_000)


class MarketAdapter(ABC):
    mic: str
    country: str
    currency: str
    tier: int
    accounting_standard: AccountingStandard
    local_index: str
    #: The securities regulator whose rules bind an issuer on this market. On
    #: the ABC rather than a lookup table so a market added later cannot forget
    #: it, exactly as country and currency already work.
    regulator: str
    settlement_days: int
    known_at_strategy: KnownAtStrategy

    @property
    @abstractmethod
    def calendar(self) -> SessionCalendar: ...

    @property
    @abstractmethod
    def fee_schedule(self) -> FeeSchedule: ...

    @abstractmethod
    def lot_size(self, instrument_id: str) -> int: ...

    @abstractmethod
    def tick_size(self, price: Decimal) -> Decimal: ...

    @abstractmethod
    def withholding(self, income_type: str, holder_country: str) -> Decimal: ...

    def lot_round_down(self, units: int, instrument_id: str) -> int:
        lot = self.lot_size(instrument_id)
        return (units // lot) * lot

    def supports_factor_model(self) -> bool:
        """docs/06 section 1: tier 3 returns attribution_unavailable for style
        components rather than inventing a factor model that does not exist."""
        return self.tier <= 2
