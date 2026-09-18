"""London Stock Exchange (XLON).

Fee schedule read Aug 2026. VERIFY BEFORE TRADING.

  brokerage      broker-set; 0.10% with a GBP 8 minimum is a common tier for a
                 foreign retail account
  stamp duty     Stamp Duty Reserve Tax, 0.5%, ON PURCHASES ONLY
  PTM levy       GBP 1.00 flat on consideration above GBP 10,000, both ways

TWO TRAPS, and each one is the kind that produces a confidently wrong number
rather than an error.

  1. SDRT IS ONE-WAY. Every other charge in this repository is symmetric, and
     FeeSchedule.round_trip used to double all of them unconditionally - the
     `per_side` field was declared and never read. London is the first market
     where that is wrong, and doubling a buy-only duty overstates the cost floor
     by 50 bps. An overstated floor sounds conservative and is not: it refuses
     positions that would in fact have cleared it.

     SDRT is also not universal. AIM-quoted shares are exempt, and so is any
     non-UK-incorporated issuer with an LSE listing. The 0.5% below is the
     MAIN-MARKET UK-INCORPORATED case, which is the expensive one; an AIM name
     is cheaper than this adapter says, never dearer.

  2. LSE QUOTES IN PENCE (GBX), and this adapter works in POUNDS. A feed handing
     over 2,750 for a GBP 27.50 share, unconverted, produces a position a
     hundred times too large and a tick size a hundred times too coarse, and
     nothing in the pipeline would notice - both numbers stay finite and
     plausible. Any price reaching lot_size or tick_size must already be in
     pounds. tick_size returns sub-penny values in GBP for exactly this reason:
     if a caller is passing pence, the ticks it gets back will be absurdly fine
     rather than quietly reasonable.

Tick sizes here are a simplification. MiFID II sets the tick from BOTH price and
average daily transactions, so two stocks at the same price tick differently by
liquidity band. The table below is the coarse end of the applicable bands, which
overstates spread cost for a liquid name and never understates it.

Tax: the UK withholds NOTHING on dividends - the one market of the five here
where a Malaysian holder loses nothing at source.

Point-in-time: RNS carries a published timestamp on every regulatory
announcement, so known_at is self-built.
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

#: PTM applies above GBP 10,000. Modelling it as a flat minimum on every trade
#: overstates a small ticket by GBP 1 and is the conservative direction; the
#: alternative is a threshold the FeeLeg contract cannot express.
LSE_FEES = FeeSchedule(
    (
        FeeLeg("brokerage", Decimal("0.0010"), minimum=Decimal("8")),
        FeeLeg("ptm_levy", Decimal(0), minimum=Decimal("1")),
        FeeLeg("stamp_duty_reserve_tax", Decimal("0.005"), per_side=False),
    )
)

#: Price in POUNDS -> tick in POUNDS. See trap 2 in the module docstring.
TICKS = (
    (Decimal("0.50"), Decimal("0.0001")),
    (Decimal("1"), Decimal("0.0002")),
    (Decimal("5"), Decimal("0.0005")),
    (Decimal("10"), Decimal("0.001")),
    (Decimal("50"), Decimal("0.002")),
    (Decimal("100"), Decimal("0.005")),
)
TOP_TICK = Decimal("0.01")


class XLON(MarketAdapter):
    mic = "XLON"
    country = "GB"
    currency = "GBP"
    tier = 2
    accounting_standard = AccountingStandard.IFRS  # UK-adopted IFRS
    local_index = "UKX"
    regulator = "Financial Conduct Authority"
    settlement_days = 2
    known_at_strategy = KnownAtStrategy.SELF_BUILT

    def __init__(self, holidays=frozenset(), half_days=frozenset(), early_closes=None) -> None:
        # One continuous session; no lunch break.
        self._cal = SessionCalendar(
            windows=(SessionWindow(time(8, 0), time(16, 30)),),
            tz_offset_hours=0,
            holidays=holidays,
            half_days=half_days,
            early_closes=early_closes,
        )

    @property
    def calendar(self) -> SessionCalendar:
        return self._cal

    @property
    def fee_schedule(self) -> FeeSchedule:
        return LSE_FEES

    def lot_size(self, instrument_id: str) -> int:
        return 1

    def tick_size(self, price: Decimal) -> Decimal:
        for ceiling, tick in TICKS:
            if price < ceiling:
                return tick
        return TOP_TICK

    def withholding(self, income_type: str, holder_country: str) -> Decimal:
        return Decimal(0)
