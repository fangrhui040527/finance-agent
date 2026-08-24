"""NASDAQ (XNAS).

US dividends paid to a Malaysian holder carry 30% withholding - Malaysia has no
US tax treaty rate for portfolio dividends. docs/06 section 2.2: withholding
turns a headline cross-border dividend yield into a different number.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal

from core.market.calendar import SessionCalendar, SessionWindow
from markets.contract import (
    AccountingStandard, FeeLeg, FeeSchedule, KnownAtStrategy, MarketAdapter,
)

# Zero-commission retail brokerage; the SEC fee applies to sells only, but is
# modelled per-side here as a conservative round-trip estimate.
NASDAQ_FEES = FeeSchedule((
    FeeLeg("commission", Decimal("0")),
    FeeLeg("sec_fee", Decimal("0.0000278")),
))


class XNAS(MarketAdapter):
    mic = "XNAS"
    country = "US"
    currency = "USD"
    tier = 1
    accounting_standard = AccountingStandard.US_GAAP
    local_index = "SPX"
    settlement_days = 1
    known_at_strategy = KnownAtStrategy.VENDOR

    def __init__(self, holidays=frozenset(), half_days=frozenset()) -> None:
        self._cal = SessionCalendar(
            windows=(SessionWindow(time(9, 30), time(16, 0)),),
            tz_offset_hours=-5,
            holidays=holidays,
            half_days=half_days,
        )

    @property
    def calendar(self) -> SessionCalendar:
        return self._cal

    @property
    def fee_schedule(self) -> FeeSchedule:
        return NASDAQ_FEES

    def lot_size(self, instrument_id: str) -> int:
        return 1

    def tick_size(self, price: Decimal) -> Decimal:
        return Decimal("0.0001") if price < Decimal("1") else Decimal("0.01")

    def withholding(self, income_type: str, holder_country: str) -> Decimal:
        if income_type == "dividend" and holder_country.upper() != "US":
            return Decimal("0.30")
        return Decimal(0)


REGISTRY = {"XKLS": None, "XNAS": None}
