"""Korea Exchange (XKRX).

Fee schedule read Aug 2026. VERIFY BEFORE TRADING.

  brokerage       broker-set; 0.15% with a KRW 1,000 minimum is a foreign-access
                  tier. Korean domestic brokers charge far less
  securities txn  0.15% ON THE SELL SIDE ONLY for KOSPI (0.20% KOSDAQ); the
                  KOSPI rate is used, which is the cheaper of the two
  no stamp duty

Like Taiwan, the transaction tax is one-way and paid on exit. Unlike Taiwan it
is half the size, so Korea lands mid-table rather than near the top.

WHAT MAKES KOREA AWKWARD IS NOT COST. Korea has historically restricted short
selling outright for extended periods, and foreign investors have at times been
subject to an investor-registration requirement. Neither is a fee and neither is
modelled here - the adapter describes what a trade costs, not whether the trade
is permitted. Check before assuming a stance is available.

Lot: 1 share. Korea moved to single-share trading on both boards, which makes it
reachable for a small account despite high per-share prices.

Tax: dividends to a non-resident are withheld at 22% (20% plus local surtax).
The Korea-Malaysia treaty provides for 10-15% depending on holding; the domestic
rate is returned because it is what is withheld at source.

Point-in-time: DART carries filing timestamps, so known_at is self-built.
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

KRX_FEES = FeeSchedule(
    (
        FeeLeg("brokerage", Decimal("0.0015"), minimum=Decimal("1000")),
        FeeLeg("securities_transaction_tax", Decimal("0.0015"), per_side=False),
    )
)

TICKS = (
    (Decimal("2000"), Decimal("1")),
    (Decimal("5000"), Decimal("5")),
    (Decimal("20000"), Decimal("10")),
    (Decimal("50000"), Decimal("50")),
    (Decimal("200000"), Decimal("100")),
    (Decimal("500000"), Decimal("500")),
)
TOP_TICK = Decimal("1000")


class XKRX(MarketAdapter):
    mic = "XKRX"
    country = "KR"
    currency = "KRW"
    tier = 2
    accounting_standard = AccountingStandard.IFRS  # K-IFRS
    local_index = "KOSPI"
    regulator = "Financial Services Commission of Korea"
    settlement_days = 2
    known_at_strategy = KnownAtStrategy.SELF_BUILT

    def __init__(self, holidays=frozenset(), half_days=frozenset()) -> None:
        self._cal = SessionCalendar(
            windows=(SessionWindow(time(9, 0), time(15, 30)),),
            tz_offset_hours=9,
            holidays=holidays,
            half_days=half_days,
        )

    @property
    def calendar(self) -> SessionCalendar:
        return self._cal

    @property
    def fee_schedule(self) -> FeeSchedule:
        return KRX_FEES

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
        return Decimal("0.22")
