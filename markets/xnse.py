"""National Stock Exchange of India (XNSE).

Fee schedule read Aug 2026. VERIFY BEFORE TRADING.

  brokerage       broker-set; 0.10% with an INR 20 minimum is a common tier, and
                  Indian discount brokers commonly cap at a flat INR 20
  STT             Securities Transaction Tax, 0.1% on delivery trades EACH SIDE
  exchange txn    ~0.00297% of turnover
  SEBI turnover   0.0001%
  stamp duty      0.015% on the BUY SIDE ONLY
  GST             18% on brokerage plus exchange charges - not modelled as a leg
                  because it is a tax ON the fees, not on the consideration, and
                  FeeLeg cannot express that. It adds roughly 2 bps at this
                  brokerage rate, so the floor below carries it as headroom.

INDIA IS THE MOST TAXED MARKET HERE. STT alone is 20 bps round trip, before
brokerage - the only market of the eight where a transaction tax applies at full
rate on both legs with no cap. Hong Kong's stamp duty is 0.1% both ways too, but
Hong Kong has no separate turnover tax on top.

FOREIGN ACCESS IS THE REAL CONSTRAINT, and it is not a fee. A Malaysian retail
holder cannot simply open an NSE account: portfolio investment by a non-resident
runs through the FPI regime, which requires registration with a designated
depository participant. The adapter models the market's mechanics; it does not
model whether you can reach it. Check before assuming a position here is
available to you at all.

Tax: dividends to a non-resident are withheld at 20% plus surcharge and cess
under domestic law; the India-Malaysia treaty provides for 5%, claimed rather
than automatic. The domestic rate is returned - it is what is withheld at source.

Point-in-time: NSE and BSE corporate announcements are timestamped, so known_at
is self-built.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal

from core.market.calendar import SessionCalendar, SessionWindow
from markets.contract import (
    AccountingStandard, FeeLeg, FeeSchedule, KnownAtStrategy, MarketAdapter,
)

NSE_FEES = FeeSchedule((
    FeeLeg("brokerage", Decimal("0.0010"), minimum=Decimal("20"), cap=Decimal("20")),
    FeeLeg("securities_transaction_tax", Decimal("0.001")),
    FeeLeg("exchange_transaction", Decimal("0.0000297")),
    FeeLeg("sebi_turnover", Decimal("0.000001")),
    FeeLeg("stamp_duty", Decimal("0.00015"), per_side=False),
))


class XNSE(MarketAdapter):
    mic = "XNSE"
    country = "IN"
    currency = "INR"
    tier = 2
    accounting_standard = AccountingStandard.LOCAL     # Ind AS, IFRS-converged
    local_index = "NIFTY"
    regulator = "Securities and Exchange Board of India"
    settlement_days = 1                                 # T+1 since Jan 2023
    known_at_strategy = KnownAtStrategy.SELF_BUILT

    def __init__(self, holidays=frozenset(), half_days=frozenset()) -> None:
        self._cal = SessionCalendar(
            windows=(SessionWindow(time(9, 15), time(15, 30)),),
            tz_offset_hours=5,          # IST is UTC+5:30; the half hour is lost
            holidays=holidays,          # here and matters only for intraday work
            half_days=half_days,
        )

    @property
    def calendar(self) -> SessionCalendar:
        return self._cal

    @property
    def fee_schedule(self) -> FeeSchedule:
        return NSE_FEES

    def lot_size(self, instrument_id: str) -> int:
        return 1                        # cash equities trade in single shares

    def tick_size(self, price: Decimal) -> Decimal:
        return Decimal("0.05")          # flat, across the cash segment

    def withholding(self, income_type: str, holder_country: str) -> Decimal:
        if income_type != "dividend":
            return Decimal(0)
        return Decimal("0.20")
