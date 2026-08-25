"""Price bars, corporate actions, FX.

docs/06 section 4.1 rule 3: adjustment factors are applied at READ and the raw
series is kept. Storing adjusted prices means history silently rewrites itself
the next time a split or dividend lands.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


class ActionKind(str, Enum):
    SPLIT = "split"
    DIVIDEND = "dividend"


@dataclass(frozen=True)
class CorporateAction:
    ex_date: date
    kind: ActionKind
    ratio: float = 1.0      # split: 2.0 means 2-for-1
    amount: float = 0.0     # dividend per share, in instrument currency


class PriceSeries:
    """Raw bars in, adjusted bars out. The raw store is never mutated."""

    def __init__(self, instrument_id: str, bars: list[Bar], actions: list[CorporateAction] | None = None):
        self.instrument_id = instrument_id
        self._raw = sorted(bars, key=lambda b: b.day)
        self._days = [b.day for b in self._raw]
        self.actions = sorted(actions or [], key=lambda a: a.ex_date)

    def __len__(self) -> int:
        return len(self._raw)

    def raw(self) -> list[Bar]:
        return list(self._raw)

    def _factor_at(self, d: date) -> float:
        """Cumulative back-adjustment factor for a bar on day d."""
        f = 1.0
        for a in self.actions:
            if a.ex_date > d:
                if a.kind is ActionKind.SPLIT:
                    f /= a.ratio
                elif a.kind is ActionKind.DIVIDEND and a.amount:
                    # Approximate: scale by (1 - dividend/close on the prior bar).
                    prior = self._close_before(a.ex_date)
                    if prior:
                        f *= 1.0 - (a.amount / prior)
        return f

    def _close_before(self, d: date) -> float | None:
        i = bisect.bisect_left(self._days, d) - 1
        return self._raw[i].close if i >= 0 else None

    def adjusted(self) -> list[Bar]:
        out = []
        for b in self._raw:
            f = self._factor_at(b.day)
            out.append(Bar(b.day, b.open * f, b.high * f, b.low * f, b.close * f,
                           b.volume / f if f else b.volume))
        return out

    def closes(self, adjusted: bool = True) -> list[float]:
        return [b.close for b in (self.adjusted() if adjusted else self._raw)]

    def returns(self, adjusted: bool = True) -> list[float]:
        c = self.closes(adjusted)
        return [(c[i] / c[i - 1]) - 1.0 for i in range(1, len(c))]

    def slice(self, start: date, end: date) -> "PriceSeries":
        lo = bisect.bisect_left(self._days, start)
        hi = bisect.bisect_right(self._days, end)
        return PriceSeries(self.instrument_id, self._raw[lo:hi], self.actions)

    def adv(self, n: int = 20) -> float:
        """Average daily VALUE traded over the last n bars (not share count)."""
        tail = self._raw[-n:]
        return sum(b.close * b.volume for b in tail) / len(tail) if tail else 0.0

    def atr(self, n: int = 20) -> float:
        """Average true range. docs/05: stops in ATR multiples, not round percentages."""
        bars = self.adjusted()
        if len(bars) < 2:
            return 0.0
        trs = []
        for i in range(1, len(bars)):
            b, p = bars[i], bars[i - 1]
            trs.append(max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)))
        tail = trs[-n:]
        return sum(tail) / len(tail) if tail else 0.0


class FxStore:
    """Explicit rates only. There is no implicit global rate lookup anywhere."""

    def __init__(self) -> None:
        self._rates: dict[tuple[str, str], list[tuple[date, Decimal]]] = {}

    def add(self, base: str, quote: str, d: date, rate: Decimal) -> None:
        key = (base.upper(), quote.upper())
        series = self._rates.setdefault(key, [])
        series.append((d, rate))
        series.sort(key=lambda x: x[0])

    def rate_asof(self, base: str, quote: str, d: date) -> tuple[Decimal, date] | None:
        base, quote = base.upper(), quote.upper()
        if base == quote:
            return Decimal(1), d
        series = self._rates.get((base, quote))
        if series:
            days = [x[0] for x in series]
            i = bisect.bisect_right(days, d) - 1
            if i >= 0:
                return series[i][1], series[i][0]
        inv = self._rates.get((quote, base))
        if inv:
            days = [x[0] for x in inv]
            i = bisect.bisect_right(days, d) - 1
            if i >= 0 and inv[i][1] != 0:
                return Decimal(1) / inv[i][1], inv[i][0]
        return None


def base_currency_return(local_return: float, fx_return: float) -> float:
    """docs/03 section 2.4: returns compound multiplicatively across currency.

    A Malaysian holder of a US stock can watch a position rise 8% in USD and
    fall in MYR. This is not a footnote.
    """
    return (1.0 + local_return) * (1.0 + fx_return) - 1.0
