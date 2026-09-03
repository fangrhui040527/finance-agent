"""Price sources — the missing fetcher seam for bars.

`PriceSeries` (prices.py) has always taken a ready-made list of Bars, and every
caller built them by hand. This is the seam that fills it, shaped deliberately
like `knowledge.feeds.adapter.FeedAdapter` so the two read the same way: a
subclass implements one method and inherits every validation rule below.

The rule that matters most here is the same one the news feed enforces, and it
matters MORE for prices than for news:

    A source that cannot be read RAISES. It never returns [].

An empty bar list must only ever mean "the market was closed". If it can also
mean "the gateway was down", then every downstream consumer - ATR, ADV,
liquidity caps, the factor fit - silently computes on a shorter history than it
thinks it has, and reports a confident number. A missing price announces itself.
A quietly stale one does not.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from core.market.prices import Bar


class PriceSourceError(RuntimeError):
    """A price source could not be read. Deliberately not an empty result."""


class PriceSource(ABC):
    """One source of daily bars. Subclass and implement _fetch_bars."""

    name: str

    def bars(self, instrument_id: str, start: date, end: date) -> list[Bar]:
        """Fetch and validate. Every source inherits these checks."""
        if end < start:
            raise ValueError(f"end {end} precedes start {start}")

        rows = self._fetch_bars(instrument_id, start, end)
        if rows is None:
            raise PriceSourceError(
                f"{self.name} returned None for {instrument_id}; a source must "
                "return bars or raise, never None"
            )
        validate(rows, instrument_id, self.name)
        return rows

    @abstractmethod
    def _fetch_bars(self, instrument_id: str, start: date, end: date) -> list[Bar]: ...


def validate(rows: list[Bar], instrument_id: str, source: str) -> None:
    """Reject a malformed series here, not three layers up in a factor fit.

    A vendor bar with high < low is not a crash anywhere downstream - it is a
    negative true range, which quietly shrinks ATR, which widens the position
    the sizing engine is willing to take. Cheap to catch, expensive to miss.
    """
    previous: date | None = None
    for b in rows:
        if previous is not None and b.day <= previous:
            raise PriceSourceError(
                f"{source} returned {instrument_id} bars out of order or "
                f"duplicated at {b.day} (previous {previous})"
            )
        previous = b.day

        if not (b.low <= b.high):
            raise PriceSourceError(
                f"{source} returned {instrument_id} {b.day} with low {b.low} "
                f"above high {b.high}"
            )
        if not (b.low <= b.open <= b.high and b.low <= b.close <= b.high):
            raise PriceSourceError(
                f"{source} returned {instrument_id} {b.day} with open/close "
                f"outside the low-high range"
            )
        if b.volume < 0:
            raise PriceSourceError(
                f"{source} returned {instrument_id} {b.day} with negative "
                f"volume {b.volume}"
            )


class StaticSource(PriceSource):
    """Bars from memory. The offline default, and the CI path.

    docs/12 section 2.5: the pipeline must be verifiable with no network and no
    keys. This is how a test gets a price series without pretending to have one.
    """

    name = "static"

    def __init__(self, series: dict[str, list[Bar]] | None = None) -> None:
        self._series = series or {}

    def _fetch_bars(self, instrument_id: str, start: date, end: date) -> list[Bar]:
        rows = self._series.get(instrument_id)
        if rows is None:
            raise PriceSourceError(
                f"static source holds no series for {instrument_id}; add one "
                "rather than reading an empty range as a closed market"
            )
        return [b for b in rows if start <= b.day <= end]
