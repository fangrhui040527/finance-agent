"""NASDAQ (XNAS).

US dividends paid to a Malaysian holder carry 30% withholding - Malaysia has no
US tax treaty rate for portfolio dividends. docs/06 section 2.2: withholding
turns a headline cross-border dividend yield into a different number.

Sessions: one continuous window, 09:30-16:00 New York. New York keeps daylight
saving, so the close is 20:00Z from the second Sunday of March to the first
Sunday of November and 21:00Z otherwise. The calendar is built on the named
zone rather than an offset because a fixed -5 put the modelled close an hour
late for eight months of the year, and a settled bar fetched in that hour read
as provisional. The offset is still given: it is what the calendar runs on
where the machine has no zone database (see `core.market.calendar._zone`).

Closures and the 13:00 early closes come from `markets/holidays.py` by way of
the registry; NYSE and Nasdaq keep the same calendar.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, time
from decimal import Decimal

from core.market.calendar import SessionCalendar, SessionWindow
from markets.contract import (
    AccountingStandard,
    FeeLeg,
    FeeSchedule,
    KnownAtStrategy,
    MarketAdapter,
)

# Zero-commission retail brokerage; the SEC fee applies to sells only, but is
# modelled per-side here as a conservative round-trip estimate.
NASDAQ_FEES = FeeSchedule(
    (
        FeeLeg("commission", Decimal("0")),
        FeeLeg("sec_fee", Decimal("0.0000278")),
    )
)


class XNAS(MarketAdapter):
    mic = "XNAS"
    country = "US"
    currency = "USD"
    tier = 1
    accounting_standard = AccountingStandard.US_GAAP
    local_index = "SPX"
    regulator = "US Securities and Exchange Commission"
    settlement_days = 1
    known_at_strategy = KnownAtStrategy.VENDOR

    def __init__(
        self,
        holidays: frozenset[date] = frozenset(),
        half_days: frozenset[date] = frozenset(),
        early_closes: Mapping[date, time] | None = None,
    ) -> None:
        self._cal = SessionCalendar(
            windows=(SessionWindow(time(9, 30), time(16, 0)),),
            tz_offset_hours=-5,  # standard time; the fallback only, see the module docstring
            holidays=holidays,
            half_days=half_days,
            tz="America/New_York",
            early_closes=early_closes,
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
