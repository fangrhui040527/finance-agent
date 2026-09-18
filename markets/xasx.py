"""Australian Securities Exchange (XASX).

Fee schedule read Aug 2026. VERIFY BEFORE TRADING.

  brokerage      broker-set; 0.10% with an AUD 10 minimum is a common tier
  clearing       ASX Clear and Settlement fees, borne by the participant and
                 usually folded into brokerage rather than shown separately
  stamp duty     none. Duty on quoted marketable securities was abolished
                 across all states by 2001

Structurally the cheapest of the five markets here for a small account: no
stamp duty, a lot size of one, and a tick that is a flat cent above AUD 2. The
binding constraint on a Bursa-sized account is the brokerage minimum, not the
market.

FRANKING IS THE THING THAT MAKES AUSTRALIA UNUSUAL, and it is why `withholding`
below cannot be a single number. A dividend paid out of Australian-taxed profits
carries franking credits, and a FULLY FRANKED dividend to a non-resident is
withheld at ZERO. An unfranked dividend is withheld at 15% under the
Australia-Malaysia treaty, 30% without it.

This adapter returns the UNFRANKED treaty rate, which is the conservative case:
it overstates what a franked payer costs and never understates it. Franking
percentage is a per-payment fact that belongs in the fundamentals store, not in
a market adapter - the market does not know it, and neither does this class.

Point-in-time: ASX announcements are timestamped, so known_at is self-built.
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

ASX_FEES = FeeSchedule((FeeLeg("brokerage", Decimal("0.0010"), minimum=Decimal("10")),))


class XASX(MarketAdapter):
    mic = "XASX"
    country = "AU"
    currency = "AUD"
    tier = 2
    accounting_standard = AccountingStandard.IFRS  # AASB, IFRS-equivalent
    local_index = "AS51"
    regulator = "Australian Securities and Investments Commission"
    settlement_days = 2
    known_at_strategy = KnownAtStrategy.SELF_BUILT

    def __init__(self, holidays=frozenset(), half_days=frozenset(), early_closes=None) -> None:
        self._cal = SessionCalendar(
            windows=(SessionWindow(time(10, 0), time(16, 0)),),
            tz_offset_hours=10,
            holidays=holidays,
            half_days=half_days,
            early_closes=early_closes,
        )

    @property
    def calendar(self) -> SessionCalendar:
        return self._cal

    @property
    def fee_schedule(self) -> FeeSchedule:
        return ASX_FEES

    def lot_size(self, instrument_id: str) -> int:
        return 1

    def tick_size(self, price: Decimal) -> Decimal:
        if price < Decimal("0.10"):
            return Decimal("0.001")
        if price < Decimal("2"):
            return Decimal("0.005")
        return Decimal("0.01")

    def withholding(self, income_type: str, holder_country: str) -> Decimal:
        if income_type != "dividend":
            return Decimal(0)
        # Unfranked, treaty rate. A fully franked dividend is withheld at zero;
        # franking is a per-payment fact this class cannot see.
        return Decimal("0.15")
