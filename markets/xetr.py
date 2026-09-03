"""Xetra, Deutsche Boerse (XETR).

Fee schedule read Aug 2026. VERIFY BEFORE TRADING.

  brokerage      broker-set; 0.10% with a EUR 8 minimum is a common tier for a
                 foreign retail account
  Xetra fee      ~0.0018% of turnover, both sides
  no stamp duty, no transaction tax

The cheapest market of the eight after XNAS, and the only European one here
without a transaction tax - Germany has debated a financial transaction tax for
years and has not levied one. That is a fact with a shelf life: if one arrives it
lands on this schedule, and the cost floor below is what would need revisiting.

Lot: 1. Tick sizes follow the MiFID II regime, which sets the tick from BOTH
price and average daily turnover, so two shares at the same price tick
differently by liquidity band. The table here is the coarse end of the
applicable bands, which overstates spread cost for a liquid name and never
understates it - the same simplification XLON makes, for the same reason.

Tax: German withholding on dividends is 26.375% (25% plus solidarity surcharge)
at source. The Germany-Malaysia treaty provides for 10-15%, reclaimed rather
than applied - and German dividend reclaims are notoriously slow. The gross rate
is returned because it is what leaves the account on the payment date, which is
the number a position's return has to survive.

Point-in-time: Bundesanzeiger and DGAP releases are timestamped, so known_at is
self-built.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal

from core.market.calendar import SessionCalendar, SessionWindow
from markets.contract import (
    AccountingStandard,
    FeeLeg,
    FeeSchedule,
    KnownAtStrategy,
    MarketAdapter,
)

XETRA_FEES = FeeSchedule(
    (
        FeeLeg("brokerage", Decimal("0.0010"), minimum=Decimal("8")),
        FeeLeg("exchange", Decimal("0.000018")),
    )
)

TICKS = (
    (Decimal("10"), Decimal("0.001")),
    (Decimal("50"), Decimal("0.005")),
    (Decimal("100"), Decimal("0.01")),
    (Decimal("500"), Decimal("0.05")),
)
TOP_TICK = Decimal("0.10")


class XETR(MarketAdapter):
    mic = "XETR"
    country = "DE"
    currency = "EUR"
    tier = 2
    accounting_standard = AccountingStandard.IFRS  # EU-adopted IFRS
    local_index = "DAX"
    regulator = "Bundesanstalt fuer Finanzdienstleistungsaufsicht"
    settlement_days = 2
    known_at_strategy = KnownAtStrategy.SELF_BUILT

    def __init__(self, holidays=frozenset(), half_days=frozenset()) -> None:
        self._cal = SessionCalendar(
            windows=(SessionWindow(time(9, 0), time(17, 30)),),
            tz_offset_hours=1,  # CET; CEST in summer
            holidays=holidays,
            half_days=half_days,
        )

    @property
    def calendar(self) -> SessionCalendar:
        return self._cal

    @property
    def fee_schedule(self) -> FeeSchedule:
        return XETRA_FEES

    def lot_size(self, instrument_id: str) -> int:
        return 1

    def tick_size(self, price: Decimal) -> Decimal:
        for ceiling, tick in TICKS:
            if price < ceiling:
                return tick
        return TOP_TICK

    def withholding(self, income_type: str, holder_country: str) -> Decimal:
        if income_type != "dividend":
            return Decimal(0)
        return Decimal("0.26375")
