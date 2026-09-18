"""Tokyo Stock Exchange (XTKS).

Fee schedule and microstructure read Aug 2026. VERIFY BEFORE TRADING - exchange
schedules change, and the cost floor in docs/05 is computed straight from these
numbers.

  brokerage      broker-set. Japanese retail has largely gone to zero on
                 domestic cash equities (SBI and Rakuten removed commissions on
                 domestic stocks in 2023), but a MALAYSIAN holder reaches Tokyo
                 through an international broker, not a Japanese one, so the
                 0.20% / JPY 1,500 minimum below is a foreign-access tier and
                 NOT the domestic zero. Using the domestic number here would
                 understate the floor for the only person who will trade it.
  exchange       no separate per-trade exchange charge to the client; the
                 trading participant fee is borne by the member
  stamp duty     none on share transfers

WHAT MAKES TOKYO DIFFERENT, and both facts bind on a small account:

  1. The tick table is coarse and PRICE-DEPENDENT in a way no other market here
     is. A JPY 4,000 stock ticks in JPY 5 - 12.5 bps per tick, against roughly
     0.5 bps for a USD 200 US stock. Spread cost dominates fees, and the sizing
     engine's cost floor only sees fees, so Tokyo's real minimum position is
     worse than its fee schedule implies.
  2. The lot is 100 shares, standardised across all domestic stocks in October
     2018. A JPY 4,000 share is a JPY 400,000 minimum ticket - roughly RM 12,000
     at 33 JPY/MYR, before any cap in engines/sizing applies.

Session: TSE extended the afternoon close from 15:00 to 15:30 on 5 November
2024, its first change in seventy years, and kept the lunch break.

Tax: dividends on listed shares pay 15.315% to a non-resident (15% plus the
2.1% reconstruction surtax). The Japan-Malaysia treaty provides for relief, but
relief is claimed rather than automatic - the rate below is what is actually
withheld at source.

Point-in-time: TDnet timestamps every disclosure, so known_at is self-built the
same way Bursa and SGX are.
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

TSE_FEES = FeeSchedule((FeeLeg("brokerage", Decimal("0.0020"), minimum=Decimal("1500")),))

#: The standard table, for stocks outside TOPIX100. TOPIX100 constituents trade
#: on a finer table (down to JPY 0.1 below JPY 1,000), so a large-cap name is
#: CHEAPER to cross than this says. Erring coarse understates nothing.
TICKS = (
    (Decimal("3000"), Decimal("1")),
    (Decimal("5000"), Decimal("5")),
    (Decimal("30000"), Decimal("10")),
    (Decimal("50000"), Decimal("50")),
    (Decimal("300000"), Decimal("100")),
    (Decimal("500000"), Decimal("500")),
    (Decimal("3000000"), Decimal("1000")),
    (Decimal("5000000"), Decimal("5000")),
    (Decimal("30000000"), Decimal("10000")),
    (Decimal("50000000"), Decimal("50000")),
)
TOP_TICK = Decimal("100000")


class XTKS(MarketAdapter):
    mic = "XTKS"
    country = "JP"
    currency = "JPY"
    tier = 2
    #: Japanese GAAP is what most listed issuers still file. IFRS and US GAAP are
    #: both permitted, so a per-issuer override belongs in the fundamentals
    #: store, not here - LOCAL is the honest default for the market.
    accounting_standard = AccountingStandard.LOCAL
    local_index = "TPX"
    regulator = "Financial Services Agency of Japan"
    settlement_days = 2
    known_at_strategy = KnownAtStrategy.SELF_BUILT

    def __init__(self, holidays=frozenset(), half_days=frozenset(), early_closes=None) -> None:
        self._cal = SessionCalendar(
            windows=(
                SessionWindow(time(9, 0), time(11, 30)),
                SessionWindow(time(12, 30), time(15, 30)),
            ),
            tz_offset_hours=9,
            holidays=holidays,
            half_days=half_days,
            early_closes=early_closes,
        )

    @property
    def calendar(self) -> SessionCalendar:
        return self._cal

    @property
    def fee_schedule(self) -> FeeSchedule:
        return TSE_FEES

    def lot_size(self, instrument_id: str) -> int:
        return 100

    def tick_size(self, price: Decimal) -> Decimal:
        for ceiling, tick in TICKS:
            if price < ceiling:
                return tick
        return TOP_TICK

    def withholding(self, income_type: str, holder_country: str) -> Decimal:
        if income_type != "dividend":
            return Decimal(0)
        return Decimal("0.15315")
