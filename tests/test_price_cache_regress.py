"""A refetch that knows less than the cached body does not replace it.

2026-09-29, 00:59 UTC: the us_close collector, 3h44m late, refetched NVDA,
AAPL, MSFT, SPY and ^KLSE. Yahoo answered with Monday's row present and its
close blank; the parser drops that row, so each body's last usable bar was
Friday's, and it overwrote a body pulled at 22:45 UTC that held Monday's
settled close. The offline readers - the nightly pack and the paper book -
then priced the US names at Friday's close."""

from __future__ import annotations

from core.market.cache import PriceCache, last_usable_bar_day
from core.market.feed import YahooFeed

HEAD = "date,open,high,low,close,volume\n"
FRI = "2026-09-25,224.0,226.0,223.0,225.07,100\n"
MON = "2026-09-28,226.0,230.0,225.5,228.86,100\n"
MON_BLANK = "2026-09-28,,,,,\n"  # Yahoo's answer at 00:59 UTC

SETTLED = HEAD + FRI + MON
REGRESSED = HEAD + FRI + MON_BLANK


def _cache(tmp_path, now="2026-09-29T00:59:31+00:00"):
    clock = {"now": now}
    cache = PriceCache(tmp_path / "p.db", today=lambda: clock["now"][:10], now=lambda: clock["now"])
    return cache, clock


def _held(cache, symbol="NVDA"):
    return cache.conn.execute(
        "SELECT body, fetched_at FROM price_csv WHERE feed='yahoo' AND symbol=?", (symbol,)
    ).fetchone()


def test_a_body_whose_last_usable_bar_is_older_does_not_replace_the_held_one(tmp_path):
    cache, clock = _cache(tmp_path, now="2026-09-28T22:45:22+00:00")
    assert cache.put("yahoo", "NVDA", SETTLED) == SETTLED
    clock["now"] = "2026-09-29T00:59:31+00:00"
    kept = cache.put("yahoo", "NVDA", REGRESSED)
    assert kept == SETTLED, "the caller is handed the settled body"
    body, fetched_at = _held(cache)
    assert body == SETTLED and fetched_at.startswith("2026-09-28T22:45")
    assert str(last_usable_bar_day(body)) == "2026-09-28"


def test_a_body_that_adds_a_session_still_replaces_the_held_one(tmp_path):
    cache, clock = _cache(tmp_path, now="2026-09-26T01:00:00+00:00")
    cache.put("yahoo", "NVDA", HEAD + FRI)
    clock["now"] = "2026-09-28T22:45:22+00:00"
    assert cache.put("yahoo", "NVDA", SETTLED) == SETTLED
    assert _held(cache)[0] == SETTLED


def test_an_in_progress_row_on_the_same_usable_bar_still_replaces(tmp_path):
    """The mid-session body ends on the same usable bar as the held one; storing
    it is the existing behaviour, and `get` already refuses to serve it online."""
    cache, clock = _cache(tmp_path, now="2026-09-26T01:00:00+00:00")
    cache.put("yahoo", "NVDA", HEAD + FRI)
    clock["now"] = "2026-09-28T15:00:00+00:00"
    mid = HEAD + FRI + MON_BLANK
    assert cache.put("yahoo", "NVDA", mid) == mid
    assert _held(cache)[0] == mid


def test_the_feed_prices_from_the_held_body_when_the_refetch_regresses(tmp_path, monkeypatch):
    cache, clock = _cache(tmp_path, now="2026-09-28T22:45:22+00:00")
    cache.put("yahoo", "NVDA", SETTLED)
    feed = YahooFeed()
    feed.cache = cache
    clock["now"] = "2026-09-29T00:59:31+00:00"  # a new UTC day: the cached row is a miss
    monkeypatch.setattr(feed, "_fetch_csv", lambda symbol: REGRESSED)
    series = feed.fetch("XNAS:NVDA")
    assert str(series.raw()[-1].day) == "2026-09-28"
    assert float(series.raw()[-1].close) == 228.86
