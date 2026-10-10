"""Bars, ticks, lots, slippage and per-leg fees for the paper book.

Everything here reads what is already cached (`core/market/feed`) and prices
with the real fee card (`markets/brokers`). A leg is priced at the bar's OPEN
plus a stated slippage, rounded to the market's tick in the direction that
costs the book - up for an entry, down for an exit - so the record can never
claim a better price than the bar offered.

Fees per leg come from the same `FeeSchedule` the sizing engines use. Its
`round_trip` doubles the symmetric legs and adds the one-way legs once; the
one-way legs on the moomoo US card are the SEC and FINRA charges, which fall
on the exit. So an entry pays the symmetric legs and an exit pays everything,
and `entry_fee + exit_fee == round_trip` is a pinned test.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from core.market.feed import PriceFeedError
from core.market.prices import Bar
from markets.brokers import schedule_for
from markets.registry import get as market_get
from markets.registry import market_currency, mic_of

ENTRY = "entry"
EXIT = "exit"
CENT = Decimal("0.01")


def dec(x: float | int | str | Decimal) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def lot_size(instrument_id: str) -> int:
    return market_get(mic_of(instrument_id)).lot_size(instrument_id)


def currency_of(instrument_id: str) -> str:
    return market_currency(mic_of(instrument_id))


def tick_for(instrument_id: str, price: Decimal) -> Decimal:
    return market_get(mic_of(instrument_id)).tick_size(price)


#: How far before `start` to read so the first bar in a window can be compared
#: with the session before it: a holiday filler is only a filler relative to
#: the row it repeats. Ten calendar days spans the longest Bursa closure run.
_LOOKBEHIND = timedelta(days=10)


def bars(
    feed, instrument_id: str, *, start: date | None = None, end: date | None = None
) -> list[Bar]:
    """The instrument's cached bars on REAL sessions of its market, oldest first.

    Yahoo answers a Bursa holiday with a row, not a gap: the previous close
    carried forward on zero volume. The pack and `aligned_closes` already drop
    those; the paper book did not, so a target decided before a holiday filled
    at the filler row - Friday's close plus slippage, dated a day nothing
    traded - and the bursa_close mark was stamped on the holiday. A bar is kept
    only when the market's calendar calls its day a session and it is not a
    carried row (`core.market.bars.drop_carried_rows`). Raises PriceFeedError
    as the feed does when nothing is cached.
    """
    from core.market.bars import drop_carried_rows

    series = feed.fetch(
        instrument_id, start=start - _LOOKBEHIND if start is not None else None, end=end
    )
    rows = sorted(series.raw(), key=lambda b: b.day)
    try:
        calendar = market_get(mic_of(instrument_id)).calendar
    except (KeyError, ValueError):
        calendar = None
    if calendar is not None:
        rows = [b for b in rows if calendar.is_session(b.day)]
    rows = drop_carried_rows(rows)
    if start is not None:
        rows = [b for b in rows if b.day >= start]
    return rows


def last_close(feed, instrument_id: str, on_or_before: date) -> tuple[Decimal, date]:
    """The last cached close on or before the day. Raises PriceFeedError if none."""
    rows = bars(feed, instrument_id, end=on_or_before)
    if not rows:
        raise PriceFeedError(f"{instrument_id}: no cached bar on or before {on_or_before}")
    last = rows[-1]
    return dec(last.close), last.day


def first_bar_after(feed, instrument_id: str, day: date, up_to: date) -> Bar | None:
    """The first cached bar strictly after `day` and not after `up_to`."""
    if up_to <= day:
        return None
    try:
        rows = bars(feed, instrument_id, start=day + timedelta(days=1), end=up_to)
    except PriceFeedError:
        return None
    return rows[0] if rows else None


def weekdays_between(start: date, end: date) -> int:
    """Weekdays strictly after `start` up to and including `end`."""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def weekdays_back(end: date, n: int) -> date:
    """The date such that the window (date, end] holds exactly `n` weekdays."""
    d, seen = end, 1 if end.weekday() < 5 else 0
    while seen < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            seen += 1
    return d - timedelta(days=1)


def leg_price(bar_open: Decimal, side: str, bps: int, tick: Decimal) -> Decimal:
    """Open plus slippage, rounded to the tick against the book."""
    slip = bar_open * Decimal(bps) / Decimal(10_000)
    raw = bar_open + slip if side == ENTRY else bar_open - slip
    if tick <= 0:
        return raw
    ticks = (raw / tick).quantize(Decimal(1), rounding=ROUND_UP if side == ENTRY else ROUND_DOWN)
    return (ticks * tick).quantize(tick)


def leg_fee(
    mic: str, broker: str | None, consideration: Decimal, price: Decimal, side: str
) -> Decimal:
    """What one leg pays on the real card: entries the symmetric legs, exits everything."""
    schedule = schedule_for(mic, broker)
    if side == EXIT:
        return schedule.one_side(consideration, price)
    return schedule.one_side(consideration, price) - schedule.one_way(consideration, price)


def round_trip_pct(mic: str, broker: str | None, consideration: Decimal, price: Decimal) -> Decimal:
    if consideration <= 0:
        return Decimal(0)
    return schedule_for(mic, broker).round_trip(consideration, price) / consideration
