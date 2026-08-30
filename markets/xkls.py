"""Bursa Malaysia (XKLS).

Fee schedule per docs/04 section 6.3 and docs/09 section 11, checked Aug 2026:
  clearing fee  0.03%, capped RM 1,000, each side
  stamp duty    0.1% (RM 1 per RM 1,000), capped RM 1,000, each side, to end-2026
  brokerage     typically 0.1%, minimum around RM 8, each side

Those minimums are what make small positions uneconomic - which is exactly what
the cost-floor cap in docs/05 section 3 exists to catch.

Point-in-time: no vendor supplies known_at for Bursa. Self-built from
announcement dates (docs/06 section 3.2).
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal

from core.market.calendar import SessionCalendar, SessionWindow
from markets.contract import (
    AccountingStandard, FeeLeg, FeeSchedule, KnownAtStrategy, MarketAdapter,
)

BURSA_FEES = FeeSchedule((
    FeeLeg("brokerage", Decimal("0.001"), minimum=Decimal("8")),
    FeeLeg("clearing", Decimal("0.0003"), cap=Decimal("1000")),
    FeeLeg("stamp_duty", Decimal("0.001"), cap=Decimal("1000")),
))


class XKLS(MarketAdapter):
    mic = "XKLS"
    country = "MY"
    currency = "MYR"
    tier = 1
    accounting_standard = AccountingStandard.IFRS
    local_index = "FBMKLCI"
    regulator = "Securities Commission Malaysia"
    settlement_days = 2
    known_at_strategy = KnownAtStrategy.SELF_BUILT

    def __init__(self, holidays=frozenset(), half_days=frozenset()) -> None:
        self._cal = SessionCalendar(
            windows=(
                SessionWindow(time(9, 0), time(12, 30)),
                SessionWindow(time(14, 30), time(16, 45)),
            ),
            tz_offset_hours=8,
            holidays=holidays,
            half_days=half_days,
        )

    @property
    def calendar(self) -> SessionCalendar:
        return self._cal

    @property
    def fee_schedule(self) -> FeeSchedule:
        return BURSA_FEES

    def lot_size(self, instrument_id: str) -> int:
        return 100

    def tick_size(self, price: Decimal) -> Decimal:
        if price < Decimal("1"):
            return Decimal("0.005")
        if price < Decimal("10"):
            return Decimal("0.01")
        return Decimal("0.02")

    def withholding(self, income_type: str, holder_country: str) -> Decimal:
        # Malaysia operates a single-tier system: dividends are not further taxed.
        return Decimal(0)
