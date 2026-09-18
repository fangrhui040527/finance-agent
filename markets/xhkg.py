"""Hong Kong Exchange (XHKG).

The second T2 market. docs/07 P18 lists HK after Singapore, and the claim under
test is still docs/01 section 10: a new market is one adapter class plus one
registry entry, with no change to any agent, engine or orchestrator. The
conformance suite is parameterised over `supported()`, so this file plus the
registry line subjects HK to every conformance test with no new test code.

Fee schedule per HKEX's published securities schedule, read Aug 2026. VERIFY
BEFORE TRADING - schedules change, and the cost floor in engines/sizing/caps.py
is computed straight from these numbers:
  brokerage        broker-set; 0.25% with a HKD 100 minimum is a common retail tier
  stamp duty       0.1% of consideration, ROUNDED UP to the nearest dollar
  SFC levy         0.0027% of consideration
  FRC levy         0.00015% of consideration
  trading fee      0.00565% of consideration
  CCASS settlement 0.002%, minimum HKD 2, capped HKD 100

Two structural facts that decide whether HK is reachable for a small account:

  1. **Stamp duty is charged on BOTH sides and has no cap.** Bursa's stamp duty
     caps at RM 1,000; Hong Kong's does not cap at all. Round-trip stamp alone is
     20 bps at every size, so HK's asymptotic cost cannot fall below roughly
     26 bps however large the position - an order of magnitude worse than XNAS
     and structurally worse than SGX.
  2. **Lot sizes are per-stock and large.** Unlike SGX's uniform 100, HKEX board
     lots run 100 / 200 / 500 / 1,000 / 2,000 and are set by the issuer. A single
     lot of an expensive name can exceed a small account's entire single-name
     cap, so `lot_size` here is a lookup with a documented default, not a
     constant - and a wrong answer is a silently unfillable order.

The cost floor therefore lands nearer XKLS than XSES, which is the opposite of
what "developed market" would lead you to assume.

Tax: Hong Kong levies no withholding tax on dividends to non-residents, so a
Malaysian holder receives them gross - the same as Bursa, unlike XNAS's 30%.

Point-in-time: HKEXnews publishes announcement timestamps, so known_at is
self-built from them the same way Bursa and SGX are.
"""

from __future__ import annotations

from datetime import time
from decimal import ROUND_CEILING, Decimal

from core.market.calendar import SessionCalendar, SessionWindow
from markets.contract import (
    AccountingStandard,
    FeeLeg,
    FeeSchedule,
    KnownAtStrategy,
    MarketAdapter,
)


class StampDutyHK(FeeLeg):
    """Stamp duty at 0.1%, rounded UP to the next whole dollar.

    The rounding is not a rounding error. On a HKD 3,000 trade the duty is
    HKD 3 rather than HKD 2.60 - and modelling it as a plain rate understates
    cost on every small trade, which is exactly where the cost floor is decided.
    """

    def charge(self, consideration: Decimal, price: Decimal | None = None) -> Decimal:
        # `price` is accepted to keep the leg contract uniform; stamp duty is
        # levied on consideration and never on share count.
        duty = (consideration * self.rate).quantize(Decimal("1"), rounding=ROUND_CEILING)
        return max(duty, self.minimum)


HKEX_FEES = FeeSchedule(
    (
        FeeLeg("brokerage", Decimal("0.0025"), minimum=Decimal("100")),
        StampDutyHK("stamp_duty", Decimal("0.001")),
        FeeLeg("sfc_levy", Decimal("0.000027")),
        FeeLeg("frc_levy", Decimal("0.0000015")),
        FeeLeg("trading_fee", Decimal("0.0000565")),
        FeeLeg("settlement", Decimal("0.00002"), minimum=Decimal("2"), cap=Decimal("100")),
    )
)

#: Board lots are set per issuer. This is the subset the system knows; anything
#: absent falls back to DEFAULT_LOT and says so through `lot_size_is_known`.
BOARD_LOTS: dict[str, int] = {
    "0001": 500,  # CK Hutchison
    "0005": 400,  # HSBC
    "0700": 100,  # Tencent
    "0939": 1000,  # CCB
    "0941": 500,  # China Mobile
    "1299": 200,  # AIA
    "2318": 500,  # Ping An
    "3690": 100,  # Meituan
    "9988": 100,  # Alibaba
}
DEFAULT_LOT = 1000


class XHKG(MarketAdapter):
    mic = "XHKG"
    country = "HK"
    currency = "HKD"
    tier = 2
    accounting_standard = AccountingStandard.IFRS  # HKFRS, IFRS-converged
    local_index = "HSI"
    regulator = "Securities and Futures Commission of Hong Kong"
    settlement_days = 2
    known_at_strategy = KnownAtStrategy.SELF_BUILT

    def __init__(self, holidays=frozenset(), half_days=frozenset(), early_closes=None) -> None:
        # HKEX keeps a lunch break, unlike SGX: 09:30-12:00 and 13:00-16:00.
        self._cal = SessionCalendar(
            windows=(
                SessionWindow(time(9, 30), time(12, 0)),
                SessionWindow(time(13, 0), time(16, 0)),
            ),
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
        return HKEX_FEES

    @staticmethod
    def _code(instrument_id: str) -> str:
        """`XHKG:0700` or `0700` -> `0700`, zero-padded to the 5-digit HKEX form."""
        code = instrument_id.rpartition(":")[2].strip()
        return code.zfill(4) if code.isdigit() else code

    def lot_size_is_known(self, instrument_id: str) -> bool:
        """Whether the lot below is a fact or the default.

        Worth asking before sizing: a wrong board lot produces an order that
        cannot fill, and HKEX lots vary by an order of magnitude between issuers.
        """
        return self._code(instrument_id) in BOARD_LOTS

    def lot_size(self, instrument_id: str) -> int:
        return BOARD_LOTS.get(self._code(instrument_id), DEFAULT_LOT)

    def tick_size(self, price: Decimal) -> Decimal:
        """HKEX's published spread table, abridged to the retail range."""
        if price < Decimal("0.25"):
            return Decimal("0.001")
        if price < Decimal("0.50"):
            return Decimal("0.005")
        if price < Decimal("10"):
            return Decimal("0.01")
        if price < Decimal("20"):
            return Decimal("0.02")
        if price < Decimal("100"):
            return Decimal("0.05")
        if price < Decimal("200"):
            return Decimal("0.10")
        if price < Decimal("500"):
            return Decimal("0.20")
        if price < Decimal("1000"):
            return Decimal("0.50")
        return Decimal("1.00")

    def withholding(self, income_type: str, holder_country: str) -> Decimal:
        return Decimal(0)
