"""Singapore Exchange (XSES).

The first T2 market, and the test of the claim in docs/01 section 10: a new
market is one adapter class plus one registry entry, with no change to any
agent, engine or orchestrator. Nothing outside this file and `registry.py`
changed to add it.

Fee schedule per SGX's published securities schedule, read Aug 2026. VERIFY
BEFORE TRADING - exchange schedules change, and the cost floor in docs/05 is
computed straight from these numbers:
  clearing fee   0.0325% of contract value, capped SGD 600 per contract
  trading fee    0.0075% of contract value
  stamp duty     none on scripless trades (it applies to physical scrip transfer)
  brokerage      broker-set; 0.08% with a SGD 10 minimum is a common retail tier

Two things make Singapore structurally cheaper than Bursa for a small account:
there is no stamp duty, and the clearing cap is per contract rather than the
RM 1,000 that only binds above roughly RM 3.3m. The cost floor therefore lands
far closer to XNAS than to XKLS.

Tax: Singapore's one-tier corporate system means dividends are paid out of taxed
profits and are not further taxed in the holder's hands - so withholding is zero
for a Malaysian holder, unlike the 30% XNAS applies.

Point-in-time: SGXNET announcement timestamps are published, so known_at is
self-built from them the same way Bursa is.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal

from core.market.calendar import SessionCalendar, SessionWindow
from markets.contract import (
    AccountingStandard, FeeLeg, FeeSchedule, KnownAtStrategy, MarketAdapter,
)

SGX_FEES = FeeSchedule((
    FeeLeg("brokerage", Decimal("0.0008"), minimum=Decimal("10")),
    FeeLeg("clearing", Decimal("0.000325"), cap=Decimal("600")),
    FeeLeg("trading", Decimal("0.000075")),
))


class XSES(MarketAdapter):
    mic = "XSES"
    country = "SG"
    currency = "SGD"
    tier = 2
    accounting_standard = AccountingStandard.IFRS   # SFRS(I), IFRS-converged
    local_index = "STI"
    regulator = "Monetary Authority of Singapore"
    settlement_days = 2
    known_at_strategy = KnownAtStrategy.SELF_BUILT

    def __init__(self, holidays=frozenset(), half_days=frozenset()) -> None:
        # SGX removed the securities-market lunch break in 2011: one continuous
        # session, unlike Bursa's two.
        self._cal = SessionCalendar(
            windows=(SessionWindow(time(9, 0), time(17, 0)),),
            tz_offset_hours=8,
            holidays=holidays,
            half_days=half_days,
        )

    @property
    def calendar(self) -> SessionCalendar:
        return self._cal

    @property
    def fee_schedule(self) -> FeeSchedule:
        return SGX_FEES

    def lot_size(self, instrument_id: str) -> int:
        # Cut from 1,000 to 100 in 2015, which is what made SGX reachable for a
        # retail account at all.
        return 100

    def tick_size(self, price: Decimal) -> Decimal:
        if price < Decimal("0.20"):
            return Decimal("0.001")
        if price < Decimal("1"):
            return Decimal("0.005")
        return Decimal("0.01")

    def withholding(self, income_type: str, holder_country: str) -> Decimal:
        return Decimal(0)
