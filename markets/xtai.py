"""Taiwan Stock Exchange (XTAI).

Fee schedule read Aug 2026. VERIFY BEFORE TRADING.

  brokerage           statutory maximum 0.1425%, both sides; discount brokers
                      rebate a large part of it, so this is the dear end
  securities txn tax  0.3% ON THE SELL SIDE ONLY - the defining cost here

TAIWAN'S TAX IS ONE-WAY AND LARGE. 0.3% on disposal is 30 bps that a round trip
pays exactly once, which is why `per_side=False` matters as much here as it does
in London: doubling it would put the floor 30 bps too high and refuse positions
that clear the real one.

Even charged once it is the second most expensive market of the eight, and the
asymmetry has a consequence the fee number alone does not show - the cost is
paid on EXIT. A position that is never closed never pays it, which is a bad
reason to hold and worth naming so nobody discovers it as a feature.

Lot: 1,000 shares on the round-lot board. Taiwan runs an odd-lot session too,
but the round lot is what the sizing engine must assume, and at TWD 500 a share
that is a TWD 500,000 minimum ticket - roughly RM 72,000.

Tax: dividends to a non-resident are withheld at 21%. The Taiwan-Malaysia
agreement provides for 12.5%, claimed rather than automatic; the domestic rate
is returned because it is what is withheld at source.

Point-in-time: TWSE's MOPS carries announcement timestamps, so known_at is
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

TWSE_FEES = FeeSchedule(
    (
        FeeLeg("brokerage", Decimal("0.001425"), minimum=Decimal("20")),
        FeeLeg("securities_transaction_tax", Decimal("0.003"), per_side=False),
    )
)

TICKS = (
    (Decimal("10"), Decimal("0.01")),
    (Decimal("50"), Decimal("0.05")),
    (Decimal("100"), Decimal("0.10")),
    (Decimal("500"), Decimal("0.50")),
    (Decimal("1000"), Decimal("1.00")),
)
TOP_TICK = Decimal("5.00")


class XTAI(MarketAdapter):
    mic = "XTAI"
    country = "TW"
    currency = "TWD"
    tier = 2
    accounting_standard = AccountingStandard.IFRS  # TIFRS, IFRS-converged
    local_index = "TWSE"
    regulator = "Financial Supervisory Commission of Taiwan"
    settlement_days = 2
    known_at_strategy = KnownAtStrategy.SELF_BUILT

    def __init__(self, holidays=frozenset(), half_days=frozenset(), early_closes=None) -> None:
        self._cal = SessionCalendar(
            windows=(SessionWindow(time(9, 0), time(13, 30)),),
            tz_offset_hours=8,
            holidays=holidays,
            half_days=half_days,
            early_closes=early_closes,
        )

    @property
    def calendar(self) -> SessionCalendar:
        return self._cal

    @property
    def fee_schedule(self) -> FeeSchedule:
        return TWSE_FEES

    def lot_size(self, instrument_id: str) -> int:
        return 1000

    def tick_size(self, price: Decimal) -> Decimal:
        for ceiling, tick in TICKS:
            if price < ceiling:
                return tick
        return TOP_TICK

    def withholding(self, income_type: str, holder_country: str) -> Decimal:
        if income_type != "dividend":
            return Decimal(0)
        return Decimal("0.21")
