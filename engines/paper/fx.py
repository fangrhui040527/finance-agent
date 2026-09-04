"""USD/MYR for a USD-based book, from the rate the collector recorded.

`core/market/fxlog.py` stores the BNM reference rate as MYR per one USD, one
row per published day, exact-date lookups only. The book needs the rate *as of*
a day (the last published on or before it) and it needs to say which rate it
used: `bnm` when the log had one, `config` when it fell back to the number in
config.toml. A converted figure that cannot name its rate is a guess.

The spread is `Config.fx_spread_per_side` (0.5% by default): the book pays
`rate * (1 - spread)` USD-per-MYR buying ringgit and receives at
`rate * (1 + spread)` selling it. Marks use the mid, and say so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

CENT = Decimal("0.01")


@dataclass(frozen=True)
class FxQuote:
    rate: Decimal  # MYR per 1 USD
    rate_date: date
    source: str  # bnm | config


class UsdMyr:
    def __init__(self, fx_db: str | Path | None, fallback: Decimal, spread: Decimal) -> None:
        self.fx_db = str(fx_db) if fx_db else None
        self.fallback = Decimal(str(fallback))
        self.spread = Decimal(str(spread))
        self._rows: list[tuple[date, Decimal]] | None = None

    def _load(self) -> list[tuple[date, Decimal]]:
        if self._rows is not None:
            return self._rows
        rows: list[tuple[date, Decimal]] = []
        if self.fx_db and (self.fx_db == ":memory:" or Path(self.fx_db).exists()):
            from core.market.fxlog import FxLog

            with FxLog(self.fx_db) as log:
                for r in log.history("USD", limit=10_000):
                    rows.append((date.fromisoformat(r["rate_date"]), Decimal(str(r["rate"]))))
        rows.sort()
        self._rows = rows
        return rows

    def asof(self, day: date) -> FxQuote:
        """The last published rate on or before `day`; the config number if none."""
        best: tuple[date, Decimal] | None = None
        for d, rate in self._load():
            if d <= day:
                best = (d, rate)
            else:
                break
        if best is None:
            return FxQuote(self.fallback, day, "config")
        return FxQuote(best[1], best[0], "bnm")

    # -- conversions ----------------------------------------------------------------------

    @staticmethod
    def myr_to_usd_mid(myr: Decimal, q: FxQuote) -> Decimal:
        return myr / q.rate

    @staticmethod
    def usd_to_myr_mid(usd: Decimal, q: FxQuote) -> Decimal:
        return usd * q.rate

    def usd_needed_to_buy_myr(self, myr: Decimal, q: FxQuote) -> Decimal:
        """Buying ringgit: the bank sells it dearer than mid."""
        return myr / (q.rate * (Decimal(1) - self.spread))

    def usd_received_for_myr(self, myr: Decimal, q: FxQuote) -> Decimal:
        """Selling ringgit: the bank buys it cheaper than mid."""
        return myr / (q.rate * (Decimal(1) + self.spread))
