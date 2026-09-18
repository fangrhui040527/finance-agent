"""Rows a vendor writes for a day the exchange was shut.

Yahoo answers a Bursa holiday with a row, not a gap. For a share the row carries
the previous close forward as open = high = low = close on zero volume. For the
index it sometimes repeats the previous row whole, volume included: in the
cache, ^KLSE for 2026-05-29, 06-01 and 06-02 is one row printed three times.
Both shapes pass the seam in `core/market/feed.py`, which checks a bar's own
arithmetic - positive prices, high above low - and has no way to know whether a
market was open. So they reached the feedback pack as a 0.00% session, were
intersected with the proxy's rows for the same holiday, and were handed a
verdict ("not_significant") for a day on which nothing traded.

The filter is here rather than in the parser because it is a judgement about a
SEQUENCE - a row is only a filler relative to the row before it - and because
dropping a bar is a stronger act than refusing a malformed one, so the caller
should be the one to choose it. A pure function over bars; no I/O.
"""

from __future__ import annotations

from core.market.prices import Bar


def drop_carried_rows(bars: list[Bar]) -> list[Bar]:
    """`bars` without the vendor's holiday fillers. Order kept; the first bar always kept.

    Each bar is compared with the row before it in the input, and dropped when
    it is either

      * a carried close: volume 0 and open = high = low = close = the previous
        close, which is how a share's holiday is written; or
      * a repeated row: open, high, low, close AND volume all equal to the
        previous row, which is how the index's holiday is written.

    Two things that look similar are kept on purpose. A genuinely flat session -
    an illiquid share that printed once, at the previous close, on real volume -
    has volume, and volume is the evidence that it traded. And a session whose
    volume the vendor has not filled yet (the index prints 0 volume on the
    current day) has prices that moved, and moving prices are a session.
    """
    kept: list[Bar] = []
    prev: Bar | None = None
    for bar in bars:
        if prev is not None and (_carried_close(bar, prev) or _repeated_row(bar, prev)):
            prev = bar
            continue
        kept.append(bar)
        prev = bar
    return kept


def _carried_close(bar: Bar, prev: Bar) -> bool:
    return bar.volume == 0 and bar.open == bar.high == bar.low == bar.close == prev.close


def _repeated_row(bar: Bar, prev: Bar) -> bool:
    return (bar.open, bar.high, bar.low, bar.close, bar.volume) == (
        prev.open,
        prev.high,
        prev.low,
        prev.close,
        prev.volume,
    )
